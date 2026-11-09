"""livepipe.parsing.ocr.row_ids — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from ..common.text import norm_text
from ..text.cleaning import _is_junk



def _row_key(r: dict) -> str:
    """行身份用的文本键：归一化后的整行（空正文行就退化成用户名）。

    **纯表情行**（正文是图片表情、OCR 一个字都没读出来，见 ``rows_from_video`` 的
    「空槽补扫表情」）没有文字，退化到 ``emoji_text`` —— 否则它既不能参与滚动对齐，
    还会和别的空行撞成同一个键（``_best_shift`` 不认空键，撞了只是白占位）。
    """
    return norm_text(r.get("text") or "") or norm_text(r.get("emoji_text") or "")


def _best_shift(prev_keys: list[str], cur_keys: list[str],
                min_run: int = 2, min_distinct: int = 2, max_shift: int = 8) -> int | None:
    """求这一帧相对上一帧的**行位移** k：本帧第 j 行 = 上帧第 j+k 行。

    评论列表是「新评论从底部进、整体上移」的，所以 k>0 是常态（k=0 表示这 8 秒没有
    新评论）。但 **k<0 也是真会发生的**：滚动区顶部的槽位时有时无（实测 t=0→t=8：
    其余 9 行位置不动，顶上多出一行「1221：好帅哦大哥哥」）——那是内容整体下移一格，
    等价于 k=-1。只允许非负位移时这一对帧直接对不齐，所有行都拿新 rid，唯一评论从
    19 涨到 27（全是重复）。所以两个方向都试。

    判据是「连续相同文本」的最长段：要求至少 ``min_run`` 行、且段里至少
    ``min_distinct`` 个**不同**文本 —— 一屏好几个一模一样的「赞了直播」不能算证据。
    平手时取 |k| 最小的（位移越小越可信）。对不齐就返回 None（宁可不合并，
    也不把两条评论并成一条）。
    """
    best = None                     # ((run, -|k|, distinct), k)
    lo = max(-max_shift, -len(prev_keys))
    for k in range(lo, len(prev_keys) + 1):
        run = 0
        for j in range(len(cur_keys) + 1):
            i = j + k
            hit = (j < len(cur_keys) and cur_keys[j] and 0 <= i < len(prev_keys)
                   and prev_keys[i] == cur_keys[j])
            if hit:
                run += 1
                distinct = len(set(cur_keys[j - run + 1: j + 1]))
                cand = (run, -abs(k), distinct)
                if best is None or cand > best[0]:
                    best = (cand, k)
            else:
                run = 0
    if best and best[0][0] >= min_run and best[0][2] >= min_distinct:
        return best[1]
    return None


def _emoji_sig(r: dict) -> frozenset | None:
    """纯表情行的**表情集**（正文为空时它才是这行的身份）；有正文 / 没认出表情 ⇒ None。

    「正文为空」要连**碎片行**一起算（``***`` / ``***1107 101`` 这种打码昵称残渣，见
    ``_is_junk``）：纯表情评论在行这一层的 text 从来不是空串，剩下的就是这个残渣 ——
    只判空串的话这道闸永远不响（实测第一版就是这样，一行没分开）。

    别再往前多做一步（把 ``S***`` 里的字母也剥掉再判）：实测全量 7 段里这么改**只**
    多出 seg4 t=27.6 那条 ``***1107 101`` —— 它和 t=6.87 的 ``[打call]×4`` 是**同一条**
    （滚动比对上移一格），多认一次就是重复计数，一条也没多救回来。这道闸只认
    「``_is_junk`` 都算不出内容」的行。
    """
    txt = r.get("text") or ""
    if txt.strip() and not _is_junk(txt):
        return None
    toks = {e.get("token") for e in (r.get("emojis") or []) if e.get("token")}
    return frozenset(toks) or None


def assign_row_ids(rows: list[dict], min_run: int = 2, min_distinct: int = 2) -> list[dict]:
    """给每行一个跨帧身份 ``rid``：靠**滚动对齐**把同一条评论在多帧里认成同一条。

    没有这一步，「唯一评论」根本算不准：本素材 7 成以上的评论是**纯表情**（正文被 OCR
    丢干净），文本去重会把同一个用户的不同表情评论撞成一条，不去重又每条评论按帧数
    翻倍。行位移是唯一可靠的跨帧线索，所以在这里把身份定下来，``dedup_comments``
    只做「同一个 rid 留最早一次」。

    **纯表情行的表情集是一票否决**：对齐算出来的是一个**整帧统一的位移** ``k``，逐行按
    ``prev[j+k]`` 认亲 —— 一屏好几行都是「打码昵称残渣 + 表情」时，几行长得一模一样，
    位移差一格就把**别人的评论**认成自己的。实测 seg4（一屏三个 ``S***``，同一秒里
    分别发 `[比心]×3` / `[爱心]×4` / `[打call]×2`）rid=14 一口气串了三条不同评论，
    ``dedup_comments`` 同一个 rid 只留最早一次 ⇒ 另外两条**整条丢掉**（用户报的
    「比心没识别出来」在交付里就是丢在这一步，`rows.jsonl` 里那行一直在）。
    一条评论的表情不会变 ⇒ 两行都是纯表情、且表情集完全不相交时**不认亲**，另起新 rid。

    就地写入 ``r["rid"]``，返回 rows。
    """
    panels: dict[str, list[dict]] = {}
    for r in rows:
        panels.setdefault(str(r.get("panel")), []).append(r)
    nid = 0
    for rs in panels.values():
        groups: dict[float | None, list[dict]] = {}
        for r in rs:
            groups.setdefault(r.get("t"), []).append(r)
        prev: list[tuple[str, int]] = []
        prev_rows: list[dict] = []
        for t in sorted(groups, key=lambda x: (x is None, x)):
            cur = sorted(groups[t], key=lambda r: (r.get("socket") is None, r.get("socket")))
            keys = [_row_key(r) for r in cur]
            k = _best_shift([p[0] for p in prev], keys, min_run, min_distinct) if prev else None
            claimed: set[int] = set()                  # 上一帧里已经被认领的行
            for j, r in enumerate(cur):
                i = j + k if k is not None else None
                b = _emoji_sig(r)
                if i is not None and 0 <= i < len(prev) and prev_rows:
                    a = _emoji_sig(prev_rows[i])
                    if a and b and not (a & b):
                        # 位移是**整帧统一**的，快速滚动时会差一两格 ⇒ 这一步配到的是
                        # 隔壁那条评论。先看邻行里有没有表情对得上的（±1 格）：有就认它，
                        # 没有才另起一条。邻行匹配只在纯表情行之间做，且不抢已被认领的行。
                        alt = next((q for q in (i - 1, i + 1)
                                    if 0 <= q < len(prev_rows) and q not in claimed
                                    and (s := _emoji_sig(prev_rows[q])) and (s & b)), None)
                        if alt is None:
                            r["rid"] = nid             # 表情完全不相交 ⇒ 不是同一条
                            nid += 1
                        else:
                            r["rid"] = prev[alt][1]
                            claimed.add(alt)
                    else:
                        r["rid"] = prev[i][1]          # 与上一帧的某行是同一条
                        claimed.add(i)
                else:
                    r["rid"] = nid                 # 新出现
                    nid += 1
            prev = [(keys[j], cur[j]["rid"]) for j in range(len(cur))]
            prev_rows = cur
    return rows
