"""livepipe.tools.kuaishou_emoji.run — 入口实现。"""

from __future__ import annotations

from pathlib import Path
import argparse
import json

from .fetch import download
from .fetch import load_panel
from .fetch import records_from_panel
from .table import build_table
from .table import contact_sheets
from .table import write_table


def run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    (out / "img").mkdir(parents=True, exist_ok=True)

    panel, source = load_panel(args.src)
    recs = records_from_panel(panel)
    print(f"[panel] {len(recs)} 个 token · 来源 {source}")

    (out / "panel_raw.json").write_text(
        json.dumps(panel, ensure_ascii=False, indent=1), encoding="utf-8")

    if not args.no_download:
        ok = bad = 0
        for i, r in enumerate(recs, 1):
            good, note = download(r["url"], out / "img" / r["img"])
            r["note"] = note
            ok, bad = (ok + 1, bad) if good else (ok, bad + 1)
            if i % 50 == 0 or not good:
                print(f"  [{i}/{len(recs)}] {'ok' if good else 'FAIL'} "
                      f"{r['token']} {note}", flush=True)
        print(f"[download] 成功 {ok}，失败 {bad}")

    table = build_table(recs, source)
    dst = out / "kuaishou-emoji.json"
    write_table(table, dst)
    print(f"[done] {table['n_indexed']} 条 → {dst}（byId / byToken / byName + thresholds）")

    if not args.no_sheet:
        sheets = contact_sheets(recs, out)
        print(f"[sheets] 验收表 {len(sheets)} 张：" + ", ".join(p.name for p in sheets))
    return 0
