"""livepipe.parsing.emoji.scan — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from ..emoji.gallery import EmojiGallery
from ..emoji.gallery import emoji_heights



def _token_scores_near(strip, gallery: EmojiGallery, box: list[int],
                       heights: tuple[int, ...], slack: int = 5) -> dict[str, tuple]:
    """在一个候选框附近（±``slack``）把**全表重算一遍**，给出每个 token 的最好成绩。

    为什么要重算全表、而不是拿粗筛的候选算：粗筛留下的候选只是一小撮峰，形近的那个
    常常**根本不在里面**（峰被抑制、或半尺寸下分数掉到线以下），于是「第二名」算出来是
    0.0，margin 虚高成满分 —— 等于**没有**区分度检验，而那正是形近对（``[比心]``/
    ``[爱心]``）会交错 token 的地方。这个函数把 margin 变回**真的**「第二名在这个位置
    能拿多少分」。

    Returns:
        ``{token: (score, gallery_index, box)}``（每个 token 只留最好的那个 hash）。
    """
    import cv2

    h, w = strip.shape[:2]
    bx, by, bw, bh = box
    wx1, wy1 = max(0, bx - slack), max(0, by - slack)
    wx2, wy2 = min(w, bx + bw + slack), min(h, by + bh + slack)
    win = strip[wy1:wy2, wx1:wx2]
    best: dict[str, tuple] = {}
    for i, g in enumerate(gallery):
        t0 = g["tmpl"]
        tok = g["token"]
        cur = best.get(tok)
        for dh in (-2, 0, 2):
            th = bh + dh
            if th < 16 or th > win.shape[0]:
                continue
            tw = max(6, int(round(t0.shape[1] * th / t0.shape[0])))
            if tw > win.shape[1]:
                continue
            t = cv2.resize(t0, (tw, th), interpolation=cv2.INTER_AREA)
            r = cv2.matchTemplate(win, t, cv2.TM_CCOEFF_NORMED)
            _mn, mx, _ml, ml = cv2.minMaxLoc(r)
            if cur is None or mx > cur[0]:
                cur = (float(mx), i,
                       [wx1 + int(ml[0]), wy1 + int(ml[1]),
                        wx1 + int(ml[0]) + tw, wy1 + int(ml[1]) + th])
                best[tok] = cur
    return best


def _box_saturation(strip, box) -> float:
    """框内的平均饱和度（HSV 的 S，0~255）——「彩色表情还是白色文字」的判据。

    官方表情是**彩色位图**；聊天区的文字（昵称、正文、系统公告）是白/浅色，三个通道
    几乎相等 ⇒ 饱和度 ≈ 0。整块文字与某些表情模板的 NCC 能到 0.75~0.82（微博实测
    ``车`` 对 ``[林致]`` 0.765），光看分数分不开；颜色上却是断崖（真表情 ≥111，白字 0）。

    只在**分数不够命名**时用它兜底（见 ``thresholds["min_saturation"]``）：真表情本来
    就过 ``min_score``/``min_margin``，不经过这一关；白色系模板（微博的「空星」「话筒」
    这类）只要形状分够高也照样命名 —— 不会因为这条判据被误杀。
    """
    import cv2

    x1, y1, x2, y2 = (int(round(float(v))) for v in box)
    h, w = strip.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 2 or y2 - y1 < 2:
        return 0.0
    return float(cv2.cvtColor(strip[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)[:, :, 1].mean())


def _nms_emoji_points(strip, pts: list[tuple], gallery: EmojiGallery,
                      heights: tuple[int, ...]) -> list[dict]:
    """候选点 → 互不重叠的表情框（含 token / guess）。

    两步：① 贪心 NMS 定位（分数从高到低留，与已留的框重叠面积 ÷ 较小框面积 > 0.4 就丢）
    —— 表情比的是**有几个、在哪**，这一步不需要 token 正确；② 每个框**全表重算**定
    token（见 ``_token_scores_near``），拿第一名与**别的 token 的第二名**之差当 margin。
    分数不够 ``gallery.thresholds["min_score"]`` 或 margin 不够 ``…["min_margin"]`` 就只给
    ``token: null`` + ``guess``（形近对见段首说明）。这些**认不出的候选**还要过一道颜色关
    （``thresholds["min_saturation"]``）：整块白字（S≈0）不可能是彩色表情，直接丢 ——
    否则一屏公告会冒出几十个 ``?[林致]``（见 ``_box_saturation``）。阈值取自**这张表**，
    不是全局常量。

    ②之前有一道**闸门**（见下面 ``< looks_like`` 那行）：候选自己那个模板的精验分数
    就不到 ``looks_like``，直接当文字丢，不跑全表重算 —— 这一步省掉的是整个表情识别
    里最大的一笔开销（见那行的注释）。
    """
    if not pts:
        return []
    looks_like = float(gallery.thresholds["looks_like"])
    min_score = float(gallery.thresholds["min_score"])
    min_margin = float(gallery.thresholds["min_margin"])
    min_sat = float(gallery.thresholds.get("min_saturation") or 0.0)
    pts.sort(key=lambda z: -z[0])
    keep: list[int] = []
    for k, p in enumerate(pts):
        bx, by, bw, bh = p[2], p[3], p[4], p[5]
        for j in keep:
            q = pts[j]
            qx, qy, qw, qh = q[2], q[3], q[4], q[5]
            inter = (max(0, min(bx + bw, qx + qw) - max(bx, qx))
                     * max(0, min(by + bh, qy + qh) - max(by, qy)))
            if inter > 0.4 * min(bw * bh, qw * qh):
                break
        else:
            keep.append(k)
    out: list[dict] = []
    for k in keep:
        bx, by, bw, bh = pts[k][2], pts[k][3], pts[k][4], pts[k][5]
        # 闸门：候选**自己那个模板**的精验分数就不够 ``looks_like`` ⇒ 是文字，丢。
        # 为什么可以这么判：全表重算（``_token_scores_near``，214 模板 × 3 高度 = 642 次
        # resize+matchTemplate）是这一层的**全部**开销，而它算出来的第一名**只有在候选
        # 自己分数够线时**才可能留得下（下面 ``score < looks_like`` 那一关）—— 一屏文字能
        # 过粗筛的候选有一大撮（本素材实测 NMS 后每行还留 11 个），逐个跑全表重算，算完
        # 全被那一关丢掉，白算 90%。分数分布本身就是双峰的（真表情 ≥0.90、杂项 ≤0.65，
        # 0.65~0.90 一个都没有，见段首），``looks_like``（抖音 0.78）正落在空谷里，所以
        # 拿它当闸门两边都留余量。代价只有「候选自己认错、别的模板反倒认得出来」这一种，
        # 实测 84 行 721 个被跳过的候选里**一次都没发生过**（同样分数下降 2.7 倍）。
        if pts[k][0] < looks_like:
            continue
        per = _token_scores_near(strip, gallery, [bx, by, bw, bh], heights)
        if not per:
            continue
        ranked = sorted(per.items(), key=lambda kv: -kv[1][0])
        tok, (score, gi, box) = ranked[0]
        second = ranked[1][1][0] if len(ranked) > 1 else 0.0
        if score < looks_like:                  # 跟这张表都不像 ⇒ 是文字，丢掉
            continue
        rec = {"box": [int(v) for v in box], "score": round(score, 3),
               "margin": round(score - second, 3), "guess": tok,
               "token": None, "name": None, "id": None}
        named = score >= min_score and score - second >= min_margin
        # 分数不够命名的候选里混着**整块白字**（形近撞上某个表情模板）：颜色判掉它，
        # 别让 `?[林致]` 这种框画到「车」字上、也别让 emoji_text 多出一个 `?`。
        # 因为只在这一支里判，真表情与白色系模板（分够就直接命名）都不受影响。
        if not named and min_sat > 0 and _box_saturation(strip, box) < min_sat:
            continue
        if named:
            rec.update({"token": tok, "name": gallery[int(gi)]["name"],
                        "id": gallery[int(gi)]["id"], "source": gallery.source})
        out.append(rec)
    out.sort(key=lambda r: r["box"][0])
    return out


def scan_emoji_strip(strip, gallery: EmojiGallery, heights: tuple[int, ...]) -> list[dict]:
    """一条行带 → 表情列表（框是**条带内坐标**，调用方再平移回原图）。

    为什么是「多模板滑窗」而不是「先分连通块，再逐块分类」：相邻两个表情（``👍👍``）按
    背景差分时是连成一块的，分块法要么并成一条、要么得猜从哪里切；滑窗法不切也知道有
    几个 —— 每个位置各留一个峰。文字与所有模板的 NCC 都低，靠 ``looks_like``
    自然滤掉，不需要额外的「这里是不是文字」判据。

    两段式（粗筛-精验）：全尺寸上 214 模板 × 3 高度直接滑一遍实测 **0.85s/行**（575x68
    的条带、1.3ms 一次 matchTemplate），换成半尺寸粗筛（位置数与每点开销各少 4 倍 ⇒
    快约 16 倍）把峰映射回原尺寸、再在原尺寸的**局部窗口**里精算一次，降到 ~0.1s/行。
    粗筛宁松勿严（``coarse_min`` 只负责**提议**位置），最终判定一律用精验出来的分数。
    两个粗筛参数都取自**这张表**（视频号的字形只有抖音的 4/5 大，半尺寸后更糊，
    用抖音的 0.55 会提议不出位置 ⇒ 同样静默空手）。
    """
    import cv2
    import numpy as np

    coarse_min = float(gallery.thresholds["coarse_min"])
    coarse_cap = int(gallery.thresholds["coarse_cap"])
    h, w = strip.shape[:2]
    if h < 24 or w < 20:
        return []
    ch, cw = h // 2, w // 2
    if ch < 12 or cw < 12:
        return []
    # ---- 粗筛：半尺寸 ----
    half = cv2.resize(strip, (cw, ch), interpolation=cv2.INTER_AREA)
    kernel = np.ones((3, 3), np.uint8)
    coarse: list[tuple] = []
    for i, g in enumerate(gallery):
        t0 = g["tmpl"]
        for H in heights:
            th = max(8, H // 2)
            tw = max(6, int(round(t0.shape[1] * th / t0.shape[0])))
            if th > ch or tw > cw:
                continue
            t = cv2.resize(t0, (tw, th), interpolation=cv2.INTER_AREA)
            r = cv2.matchTemplate(half, t, cv2.TM_CCOEFF_NORMED)
            rd = cv2.dilate(r, kernel)
            ys, xs = np.where((r >= coarse_min) & (r >= rd - 1e-6))
            for y, x in zip(ys, xs):
                coarse.append((float(r[y, x]), i, int(x) * 2, int(y) * 2, H))
    if not coarse:
        return []
    # ---- 精验：原尺寸，只在粗筛给的峰附近（±5px）搜 ----
    pts: list[tuple] = []
    for _, i, x, y, H in sorted(coarse, reverse=True)[:coarse_cap]:
        t0 = gallery[i]["tmpl"]
        for dh in (-2, 0, 2):
            th = H + dh
            if th < 16 or th > h:
                continue
            tw = max(6, int(round(t0.shape[1] * th / t0.shape[0])))
            if tw > w:
                continue
            t = cv2.resize(t0, (tw, th), interpolation=cv2.INTER_AREA)
            wx1, wy1 = max(0, x - 5), max(0, y - 5)
            win = strip[wy1:min(h, y + th + 5), wx1:min(w, x + tw + 5)]
            if win.shape[0] < th or win.shape[1] < tw:
                continue
            r = cv2.matchTemplate(win, t, cv2.TM_CCOEFF_NORMED)
            _, mx, _, ml = cv2.minMaxLoc(r)
            pts.append((float(mx), i, wx1 + int(ml[0]), wy1 + int(ml[1]), tw, th))
    return _nms_emoji_points(strip, pts, gallery, heights)


def merge_emoji_runs(emojis: list[dict], gap_ratio: float = 0.75) -> list[dict]:
    """相邻同款表情并成一条：``👍👍👍👍`` 是强调，不是四条独立语义 —— 计数进 ``n``。

    判据是「框挨着」且 key 相同（token，未识别的统一算 ``unknown``）：同一行内 x **间隙
    ≤ ``gap_ratio`` × 框高**，且 y 有重叠。y 那条是为了防跨行误并 —— 两行的表情框可能 x
    恰好接得上（每行都从左往右排），但 y 差一个行距，不是同一条。

    **间隙得按框高算，不能写死像素**：匹配框是**紧致字形框**，框间空隙 = 字形内留白 ×2，
    随字形宽窄变（实测 ``👍`` 框宽 34、间隙 14；``🌹`` 框宽 24、间隙 24 —— 表情的**步进**
    都是 48px，但空隙差 10px）。写死一个值就会「赞并得上、玫瑰并不上」，一条 5 朵玫瑰
    的数成了 5 条。按框高（38）的比例算，两者都过，而中间隔着文字时（间隔 ≥60px）不过。
    """
    out: list[dict] = []
    for e in sorted(emojis, key=lambda z: (z["box"][1], z["box"][0])):
        key = e.get("token") or "unknown"
        if out and (out[-1].get("token") or "unknown") == key \
                and abs(e["box"][1] - out[-1]["box"][1]) < 0.5 * min(
                    e["box"][3] - e["box"][1], out[-1]["box"][3] - out[-1]["box"][1]) \
                and e["box"][0] - out[-1]["box"][2] <= gap_ratio * max(
                    e["box"][3] - e["box"][1], out[-1]["box"][3] - out[-1]["box"][1]):
            prev = out[-1]
            prev["box"] = [prev["box"][0], min(prev["box"][1], e["box"][1]),
                           max(prev["box"][2], e["box"][2]), max(prev["box"][3], e["box"][3])]
            prev["n"] += 1
            prev["score"] = min(prev["score"], e["score"])   # 整条按最差的那个算
            continue
        e = dict(e)
        e["n"] = 1
        out.append(e)
    return out


def emoji_text_of(emojis: list[dict] | None) -> str:
    """表情列表 → 紧凑 token 串（``[赞][赞][爱心]``）。

    ``content``（OCR 文字）**不掺进来**：文字是原文、表情是识别结果，混在一个字段里
    下游就分不出哪个是读出来的、哪个是认出来的。空正文的纯表情评论靠这个字段补内容。
    """
    if not emojis:
        return ""
    return "".join((e.get("token") or "?") * int(e.get("n", 1)) for e in emojis)


def extract_row_emojis(frame_bgr, yc: float, pitch: int, x1: int, x2: int,
                       gallery: EmojiGallery) -> list[dict]:
    """一行 → 表情列表（**原图坐标**，已按相邻同款合并）。

    Args:
        frame_bgr: 整帧（BGR，``sample_frames`` 给的那种）。
        yc: 该行的 y 中心（原图 y）—— 传**这一行 OCR 框的中心**，不要用行网格算：
            网格可以偏到半个行距，而带子只有一个行距高，偏了就切掉表情（实测偏 21px
            时 👍 的下半截没了）。行带取 ``yc ± pitch/2``，比文字框高（表情 38 vs 文字 30）
            才不会上下切。
        x1/x2: 表情区的左右界（``x_text`` ~ 面板右边）。左边是等级徽章列，不是内容。
    """
    y1 = max(0, int(round(yc - pitch / 2)) + 2)
    y2 = min(frame_bgr.shape[0], int(round(yc + pitch / 2)) - 2)
    xa = max(0, int(x1))
    xb = min(frame_bgr.shape[1], int(x2))
    if y2 - y1 < 24 or xb - xa < 20:
        return []
    got = scan_emoji_strip(frame_bgr[y1:y2, xa:xb], gallery,
                           emoji_heights(pitch, gallery))
    for e in got:
        b = e["box"]
        e["box"] = [b[0] + xa, b[1] + y1, b[2] + xa, b[3] + y1]
    return merge_emoji_runs(got)
