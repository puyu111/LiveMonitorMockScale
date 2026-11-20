"""``python -m livepipe verify`` 的入口：读配置 → 核对盘上产物 → 打印结论。"""

from __future__ import annotations

import argparse
import json
import sys

from ... import config as C
from .checks import verify
from .common import ICON, LEVEL_ORDER
from .doc import DOC
from .selftest import selftest


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m livepipe verify", description=DOC,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("config", nargs="?", help="配置文件（与 run 用的是同一份）")
    p.add_argument("--only", default=None,
                   help="只核对这些步骤（逗号分隔；与 run --only 同义）")
    p.add_argument("--skip", default=None,
                   help="不核对这些步骤（逗号分隔）")
    p.add_argument("--json", action="store_true", help="只输出机读 JSON")
    p.add_argument("--strict", action="store_true",
                   help="警告也算失败（退出码 1）；交付前检查用")
    p.add_argument("--time-tolerance", type=float, default=120.0, metavar="SEC",
                   help="视频文件名区间与实际时长的容差秒数（默认 120）")
    p.add_argument("--no-media-time", action="store_true",
                   help="不核对视频时间（不调用 ffprobe）")
    p.add_argument("--video-map", action="append", default=None, metavar="OLD=NEW",
                   help="素材盘挪位置后映射视频路径前缀（可重复），如 "
                        "'/home/puyu/work/data=/run/media/puyu/MyDisk'")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="每条结论都打印细节（默认只打印警告/错误细节）")
    p.add_argument("--selftest", action="store_true", help="跑纯函数/临时目录自检后退出")
    return p


def _list(value: str | None) -> list[str] | None:
    return [s.strip() for s in value.split(",") if s.strip()] if value else None


def _video_map(values: list[str] | None) -> list[tuple[str, str]] | None:
    out: list[tuple[str, str]] = []
    for value in values or []:
        old, sep, new = str(value).partition("=")
        if not sep or not old:
            raise ValueError(f"--video-map 要写成 旧前缀=新前缀：{value!r}")
        out.append((old, new))
    return out or None


def _print_report(rep, *, verbose: bool = False) -> None:
    print(f"[verify] 配置：{rep.config}")
    print(f"[verify] 名称：{rep.name}")
    print(f"[verify] 输出：{rep.output_dir}")
    print(f"[verify] 校验步骤：{' → '.join(rep.steps) or '(空)'}")
    print()
    for c in rep.checks:
        if c.level == "info" and not verbose:
            continue
        print(f"  {ICON[c.level]} [{c.step}] {c.target}  {c.message}")
        if c.details and (verbose or c.level in ("warn", "fail")):
            for d in c.details:
                print(f"        - {d}")
    counts = {k: sum(1 for c in rep.checks if c.level == k) for k in LEVEL_ORDER}
    print()
    verdict = "完整" if rep.ok else "不完整"
    print(f"[verify] 结论：{verdict}（{counts['fail']} 错误 / {counts['warn']} 警告）")
    print(f"[verify] 明细：{counts['ok']} 通过 · {counts['info']} 提示 · "
          f"{counts['warn']} 警告 · {counts['fail']} 错误")


def _load_config(path: str, *, json_mode: bool):
    """先按严格模式读；素材不可达时退回宽松模式（只核对产物，不需要原视频）。"""
    try:
        return C.load(path), False
    except C.ConfigError as e:
        msg = str(e)
        if not any(k in msg for k in ("不存在", "没命中", "没有视频文件")):
            raise
        cfg = C.load(path, check_videos=False)
        if not json_mode:
            print(f"[verify][note] 素材路径不可达（{msg.splitlines()[0]}）"
                  "⇒ 以宽松模式读配置，只核对已落盘产物")
            print()
        return cfg, True


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.config:
        make_parser().print_help()
        return 2
    only, skip = _list(args.only), _list(args.skip)
    bad = [s for s in (only or []) + (skip or []) if s not in C.STEPS]
    if bad:
        print(f"[verify][error] 未知步骤 {bad}；可用：{list(C.STEPS)}", file=sys.stderr)
        return 2
    if args.time_tolerance < 0:
        print("[verify][error] --time-tolerance 不能为负", file=sys.stderr)
        return 2
    try:
        vmap = _video_map(args.video_map)
    except ValueError as e:
        print(f"[verify][error] {e}", file=sys.stderr)
        return 2
    try:
        cfg, _ = _load_config(args.config, json_mode=args.json)
    except C.ConfigError as e:
        print(f"[verify][error] 配置读不了：{e}", file=sys.stderr)
        return 2
    rep = verify(cfg, only=only, skip=skip,
                 media_time=not args.no_media_time,
                 time_tolerance=args.time_tolerance, video_map=vmap)
    if args.json:
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_report(rep, verbose=args.verbose)
    if rep.errors:
        return 1
    if args.strict and rep.warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
