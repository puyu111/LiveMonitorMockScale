"""``livepipe.tools.dataset.run`` —— 两个子命令的调度。"""

from __future__ import annotations

import json
from pathlib import Path

from .common import DEFAULT_SPLIT_RULES, _log, normalize_split_rules
from .parser import make_parser
from .review import reject_frame, render, restore
from .selftest import selftest
from .sources import frame_groups, scan_runs, summarize, units_of
from .verify import verify
from .write import build


def _rules(path) -> dict:
    doc = DEFAULT_SPLIT_RULES
    if path:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        return normalize_split_rules(doc)
    except ValueError as exc:
        raise SystemExit(f"split 规则有问题：{exc}")


def do_review(args) -> int:
    rules = _rules(args.split_file)
    sides = scan_runs(args.runs, exclude=args.exclude)
    if not sides:
        raise SystemExit("一段素材都没扫到 —— 检查 --runs / --exclude")
    units = units_of(frame_groups(sides), rules)
    st = summarize(units)
    no_roi = sum(1 for s in sides for r in s.rows if not r.get("roi"))
    dropped = sum(s.dropped for s in sides)
    _log(f"[scan] {len(sides)} 段 / {st['videos']} 个视频 · 记录 {st['records']} 条 "
         f"→ 复核单元 {st['units']}（同帧合并 {st['merged']}）· 帧 {st['frames']}"
         + (f" · 无 roi 丢弃 {no_roi} 条" if no_roi else "")
         + (f" · 过滤 {dropped} 条（username/colon 为空）" if dropped else ""))
    _log("[scan] 框（去重前）：" + " ".join(f"{k}={v}" for k, v in st["boxes_raw"].items()))
    for s in sides:
        _log(f"       {s.run}/{s.seg:<5} {len(s.rows):5d} 条 · {Path(s.video).name} "
             f"fps={s.fps:.3f} {s.wh[0]}×{s.wh[1]} {s.mode}")
    render(units, Path(args.out) / "review", scale=args.scale, pad=args.pad,
           quality=args.quality, redraw=args.redraw)
    return 0


def do_build(args) -> int:
    out = Path(args.out)
    review_dir = Path(args.review) if args.review else out / "review"
    if args.restore:
        return 0 if restore(review_dir, args.restore) else 1
    if args.reject_frame:
        return 0 if reject_frame(review_dir, args.reject_frame) else 1
    build(out, review_dir=review_dir, quality=args.quality,
          dry_run=args.dry_run, force=args.force)
    if args.dry_run or args.no_verify:
        return 0
    return 0 if verify(out, review_dir=review_dir, preview_n=args.preview_n)["ok"] else 1


def run(argv=None) -> int:
    args = make_parser().parse_args(argv)
    if getattr(args, "selftest", False):
        return selftest()
    if args.cmd == "review":
        return do_review(args)
    if args.cmd == "build":
        return do_build(args)
    make_parser().print_help()
    return 2
