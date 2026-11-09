"""livepipe.tools.merge_segments.align — 跨段账号归一（名字相似 + 证据复核）。

为什么需要这一步：多段素材是**逐段解析**的，同一场直播的同一个人，在 A 段可能被读成
干净昵称 `八重樱`，在 B/C 段被读成「药丸并进昵称」的 `+1八重樱`，或者末字被检测框切掉
的 `路顺风`。解析层的 ``normalize_badge_alias`` / ``normalize_username_alias`` 只能在
**段内**取证（同一条评论的相邻帧）；段内一次干净读法都没有时它就无能为力，而 qualify
又按昵称原文合并 —— 同一个人在交付树里就成了两个目录。

判据分两步，**先入围、再复核**：

1. **入围**：名字（忽略大小写）相似度 ≥0.7，或两者构成「前缀/后缀」关系（药丸前缀、
   头部被切掉一个字）。相似度只是候选，不是结论。
2. **复核**（至少满足一条才合并）：
   * **共用正文**：两个账号发过**同一句话**（归一化后完全相等）。直播评论里同句文案
     可能被多人复制，所以它单独不足以定案 —— 但和下面任一条叠加就是强证据。
   * **带数字的药丸前缀**：`U = 前缀 + V`，前缀是「字形 + 数字」（`+1` / `★1` / `￥1`）
     ⇒ 直接认（真实昵称几乎不会以这些字形开头）。
   * **纯数字前缀**：`7用户…` / `11义节` / `163358` —— 形状上与真实数字昵称无法区分，
     必须另有证据：两账号共用正文（≥1 条）或前缀数字 == 读出的等级标。
   * **无数字的标点前缀**（`-` / `~` / `）` / `？？？`）**不算药丸**：这些字符真实昵称
     里也常见（`-风-`、`~龙姐`），前缀关系只当入围线索，必须和「只差一个字符 + 共用
     正文 ≥2」叠加才合并 —— 否则 `-风-`(23 条) 会被并进 `风-`(1 条)。
   * **只差一个字符**（插入/删除/替换）：头部截断（`路顺风`/`一路顺风`）、字符误读
     （`谯蕉美食`/`谯谯美食`）—— 要 ≥2 条共用正文才算。
   * **仅大小写不同**（`Vkgkgnf`/`vkgkgnf`）⇒ 直接认。

**等级标硬闸**：两边都读到等级标且不一致 ⇒ 不并（与解析层的药丸别名同一条）。

**打码昵称（匿名）另有一条更强的闸**（用户 2026-11-08 定）：可见部分（星号之前，即屏幕上
唯一露出的那几个字）必须**逐字 100% 相同** —— `小***` 与 `健***` 是两个名字；星号个数被
OCR 读成不同不算差异（`小***` / `小**` 是同一个可见名）。名字对上之后，还要**等级标一致**
（两边都读到且相同）才算同一个人；有一边没读到就不并。形近、大小写、共用正文这些通道对
打码名**一律关闭**：打码名底下本来就是几十个人，共用一句模板文案是常态，不是证据。
药丸前缀（`15木***` -> `木***`）不受这条影响 —— 剥掉的是画在徽章列里的药丸，剩下的名字
逐字相同，前缀数字就是这张行的等级标（见 ``promote_prefix_level``）。

主写法（canon）的选择：
* 药丸/数字前缀关系 ⇒ 选**被加前缀的那个**（`八重樱`、`用户6758305`、`63358`）——
  前缀是画在徽章列里的药丸，不是名字；
* 头部截断 ⇒ 选**更长**的那版；
* 其余 ⇒ 选**评论条数更多**的那版（平票按更长的，再按字典序，保证确定性）。

所有重命名**整批跨段应用**（不只是取证的那一段），否则等于没修。
"""

from __future__ import annotations

from collections import defaultdict
import difflib

from ...account_id import is_masked, visible_name
from ...parsing.text.badges import _is_badge_token
from ...parsing.common.text import norm_text


_SIM_MIN = 0.7          # 入围阈值（用户口径：相似度 0.7 以上都要再比对一遍）
_SHARED_MIN = 2         # 「只差一个字符」/「相似」这两类要的共用正文条数
_SHARED_MIN_DIGIT = 1   # 纯数字前缀要的共用正文条数（形状本身不可区分）
_CURRENCY = set("￥¥$﹩")  # 金币徽章被读成的货币符号（见 _prefix_kind）


def _name(u: dict) -> str:
    return str(u.get("username") or "").strip()


def _level(u: dict) -> str:
    return str(u.get("level") or "")


def _n_comments(u: dict) -> int:
    return int(u.get("n_comments") or len(u.get("comments") or []))


def _contents(u: dict) -> set[str]:
    out = set()
    for c in u.get("comments") or []:
        b = norm_text(c.get("content") or "")
        if b:
            out.add(b)
    return out


def _one_edit(a: str, b: str) -> bool:
    """编辑距离是否正好 1（插入 / 删除 / 替换一个字）。"""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(x != y for x, y in zip(a, b)) == 1
    if la > lb:
        a, b = b, a
    i = j = 0
    skipped = False
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        if skipped:
            return False
        skipped = True
        j += 1
    return True


