"""livepipe.tools.douyin_emoji.cli"""

from __future__ import annotations

from .doc import DOC
from pathlib import Path
import argparse
import json
import sys

from .fetch import download
from .fetch import download_any
from .fetch import extract_urls
from .fetch import fetch_api
from .fetch import records_from_api
from .source import API_URL
from .source import OBJ_PREFIX
from .table import build_table
from .table import contact_sheets
from .table import merge



def run_api(args: argparse.Namespace, out: Path) -> int:
    """走官方接口：拿到什么名字就是什么名字，不做人工标注。"""
    payload = fetch_api(args.api, out / "emoji_list_api.json")
    recs = records_from_api(payload)
    hidden = sum(1 for r in recs if r["hide"])
    print(f"[api] version={payload.get('version')} 共 {len(recs)} 个表情"
          f"（hide!=0：{hidden}）")
    no_hash = [r["idx"] for r in recs if not r["hash"]]
    if no_hash:
        print(f"[warn] 这些条目没解析出 hash，已排除：{no_hash}", file=sys.stderr)
    seen: dict[str, int] = {}
    for r in recs:
        if r["hash"]:
            seen[r["hash"]] = seen.get(r["hash"], 0) + 1
    dup = {h: c for h, c in seen.items() if c > 1}
    if dup:
        print(f"[warn] 重复 hash {len(dup)} 个（同一张图对多个名字）："
              f"{list(dup)[:5]}", file=sys.stderr)

    if not args.no_download:
        ok = bad = 0
        for i, r in enumerate(recs, 1):
            if not r["hash"]:
                continue
            dest = out / "img" / f"{r['hash']}.webp"
            good, note = download_any(r["urls"], dest)
            r["note"] = note
            ok, bad = (ok + 1, bad) if good else (ok, bad + 1)
            if i % 50 == 0 or not good:
                print(f"  [{i}/{len(recs)}] {'ok' if good else 'FAIL'} "
                      f"{r['token']} {r['hash'][:8]} {note}", flush=True)
        print(f"[download] 成功 {ok}，失败 {bad}")

    (out / "emoji_raw.json").write_text(json.dumps(recs, ensure_ascii=False, indent=2))
    source = args.source or (f"{API_URL} (version {payload.get('version')})"
                             if args.api.startswith("http") else args.api)
    table = build_table(recs, source)
    (out / "douyin-emoji.json").write_text(
        json.dumps(table, ensure_ascii=False, indent=2))
    sheets = contact_sheets(recs, out, per_sheet=args.per_sheet,
                            captions=[r["token"] for r in recs])
    print(f"[sheets] 验收表 {len(sheets)} 张：" + ", ".join(p.name for p in sheets))
    print(f"[done] {table['n_indexed']} 条 → {out/'douyin-emoji.json'}"
          f"（byToken/byName/bySlug/byHash）")
    return 0


def run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    (out / "img").mkdir(parents=True, exist_ok=True)

    if args.api and not args.merge:
        return run_api(args, out)

    if args.labels and args.merge:
        raw = json.loads((out / "emoji_raw.json").read_text())
        table = merge(raw, json.loads(Path(args.labels).read_text()),
                      out / "douyin-emoji.json", args.source)
        print(f"[done] {len(table['byHash'])}/{table['n_raw']} 条已标注 -> {out/'douyin-emoji.json'}")
        return 0

    text = Path(args.html).read_text(encoding="utf-8", errors="replace") \
        if args.html != "-" else sys.stdin.read()
    raw = extract_urls(text)
    if not raw:
        print("[error] 没抽到任何表情 URL —— 检查 DOM 里是否有 "
              f"`{OBJ_PREFIX}<32位hex>`", file=sys.stderr)
        return 2
    dup = [r for r in raw if r["count"] > 1]
    print(f"[parse] 唯一表情 {len(raw)} 个"
          f"（DOM 里重复出现的 {len(dup)} 个，最多 {max((r['count'] for r in dup), default=1)} 次）")

    if not args.no_download:
        ok = bad = 0
        for i, it in enumerate(raw, 1):
            p = out / "img" / f"{it['hash']}.webp"
            good, note = download(it["url"], p)
            it["file"] = str(p) if good else None
            it["note"] = note
            ok, bad = (ok + 1, bad) if good else (ok, bad + 1)
            if i % 25 == 0 or not good:
                print(f"  [{i}/{len(raw)}] {'ok' if good else 'FAIL'} {it['hash'][:8]} {note}",
                      flush=True)
        print(f"[download] 成功 {ok}，失败 {bad}")
    else:
        for it in raw:
            it["file"] = None

    (out / "emoji_raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2))
    sheets = contact_sheets(raw, out, per_sheet=args.per_sheet)
    print(f"[sheets] {len(sheets)} 张：" + ", ".join(p.name for p in sheets))
    print(f"[done] 原始表 {out/'emoji_raw.json'}；看图写 labels.json 后加 "
          f"`--labels labels.json --merge` 生成 douyin-emoji.json")
    return 0


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=DOC,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--html", default="-", help="粘贴的 DOM 文件（- 表示 stdin）")
    p.add_argument("--api", default=None, nargs="?", const=API_URL,
                   help="走官方表情接口（默认取 live 接口；也可给本地 JSON 路径）")
    p.add_argument("--out", default="out/douyin_emoji")
    p.add_argument("--per-sheet", type=int, default=100, help="每张 contact sheet 放几个")
    p.add_argument("--no-download", action="store_true", help="只解析 URL，不下载")
    p.add_argument("--labels", default=None, help="人工标注 JSON（键为 sheet 序号）")
    p.add_argument("--merge", action="store_true", help="把 --labels 并进最终表")
    p.add_argument("--source", default="douyin web emoji panel DOM",
                   help="写进最终表的 source 字段")
    return p
