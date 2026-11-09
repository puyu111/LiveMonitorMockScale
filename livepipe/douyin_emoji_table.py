#!/usr/bin/env python
"""``livepipe/douyin_emoji_table.py``（实现在 ``tools/douyin_emoji``）。"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "livepipe"

from .tools.douyin_emoji import (
    API_URL,
    DOUYIN_THRESHOLDS,
    OBJ_PREFIX,
    _UA,
    _URL_RE,
    _font,
    build_table,
    contact_sheets,
    download,
    download_any,
    extract_urls,
    fetch_api,
    make_parser,
    merge,
    records_from_api,
    run,
    run_api,
)

if __name__ == "__main__":
    raise SystemExit(run(make_parser().parse_args()))
