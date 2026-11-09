#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""擦掉一条评论的正文，按原几何写一条新的 —— 「量」（:mod:`body_text`）之后的那一步。

三件事，顺序不能换
------------------
1. **擦**：每行一个矩形（:meth:`body_text.BodyLine.erase_rect`），用
   :func:`inpaint_local.fill_local` **逐行取左右中位数铺平**。
   ⚠️ 取样的左界要夹在**冒号右边**（``clamp``），不然左侧那 16px 会把冒号的颜色
   抹进擦除区 —— 这一条是名字那一路（``draw_text.shift_tail``）用血换来的经验。
2. **断行**：按**像素宽**断，不按字数。第一行可用宽 = 正文右缘 − 正文左沿；
   续行可用宽 = 正文右缘 − 段落左沿（续行回到段落左沿，比正文左沿还靠左）。
3. **写**：每行按**墨迹包围盒**对齐（左缘 = 原行左缘、上缘 = 原行上缘），
   与 ``draw_text.render_name`` 同一套对齐口径。

为什么正文不新立字号来源
------------------------
实测同一行「名字 / 正文」的墨迹字高一致（25/24、26/26、23/23，见
``docs/mock正文.html`` 02）—— 所以正文直接用 **这一行的 em**（量测给的
``BodyRegion.em``），不另找字体、不另定字号。590 宽那批（em 20）会跟名字那一路
一起偏大一号，这是已知风险，方案里写明「要修一起修」。

四条判据（与方案 04 一致，写在 :func:`plan_body` 里）
-----------------------------------------------------
行数相等 · 每行不越界 · 第一行要顶满（≥ 可用宽 − 0.6em）· 右缘对齐
（单行 |Δ| ≤ 0.5em，且不越过绿框右边 2px）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw

import body_text
import config
import draw_text
import inpaint_local

WRAP_SLACK_RATIO: float = 0.6
"""第一行「顶满」的容差（em 的倍数）—— 与方案 04 同值。

断行点由排版引擎决定；新句子只要第一行落在这条带里，折行点就与原图对得上。
"""

RIGHT_DELTA_RATIO: float = 0.5
"""单行右缘的允许偏差（em 的倍数）。"""

BOX_MARGIN_PX: int = 2
"""新右缘离上游框右沿至少留几 px —— 别顶到绿标注线上。"""


@dataclass(frozen=True)
class BodyPlan:
    """一条正文的换行方案（还没画）。"""

    text: str
    lines: tuple[str, ...]
    widths: tuple[int, ...]
    """每行的**墨迹宽**（px，按当前字号的真实字体量出来的）。"""

    font_size: int
    """字号（em）。"""

    @property
    def line_count(self) -> int:
        return len(self.lines)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "lines": list(self.lines),
            "widths": list(self.widths),
            "font_size": self.font_size,
        }


def _ink_width(text: str, font) -> int:
    """一段文字的真实墨迹宽度（和 ``draw_text.measure_ink`` 同一把尺子）。"""
    return draw_text.measure_ink(text, font)[0]


