#!/usr/bin/env python3
"""plan 差异面：C 档「只许改配对字段」的验收工具（`docs/评测方案.html` §3.2）。

C 有意改「用哪张底图」，所以产物 md5 不能要求相同；能要求的是：

  · `expand_plan.json` 的差异**只落在配对字段**（`base` 及其派生），量测字段（`base_slot` /
    `base_gh` / `w_b` / `delta_w` …）对**同一张底图**必须一列不差；
  · 每个成品图的 md5 变化都能追到「这张底图换了」—— 配对没变的 job，产物 md5 必须相同。

用法::

    python3 eval/plandiff.py --plan-a /tmp/evalC/A/expand_plan.json \\
        --plan-b /tmp/evalC/B/expand_plan.json \\
        --out-a /tmp/evalC/outA --out-b /tmp/evalC/outB --json /tmp/plandiff.json

退出码：0 = 只改配对且产物可逐张归因；3 = 有越界差异（量测字段变了 / 产物变化归不了因）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# 配对字段：C 允许它们变（`base` 换了，派生量自然跟着换）
PAIR_FIELDS = {
    "base", "base_user", "base_roi", "base_slot", "base_badge_w", "base_badge_rect",
    "base_name_ratio", "base_t", "base_gh", "w_b", "delta_w", "tier", "t_level", "b_level",
}
# plan 顶层的「运行账」，不参与差异面判定（缓存/耗时随环境变）
TOP_IGNORE = {"meta"}
# 两臂的语料根不同 ⇒ 路径前缀必然不同；`fingerprint` 含逐个 job 的配对，C 改了配对就该变。
TOP_EXPECTED = {"source", "fingerprint"}
# users[] 里允许变的字段：`pool` / `short_reason` / `level_excluded` 由配对规则决定
USER_KEEP = {"dir"}


def _md5(p: Path) -> str:
    h = hashlib.md5()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _jobs_by_key(plan: dict) -> dict:
    out: dict = defaultdict(list)
    for j in plan.get("jobs") or []:
        out[(j.get("dir"), j.get("name"))].append(j)
    for v in out.values():
        v.sort(key=lambda j: (str(j.get("base")), str(j.get("self_image"))))
    return out


def _users_by_dir(plan: dict) -> dict:
    return {u.get("dir"): u for u in plan.get("users") or []}


def _diff_fields(a: dict, b: dict) -> dict:
    keys = set(a) | set(b)
    return {k: [a.get(k), b.get(k)] for k in sorted(keys) if a.get(k) != b.get(k)}


def _norm(plan: dict) -> dict:
    """把两臂语料根前缀抹掉（`/tmp/evalC/A/…` ↔ `/tmp/evalC/B/…`），其余原样。"""
    root = str(plan.get("source") or "").rstrip("/")
    if not root:
        return plan
    prefix = root + "/"

    def strip(o):
        if isinstance(o, str):
            return o[len(prefix):] if o.startswith(prefix) else o
        if isinstance(o, list):
            return [strip(x) for x in o]
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items()}
        return o

    out = dict(plan)
    for k in ("jobs", "users"):
        out[k] = strip(plan.get(k) or [])
    return out


def compare(plan_a: dict, plan_b: dict, out_a: Path | None, out_b: Path | None) -> dict:
    report: dict = {"schema_version": 1, "kind": "plan_diff"}
    plan_a, plan_b = _norm(plan_a), _norm(plan_b)

    # ① 顶层（`meta` 是运行账：耗时/缓存计数随环境变，不参与差异面判定）
    top_diff, top_expected = {}, {}
    for k in sorted(set(plan_a) | set(plan_b)):
        if k in TOP_IGNORE or k in ("users", "jobs", "short"):
            continue
        if plan_a.get(k) != plan_b.get(k):
            (top_expected if k in TOP_EXPECTED else top_diff)[k] = "两臂语料根/指纹不同（预期）" \
                if k in TOP_EXPECTED else [plan_a.get(k), plan_b.get(k)]
    report["top_diff"] = top_diff
    report["top_expected"] = top_expected

    # ② users[]
    ua, ub = _users_by_dir(plan_a), _users_by_dir(plan_b)
    user_field = Counter()
    user_samples: dict = defaultdict(list)
    for d in sorted(set(ua) | set(ub)):
        fa, fb = ua.get(d) or {}, ub.get(d) or {}
        for k, v in _diff_fields(fa, fb).items():
            user_field[k] += 1
            if len(user_samples[k]) < 5:
                user_samples[k].append({"dir": d, "a": v[0], "b": v[1]})
    report["users"] = {"dirs_a": len(ua), "dirs_b": len(ub),
                       "changed_fields": dict(user_field), "samples": user_samples}

    # ③ jobs[]：同一 (dir, name) 组内按 base 配对
    ja, jb = _jobs_by_key(plan_a), _jobs_by_key(plan_b)
    same_pair_field_diff = []
    pair_meta_diff = []
    dropped, added, swapped = [], [], []
    for key in sorted(set(ja) | set(jb)):
        la, lb = ja.get(key) or [], jb.get(key) or []
        map_a = {j.get("base"): j for j in la}
        map_b = {j.get("base"): j for j in lb}
        for base in sorted(set(map_a) | set(map_b)):
            if base in map_a and base in map_b:
                df = _diff_fields(map_a[base], map_b[base])
                # 配对元数据（`tier` / `t_level` / `b_level`）本来就会新增 —— 它们是
                # `PAIR_FIELDS` 的一部分；这里只揪**量测字段**是否被 C 改动。
                meta_df = {k: v for k, v in df.items() if k in PAIR_FIELDS}
                quant_df = {k: v for k, v in df.items() if k not in PAIR_FIELDS}
                if meta_df:
                    pair_meta_diff.append({"dir": key[0], "name": key[1],
                                           "base": base, "fields": meta_df})
                if quant_df:
                    same_pair_field_diff.append({"dir": key[0], "name": key[1],
                                                 "base": base, "fields": quant_df})
            elif base in map_a:
                dropped.append({"dir": key[0], "name": key[1], "base": base})
            else:
                added.append({"dir": key[0], "name": key[1], "base": base})
        if la and lb and set(map_a) != set(map_b):
            swapped.append({"dir": key[0], "name": key[1],
                            "a": sorted(map_a), "b": sorted(map_b)})
    report["jobs"] = {
        "count_a": len(plan_a.get("jobs") or []), "count_b": len(plan_b.get("jobs") or []),
        "same_pair_field_diff": same_pair_field_diff[:20],
        "same_pair_field_diff_count": len(same_pair_field_diff),
        "same_pair_meta_diff_count": len(pair_meta_diff),
        "groups_same_base_set": sum(1 for key in set(ja) | set(jb)
                                    if {j.get("base") for j in ja.get(key) or []}
                                    == {j.get("base") for j in jb.get(key) or []}),
        "groups_changed_base_set": len(swapped),
        "dropped_count": len(dropped), "added_count": len(added),
        "swapped_groups": len(swapped),
        "dropped": dropped[:20], "added": added[:20],
    }

    # ④ 产物：配对没变的 job 必须 md5 相同；变了的必须能归因到 base 变化
    if out_a and out_b:
        oa = {p.relative_to(out_a).as_posix(): _md5(p)
              for p in sorted(Path(out_a).rglob("扩展*.jpg"))}
        ob = {p.relative_to(out_b).as_posix(): _md5(p)
              for p in sorted(Path(out_b).rglob("扩展*.jpg"))}
        changed = sorted(rel for rel in set(oa) & set(ob) if oa[rel] != ob[rel])
        # (dir, name) → 是否有底图变化；产物路径的第一段是目录名
        swap_dirs = {(s["dir"], s["name"]) for s in swapped}
        unexplained, explained = [], []
        for rel in changed:
            d = rel.split("/", 1)[0]
            hit = any(dd == d for dd, _n in swap_dirs)
            (explained if hit else unexplained).append(rel)
        report["outputs"] = {
            "a": len(oa), "b": len(ob),
            "only_a": sorted(set(oa) - set(ob))[:20], "only_b": sorted(set(ob) - set(oa))[:20],
            "only_a_count": len(set(oa) - set(ob)), "only_b_count": len(set(ob) - set(oa)),
            "md5_changed": len(changed), "md5_changed_explained": len(explained),
            "md5_changed_unexplained": unexplained[:20],
            "md5_changed_unexplained_count": len(unexplained),
        }

    # ⑤ 判定
    bad = []
    if report["top_diff"]:
        bad.append(f"顶层字段变了：{sorted(report['top_diff'])}")
    unexpected_users = {k: v for k, v in user_field.items()
                        if k not in ("pool", "short_reason", "level_excluded", "t_level",
                                     "count", "self_image", "badge_rect", "badge_votes",
                                     "text_color", "text_color_n", "text_color_dev",
                                     "no_sprite", "name_note", "name_width", "slot_width",
                                     "badge_w", "have", "target")}
    if unexpected_users:
        bad.append(f"users[] 出现非配对字段差异：{unexpected_users}")
    if same_pair_field_diff:
        bad.append(f"同一配对出现字段差异 {len(same_pair_field_diff)} 处")
    if report.get("outputs", {}).get("md5_changed_unexplained_count"):
        bad.append(f"产物变化归不了因 {report['outputs']['md5_changed_unexplained_count']} 张")
    report["ok"] = not bad
    report["problems"] = bad
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="plan 差异面验收（C：只许改配对字段）")
    ap.add_argument("--plan-a", required=True)
    ap.add_argument("--plan-b", required=True)
    ap.add_argument("--out-a")
    ap.add_argument("--out-b")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    plan_a = json.loads(Path(a.plan_a).read_text(encoding="utf-8"))
    plan_b = json.loads(Path(a.plan_b).read_text(encoding="utf-8"))
    rep = compare(plan_a, plan_b,
                  Path(a.out_a) if a.out_a else None, Path(a.out_b) if a.out_b else None)
    text = json.dumps(rep, ensure_ascii=False, indent=1)
    if a.json:
        Path(a.json).write_text(text, encoding="utf-8")
        print(f"→ {a.json}")
    print(text if not a.json else json.dumps(
        {k: rep[k] for k in ("ok", "problems")}, ensure_ascii=False))
    return 0 if rep["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
