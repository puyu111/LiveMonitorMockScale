#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""正文（评论文字）的**几何量测** —— 只量，不画。

上游 ``geom.json`` 回答的是「这条评论在哪一框里」（``text_box`` / ``colon_box``）；
这个模块回答的是**换掉它要用的五个数**：

1. **正文左沿**：冒号右沿起第一列墨迹（不能用 ``x_text`` —— 它指向名字/冒号，
   实测 ``苏***`` 那行 ``x_text=106`` 落在冒号 102–111 里面）；
2. **正文右缘**：正文自己的最右墨迹列（不能用 ``text_box.x2`` —— 它比墨迹宽 1~7px，
   拿它当右缘，新写的字会顶到绿框上）；
3. **行数与每行的上下沿**：框内墨迹的**行带**（:func:`inkmask.row_bands`），
   不是「框高 ÷ 字高」；
4. **字号 em**：这一行正文的墨迹字高（本字体 ``ink_height == size``，见
   :func:`draw_text.fit_font_size`）—— 批量阶段应传**这一行名字的 em** 覆盖它
   （``docs/mock正文.html`` §02：同一行名字 25/24、26/26、23/23）；
5. **颜色 / 底色**：墨迹最亮 25% 的均值（:func:`inkmask._core_color_of`）、
   框**内**分箱后的众数（:func:`_estimate_bg` —— 视频底色带噪声，精确色众数会被
   刚写的实心字抢走，见 :data:`BG_BIN_LEVELS`）。

为什么行带要连**整框**一起量，而不是只看冒号右边
------------------------------------------------
原评论是**一整段**：折行后第二行回到**段落左沿**（实测 40px，比正文左沿 190 还靠左），
不在冒号右边。只看右边会把两行读成一行（``讲___-2/2``、``远___◆19-2/2`` 就是这种）。

反过来，上游框偶尔**跨到下一行评论**（4 张，框高 76~84px）—— 那条行带是**另一条评论**，
不是续行。区分用两道闸，任一中就判跨行：

* **行距**：真折行 31/36/38/38px（1.3~1.6em），下一条评论 43/46/48/49px（见
  :data:`config.BODY_LINE_PITCH_RATIO`）；
* **开头彩块**：评论行的开头必有徽章 / 彩色昵称 / emoji（段内平均饱和度 68~160），
  续行是白字（16~28，见 :data:`config.BODY_ROW_SAT_MIN`）。

（``18-2/2`` 的跨行行距只有 43px，单看行距贴着闸门；两道闸结论才一致。）

量不出来就**不换**：所有跳过理由见 :data:`REASON_TEXT`。换错（把官方横幅改写成评论、
把下一条评论擦掉）比不换贵得多。

用法::

    import numpy as np
    from PIL import Image
    import body_text

    frame = np.array(Image.open(path).convert("RGB"))
    region = body_text.measure_body(frame, geom)
    if region.ok:
        print(region.lines, region.em, region.color)

批量入口见 ``mock/measure_bodies.py``（把 73 张的量测写成 JSON）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

import config
import inkmask

REASON_NO_GEOM = "NO_GEOM"
"""上游没给 ``text_box``（或形状不是 4 个整数）。"""

REASON_NO_COLON = "NO_COLON"
"""上游没给 ``colon_box`` —— 正文左沿没有锚点。"""

REASON_NO_BAND = "NO_BAND"
"""框里一行墨迹都没量出来（框空了 / 框的位置不对）。"""

REASON_NO_BODY_LINE = "NO_BODY_LINE"
"""``colon_box`` 的垂直中心不在任何一条行带里 —— 冒号与正文框对不上。"""

REASON_BOX_SPANS_PREV_ROW = "BOX_SPANS_PREV_ROW"
"""框的上方还有一条行带：框不是紧贴这一条评论，上方压着别的东西。"""

REASON_BOX_SPANS_NEXT_ROW = "BOX_SPANS_NEXT_ROW"
"""框的下方那条行带是**另一条评论**（行距太远 / 开头有彩块）。"""

REASON_TOO_MANY_LINES = "TOO_MANY_LINES"
"""认到 :data:`config.BODY_MAX_LINES` 行以上。"""

REASON_WRAP_INDENT_BAD = "WRAP_INDENT_BAD"
"""续行的左沿在正文左沿**右边** —— 不是折行（折行的续行回到段落左沿，必定更靠左）。"""

