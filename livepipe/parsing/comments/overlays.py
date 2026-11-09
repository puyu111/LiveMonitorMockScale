"""livepipe.parsing.comments.overlays — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import re
import statistics

from ..text.notices import overlay_card_re



# --------------------------------------------------------------------------- #
# 6. 行 → 评论（含跨帧去重）
# --------------------------------------------------------------------------- #
def overlay_card_re_for(panels: dict[str, str | None] | None,
                        panel) -> re.Pattern | None:
    """这个面板的浮层卡判据（不在表里 ⇒ ``None`` = 不认浮层卡）。"""
    if not panels or panel not in panels:
        return None
    return overlay_card_re(panels[panel])


# 浮层卡的第二条判据：**列表滚了、它没动**（跨帧；不用任何素材词表） -----------------
_FROZEN_SCROLL_RATIO = 0.25     # 列表位移 ≥ 这么多倍行距 ⇒ 这一对帧算「真滚了」
_FROZEN_STILL_RATIO = 0.2       # 该行自己的位移 < 这么多倍行距 ⇒ 算「它没动」
_FROZEN_MIN_MATCHED = 4         # 一帧对里至少这么多行两帧都读到，位移中位数才算数
_FROZEN_MIN_EVENTS = 2          # 至少被这么多次滚动事件投票「没动」才判浮层
_FROZEN_MIN_FRAMES = 3          # 该文本至少出现这么多帧
_FROZEN_BOX_RATIO = 0.25        # 盒键：y 中心相差 < 这么多倍行距 ⇒ 认成「同一条带上的同一条」
_FROZEN_BAND_SHARE = 0.5        # 盒键的兜底闸：这条带的「没动」票要占真滚动帧对的这么多比例
_FROZEN_BAND_MERGE = 3.0        # 数票时把 ±这么多像素的观测当同一条带（y 实测只飘 1~2px）


def frozen_overlay_texts(rows: list[dict], *, panels=None,
                         row_pitch: dict[str, float] | None = None) -> dict[str, dict]:
    """浮层卡的**第二条判据**：列表滚动了，而这一行没动 ⇒ 它是压在列表上的浮层。

    为什么要有它（短语表不够）：广告气泡的**品牌行**没有任何通用特征词 ——
    素材一换短语表就失效（``奇瑞汽车`` → ``2027款艾瑞泽8PRO``，实测 2026-10-12）。
    而气泡的**位置**是通用的：它挂在屏幕同一条带上，列表滚动时它一格不动。
    实测（8pro seg2/seg3/seg6，真几何）：气泡行在 20 帧里 y 恒为 1284（品牌行）/
    1320（CTA），而列表每次滚动的位移恰好是行距的整数倍（−50.5 / −101 / −152）。
    同一判据扫全部 12 本语料，命中的**全是真浮层**（广告气泡、录屏机的
    ``电池电量不足`` 系统弹窗、抖音底部「XX来了」横幅），没有一条真评论被误判。

    判据（逐面板，只看**无冒号**的行）：
    ① 相邻两帧里两帧都读到的行 ≥ ``_FROZEN_MIN_MATCHED`` 条 ⇒ 这一对帧的
       **列表位移** = 它们 y 差的中位数；
    ② |位移| ≥ ``_FROZEN_SCROLL_RATIO`` × 行距 ⇒ 这一对帧是**真滚动**（列表没动的
       帧对上「没动」说明不了任何事，直接跳过 —— 这是它不误伤静止列表的关键）；
    ③ 某文本在这对帧里两帧都在、自己的位移 < ``_FROZEN_STILL_RATIO`` × 行距
       ⇒ 它「没动」，投一票；
    ④ 得票 ≥ ``_FROZEN_MIN_EVENTS``、跨帧 y 极差 ≤ ``_FROZEN_STILL_RATIO`` × 行距、
       出现帧数 ≥ ``_FROZEN_MIN_FRAMES`` ⇒ 判浮层。

    ①②的闸门值**必须一大一小**（``_FROZEN_SCROLL_RATIO > _FROZEN_STILL_RATIO``）：
    真行是**跟着列表一起走**的，列表挪了 ≥0.25 行距，真行就也挪了那么多 ⇒ 它自己不满足
    「位移 <0.2 行距」⇒ 不投票；只有真没动的才投得出来。两个闸都**必须比 OCR 抖动大**
    （实测跨语料静止帧的位移中位数是 0.0），又**必须比「慢滚」小** —— 8pro seg4/seg5 的
    列表是**一帧挪十几像素**地滚（位移中位数 −4 ~ −35px），早先取 0.8 倍行距（40px）时
    这些帧对全被判成「没滚」，判据静默失效、顶部横幅行整段漏下来当匿名评论。

    **两条键：文本键 + 盒键（带键）。** 气泡上的字 OCR 常常每帧读得不一样（实测 8pro seg3：
    同一条气泡读成 ``天生智领新境2027款艾瑞泽8PRO`` / ``领新境2027款艾瑞泽8PRO`` /
    ``智领新境2027款艾瑞泽8PRO``；seg4/seg5 顶部横幅更是**每帧都不同**，因为 OCR 把横幅和
    「XX来了」入场条吞成了一条）—— 光按文本配对会漏。钉死的是**位置**，所以再按「同一条
    y 带上有一条无冒号行没动」投一次票。

    **只比 y，不比 x。** 试过「y + 左沿或右沿对上」：气泡被 OCR 多吞一块时**两条竖边都在
    飘**（seg3 的品牌行右沿 369→678、左沿跟着「广告」标签飘到 40；seg6 的 t=51.3 同理，
    文本读成 ``2027款艾瑞泽8PRO++``），按边配对要么漏要么松到误伤 —— 而「带」是那个真正
    钉死的量（实测气泡带 y 恒 1284/1320，横幅带恒 780）。

    带键松，所以它要过**三道闸**：「真滚动」+ 得票 ≥ ``_FROZEN_MIN_EVENTS`` +
    ⑤ **这条带的「没动」票要占真滚动帧对的 ≥ ``_FROZEN_BAND_SHARE``**。
    ⑤ 是这条判据的**精度来源**：浮层是**整段一直压着**的，而网格上的巧合碰撞是**偶发**的
    （列表正好滚整数倍行距时，前一帧的某一「行位」上会有一条**完全不同的行**，也会被算成
    「这条带上有东西没动」）。
    **取证**（``panels`` 闸门全开、扫 12 本语料的最终 comments，开关差分）：
    不带 ⑤ 时抖音少 61 条、微博少 971 条（几乎全灭）；带 ⑤ 之后是
    **抖音 2 条 + example3 1 条 + 微博 56 条**。抖音/example3 那 3 条是**真的**浮层
    （录屏机的 ``电池电量不足`` / ``请尽快接通电脑电源。`` 系统弹窗，同一句）；
    微博那 56 条是**微博桌面版自己的常驻条**（``榜N`` 药丸那一片，带票率过了 0.5），
    说明这条判据在**没为它做过取证的版式上**仍会多摘。
    ⇒ 所以它跟短语表一样**挂平台档案**（``overlay_cards``）：只为做过取证的版式开，
    生产路径上 12 本语料的开关差分是**零误伤**（见 ``comments_from_rows``）。
    真浮层的带票率实测 64%~100%（8pro seg4 顶部横幅 9/14、seg2 气泡 4/4、seg6 气泡
    13/14），偶发带最高只到 25%（抖音 y=696、微博 y=1812）—— 0.5 这条线是照这两组数
    划的，两边都留了一倍余量。

    为什么只认无冒号行：有 ``昵称：正文`` 的行**按定义**就是评论 —— 它出现在这里只能是
    同名文本的别名（微博那种会复用 y 的版式上量到过），拿它换精度不亏。

    摘行时用的是同一套判据（``comments_from_rows._is_frozen`` 也先看冒号），**两边必须
    一致** —— 投票时跳过、消费时不跳过，就是一开始那版「微博少 971 条」的写法。

    返回 ``{面板名: {"texts": {文本}, "bands": [y中心], "pitch": 行距}}``
    （面板不在 ``panels`` 里 ⇒ 不参与，抖音/微博行为一字不变）。
    """
    keep_panels = set(panels or ())
    if not keep_panels:
        return {}
    by_panel: dict[str, dict[float, list[dict]]] = {}
    for r in rows:
        if r.get("panel") in keep_panels and r.get("t") is not None:
            by_panel.setdefault(r["panel"], {}).setdefault(r["t"], []).append(r)
    out: dict[str, dict] = {}
    for panel, frames in by_panel.items():
        # 只收**有文字**的行：纯表情行的 text 是空串（见 rows_from_video 的空槽补扫），
        # 混进来会让所有空行挤成同一个键 ""，位移/得票全是假的；而 '' 一旦进了
        # ``texts``，``_is_frozen`` 的文本键会把**每一条**纯表情评论都当浮层摘掉。
        tex = {t: {r["text"]: (r["box"][1] + r["box"][3]) / 2
                   for r in rs if (r.get("text") or "").strip()}
               for t, rs in frames.items()}
        pitch = (row_pitch or {}).get(panel)
        if not pitch:
            # 面板档案没给行距就现场标：相邻行中心的间距取**众数**。中位数会被折行带偏
            # （折行是 0.75 个行距，多的那一帧就把尺子量歪了）。
            hist: dict[int, int] = {}
            for per in tex.values():
                ys = sorted(per.values())
                for a, b in zip(ys, ys[1:]):
                    if b - a > 4:
                        hist[round(b - a)] = hist.get(round(b - a), 0) + 1
            if not hist:
                continue
            pitch = float(max(hist.items(), key=lambda kv: kv[1])[0])
        ts = sorted(frames)
        votes: dict[str, int] = {}
        still_y: dict[int, set[int]] = {}     # 取整 y 中心 → 投过「这一带有东西没动」的帧对序号
        n_pairs = 0
        for i, (a, b) in enumerate(zip(ts, ts[1:])):
            both = tex[a].keys() & tex[b].keys()
            if len(both) < _FROZEN_MIN_MATCHED:
                continue
            deltas = [tex[b][x] - tex[a][x] for x in both]
            if abs(statistics.median(deltas)) < _FROZEN_SCROLL_RATIO * pitch:
                continue                      # 列表本来就没滚 ⇒ 这一对帧什么也证明不了
            n_pairs += 1
            for x in both:
                if abs(tex[b][x] - tex[a][x]) < _FROZEN_STILL_RATIO * pitch:
                    votes[x] = votes.get(x, 0) + 1
            # 盒键（文本每帧读得不一样时的兜底）：**这条 y 带上有一条无冒号行没动**。
            # 只比 y，不比 x —— 气泡被 OCR 多吞一块时两条竖边都在飘（见函数注释），
            # 「带」才是钉死的那个量；带上的一条行到底是不是同一个盒子，靠 ⑤ 的票率兜。
            for r in frames[a]:
                # 有冒号的是评论；没有文字的（纯表情行）没有「字」可配，浮层判据对它不成立
                if "：" in r["text"] or ":" in r["text"] or not r["text"].strip():
                    continue
                ya = (r["box"][1] + r["box"][3]) / 2
                for r2 in frames[b]:
                    if "：" in r2["text"] or ":" in r2["text"] or not r2["text"].strip():
                        continue
                    if abs((r2["box"][1] + r2["box"][3]) / 2 - ya) <= _FROZEN_BOX_RATIO * pitch:
                        still_y.setdefault(round(ya), set()).add(i)
                        break
        frozen: set[str] = set()
        for txt, n in votes.items():
            if n < _FROZEN_MIN_EVENTS or "：" in txt or ":" in txt:
                continue
            ys = [per[txt] for per in tex.values() if txt in per]
            if len(ys) < _FROZEN_MIN_FRAMES:
                continue
            if max(ys) - min(ys) > _FROZEN_STILL_RATIO * pitch:
                continue                      # 跨帧 y 不恒定 ⇒ 是同名不同条，不是浮层
            frozen.add(txt)
        # ⑤ 盒键的兜底闸：这条带的「没动」票要占**真滚动帧对**的一大半。y 会飘 1~2px，
        # 所以先把 ±``_FROZEN_BAND_MERGE`` 内的观测并起来再数覆盖了几对帧（同一对帧只算
        # 一次）。这个窗口**不能开成 0.25 行距** —— 那样会把相邻的几条不同带并成一条，
        # 微博上实测会把 20~25% 的偶发带抬到 50% 以上、误摘 68 条。
        need = max(_FROZEN_MIN_EVENTS, _FROZEN_BAND_SHARE * n_pairs)
        bands: list[int] = []
        for y in sorted(still_y):
            cover: set[int] = set()
            for y2, ps in still_y.items():
                if abs(y2 - y) <= _FROZEN_BAND_MERGE:
                    cover |= ps
            if len(cover) >= need:
                bands.append(y)
        if frozen or bands:
            out[panel] = {"texts": frozen, "bands": bands, "pitch": pitch}
    return out
