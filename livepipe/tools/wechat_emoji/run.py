"""livepipe.tools.wechat_emoji.run"""

from __future__ import annotations

from pathlib import Path
import argparse
import json

from .common import _log
from .common import contact_sheets
from .fetch import copy_src
from .fetch import extract_pngs
from .fetch import fetch_tarball
from .records import records_from_files
from .source import BRANCH
from .source import DEFAULT_THRESHOLDS
from .source import REPO
from .table import build_table



def run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    img_dir = out / "img"

    if args.src:
        copy_src(Path(args.src), img_dir)
    else:
        extract_pngs(fetch_tarball(out / f"{REPO.split('/')[-1]}-{BRANCH}.tar.gz"), img_dir)

    recs = records_from_files(img_dir)
    if not recs:
        raise SystemExit(f"{img_dir} 里一张 NNN_名称.png 都没有，检查 --src / 下载是否成功")
    ns = [r["index"] for r in recs]
    if ns != list(range(1, len(ns) + 1)):
        missing = sorted(set(range(1, 110)) - set(ns))
        _log(f"[warn] 序号不连续（缺 {missing[:10]}）—— 表仍然生成，但会缺表情")
    sizes = sorted({tuple(r["size"]) for r in recs})
    _log(f"[table] {len(recs)} 条；尺寸 {sizes}；阈值 {DEFAULT_THRESHOLDS}")

    if not args.no_sheet:
        for p in contact_sheets(recs, out):
            _log(f"[sheet] -> {p}（核一遍序号↔名字）")

    table = build_table(recs, DEFAULT_THRESHOLDS)
    dst = out / "wechat-emoji.json"
    dst.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"[done] {table['n_indexed']} 个表情 -> {dst}")
    return 0
