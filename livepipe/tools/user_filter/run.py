"""livepipe.tools.user_filter.run"""

from __future__ import annotations

from ... import account_id as aid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import json
import os
import threading
import time

from .cache import _key
from .cache import _save_cache
from .comments import load_comments
from .comments import split_panels_of
from .group import group_accounts
from .judge import judge_batch



def run(args: argparse.Namespace) -> int:
    src = Path(args.comments)
    if not src.exists():
        raise SystemExit(f"找不到评论文件：{src}")
    comments = load_comments(str(src))
    keywords = [k.strip() for k in str(args.exclude).split(",") if k.strip()]

    # ---- ① 按账号聚合 + 用户名排除词 ----
    # 账号 = (昵称, 等级标)：只有面板开了按等级标认人时才取等级标（见 account_id）。
    split_panels = split_panels_of(getattr(args, "identity_panels", ""))
    masked_panels = split_panels_of(getattr(args, "identity_masked", ""))
    by_user, anon, excluded = group_accounts(comments, keywords, split_panels, masked_panels)

    # ---- ② 预筛：评论数 < N 的账号不可能合格（省掉大部分调用）----
    n = int(args.min_positive)
    candidates = {u: v for u, v in by_user.items() if len(v) >= n}
    cand_comments = sum(len(v) for v in candidates.values())
    print(f"[filter] 评论 {len(comments)} 条（匿名 {anon} 条，用户名命中排除词 "
          f"{len(excluded)} 条）")
    print(f"[filter] 账号 {len(by_user)} 个 → 评论数 ≥ {n} 的候选 {len(candidates)} 个"
          f"（{cand_comments} 条评论）"
          + (f" · 按 (昵称, 等级标) 认人：{sorted(split_panels)}" if split_panels else "")
          + (f" · 其中只对**打码昵称**拆等级：{sorted(masked_panels)}" if masked_panels else ""))

    # ---- ③ 缓存：重复评论只判一次 ----
    cache_path = Path(args.cache) if args.cache else src.parent / "judgments.jsonl"
    cache: dict[str, str] = {}
    if cache_path.exists():
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                cache[d["k"]] = d["label"]
    todo = [(u, c) for u, cs in candidates.items() for c in cs
            if _key(c.get("content") or "", c.get("emoji_text") or "") not in cache]
    uniq = {_key(c.get("content") or "", c.get("emoji_text") or "") for _, c in todo}
    print(f"[filter] 待判 {len(todo)} 条评论，其中去重后 {len(uniq)} 条"
          f"（缓存已有 {len(cache)} 条）")

    if args.dry_run:
        bs = max(1, int(args.batch_size))
        print(f"[filter] dry-run：约 {len(uniq)} 次单条判定 / 按 batch={bs} 合并后约 "
              f"{(len(uniq) + bs - 1) // bs} 次调用。未调用模型。")
        return 0

    api_key = args.api_key or os.environ.get(args.api_key_env or "", "")
    if not api_key:
        raise SystemExit(
            f"没有 API key：用 --api-key，或设环境变量 {args.api_key_env or 'NLP_API_KEY'}")

    # ---- ④ 批量判定 ----
    uniq_list = sorted(uniq)
    bs = max(1, int(args.batch_size))
    # 哈希 → 文本，方便回填
    text_of = {_key(c.get("content") or "", c.get("emoji_text") or ""): c
               for _, c in todo}
    batches = [uniq_list[i:i + bs] for i in range(0, len(uniq_list), bs)]
    if args.limit:
        batches = batches[: int(args.limit)]
    print(f"[filter] 调模型：{len(batches)} 批 × batch={bs}，并发 {args.concurrency}"
          f"，模型 {args.model}")

    lock = threading.Lock()
    done = [0]
    t0 = time.time()

    def work(batch: list[str]) -> None:
        items = [(text_of[h].get("content") or "", text_of[h].get("emoji_text") or "")
                 for h in batch]
        labels = judge_batch(items, base_url=args.base_url, api_key=api_key,
                             model=args.model, prompt=args.prompt,
                             timeout=float(args.timeout), retries=int(args.retries),
                             verbose=True)
        with lock:
            for h, lab in zip(batch, labels):
                cache[h] = lab
            _save_cache(cache_path, cache)      # ★ 每批立即落盘 ⇒ 中断/失败也不丢已判的
            done[0] += 1
            if done[0] % 10 == 0 or done[0] == len(batches):
                el = time.time() - t0
                print(f"  [{done[0]}/{len(batches)}] 批 已用 {el:.0f}s", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, int(args.concurrency))) as ex:
        list(ex.map(work, batches))

    # ---- ⑤ 落盘 + 汇总 ----
    _save_cache(cache_path, cache)          # 收尾再存一次（幂等；正常路径每批已存过）

    users = []
    for (u, lv), cs in sorted(candidates.items()):
        rows = []
        for c in cs:
            lab = cache.get(_key(c.get("content") or "", c.get("emoji_text") or ""), "中性")
            rows.append({"t": c.get("t"), "panel": c.get("panel"), "label": lab,
                         "content": c.get("content"), "emoji_text": c.get("emoji_text"),
                         "roi": c.get("roi")})
        npos = sum(1 for r in rows if r["label"] == "正面")
        if npos < n:
            continue
        # ``level`` 是账号身份的一半 —— 交付那边（merge_segments/export）只认
        # (username, level) 这一对，index 也照它走（见 account_id）。
        users.append({"username": u, "level": lv, "n_comments": len(rows), "n_positive": npos,
                      "n_negative": sum(1 for r in rows if r["label"] == "负面"),
                      "n_neutral": sum(1 for r in rows if r["label"] == "中性"),
                      "comments": rows})
    users.sort(key=lambda r: (-r["n_positive"], -r["n_comments"], r["username"], r["level"]))

    out = Path(args.out) if args.out else src.parent / "users.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for u in users:
            f.write(json.dumps(u, ensure_ascii=False) + "\n")

    summary = {"source": str(src), "min_positive": n, "model": args.model,
               "exclude_keywords": keywords,
               "identity_panels": sorted(split_panels),
               "identity_masked": sorted(masked_panels),
               "n_comments": len(comments), "n_anonymous": anon,
               "n_users": len(by_user), "n_candidates": len(candidates),
               "n_qualified": len(users),
               "n_judged": len(uniq_list), "n_calls": len(batches),
               "excluded_users": sorted({e["username"] for e in excluded}),
               "elapsed_s": round(time.time() - t0, 1)}
    (out.parent / "users.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[filter] 合格账号 {len(users)} 个（≥{n} 条正面）")
    for u in users[:10]:
        print(f"  {u['n_positive']:2d}/{u['n_comments']:2d} 正面  "
              f"{aid.display(u['username'], u['level'])}")
    if len(users) > 10:
        print(f"  … 其余 {len(users) - 10} 个见 {out}")
    print(f"[done] -> {out}（+ users.json / {cache_path.name}）")
    return 0
