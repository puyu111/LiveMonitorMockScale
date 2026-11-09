"""名单 × 交付账号的核心匹配（纯函数，不读盘）。

口径：**平台内各撞各的** —— 抖音名单只撞抖音的账号，绝不跨平台「凑」。
每个名单名对平台内的每个账号算一个匹配度，**≥ 阈值**的算命中；一个名单名可以命中
多个账号（例如同一昵称的 ``◆16`` / ``◆21`` 两档，或 OCR 多读出一个等级数字），
报告全部列出、按分数降序。没命中的名单名也逐条留下「最近账号 + 分数」，方便人工复核
是不是阈值卡掉了、还是确实没拍到。
"""

from __future__ import annotations

from .common import (
    display_account,
    normalize_name,
    platform_sort_key,
    score_norm,
)


def _account_key(account: dict) -> str:
    """账号在报告里的去重键：目录名优先（同名不同等级的档也不同目录）。"""
    return account.get("dir") or display_account(
        account["username"], account.get("level"))


def match_platform(
    label: str,
    entries: list[dict],
    accounts: list[dict],
    tree=None,
    threshold: float = 0.8,
    metric: str = "partial",
    max_matches: int = 5,
    platform_dir=None,
) -> dict:
    """一个平台内：名单条目 × 账号 → 平台报告（不写盘）。

    ``accounts`` 可以是交付账号，也可以是「交付 ∪ 直播全量」——后者让**只说过一条
    评论、没进交付树**的人也能被撞出来（``in_delivery=False``）。
    ``max_matches <= 0`` 表示不封顶；默认 5，防止一个两字名单名在大账号集里撞出一长串。
    """
    norm_accounts = [(normalize_name(a["username"]), a) for a in accounts]
    matches: list[dict] = []
    unmatched: list[dict] = []
    used: set[str] = set()
    used_live: set[str] = set()

    for e in entries:
        ne = normalize_name(e["name"])
        scored: list[tuple[float, dict]] = []
        for na, acc in norm_accounts:
            s = score_norm(ne, na, metric)
            if s > 0:
                scored.append((s, acc))
        scored.sort(key=lambda x: (-x[0], x[1]["username"], x[1].get("level") or ""))

        hits = [(s, a) for s, a in scored if s >= threshold]
        if max_matches and len(hits) > max_matches:
            hits = hits[:max_matches]
        if hits:
            for s, acc in hits:
                d = acc.get("dir") or ""
                key = _account_key(acc)
                used.add(key)
                in_delivery = bool(acc.get("in_delivery"))
                if not in_delivery:
                    used_live.add(key)
                matches.append({
                    "list_name": e["name"],
                    "list_row": e.get("row"),
                    "list_seq": e.get("seq"),
                    "account": acc["username"],
                    "display": display_account(acc["username"], acc.get("level")),
                    "level": acc.get("level") or "",
                    "score": round(float(s), 4),
                    "account_dir": d,
                    "in_delivery": in_delivery,
                    "n_comments": acc.get("n_comments"),
                    "n_positive": acc.get("n_positive"),
                    "count": acc.get("count"),
                    "segs": acc.get("segs") or {},
                    "first_image": acc.get("first_image") or "",
                    "dir_exists": bool(acc.get("path") is not None and acc["path"].is_dir()),
                })
        else:
            best = None
            if scored:
                top = scored[0]
                best = {
                    "account": top[1]["username"],
                    "display": display_account(
                        top[1]["username"], top[1].get("level")),
                    "account_dir": top[1].get("dir") or "",
                    "score": round(float(top[0]), 4),
                }
            unmatched.append({
                "list_name": e["name"],
                "list_row": e.get("row"),
                "list_seq": e.get("seq"),
                "best": best,
            })

    matched_names = len({m["list_name"] for m in matches})
    return {
        "platform": label,
        "tree": str(tree) if tree is not None else None,
        "platform_dir": str(platform_dir) if platform_dir is not None else None,
        "list_count": len(entries),
        "matched_count": matched_names,
        "unmatched_count": len(unmatched),
        "accounts_count": len(accounts),
        "delivered_count": sum(1 for a in accounts if a.get("in_delivery")),
        "matched_accounts": len(used),
        "matched_not_delivered": len(used_live),
        "accounts_not_matched": len({_account_key(a) for a in accounts} - used),
        "matches": matches,
        "unmatched": unmatched,
    }


def build_report(
    inputs: dict,
    platform_reports: list[dict],
    skipped: list[dict] | None = None,
) -> dict:
    """平台报告 → 一份完整的 ``matches.json`` 结构。"""
    reports = sorted(platform_reports, key=lambda r: platform_sort_key(r["platform"]))
    return {
        "kind": "list_match",
        "schema_version": 1,
        "generated_at": inputs.get("generated_at"),
        "inputs": {k: v for k, v in inputs.items() if k != "generated_at"},
        "summary": {
            "platforms": len(reports),
            "list_names": sum(r["list_count"] for r in reports),
            "matched_names": sum(r["matched_count"] for r in reports),
            "unmatched_names": sum(r["unmatched_count"] for r in reports),
            "matches": sum(len(r["matches"]) for r in reports),
            "matched_accounts": sum(r["matched_accounts"] for r in reports),
            "matched_not_delivered": sum(r.get("matched_not_delivered", 0)
                                         for r in reports),
            "accounts": sum(r["accounts_count"] for r in reports),
            "skipped_platforms": [s["platform"] for s in (skipped or [])],
        },
        "platforms": {r["platform"]: r for r in reports},
        "skipped": skipped or [],
    }
