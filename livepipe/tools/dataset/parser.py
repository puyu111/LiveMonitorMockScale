"""``livepipe.tools.dataset.parser`` —— 命令行。"""

from __future__ import annotations

import argparse

from .doc import DOC


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dataset", description=DOC,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--selftest", action="store_true", help="跑纯函数自检后退出（不读盘）")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("review", help="出复核图 + 写 review/index.jsonl",
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    r.add_argument("--runs", nargs="+", required=True,
                   help="run 目录（里面是 seg*/comments.jsonl），可给多个")
    r.add_argument("--out", required=True, help="输出根目录（复核图写到 <out>/review/）")
    r.add_argument("--exclude", action="append", default=[],
                   help="排除某些段，子串匹配「run/seg」或路径（可重复，如 --exclude _02/seg7）")
    r.add_argument("--split-file", "--split", dest="split_file",
                   help="split 规则 JSON（默认内置：微博→test、17：13-17：33→val、其余 train）")
    r.add_argument("--scale", type=float, default=1.6, help="复核横条的放大倍数（默认 1.6）")
    r.add_argument("--pad", type=int, default=10, help="横条在 ROI 外留的像素边（默认 10）")
    r.add_argument("--quality", type=int, default=90, help="复核图 JPEG 质量（默认 90）")
    r.add_argument(
        "--redraw", action="store_true",
        help="已有复核图也重画（口径变了要刷新旧横条；仍尊重 rejected.jsonl，删掉的不会回来）",
    )

    b = sub.add_parser("build", help="按复核目录落数据集 + 过八道闸",
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    b.add_argument("--out", required=True, help="数据集根目录（review/ 也在里面）")
    b.add_argument("--review", help="复核目录（默认 <out>/review）")
    b.add_argument("--quality", type=int, default=90, help="数据集图 JPEG 质量（默认 90）")
    b.add_argument("--dry-run", action="store_true", help="只算账（复核/删除/图数），不落盘")
    b.add_argument("--force", action="store_true", help="输出里已经有图也照写")
    b.add_argument("--restore", help="撤回一次误删：给复核图文件名（可带路径）")
    b.add_argument("--reject-frame",
                   help="整帧丢弃：给 <视频键>_f<帧号>（如 12-01-13-00_f010946），这一帧的全部评论都不进数据集")
    b.add_argument("--preview-n", type=int, default=20, help="PREVIEW 闸反画几张（默认 20）")
    b.add_argument("--no-verify", action="store_true", help="落完不跑八道闸（只在调试时用）")
    return p