REASON_WRAP_TOO_WIDE = "WRAP_TOO_WIDE"
"""续行比正文右缘还宽 —— 右缘量错了，或那条不是续行。"""

REASON_EMOJI_ONLY = "EMOJI_ONLY"
"""框在冒号就结束（``text_box.x2`` ≤ 冒号右沿）：正文只有表情，且表情在框外。"""

REASON_NO_INK = "NO_INK"
"""冒号右边没有墨迹，但框也没在冒号结束 —— 说不清，跳过。"""

REASON_TEXT: dict[str, str] = {
    REASON_NO_GEOM: "上游没给正文框",
    REASON_NO_COLON: "上游没给冒号框",
    REASON_NO_BAND: "框里没有墨迹",
    REASON_NO_BODY_LINE: "冒号不在任何行带里",
    REASON_BOX_SPANS_PREV_ROW: "框上方压着另一条行带",
    REASON_BOX_SPANS_NEXT_ROW: "框跨到了下一条评论",
    REASON_TOO_MANY_LINES: "正文超过两行",
    REASON_WRAP_INDENT_BAD: "第二行不在段落左沿",
    REASON_WRAP_TOO_WIDE: "第二行比正文还宽",
    REASON_EMOJI_ONLY: "正文只有表情（框在冒号结束）",
    REASON_NO_INK: "冒号右边没有字",
}
"""跳过理由 → 一句人话（报告与日志用；键是 ``reason`` 字段的取值）。"""


# --------------------------------------------------------------------------
# 结果
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BodyLine:
    """一行正文的墨迹几何（**图坐标**，闭区间）。"""

    top: int
    bottom: int
    left: int
    right: int

    @property
    def width(self) -> int:
        """这一行的墨迹宽度（px）。"""
        return self.right - self.left + 1

    @property
    def height(self) -> int:
        """这一行的墨迹高度（px）—— 就是这一行的 em 估计。"""
        return self.bottom - self.top + 1

    def chars(self, em: int) -> int:
        """这一行大约几个字 = 宽度 ÷ em（**四舍五入**，不是 ``round()`` 的银行家舍入）。"""
        return int(self.width / max(1, em) + 0.5)

    def erase_rect(self) -> tuple[int, int, int, int]:
        """擦这一行用的矩形 ``(x0, y0, x1, y1)``（闭区间，已按配置外扩）。"""
        return (
            self.left - config.BODY_ERASE_PAD_LEFT,
            self.top - config.BODY_ERASE_PAD_TOP,
            self.right + config.BODY_ERASE_PAD_RIGHT,
            self.bottom + config.BODY_ERASE_PAD_BOTTOM,
        )

    def to_dict(self, em: int) -> dict:
        return {
            "top": self.top,
            "bottom": self.bottom,
            "left": self.left,
            "right": self.right,
            "width": self.width,
            "height": self.height,
            "chars": self.chars(em),
            "erase": list(self.erase_rect()),
        }


