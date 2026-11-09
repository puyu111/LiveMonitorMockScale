"""livepipe.tools.remedy_badges.report"""

from __future__ import annotations

from pathlib import Path

from ... import account_id as aid

from .common import _log
from .levels import plan_of



def report(cfg: dict, root: Path, old_tree: dict, idx: dict,
           tot: dict, min_n: int, show: list[str]) -> dict:
    """印单子。返回可写成 ``remedy_plan.json`` 的结构（核账也吃它）。"""
    plan = plan_of(old_tree, idx, min_n)
    moved, unknown = plan["moved"], plan["unknown"]
    # 逐图的新档（按**身份**认，不按字节）：``--show`` 与落盘后的核账都查这一份
    key_of_iden = {im["iden"]: f"{k[0]}\x1f{k[1]}" for k, v in moved.items() for im in v}

    # 旧树按昵称归堆：同一个打码昵称底下本来就可能有好几个档
    by_name: dict[str, dict[str, list]] = {}
    for (name, level), rec in old_tree.items():
        by_name.setdefault(name, {})[level] = [rec["dir"], rec["count"]]
    qualified = {k for k, n in tot.items() if n >= min_n}
    dropped = {k: n for k, n in tot.items() if 0 < n < min_n}

    _log(f"[补救] 交付树 {root}")
    _log(f"[补救] 现状 {len(old_tree)} 档 / {sum(r['count'] for r in old_tree.values())} 张"
         f"（其中带等级标的 {sum(1 for _n, lv in old_tree if lv)} 档）")
    _log(f"[补救] 新口径 合格 {len(qualified)} 档"
         f"（带等级标 {sum(1 for _n, lv in qualified if lv)} 档）·"
         f" 掉到 {min_n} 张以下的 {len(dropped)} 档"
         f" / {sum(dropped.values())} 张")

    # ---- 拆分明细：只印「新旧档对不上」的昵称（没动的一个字不印，免得刷屏）----
    changed = []
    for name, olds in sorted(by_name.items()):
        news = {lv: [im for im in moved.get((name, lv), [])]
                for lv in {k[1] for k in qualified if k[0] == name}}
        news = {lv: v for lv, v in news.items() if v}
        if set(olds) == set(news):
            continue
        changed.append((name, olds, news))
    _log(f"[补救] 档位有变的昵称 {len(changed)} 个 / 旧树里 {len(by_name)} 个：")
    for name, olds, news in changed[:40]:
        o = "、".join(f"{lv or '(无标)'}×{c}" for lv, (_d, c) in sorted(olds.items()))
        n = "、".join(f"{lv or '(无标)'}×{len(v)}" for lv, v in sorted(news.items())) or "—"
        _log(f"          {name:<12} 旧 {o:<30} → 新 {n}")
    if len(changed) > 40:
        _log(f"          ……还有 {len(changed) - 40} 个（全量见 remedy_plan.json）")

    # ---- 逐图归属：用户点名的那几个目录，一张一张印 ----
    for d in show:
        rec = next((r for r in old_tree.values() if r["dir"] == d), None)
        if rec is None:
            _log(f"[补救] --show {d}：交付树里没有这个目录")
            continue
        far = {u["iden"] for u in unknown}
        _log(f"[补救] {d}/ 逐图：")
        for i, im in enumerate(rec["images"], 1):
            k = key_of_iden.get(im["iden"], "")
            lv = k.split("\x1f", 1)[1] if k else "?"
            far_s = "  ⚠ 认不回去" if im["iden"] in far else ""
            _log(f"          {i}.jpg  seg={im.get('seg')}  t={float(im.get('t') or 0):.2f}"
                 f"  正文「{im.get('content') or im.get('emoji_text') or ''}」"
                 f"  → 等级 {lv or '(读不到)'}{far_s}")

    # ---- 掉档：本脚本唯一可能丢图的地方 ----
    if dropped:
        _log(f"[补救] ⚠ 拆开后不足 {min_n} 张、**不再交付**的 {len(dropped)} 档：")
        for (name, lv), n in sorted(dropped.items(), key=lambda t: -t[1]):
            ims = moved.get((name, lv), [])
            where = f"（旧目录 {sorted({i['dir'] for i in ims})}）" if ims else ""
            _log(f"          {aid.display(name, lv):<14} {n} 张{where}")
        _log(f"          合计 {sum(dropped.values())} 张。要留住就调 qualify.min_positive"
             f"（当前 {min_n}）—— **这是取舍，脚本不替你做**")
    if unknown:
        _log(f"[补救] ⚠ {len(unknown)} 张在**新**评论里认不回去（同帧同正文重名）"
             f"—— 不猜，落盘后按身份复核")

    # ---- 新增交付（原来不够 N 张、拆开后够了）----
    fresh = sorted(k for k in qualified if k not in old_tree and k[0] in by_name)
    if fresh:
        _log(f"[补救] 新增交付 {len(fresh)} 档（原先并在一起不够数、拆开后够数）：")
        for k in fresh[:10]:
            _log(f"          {aid.display(*k):<14} {tot[k]} 张")

    def _k(key: tuple[str, str]) -> str:
        return f"{key[0]}\x1f{key[1]}"

    return {"root": str(root), "min_positive": min_n,
            "old": {_k(k): {"dir": r["dir"], "count": r["count"]}
                    for k, r in old_tree.items()},
            "new": {_k(k): {"n_positive": tot[k]} for k in sorted(qualified)},
            "levels": key_of_iden,
            "moved": {_k(k): [{"from": i["dir"], "seg": i.get("seg"), "t": i.get("t"),
                               "content": i.get("content"), "iden": i["iden"]}
                              for i in v] for k, v in moved.items()},
            "dropped": {_k(k): tot[k] for k in dropped},
            "unknown": [{k: v for k, v in u.items() if k != "path"} for u in unknown]}