def plan_body(
    text: str, region: body_text.BodyRegion, *, font_size: Optional[int] = None,
) -> Optional[BodyPlan]:
    """把一句话断成「与原图同构」的行；不可行返回 ``None``。

    判据四条（任一不过都不收）：

    1. **行数相等**：单行正文只收单行能放下的句子；两行正文必须能在原折行点断开。
    2. **每行不越界**：第一行 ≤ 右缘 − 正文左沿；续行 ≤ 右缘 − 段落左沿。
    3. **第一行要顶满**：第一行宽 ≥ 可用宽 − 0.6em。
    4. **右缘对齐**：单行时 |新宽 − 原宽| ≤ 0.5em，且新右缘 ≤ 框右 − 2px。

    Args:
        text:     候选句子（已过合规检查）。
        region:   :func:`body_text.measure_body` 的结果（必须 ``ok``）。
        font_size: 覆盖字号；``None`` = 用 ``region.em``。

    Returns:
        :class:`BodyPlan` 或 ``None``。
    """
    if not region.ok or not region.lines:
        return None
    em = int(font_size or region.em)
    font = draw_text.load_font(em)
    first = region.lines[0]
    avail1 = first.width
    box_right = (region.text_box[2] if region.text_box else first.right)
    min_fill = avail1 - int(round(WRAP_SLACK_RATIO * em))

    if region.line_count == 1:
        width = _ink_width(text, font)
        if width > avail1 or width < min_fill:
            return None
        if abs(width - first.width) > int(round(RIGHT_DELTA_RATIO * em)):
            return None
        if first.left + width - 1 > box_right - BOX_MARGIN_PX:
            return None
        return BodyPlan(text=text, lines=(text,), widths=(width,), font_size=em)

    # ---- 两行：在「第一行顶满」的位置断开，余下放第二行 ----
    # 第二行也要「顶满 + 右缘对齐」：只判「放得下」会写出 1 个字占满第二行的排版
    # （实测 ``远***-2/2`` 原图 9+4 字，只判放得下会断成 9+1，右缘差 74px）。
    #
    # ⚠️ 续行的**目标**是原图第二行的宽度，不是 ``avail2``：``avail2`` 是「框有多宽」
    # （245−40+1=206px），而原图第二行只有 98px —— 拿 206 当目标等于要求续行顶到框右沿，
    # 两行评论永远配不上（实测 2 行全被判 no_candidate）。顶满与否看 |尾巴 − 原尾巴|。
    avail2 = region.right - region.para_left + 1
    orig2 = region.lines[1].width
    for cut in range(len(text) - 1, 0, -1):
        head, tail = text[:cut], text[cut:]
        width1 = _ink_width(head, font)
        if width1 > avail1:
            continue
        if width1 < min_fill:
            return None  # 再往前断只会更短
        width2 = _ink_width(tail, font)
        if width2 > avail2:
            return None  # 再往前断只会更长（尾巴更放不下）
        if abs(width2 - orig2) > int(round(RIGHT_DELTA_RATIO * em)):
            continue  # 尾巴与原图第二行差太多 ⇒ 折行点不对，换个断点
        if region.para_left + width2 - 1 > box_right - BOX_MARGIN_PX:
            continue  # 续行也顶到绿框了（续行从段落左沿起，框右沿照样管）
        return BodyPlan(text=text, lines=(head, tail), widths=(width1, width2), font_size=em)
    return None


