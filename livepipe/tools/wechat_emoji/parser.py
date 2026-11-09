"""livepipe.tools.wechat_emoji.parser"""

from __future__ import annotations

from .doc import DOC
import argparse



def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=DOC, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="out/wechat_emoji", help="输出目录")
    p.add_argument("--src", default=None,
                   help="已有的 <NNN_名称>.png 目录（离线建表；给了就不联网）")
    p.add_argument("--no-sheet", action="store_true", help="不产出核对用的 sheet_NN.png")
    return p
