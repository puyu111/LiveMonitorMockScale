"""``python -m livepipe`` 入口。

    python -m livepipe run    <config.json> [--only a,b] [--skip c] [--dry-run]
    python -m livepipe panels <config.json>     # 阶段①：只定位评论区 → 待人工标注 platform
    python -m livepipe check  <config.json>     # 只校验配置，不跑
    python -m livepipe match  <名单> <直播路径>  # 撞库：名单 × 已交付账号（--threshold 0.8）
    python -m livepipe steps                    # 列出可用步骤
    python -m livepipe example [PATH]           # 把示例配置写到 PATH（默认 stdout）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import STEPS, config as C, pipeline as P

_EXAMPLE = Path(__file__).resolve().parent / "examples" / "pipeline.example.json"


def _cmd_run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="livepipe run", description="按 JSON 配置跑管线")
    ap.add_argument("config", help="JSON 配置文件")
    ap.add_argument("--only", default=None, help="只跑这些步骤（逗号分隔）")
    ap.add_argument("--skip", default=None, help="跳过这些步骤（逗号分隔）")
    ap.add_argument("--dry-run", action="store_true", help="只打印要做什么，不执行")
    ap.add_argument("--print-config", action="store_true",
                    help="打印合并默认值后的完整配置（含绝对路径）后退出")
    a = ap.parse_args(argv)

    cfg = C.load(a.config)
    if a.print_config:
        print(json.dumps({k: v for k, v in cfg.items()}, ensure_ascii=False, indent=2))
        return 0
    only = [s.strip() for s in a.only.split(",") if s.strip()] if a.only else None
    skip = [s.strip() for s in a.skip.split(",") if s.strip()] if a.skip else None
    return P.run_pipeline(cfg, only=only, skip=skip, dry_run=a.dry_run)


def _cmd_check(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="livepipe check", description="只校验配置")
    ap.add_argument("config")
    a = ap.parse_args(argv)
    cfg = C.load(a.config)
    print(f"✔ 配置合法：{cfg['_path']}")
    print(f"  步骤：{' → '.join(C.resolve(cfg))}")
    segs = C.segments(cfg)
    per_seg = [s for s in C.PER_SEGMENT_STEPS if s in C.resolve(cfg)]
    if cfg["_videos"]:
        print(f"  多段素材：{len(segs)} 段（每段各跑一遍 {'/'.join(per_seg)}）")
        for s in segs:
            print(f"    {s['name']:<8} {Path(s['video']).name}"
                  f"{'  （已有 rows.jsonl ⇒ 复算）' if s['rows'] else ''}")
    print(f"  面板模式：{cfg['panels']['mode']}"
          f"（文件 {cfg['panels']['file'] or '<段目录>/panels.json'}）")
    for s in segs:
        try:
            pf, spec = C.resolve_panels(cfg, write=False, seg=s)
        except C.ConfigError as e:
            print(f"  面板：⚠ {s['name'] or ''} 还不能用于解析")
            for line in str(e).splitlines():
                print(f"        {line}")
            continue
        tag = f"  [{s['name']}]" if cfg["_videos"] else "  "
        if pf:
            print(f"{tag}面板文件：{pf}")
        print(f"{tag}表情表：{spec or '(不跑表情)'}")
    return 0


def _cmd_panels(argv: list[str]) -> int:
    """阶段①：只跑定位，产出待人工标注 platform 的面板文件。"""
    ap = argparse.ArgumentParser(
        prog="livepipe panels",
        description="只跑「定位评论区」这一步，产出待人工标注平台的面板文件")
    ap.add_argument("config")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    cfg = C.load(a.config)
    rc = P.run_pipeline(cfg, only=["panels"], dry_run=a.dry_run)
    if rc == 0 and not a.dry_run:
        print(f"\n下一步：打开 {cfg['panels']['file']}，给每个面板填 platform"
              f"（可用：{'、'.join(cfg['platforms'])}），然后跑：\n"
              f"  python -m livepipe run {a.config}")
    return rc


def _cmd_match(argv: list[str]) -> int:
    """撞库（独立工具，不走管线）：名单 × 已交付账号目录。"""
    from .tools.list_match.cli import main as list_match_main
    return list_match_main(argv)


def _cmd_steps(_argv: list[str]) -> int:
    print("可用步骤（按默认顺序）：")
    for i, s in enumerate(STEPS, 1):
        doc = (P._STEPS[s].__doc__ or "").strip().splitlines()
        print(f"  {i}. {s:12s} {doc[0] if doc else ''}")
    return 0


def _cmd_example(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="livepipe example")
    ap.add_argument("out", nargs="?", default=None, help="写到这个路径；省略则打到 stdout")
    a = ap.parse_args(argv)
    text = _EXAMPLE.read_text(encoding="utf-8")
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"示例配置 → {a.out}")
    else:
        print(text, end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    table = {"run": _cmd_run, "check": _cmd_check, "panels": _cmd_panels,
             "match": _cmd_match, "steps": _cmd_steps, "example": _cmd_example}
    if cmd not in table:
        print(f"未知子命令：{cmd}\n", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        return 2
    return table[cmd](rest)


if __name__ == "__main__":
    raise SystemExit(main())
