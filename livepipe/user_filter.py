#!/usr/bin/env python
"""``livepipe/user_filter.py``（实现在 ``tools/user_filter``）。"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "livepipe"

from .tools.user_filter import (
    DEFAULT_BASE_URL,
    DEFAULT_EXCLUDE,
    DEFAULT_MODEL,
    DEFAULT_PROMPT,
    LABELS,
    _excluded_by,
    _extract_json,
    _key,
    _post,
    _save_cache,
    group_accounts,
    judge_batch,
    load_comments,
    make_parser,
    run,
    split_panels_of,
)

if __name__ == "__main__":
    raise SystemExit(run(make_parser().parse_args()))
