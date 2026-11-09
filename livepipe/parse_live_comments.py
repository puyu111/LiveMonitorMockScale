#!/usr/bin/env python
"""抖音直播录屏 → 结构化评论（一体化：OCR 抽行 + 文本/版式解析）。

一条命令从录屏到 ``[{"username", "content", "roi", ...}]``：

    python3 livepipe/parse_live_comments.py --video V.mp4 --out out/comments
    python3 livepipe/parse_live_comments.py --video V.mp4 --auto-panels --out out/comments
    python3 livepipe/parse_live_comments.py --video V.mp4 --dump-rows   # 顺手留下行序，便于复算
    python3 livepipe/parse_live_comments.py --video V.mp4 --no-dedup    # 每帧各成一条，不去重

``--out`` 下产出：``comments.jsonl``（唯一评论）· ``summary.json`` · ``frames/<t>_roi.jpg``
（**标注图**：每条评论的 ROI 画粗彩框 + 编号 + 用户名，纯 OCR 文字框画细灰框 ——
两者之差就是框外的表情区，扩没扩对一眼能看出；``--no-frames`` 可关）。

``summary.json`` 里 ``frames`` 是**出图清单**，一条一张：
``{"t": 8.0, "path": "frames/00008.0_roi.jpg", "comments": 14}`` —— 图在哪、哪一帧、
画了几条，从 summary 直接反查；``path`` 相对 ``--out``，整份产物搬走也不会指错。
（``frames_sampled`` 是抽了多少帧，``frames_drawn`` 是出了几张图，两者不是一回事。）

也接受上游产物，用于复算/调参（不必重跑 OCR）：

    python3 livepipe/parse_live_comments.py --rows out/comments/rows.jsonl
    python3 livepipe/parse_live_comments.py --rows rows.jsonl --video V.mp4 --out out/x  # 复算 + 出标注图
    python3 livepipe/parse_live_comments.py --rows out/comments/rows.jsonl --report   # 逐行归类统计
    python3 livepipe/parse_live_comments.py --text lines.txt        # 或 --text - 读 stdin
    python3 livepipe/parse_live_comments.py                         # 内置示例自检（零依赖）

四层结构
--------
0. **面板定位**（``locate_panels_auto``，``--auto-panels``）：先把「该在哪 OCR」定出来 ——
   按窗口分界切左右两半，各自定位出**评论滚动区**矩形。这个矩形随后就是 OCR 的裁切窗口，
   面积比整帧小一个量级（RapidOCR 耗时随面积增长，实测 583x1354 要 3.6s、583x690 只要
   1.3s）。三种方法（``model`` 用 LocateAnything / ``ocr`` 只用评论行 / ``hybrid`` 各取
   所长，默认）；不定位就用内置 ``DEFAULT_PANELS``（手写值，只对这一个录屏有效）。
   定位结果可直接喂裁切那一步（不必落盘中转），也可以 ``--dump-panels`` 存下来复用。
   （原先独立的 ``livepipe/locate_panels.py`` 现在是本模块的**兼容壳**：只转出旧名字 +
   保留旧命令行，实现一行都没有了。）
1. **OCR 前端**（``rows_from_video``）：面板矩形 + 固定行网格 → RapidOCR 逐行识别 →
   文字行吸附到行槽位（``socket``），得到「每帧每槽位一行 + 几何」的 ``rows``。
   **抽哪几帧**由 ``sample_mode`` 决定：``interval``（默认，固定间隔）/ ``backoff`` /
   ``adaptive``（按面板位移/停稳/入口内容触发，见第 5b 节）——后两种治「固定抽帧静止时
   白拍、快滚时漏条」。
2. **表情识别**（``extract_row_emojis``，第 4 节）：行带像素 → 抖音**官方表情 token**
   （``[赞]`` / ``[爱心]``）。只在有像素的地方能做（``rows_from_video``），所以结果**写在行上**
   带到下游 —— ``--rows`` 复算时没有像素，行里带下来的 ``emojis`` 原样过一遍。
3. **解析层**（``parse_live_rows`` / ``parse_live_comments``）：把行变成评论。
   判据**只有一套** —— ``classify_line``，统计与解析共用，避免两边对不上账。
4. **跨帧身份**（``assign_row_ids``）：评论列表「新评论从底部进、整体上移」，相邻两帧的
   行序列是**平移**关系，据此把同一条评论在多帧里认成同一条（``rid``）。本素材 7 成以上
   的评论是纯表情、正文被 OCR 丢干净，文本上认不出谁是谁 —— 不去重会按帧数翻倍，
   按文本去重又会把同一个用户的不同表情评论撞成一条，只有行位移是可靠线索。
5. **输出**：按 ``rid`` 去重后的唯一评论表（每个 rid 留最早出现的帧）；带几何时附 ROI。

为什么用 OCR 而不是 LocateAnything
----------------------------------
本仓早先用 LocateAnything 当「检测器 + 读字器」抽同一批评论，实测召回很低
（``.wolf/buglog.json`` 的 bug-196）：28px 小字号、一屏十几行的稠密面板上，每帧只能框出
0–3 行。抽评论本质是 **OCR 任务**，不是 grounding 任务。RapidOCR（纯 onnxruntime，无系统
依赖）召回高一个量级，所以 OCR 是这里唯一的前端。

行文本规则（``classify_line``）
-------------------------------
1. 冒号分隔：中文 ``：`` 与英文 ``:`` 都认。
2. 冒号前是「短用户名」（≤ ``max_username_len``）⇒ 新评论；行首的等级徽章
   （``1 时死你`` / ``10 胡杨``）在判定前先剥掉。
3. 冒号在、冒号前**过短且是碎片**（纯数字徽章、或只剩冒号）而冒号后有内容 ⇒ 仍算新评论，
   ``username=None``（用户名没被识别出来）。**不能并成续行** —— 那会把两个用户的正文搅在
   一起。实测反例：``111：这是要漂移吗？``、``：帅``。
4. 没冒号、或冒号前**过长**（说明冒号在正文里）⇒ 续行候选，并进上一条评论的 content。
5. 丢弃：系统通知（``赞了直播``…）· 底部提示（``需先登录，才能开始聊天``）·
   微博 App 的系统行（横幅 / 通知 / 开播公告，**只认没有冒号的**，见 ``_NOCOLON_NOTICE_RE``）·
   纯数字行 · 碎片行。

「纯符号」这条有个坑：``❤️``/``👍`` 在 Unicode 里是 ``So``（symbol, other），跟 ``…``/``。``
不在一个类里，所以判「有没有内容」不能只看「是不是标点」，否则 emoji 会被当符号丢掉 ——
而它们恰恰是最常见的评论内容。``_CONTENT_RE`` 用一张显式的「算内容」字符表来判。

续行判定的固有歧义（重要）
--------------------------
**纯文本分不出「折行的续行」和「另一个人发的、没冒号的行」**：只发了 emoji 的用户，OCR
出来就是一行 ``❤️❤️❤️``，没有冒号也没有用户名 —— 跟「上一条评论的第二行」在文本上一模一样。
要分开只能靠**版式**。所以带几何的输入（``--video`` / ``--rows``）默认开 ``require_adjacent_socket``：
只有**落在一个行槽位之后**（``socket == 上一行 + 1``、同帧同面板）的行才允许并入上一条。
合并不了的孤行**不丢弃**，降级成 ``username=None`` 的匿名评论 —— 真实语料里 ``緣起``、
``大家研究一下`` 多是「昵称被 OCR 丢了」的整条评论，丢了就是真丢数据。

ROI 为什么不能直接用 OCR 框
---------------------------
OCR 框只到文字为止，**表情在框的右边**（实测 ``暗黑盛战：`` 的框止于冒号，❤️ 在框外）。
按 OCR 框裁 ROI 会正好把表情裁掉，所以默认把右边界扩到面板右边（``x_right``）。
左边同理：**等级徽章在昵称左边**，而它被当成「徽章列碎片」从正文里丢掉了（它不是内容），
所以左边界取徽章块的框（``badge_box``）；OCR 只在约一半的行上把徽章单独检出（小图标 +
小字号），没检出时用 ``badge_left``（徽章列左沿 = ``x_text`` 锚点往左 ``BADGE_SPAN``）兜底 ——
ROI 的语义是「整整一行：**徽章 + 昵称 + 正文 + 表情**」。
**徽章列宽要量原图像素**（药丸 ``577→645``），别拿 OCR 检出的数字块宽度当代理：那个块只含
数字，量出来会小 20px，框就切在药丸中间、◆ 菱形露在外面（bug-214）。

表情：为什么是模板匹配，为什么表情区是几何的
--------------------------------------------
录屏里拿不到 DOM，只有像素，所以官方表情的**身份**是匹配出来的：``out/douyin_emoji/``
存着官方表（214 条：``token``/``name``/``hash`` + lossless webp）。官方图带 alpha，先按
聊天背景色合成、再裁紧致框，缩到与画面同高算归一化互相关（NCC）取 top-1。
**表情区不是 OCR 框**：表情不在 OCR 的识别对象里，框的边界跟它在哪无关（实测 ``蓝爵``
那条的 ``text_box`` x2=882 盖住了 x∈[832,912] 的两个表情）。所以表情区按几何取 ——
``x_text``（徽章列之右）到面板右边、行槽位上下一整个行距（表情 38px 比文字 30px 高，
按文字框取带会把它上下切掉）。同一个教训在 ROI 那边也出现过（bug-214）。

**token 与 meaning 是两件事**：``[微笑]`` 是官方名，不是语义 —— 直播语境里它常是
「敷衍/无语」。这一段只产出**可验证的身份**（token），``emoji_text`` 是它的紧凑串
（``[赞][赞][爱心]``），要不要再叠一层 meaning/情感极性是下游的事，别把两者塞进一个字段。

依赖
----
文本模式（``--text`` / ``--rows``）与自检**零第三方依赖**；``--video`` 需要 RapidOCR +
opencv（本机在 conda env ``vlx``）：``pip install rapidocr-onnxruntime onnxruntime opencv-python``。
表情识别额外要 ``out/douyin_emoji/``（``python3 livepipe/douyin_emoji_table.py --api --out
out/douyin_emoji``，联网一次）；表不在就**自动降级**（只警告，其余照跑），``--no-emoji`` 可显式关。
``--auto-panels model|hybrid`` 额外要 torch + transformers + bitsandbytes（同一个 env；
无外网时设 ``HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1``。**只用面板定位的话优先 ``ocr``：
纯 onnxruntime、不占 GPU、不加载 3B 模型**）。
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "livepipe"

from .parsing.common.log import (
    _log,
)

from .parsing.common.text import (
    norm_text,
)

from .parsing.text.patterns import (
    _COLON_RE,
    _INDENT_MAX_GAP_RATIO,
    _INDENT_MIN_PX,
    _TAIL_FRAGMENT_MAX,
    _TAIL_FRAGMENT_RE,
)

from .parsing.text.cleaning import (
    _CONTENT_RE,
    _PURE_NUM_RE,
    _ZW_TRANS,
    _clean,
    _is_junk,
    _is_tail_fragment,
)

from .parsing.text.notices import (
    _ENTER_RE,
    _FOOTER_RE,
    _MASKED_NAME_LINE_RE,
    _MASKED_NAME_RE,
    _NOCOLON_NOTICE_RE,
    _NOTICE_RE,
    _OVERLAY_CARD_RE,
    overlay_card_re,
)

from .parsing.text.badges import (
    MIN_GAP,
    _BADGE_RE,
    _FAN_BADGE_RE,
    _badge_digits,
    _badge_renames,
    _badge_x1,
    _is_badge_token,
    _strip_fan_badge,
    normalize_badge_alias,
    normalize_username_alias,
)

from .parsing.text.classify import (
    _RANK_PILL_RE,
    _split_comment,
    _split_rank_pill,
    classify_line,
    parse_live_comments,
)

from .parsing.layout.roi import (
    _has_own_badge,
    _indented_continuation,
    _record,
    _roi_of,
    _union,
    _wrapped_socket,
    badge_left_of,
    refresh_badge_left,
)

from .parsing.layout.paragraphs import (
    parse_live_rows,
)

from .parsing.constants import (
    ASCII_FRAGMENT_RE,
    BADGE_INSET,
    BADGE_SPAN,
    COMMENT_SEARCH_FRAC,
    DEFAULT_PANEL_MODEL,
    DEFAULT_SPLIT_X,
    OVERLAY_STRIP_FRAC,
    PANEL_PROMPT,
    PANEL_PROMPT_INPUT,
    TALL_BLOCK_FRAC,
    WRAP_GAP_RATIO,
    WRAP_MIN_GAP_RATIO,
    WRAP_TEXT_LEFT_SLACK,
    _BOX_RE,
)

from .parsing.panels.model import (
    _PANEL_WORKERS,
    collect_comment_rows,
    load_panel_worker,
    parse_model_boxes,
    read_frame_rgb,
    scroll_area_from_rows,
)

from .parsing.panels.locate import (
    locate_panel_in_window,
    locate_panel_rect,
    locate_panels_auto,
)

from .parsing.emoji.constants import (
    DEFAULT_EMOJI_TABLE,
    DEFAULT_THRESHOLDS,
    EMOJI_BG,
    EMOJI_COARSE_CAP,
    EMOJI_COARSE_MIN,
    EMOJI_HEIGHT_RATIO,
    EMOJI_LOOKS_LIKE,
    EMOJI_MIN_MARGIN,
    EMOJI_MIN_SCORE,
    SOURCE_TABLES,
)

from .parsing.emoji.gallery import (
    EmojiGallery,
    _EMOJI_CACHE,
    _tight_crop,
    emoji_heights,
    load_emoji_gallery,
    parse_emoji_tables,
)

from .parsing.emoji.scan import (
    _box_saturation,
    _nms_emoji_points,
    _token_scores_near,
    emoji_text_of,
    extract_row_emojis,
    merge_emoji_runs,
    scan_emoji_strip,
)

from .parsing.ocr.engine import (
    OCR_INTRA_THREADS,
    _rapidocr,
)

from .parsing.ocr.lines import (
    PILL_FRAG_MAX_DIGITS,
    PILL_FRAG_MAX_W,
    PILL_FRAG_SLACK_L,
    _in_badge_gutter,
    _is_pill_fragment,
    _is_true_wrapped_line,
    _is_wrapped_line,
    _is_wrapped_shape,
)

from .parsing.ocr.grid import (
    CALIB_FRAMES,
    DEFAULT_PANELS,
    PITCH_MIN_CONF,
    PITCH_RANGE,
    WRAP_CONT_MEDIAN_MAX,
    WRAP_RATIO_MAX,
    _dedup_ys,
    _head_centers,
    _static_overlay_x,
    _wrap_candidates,
    _wrap_ratios,
    calibrate_panels,
    estimate_phase,
    estimate_pitch,
    snap_to_grid,
)

from .parsing.ocr.grouping import (
    _colon_wrap_candidates,
    _merge_straddled_lines,
    _text_left_candidates,
    group_rows,
)

from .parsing.ocr.fused import (
    _offset_box,
    _split_fused_sockets,
    assemble_row,
    badge_box_of,
    badge_text_of,
    split_fused_rows,
)

from .parsing.ocr.colon import (
    _COLON_PROBE_RIGHT,
    colon_box_by_pixels,
    colon_box_in_row,
    colon_box_in_span,
)

from .parsing.ocr.sampling import (
    SAMPLE_DEFAULTS,
    SAMPLE_MODES,
    _AdaptiveSampler,
    _est_shift_px,
    _roi_profile,
    sample_frames,
    sample_frames_adaptive,
    sample_frames_backoff,
)

from .parsing.ocr.overrides import (
    _apply_panel_override,
    _badge_alias_panels,
    _fan_badge_panels,
    _username_alias_panels,
    _overlay_cards_panels,
    _rank_pill_panels,
    _x2_policy,
    parse_badge_alias,
    parse_fan_badge,
    parse_username_alias,
    parse_overlay_cards,
    parse_rank_pill,
    parse_x2_from,
    resolve_panels,
)

from .parsing.ocr.row_ids import (
    _best_shift,
    _emoji_sig,
    _row_key,
    assign_row_ids,
)

from .parsing.ocr.slots import (
    sweep_emoji_slots,
)

from .parsing.ocr.video_rows import (
    rows_from_video,
)

from .parsing.ocr.pills import (
    PILL_BAND_DY,
    PILL_BAND_PAD_R,
    PILL_BAND_W,
    PILL_BATCH,
    PILL_MIN_AGREE,
    PILL_MIN_SCORE,
    _PILL_DIGITS_RE,
    _SampledFrameReader,
    _pill_vote,
    _reread_for,
    _sequential_ruler,
    pill_band_box,
    pill_reading,
    reread_pills,
)

from .parsing.comments.overlays import (
    _FROZEN_BAND_MERGE,
    _FROZEN_BAND_SHARE,
    _FROZEN_BOX_RATIO,
    _FROZEN_MIN_EVENTS,
    _FROZEN_MIN_FRAMES,
    _FROZEN_MIN_MATCHED,
    _FROZEN_SCROLL_RATIO,
    _FROZEN_STILL_RATIO,
    frozen_overlay_texts,
    overlay_card_re_for,
)

from .parsing.comments.build import (
    _decisive,
    _log_badge_fill,
    _row_pitch_panels,
    _same_user,
    comments_from_rows,
)

from .parsing.comments.dedup import (
    SIBLING_FRAME_GAP,
    _pool_sibling_votes,
    dedup_comments,
)

from .parsing.comments.input import (
    has_geometry,
    load_rows,
    parse_rows_any,
)

from .parsing.render import (
    _PALETTE,
    _font,
    _label_width,
    draw_frames,
)

from .parsing.reporting import (
    _emit,
    _load_summary,
    _report,
    _stats,
    _stats_emoji,
    emoji_summary,
)

from .parsing.cli.run import (
    run,
)

from .parsing.cli.parser import (
    make_parser,
)

from .parsing.cli.draw import (
    _draw,
)

from .parsing.selftest.fixtures import (
    EXAMPLE,
    EXPECTED,
)

from .parsing.selftest.roi import (
    _selftest_roi,
)

from .parsing.selftest.panels import (
    _selftest_panels,
)

from .parsing.selftest.dedup import (
    _selftest_dedup,
)

from .parsing.selftest.emoji import (
    _selftest_emoji,
)

from .parsing.selftest.sampling import (
    _selftest_sampling,
)

from .parsing.selftest.rules import (
    _selftest_rules,
)

from .parsing.selftest.runner import (
    _selftest,
)



def _main() -> int:
    """命令行入口：无输入参数时跑内置自检。"""
    args = make_parser().parse_args()
    if not (args.video or args.rows or args.text):
        return _selftest()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(_main())
