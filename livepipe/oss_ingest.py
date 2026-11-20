#!/usr/bin/env python
"""``livepipe/oss_ingest.py``（实现在 ``tools/oss_ingest``）。

薄壳：既支持 ``python livepipe/oss_ingest.py ...``（直接跑脚本），也支持
``import livepipe.oss_ingest``。导出 ``main`` / ``make_parser`` / ``selftest``。
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "livepipe"

from .tools.oss_ingest.cli import main, make_parser
from .tools.oss_ingest.selftest import selftest


if __name__ == "__main__":
    raise SystemExit(main())
