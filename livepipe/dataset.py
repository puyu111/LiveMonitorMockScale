#!/usr/bin/env python
"""``livepipe/dataset.py``（实现在 ``tools/dataset``）。

识别结果（各段 ``comments.jsonl``）→ YOLO 数据集：``review`` 出复核图让人删，
``build`` 按复核目录落盘并过七道闸。说明见 ``python3 -m livepipe.dataset -h``。
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "livepipe"

from .tools.dataset import (
    CLASS_NAMES,
    DEFAULT_SPLIT_RULES,
    build,
    frame_lines,
    make_parser,
    render,
    restore,
    run,
    selftest,
    verify,
    yolo_line,
)

if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
