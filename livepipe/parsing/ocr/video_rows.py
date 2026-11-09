"""livepipe.parsing.ocr.video_rows — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import time

from ..common.log import _log
from ..constants import DEFAULT_PANEL_MODEL
from ..constants import DEFAULT_SPLIT_X
from ..emoji.constants import DEFAULT_EMOJI_TABLE
from ..emoji.gallery import EmojiGallery
from ..emoji.gallery import load_emoji_gallery
from ..emoji.gallery import parse_emoji_tables
from ..emoji.scan import emoji_text_of
from ..emoji.scan import extract_row_emojis
from ..layout.roi import badge_left_of
from ..ocr.colon import _COLON_DROPPED_NICK_GAP
from ..ocr.colon import _COLON_PROBE_LEFT
from ..ocr.colon import _COLON_PROBE_RIGHT
from ..ocr.colon import colon_box_by_pixels
from ..ocr.colon import colon_box_in_row
from ..ocr.colon import insert_colon_by_pixels
from ..ocr.engine import _rapidocr
from ..ocr.fused import _offset_box
from ..ocr.fused import assemble_row
from ..ocr.fused import badge_box_of
from ..ocr.fused import badge_text_of
from ..ocr.fused import split_fused_rows
from ..ocr.grid import CALIB_FRAMES
from ..ocr.grid import calibrate_panels
from ..ocr.grid import estimate_phase


def _boxes_hit(a: list, b: list) -> bool:
    """两个框是否明显重叠（IoU > 0.3）—— 表情去重用。"""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if not inter:
        return False
    area_a = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1, (b[2] - b[0]) * (b[3] - b[1]))
    return inter / min(area_a, area_b) > 0.3
from ..ocr.grouping import group_rows
from ..ocr.overrides import _x2_policy
from ..ocr.overrides import resolve_panels
from ..ocr.row_ids import assign_row_ids
from ..ocr.sampling import sample_frames
from ..ocr.sampling import sample_frames_adaptive
from ..ocr.sampling import sample_frames_backoff
from ..ocr.slots import sweep_emoji_slots
from ..panels.locate import locate_panels_auto



def rows_from_video(video: str, *, start: float = 0.0, duration: float | None = None,
                    interval: float = 2.0, max_frames: int | None = None,
                    sample_mode: str = "interval", sample_opts: dict | None = None,
                    panels_file: str | None = None, calibrate: bool = True,
                    verbose: bool = True, auto_panels: str | None = None,
                    at: float | None = None, split_x: int = DEFAULT_SPLIT_X,
                    panel_frames: int = 5, panel_span: float | None = None,
                    sub_width: int = 1000, pad_x: int = 180, pad_y: int = 16,
                    pad_top: int = 80, panel_model: str = DEFAULT_PANEL_MODEL,
                    quant: str = "4bit", device: str = "cuda",
                    dump_panels: str | None = None,
                    emoji_table: str | None = DEFAULT_EMOJI_TABLE,
                    emoji_workers: int = 1,
                    x2_from: dict[str, str] | None = None,
                    resume_rows: list[dict] | None = None,
                    on_frame=None) -> tuple[list[dict], dict]:
    """视频 → 带几何的行（不去重、按 t → 面板 → socket 排序）。

    ``sample_mode`` 决定**抽哪几帧**：``interval``（默认，每 ``interval`` 秒一帧）·
    ``backoff``（按变化自适应间隔）· ``adaptive``（按面板位移/停稳/入口内容触发，见 5b 节；
    只 OCR 触发了的面板）。后两者的参数走 ``sample_opts``（键见 ``SAMPLE_DEFAULTS``）。

    ``auto_panels`` 给 ``model`` / ``ocr`` / ``hybrid`` 时**先自动定位面板**，
    定位结果直接当 OCR 的裁切窗口（这就是 ``--auto-panels`` 省 OCR 面积的机制）；
    不给则用 ``panels_file`` 或内置 ``DEFAULT_PANELS``。

    每行 ``{"t","frame","panel","socket","text","box","x_text","x_right"}``：
    ``x_text`` 是正文起始 x（判续行看对齐），``x_right`` 是面板右边界（扩 ROI 用）。
    另有 ``colon_box``：分隔冒号的小框（**优先**按像素找「上下两个小点」——
    ``colon_box_by_pixels``；找不到才按字数插值 ``colon_box_in_row``；没有冒号就是 ``None``）——
    标注图圈它；落在行序里，``--rows`` 复算时也画得出。
    另有 ``emojis``/``emoji_text``：表情识别（见第 4 节）**只在有像素的地方能做**，
    所以放在这里而不是放进 ``parse_live_rows``。

    注意这里**不做文本过滤**：通知行、提示行原样留着，交给 ``classify_line`` 判 ——
    过滤逻辑有两套就会对不上账（bug-198 的教训）。
    """
    import cv2

    engine = _rapidocr()          # 定位与解析共用同一个实例，别把 onnxruntime 加载两遍
    panel_meta = None
    auto_rects = None
    if auto_panels:
        # 先定位，再 OCR：定位出的矩形就是后面的裁切窗口。
        # 定位的取样窗要**覆盖真正要解析的那些帧**（x2 是按定位帧里最长的评论定的，
        # 只在短评论的帧上取样会把 x2 收窄，后面的长评论就被裁掉半行）。
        if at is None:
            at = start
        if panel_span is None:
            if max_frames and max_frames > 1:
                span = interval * (max_frames - 1)
            else:
                span = duration if duration else 60.0
            panel_span = max(1.0, min(float(span), 120.0))
        auto_rects, panel_meta = locate_panels_auto(
            video, auto_panels, at=at, split_x=split_x, frames=panel_frames,
            span=panel_span, sub_width=sub_width, pad_x=pad_x, pad_y=pad_y,
            pad_top=pad_top, model=panel_model, quant=quant, device=device,
            engine=engine, verbose=verbose)
        if not auto_rects:
            raise SystemExit(
                "自动定位没找到任何评论面板（左右两个窗口都没有含冒号的评论行）。"
                "改用 --panels-file 指定矩形，或调 --split-x / --at。")
        # 裁切右沿策略：window ⇒ 直接取窗口右沿（超集，按构造不裁内容）。放在 dump 之前，
        # 让落盘的面板文件反映的就是实际会用的裁切窗口。
        for name, r in auto_rects.items():
            if _x2_policy(name, {}, x2_from) == "window":
                win = (panel_meta or {}).get(name, {}).get("window")
                if win:
                    r[2] = int(win[2])
                    _log(f"  [panels] {name}: x2_from=window ⇒ 裁切右沿取窗口右沿 {r[2]}",
                         flush=True)
        if dump_panels:
            dst = Path(dump_panels)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(json.dumps(
                {"_source": {"video": video, "at": at, "method": auto_panels,
                             "model": panel_model if auto_panels != "ocr" else None,
                             "split_x": split_x},
                 "panels": auto_rects, "meta": panel_meta},
                ensure_ascii=False, indent=2), encoding="utf-8")
            _log(f"  [panels] -> {dst}（下次可直接 --panels-file 复用）", flush=True)

    panels = resolve_panels(panels_file, rects=auto_rects)
    # 表情表**按面板**加载（左右两个窗口用的不是同一套表情资产，见 DEFAULT_EMOJI_TABLE）。
    # 表不在就**降级**（只警告、不中断）：表情是锦上添花，不该因为没建某一张表就跑不了
    # 主流程；也**只降级那一个面板** —— 抖音的表缺了不该连累视频号那边。
    galleries: dict[str, EmojiGallery | None] = {}
    for pname, tpath in parse_emoji_tables(emoji_table, panels).items():
        if not tpath:
            galleries[pname] = None
            continue
        try:
            galleries[pname] = load_emoji_gallery(tpath, verbose=verbose)
        except SystemExit as e:
            _log(f"  [emoji] 面板 {pname} 跳过表情识别：{e}", flush=True)
            galleries[pname] = None
    if verbose:
        def _desc(g: EmojiGallery | None) -> str:
            if not g:
                return "不跑"
            return f"{g.source or Path(g.path).stem}（{len(g)} 模板）"

        _log("  [emoji] 按面板选表："
             + "；".join(f"{p}→{_desc(g)}" for p, g in galleries.items()), flush=True)
    if verbose:
        # 裁切右沿策略：window 的面板，裁切窗口是超集 ⇒ x2 会比内容宽一截，
        # 而 ROI 右边界**不取裁切边**（否则每条评论的框都被拉到窗口右沿），
        # 改用「该帧内容右沿 + pad_x」。说清楚，免得看日志的人以为矩形算错了。
        for pname, panel in panels.items():
            if _x2_policy(pname, panel, x2_from) == "window":
                _log(f"  [x2] {pname}: x2_from=window ⇒ 裁切右沿取 {panel['rect'][2]}"
                     f"（超集，不裁内容）；ROI 右边界改用该帧内容右沿 + {pad_x}",
                     flush=True)
    # 表情识别的线程池（``--emoji-workers``）：默认 1 ⇒ 不开，与从前逐字节一致。
    # >1 时把「一行一次」的表情扫描摊到多个核上（结果与串行一致，见主循环里那段说明）。
    # 表情扫描是这一层的大头，而它**只吃一个核**（OpenCV 的 matchTemplate 在小图上不走
    # 内部并行）—— 16 核的机器跑全片，十几个核在闲着，这里就是它们该待的地方。
    emoji_pool = (ThreadPoolExecutor(max_workers=int(emoji_workers))
                  if emoji_workers and int(emoji_workers) > 1 else None)
    t0 = time.time()

    # 面板几何自标定：行距 / 续行间距比 / 浮层右界。自动定位给出的 rect 顶部不固定，
    # 而且**同平台不同缩放**（手机 App vs 桌面网页）行距、续行间距都不一样 —— 这几个量
    # 都从采样帧里量出来，写死的档案值只作证据不足时的兜底。逐帧变的 y 相位不在这里，
    # 由主循环逐帧估（见下）。
    if calibrate:
        calibrate_panels(video, panels, engine, start=start, duration=duration,
                         interval=interval,
                         frames=min(CALIB_FRAMES, max_frames or CALIB_FRAMES),
                         verbose=verbose)

    # 抽帧策略：interval（默认，固定间隔）· backoff（按变化自适应间隔的降级版）·
    # adaptive（按面板位移/停稳/入口内容触发 —— 不漏且少重复）。后两种只在 --video 时生效。
    sample_stats: dict = {}
    if sample_mode == "adaptive":
        frames_iter = sample_frames_adaptive(video, start, duration, panels,
                                             opts=sample_opts, max_frames=max_frames,
                                             stats=sample_stats)
    elif sample_mode == "backoff":
        frames_iter = sample_frames_backoff(video, start, duration, interval, panels,
                                            opts=sample_opts, max_frames=max_frames)
    else:
        frames_iter = ((t, f, None) for t, f in
                       sample_frames(video, start, duration, interval, max_frames))

    rows: list[dict] = list(resume_rows) if resume_rows else []
    # 续跑：把检查点里已处理过的 (t, 面板) 认出来 —— 这些帧/面板这一轮不再 OCR。
    seen = {(r.get("t"), r.get("panel")) for r in (resume_rows or [])}
    n_frames = 0
    for t, frame, fired in frames_iter:
        n_frames += 1
        # 内容触发模式下 fired 是这一帧该处理的面板集合（两侧窗口独立触发）；没触发的
        # 面板这一帧不必再 OCR —— 这是 adaptive 真正省算力的地方。interval/backoff 时为
        # None（= 全部面板）。
        n_rows = 0
        frame_all: list[dict] = []      # 本帧(全部面板)的行 —— 增量落盘 / 续跑检查点用
        if n_frames == 1 and verbose:
            # OCR 面积：整帧 vs 所有面板裁切之和 —— 「先定位再 OCR」省的就是这块
            fh, fw = frame.shape[:2]
            cut = sum((p["rect"][2] - p["rect"][0]) * (p["rect"][3] - p["rect"][1])
                      for p in panels.values())
            _log(f"  [area] 整帧 {fw}x{fh} -> 裁切合计 {cut} px²，占 "
                  f"{100 * cut / max(fw * fh, 1):.1f}%（每帧少 OCR 这么多）", flush=True)
        for pname, panel in panels.items():
            if fired is not None and pname not in fired:
                continue
            if (t, pname) in seen:      # 续跑：这帧这面板上一轮已 OCR 过，跳过
                continue
            gx1, gy1, gx2, gy2 = panel["rect"]
            crop = frame[gy1:gy2, gx1:gx2]
            items, _ = engine(crop) or (None, None)
            items = list(items or [])
            # 逐帧估相位：手机 App 版面板**平滑滚动**、相位逐帧变（同一条评论 2 秒里移动
            # 2.8 个行距），一次性标定的相位套到后面几帧就错位；网页版**整行滚动**、相位
            # 不变，逐帧估得到的值与一次性标定等价（对网页版是 no-op）。估不出来就退回。
            # 与其它自标定一起受 ``calibrate`` 管（``--no-calibrate`` 回到旧的单相位行为）。
            phase = estimate_phase(items, panel) if calibrate else None
            if phase is not None:
                panel = {**panel, "y_phase": phase}
            badge_col: dict[int, list] = {}     # 被丢的等级徽章块，按槽位留着当 ROI 左边界
            frame_rows: list[dict] = []         # 先收齐这一帧这一面板的行，再统一定 x_right
            # 先把「行」收齐（文字 + 框），再统一算表情。分成两趟是为了能把表情那一步丢进
            # 线程池（``--emoji-workers``）：一行一次 ``extract_row_emojis``，只读 frame 与
            # gallery、不碰任何跨行状态，行与行之间没有依赖，所以并行结果与串行**逐字节
            # 一致**（顺序由 ``rows_meta`` 保序）。表情扫描是这一层的大头（本素材实测
            # ~0.28s/行，占每帧七成上下），单线程跑满一个核，剩下的核闲着。
            rows_meta: list[tuple] = []
            # ``split_fused_rows``：一个槽里并了两行（两条评论粘成一行）时拆回两条 ——
            # 见那边的说明（网格线正好卡在两行中间时两行会同时归进同一槽）。
            for socket, blocks in split_fused_rows(group_rows(items, panel, badge_col),
                                                  panel):
                text, lbox = assemble_row(blocks)
                if not text:
                    continue
                # 分隔冒号的位置（昵称与正文之间那个 ``:`` / ``：``）：OCR 只给整块框，
                # 这里按字符比例估出一个小框，随行一起落盘 —— 标注图要圈它，而 --rows
                # 复算时手上只有这份行序、没有 OCR 块，必须在抽行这一趟就算好。
                colon = colon_box_in_row(blocks, text)
                # 冒号常被 OCR 整块吃掉（微博「榜N」药丸行实测 178/212），按字数插值的框
                # 会落到正文第一个字上；像素里的「上下两个小点」不吃，找得到就用它
                # （见 colon_box_by_pixels）。找不到（系统行/续行/浅色底）退回插值。
                # **搜索框右沿要放宽**：药丸行里 OCR 的文字块止于药丸右沿（冒号与表情都
                # 不是「文字」），真冒号落在块外那段 ≈44px 的版式空隙里 —— 实测 t=63.4
                # 的「用户6861943086榜一：😭×4」块右沿 331、冒号 360，卡在块内的找法
                # **一整类行永远是 None**（用户报的「没有画冒号的位置」就是它）。
                #
                # **左沿也要放宽**（另一种并块）：OCR 把「等级药丸 + 昵称 + 冒号」并成一个
                # 不带冒号的块、按徽章列丢掉，正文块从冒号右边才开始（抖音实测 t=191.2：
                # 药丸块 `121***` 右沿 167、正文块左沿 188，冒号在 163~173）。判据是
                # **药丸块里确实带掩码昵称**（`*`）—— 此时用正文左沿当先验，挑最近的点对。
                badge_txt = badge_text_of(badge_col, socket) or ""
                nick_in_badge = colon is None and "*" in badge_txt
                # 另一种「昵称整块没被 OCR 检出」：正文块从 x_text 右边很远才开始
                #（正常行只隔 12~16px；A9L 实测 `6**：比同级别合资强多了` 的正文块左沿
                # 106、x_text 30），冒号落在块外左侧。只放宽到 x_text，别扫进徽章列；
                # 找到的点对中心还必须落在正文框左沿左边一段内（见下面的验收）。
                x_text = int(panel.get("x_text") or 0)
                dropped_nick = (colon is None and not nick_in_badge
                                and not badge_txt
                                and lbox[0] - x_text >= _COLON_DROPPED_NICK_GAP)
                left_probe = _COLON_PROBE_LEFT if (nick_in_badge or dropped_nick) else 0
                # 右探只给「文本里本来就有冒号」的行用（微博药丸行：OCR 块止于药丸右沿，
                # 冒号在块外那段版式空隙里）。文本里没有冒号、左侧也没有并块证据时，
                # 把右探关掉 —— 否则正文右侧背景里的点对会被当成冒号（A9L 实测 t=4.57
                # `天盾，小桌板有汉有` 的框被圈到文字框右边 23px 的背景上）。
                right_probe = (_COLON_PROBE_RIGHT
                               if (colon is not None or nick_in_badge or dropped_nick)
                               else 0)
                probe = [max(x_text, lbox[0] - left_probe) if left_probe else lbox[0],
                         lbox[1], lbox[2] + right_probe, lbox[3]]
                # 插值当先验传给像素找法：打码昵称的 `*` 贴着冒号（差 3px）时，
                # 「孤立」判据会把真冒号挡掉，而插值只偏几个像素 —— 见 colon_box_by_pixels。
                if colon is not None:
                    hint = (colon[0] + colon[2]) / 2
                elif nick_in_badge or dropped_nick:
                    hint = lbox[0] - 8          # 冒号就在正文块左沿左边几个像素
                else:
                    hint = None
                # 阈值可由平台档案覆盖（浅灰气泡底要把 bg_max_v 调大、v_delta 调低；
                # 不写这三个键 ⇒ 与旧行为逐字节相同）。
                pix_colon = colon_box_by_pixels(
                    frame[gy1:gy2, gx1:gx2], probe, hint=hint,
                    bg_max_v=float(panel.get("colon_bg_max_v", 110.0)),
                    v_min=float(panel.get("colon_v_min", 100.0)),
                    v_delta=float(panel.get("colon_v_delta", 60.0)))
                if pix_colon and dropped_nick:
                    # 左探是「补框」不是「修框」：只认紧贴正文左沿的那一对，徽章列/
                    # 头像里的点对离得远，一律不要（否则续行会被误圈一个冒号）。
                    cx = (pix_colon[0] + pix_colon[2]) / 2.0
                    if not (lbox[0] - _COLON_PROBE_LEFT <= cx <= lbox[0] + 5):
                        pix_colon = None
                if pix_colon:
                    colon = pix_colon
                    # 文本切分只看文本里的冒号（classify_line）；OCR 把冒号吃掉的整行，
                    # 用户名的部分会被当成正文。**opt-in**（platform/面板里写
                    # colon_text_fix=true）时按像素位置把冒号补回文本 —— 不写这个键的
                    # 平台与旧行为逐字节相同。昵称整个落在药丸块里时文本里没有昵称，
                    # 插一个冒号只会把正文首字切成用户名 ⇒ 这种情况只画框、不插字。
                    # colon_text_fix_masked_only：只认打码星号锚点（见 insert_colon_by_pixels）。
                    if panel.get("colon_text_fix") and not nick_in_badge:
                        text = insert_colon_by_pixels(
                            text, lbox, colon,
                            masked_only=bool(panel.get("colon_text_fix_masked_only")),
                            blocks=blocks)
                rows_meta.append((socket, text, _offset_box(lbox, gx1, gy1),
                                  badge_box_of(badge_col, socket),
                                  badge_text_of(badge_col, socket),
                                  _offset_box(colon, gx1, gy1) if colon else None))
            gal = galleries.get(pname)
            if gal:
                # 表情：行带 = **这一行 OCR 框的 y 中心**上下各半个行距。
                # 为什么不用行网格（``y_phase + socket·pitch``）定中心：网格是全局拟合的，
                # 单个槽位可以偏到半个行距（分组的容差就是 pitch/2），而表情的带子只有
                # 一个行距高 —— 右面板实测偏 21px，就把 👍 的下半截切掉了（认不出来）。
                # 而 OCR 框就是这一行的实测位置，直接拿它当中心最准（表情与文字同心：
                # 左面板实测文字中心 1306 / 表情中心 1307）。
                # x 从徽章列之右（``x_text``）到面板右边，**不用 OCR 框的左右边界** ——
                # 表情不在 OCR 的识别对象里，框的右边跟表情在哪无关（bug-214 的教训）。
                ycs = [(m[2][1] + m[2][3]) / 2 for m in rows_meta]

                def _emojis_at(yc: float):
                    return extract_row_emojis(frame, yc, panel["row_pitch"],
                                              gx1 + panel["x_text"], gx2, gal)

                emo_list = (list(emoji_pool.map(_emojis_at, ycs)) if emoji_pool is not None
                            else [_emojis_at(yc) for yc in ycs])
            else:
                emo_list = [[] for _ in rows_meta]
            for (socket, text, gbox, badge, badge_txt, colon_box), emojis in zip(rows_meta, emo_list):
                frame_rows.append({
                    "t": t, "frame": n_frames, "panel": pname, "socket": socket,
                    "text": text, "box": gbox,
                    # colon_box: 分隔冒号的小框（见 colon_box_in_row）—— 标注图圈它。
                    # 没有冒号（续行/系统行）就是 None。
                    "colon_box": colon_box,
                    # motion_px: 这一帧该面板的瞬时位移（内容触发模式下才有）—— 越接近 0
                    # 拍得越清楚，跨帧去重时用它挑同一条评论最清楚的那次（见 dedup_comments）。
                    "motion_px": fired.get(pname) if isinstance(fired, dict) else None,
                    # badge_box: 等级徽章框 —— ROI 左边界扩到它（整行 = 徽章 + 昵称 + 正文 + 表情）
                    "badge_box": _offset_box(badge, gx1, gy1) if badge else None,
                    # badge_text: 徽章块**读出来的读数**（等级标，如 `◆6` / `6`）——
                    # 身份证据：等级标一致才算同一个人（见 _badge_renames）。检出率约 4 成，
                    # 检不出时是 None（那时药丸往往被并进昵称，读数就在用户名里）。
                    "badge_text": badge_txt,
                    # badge_left: 徽章列左沿，没检出徽章块时的兜底（见 BADGE_SPAN）。
                    # 徽章列宽按**平台档案**给的 ``badge_span`` 走（默认 BADGE_SPAN）：
                    # 桌面网页版的 x_text 锚点落在药丸右端内侧，58 就够；手机 App 版
                    # （douyin_app）的 x_text 是药丸右沿**外侧**，58 只到药丸中段 ⇒
                    # ROI 左沿切在 ◆ 菱形上（用户报的「左侧等级标没有圈进去」）。
                    "badge_left": badge_left_of(gx1, panel),
                    # x_text: 正文起始 x（徽章列之右）—— 判「续行」看它的对齐
                    # x_right: 面板右边界 —— 扩 ROI 用（表情在 OCR 框右边）。下面按策略回填
                    "x_text": gx1 + panel["x_text"], "x_right": gx2,
                    # emojis: 这一行的表情（含 token），emoji_text 是它的紧凑串
                    "emojis": emojis,
                    "emoji_text": emoji_text_of(emojis),
                })
            # **空槽补扫表情**：表情是图片，OCR 只认文字 ⇒ 「只有表情」的评论经常连打码
            # 昵称都读不出来，那一行在行网格里就是个空槽 ⇒ 不圈、也不进 comments.jsonl。
            # 细节（插值定槽中心、面板闸、只认有名字的表情）见 sweep_emoji_slots。
            frame_rows.extend(sweep_emoji_slots(
                frame, t=t, n_frames=n_frames, pname=pname, panel=panel,
                gy1=gy1, gx1=gx1, gx2=gx2, rows_meta=rows_meta,
                badge_col=badge_col, fired=fired, gal=gal))
            # **表情折行补扫**（opt-in `emoji_gap_sweep`）：抖音的长表情评论会折成多行，
            # 第二行往往没有 OCR 文字、又正好落在两条已检出行之间的纵向空档里 ——
            # 逐行带（以行文字框中心、半个行距为半径）扫不到，空槽补扫也接不住（槽被
            # 下一行的文字占着）。这里在**相邻两行的空档**里再扫一遍，只把**中心落在
            # 空档里**、且与上下两行已有表情不重叠的那些并进上一行。
            # 实测 t=160.1 的 `囍财` 表情评论：第一行 2 颗 [比心] 已认出，第二行 4 颗落在
            # 它与商品卡文字行之间 ⇒ 修复前整条评论只记了 2 颗。
            if panel.get("emoji_gap_sweep") and gal and len(frame_rows) > 1:
                pitch = float(panel["row_pitch"])
                ordered = sorted(frame_rows,
                                 key=lambda r: (r["box"][1] + r["box"][3]) / 2)
                for up, down in zip(ordered, ordered[1:]):
                    gap_top, gap_bot = up["box"][3], down["box"][1]
                    gap = gap_bot - gap_top
                    if not (pitch * 0.15 <= gap <= pitch * 1.2):
                        continue
                    yc = (gap_top + gap_bot) / 2
                    band = max(pitch, gap + pitch * 0.6)
                    # x 从**面板左沿**扫：折行的表情行常常缩进到徽章列左边
                    # （实测 `囍财` 的第二行 [比心] 在 x=49~112，而 x_text=116），
                    # 从 x_text 起扫会整行漏掉。误检由「中心必须在空档内」与
                    # 「与上下行已有表情不重叠」两道闸挡着。
                    cand = extract_row_emojis(frame, yc, band,
                                              gx1, gx2, gal)
                    have = list(up.get("emojis") or []) + list(down.get("emojis") or [])
                    add = []
                    for e in cand:
                        if not e.get("token"):
                            continue
                        cy = (e["box"][1] + e["box"][3]) / 2
                        if not (gap_top - 2 <= cy <= gap_bot + pitch * 0.25):
                            continue
                        if any(_boxes_hit(e["box"], x["box"]) for x in have):
                            continue
                        add.append(e)
                    if add:
                        up["emojis"] = list(up.get("emojis") or []) + add
                        up["emoji_text"] = emoji_text_of(up["emojis"])
            # x_right（ROI 的右边界）：
            #   content —— 面板右边**由内容定**（裁切边 = 内容右沿 + pad_x），沿用裁切边即可；
            #   window  —— 裁切窗口是「超集」（面板右边跟着内容伸缩，如视频号），这时
            #              **不能**拿裁切边当 ROI 右沿，否则每条评论的框都会被拉到窗口右沿。
            #              改用**这一帧的内容右沿**：本帧所有行的文字框 ∪ 表情框的最右值，
            #              再 + pad_x（与 content 侧同一个余量语义）—— 这就是「面板在这一帧
            #              的真实右边界」，也是 ROI 该到的地方。
            if _x2_policy(pname, panel, x2_from) == "window" and frame_rows:
                content_right = max(
                    [r["box"][2] for r in frame_rows]
                    + [e["box"][2] for r in frame_rows for e in r["emojis"]])
                xr = min(gx2, int(content_right) + pad_x)
                for r in frame_rows:
                    r["x_right"] = xr
            rows.extend(frame_rows)
            frame_all.extend(frame_rows)
            n_rows += len(frame_rows)
        if on_frame and frame_all:
            on_frame(t, frame_all)      # 跑一帧写一帧：中断也能从这帧之后接着来
        if verbose:
            el = time.time() - t0
            _log(f"  t={t:7.1f}s 本帧行数={n_rows:3d} 累计 {len(rows):4d} 行 "
                  f"(已用 {el:.0f}s, {el/max(n_frames,1):.2f}s/帧)", flush=True)

    if emoji_pool is not None:
        emoji_pool.shutdown()

    assign_row_ids(rows)          # 跨帧身份：滚动对齐，供 dedup_comments 用
    meta = {"video": video, "engine": "rapidocr-onnxruntime", "start": start,
            "duration": duration, "interval": interval, "frames_sampled": n_frames,
            "sample_mode": sample_mode, "resumed_rows": len(resume_rows or []),
            "rows": len(rows), "panels": panels, "elapsed_s": round(time.time() - t0, 1),
            # 表情识别是可选层：记下**每个面板**用的哪张表、哪个源、几个模板（None ⇒ 没跑），
            # 出处可追 —— 左右两个窗口可能用的是两套完全不同的表情资产
            "emoji_table": emoji_table,
            "emoji_tables": {p: (g.path if g else None) for p, g in galleries.items()},
            "emoji_sources": {p: (g.source if g else None) for p, g in galleries.items()},
            "emoji_templates": {p: (len(g) if g else 0) for p, g in galleries.items()},
            "emoji_thresholds": {p: (dict(g.thresholds) if g else None)
                                 for p, g in galleries.items()}}
    if panel_meta is not None:
        meta["auto_panels"] = auto_panels       # 矩形是现场定位来的，记下方法与取证
        meta["panel_locate"] = panel_meta
    if sample_stats:
        meta["sample_stats"] = sample_stats     # 内容触发：每个面板各触发几次、按类别（取证用）
    return rows, meta
