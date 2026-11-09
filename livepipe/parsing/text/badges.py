"""livepipe.parsing.text.badges — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import re
import unicodedata

from collections import Counter

from ...account_id import is_masked, visible_name
from ..common.text import norm_text

# 行首等级徽章：「1 时死你」「10 胡杨」「2。hubvg」。只吃行首、最多 3 位数，**且数字后面
# 必须是分隔符或行尾** —— 否则 '7喜：好' 这种昵称会被啃掉首位的 7（旧正则确实啃了，
# 剥出来是 '喜'）。分隔符那一段是给「徽章和昵称被 OCR 并成一块」的情况用的：
# '2。hubvg:' / '2 1221：…' 要剥成 'hubvg' / '1221'。
_BADGE_RE = re.compile(r"^\s*\d{1,3}(?=[\s。.、·•]|$)[\s。.、·•]*")

# 「粉丝」徽章 —— 视频号手机 App 的行首药丸，也是**唯一一个 OCR 认不出是徽章的徽章**。
# 数字等级标（``✦1``）要么被 OCR 单独检出来当碎片丢掉（见 ``_is_junk``），要么按
# ``_BADGE_RE`` 从正文里剥掉；而「粉丝」两个字既不是数字也不是标点，于是**并进正文**
# （实测 100% 是这个形态）⇒ 昵称变成 ``粉丝踏雪归来``，交付名单里就是错的用户名。
# 参考交付语料里同一个人的名字是 ``踏雪归来``（2-2026-06-17 艾瑞泽8 视频号），所以这是
# 「交付物里的名字错了」，不是内部记账问题。
#
# 判据要 text + 几何**两条**，缺一不可：
#   · 文本：行首就是「粉丝」两个字 —— 它是徽章的前提；
#   · 几何：文字框左沿落在**行首徽章区**里（``box[0] <= fan_badge_x_max``）。
#     为什么非要几何闸：真昵称也可能以「粉丝」开头（「粉丝团团长」），只按文本剥就把它的
#     头两个字啃掉 —— 那比不剥更糟（名单里的用户名是错的，而且跟这个人在别的帧里的读法
#     对不上，会被算成两个人）。实测同一个人的三种读法就吃过这个亏：
#     ``A-幸运小福星`` / ``1A-幸运小福星`` / ``+1A-幸运小福星``（等级标并进正文）。
#
# 闸门值来自两簇**互不重叠**的实测左沿（2026-10-09 CTCC 视频号素材，720×1600 竖屏，
# seg1+seg3 共 723 行）：徽章行 x1 ∈ [32, 35]（「粉丝」药丸自己的左沿）· 真昵称行
# x1 ∈ [38, 45]（没有药丸的昵称顶在正文列；有药丸的昵称被药丸顶到 84~95，更远）。
# 取 37 夹在两簇中间。
_FAN_BADGE_RE = re.compile(r"^\s*粉丝[\s　]*")

# 等级药丸与「粉丝」药丸**一起**被 OCR 并进正文（视频号手机 App 的另一种版式：
# ✦N 在左、粉丝在右，2026-11-01 风云A9L 的三个账号实测）：行首会多出 `+6` / `1`
# 之类的前缀，文本判据 ``^粉丝`` 就匹配不上 ⇒ 昵称变成 ``+6粉丝若云``。
# 只在面板显式开了 ``fan_badge_with_level`` 时才用这条（见 ``_strip_fan_badge``）——
# 既有语料的面板没写这个键 ⇒ 行为逐字节不变（8PRO 的 260 行 `+N粉丝…` 保持原样）。
_FAN_BADGE_LEVEL_RE = re.compile(
    r"^\s*(?:[+✦★?￥.:：·]*\s*\d{1,3}\s*){0,3}[+✦★?￥.:：·]*\s*粉丝[\s　]*")


def _strip_fan_badge(row: dict, x_max: int, *, with_level: bool = False,
                     text: str | None = None) -> str:
    """剥掉行首的「粉丝」药丸徽章（**只在这个面板配了 ``fan_badge_x_max`` 时才调**）。

    Args:
        row: 一行（要 ``text`` 和 ``box``；``box[0]`` 是几何闸的判据）。
        x_max: 行首徽章块的左沿上限（面板配置 ``fan_badge_x_max``，见 ``_FAN_BADGE_RE``）。
        with_level: 允许行首再有**等级药丸前缀**（``+6粉丝…`` / ``1粉丝…``，面板配置
            ``fan_badge_with_level``）。默认关 ⇒ 与旧行为逐字节相同。
        text: 已经清洗过的行文本（例如先剥掉了粘连的顶部通知）；``None`` ⇒ 用 ``row["text"]``。

    Returns:
        剥完的行文本。**整行就是这个药丸时返回空串** —— 空行会被 ``classify_line`` 判成
        ``drop 空行``，正是我们要的：药丸块不是内容。留着它更糟，它不是 junk
        （见 ``_is_junk``：有内容的字），会被当**续行**粘到上一条评论的尾巴上。
    """
    if text is None:
        text = row.get("text") or ""
    rx = _FAN_BADGE_LEVEL_RE if with_level else _FAN_BADGE_RE
    if not rx.match(text):
        return text
    box = row.get("box")
    if not box or box[0] > x_max:
        return text          # 文字从昵称列起头 ⇒ 这两个字是昵称的一部分，不许啃
    return rx.sub("", text, count=1).strip()


# 系统通知：抖音自己发的行。
# ``入了直播间`` 不写 ``加入了直播间``：OCR 会把行首的「加」吃掉（实测 t=256
# '加入了直播间' 整条被读成 '入了直播间'）。写成 ``入了直播间`` 同时覆盖两种写法，
# 少了它这条被截断的通知就会当成「用户名丢了的评论」留下来（bug-229 的连带）。
# --------------------------------------------------------------------------- #
# 行首「等级药丸」并进正文 ⇒ 同一个人的多种读法被算成不同账号（bug-349）
# --------------------------------------------------------------------------- #
# 与上面那条「粉丝」药丸规则的分工：那条是**文本判据**（认得「粉丝」这两个字），管的是
# OCR 每次都把药丸并进去、**没有任何干净读法可比对**的情况；这条是**见证判据**，管的是
# 「同一行有时并进去、有时没并进去」的情况。
#
# 实测（720×1600 视频号手机 App，2026-10-12，seg1 的 5620 行）：
#   · 同一条评论会被读成最多 7 种昵称：`A-幸运小福星` x1=85 · `1A-幸运小福星` x1=44
#     · `+1A-幸运小福星` x1=42 · `★1A-…` / `?1A-…` / `￥1A-…` / 空名 x1=28
#   · 药丸画在昵称左边，所以**并进正文的读法，文字框左沿一定比没并进去的更靠左**
#     （药丸占位 ≈42~56px；而同一个读法自己的抖动只有 ≤3px）⇒ 同一行里 x1 最大的那版
#     就是干净的。这不是启发式，是版式的几何事实。
MIN_GAP = 20          # 两版文字框左沿至少差这么多像素才算「药丸占位」的证据。
                      # 见上：抖动 ≤3px、丢一个汉字 ≈24px、丢一个数字 ≈13px
                      # ⇒ 取 20 是为了把「OCR 丢了个行首字」排除掉（丢字同样会让框右移）。


def _is_badge_token(s: str) -> bool:
    """``s`` 像不像「一个等级药丸」的读数：`1`/`+1`/`+.1`/`✦1`/`￥1` 像，`11`/`A-`/`天空` 不像。

    按 **Unicode 类别**写而不是写死字符表：`?`(Po) `￥`(Sc) `★`(So) `+`(Sm) 都是真实读出来的
    （写死字符表的那版认得 `+1`、认不出 `?1`，rid 91 那一组 7 种读法就因为一个 `?1` 整组放弃）。
    两道**防误吃真实昵称**的闸门：① 含字母/汉字（L*）⇒ 判成名字（`11天空` `123亩176` 是本
    语料的真名）；② 「光秃秃的两位数字」判成名字 —— 等级是 1~2 位数，而 `11` 这种更像真名。
    """
    if not 1 <= len(s) <= 4:
        return False
    cats = [unicodedata.category(ch) for ch in s]
    if any(c.startswith("L") for c in cats):
        return False
    n_digit = sum(1 for c in cats if c == "Nd")
    if n_digit > 2:                                 # 等级最多两位
        return False
    has_other = any(not (c == "Nd" or c.startswith("Z")) for c in cats)
    if not n_digit and not has_other:
        return False                                # 全是空白
    return has_other or len(s) == 1


def _badge_x1(c: dict) -> float | None:
    """这条评论文字框的左沿。``text_box`` / ``roi`` / ``box`` 依次取（不同解析路径叫法不同）。"""
    for k in ("text_box", "roi", "box"):
        b = c.get(k)
        if b:
            return float(b[0])
    return None


def _badge_digits(s: str | None) -> str:
    """从一段药丸/徽章读数里取数字：``◆29`` → ``29``、``+7`` → ``7``、``）`` → ``""``。

    按 Unicode 类别取（``Nd``）而不是写死 ASCII —— OCR 认成 ``+``/``¥``/``?``/``）`` 的
    菱形图标都不挡路；取不出数字就返回空串，调用方据此判「这一次没读出等级标」。
    先过一遍 ``NFKC`` 把全角数字折成 ASCII（``２９`` → ``29``）：等级标要跟**并进昵称的
    那串数字**逐字符比，两种宽度混着会出现「读到了却比不中」的假不匹配。
    """
    return "".join(ch for ch in unicodedata.normalize("NFKC", s or "")
                   if unicodedata.category(ch) == "Nd")


def _badge_renames(comments: list[dict], stats: dict | None = None) -> dict[str, str]:
    """**取证**：从「同一条评论的多种读法」里得出 ``脏名字 -> 干净名字`` 的对照表。

    两条**互相独立**的证据通道，两条都要求**等级标一致**：

      * **A 同一 rid 组**（滚动对齐给的「同一行」身份）：同一行在不同帧里被读成不同昵称，
        其中一版更靠右、差的正好是一个行首药丸 token ⇒ 同一个人（bug-349）。
      * **B 同一句话落在不同 rid 上**：实测这是**常态**不是例外 —— seg1/seg6/seg7 三段里
        13 对「同一个人的两种读法」**全都是**相邻但不同的 rid（`11山***` rid 115 对
        `山***` rid 87、`19爱***` 112 对 `爱***` 111…）：两端内容一字不差，却被滚动对齐
        判成两条不同的行。A 通道**永远看不见**它们，只能靠「内容相同 + 文字框更靠左 +
        等级标对得上」这三样一起配。

    **为什么等级标能当身份**：抖音 App 的 ``◆N`` 是**粉丝团等级**，挂在人身上、与昵称绑定
    （实测同一个 ``山***`` 跨帧稳定读出 11）。所以 ``11山***`` 与 ``山***`` 算不算同一个人，
    判据就是那位 ``山***`` 读出来的等级标是不是 **11**；反过来 ``小明``（等级 6）与 ``1小明``
    （等级 1）发了同一句话也**不会**被并掉 —— 这正是「按正文/形状分组取证」在没有等级标时
    **不敢**用的那条路：两个不同的人碰巧都发「好看」，一个叫 `小明`、一个叫 `1小明`，
    没有等级标就只能靠形状赌，有了等级标就是对账。

    等级标**读不出来**时（OCR 只检出约 4 成的徽章块）退回形状判据 ``_is_badge_token``，
    与从前逐字节一致；读出来而**对不上**时一律**不并**（宁可漏归一，不可错归并）。

    ``stats``：可选，就地累加取证统计（``badge_ok`` 靠等级标认下的对数、``badge_pairs``
    B 通道命中数、``vetoed`` 因等级标不一致被否掉的样例）。
    """
    stat = stats if stats is not None else {}
    stat.setdefault("badge_ok", 0)
    stat.setdefault("badge_pairs", 0)
    vetoed: list = stat.setdefault("vetoed", [])

    # 每个名字**读出来的等级标**：只收读出了数字的读法。药丸被并进昵称的那些读法徽章块
    # 检不出来（读数为空）⇒ 这里收到的几乎都是干净读法的等级标，正是我们要的对照物。
    digits_of: dict[str, set] = {}
    for c in comments:
        u = c.get("username") or ""
        d = _badge_digits(c.get("badge_text"))
        if u and d:
            digits_of.setdefault(u, set()).add(d)

    renames: dict[str, str] = {}
    conflict: set[str] = set()                    # 两处给出不一致的对照 ⇒ 这个名字放弃

    def _add(u: str, v: str) -> None:
        if u in conflict:
            return
        if u in renames and renames[u] != v:
            conflict.add(u)                       # 两处说法不一致 ⇒ 这个名字放弃
            renames.pop(u, None)
            return
        renames[u] = v

    # ---- A. 同一 rid 组 -------------------------------------------------- #
    groups: dict[tuple, list[int]] = {}
    for i, c in enumerate(comments):
        if c.get("rid") is None:                  # 没有跨帧身份 ⇒ 没有「同一行」可比
            continue
        groups.setdefault((str(c.get("panel")), c["rid"]), []).append(i)
    stat["rid_groups"] = len(groups)
    for idxs in groups.values():
        right_of: dict[str, float] = {}           # 昵称 -> 它**最靠右**的一次文字框左沿
        badge_of: dict[str, set] = {}             # 昵称 -> 在这一组里读出来的等级标
        broke = False
        for i in idxs:
            x = _badge_x1(comments[i])
            if x is None:
                broke = True                      # 有一行没框 ⇒ 这一组没证据，别动
                break
            u = comments[i].get("username") or ""
            if not u:
                continue                          # 没名字的读法（纯表情行/续行）不参与比对
            if u not in right_of or x > right_of[u]:
                right_of[u] = x
            d = _badge_digits(comments[i].get("badge_text"))
            if d:
                badge_of.setdefault(u, set()).add(d)
        if broke or len(right_of) < 2:
            continue
        canon = max(right_of, key=lambda u: right_of[u])
        others = [u for u in right_of if u != canon]
        # 比的是「**别人**最靠右的一次」，不是「同一版自己的抖动」——同名 ±2px 不算数
        if right_of[canon] - max(right_of[u] for u in others) < MIN_GAP:
            continue
        cdig = badge_of.get(canon) or set()
        ok = True
        for u in others:
            # 把「多出来的那一小段」单独拎出来判（不用 match 啃整串：贪婪会先吃掉 4 个字，
            # 反而漏掉 `+.14于亚洲3` 这种带点的写法）
            p = u[:-len(canon)].strip() if u.endswith(canon) else ""
            pd = _badge_digits(p)
            if p and _is_badge_token(p):
                # 形状已经像药丸：**等级标读出来却对不上** ⇒ 不是一个用户（硬闸）
                if cdig and pd and pd not in cdig:
                    vetoed.append((u, canon, pd, sorted(cdig)))
                    ok = False
                    break
                continue
            # 形状不像药丸（`22阳***` 这种「光秃秃两位数」）：**只有**等级标读数一致才认。
            # 这就是「等级标一致才是一个用户」在取证上的落点 —— `_is_badge_token` 为了
            # 不啃 `11天空`/`123亩176` 这类真名必须挡两位数，但挡不住**有等级标对账**的情况。
            if p and pd and pd in cdig:
                stat["badge_ok"] += 1
                continue
            if p and pd and cdig:
                # 有等级标可比、且对不上 ⇒ 明确不并（留证，见调用处的日志）
                vetoed.append((u, canon, pd, sorted(cdig)))
            ok = False
            break
        if not ok:
            # 有一版对不上 ⇒ **这一组**证据作废。注意是「跳过这一组」而不是「整批作废」：
            # 实测 rid=143 那一组同时挂着「这圈速可以啊…」和另一个人的「西西…」（rid 分错了），
            # 整批作废会把 rid=91/147 那两组**有效**的证据一起扔掉（那样改完一个字都不会变）。
            continue
        for u in others:
            _add(u, canon)

    # ---- B. 跨 rid 的同一句话：内容 + 几何 + 等级标，三样都要 ------------ #
    buckets: dict[str, list[int]] = {}
    for i, c in enumerate(comments):
        body = norm_text(c.get("content") or "")
        if body:
            buckets.setdefault(body, []).append(i)
    for idxs in buckets.values():
        if len(idxs) < 2:
            continue
        right_of_b: dict[str, float] = {}         # 这份正文里，每个名字最靠右的一次
        for i in idxs:
            u = comments[i].get("username") or ""
            x = _badge_x1(comments[i])
            if u and x is not None:
                if u not in right_of_b or x > right_of_b[u]:
                    right_of_b[u] = x
        names = list(right_of_b)
        for u in names:
            for v in names:
                if u == v or not u.endswith(v):
                    continue
                p = u[:-len(v)].strip()
                pd = _badge_digits(p)
                if not p or not pd:
                    continue                      # 多出来的那一段没有数字 ⇒ 没有等级标可比
                if pd not in (digits_of.get(v) or set()):
                    continue                      # **等级标不一致 ⇒ 不是同一个用户**
                if right_of_b[v] - right_of_b[u] < MIN_GAP:
                    continue                      # 「没并进药丸」的那版必须明显更靠右
                stat["badge_pairs"] += 1
                _add(u, v)
    return renames


def normalize_badge_alias(comments: list[dict],
                          panels: set[str] | None = None) -> dict:
    """把「同一个人被读成多种昵称」收敛成一种：取**文字框最靠右**的那版（bug-349）。

    就地改写 ``comments`` 的 ``username``，返回统计（供日志取证）。
    ``panels``：只有这些面板参与（**没配的面板逐字节不动**）。``None`` = 全都参与。

    顺带**捡等级标**：被改掉的那一截就是并进昵称的药丸读数（`13白***` 的 `13`），
    对**没读到** ``badge_text`` 的记录补上（读到的逐字节不动）。同一件事的两面 ——
    「这一截是别人名字的一部分」还是「这一截是等级标」，答案只有一个，取证过了就一起用。

    两步：``_badge_renames`` **取证**（两条通道、以**等级标一致**为准，见那边的说明），
    拿到的对照表**全局应用**。为什么要全局应用（而不是只在取证的那一组里改）—— 实测 seg1
    的同一条评论「这圈速可以啊…」被滚动对齐拆成了 5 个 rid，干净读法只落在其中两个组里：
    只在组内改的话，另外 3 个组里的脏读法会原样留下，交付名单里同一个人依旧是两个账号
    （实测：只改组内时账号数 23→23、一个字都没变）。药丸长相与人的对应关系跟「哪一帧读到」
    无关，所以对照表一旦取到证据就该整块面板统一。
    证据不足时（没配面板 / 没有可比的两版 / 差距不够 / 等级标对不上）一个字都不改 ——
    宁可漏归一，不可错归并。
    """
    stat = {"groups": 0, "normalized": 0, "rewritten": 0, "renames": {},
            "badge_ok": 0, "badge_pairs": 0, "vetoed": [], "badge_from_alias": 0}
    if panels is not None and not panels:
        return stat
    # 取证只读不改，所以直接切片、不复制
    scoped = comments if panels is None else \
        [c for c in comments if str(c.get("panel")) in panels]
    renames = _badge_renames(scoped, stat)
    # 链式改写要收敛到根（`11小明->1小明` 与 `1小明->小明` 同时取证到时要能走到底），
    # 并且防打环（真打环说明证据自相矛盾，宁可全不改）。
    root: dict[str, str] = {}
    for k in renames:
        seen = {k}
        v = renames[k]
        while v in renames:
            if v in seen:
                return stat                       # 打环 ⇒ 证据矛盾，一字不改
            seen.add(v)
            v = renames[v]
        root[k] = v

    stat["groups"] = len(renames)
    stat["renames"] = {k: v for k, v in sorted(root.items())}
    for c in comments:
        if panels is not None and str(c.get("panel")) not in panels:
            continue
        u = c.get("username") or ""
        if u in root and u != root[u]:
            c["username"] = root[u]
            stat["rewritten"] += 1
            # 改名的同时**顺手把等级标捡回来**：被改掉的那一截就是并进昵称的药丸读数
            # （`13白***` 的 `13`）。它能进对照表，本身就已经过了取证（形状像药丸，或
            # 等级标跟干净那版对得上）—— 所以这一截的数字就是这位的等级。
            # 只在**没读到**等级标时补，读到的一律不动（那是 OCR 的直接读数，证据更强）。
            if not _badge_digits(c.get("badge_text")):
                d = _badge_digits(u[:-len(root[u])])
                if d:
                    c["badge_text"] = d
                    stat["badge_from_alias"] += 1
    if root:
        stat["normalized"] = len({root[k] for k in root})
    return stat


# --------------------------------------------------------------------------- #
# 昵称「尾巴」别名：同一条评论在不同帧被读成只差一个尾巴的两个名字
# --------------------------------------------------------------------------- #
#
# 现象（用户 2026-11-06 报）：交付树里 ``W-3`` / ``W-5``、``期待si-3`` / ``期待sj-26``
# 是同一个人，却被拆成两个账号。都是同一个机制：**同一个物理行在不同帧被 OCR 读成两个
# 只差尾巴的昵称** ——
#   · `W.` / `W`          末尾 2×3px 的句点被漏读（交付图上昵称后面是三个点：句点 +
#                         全角冒号的两点，句点确实画在像素里）
#   · `李素熵` / `李素`     末尾的 `熵` 整个漏读
#   · `期待sj` / `期待si`   `j` 的降部被**检测框**切掉 1px ⇒ 认成 `i`。重跑 OCR 可复现：
#                         同一条评论在 y=1016 的框是 1016..1039（降部画到 1040，被切）⇒
#                         `期待si`；滚到 y=975 框把降部包进去 ⇒ `期待sj`。同一份像素，
#                         跟「哪一帧留下」无关 —— 所以**按单帧选代表治不了**，只能按账号
#                         级的多数写法归一。
# 交付层按昵称**原文字符串**认人（见 ``account_id``），于是同一个人分成两档。
#
# 判据与 ``normalize_badge_alias`` 同一条路：只认**取证**，不猜相似度。取证 = 两个读法
# **在同一条评论的拍摄里同现**（同一个 ``rid`` 组、各至少 2 次）—— 那是同一行在相邻帧的
# 两种读法，不可能是两个人；再放过一道**等级标**硬闸（两边都读到等级标且不一致 ⇒ 不并）。
# 允许的「尾巴」差异只有三类（都实测过）：
#   · 多/少**一个**末尾字（`李素` ↔ `李素熵`）
#   · 多/少**一串纯标点**（`W` ↔ `W.`）
#   · **末尾一个字**被认成另一个（`期待si` ↔ `期待sj`；两边都要 ≥4 个字 —— 别把二字
#     昵称 `小明`/`小梦` 也并掉）
#
# 主写法取**整个面板里行数最多**的那版（OCR 多数次读对了）：10.31 视频号实测
# `W.` 192 vs `W` 68、`李素熵` 244 vs `李素` 53、`期待sj` 1343 vs `期待si` 42。
# 平票才退到「更长的那版」—— OCR 漏字比凭空多字常见，长的那版更可能是完整的。
# **行首药丸那一类不归这里管**（`+1八重樱`）：药丸的干净版是「文字框最靠右」的那版，
# 见 ``normalize_badge_alias``；它按行数多数反而会选中脏的那版，两套判据不能混。
_TAIL_EXTRA_CHARS = 1        # 多/少一个末尾字
_TAIL_PUNCT_CHARS = 3        # 或一串纯标点（`W` ↔ `W.`；全角/半角都要认）
_TAIL_PUNCT = set("。.．，,、·•…:：;；!！?？~～-—_ ")
_TAIL_SUB_MIN_LEN = 4        # 等长换尾字的最短长度（`期待si` = 4；二字昵称不参与）


def _tail_variant(u: str, v: str) -> bool:
    """两个昵称是不是「同一行被读出的两种相邻写法」（只差一个尾巴，见上一段）。"""
    if not u or not v or u == v:
        return False
    a, b = (u, v) if len(u) >= len(v) else (v, u)
    if a.startswith(b):
        tail = a[len(b):]
        if len(tail) <= _TAIL_PUNCT_CHARS and all(ch in _TAIL_PUNCT for ch in tail):
            return True                      # `W` ↔ `W.`（标点整段被切）
        # 多一个字：短名至少 2 字。否则 `王` ↔ `王强` 这种也会过 —— 单字昵称是两个
        # 不同的人完全有可能，截断证据撑不起这种归并。
        return len(tail) <= _TAIL_EXTRA_CHARS and len(b) >= 2
    return (len(a) == len(b) and len(a) >= _TAIL_SUB_MIN_LEN
            and a[:-1] == b[:-1] and a[-1] != b[-1])


def normalize_username_alias(comments: list[dict],
                             panels: set[str] | None = None) -> dict:
    """把「同一条评论的两种**相邻**昵称读法」认成同一个账号，按多数写法统一（opt-in）。

    做什么、为什么、主写法怎么选：见上一大段注释。就地改写 ``comments`` 的
    ``username``，返回统计（供日志取证）。

    **打码昵称另走一条**（用户 2026-11-08 定）：可见部分（星号前，通常就是第一个字）
    必须逐字 100% 相同（`小***` 与 `健***` 是两个名字；`小***` 与 `小**` 只是星号
    个数被 OCR 读错，可见名都是 `小`）；名字对上之后，两边等级标都要读到且一致才并。
    打码名底下本来就挂着几十个人，「只差一个尾巴」的形近通道对它不成立。

    ``panels``：只有这些面板参与（**没配的面板逐字节不动**）。``None`` = 全都参与。
    必须跑在 ``dedup_comments`` **之前**（与 ``normalize_badge_alias`` 同理：dedup 的
    文本身份键含用户名，名字不先统一，同一条评论的两种读法会被拆成两条 —— 那正是
    「一个人两个账号」的另一半原因）。
    """
    stat: dict = {"groups": 0, "pairs": 0, "rewritten": 0,
                  "renames": {}, "rows": {}, "vetoed": []}
    if panels is not None and not panels:
        return stat
    scoped = comments if panels is None else \
        [c for c in comments if str(c.get("panel")) in panels]

    # 每个名字在**整个面板**里的行数（多数写法的证据）与读到的等级标（硬闸）。
    rows_of: dict[tuple[str, str], int] = {}
    digits_of: dict[tuple[str, str], set] = {}
    for c in scoped:
        u = (c.get("username") or "").strip()
        if not u:
            continue
        k = (str(c.get("panel")), u)
        rows_of[k] = rows_of.get(k, 0) + 1
        d = _badge_digits(c.get("badge_text"))
        if d:
            digits_of.setdefault(k, set()).add(d)

    # 取证：同一个 rid 组里两个读法各 ≥2 次 + 只差尾巴 + 等级标不冲突。
    groups: dict[tuple, Counter] = {}
    for c in scoped:
        if c.get("rid") is None:                 # 没有跨帧身份 ⇒ 没有「同一行」可比
            continue
        u = (c.get("username") or "").strip()
        if u:
            groups.setdefault((str(c.get("panel")), c["rid"]), Counter())[u] += 1
    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(x: tuple[str, str]) -> tuple[str, str]:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (panel, _rid), cnt in groups.items():
        names = [u for u, n in cnt.items() if n >= 2]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                u, v = names[i], names[j]
                du = digits_of.get((panel, u)) or set()
                dv = digits_of.get((panel, v)) or set()
                if is_masked(u) or is_masked(v):
                    # 打码昵称（用户 2026-11-08 定）：可见部分（星号前的第一个字）
                    # 必须逐字 100% 相同，星号个数读错不算差异；名字对上后，
                    # **两边等级标都要读到且一致**才算同一个人。
                    if visible_name(u) != visible_name(v):
                        continue
                    if not (du and dv and (du & dv)):
                        stat["vetoed"].append((u, v, sorted(du), sorted(dv)))
                        continue
                else:
                    if not _tail_variant(u, v):
                        continue
                    if du and dv and not (du & dv):
                        # 等级标不一致 ⇒ **不是同一个用户**（与药丸别名同一条硬闸）
                        stat["vetoed"].append((u, v, sorted(du), sorted(dv)))
                        continue
                stat["pairs"] += 1
                a, b = find((panel, u)), find((panel, v))
                if a != b:
                    parent[a] = b

    # 每个连通块选主写法：面板内行数最多 → 更长 → 字典序（确定性）。
    clusters: dict[tuple[str, str], list[str]] = {}
    for node in list(parent):
        clusters.setdefault(find(node), []).append(node[1])
    for (panel, _root), names in clusters.items():
        if len(names) < 2:
            continue
        canon = max(names, key=lambda u: (rows_of.get((panel, u), 0), len(u), u))
        stat["groups"] += 1
        for u in names:
            if u != canon:
                stat["renames"][(panel, u)] = canon
                stat["rows"][(panel, u)] = rows_of.get((panel, u), 0)
                stat["rows"][(panel, canon)] = rows_of.get((panel, canon), 0)

    for c in comments:
        panel = str(c.get("panel"))
        u = (c.get("username") or "").strip()
        canon = stat["renames"].get((panel, u))
        if canon and canon != u:
            c["username"] = canon
            stat["rewritten"] += 1
    return stat
