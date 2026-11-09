"""livepipe.parsing.ocr.grid — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import statistics

from ..common.log import _log
from ..constants import BADGE_INSET
from ..constants import BADGE_SPAN
from ..constants import WRAP_GAP_RATIO
from ..ocr.lines import _in_badge_gutter
from ..ocr.lines import _is_wrapped_line
from ..ocr.sampling import sample_frames
from ..text.cleaning import _is_junk
from ..text.patterns import _COLON_RE



# --------------------------------------------------------------------------- #
# 5. OCR 前端：视频 → 带几何的行
# --------------------------------------------------------------------------- #
# 固定版式：2560x1520 画面里并排两个抖音窗口，各一个评论面板。
# 每个面板给出「评论区」的矩形（相对原图）与该区的行网格。
#   rect        : 只含评论滚动区的矩形（原图像素）——刻意不含上方的观众列表/标题，
#                 既去掉污染又让 OCR 更快（RapidOCR 耗时随面积增长）
#   row_pitch   : 行距（原图像素），实测稳定在 68 / 61
#   y_phase     : 裁切顶端到第一行中心的偏移（原图像素），使行中心落在 phase+k*pitch
#   x_text      : 评论文本起始 x（裁切内坐标），其左是等级徽章列
DEFAULT_PANELS = {
    "left": {
        # y1 原为 750，实测偏**低**：评论滚动区的顶部槽位（中心 ≈698.5，行 664–733）被切掉，
        # 一条真评论「◆2 1221：好帅哦大哥哥」(t=8, y 685–715) 因此整行丢失。上方的观众列表
        # 底边 ≈640，所以 664 是「整行进来、邻居别进来」的边界。
        "rect": [563, 664, 1146, 1440],   # 左窗口：评论滚动区（上方是观众列表，已排除）
        "row_pitch": 68,
        "y_phase": 17,                    # 首行中心约在裁切顶端下方 17px
        "x_text": 70,                     # 跳过左侧徽章数字列
    },
    "right": {
        "rect": [1225, 1128, 1820, 1300],  # 右窗口：评论区（下方是输入框，已排除）
        "row_pitch": 61,
        "y_phase": 21,
        "x_text": 60,   # 右面板徽章在头像与文字之间，x<60 的碎块多为徽章数字
    },
}


def snap_to_grid(y_center: float, phase: int, pitch: int) -> int:
    """把行中心 y 吸附到最近的行槽位，返回槽位号。"""
    return int(round((y_center - phase) / pitch))


def estimate_phase(ocr_items: list, panel: dict) -> int | None:
    """从一帧的 OCR 结果估计行相位（第一行中心的 y）。

    行中心满足 ``yc ≈ phase + k * pitch``，所以 ``yc mod pitch`` 应聚集在
    ``phase mod pitch``。取所有行中心对 pitch 取模后的众数（按 5px 分桶）作为
    相位估计；与实际相位相差整数个 pitch 不影响吸附结果。

    **逐帧调用**（不只第一帧）：手机 App 版面板是**平滑滚动**，相位逐帧变（实测同一条
    评论 2 秒里移动 2.8 个行距），一次性标定的相位套到后面几帧就错位。网页版是**整行滚动**、
    相位不变，逐帧估得到的值与一次性标定一致（对网页版是 no-op）。
    """
    pitch = panel["row_pitch"]
    x_ignore = panel.get("x_ignore_from")
    centers = []
    for box, text, _score in ocr_items:
        xs = [p[0] for p in box]
        if x_ignore is not None and min(xs) >= x_ignore:   # 浮层文字不是评论行
            continue
        if _in_badge_gutter(min(xs), max(xs), text, panel):
            continue
        # 折行续行也排除：它天生卡在两槽之间（半个行高），拉进来会把相位众数拉偏。
        if _is_wrapped_line(min(xs), max(xs), text, panel):
            continue
        ys = [p[1] for p in box]
        centers.append((min(ys) + max(ys)) / 2)
    if len(centers) < 3:               # 行太少，估计不可靠
        return None
    buckets: dict[int, int] = {}
    for yc in centers:
        b = int(round((yc % pitch) / 5) * 5)
        buckets[b] = buckets.get(b, 0) + 1
    best = max(buckets, key=buckets.get)
    return int(best)


# --------------------------------------------------------------------------- #
# 5a2. 面板几何自标定：行距 · 续行间距比 · 浮层右界
# --------------------------------------------------------------------------- #
# 为什么需要它：``douyin`` 档案里的数字（行距 68、``x_text`` 70、``WRAP_GAP_RATIO`` 0.73、
# 表情 ``height_ratio`` 0.56）全是在**桌面网页版**录屏（2560×1520）上量的。同一平台的
# **手机 App 录屏**是另一套版式：720×1600 竖屏、行距 ≈53、折行续行**缩进**进药丸带、
# 续行间距 ≈0.75 行距、直播间浮层（商品讲解卡 / 预约按钮）压在评论列右边。这些量都能从
# 几帧 OCR 结果里**量出来**，不必按录屏手填 —— 这正是 ``_apply_panel_override`` 那句
# 「同平台不同缩放时用」想解决的，只是以前要人肉填。下面把这些量的估计自动化。
PITCH_RANGE = (30, 100)     # 行距搜索区间（原图 px）：覆盖手机竖屏(~53)到桌面网页(~68)
PITCH_MIN_CONF = 0.6        # 行距置信度（落在最终带宽内的相邻间距占比）低于它就不改
WRAP_RATIO_MAX = 1.05       # 续行间距比上限（≥1 个行距的是左对齐横幅，不是续行）
WRAP_CONT_MEDIAN_MAX = 0.85  # 判「这些缩进样本是续行」的间距比上限（横幅级 ≥1.2、续行级 ≤0.8）
CALIB_FRAMES = 4            # 自标定采样帧数（每帧都要 OCR，别多）


def _head_centers(items: list, panel: dict) -> list[float]:
    """一帧里「评论首行」的 y 中心（含冒号的块，按 y 去重）。

    首行才是行网格的**格点**：续行（无冒号）天生卡在半格、系统通知行（``…关注了主播``）
    不在格点上，把它们喂进行距估计会把结果拉偏（实测同一帧用全部行估出 42、只用首行 54）。
    """
    x_ignore = panel.get("x_ignore_from")
    ys: list[float] = []
    for box, text, _score in items:
        if not _COLON_RE.search(text):
            continue
        xs = [p[0] for p in box]
        if x_ignore is not None and min(xs) >= x_ignore:   # 浮层文字不是评论首行
            continue
        yy = [p[1] for p in box]
        ys.append((min(yy) + max(yy)) / 2)
    ys.sort()
    out: list[float] = []
    for y in ys:
        if not out or y - out[-1] > 12:                    # 同一行被 OCR 切成几块 ⇒ 并成一个
            out.append(y)
    return out


def estimate_pitch(head_gaps: list[float], lo: int = PITCH_RANGE[0],
                   hi: int = PITCH_RANGE[1]) -> tuple[float | None, float]:
    """从**多帧汇总**的「相邻首行间距」估行距，返回 ``(pitch, 置信度)``。

    相位无关：只吃**帧内**相邻间距，所以不要求各帧相位一致（手机版面板平滑滚动、
    每帧相位都不同）。首行之间偶尔隔着一行（间距 = 2×行距）⇒ 先取中位数、再迭代收紧
    带宽把它们滤掉。置信度 = 落在最终带宽内的间距占比；证据不足（<3 个间距）返回 None。
    """
    gaps = [g for g in head_gaps if lo <= g <= hi]
    if len(gaps) < 3:
        return None, 0.0
    m = statistics.median(gaps)
    for _ in range(3):
        keep = [g for g in gaps if 0.7 * m <= g <= 1.4 * m]
        if not keep:
            break
        m = statistics.median(keep)
    band = [g for g in gaps if 0.85 * m <= g <= 1.18 * m]
    return m, len(band) / len(gaps)


def _dedup_ys(ys: list[float], tol: float = 10.0) -> list[float]:
    """把同一行的多个 y 合并成一个（OCR 常把一行切成几块，y 略有出入）。"""
    out: list[float] = []
    for y in sorted(ys):
        if not out or y - out[-1] > tol:
            out.append(y)
    return out


def _static_overlay_x(frame_blocks: list[list[tuple]], width: int) -> int | None:
    """浮层的左界 x：**跨帧不动、且落在面板右半边**的文字块的最小左沿。

    浮层（商品讲解卡 / 预约按钮）不随评论滚动 ⇒ 同一文字块在多帧里坐标几乎不变；评论块
    会滚动。「落在右半边」（``x1 >= width/2``）这一条是为了不误伤：**面板没滚动的采样帧**
    里评论块也是静止的，但它们的左沿都在正文列（≪ width/2）。至少要两个静止块才敢认
    （防单个巧合）。
    """
    if len(frame_blocks) < 2:
        return None
    seen: dict[tuple, list[int]] = {}
    for blocks in frame_blocks:
        for x1, y1, _x2, _y2, text in blocks:
            if not text or x1 < width / 2:
                continue
            seen.setdefault((text, round(x1 / 8), round(y1 / 8)), []).append(x1)
    static_x = [min(v) for v in seen.values() if len(v) >= 2]
    if len(static_x) < 2:
        return None
    return int(min(static_x))


def _wrap_candidates(blocks: list[tuple], panel: dict) -> list[tuple[float, int]]:
    """一帧里的**续行候选**块 → ``[(yc, x1), …]``。

    候选 = 无冒号、有文字、落在正文列左边（``x1 < x_text``，**含缩进带**）的块。刻意
    **不看** ``panel["wrap_indent"]`` —— 那是这里要标定的结果，不是输入：标定得能在开关
    还没打开时就发现缩进续行。
    """
    x_ignore = panel.get("x_ignore_from")
    out: list[tuple[float, int]] = []
    for x1, y1, _x2, y2, text in blocks:
        if x_ignore is not None and x1 >= x_ignore:
            continue
        if _COLON_RE.search(text) or _is_junk(text):
            continue
        if x1 < panel["x_text"]:
            out.append(((y1 + y2) / 2, x1))
    return out


def _wrap_ratios(blocks: list[tuple], panel: dict) -> list[tuple[float, int]]:
    """一帧里续行候选的 ``(间距比, x1)``，只收 <1 的（真续行）。

    间距 = 候选块到**上一行**（同帧所有文字块按 y 去重后、它上方最近的那个）的距离 / 行距。
    真续行 ≈0.6~0.8 行距、左对齐横幅 ≥1.2，所以 ≥1 的丢掉。x1 带回去给 ``calibrate_panels``
    判「这些续行是贴左沿（网页版）还是缩进（手机版）」。
    """
    x_ignore = panel.get("x_ignore_from")
    ys: list[float] = []
    for x1, y1, _x2, y2, _text in blocks:
        if x_ignore is not None and x1 >= x_ignore:
            continue
        ys.append((y1 + y2) / 2)
    lines = _dedup_ys(ys)
    pitch = panel["row_pitch"]
    out: list[tuple[float, int]] = []
    for yc, x1 in _wrap_candidates(blocks, panel):
        above = [y for y in lines if y < yc]
        if not above:
            continue
        ratio = (yc - max(above)) / pitch
        if 0.3 <= ratio < 1.0:
            out.append((ratio, x1))
    return out


def calibrate_panels(video: str, panels: dict, engine, *, start: float = 0.0,
                     duration: float | None = None, interval: float = 2.0,
                     frames: int = CALIB_FRAMES, verbose: bool = True) -> dict:
    """从若干采样帧**自标定**每个面板的行网格与浮层界，就地写回 ``panels`` 并返回。

    标定三个「跨帧不变」的量（逐帧变的 y 相位不在这里 —— 手机版平滑滚动、相位逐帧变，
    由主循环逐帧估，见 ``estimate_phase`` / ``rows_from_video``）：

      * ``row_pitch`` —— 行距。手机竖屏 ≈53、桌面网页 ≈68。
      * ``wrap_gap_ratio`` —— 续行间距比。网页版 ≈0.63、手机版 ≈0.75。
      * ``x_ignore_from`` —— 直播间浮层（商品卡 / 预约按钮）的左界，该 x 以右的文字不是评论。

    **只在证据足够时改**，否则保留档案常量 —— 自标定不该比手填更危险：
    行距置信度 < ``PITCH_MIN_CONF`` 不改；续行样本 < 3 个不改；找不到跨帧静止的右半边
    文字块就不设 ``x_ignore_from``（没有浮层时凭空设一个会把长评论切掉）。
    """
    frame_blocks: dict[str, list[list[tuple]]] = {n: [] for n in panels}
    head_gaps: dict[str, list[float]] = {n: [] for n in panels}
    widths: dict[str, int] = {}
    n_frames = 0
    for _t, frame in sample_frames(video, start, duration, interval, frames):
        n_frames += 1
        for pname, panel in panels.items():
            gx1, gy1, gx2, gy2 = panel["rect"]
            crop = frame[gy1:gy2, gx1:gx2]
            if crop.size == 0:
                continue
            widths[pname] = crop.shape[1]
            items, _ = engine(crop) or (None, None)
            items = list(items or [])
            hc = _head_centers(items, panel)
            head_gaps[pname].extend(b - a for a, b in zip(hc, hc[1:]))
            frame_blocks[pname].append([
                (round(min(p[0] for p in box)), round(min(p[1] for p in box)),
                 round(max(p[0] for p in box)), round(max(p[1] for p in box)), text)
                for box, text, _s in items])
    if n_frames == 0:
        return panels

    for pname, panel in panels.items():
        # ① 行距（先定，续行比要用它）
        pitch, conf = estimate_pitch(head_gaps[pname])
        if pitch is not None and conf >= PITCH_MIN_CONF:
            # **留小数**（一位）：行距是逐行累加的间距，归整那 0.5px 会在面板底部攒成
            # 半个行距的漂移，正好把相邻两行挤进同一槽（见 ``_apply_panel_override``）。
            if verbose and round(pitch, 1) != round(float(panel["row_pitch"]), 1):
                _log(f"  [calib] {pname}: 行距 {panel['row_pitch']} -> {pitch:.1f}"
                     f"（置信度 {conf:.2f}）", flush=True)
            panel["row_pitch"] = round(pitch, 1)
        elif verbose:
            _log(f"  [calib] {pname}: 行距证据不足（置信度 {conf:.2f}），"
                 f"保留档案值 {panel['row_pitch']}", flush=True)
        # ② 浮层右界（要在续行比之前定 —— 续行比得先把浮层块排除掉）
        #
        # **档案/面板里手写了就不覆盖**（``config.py`` 的约定：「写了就以档案/面板里的值为准」）。
        # 这条闸门是量出来的，不是洁癖：标定取的是「跨帧静止块的**最小左沿**」，而浮层最左
        # 那一块自己会飘 1px —— 风云T7 seg6 的 ``风云T7600k`` 实测 x1=478，标定那 4 帧读到
        # 479 ⇒ 闸门落在 479 时它整块漏下来，被 ``assemble_row`` 拼到同槽的评论行尾巴上
        # （实测 4 条评论被污染成 ``魏***：多少钱风云T7600k``）。手写值是从**两簇之间的
        # 空带**里取的（评论侧最大 325、浮层最左 478 ⇒ 取 400），没有这个 1px 问题。
        # ⚠ 仓库里没有别的配置写过这个键（``ctcc``/``csj``/``8PRO``/``exeed`` 里出现的
        # 都是注释里的 ``"x_ignore_from": null``）⇒ 这条闸对既有语料是**逐字节空操作**。
        w = widths.get(pname)
        if w:
            x_ignore = _static_overlay_x(frame_blocks[pname], w)
            if x_ignore is not None and panel.get("x_ignore_from") is None:
                panel["x_ignore_from"] = x_ignore
                if verbose:
                    _log(f"  [calib] {pname}: 浮层右界 x>={x_ignore}（该列文字不是评论）",
                         flush=True)
            elif x_ignore is not None and verbose:
                # 标定值只在日志里留证，闸门用手写的那个
                _log(f"  [calib] {pname}: 自标定浮层右界 x>={x_ignore}，"
                     f"档案已有 {panel['x_ignore_from']} ⇒ 保留档案值", flush=True)
        # ③ 续行间距比 + 缩进续行开关（只在测到**缩进**续行时才动，网页版保持原样）
        ratios: list[float] = []
        n_indent = 0
        flush = panel["x_text"] - BADGE_SPAN + BADGE_INSET
        for blocks in frame_blocks[pname]:
            for ratio, x1 in _wrap_ratios(blocks, panel):
                ratios.append(ratio)
                if x1 >= flush:
                    n_indent += 1
        # 缩进续行是**手机版版式**的证据：测到它才开 ``wrap_indent``、才调续行间距比。
        # 三条闸都要过，缺一不可 —— 否则网页版会被误开（实测视频号面板的「缩进样本」
        # 其实是横幅级间距 ≈0.88，不是续行）：
        #   ① 样本够多（≥3）；② 缩进样本占多数（≥2 且 ≥ 半数）；③ **间距比确实像续行**
        #   （中位数 ≤ ``WRAP_CONT_MEDIAN_MAX``，横幅级 ≥1.2、续行级 ≤0.8，0.85 在中间）。
        if (len(ratios) >= 3 and n_indent >= 2 and n_indent >= len(ratios) / 2
                and statistics.median(ratios) <= WRAP_CONT_MEDIAN_MAX):
            new = min(max(statistics.median(ratios) * 1.2, WRAP_GAP_RATIO), WRAP_RATIO_MAX)
            panel["wrap_indent"] = True
            panel["wrap_gap_ratio"] = round(new, 3)
            if verbose:
                _log(f"  [calib] {pname}: 缩进续行 ×{n_indent} ⇒ 开 wrap_indent、"
                     f"续行间距比 {WRAP_GAP_RATIO:.2f} -> {new:.2f}"
                     f"（{len(ratios)} 个样本）", flush=True)
    return panels