@dataclass(frozen=True)
class BodyRegion:
    """一条评论正文的量测结果。

    ``ok`` 为 False 时 ``lines`` 可能仍带量到一半的行（证据），但**不许拿去写** ——
    跳过理由见 :data:`REASON_TEXT`。
    """

    reason: str = ""
    """跳过理由（空 = 可以换）。取值见 :data:`REASON_TEXT`。"""

    text_box: Optional[tuple[int, int, int, int]] = None
    """上游正文框（图坐标，闭区间）。"""

    colon_box: Optional[tuple[int, int, int, int]] = None
    """上游冒号框（图坐标，闭区间）。"""

    colon_x: Optional[tuple[int, int]] = None
    """**墨迹里**认出来的冒号列范围 ``(x0, x1)``；``None`` = 没认出来。"""

    colon_source: str = ""
    """``ink`` = 冒号是从墨迹里认的（可信）；``box`` = 退回上游 ``colon_box.x2``。"""

    em: int = 0
    """字号（em）。批量阶段应传这一行**名字**的 em 覆盖，见模块说明。"""

    em_source: str = ""
    """``ink_height``（自己量的）/ ``given``（调用方传的）。"""

    lines: tuple[BodyLine, ...] = ()
    """正文各行。第 0 行是冒号那一行；第 1 行（若有）是折行，左沿在段落左沿。"""

    para_left: int = 0
    """段落左沿：折行时续行从这儿起。单行 = 正文左沿。"""

    right: int = 0
    """正文右缘（px）—— 断行宽度用它，别用 ``text_box.x2``。"""

    color: tuple[int, int, int] = (0, 0, 0)
    """正文主体色（最亮 25% 墨迹像素的均值）。"""

    bg: tuple[int, int, int] = (0, 0, 0)
    """框附近的底色（众数）。"""

    box_right_slack: int = 0
    """``text_box.x2 − 正文右缘``（px）：绿框给末尾留的余量，1~7px。"""

    bands: tuple[dict, ...] = ()
    """证据：框内每条行带的原始统计（上下沿、墨量、段数、开头彩块）。"""

    @property
    def ok(self) -> bool:
        """量全了、可以拿去换（``reason`` 为空）。"""
        return not self.reason

    @property
    def line_count(self) -> int:
        """正文行数（1 或 2）。"""
        return len(self.lines)

    @property
    def chars(self) -> int:
        """整段正文大约几个字 = 各行字数之和。"""
        return sum(line.chars(self.em) for line in self.lines)

    def to_dict(self) -> dict:
        """转成可写 JSON 的 dict（``mock/measure_bodies.py`` 用）。"""
        return {
            "status": "ok" if self.ok else "skip",
            "reason": self.reason,
            "reason_text": REASON_TEXT.get(self.reason, ""),
            "text_box": list(self.text_box) if self.text_box else None,
            "colon_box": list(self.colon_box) if self.colon_box else None,
            "colon_x": list(self.colon_x) if self.colon_x else None,
            "colon_source": self.colon_source,
            "em": self.em,
            "em_source": self.em_source,
            "line_count": self.line_count,
            "lines": [line.to_dict(self.em) for line in self.lines],
            "chars": self.chars,
            "body_left": self.lines[0].left if self.lines else 0,
            "para_left": self.para_left,
            "right": self.right,
            "color": list(self.color),
            "bg": list(self.bg),
            "box_right_slack": self.box_right_slack,
            "bands": list(self.bands),
        }


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------


def _box4(value) -> Optional[tuple[int, int, int, int]]:
    """把上游的框读成 ``(x0, y0, x1, y1)``；形状不对返回 None。"""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (int(v) for v in value)
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def _segments(
    patch: np.ndarray, ink: np.ndarray, top: int, bottom: int, x_offset: int,
) -> list[dict]:
    """把一条行带切成**列段**，每段带宽度 / 填充率 / 饱和度 / 墨量。

    Args:
        patch:     ``(H, W, 3)`` 整框 RGB。
        ink:       ``(H, W)`` 整框墨迹。
        top:       行带在**框内**的上沿（含）。
        bottom:    行带在**框内**的下沿（含）。
        x_offset:  框左沿在**图**里的 x（把列号换算回图坐标）。

    Returns:
        ``[{x0, x1, w, fill, sat, mass}, ...]``，按 x 升序；坐标是**图坐标**。
    """
    band_ink = ink[top : bottom + 1]
    band_rgb = patch[top : bottom + 1]
    out: list[dict] = []
    for start, end in inkmask.column_segments(band_ink.sum(axis=0) > 0):
        block = band_ink[:, start : end + 1]
        pixels = band_rgb[:, start : end + 1][block]
        if len(pixels):
            spread = pixels.max(axis=1).astype(np.int32) - pixels.min(axis=1).astype(np.int32)
            sat = int(round(float(spread.mean())))
        else:  # pragma: no cover - column_segments 只回有墨的列
            sat = 0
        out.append(
            {
                "x0": start + x_offset,
                "x1": end + x_offset,
                "w": end - start + 1,
                "fill": round(float(block.mean()), 3),
                "sat": sat,
                "mass": int(block.sum()),
            }
        )
    return out


