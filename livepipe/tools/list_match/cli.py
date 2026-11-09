"""``python -m livepipe match`` 的入口：读名单 + 交付树 → 匹配 → 落报告。"""

from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from pathlib import Path

from .common import platform_label, platform_sort_key
from .doc import DOC
from .matching import build_report, match_platform
from .report import write_reports
from .selftest import selftest
from .sources import (
    SourceError,
    discover_platforms,
    load_accounts,
    load_stream_accounts,
    merge_accounts,
    read_name_list,
    stream_segments,
)


def _log(*a) -> None:
    print(*a, flush=True)


def _platform_set(values: list[str] | None) -> set[str] | None:
    out: set[str] = set()
    for value in values or []:
        for part in re.split(r"[,，]", str(value)):
            part = part.strip()
            if part:
                out.add(platform_label(part) or part)
    return out or None


def _video_map(values: list[str] | None) -> list[tuple[str, str]] | None:
    """``--video-map`` 的 ``旧前缀=新前缀`` 列表 → [(旧, 新), …]。"""
    out: list[tuple[str, str]] = []
    for value in values or []:
        old, sep, new = str(value).partition("=")
        if not sep or not old:
            raise SystemExit(f"--video-map 要写成 旧前缀=新前缀：{value!r}")
        out.append((old, new))
    return out or None


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m livepipe match", description=DOC,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("names", nargs="?", help="名单文件（.xlsx / .csv / .tsv / .txt）")
    p.add_argument("live", nargs="?",
                   help="直播产物根目录（下面有 <平台>/all_users_*；或直接给一棵交付树）")
    p.add_argument("-t", "--threshold", type=float, default=0.8,
                   help="命中阈值 0~1（默认 0.8）")
    p.add_argument("--metric", choices=["partial", "ratio"], default="partial",
                   help="匹配指标：partial=容忍昵称截断（默认）/ ratio=只比整名")
    p.add_argument("--sheet", default=None, help="xlsx 的工作表名（默认自动找有平台表头的表）")
    p.add_argument("--layout", choices=["auto", "row", "column"], default="auto",
                   help="名单布局：auto（默认）/ row=名字在平台表头右边 / column=名字在下面")
    p.add_argument("--platform", action="append", default=None,
                   help="只撞这些平台（可重复或逗号分隔）；纯文本/无平台列的名单用它指定")
    p.add_argument("--tree", default=None,
                   help="直接指定某一棵交付树（平台下有多个 all_users_* 阈值目录时用）")
    p.add_argument("--delivered-only", action="store_true",
                   help="只撞交付树里的账号（默认还撞直播全量：发过任意一条评论就算，"
                        "正面/负面/中性都在池子里）")
    p.add_argument("--max-matches", type=int, default=5,
                   help="每个名单名最多列几个命中账号；0=不限（默认 5）")
    p.add_argument("--out", default=None,
                   help="结果目录（默认 <直播路径>/撞库结果）")
    p.add_argument("--no-images", action="store_true",
                   help="不导出标记图（默认把命中账号的交付图原样复制到 <结果>/标记图/）")
    p.add_argument("--no-html", action="store_true",
                   help="不写 撞库清单/ 的 HTML 交付清单（默认写，并自动找配置的场次名）")
    p.add_argument("--title", default=None,
                   help="清单场次名（默认自动从 cwd 里 output.dir 指向本平台的配置取 name）")
    p.add_argument("--date", default=None,
                   help="清单日期（默认自动从配置名/直播目录名推断，如 10.31）")
    p.add_argument("--video-map", action="append", default=None, metavar="OLD=NEW",
                   help="现补图的视频路径前缀重映射（可重复）：summary.json 里的旧路径"
                        "失效时用，如 '/media/old/10.31=/media/new/10.31'")
    p.add_argument("--copy", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--no-xlsx", action="store_true", help="不写 撞库结果.xlsx")
    p.add_argument("--quiet", action="store_true", help="不逐平台打印未命中名单")
    p.add_argument("--selftest", action="store_true", help="跑纯函数自检后退出")
    return p


def _print_summary(report: dict, out: Path, elapsed: float, quiet: bool,
                   files: dict | None = None) -> None:
    s = report["summary"]
    _log(f"[撞库] 名单：{report['inputs'].get('names')}（{s['list_names']} 人）")
    _log(f"[撞库] 直播：{report['inputs'].get('live')}")
    _log(f"[撞库] 阈值 {float(report['inputs'].get('threshold', 0)):.2f}"
         f" · 指标 {report['inputs'].get('metric')}")
    stream = report["inputs"].get("scope") != "delivered"
    universe = "直播账号" if stream else "账号"
    for label in sorted(report["platforms"], key=platform_sort_key):
        r = report["platforms"][label]
        _log(f"  {label:<4} 名单 {r['list_count']:>3} · 命中 {r['matched_count']:>3}"
             f" · 未命中 {r['unmatched_count']:>3}"
             f" · {universe} {r['accounts_count']:>4}"
             f"（交付 {r.get('delivered_count', 0):>3}）"
             f" · 命中账号 {r['matched_accounts']:>3}"
             f"（仅直播 {r.get('matched_not_delivered', 0):>2}）")
    for skip in report.get("skipped") or []:
        _log(f"  {skip['platform']:<4} 跳过：{skip['reason']}")
    _log(f"[撞库] 合计：名单 {s['list_names']} · 命中 {s['matched_names']}"
         f" · 未命中 {s['unmatched_names']} · 命中账号 {s['matched_accounts']}"
         f"（仅直播 {s.get('matched_not_delivered', 0)}）"
         f" · 用时 {elapsed:.1f}s")
    if not quiet:
        for label in sorted(report["platforms"], key=platform_sort_key):
            names = [u["list_name"] for u in report["platforms"][label]["unmatched"]]
            if names:
                shown = "、".join(names[:20])
                more = f" …（共 {len(names)}）" if len(names) > 20 else ""
                _log(f"[撞库] 未命中·{label}：{shown}{more}")
    _log(f"[撞库] 结果 → {out}")
    if files is None:
        xlsx_written = (out / "撞库结果.xlsx").is_file()
    else:
        xlsx_written = bool(files.get("xlsx"))
    _log(f"  matches.json · 命中明细.csv · 未命中明细.csv"
         f"{' · 撞库结果.xlsx' if xlsx_written else ''}")
    img = (files or {}).get("images")
    if img:
        r = img.get("rendered") or {}
        extra = (f" · 现补 {r.get('accounts', 0)} 个（仅直播命中）"
                 if r.get("accounts") else "")
        _log(f"[撞库] 标记图：{img['accounts']} 个账号目录 · {img['images']} 张 · "
             f"{img['bytes'] / 1048576:.1f} MB{extra} → {img['dir']}")
        if r.get("skipped"):
            _log(f"[撞库][warn] 有 {r['skipped']} 个「仅直播命中」补不了图，只看表格")
    elif files is not None:
        _log("[撞库] 标记图：--no-images，未导出")
    doc = (files or {}).get("html")
    if doc:
        _log(f"[撞库] 交付清单：{len(doc['files'])} 个 HTML → {doc['dir']}")
    elif files is not None:
        _log("[撞库] 交付清单：--no-html，未写")


def run(args: argparse.Namespace) -> int:
    if args.selftest:
        return selftest()
    if not args.names or not args.live:
        raise SystemExit("用法：python -m livepipe match <名单> <直播路径> "
                         "[--threshold 0.8]（或 --selftest）")
    if not (0.0 <= float(args.threshold) <= 1.0):
        raise SystemExit(f"--threshold 要在 0~1：{args.threshold}")
    if int(args.max_matches) < 0:
        raise SystemExit("--max-matches 不能为负（0 = 不限）")
    vmap = _video_map(args.video_map)

    t0 = time.time()
    names = Path(args.names)
    live = Path(args.live)
    if not names.is_file():
        raise SystemExit(f"找不到名单：{names}")
    if not live.is_dir():
        raise SystemExit(f"找不到直播产物目录：{live}")

    only = _platform_set(args.platform)
    single = next(iter(only)) if only and len(only) == 1 else None
    try:
        grouped = read_name_list(
            names, sheet=args.sheet, layout=args.layout, platform=single)
        trees = discover_platforms(
            live, tree=Path(args.tree) if args.tree else None,
            platform=single, only=only)

        # 名单没有平台列（.txt 或普通单列表）：只有一个平台就直接用，否则必须 --platform
        if None in grouped:
            entries = grouped.pop(None)
            if len(trees) != 1:
                raise SystemExit(
                    "名单没有平台列；直播产物里有多个平台，用 --platform 指定撞哪个"
                    f"（{'、'.join(t['label'] for t in trees)}）")
            label = trees[0]["label"]
            grouped[label] = entries
            _log(f"[撞库][note] 名单没有平台列 ⇒ 全部按「{label}」撞")

        reports: list[dict] = []
        skipped: list[dict] = []
        tree_labels = {t["label"] for t in trees}
        for label in grouped:
            if label not in tree_labels:
                skipped.append({"platform": label, "reason": "直播产物里没有该平台"})
                _log(f"[撞库][warn] 名单有「{label}」列，但直播产物里没有该平台，跳过")
        for t in trees:
            label = t["label"]
            entries = grouped.get(label) or []
            if not entries:
                skipped.append({"platform": label, "reason": "名单里没有该平台的人名"})
                _log(f"[撞库][warn] {label}：名单里没有这个平台的人名，跳过"
                     f"（交付账号 {len(load_accounts(t['tree'], t['manifest']))}）")
                continue
            delivered = load_accounts(t["tree"], t["manifest"])
            if args.delivered_only:
                accounts = delivered
            else:
                stream = load_stream_accounts(t["dir"])
                if stream:
                    accounts = merge_accounts(delivered, stream)
                    _log(f"[撞库][note] {label}：交付账号 {len(delivered)} + "
                         f"直播全量账号 {len(stream)} ⇒ 去重后 {len(accounts)}"
                         f"（发过任意一条评论就算，正面/负面/中性都进池子）")
                    segs = stream_segments(t["dir"])
                    if not any((s / "comments.jsonl").is_file() for s in segs):
                        _log(f"[撞库][warn] {label}：没有 seg*/comments.jsonl，"
                             f"只能退回 users.jsonl（只有正面评论的账号）")
                else:
                    accounts = delivered
                    _log(f"[撞库][warn] {label}：没有 seg*/comments.jsonl 或 "
                         f"users.jsonl，只撞交付树")
            reports.append(match_platform(
                label, entries, accounts, tree=t["tree"], platform_dir=t["dir"],
                threshold=float(args.threshold), metric=args.metric,
                max_matches=int(args.max_matches)))
    except SourceError as e:
        raise SystemExit(f"[撞库] {e}") from e

    if not reports:
        raise SystemExit("[撞库] 一个平台都没撞：检查名单的平台列 / --platform / --tree")

    report = build_report(
        {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "names": str(names.resolve()),
            "live": str(live.resolve()),
            "threshold": float(args.threshold),
            "metric": args.metric,
            "sheet": args.sheet,
            "layout": args.layout,
            "max_matches": int(args.max_matches),
            "scope": "delivered" if args.delivered_only else "stream",
            "title": args.title,
            "date": args.date,
            "platforms": sorted(only) if only else None,
        },
        reports, skipped=skipped)

    out = Path(args.out) if args.out else live / "撞库结果"
    paths = write_reports(out, report, write_xlsx=not args.no_xlsx,
                          images=not args.no_images, html=not args.no_html,
                          log=_log, video_map=vmap)
    _print_summary(report, out, time.time() - t0, args.quiet, files=paths)
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(make_parser().parse_args(argv))
