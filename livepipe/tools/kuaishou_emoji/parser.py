"""livepipe.tools.kuaishou_emoji.parser — CLI 参数。"""

from __future__ import annotations

from .doc import DOC
import argparse


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=DOC, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="out/kuaishou_emoji", help="输出目录")
    p.add_argument("--src", default=None,
                   help="离线源：另存的直播页 HTML，或 token→URL 的 JSON；不给就现抓 "
                        "https://live.kuaishou.com")
    p.add_argument("--no-download", action="store_true", help="只建表，不下载/不检查图片")
    p.add_argument("--no-sheet", action="store_true", help="不产出验收表 sheet_NN.png")
    return p
