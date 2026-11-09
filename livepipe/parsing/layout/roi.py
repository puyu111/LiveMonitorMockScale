"""livepipe.parsing.layout.roi — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from ..constants import BADGE_SPAN
from ..emoji.scan import emoji_text_of
from ..ocr.colon import colon_box_in_span
from ..text.notices import _MASKED_NAME_RE
from ..text.patterns import _INDENT_MAX_GAP_RATIO
from ..text.patterns import _INDENT_MIN_PX



# --------------------------------------------------------------------------- #
# 2. 版式解析：给每条评论标 ROI
# --------------------------------------------------------------------------- #
def _union(a: list[int], b: list[int]) -> list[int]:
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _roi_of(r: dict) -> list[int] | None:
    """一行（或一条合并后的评论）的 ROI：**含左侧等级徽章**的整行。

    徽章块被 ``group_rows`` 从正文里丢掉了（它不是内容），但它是「这一行」的一部分，
    缺了它出图和裁图都从昵称开始，等级标看不见。左边界取三者最小：

    1. ``badge_box`` —— OCR 真的把徽章检出来时的框（最准，含 ◆ 图标）；
    2. ``badge_left`` —— 徽章列的左沿（``x_text`` 往左量一个徽章宽度；宽度见平台档案
       ``badge_span``，默认 ``BADGE_SPAN``），**兜底**。
       必要性：实测左面板 50 行里只有 25 行检出了徽章块（小图标+小字号，OCR 时有时无），
       只认 (1) 就有一半的框圈不到等级标；
    3. 文字框自己的左沿 —— 没徽章的用户（昵称直接顶到面板左边）取它。

    上沿/下沿与右沿取**整行的并集**：文字框 ∪ ``badge_box`` ∪ 本行 ``emojis`` 的框。
    表情要算进来，否则纯表情评论（正文就是图片、OCR 框只剩打码昵称那一小截）圈出来的
    是一条细缝，表情全在框外。

    行里没有几何（纯文本模式）就返回 None。
    """
    box = r.get("box")
    badge = r.get("badge_box")
    left = r.get("badge_left")
    roi = _union(box, badge) if (box and badge) else (box or badge)
    # 表情框也算「这一行」的一部分：纯表情评论（正文就是图片，见 parse_live_rows 的
    # 「纯表情评论」分支）的 OCR 框可能只是打码昵称那一小截（实测 h=10），只按文字框
    # 出图/裁图会把表情整个切在框外 —— 用户看到的正是「表情认出来了，框却没圈住」。
    for e in r.get("emojis") or []:
        b = e.get("box")
        if b:
            roi = _union(roi, b) if roi else list(b)
    if roi and left is not None:
        roi = [min(int(roi[0]), int(left)), roi[1], roi[2], roi[3]]
    return list(roi) if roi else None


def badge_left_of(gx1: int, panel: dict) -> int:
    """徽章列左沿（ROI 兜底左边界）= ``x_text`` 往左量 ``badge_span``。

    ``badge_span`` 来自平台档案（``normalize_panels`` 合并进面板），缺省 ``BADGE_SPAN``。
    它只决定 **ROI 画到哪**，不参与徽章碎片的几何判据（那条仍用 ``BADGE_SPAN``）——
    手机 App 的 ``x_text`` 在药丸右沿外侧，需要比桌面版更宽的 span 才圈得住 ``◆``。
    """
    span = int(panel.get("badge_span") or BADGE_SPAN)
    return max(int(gx1), int(gx1) + int(panel["x_text"]) - span)


def refresh_badge_left(rows: list[dict], panels: dict[str, dict]) -> int:
    """按**当前面板档案**重算行序里的 ``badge_left``（ROI 左沿兜底）。

    ``badge_left`` 是抽行时写死进行序的。改了平台档案的 ``badge_span`` 之后，已有的
    ``rows.jsonl`` 走 ``--rows`` 复算 / 断点续跑时仍是旧值 —— 用户重跑同一条命令会
    看不到任何变化（实测「左侧等级标没有圈进去」就是这么复现的）。这里用行上的
    ``x_text`` 绝对锚点与当前面板档案重算，复算路径**零 OCR** 也能修好标注图/交付裁图。
    行/面板对不上（旧面板名、纯文本行）就跳过，返回值是实际改写的行数。
    """
    n = 0
    for r in rows:
        p = panels.get(r.get("panel"))
        if not p or "rect" not in p or "x_text" not in p:
            continue
        bl = badge_left_of(p["rect"][0], p)
        if r.get("badge_left") != bl:
            r["badge_left"] = bl
            n += 1
    return n


def _record(r: dict, username: str | None, content: str,
            box: list[int] | None, x_right, panel, sock) -> dict:
    badge = r.get("badge_box")
    roi = _roi_of(r)
    emojis = [dict(e) for e in (r.get("emojis") or [])]
    # 冒号框：新抽的行里带着块级估的位置；旧 rows.jsonl 没有它（本字段上线前抽的），
    # 就按整行框插值 —— --rows 复算不必为了一个标注框重跑 OCR。
    colon = (r.get("colon_box")
             or (colon_box_in_span(r.get("text") or "", box) if box else None))
    return {
        "username": username, "content": content,
        "roi": roi,
        "text_box": list(box) if box else None,
        # 分隔冒号的小框（昵称与正文之间的 ``:`` / ``：``）：标注图圈它，核「这一行为什么
        # 被切成昵称/正文」时最直观。续行没有冒号 ⇒ 合并时**不动**首行这一份。
        "colon_box": list(colon) if colon else None,
        "badge_box": list(badge) if badge else None,
        # 等级标读数（身份证据，见 _badge_renames）；续行不带 —— 徽章只画在首行
        "badge_text": r.get("badge_text"),
        "panel_right": x_right,
        "lines": 1, "sockets": [sock],
        "panel": panel, "t": r.get("t"), "x_text": r.get("x_text"),
        # emojis：这一行的表情（框 + token），**像素是在 rows_from_video 里算的** ——
        # 走 --rows 重算（没有像素）时靠行里带下来的这份原样传递，不然重算一次表情就没了。
        # content 只有 OCR 文字，纯表情评论的 content 是空串，靠 emoji_text 补内容。
        "emojis": emojis,
        "emoji_text": emoji_text_of(emojis),
        # rid：跨帧身份（见 assign_row_ids）。续行合并时**不动它** —— 身份取首行的。
        "rid": r.get("rid"),
        # motion_px：拍摄时面板的瞬时位移（内容触发模式标在行上）—— 去重时用来在同一条
        # 评论的多次拍摄里挑最清楚的那次。interval 模式没有它（None）。
        "motion_px": r.get("motion_px"),
    }


def _has_own_badge(r: dict) -> bool:
    """这一行在左侧徽章列里有**自己的等级徽章**吗（⇒ 它是新评论的首行，不可能是续行）？

    判据是**几何**，不是「OCR 有没有把徽章单独检出来」—— 后者只有约一半的行命中（见
    ``BADGE_SPAN`` 那段的说明），拿它当判据会漏掉一半。真正的几何事实是：

      * 有徽章的行，文字从徽章**右边**开始 ⇒ ``box[0] >= x_text``（左面板 ≈655 > 633）；
      * 续行没有徽章，文字从 ``x_text`` 开始 ⇒ ``box[0] ≈ x_text``。

    实测（150 行抽样，量徽章列 ``[x_text-52, x_text]`` 的平均亮度）：命中这条规则的行
    median 184、p10 98；未命中的 median 69、p90 124 —— 分得很开，没有中间地带。

    **为什么必须加这条**：``classify_line`` 只看文本，判「续行」的唯一依据是「没有冒号」。
    但 OCR 经常把昵称末尾的冒号吃掉（``緣起：`` → ``緣起``、``口万事顺李026：`` →
    ``口万事顺李026``），于是一条**新评论**被当成续行，并进上一条评论的正文里 ——
    两个用户的评论搅在一起。全片实测：614 个「被并进来的第二行」**全部** ``box[0] >=
    x_text``（即全部有自己的徽章），一个真续行都没有（bug-228）。

    ⚠️ **它成立的前提是「面板有左侧徽章列」**（抖音：徽章列在 ``x_text`` 左边、正文在右）。
    微博没有徽章列（等级标是跟在昵称右边的「榜N」药丸，``x_text`` 就是正文列左沿），
    折行跟首行都从这个左沿起 ⇒ 每一行都满足它，折行全被拦下。这类面板（``wrap_at_text_left``）
    的折行靠 ``_wrapped_socket`` 那条门放行，判据是**间距**而不是坐标 ——
    别再往这条判据上加补丁（那正是「拿坐标关系当语义判据」的老病）。
    """
    box, xt = r.get("box"), r.get("x_text")
    return bool(box) and xt is not None and int(box[0]) >= int(xt)


def _indented_continuation(open_rec: dict, r: dict, pitch: float | None = None,
                           prev_box: list | None = None,
                           base_box: list | None = None) -> bool:
    """手机版折行续行：第二行**缩进**到首行的**正文列**（昵称 +「：」之后），于是 ``box[0]``
    比首行明显右移，但仍在首行文字跨度之内。

    桌面网页版的续行**贴面板左沿**（``box[0] ≈ 0 < x_text``），走 ``_has_own_badge`` 那条
    老路就行；手机 App 版的续行右移进正文列（本素材 x1≈128 > x_text=70），会被
    ``_has_own_badge`` 误判成「新评论首行」而拒绝并入（这正是 2026-07 竖屏素材
    ``芋***：杨紫×4`` + 缩进续行被拆成两条的现场）。这里补一条**相对**判据：
    比首行右移**至少 ``_INDENT_MIN_PX``**、且没越过首行右沿 ⇒ 是折行续行，不是另起一列的新评论。

    **为什么必须有那个最小量**（原来只判「比首行右移」）：昵称列左沿本身有 OCR 抖动
    （微博实测同帧极差 median 4 / max 9px），于是「右移 1~2px」也被认成缩进 ——
    一整屏互不相干的评论被串成一串（实测 t=3124：3 条并成 1 条、4 条并成 1 条，
    画出来的 ROI 一框圈三行）。真续行的缩进是**一个昵称宽**（抖音竖屏 58px），
    两者差着量级，取 12 一刀切开（证据见 ``_INDENT_MIN_PX``）。

    **再加一道间距闸** ``pitch``（``_INDENT_MAX_GAP_RATIO``）：缩进对**独立评论**同样成立
    —— 昵称被 OCR 吃掉（掩码昵称 `**` 整块丢）时，正文正好落在昵称列的右边，形状与折行
    一模一样，光看 x 分不开（实测 seg2 t=0/20.07：`萌萌：五站八冠…` + `线下有品鉴会就好了`
    被并成一条，而同一句话在 t=45.5/52.8 明明白白是**另一个人**的独立评论）。两条评论
    按行网格排（间距 ≈1 个整行距），折行紧贴首行（≈0.75）—— 用间距分。
    ``pitch=None``（面板没有 ``row_pitch``）⇒ 只按形状判，与从前一字不差。

    两个判据量的**基准不是同一个**：缩进是相对**首行**的正文列（``base_box`` 的左沿，
    没传时退回 ``open_rec["text_box"]``），间距是相对**最后一行**（``prev_box``）——
    多行评论的并集中心落在两行之间，拿它当基准会把第三行算远半个行距（三行折行会被截成
    两行，见 selftest）。

    ⚠️ ``open_rec["text_box"]`` 是**并集**，并进折行后就**不再是首行左沿**：视频号贴左沿
    的折行（x1≈21）会把并集左沿拉到 21，下一条真评论（x1≈34）于是看起来「右移 13px」、
    被误并成同一条评论（2026-11-05 实测 10.31 视频号 t=4637：`+7派大星：…` 的两行 +
    下一条 `摸鱼的西红柿🍅：…` 的两行被圈进同一个 ROI，标注图第 5 条「把两条评论结合在
    一起」）。所以调用方要传 ``base_box`` = **首行**的文字框；这里再把基准抬到 ``x_text``
    （首行是「药丸并进正文」的读法、x1≈22 时，正文列才是真正的基准）。
    """
    ob, b = open_rec.get("text_box"), r.get("box")
    if not (ob and b):
        return False
    x1 = int(b[0])
    base_x = int((base_box or ob)[0])
    xt = open_rec.get("x_text")
    if xt is not None:
        base_x = max(base_x, int(xt))
    if not (base_x + _INDENT_MIN_PX <= x1 <= int(ob[2])):
        return False
    if pitch:
        ref = prev_box or ob              # 续行的**上一行**：间距要贴着它量
        oy = (int(ref[1]) + int(ref[3])) / 2
        y = (int(b[1]) + int(b[3])) / 2
        if y - oy > pitch * _INDENT_MAX_GAP_RATIO:
            return False
    # 但缩进在跨度内**也可能是另一条新评论**（昵称的冒号被 OCR 读成别的、或整行被当成通知
    # 却没命中 _NOTICE_RE，如「A***分享了直播」）。这一类的共同特征：开头是**掩码昵称**
    # 「X***」（抖音把昵称中间打码成 ***）。缩进续行是正文，不含这种前缀 —— 用它把新行挡回去。
    # 星号数按 ``_MASKED_NAME_RE``（≥2）取，别跟着抖音写死 3 颗：视频号打的是 1~2 颗，
    # 写 3 颗时 ``**.线下有品鉴会就好了`` 就挡不住（实测 seg2 t=30.1 被并进上一条）。
    txt = (r.get("text") or "").strip()
    return _MASKED_NAME_RE.match(txt) is None


def _wrapped_socket(sock) -> bool:
    """这一行的槽位是 ``group_rows`` 给折行续行的小数槽位（``k±0.5``）吗？

    小数槽位只有一个来源：``group_rows`` 里那个块**过了间距闸**（``_is_true_wrapped_line``
    —— 跟上一行 0.15~0.73 个行距），才被摘出普通槽、挂上 ``k±0.5``（两个入口，见
    ``group_rows`` 的说明）。所以「小数槽位」= **几何上已经证明它是折行**，比任何坐标
    判据都硬。

    **为什么合并那条路上需要它**：``_has_own_badge`` 判的是「文字从徽章列右边起 ⇒ 自带
    徽章 ⇒ 新评论的首行」，这条判据**要求面板有左侧徽章列**。微博没有（等级标是跟在昵称
    右边的「榜N」药丸，``x_text`` 就是正文列左沿 = 38），折行也从同一个左沿起（实测折行
    首块 x1 ∈ [38,43]）⇒ 微博**每一行**都满足 ``box[0] >= x_text``，所有折行都被那条判据
    挡在门外：用户报的 t=3836.8 第 8/9 行（`昵称不能为空513：这个价格…` + `满满啦`）就是这样，
    全量 91 对折行**一对都没并上**。

    对抖音是无副作用的：那条路上来的折行块按形状判据必然 ``x1 < x_text``（贴裁切左沿，
    或在缩进带里），所以 ``_has_own_badge`` 本来就是 False，这一条只是等价的保险。
    """
    return isinstance(sock, float) and sock != int(sock)
