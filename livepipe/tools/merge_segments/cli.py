"""livepipe.tools.merge_segments.cli"""

from __future__ import annotations

import sys

from .collect import collect
from .qualify import qualify



class _Args:
    """``python -m livepipe.merge_segments <config> qualify|collect`` 用的极简入口。"""


def main(argv: list[str] | None = None) -> int:
    """单独跑这两步（不经管线）：``python -m livepipe.merge_segments cfg.json collect``。

    多段素材下从 ``cfg["output"]["dir"]/segments.json`` 读段表（管线跑过任一步就会写），
    读不到就现场用 :func:`livepipe.config.load` 展开一次配置。
    """
    from ... import config as C
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[1] not in ("qualify", "collect"):
        print(__doc__)
        print("用法：python -m livepipe.merge_segments <config.json> qualify|collect")
        return 2
    cfg = C.load(argv[0])
    segs = C.segments(cfg)
    if argv[1] == "qualify":
        return qualify(cfg, segs)
    return collect(cfg, segs)