def _prefix(a: str, b: str) -> str | None:
    """``a = p + b`` 时返回药丸/数字前缀 ``p``（否则 None）。"""
    if not b or not a.endswith(b) or a == b:
        return None
    p = a[:-len(b)].strip()
    return p or None


def _prefix_kind(p: str) -> str | None:
    """前缀属于哪一类药丸（None = 不是药丸）。

    * ``strong``：**带数字**的药丸字形（`+1` / `★1` / `￥1` / `14=`）——真实昵称
      几乎不会这么开头，可以直接认；
    * ``digit``：1~3 位纯数字（`7` / `11` / `163`）——和真实数字昵称同形，要另有证据；
    * ``weak``：**不带数字的标点**（`-` / `~` / `）` / `？？？`）——真实昵称里很常见
      （`-风-`、`~龙姐`），只能当入围线索，必须走正文/形近复核，不能直接并。

    快手之所以特别需要这一档：它的药丸是**行内**的，昵称本身就可能以 `-`/`~` 开头，
    而视频号/抖音的药丸在独立徽章列里，`）0176` 这种单体标点前缀多半是徽章。
    但即便在快手，``）0176`` 与 ``0176`` 共用 3 条正文时仍会经「只差一个字符」合并。
    """
    if not p:
        return None
    if p.isdigit():
        return "digit" if len(p) <= 3 else None
    if not _is_badge_token(p):
        return None
    # 金币/赏字图标（快手粉丝团徽章）会被读成货币符号：`￥63358` / `$63358`。
    # 真实昵称几乎不会以货币符号开头，即使没带数字也算强药丸。
    if any(ch in _CURRENCY for ch in p) and all(
            ch.isdigit() or ch in _CURRENCY for ch in p):
        return "strong"
    return "strong" if any(ch.isdigit() for ch in p) else "weak"


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def consolidate_users(raw: dict[int, list[dict]],
                      *, promote_prefix_level: bool = False) -> dict:
    """就地合并 ``raw``（``{段号: users.jsonl 记录}``）里同一个人的多种昵称。

    返回统计（供日志）：``renames``（``脏名 -> 主写法``）、``groups``、``merged``、
    ``skipped``（入围但复核没过的对，留证）、``level_from_prefix``（从数字药丸前缀补回
    等级标的记录数）。

    ``promote_prefix_level``：``account_key`` 按等级认人时（``users.json`` 的
    ``identity_panels`` 非空）打开 —— 被剥掉的**数字药丸前缀**就是这张行读到的等级标
    （`15木***` 的 `15`），补回 ``level`` 之后它才会和同一等级的干净读法落进同一个账号。
    不按等级认人的老口径保持 ``level=""``，否则 username 口径会被这些前缀悄悄拆成多个账号。
    """
    stat: dict = {"groups": 0, "renames": {}, "merged": 0, "skipped": [],
                  "evidence": {}, "details": [], "level_from_prefix": 0}
    # 账号级视图：名字 -> 总评论数 / 正文集合 / 等级标集合 / 出现过的段
    rows_of: dict[str, int] = defaultdict(int)
    contents: dict[str, set[str]] = defaultdict(set)
    levels: dict[str, set[str]] = defaultdict(set)
    segs_of: dict[str, set[str]] = defaultdict(set)
    for i, users in raw.items():
        for u in users:
            n = _name(u)
            if not n:
                continue
            rows_of[n] += _n_comments(u)
            contents[n] |= _contents(u)
            lv = _level(u)
            if lv:
                levels[n].add(lv)
            segs_of[n].add(str(i))
    names = sorted(rows_of)

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    edges: dict[tuple[str, str], str] = {}

    def try_pair(a: str, b: str) -> None:
        la, lb = levels.get(a) or set(), levels.get(b) or set()
        if la and lb and not (la & lb):
            stat["skipped"].append((a, b, "等级标不一致"))
            return
        shared = len(contents.get(a, set()) & contents.get(b, set()))
        p_ab = _prefix(a, b)
        p_ba = _prefix(b, a)
        p = p_ab or p_ba
        pk = _prefix_kind(p) if p else None
        if pk in ("strong", "digit"):
            dirty, base = (a, b) if p_ab else (b, a)
            if pk == "digit":
                # 纯数字前缀：形状分不清「药丸 7」和「名字就是 7…」，要另有证据
                d = la | lb
                if shared < _SHARED_MIN_DIGIT and p not in d:
                    stat["skipped"].append((a, b, f"数字前缀无佐证（共用正文 {shared}）"))
                    return
                kind = "数字药丸前缀"
            else:
                kind = "药丸字形前缀"
            union(a, b)
            edges[(a, b)] = kind
            stat["evidence"][(dirty, base)] = (kind, shared, p)
            return
        # ---- 打码昵称：可见部分逐字 100% 相同 + 等级标一致，才算同一个名字/同一个人 ----
        # 用户 2026-11-08 定。形近、大小写、共用正文对打码名**一律不算证据**：`小***`
        # 底下本来就挂着几十个人，模板化直播里任意两个号共用两条正文是常态。
        # 星号个数被 OCR 读成不同不算差异（`小***` / `小**` 的可见名都是 `小`）。
        if is_masked(a) and is_masked(b):
            if visible_name(a) != visible_name(b):
                return                            # 例：`小***` vs `健***`
            if not (la and lb and (la & lb)):
                stat["skipped"].append(
                    (a, b, "打码名可见部分相同，但等级标缺失/不一致"))
                return
            union(a, b)
            edges[(a, b)] = "打码名一致 + 等级标一致"
            stat["evidence"][(a, b)] = ("打码名一致 + 等级标一致", shared, "")
            return
        # pk == "weak"：无数字的标点前缀，不能单独定案 —— 落到下面按形近 + 共用正文复核
        same_ci = a.lower() == b.lower()
        if same_ci:
            union(a, b)
            edges[(a, b)] = "仅大小写不同"
            stat["evidence"][(a, b)] = ("仅大小写不同", shared, "")
            return
        if (_one_edit(a, b) or _similar(a, b) >= _SIM_MIN) and shared >= _SHARED_MIN:
            kind = "只差一个字符" if _one_edit(a, b) else f"相似度≥{_SIM_MIN:g}"
            union(a, b)
            edges[(a, b)] = kind
            stat["evidence"][(a, b)] = (kind, shared, "")
            return
        if _similar(a, b) >= _SIM_MIN:
            stat["skipped"].append(
                (a, b, f"相似但证据不足（共用正文 {shared}，只差一字 {_one_edit(a, b)}）"))

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            # 入围：相似 ≥0.7 或 前缀/后缀关系
            if (_similar(a, b) >= _SIM_MIN
                    or _prefix(a, b) is not None or _prefix(b, a) is not None):
                try_pair(a, b)

    clusters: dict[str, list[str]] = defaultdict(list)
    for n in names:
        clusters[find(n)].append(n)

    def base_score(u: str, members: list[str]) -> int:
        """u 是 cluster 里多少个成员「加了**数字药丸**前缀」后的 base。

        只认带数字的前缀：`-风-` 去掉 `-` 后是 `风-`，但那是名字的一部分，不是药丸，
        不能因此把 23 条评论的那版判成 1 条的短版。
        """
        n = 0
        for v in members:
            if v == u:
                continue
            p = _prefix(v, u)
            if p and _prefix_kind(p) in ("strong", "digit"):
                n += 1
        return n

    canon_of: dict[str, str] = {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        canon = max(members, key=lambda u: (base_score(u, members),
                                            rows_of.get(u, 0), len(u), u))
        stat["groups"] += 1
        for u in members:
            if u != canon:
                canon_of[u] = canon
                stat["renames"][u] = canon

    if not canon_of:
        return stat

    # 就地改写 + 同段同账号的条目合并（正文拼接、计数相加）。
    for i, users in raw.items():
        by_key: dict[tuple[str, str], dict] = {}
        order: list[tuple[str, str]] = []
        for u in users:
            n = _name(u)
            n2 = canon_of.get(n, n)
            u["username"] = n2
            # 数字药丸前缀被剥掉后，前缀本身就是这张行读到的等级标（与解析层
            # normalize_badge_alias 补 badge_text 同一条）。只在**按等级认人**且原记录
            # 没读到等级时补；否则 username 口径会被悄悄拆成多个账号。
            if promote_prefix_level and n2 != n and not _level(u) and is_masked(n2):
                p2 = _prefix(n, n2)
                if p2 and p2.isdigit():
                    u["level"] = p2
                    stat["level_from_prefix"] += 1
            k = (n2, _level(u))
            if k not in by_key:
                by_key[k] = u
                order.append(k)
                continue
            dst = by_key[k]
            dst["comments"] = list(dst.get("comments") or []) + list(u.get("comments") or [])
            for f in ("n_comments", "n_positive", "n_negative", "n_neutral"):
                dst[f] = int(dst.get(f) or 0) + int(u.get(f) or 0)
            if not _level(dst) and _level(u):
                dst["level"] = u["level"]
            stat["merged"] += 1
        raw[i] = [by_key[k] for k in order]

    for u, v in sorted(stat["renames"].items()):
        # 证据按 (脏名, 主写法) 存；只有前缀类能保证顺序，其余按字典序，两个方向都查
        ev = stat["evidence"].get((u, v)) or stat["evidence"].get((v, u)) or ()
        if ev:
            kind, shared, _p = ev
            stat["details"].append(
                f"[align] {u} -> {v}（{kind}；共用正文 {shared}；"
                f"评论 {rows_of.get(u, 0)} + {rows_of.get(v, 0)}；"
                f"段 {','.join(sorted(segs_of.get(u, set())))} + "
                f"{','.join(sorted(segs_of.get(v, set())))}）")
        else:
            stat["details"].append(
                f"[align] {u} -> {v}（链式：{rows_of.get(u, 0)} 条）")
    return stat
