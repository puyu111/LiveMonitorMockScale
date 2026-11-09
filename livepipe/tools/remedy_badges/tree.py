"""livepipe.tools.remedy_badges.tree"""

from __future__ import annotations

from ... import account_id as aid
from ... import config as C
from pathlib import Path
import json
import os

from .common import _iden
from .common import _jsonl
from .common import _log
from .common import _stage_name



def delivery_root(cfg: dict, segs: list[dict]) -> Path:
    """交付树的根 —— 与 ``merge_segments.collect`` 同一套算法（两处不一致就会看错账）。"""
    c = cfg["collect"]
    if c["out"]:
        return Path(c["out"])
    n = int(cfg["qualify"]["min_positive"])
    # ⚠️ 两个名字**不一样**：各段的导出目录用 `stage_min`（本配置 = `1张以上`），交付树用
    # `min_positive`（= `3张以上`）。探平台要拿**段目录**那个名字去找。
    name = _stage_name(cfg)
    plat = c["platform_dir"]
    if not plat:
        seen = [d.parent.parent.name for s in segs
                for d in Path(s["out"]).glob(f"*/{name}/manifest.json")]
        plat = seen[0] if seen else "未标平台"
    return Path(cfg["output"]["dir"]) / plat / f"all_users_正面_{n}张以上"


def read_tree(root: Path) -> tuple[dict, dict]:
    """交付树 → ``{(昵称, 等级标): 档}``。读的是 ``manifest.json``；每张图带 `iden`（身份）。"""
    doc = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    tree: dict[tuple[str, str], dict] = {}
    for u in doc.get("users") or []:
        k = aid.account_key(u.get("username"), u.get("level"))
        imgs = []
        for im in u.get("images") or []:
            p = root / im["rel"]
            imgs.append({**im, "iden": _iden(im), "missing": not p.exists()})
        tree[k] = {"dir": u["dir"], "count": int(u.get("count") or 0), "images": imgs}
    return doc, tree


# --------------------------------------------------------------------------- #
# ③ 预检
# --------------------------------------------------------------------------- #

def check_rows(segs: list[dict]) -> int:
    """``rows.jsonl`` 不在 ⇒ 这一步会去开视频 OCR（几小时）。宁可当场拦下。"""
    miss = [str(Path(s["out"]) / "rows.jsonl") for s in segs
            if not (Path(s["out"]) / "rows.jsonl").exists()]
    if miss:
        _log(f"[补救][error] {len(miss)} 段没有 rows.jsonl —— 补救会退化成重新 OCR：")
        for m in miss:
            _log(f"          {m}")
        _log("          先把这些段跑出来（--only parse），再回来补救")
        return 1
    return 0


def check_cache(cfg: dict, segs: list[dict]) -> int:
    """判定缓存够不够 filter 走完。够 ⇒ 这一步零模型调用（连 key 都不用）。

    **按 filter 自己的候选口径算**，不是按全部评论：``user_filter.run`` 只判「有昵称、没被
    排除词命中、且评论数 ≥ min_positive」的账号的评论。差在这里就是假警报 —— 本素材
    comments.jsonl 里 8588 条有 2763 条是**匿名行**（续行/浮层文字），filter 从不判它们，
    它们当然不在缓存里。
    """
    from ... import user_filter as UF
    a = C.build_filter_args(cfg, segs[0])
    cache_p = Path(a.cache)
    have = {json.loads(l)["k"] for l in cache_p.read_text(encoding="utf-8").splitlines()
            if l.strip()} if cache_p.exists() else set()
    kw = [k.strip() for k in str(a.exclude).split(",") if k.strip()]
    panels = UF.split_panels_of(a.identity_panels)
    masked = UF.split_panels_of(getattr(a, "identity_masked", ""))
    cand: list[dict] = []
    n_all = 0
    for s in segs:
        cs = _jsonl(Path(s["out"]) / "comments.jsonl")
        n_all += len(cs)
        by_user, _anon, _ex = UF.group_accounts(cs, kw, panels, masked)
        cand += [c for v in by_user.values() if len(v) >= int(a.min_positive) for c in v]
    keys = {UF._key(c.get("content") or "", c.get("emoji_text") or "") for c in cand}
    miss = keys - have
    _log(f"[补救] 判定缓存 {len(have)} 条；评论 {n_all} 条 → 要吃判定的 {len(cand)} 条"
         f"（{len(keys)} 种）⇒ 未命中 {len(miss)} 种（{cache_p.name}）")
    if miss:
        _log(f"[补救] ⚠ {len(keys)} 种评论里有 {len(miss)} 种没判过 ⇒ filter 会**真的调模型**。"
             f"key 只从环境变量读：{cfg['filter']['nlp']['api_key_env']}")
        return 1 if not os.environ.get(cfg["filter"]["nlp"]["api_key_env"]) else 0
    return 0
