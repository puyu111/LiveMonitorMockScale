"""标注框检测 —— 从截图里找出彩色空心矩形描边的坐标。

⚠️ **这是移植件，不是本实验的产出。** 算法与参数照搬 ``work/test/box_detect.py``
（那边在 650 张全量语料上做过标定），本实验主体刻意不含检测器：8 张图的框是
查表固定的，好让「Telea 够不够」这个问题不被检测误差污染。批量跑真实语料时
没有表可查，才需要它。

搬运时做的**唯一**改动是去掉 ``config._env()`` 的环境变量覆盖，改成普通常量
（test2 的 config 没有那套机制）。行为不变。

思路：转 HSV → 按颜色阈值 ``inRange`` → 形态学闭运算把断掉的描边连起来 →
``findContours`` 找轮廓 → 判这个轮廓是不是**空心矩形描边**（而不是聊天区里的
彩色表情、头像等实心色块）。

两条踩过的坑，都以注释形式留在对应位置：
  * **不能拿外接矩形当框** —— 闭运算会把几十像素外的同色碎屑并进来，外接矩形
    比真描边大出一圈（实测下缘多 8px），「外圈环带覆盖率」这种判据就整条落进
    空边里失效（全量 31/650 张提不出框）。正解是**找线**：描边的本质是四条贯穿
    的直线，在行/列覆盖度剖面上取最外侧的两条实线，框由线自己围出来。
  * **大框套小框**要专门去重 —— 标注框不会套在另一个标注框里，被套住的那个是
    误检，而两者 IoU 可能低到 0.10，光靠 IoU 去重去不掉。
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

import config


class AnnotationNotFoundError(RuntimeError):
    """图里没找到符合要求的彩色标注框。本实验的批量脚本据此记 ``NO_BOX``。"""


class InvalidBBoxError(ValueError):
    """命令行传入的 --bbox 格式不对。"""


@dataclass(frozen=True)
class Box:
    """一个标注框，坐标是像素，(x1,y1) 左上，(x2,y2) 右下，x2/y2 不含。"""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    def clamp(self, img_w: int, img_h: int) -> "Box":
        """把框裁进图片范围内，防止越界坐标导致后面报错。"""
        return Box(
            max(0, min(self.x1, img_w - 1)),
            max(0, min(self.y1, img_h - 1)),
            max(0, min(self.x2, img_w)),
            max(0, min(self.y2, img_h)),
        )

    def expand(self, px: int, img_w: int, img_h: int) -> "Box":
        """向外扩张 px 像素，再裁回图片范围。"""
        return Box(self.x1 - px, self.y1 - px, self.x2 + px, self.y2 + px).clamp(
            img_w, img_h
        )

    def shrink(self, px: int, min_side: int = 4) -> "Box":
        """向内收缩 px 像素，用于把标注描边本身排除在擦除范围之外。

        收缩后若宽或高不足 min_side，则退回最小可用尺寸，避免出现空框。
        """
        if px <= 0:
            return self
        x1, y1, x2, y2 = self.x1 + px, self.y1 + px, self.x2 - px, self.y2 - px
        if x2 - x1 < min_side:
            cx = (self.x1 + self.x2) // 2
            x1, x2 = cx - min_side // 2, cx + min_side // 2
        if y2 - y1 < min_side:
            cy = (self.y1 + self.y2) // 2
            y1, y2 = cy - min_side // 2, cy + min_side // 2
        return Box(x1, y1, x2, y2)

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x1, self.y1, self.x2, self.y2)

    def __str__(self) -> str:  # 方便打日志
        return f"({self.x1},{self.y1})-({self.x2},{self.y2}) [{self.width}x{self.height}]"


def parse_bbox_arg(raw: str) -> Box:
    """解析命令行 --bbox "x1,y1,x2,y2"。格式不对就抛 InvalidBBoxError。"""
    parts = [p.strip() for p in raw.replace("，", ",").split(",")]
    if len(parts) != 4:
        raise InvalidBBoxError(f"--bbox 需要 4 个数字，格式 x1,y1,x2,y2，收到：{raw!r}")
    try:
        x1, y1, x2, y2 = (int(float(p)) for p in parts)
    except ValueError as exc:
        raise InvalidBBoxError(f"--bbox 里有非数字：{raw!r}") from exc
    if x2 <= x1 or y2 <= y1:
        raise InvalidBBoxError(
            f"--bbox 要求 x2>x1 且 y2>y1（x1,y1 是左上角），收到：{raw!r}"
        )
    return Box(x1, y1, x2, y2)


def _coverage_bands(cov: np.ndarray) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """在一维覆盖度剖面里找**最外侧**的两条实线，各返回 (起, 止) 闭区间。

    「实线」= 覆盖度 >= ``LINE_COVERAGE_MIN`` 的连续段。取最外侧两条，且它们必须
    是两个分开的段 —— 只有一段（或者干脆连着）说明这是个实心块，不是描边。

    **刻意不加「必须靠近两端」的限制**：闭运算把几十像素外的同色碎屑并进来时，
    真描边会离外接矩形边缘很远（实测 ``我心甫非常-5/3.jpg`` 的右描边离外接矩形
    右缘 68px），任何「离边缘多少以内」的阈值都会在这儿翻车。取最外侧两条线之后，
    框由线自己围出来，外接矩形多出来多少都不影响结果。
    """
    on = cov >= config.LINE_COVERAGE_MIN
    runs: list[tuple[int, int]] = []
    i = 0
    n = len(cov)
    while i < n:
        if not on[i]:
            i += 1
            continue
        start = i
        while i < n and on[i]:
            i += 1
        runs.append((start, i - 1))
    if len(runs) < 2:
        return None
    return runs[0], runs[-1]


def _rect_outline_box(sub_mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """判断一小块二值蒙版是不是「空心矩形描边」，是就返回**收紧后的**相对框。

    描边的本质是「四条贯穿的直线」：

    1. 行/列覆盖度剖面里，靠近某一条边的位置必须存在一条覆盖度达标的**连续**带；
    2. 四条带都要有，且上下两条、左右两条之间要有真正的空档（否则是实心块）；
    3. 四条带围出来的内部必须基本是空的。

    Returns:
        相对坐标 (x1, y1, x2, y2)，x2/y2 不含；不是矩形描边则 None。
    """
    h, w = sub_mask.shape
    if h < 4 or w < 4:
        return None

    rows = _coverage_bands(sub_mask.mean(axis=1))
    cols = _coverage_bands(sub_mask.mean(axis=0))
    if rows is None or cols is None:
        return None

    (top, top_end), (bottom_start, bottom) = rows
    (left, left_end), (right_start, right) = cols

    x1, y1, x2, y2 = left, top, right + 1, bottom + 1
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None

    # 描边应当是**细框**：四条带各不能太厚，否则「内部」是被两条粗带夹出来的一线天。
    if (top_end + 1 - top) > config.FRAME_MAX_RATIO * h:
        return None
    if (bottom + 1 - bottom_start) > config.FRAME_MAX_RATIO * h:
        return None
    if (left_end + 1 - left) > config.FRAME_MAX_RATIO * w:
        return None
    if (right + 1 - right_start) > config.FRAME_MAX_RATIO * w:
        return None

    # 内部 = 四条带围出来的那块，不含带自身
    inner = sub_mask[top_end + 1 : bottom_start, left_end + 1 : right_start]
    if inner.size == 0:
        return None  # 没有内部 => 实心
    if float(inner.mean()) > config.INTERIOR_MAX:
        return None

    return x1, y1, x2, y2


def _iou(a: Box, b: Box) -> float:
    """两个框的交并比，用来去重。"""
    ix1, iy1 = max(a.x1, b.x1), max(a.y1, b.y1)
    ix2, iy2 = min(a.x2, b.x2), min(a.y2, b.y2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    union = a.width * a.height + b.width * b.height - inter
    return inter / union if union else 0.0


def _containment(inner: Box, outer: Box) -> float:
    """inner 有多大比例落在 outer 里（按面积算）。1.0 = 完全被包住。"""
    ix1, iy1 = max(inner.x1, outer.x1), max(inner.y1, outer.y1)
    ix2, iy2 = min(inner.x2, outer.x2), min(inner.y2, outer.y2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area = inner.width * inner.height
    return inter / area if area else 0.0


def _color_mask(hsv: np.ndarray, lower, upper) -> np.ndarray:
    """单个颜色区间的二值蒙版，并把断掉的描边连起来。"""
    mask = cv2.inRange(hsv, np.array(lower, np.uint8), np.array(upper, np.uint8))
    kernel = np.ones((3, 3), np.uint8)
    return cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, kernel, iterations=config.MORPH_CLOSE_ITERATIONS
    )


def detect_boxes(
    image_bgr: np.ndarray,
    colors: tuple[str, ...] = config.DEFAULT_COLORS,
    min_width: int = config.MIN_BOX_WIDTH,
    min_height: int = config.MIN_BOX_HEIGHT,
) -> list[Box]:
    """检测图里所有彩色标注框。

    Args:
        image_bgr: OpenCV 读进来的 BGR 图片。
        colors:    要检测的颜色名，取自 ``config.COLOR_PROFILES`` 的键。
        min_width / min_height: 小于这个尺寸的轮廓直接丢掉（滤 UI 小色块）。

    Returns:
        去重并按「从上到下、从左到右」排好序的框列表；可能为空。

    Raises:
        ValueError: colors 里有 ``config.COLOR_PROFILES`` 不认识的颜色名。
    """
    unknown = [c for c in colors if c not in config.COLOR_PROFILES]
    if unknown:
        raise ValueError(
            f"不认识的颜色名 {unknown}，可选：{sorted(config.COLOR_PROFILES)}"
        )

    img_h, img_w = image_bgr.shape[:2]
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)

    candidates: list[Box] = []
    for color in colors:
        profile = config.COLOR_PROFILES[color]
        mask = _color_mask(hsv, profile["lower"], profile["upper"])

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            # 粗筛用外接矩形：它只会比真框大，拿它当上界是安全的
            if w < min_width or h < min_height:
                continue

            found = _rect_outline_box(mask[y : y + h, x : x + w] > 0)
            if found is None:
                continue

            # 框由「四条描边线」重新算出来，不用外接矩形 —— 闭运算并进来的
            # 碎屑因此被甩在框外，而不是把框撑大
            ox1, oy1, ox2, oy2 = found
            box = Box(x + ox1, y + oy1, x + ox2, y + oy2)
            if box.width < min_width or box.height < min_height:
                continue

            candidates.append(box.clamp(img_w, img_h))

    # 去重：同一根描边在闭运算后可能产生嵌套的外/内两条轮廓。
    # 按面积从大到小处理，所以先留下的一定是外面那个。
    deduped: list[Box] = []
    for box in sorted(candidates, key=lambda b: -b.width * b.height):
        if any(_iou(box, kept) > 0.5 for kept in deduped):
            continue
        if any(_containment(box, kept) >= config.CONTAINMENT_RATIO for kept in deduped):
            continue
        deduped.append(box)

    # 人类阅读顺序：先按上边缘，再按左边缘
    deduped.sort(key=lambda b: (b.y1, b.x1))
    return deduped


def pick_box(boxes: list[Box]) -> Box:
    """多框时选哪一个 —— **按面积取最大**，不要用 ``boxes[0]``。

    返回顺序是阅读顺序（按 y1,x1），不是面积序；误检的 UI 小色块常常排在真框
    前面。与 ``work/test/batch_test.py`` 同一口径。
    """
    if not boxes:
        raise AnnotationNotFoundError("没有候选框")
    return max(boxes, key=lambda b: b.width * b.height)


def draw_debug(image_bgr: np.ndarray, boxes: list[Box]) -> np.ndarray:
    """把检测结果画回图上，用于人工核对检测准不准。"""
    canvas = image_bgr.copy()
    for i, box in enumerate(boxes, start=1):
        cv2.rectangle(canvas, (box.x1, box.y1), (box.x2, box.y2), (0, 0, 255), 2)
        cv2.putText(
            canvas,
            f"#{i}",
            (box.x1, max(20, box.y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
    return canvas
