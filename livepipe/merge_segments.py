#!/usr/bin/env python
"""``livepipe/merge_segments.py``（实现在 ``tools/merge_segments``）。"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "livepipe"

from .tools.merge_segments import (
    _Args,
    _BAD,
    _README_HEAD,
    _WS,
    _label,
    _readme,
    _stage_dirs,
    _ukey,
    _uname,
    collect,
    log,
    main,
    qualify,
    read_jsonl,
    sanitize,
    write_jsonl,
)

if __name__ == "__main__":
    raise SystemExit(main())
