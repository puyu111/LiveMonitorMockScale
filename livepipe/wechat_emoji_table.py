#!/usr/bin/env python
"""``livepipe/wechat_emoji_table.py``（实现在 ``tools/wechat_emoji``）。"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "livepipe"

from .tools.wechat_emoji import (
    BRANCH,
    DEFAULT_THRESHOLDS,
    FNAME_RE,
    REPO,
    SOURCE,
    SOURCE_DESC,
    SUBDIR,
    TARBALL,
    _font,
    _log,
    _ua,
    build_table,
    contact_sheets,
    copy_src,
    extract_pngs,
    fetch_tarball,
    make_parser,
    records_from_files,
    run,
)

if __name__ == "__main__":
    raise SystemExit(run(make_parser().parse_args()))
