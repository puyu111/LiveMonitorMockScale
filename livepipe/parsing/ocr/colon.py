"""livepipe.parsing.ocr.colon — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import re

from ..text.patterns import _COLON_RE


# 字符在 OCR 块框里的宽度权重：全角（汉字 / 全角标点）记 1，半角（ASCII、打码星号）记 0.55。
# 与标注图的 ``render._label_width`` 同一把尺子。OCR 只给整块框，块里字符**并不等宽**：
# 抖音手机版的打码昵称（`恬***`）末尾是半角星号，正文是汉字，按字数等分就把冒号往右推
# （实测中位偏 21px，圈到正文首字上）；按宽度加权后中位偏 8px。
_HALF_WIDTH = 0.55


def _char_weight(ch: str) -> float:
    return 1.0 if ord(ch) > 0x2E80 else _HALF_WIDTH


def colon_box_in_row(blocks: list, text: str) -> list[int] | None:
    """定位一行里**第一个分隔冒号**（``:`` / ``：``）的框（裁切内坐标）。

    ⚠️ 抽行时**优先用** ``colon_box_by_pixels``（冒号是两个小方点，像素上认得出，
    与 OCR 认没认出无关）；本函数是**退路**：没有像素（``--rows`` 复算、浅色底）或
    像素里找不到点对时用它 —— 它只能在 OCR 文本里看到冒号时才算得出位置。

    OCR 只给**整块**文字框，不给字符框，所以冒号只能**按字符比例估**：先按 x 把块排好、
    累加每块的字数，找到冒号落在哪一块的第几个字，再按**字符宽度**（``_char_weight``：
    全角 1、半角 0.55）把它映射成 x。框高取那一块的行高（冒号就画在这一行里），
    宽取冒号自己的字宽（至少 6px）——画出来是一个贴着冒号的小矩形，不是整块的大框。

    估的误差来自「同一块里字符不等宽」（昵称里的 ``***`` / 数字比汉字窄）与**块间空隙**
    （微博「榜N」药丸右侧到正文有 44px 版式空隙，实测偏 33px）—— 标注图够看，
    但「冒号在哪」这件事以像素值为准。定位不了（没有冒号 / 块与文本对不上）返回 ``None``。
    """
    m = _COLON_RE.search(text or "")
    if m is None:
        return None
    idx = m.start()
    off = 0
    for x1, y1, x2, y2, t, *_ in sorted(blocks, key=lambda b: b[0]):
        n = len(t)
        if n and idx < off + n:
            j = idx - off
            span = float(x2) - float(x1)
            weights = [_char_weight(c) for c in t]
            tot = sum(weights) or 1.0
            cw = max(6.0, weights[j] * span / tot)       # 冒号自己的宽度
            left = float(x1) + sum(weights[:j]) * span / tot
            return [int(round(left)), int(y1),
                    int(round(left + cw)), int(y2)]
        off += n
    return None


def colon_box_in_span(text: str, box: list[int]) -> list[int] | None:
    """没有块级坐标时的退路：把**整行**文字框按字符宽度（见 ``_char_weight``）等分，取冒号那一格。

    旧 ``rows.jsonl``（本字段上线前抽的行）里没有 ``colon_box``，而 ``--rows`` 复算不会
    再跑 OCR。这时按整行框插值，虽然不如 ``colon_box_in_row`` 准（块间空隙被摊进字宽），
    但足够把冒号圈出来 —— **不必为了一个标注框重跑几小时 OCR**。
    """
    m = _COLON_RE.search(text or "")
    if m is None or not box:
        return None
    span = float(box[2]) - float(box[0])
    weights = [_char_weight(c) for c in text]
    tot = sum(weights) or 1.0
    cw = max(6.0, weights[m.start()] * span / tot)
    left = float(box[0]) + sum(weights[:m.start()]) * span / tot
    return [int(round(left)), int(box[1]), int(round(left + cw)), int(box[3])]


# 抖音手机版的打码昵称一律以 2~4 个半角星号结尾（`D***` / `直***` / `***`）。冒号丢失时
# 星号串的右沿就是昵称/正文边界 —— 比按字宽等分稳：整行昵称全是半角星号时，等分模型把
# 冒号算在最后一个星号之后**1~2px**，实测会把冒号插进正文首字之后（`T***落：地什么价位`）。
_MASKED_TAIL_RE = re.compile(r"^[^\s*]{0,24}\*{2,}")

# 视频号手机 App 的打码昵称常只有**一个**星号（`苗苗*` / `铠子*`）。同一套「星号右沿 =
# 昵称右沿」判据；只在 ``masked_only`` 模式下用（默认的 2+ 星号规则不动，既有语料逐字节
# 相同）。单星号也是半角、贴着冒号，等分模型容易把冒号算进正文首字。
_MASKED_TAIL_LOOSE_RE = re.compile(r"^[^\s*]{0,24}\*{1,}")

# 「正文块从 x_text 右边很远才开始」的阈值：正常行文字块左沿距 x_text 只有 12~16px
#（实测 42~46 vs 30），而**昵称块整块没被 OCR 检出**时正文从昵称右沿之后起（A9L 实测
# `6**：比同级别合资强多了` 的正文块左沿 106 ⇒ 距 x_text 76px）。用它区分「昵称丢了」
# 与「续行/系统行」，只在正文里**没有冒号**时放宽左沿去找像素冒号。
_COLON_DROPPED_NICK_GAP = 40

# 昵称块与正文块之间那段「不是文字」的版式空隙（昵称后面的表情 + 冒号）宽度下限。
# 正常行内的块缝只有几个 px（等级药丸和正文之间实测 6px）；昵称后面夹了表情时实测
# 42px（10.31 视频号 t=4637 `摸鱼的西红柿🍅：在泉州…`，昵称块右沿 156、正文块左沿 198）。
# 24 落在两者之间，既能认到表情缝，又不会把药丸缝当成分隔点。
_BLOCK_GAP_MIN_PX = 24.0


def _colon_gap_pos(text: str, blocks: list, cx: float) -> int | None:
    """像素冒号落在哪一段**块缝**里 ⇒ 返回该处的字符下标（冒号插在它前面）。

    为什么需要它：``insert_colon_by_pixels`` 的按字宽等分把整行文字框当均匀铺开，
    而块缝里的表情（实测 42px，≈ 2 个汉字宽）不在文本里 —— 等分模型会把冒号往后推
    一两个字（`摸鱼的西红柿在：泉州…`）。块缝是**实测坐标**，插在「前一块的最后一个字
    之后」就是准确位置，不依赖任何字宽估计。

    只认「相邻两块之间、缝宽 ≥ ``_BLOCK_GAP_MIN_PX``、且像素冒号中心落在缝里」的那一处；
    且 ``text`` 必须恰好是各块文本的顺序拼接（``assemble_row`` 的产出），否则不猜。
    """
    order = sorted(blocks, key=lambda b: b[0])
    if sum(len(b[4]) for b in order) != len(text):
        return None
    off = 0
    for i in range(len(order) - 1):
        off += len(order[i][4])
        gap = float(order[i + 1][0]) - float(order[i][2])
        if gap < _BLOCK_GAP_MIN_PX:
            continue
        if not (float(order[i][2]) - 2 <= cx <= float(order[i + 1][0]) + 2):
            continue
        return off if 0 < off < len(text) else None
    return None


def insert_colon_by_pixels(text: str, box: list, colon_box: list, *,
                           masked_only: bool = False,
                           blocks: list | None = None) -> str:
    """OCR 文本里**没有**分隔冒号、但像素里找到了 ⇒ 把冒号补回文本（opt-in）。

    用户名/正文的切分看的是**文本里的冒号**（``classify_line`` 的 ``_COLON_RE``），
    光有 ``colon_box`` 只能把标注框画准，救不回用户名。把冒号字符补回文本，下游一切照旧。

    落点优先用**打码昵称的星号串**（``_MASKED_TAIL_RE``，星号右沿 = 昵称右沿）；
    没有星号串才退回 ``colon_box_in_row`` 同一把尺子（全角 1 / 半角 0.55 的加权等分），
    并且只在插值位置离像素冒号 ≤45px 时才认。插不进去（文本里本来就有冒号、中心落在
    文字框外、落点在首尾）⇒ 原样返回。

    ``blocks``：这一行的 OCR 块（``assemble_row`` 的输入）。给了就**优先**用块缝定位
    （见 ``_colon_gap_pos``）：昵称与正文之间夹着表情时，表情不在文本里、等分会把冒号
    推后一两个字，而块缝是实测坐标。块缝定位**不看 ``masked_only``** —— 它是精确落点，
    不是赌出来的（视频号未打码昵称 `摸鱼的西红柿🍅：…` 就靠它救回用户名）。

    ``masked_only``：只认**星号锚点**，没有星号串就不插。视频号这类「昵称后面紧跟的
    标点会被 OCR 一起吃掉」的版式上，加权等分会被缺失的字形整体推偏一格（A9L 实测
    `梁，：内饰看着简约时尚` 读成 `梁内饰看着简约时尚`，等分插出来是 `梁内饰：…`、
    昵称多啃一个字；`平安：边吃饭边看` 插成 `平安边：…`）。星号右沿不依赖等分，稳。
    单星号（`苗苗*`）在这一模式下也认。默认关 ⇒ 旧行为不变。
    """
    if not text or not box or not colon_box:
        return text
    if _COLON_RE.search(text):
        return text
    span = float(box[2]) - float(box[0])
    if span <= 0:
        return text
    cx = (float(colon_box[0]) + float(colon_box[2])) / 2.0
    if not (float(box[0]) - 10 <= cx <= float(box[2]) + 10):
        return text

    if blocks:
        pos = _colon_gap_pos(text, blocks, cx)
        if pos is not None:
            return text[:pos] + "：" + text[pos:]

    def _model_x(pos: int) -> float:
        weights = [_char_weight(c) for c in text]
        tot = sum(weights) or 1.0
        return float(box[0]) + sum(weights[:pos]) * span / tot

    m = _MASKED_TAIL_RE.match(text)
    if m is None and masked_only:
        m = _MASKED_TAIL_LOOSE_RE.match(text)
    if m is not None and 0 < m.end() < len(text):
        if abs(_model_x(m.end()) - cx) <= 45.0:
            pos = m.end()
            return text[:pos] + "：" + text[pos:]
    if masked_only:
        return text                      # 没有星号锚点 ⇒ 不赌等分（见 docstring）

    weights = [_char_weight(c) for c in text]
    tot = sum(weights) or 1.0
    acc = 0.0
    pos = None
    for j, w in enumerate(weights):
        lx = float(box[0]) + acc * span / tot
        rx = float(box[0]) + (acc + w) * span / tot
        if lx <= cx <= rx:
            pos = j + 1                  # 插在第 j 个字之后
            break
        acc += w
    if pos is None or pos <= 0 or pos >= len(text):
        return text
    return text[:pos] + "：" + text[pos:]


# 像素找冒号时把搜索框**右沿**放宽多少像素。药丸行（微博「榜N」）的 OCR 文字块止于
# 药丸右沿，真冒号在块外那段 ≈44px 的版式空隙里（实测块右沿 331 / 冒号 360），卡在块内
# 就整类行找不到。48 = 版式空隙 + 余量；再往右就是正文，放宽了也不会跨行（纵向仍由行带限制）。
_COLON_PROBE_RIGHT = 48

# 同理的**左沿**放宽，给另一种并块：OCR 把「等级药丸 + 昵称 + 冒号」并成一个块、又按徽章列
# 丢掉了，正文块从冒号右边才开始（抖音手机 App 实测 t=191.2：药丸块 `121***` 右沿 167、
# 正文块左沿 188，冒号在 163~173）。只有拿到「药丸块里确实含掩码昵称」这条强证据时调用方
# 才放宽左沿（见 video_rows / render.refresh_colon_box），平时不扩大搜索面。
_COLON_PROBE_LEFT = 48

# 插值给的冒号位置附近多大范围内，才认「有字贴着」的点对（见 ``colon_box_by_pixels``）。
# 插值本身的中位误差 8px、p99 约 34px（抖音手机版实测），取 40 留一档余量；再放宽就会
# 把正文里的「；」「：」也划进候选。
_COLON_HINT_WINDOW = 40


def colon_box_by_pixels(crop, box, *, bg_max_v: float = 110.0,
                        hint: float | None = None,
                        v_min: float = 100.0,
                        v_delta: float = 60.0) -> list[int] | None:
    """在行带里按**像素**找分隔冒号（裁切内坐标）—— 比按字数插值准得多。

    冒号在像素上是个很硬的签名：**同一列上下两个小方点**（微博实测两点各 4×5px、
    中间空 7px），而任何汉字/数字都是一整块高得多的连通块。做法：在文字框内取
    「明显亮于背景」的二值掩码（文字白/浅色、聊天区背景深），找满足下面条件的点对 ——
    左沿相差 ≤2px、宽高各相差 ≤2px、竖向间隙 3~14px，并且这一列左右 3px 内**没有
    别的文字块**（排除汉字里的点、``i`` 的竖点、省略号的一个点）。

    为什么不能只靠 OCR：冒号只有 2~3px 宽、又孤立，RapidOCR **经常整块不检**
    （微博「昵称 榜N 药丸 正文」行实测 **178/212** 被吃掉）。吃掉之后
    ``colon_box_in_row`` 只能按字数插值，而误差不是一个字宽 —— 药丸右侧到正文之间
    有 44px 版式空隙（`用户6861943086榜二：这车这么稳` 实测：插值给 396，真冒号在 363），
    框会圈在正文第一个字上，看着就像「没画冒号」。而冒号的**像素一直在那儿**，
    不管 OCR 认不认它。

    Args:
        crop: 面板裁切后的 BGR 图（``rows_from_video`` 里就是 ``frame[gy1:gy2, gx1:gx2]``）。
        box: 搜索框（裁切内坐标），左边徽章列/头像里的噪点进不来；调用方把**右沿**
            放宽 ``_COLON_PROBE_RIGHT`` —— OCR 的文字块常常止于药丸右沿，真冒号在
            块外那段版式空隙里（见 ``rows_from_video`` 的调用处）。
        bg_max_v: 背景亮度上限。超过它就当成浅色底（白底黑字的平台），掩码取「亮于背景」
            会失效 ⇒ 直接返回 ``None``，退回按字数插值。**浅灰气泡底**（直播画面亮、气泡
            半透明，实测 V≈146~189）也让像素法整个失效 —— 平台档案可以把它调大，并配
            ``v_delta`` 一起用（见下面两个参数）。
        hint: 插值（``colon_box_in_row``）给的冒号 x 中心（裁切内坐标），可省。给了就以它
            为准在 ``_COLON_HINT_WINDOW`` 内挑**最近的点对** —— 「有字贴着」的点对也认
            （抖音手机版打码昵称 `恬***`：星号右沿 132、冒号 135，差 3px，孤立判据把
            一整类的真冒号全挡掉，而插值只偏 8px 左右，够当先验）。不给则只认孤立的点对
            （旧行为）：OCR 把冒号整块吃掉时没有 hint，此时只能靠几何。
        v_min: 掩码的绝对亮度下限（默认 100）。浅底 + 深色文字的平台上要调低。
        v_delta: 掩码要求「比背景亮多少」。默认 60 是给暗底的白字量的；浅灰气泡上冒号
            是**两个 2px 的彩色小点**，抗锯齿后只比背景亮 20~40（A9L 素材实测：背景 V≈146、
            上点 ≈205、下点 ≈165），30 才能同时收进两个点。调低会多收背景噪点，误检仍由
            点对几何与孤立性判据兜着；既有档案不写这两个键 ⇒ 与旧行为逐字节相同。

    Returns:
        ``[x1, y1, x2, y2]``（裁切内坐标，左右各放 3px 好画成一个看得见的小方框）；
        找不到点对（没有冒号的系统行/续行）⇒ ``None``。
    """
    import cv2
    import numpy as np

    x1, y1, x2, y2 = (int(round(float(v))) for v in box)
    h, w = crop.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 20 or y2 - y1 < 10:
        return None
    band = crop[y1:y2, x1:x2]
    v = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)[:, :, 2]
    bg = float(np.median(v))
    if bg > bg_max_v:
        return None
    # 亮度下限别写死 150：视频号手机端的**昵称与冒号是灰色**（实测 V≈126–145，正文才是
    # 纯白）。150 会把灰点整对滤掉，退回按字数插值 ⇒ 框成一个全角字符宽，看着就是
    # 「冒号没圈准」（实测 t=38.3「哎哎：…」）。100 仍明显高于暗背景噪声，误检交给
    # 下面的点对几何与孤立性判据挡（三平台样本上无新增误检）。
    # ``v_min`` / ``v_delta`` 默认值与旧行为相同；浅灰气泡底由平台档案显式调低。
    mask = (v >= max(v_min, bg + v_delta)).astype(np.uint8)
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    bh = band.shape[0]
    dots: list[tuple[int, int, int, int]] = []
    for i in range(1, n):
        dx, dy, dw, dh, area = (int(s) for s in stats[i])
        # 面积闸放到 3：视频号手机端的冒号是灰色（V≈130~145），点只有 2×2，抗锯齿后常见
        # 「缺一个角」⇒ 面积 3。写 4 会整类漏掉（实测 seg2 的 `哎哎：…` / `星星：…` /
        # `团子：…` 落到字数插值，框宽 25px）。点对几何（同列 ±2px、同宽高、竖向间隙
        # 3~14px）仍然很严，噪声凑不出这样一对。
        # 2026-11-05：再放到 2 —— 灰点抗锯齿更狠时下点只剩 2×1（实测 10.31 视频号
        # t=4637 `摸鱼的西红柿🍅：在泉州…`，上点 2×2 过闸、下点 2×1 被挡 ⇒ 整对丢掉，
        # 冒号框 None ⇒ 昵称与正文粘在一起）。同列/同尺寸/间隙/孤立性几道判据不变。
        if area < 2 or dw > 10 or dh > 12 or dh > 0.6 * bh:
            continue
        dots.append((dx, dy, dw, dh))
    found: list[list[int]] = []     # 孤立的两点（左右 3px 内没有别的文字块）
    loose: list[list[int]] = []     # 形态对、但有字贴着（打码昵称的 `*` 就贴在冒号左边 3px）
    for i, a in enumerate(dots):
        for b in dots[i + 1:]:
            if abs(a[0] - b[0]) > 2 or abs(a[2] - b[2]) > 2 or abs(a[3] - b[3]) > 2:
                continue
            top, bot = (a, b) if a[1] < b[1] else (b, a)
            gap = bot[1] - (top[1] + top[3])
            if not (3 <= gap <= 14):
                continue
            if 2 * max(top[3], bot[3]) + gap > 0.8 * bh:
                continue
            lx, rx = min(a[0], b[0]), max(a[0] + a[2], b[0] + b[2])
            pair = [x1 + lx, y1 + top[1],
                    x1 + max(a[0] + a[2], b[0] + b[2]), y1 + bot[1] + bot[3]]
            loose.append(pair)
            # 这一列左右 3px 内不许有别的文字块：汉字里的点、`i`、省略号的点都是半边
            if not any(c is not a and c is not b
                       and not (c[0] + c[2] < lx - 3 or c[0] > rx + 3) for c in dots):
                found.append(pair)
    if hint is not None:
        # 插值已经说了冒号大约在哪：以它为准挑**最近**的那个点对 —— 这样被字贴着的
        # 真冒号（`恬***`：星号右沿 132、冒号 135，差 3px ⇒ 孤立判据一整类全挡掉）
        # 也认得到，而正文/昵称里歪打正着的点对离得远，挑不上。
        near = [p for p in loose
                if abs((p[0] + p[2]) / 2 - float(hint)) <= _COLON_HINT_WINDOW]
        if near:
            near.sort(key=lambda p: (abs((p[0] + p[2]) / 2 - float(hint)),
                                     p not in found))     # 等距时优先孤立的那个
            p = near[0]
            return [p[0] - 3, p[1], p[2] + 3, p[3]]
    if not found:
        return None
    found.sort()                    # 最左那一个 = 昵称与正文之间的分隔冒号
    cx1, cy1, cx2, cy2 = found[0]
    return [cx1 - 3, cy1, cx2 + 3, cy2]
