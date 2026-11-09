#!/usr/bin/env python
"""``livepipe/export_user_images.py``（实现在 ``tools/export_user_images``）。"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "livepipe"

from .tools.export_user_images import (
    FrameReader,
    PLATFORM_CN,
    SCHEMA_VERSION,
    _BAD,
    _WS,
    _job_base,
    _log,
    expand_box,
    frame_index,
    frame_name,
    load_contract,
    load_panel_platform,
    make_parser,
    parse_color,
    pick_comments,
    platform_dir,
    platform_of,
    read_jsonl,
    run,
    sanitize,
    select,
    selftest,
    unique_dir,
    write_images,
)

if __name__ == "__main__":
    raise SystemExit(run(make_parser().parse_args()))
