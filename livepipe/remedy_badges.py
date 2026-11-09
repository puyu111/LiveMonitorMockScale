#!/usr/bin/env python
"""``livepipe/remedy_badges.py``（实现在 ``tools/remedy_badges``）。"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "livepipe"

from .tools.remedy_badges import (
    _iden,
    _jsonl,
    _log,
    _stage_name,
    check_cache,
    check_rows,
    delivery_root,
    main,
    new_level_index,
    plan_of,
    read_tree,
    report,
    totals_of,
    verify,
)

if __name__ == "__main__":
    raise SystemExit(main())