def _looks_like_new_row(segments: list[dict], box_x0: int) -> bool:
    """这条行带像不像**另一条评论**（而不是本条的折行续行）。

    判据只有一条：**行带开头有彩色块**。评论行的开头必是等级徽章 / 彩色昵称 /
    emoji（实测段内平均饱和度 68~160），而正文是白字（16~28）。位置分不开
    （续行的首字也在段落左沿，与评论行同 x），只有颜色分得开。

    Args:
        segments: :func:`_segments` 的输出。
        box_x0:   框左沿（图坐标）。

    Returns:
        True = 这条行带是另一条评论（⇒ 整个框跨行，跳过）。
    """
    for seg in segments:
        if seg["x0"] > box_x0 + config.BODY_ROW_WINDOW_PX:
            break
        if seg["w"] >= config.BODY_ROW_SEG_MIN_W and seg["sat"] >= config.BODY_ROW_SAT_MIN:
            return True
    return False


def _colon_fence(
    segments: list[dict], colon_box: tuple[int, int, int, int], band_height: int,
) -> tuple[int, str, Optional[tuple[int, int]]]:
    """在**墨迹**里认出冒号，返回正文左沿的闸门 ``(fence, 来源, 冒号列范围)``。

    不直接用 ``colon_box.x2`` 的原因：**590 宽那批截图的上游冒号框整体右偏 17px**
    （``四___◆3-5/5``：墨迹里冒号在 135–138，上游框是 152–170，框住的是正文第一个字），
    按 ``x2`` 切会把第一个字判丢。

    认法：窄段（宽 ≤ :data:`config.BODY_COLON_MAX_RATIO` × 行高）且落在
    ``colon_box`` 左右各「一行高」的搜索窗里；有多个就取**离冒号框中心最近**的那个
    （``*`` 段也窄，但它离冒号框中心更远）。

    Args:
        segments:     这一条行带的列段（:func:`_segments`）。
        colon_box:    上游冒号框。
        band_height:  行带高度（≈ em，用来定「窄」和搜索窗）。

    Returns:
        ``(fence, "ink" 或 "box", (x0, x1) 或 None)``：``fence`` 是正文搜索起点；
        来源 ``box`` 表示墨迹里没认出来、退回上游框（记录在证据里）。
    """
    narrow = max(6, int(round(config.BODY_COLON_MAX_RATIO * band_height)))
    reach = band_height
    center = (colon_box[0] + colon_box[2]) / 2
    candidates = [
        seg for seg in segments
        if seg["w"] <= narrow
        and seg["x1"] >= colon_box[0] - reach
        and seg["x0"] <= colon_box[2] + reach
    ]
    if candidates:
        best = min(candidates, key=lambda s: abs((s["x0"] + s["x1"]) / 2 - center))
        return (
            best["x1"] + config.BODY_COLON_FENCE_PAD_PX,
            "ink",
            (best["x0"], best["x1"]),
        )
    return colon_box[2] + config.BODY_COLON_FENCE_PAD_PX, "box", None


def _ink_height(ink: np.ndarray, top: int, bottom: int, left: int, right: int) -> int:
    """在给定列范围内，数「有墨迹的行」有多少 —— 也就是这一行的墨迹字高。"""
    cols = np.zeros(ink.shape[1], dtype=bool)
    cols[left : right + 1] = True
    rows_hit = ink[top : bottom + 1][:, cols].sum(axis=1) > 0
    return int(rows_hit.sum())


BG_BIN_LEVELS: int = 8
"""底色估计的**分箱粒度**（每通道 8 级 = 32 档）。

为什么要分箱：正文框里是**视频底色**，不是纯 UI 色 —— 同一块地铺着上百种相近值
（实测 ``你***-3/2`` 框内 6330 像素里，出现次数最多的精确色只有 175 个）。
精确众数在这种情况下会被「刚画上去的实心字」抢走：PIL 画的字是**同一个精确色**，
一贴上去就有六百多像素同值，众数立刻变成字色（bg=(241,254,255)），
整条量测当场反转成 ``NO_INK`` —— 这是 2026-10-28 验收闸挡下来的真实故障。
分箱后「相近的底色几百种值」合成一票，而字与底色相差 45 以上（远超一个箱的 7 级），
永远落在别的箱里 ⇒ 量大字、量小字、量擦完再写的字，拿到的都是**同一个底色**。
"""


