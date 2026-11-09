"""本地确定性擦除 —— 三种策略，全部**不依赖任何接口**。

本实验的主角。相对 DashScope 擦除接口的结构性优势：

1. **不会造字**。填充值只来自邻域**已有的**像素，数学上没有「生成新笔画」这个操作。
   而 API 那条路实测会在蒙版区里**凭空写字**（bug-144：14 张真调里 4 张擦完墨迹更多）。
2. **确定性**。同样输入永远同样输出。API 那条路是随机的 —— 同一个用户的 ``-4/3``
   擦干净、``-4/1`` 造字，几何几乎相同。
3. **没有尺寸下限**。DashScope 硬性要求 ≥512px，逼得 work/test 把 295×47 的标注框
   外扩裁剪再缩放回来；本地路线在原分辨率上按蒙版擦，**没有任何重采样往返**。

三种策略：

============  ==================================================================
``telea``     OpenCV 快速行进法。**默认**，但做了一处关键改造，见下。
``ns``        OpenCV Navier-Stokes。
``flat``     直接把蒙版像素填成背景 mode 色。最粗暴，但在「背景近乎纯色」时
              **最不可能出意外** —— 它连源像素都不看，也就没机会被污染。
============  ==================================================================

---------------------------- 那个「关键改造」 ----------------------------

直接调 ``cv2.inpaint`` 在本场景**会出错**，而且是那种肉眼一眼能看出、指标却抓不到的错：

    cv2.inpaint 会从蒙版外 ``radius`` 个像素内取样。标注框的绿色描边只有 5px 厚，
    而 3.jpg 的用户名字形**下缘几乎贴着描边** ⇒ 蒙版一碰到框内边界，
    半径 5 的取样范围就伸进描边里 ⇒ **Telea 把绿色抹进了填充区**。

所以本模块做两件事：

1. **洗源**：把「蒙版外围一个半径内的**非背景**像素」在**工作副本**里替换成背景色。
   只洗工作副本，不动原图 —— Telea 于是只从干净的背景取样，绿边/白字/徽章都够不着。
2. **合成**：``inpaint`` 的结果**只取蒙版内那部分**贴回原图。
   于是「蒙版外逐像素不变」是**构造上成立**的，不依赖算法的行为，也不需要事后断言。

第 2 条同时让 ``assert_outside_untouched`` 变成一道**冗余**保险 —— 留着它，
是因为它守的是「未来有人改坏了合成逻辑」这种情况。
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

import config

LOGGER = logging.getLogger(__name__)

_METHODS = {
    "telea": cv2.INPAINT_TELEA,
    "ns": cv2.INPAINT_NS,
}

METHODS: tuple[str, ...] = ("telea", "ns", "flat")
"""对外暴露的全部策略名。"""


def _disk(radius: int) -> np.ndarray:
    """生成一个圆盘形结构元。

    用圆盘而不是方框：方框的四个角会伸出去 ``radius×√2``，等于变相加大了取样范围 ——
    正是我们想收紧的东西。

    Args:
        radius: 半径，<=0 时返回 1×1。

    Returns:
        ``(2r+1, 2r+1)`` 的 uint8 结构元。
    """
    if radius <= 0:
        return np.ones((1, 1), np.uint8)
    size = 2 * radius + 1
    yy, xx = np.mgrid[:size, :size]
    return (((yy - radius) ** 2 + (xx - radius) ** 2) <= radius**2).astype(np.uint8)


def clean_sources(
    work: np.ndarray,
    mask: np.ndarray,
    bg_color: tuple[int, int, int],
    *,
    radius: int = config.INPAINT_RADIUS,
    thresh: int = config.BG_DIST_THRESH,
) -> np.ndarray:
    """在**工作副本**里把蒙版周边一个半径内的非背景像素刷成背景色。

    目的是给 inpaint 提供**干净的取样源**。不改原图 —— 调用方只会取蒙版内的结果。

    Args:
        work:     工作副本（会被就地修改并返回）。
        mask:     ``(H, W)`` uint8 蒙版，非 0 = 待修补。
        bg_color: 背景色。
        radius:   取样半径，与传给 inpaint 的一致。
        thresh:   判定「这个像素算不算背景」的阈值。

    Returns:
        洗过源的 ``work``（同一个数组对象）。
    """
    binary = (mask > 0).astype(np.uint8)
    band = cv2.dilate(binary, _disk(radius), iterations=1) > 0
    band &= mask == 0  # 蒙版自己不在「源」的范畴里

    diff = np.abs(work.astype(np.int32) - np.array(bg_color, dtype=np.int32)).max(axis=2)
    contaminated = band & (diff > thresh)
    n = int(contaminated.sum())
    if n:
        # 这条日志很重要：它一响，就说明**有东西贴得离蒙版太近**（描边/邻居文字/徽章）。
        # 不是错误 —— 是这次擦除「本来会被污染」的证据。
        LOGGER.debug(
            "洗净 %d 个非背景源像素（半径 %d 内）—— 不做这一步，inpaint 会把它们的颜色抹进来",
            n, radius,
        )
    work[contaminated] = np.array(bg_color, dtype=np.uint8)
    return work


def erase(
    img_rgb: np.ndarray,
    mask: np.ndarray,
    *,
    method: str = config.INPAINT_METHOD,
    radius: int = config.INPAINT_RADIUS,
    bg_color: tuple[int, int, int] | None = None,
) -> np.ndarray:
    """按蒙版擦除，只把蒙版内的像素换掉。

    Args:
        img_rgb:  ``(H, W, 3)`` RGB 原图。
        mask:     ``(H, W)`` uint8，**非 0 即视为待修补**。
        method:   ``"telea"`` / ``"ns"`` / ``"flat"``。
        radius:   inpaint 邻域半径（``flat`` 忽略）。
        bg_color: 背景色。给了才做「洗源」；``flat`` 必须给。

    Returns:
        修补后的新数组；**蒙版外的像素是原图的副本**（构造保证）。

    Raises:
        ValueError: method 不认识、蒙版尺寸不符、或 ``flat`` 没给 ``bg_color``。
    """
    key = method.strip().lower()
    if key not in METHODS:
        raise ValueError(f"未知的擦除策略 {method!r}，可选：{list(METHODS)}")
    if mask.shape[:2] != img_rgb.shape[:2]:
        raise ValueError(
            f"蒙版尺寸 {mask.shape[:2]} 与原图 {img_rgb.shape[:2]} 不一致 —— 画布被改过尺寸？"
        )

    binary = (mask > 0).astype(np.uint8) * 255
    inside = binary > 0
    if not inside.any():
        LOGGER.warning("蒙版全空，跳过擦除")
        return img_rgb.copy()

    if key == "flat":
        if bg_color is None:
            raise ValueError("flat 策略需要 bg_color")
        filled = np.empty_like(img_rgb)
        filled[:] = np.array(bg_color, dtype=np.uint8)
        LOGGER.debug("flat 填充 %d px 为背景色 %s", int(inside.sum()), bg_color)
    else:
        work = img_rgb.copy()
        if bg_color is not None:
            work = clean_sources(work, binary, bg_color, radius=radius)
        filled = cv2.inpaint(work, binary, float(radius), _METHODS[key])
        LOGGER.debug(
            "inpaint[%s r=%d] 修补 %d px", key, radius, int(inside.sum())
        )

    # ---- 合成：只贴蒙版内那部分 ----
    # 这一步让「蒙版外逐像素不变」成为构造保证，而不是需要事后验证的性质。
    out = img_rgb.copy()
    out[inside] = filled[inside]
    return out


def fill_local(
    img_rgb: np.ndarray,
    rect: tuple[int, int, int, int],
    *,
    sides: tuple[str, ...] = ("left", "right"),
    span_px: int = config.LOCAL_FILL_SPAN_PX,
    clamp: tuple[int, int] | None = None,
    bg_color: tuple[int, int, int] | None = None,
) -> np.ndarray:
    """把一块矩形填成**紧挨着它的那圈背景**（2026-10-11）。

    用途是「这块地要空出来，空出来的样子得跟旁边一样」。跟 :func:`erase` 的分工：
    擦除是**按蒙版**修补（只动判定为内容的像素，靠 inpaint 从邻域推），本函数是
    **整块铺平**（矩形内一个像素不留），用在「原本就是一块实心东西、而且必须整个消失」
    的地方 —— 等级标（``inkmask._badge_rect``）、尾巴平移腾出来的那条竖带
    （``draw_text.shift_tail``）。

    为什么不用一个全框统一的 mode 色涂：见 :data:`config.LOCAL_FILL_SPAN_PX` 那一段
    （本语料框内底色沿 x 差 18 级，涂出来就是一块看得见的浅灰板，用户 2026-10-11 报的
    「背景填补超过 roi 框，并且背景色不对」）。做法是**逐行**量：``left`` 段取矩形左边
    ``span_px`` 列的中位数、``right`` 段取右边同样宽的，再按列线性过渡（两边都有）
    或平铺（只有一边）。

    ⚠️ **逐行**是必须的：底色沿 y 基本不变、沿 x 会跳，逐行量才既跟得上台阶，
    又能把「绿色描边」这种整行同色的东西原样续上（尾巴的行窗口会伸到描边上，
    那几行必须跟着一起填成绿色，否则绿框断一截）。

    ⚠️ 中位数而不是均值：这两段里常混着邻居的抗锯齿边、徽章的投影、甚至半截笔画，
    均值会被它们拽走，中位数不会。

    Args:
        img_rgb:  ``(H, W, 3)`` 图。
        rect:     要填的矩形 ``(x0, y0, x1, y1)``，**半开区间**。
        sides:    量哪几边的背景。``("left", "right")`` = 左右过渡；只给一边 = 平铺
                  那一边（尾巴腾出来的竖带就用这个 —— 另一侧马上要贴上刚搬过来的墨迹，
                  不是背景）。都没量到（矩形顶到图片边缘）时退回 ``bg_color``。
        span_px:  每边量多宽，默认 :data:`config.LOCAL_FILL_SPAN_PX`。
        clamp:    取样列的**允许范围** ``(lo, hi)``（半开）。传内缩框的列区间，
                  免得够到框外的绿色描边。``None`` = 不夹。
        bg_color: 两边都量不到时的兜底色；``None`` = 该矩形原样不动（并记一条 debug）。

    Returns:
        填过的新数组；**矩形外逐像素不变**（与其他擦除策略同一条承诺），原数组不动。
    """
    x0, y0, x1, y1 = rect
    out = img_rgb.copy()
    if x1 <= x0 or y1 <= y0:
        return out
    lo, hi = (0, img_rgb.shape[1]) if clamp is None else (int(clamp[0]), int(clamp[1]))

    left = right = None
    if "left" in sides:
        lx = max(lo, x0 - span_px)
        if lx < x0:
            left = np.median(img_rgb[y0:y1, lx:x0].astype(np.float32), axis=1)
    if "right" in sides:
        rx = min(hi, x1 + span_px)
        if rx > x1:
            right = np.median(img_rgb[y0:y1, x1:rx].astype(np.float32), axis=1)

    if left is None and right is None:
        if bg_color is None:
            LOGGER.debug("矩形 %s 两侧都取不到背景、也没给 bg_color —— 原样不动", rect)
            return out
        out[y0:y1, x0:x1] = np.asarray(bg_color, dtype=np.uint8)
        return out
    if left is None:
        left = right
    if right is None:
        right = left

    n = x1 - x0
    t = ((np.arange(n) + 0.5) / n)[None, :, None]          # (1, n, 1)
    block = left[:, None, :] * (1.0 - t) + right[:, None, :] * t
    out[y0:y1, x0:x1] = np.rint(block).clip(0, 255).astype(np.uint8)
    LOGGER.debug(
        "就地填背景 %s：左侧 RGB%s → 右侧 RGB%s（各取 %d 列、逐行中位数）",
        rect,
        tuple(int(v) for v in np.median(left, axis=0)),
        tuple(int(v) for v in np.median(right, axis=0)),
        span_px,
    )
    return out


def region_ink(
    img_rgb: np.ndarray,
    region: tuple[int, int, int, int],
    bg: tuple[int, int, int],
    thresh: int = config.RESIDUAL_DIST_THRESH,
) -> int:
    """数一块区域里「还不是背景色」的像素数。

    本实验的核心度量：擦除**前后**在同一个框上各数一次，就能区分「擦干净了」
    和「擦完还剩东西 / 反而多了东西」。

    ⚠️ 必须量在**擦完、写字之前**的图上。量在最终图上会把**新写的名字**也算成残余，
    得出「越擦越多」的假象 —— 那是量错了对象，不是算法出了问题。

    Args:
        img_rgb: ``(H, W, 3)`` RGB。
        region:  ``(x0, y0, x1, y1)`` 半开区间。
        bg:      背景色，长度 3。
        thresh:  最大通道差阈值。

    Returns:
        非背景像素的个数。
    """
    x0, y0, x1, y1 = region
    patch = img_rgb[y0:y1, x0:x1]
    if patch.size == 0:
        return 0
    diff = np.abs(patch.astype(np.int32) - np.array(bg, dtype=np.int32)).max(axis=2)
    return int((diff > thresh).sum())


def assert_outside_untouched(
    before: np.ndarray,
    after: np.ndarray,
    mask: np.ndarray,
    tolerance: int = 0,
) -> int:
    """硬校验：**蒙版之外一个像素都不许变**。

    :func:`erase` 已经把这条做成了构造保证（只贴蒙版内），所以本函数是**冗余**保险 ——
    留着它守的是「未来有人改坏了合成逻辑」这种情况。对照：API 那条路最初有 23.1% 的
    框内像素被改动过。

    Args:
        before:    处理前的图。
        after:     处理后的图。
        mask:      蒙版，非 0 = 允许改动。
        tolerance: 允许的最大通道差（0 = 逐位相同）。

    Returns:
        蒙版外**发生了改动**的像素数（正常应为 0）。

    Raises:
        ValueError: 两图形状不一致。
    """
    if before.shape != after.shape:
        raise ValueError(f"形状不一致：{before.shape} vs {after.shape}")
    diff = np.abs(before.astype(np.int32) - after.astype(np.int32)).max(axis=2)
    outside = (mask == 0) & (diff > tolerance)
    n = int(outside.sum())
    if n:
        ys, xs = np.nonzero(outside)
        LOGGER.error(
            "蒙版外有 %d 个像素被改动！最大通道差 %d，示例坐标 (%d, %d)",
            n, int(diff[outside].max()), int(xs[0]), int(ys[0]),
        )
    return n
