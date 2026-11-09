"""livepipe.tools.merge_segments.qualify"""

from __future__ import annotations

from pathlib import Path
import json

from .align import consolidate_users
from .common import _label
from .common import _ukey
from .common import _uname
from .common import log
from .common import read_jsonl
from .common import write_jsonl



def _uses_level_identity(s: dict) -> bool:
    """这段的用户表是不是按 (昵称, 等级标) 认人的（filter 把口径写在 users.json 里）。"""
    p = Path(s["out"]) / "users.json"
    if not p.exists():
        return False
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(d.get("identity_panels"))


# --------------------------------------------------------------------------- #
# ① qualify：跨段判合格 + 回写每段的 users_qualified.jsonl
# --------------------------------------------------------------------------- #

def qualify(cfg: dict, segs: list[dict], *, dry_run: bool = False) -> int:
    q = cfg["qualify"]
    n = int(q["min_positive"])

    # 账号身份是 ``(昵称, 等级标)``（见 account_id）：抖音 App 的昵称打码成 `X***`，
    # 同一个显示名底下是几十个人，只有粉丝团药丸 `◆N` 还能把人分开。老 users.jsonl
    # 没有 ``level`` 字段 ⇒ 键退化成 ``(昵称, "")``，与改动前**完全一致**。
    per_seg: dict[int, dict[tuple[str, str], dict]] = {}
    totals: dict[tuple[str, str], dict] = {}
    missing = []
    raw = {}
    for s in segs:
        p = Path(s["out"]) / "users.jsonl"
        if not p.exists():
            missing.append(str(p))
            continue
        raw[s["i"]] = [u for u in read_jsonl(p) if u.get("username")]
    # 跨段账号归一：同一场直播的三段是各自解析的，同一个人可能在 A 段读到干净名、
    # 在 B 段读到「药丸并进昵称」/末字被切的名 —— 只在段内取证就永远并不到一起。
    # 判据见 align.py：**名字相似度 ≥0.7 只是入围**，还要过证据复核（共用正文条数 /
    # 药丸前缀形状 / 等级标 / 只差一个字符），复核不通过的原样保留并在日志里留证。
    if raw:
        st = consolidate_users(raw, promote_prefix_level=any(
            _uses_level_identity(s) for s in segs))
        if st["level_from_prefix"]:
            log(f"[qualify][align] 数字药丸前缀补回等级标 {st['level_from_prefix']} 条"
                f"（`15木***` 的 `15` 就是这张行的等级）")
        if st["groups"]:
            log(f"[qualify][align] 跨段账号归一：{st['groups']} 组、"
                f"合并 {st['merged']} 条重复记录（名字相似度 ≥0.7 入围 + 证据复核；"
                f"打码名只认「可见部分逐字相同 + 等级标一致」）")
            for d in st["details"]:
                log("          " + d)
        for (a, b, why) in st["skipped"][:8]:
            log(f"          [align] ⚠ 不并：{a} / {b}（{why}）")
    for s in segs:
        if s["i"] in raw:
            per_seg[s["i"]] = {_ukey(u): u for u in raw[s["i"]]}
        for u in per_seg[s["i"]].values():
            acc = totals.setdefault(_ukey(u), {
                "username": u["username"], "level": str(u.get("level") or ""),
                "n_positive": 0, "n_comments": 0, "segs": {}})
            acc["n_positive"] += int(u.get("n_positive") or 0)
            acc["n_comments"] += int(u.get("n_comments") or 0)
            acc["segs"][_label(s)] = int(u.get("n_positive") or 0)
    if missing and totals:
        # 缺一段就**不能**判：合计少算那一段的正面条数，正好卡在阈值上的账号会被静默判掉
        # —— 而「不静默掉账号」正是这一步存在的理由，所以这里宁可停。
        log(f"[qualify][error] {len(missing)} 段的 users.jsonl 还不存在，合计会少算它们的评论：")
        for m in missing:
            log(f"          {m}")
        log("          先把这些段的 filter 跑完（--only filter），再判合格")
        return 1
    if not totals:
        log("[qualify] 一段的用户表都没有 —— 先把 filter 步跑完")
        return 1

    qualified = {k: v for k, v in totals.items() if v["n_positive"] >= n}
    log(f"[qualify] 跨段合计 {len(totals)} 个有正面评论的账号 → ≥{n} 条正面 **{len(qualified)}** 个")
    only_here = sorted((v for v in qualified.values() if max(v["segs"].values()) < n),
                       key=lambda r: -r["n_positive"])
    log(f"[qualify] 其中**只在合计下合格**（任何单段都不够 {n} 条）的 {len(only_here)} 个：")
    if not only_here:
        log("          （无）")
    for v in only_here:
        log(f"          {_uname(v)}  合计 {v['n_positive']}  " +
            " ".join(f"{s}={c}" for s, c in sorted(v["segs"].items())))
    near = sorted((v for v in totals.values() if v["n_positive"] == n - 1),
                  key=lambda r: (r["username"], r["level"]))
    if near:
        log(f"[qualify] 差一步就够的（合计 {n - 1} 条）：" +
            "、".join(f"{_uname(v)}({v['n_positive']})" for v in near))

    if dry_run:
        return 0
    # 回写：每段只留合格账号（合格判定已在此完成 ⇒ export 用 stage_min 的阈值照出即可）
    for s in segs:
        keep = [u for k, u in per_seg.get(s["i"], {}).items() if k in qualified]
        keep.sort(key=lambda u: (-int(u.get("n_positive") or 0), u["username"],
                                 str(u.get("level") or "")))
        dst = Path(s["out"]) / "users_qualified.jsonl"
        write_jsonl(dst, keep)
        log(f"[qualify] {_label(s)}: {len(per_seg.get(s['i'], {}))} → {len(keep)} 个账号"
            f"  → {dst.name}")

    merged = Path(cfg["output"]["dir"]) / "merged_users.json"
    merged.parent.mkdir(parents=True, exist_ok=True)
    merged.write_text(json.dumps(
        {"kind": "merged_users", "min_positive": n, "stage_min": q["stage_min"],
         "n_qualified": len(qualified), "segments": [_label(s) for s in segs],
         "users": sorted(qualified.values(),
                         key=lambda r: (-r["n_positive"], r["username"], r["level"]))},
        ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[qualify] 合计口径 → {merged}")
    return 0