def _estimate_bg(patch: np.ndarray) -> tuple[int, int, int]:
    """框内底色：**分箱后的众数箱**取中位数（不是精确色众数 —— 见 :data:`BG_BIN_LEVELS`）。

    Args:
        patch: ``(H, W, 3)`` 的 RGB 数组（正文框**内**，不外扩）。

    Returns:
        ``(r, g, b)``。空数组退回 ``(0, 0, 0)``（调用方会因此量不出墨迹 ⇒ 跳过）。
    """
    if patch.size == 0:
        return (0, 0, 0)
    flat = patch.reshape(-1, 3).astype(np.int32)
    quant = flat // BG_BIN_LEVELS
    packed = (quant[:, 0] << 10) | (quant[:, 1] << 5) | quant[:, 2]
    values, counts = np.unique(packed, return_counts=True)
    winner = values[int(np.argmax(counts))]
    members = flat[packed == winner]
    # 中位数而不是均值：箱里可能混着字的抗锯齿边缘，均值会被拽偏
    return np.array(
        [int(v) for v in np.median(members, axis=0).round()], dtype=np.uint8)


# --------------------------------------------------------------------------
# 量测
# --------------------------------------------------------------------------


def measure_body(
    frame: np.ndarray,
    geom: dict,
    *,
    em: Optional[int] = None,
) -> BodyRegion:
    """量一条评论的正文几何。

    Args:
        frame: ``(H, W, 3)`` uint8 RGB **整张图**（不是 ROI）。
        geom:  上游给这一条的几何：``text_box`` / ``colon_box``（必填），
               ``badge_box`` / ``emojis`` 本模块不用（跳过判据已经够）。
        em:    这一行**名字**的 em（字号）。给了就按它算字数与断行宽度上限；
               不给就自己量正文的墨迹字高（本字体 ``ink_height == size``）。

    Returns:
        :class:`BodyRegion`。``region.ok`` 为 False 时**不要拿去做替换**，
        按 ``region.reason`` 记账（:data:`REASON_TEXT` 是人话）。

    Notes:
        本函数**只读** ``frame`` —— 不就地改任何一个像素。
    """
    text_box = _box4(geom.get("text_box"))
    colon_box = _box4(geom.get("colon_box"))
    if text_box is None:
        return BodyRegion(reason=REASON_NO_GEOM)
    if colon_box is None:
        return BodyRegion(reason=REASON_NO_COLON, text_box=text_box)

    height, width = frame.shape[:2]
    x0 = max(0, min(text_box[0], width - 1))
    y0 = max(0, min(text_box[1], height - 1))
    x1 = max(x0 + 1, min(text_box[2], width - 1))
    y1 = max(y0 + 1, min(text_box[3], height - 1))

    # 底色取**框内**的众数：直播 UI 的底色是大片纯色，mode 不会被字带跑。
    # ⚠️ 不能外扩取 —— 绿标注线就贴在框外 5~6px，外扩会把它取成底色
    # （实测 ``嘟___◆2-5/1``：外扩后 bg = RGB(17,243,22) 荧光绿，整行墨迹就废了）。
    # ⚠️ 也不能用精确色众数 —— 视频底色是带噪声的（见 :data:`BG_BIN_LEVELS`）。
    bg = _estimate_bg(frame[y0 : y1 + 1, x0 : x1 + 1])

    patch = frame[y0 : y1 + 1, x0 : x1 + 1]
    ink = inkmask.compute_ink_mask(patch, bg, config.RESIDUAL_DIST_THRESH)

    raw_bands = inkmask.row_bands(
        ink.sum(axis=1), config.ROW_BAND_GAP_PX, config.ROW_BAND_MIN_ROWS
    )
    if not raw_bands:
        return BodyRegion(reason=REASON_NO_BAND, text_box=text_box, colon_box=colon_box)

    colon_cy = (colon_box[1] + colon_box[3]) // 2 - y0
    target = next((i for i, (t, b, _) in enumerate(raw_bands) if t <= colon_cy <= b), None)
    if target is None:
        return BodyRegion(
            reason=REASON_NO_BODY_LINE, text_box=text_box, colon_box=colon_box
        )

    bands: list[dict] = []
    for t, b, mass in raw_bands:
        segs = _segments(patch, ink, t, b, x0)
        bands.append(
            {
                "top": t + y0,
                "bottom": b + y0,
                "height": b - t + 1,
                "mass": mass,
                "segments": len(segs),
                "new_row": _looks_like_new_row(segs, x0),
                "saturated": max((s["sat"] for s in segs), default=0),
            }
        )

    t0, b0, _ = raw_bands[target]
    evidence = tuple(bands)

    if target > 0:
        return BodyRegion(
            reason=REASON_BOX_SPANS_PREV_ROW,
            text_box=text_box, colon_box=colon_box, bands=evidence,
        )

    # ---- 第一行：只取冒号右边 ----
    segs1 = _segments(patch, ink, t0, b0, x0)
    fence, colon_source, colon_x = _colon_fence(segs1, colon_box, b0 - t0 + 1)
    band_height = b0 - t0 + 1
    gap_min = max(4, int(round(config.BODY_COLON_BODY_GAP_RATIO * band_height)))
    body_segs = [
        s for s in segs1
        if s["x0"] >= fence
        and s["w"] >= config.BODY_MIN_SEGMENT_PX
        # 冒号与正文之间有一个排版空；比它窄的只可能是冒号自己的碎屑（见 config 的说明）
        and (colon_x is None or s["x0"] - colon_x[1] >= gap_min)
    ]
    if not body_segs:
        # 框在冒号就结束（`text_box.x2` 顶到冒号右沿）⇒ 正文只有表情，且表情在框外。
        ends_at_colon = x1 <= colon_box[2] + config.BODY_COLON_FENCE_PAD_PX
        reason = REASON_EMOJI_ONLY if ends_at_colon else REASON_NO_INK
        return BodyRegion(
            reason=reason, text_box=text_box, colon_box=colon_box,
            colon_x=colon_x, colon_source=colon_source,
            bands=evidence, bg=tuple(int(v) for v in bg),
        )

    first = BodyLine(
        top=t0 + y0, bottom=b0 + y0,
        left=body_segs[0]["x0"], right=body_segs[-1]["x1"],
    )
    ink_height = _ink_height(ink, t0, b0, body_segs[0]["x0"] - x0, body_segs[-1]["x1"] - x0)
    if em is not None:
        em_use = int(em)
        em_source = "given"
    else:
        em_use = max(config.FONT_SIZE_MIN, min(config.FONT_SIZE_MAX, ink_height))
        em_source = "ink_height"

    lines = [first]

    def skip(reason: str) -> BodyRegion:
        """下半程的跳过：第一行的几何已经量到，一并带上当证据。"""
        return BodyRegion(
            reason=reason, text_box=text_box, colon_box=colon_box,
            colon_x=colon_x, colon_source=colon_source,
            em=em_use, em_source=em_source, lines=tuple(lines),
            para_left=first.left, right=first.right, bands=evidence,
        )

    # ---- 下方行带：折行续行，还是另一条评论 ----
    for t, b, _mass in raw_bands[target + 1 :]:
        pitch = t - t0
        segs = _segments(patch, ink, t, b, x0)
        is_row = _looks_like_new_row(segs, x0)
        if is_row or pitch > int(round(config.BODY_LINE_PITCH_RATIO * em_use)):
            return skip(REASON_BOX_SPANS_NEXT_ROW)
        if len(lines) >= config.BODY_MAX_LINES:
            return skip(REASON_TOO_MANY_LINES)
        kept = [s for s in segs if s["w"] >= config.BODY_MIN_SEGMENT_PX]
        if not kept:  # pragma: no cover - row_bands 保证有墨
            continue
        wrap = BodyLine(t + y0, b + y0, kept[0]["x0"], kept[-1]["x1"])
        if wrap.left > first.left:
            return skip(REASON_WRAP_INDENT_BAD)
        if wrap.right > first.right + em_use:
            return skip(REASON_WRAP_TOO_WIDE)
        lines.append(wrap)

    para_left = lines[1].left if len(lines) > 1 else first.left

    # 取色：只看第一行的正文列 + 那一行的行带（别把徽章 / 下一行拉进来）
    cols = np.zeros(ink.shape[1], dtype=bool)
    cols[first.left - x0 : first.right - x0 + 1] = True
    rows = np.zeros(ink.shape[0], dtype=bool)
    rows[t0 : b0 + 1] = True
    color = inkmask._core_color_of(patch, ink, cols, rows)

    return BodyRegion(
        text_box=text_box,
        colon_box=colon_box,
        colon_x=colon_x,
        colon_source=colon_source,
        em=em_use,
        em_source=em_source,
        lines=tuple(lines),
        para_left=para_left,
        right=first.right,
        color=tuple(int(v) for v in color),
        bg=tuple(int(v) for v in bg),
        box_right_slack=x1 - first.right,
        bands=evidence,
    )
