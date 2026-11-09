"""livepipe.tools.remedy_badges.levels"""

from __future__ import annotations

from ... import account_id as aid
from ... import config as C
from pathlib import Path

from .common import _jsonl



# --------------------------------------------------------------------------- #
# ④ 对账：旧目录 → 新档
# --------------------------------------------------------------------------- #

def new_level_index(cfg: dict, segs: list[dict]) -> dict[tuple, set]:
    """``(段名, 时刻, 正文, 昵称) → {新等级}``：靠这些字段把旧图认回**新**评论。"""
    idx: dict[tuple, set] = {}
    for s in segs:
        panels = set(C.identity_panels(cfg, s))
        masked = set(C.masked_identity_panels(cfg, s))
        for c in _jsonl(Path(s["out"]) / "comments.jsonl"):
            k = (s["name"], round(float(c.get("t") or 0.0), 3),
                 c.get("content") or "", c.get("username") or "")
            idx.setdefault(k, set()).add(aid.level_of(c, panels, masked))
    return idx


def totals_of(cfg: dict, segs: list[dict]) -> dict[tuple[str, str], int]:
    """新口径下每个 ``(昵称, 等级标)`` 的正面合计（跨段相加，与 ``qualify`` 同一算法）。"""
    tot: dict[tuple[str, str], int] = {}
    for s in segs:
        for u in _jsonl(Path(s["out"]) / "users.jsonl"):
            if not u.get("username"):
                continue
            k = aid.account_key(u.get("username"), u.get("level"))
            tot[k] = tot.get(k, 0) + int(u.get("n_positive") or 0)
    return tot


def plan_of(old_tree: dict, idx: dict, min_n: int) -> dict:
    """逐图算出新归属：``{新键: [图…]}`` + 认不回去的图。"""
    moved: dict[tuple[str, str], list[dict]] = {}
    unknown: list[dict] = []
    for (name, level), rec in old_tree.items():
        for im in rec["images"]:
            key = (str(im.get("seg") or ""), round(float(im.get("t") or 0.0), 3),
                   im.get("content") or "", name)
            got = idx.get(key)
            if got is None or len(got) != 1:          # 认不回去（同一帧同正文的重名）⇒ 不猜
                unknown.append({**im, "dir": rec["dir"], "why": "新评论里对不上"})
                continue
            nk = (name, next(iter(got)))
            moved.setdefault(nk, []).append({**im, "dir": rec["dir"], "level": nk[1]})
    return {"moved": moved, "unknown": unknown}