def erase_rects(
    region: body_text.BodyRegion,
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> list[tuple[int, int, int, int]]:
    """每行要铺平的矩形 ``(x0, y0, x1, y1)``（**半开**区间，可直接切片）。

    与 :meth:`body_text.BodyLine.erase_rect` 的差别只有一处：**续行往右扩**。
    续行的新字允许比原尾巴宽 0.5em（方案 04 的右缘判据），擦除矩形必须盖住
    「新字可能落到的范围」，否则新字的最后几个像素会画在矩形外 ——
    实测 ``远***-2/2`` 的 ``OUTSIDE`` 就漏在尾巴右边 4 列（140–143）。

    ⚠️ 只往右扩、且不越过 ``框右沿 + 1``：绿标注线就在框外 5~6px，扩过头会把它填掉。
    框右沿本身也兜住了「新字不越框」——:func:`plan_body` 已经按 ``BOX_MARGIN_PX`` 挡过。

    Args:
        region: 量测结果。
        width:  图宽（给了就裁到图内）；``None`` = 不裁。
        height: 图高。

    Returns:
        与 ``region.lines`` 一一对应的矩形列表。
    """
    box_right = region.text_box[2] if region.text_box else (
        width - 1 if width is not None else 1 << 20)
    wrap_shift = int(round(RIGHT_DELTA_RATIO * region.em)) + config.BODY_ERASE_PAD_RIGHT
    out: list[tuple[int, int, int, int]] = []
    for i, line in enumerate(region.lines):
        x0, y0, x1, y1 = line.erase_rect()
        if i > 0:
            x1 = min(max(x1, line.right + wrap_shift), box_right + 1)
        rect = (x0, y0, x1 + 1, y1 + 1)
        x0, y0, x1, y1 = rect
        if width is not None:
            x0, x1 = max(0, x0), min(width, x1)
        if height is not None:
            y0, y1 = max(0, y0), min(height, y1)
        out.append((x0, y0, x1, y1))
    return out


def erase_body(
    frame: np.ndarray, region: body_text.BodyRegion,
) -> tuple[np.ndarray, list[int]]:
    """按行铺平正文（**不画新字**）。

    Args:
        frame:  ``(H, W, 3)`` RGB。
        region: 量测结果（``ok``）。

    Returns:
        ``(擦完的图, 每行矩形里「还不是背景色」的像素数)``。
        第二个值是「擦干净了没」的硬证据，必须在写字**之前**量
        （量在成品上会把新字算成残余，见 :func:`inpaint_local.region_ink`）。
    """
    out = frame
    residual: list[int] = []
    colon_right = region.colon_x[1] if region.colon_x else None
    box_right = region.text_box[2] if region.text_box else frame.shape[1] - 1
    rects = erase_rects(region, width=frame.shape[1], height=frame.shape[0])
    for i, rect in enumerate(rects):
        # 左取样不许够到冒号：第一行只从「冒号右沿 +2」往右取
        clamp_lo = (colon_right + 2) if (i == 0 and colon_right is not None) else 0
        clamp_hi = min(frame.shape[1], box_right + 1)
        out = inpaint_local.fill_local(
            out, rect, sides=("left", "right"),
            span_px=config.LOCAL_FILL_SPAN_PX,
            clamp=(clamp_lo, clamp_hi),
            bg_color=region.bg,
        )
        residual.append(inpaint_local.region_ink(out, rect, region.bg))
    return out, residual


def render_body(
    frame: np.ndarray, region: body_text.BodyRegion, plan: BodyPlan,
    color: Optional[tuple[int, int, int]] = None,
) -> np.ndarray:
    """把已断好的行画上去（**必须先** :func:`erase_body`）。

    对齐口径与 ``draw_text.render_name`` 一致：按**墨迹包围盒**对齐 ——
    用 ``measure_ink`` 的偏移把墨迹左上角压在原行的左上角上。

    Args:
        frame: 擦完的图。
        region: 量测结果（提供每行的 ``left`` / ``top`` / 续行的段落左沿）。
        plan:  :func:`plan_body` 的结果。
        color: 文字色；``None`` = 原正文色（``region.color``）。

    Returns:
        新数组。
    """
    canvas = Image.fromarray(frame)
    drawer = ImageDraw.Draw(canvas)
    fill = tuple(int(v) for v in (color or region.color))
    font = draw_text.load_font(plan.font_size)
    for i, (text, line) in enumerate(zip(plan.lines, region.lines)):
        _w, _h, off_x, off_y = draw_text.measure_ink(text, font)
        left = line.left
        drawer.text((left - off_x, line.top - off_y), text, font=font, fill=fill)
    return np.array(canvas)


def replace_body(
    frame: np.ndarray,
    region: body_text.BodyRegion,
    text: str,
    *,
    color: Optional[tuple[int, int, int]] = None,
) -> tuple[Optional[np.ndarray], Optional[BodyPlan], list[int]]:
    """``erase → plan → render`` 一条龙。

    Returns:
        ``(新图, 方案, 每行残余)``；句子放不下时 ``(None, None, 残余)`` ——
        此时**只擦了没写**，调用方应把这张登记成「未命中」，不要落盘。
    """
    plan = plan_body(text, region)
    if plan is None:
        return None, None, []
    erased, residual = erase_body(frame, region)
    return render_body(erased, region, plan, color=color), plan, residual
