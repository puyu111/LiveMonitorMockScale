#!/usr/bin/env python
"""微博 PC 端表情面板 → ``out/weibo_emoji/weibo-emoji.json``（实现在 ``tools/weibo_emoji``）。"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "livepipe"

from .tools.weibo_emoji import (
    CALIBRATED,
    CDN_DIR,
    CONFIG_API,
    CONFIG_URL,
    HOSTS,
    PARSE_ONLY_GROUP,
    REFERER,
    SOURCE,
    SOURCE_CFG,
    SOURCE_DESC,
    THRESHOLDS,
    THRESHOLDS_NOTE,
    UA,
    _BAD,
    _FACE_RE,
    _GROUP_RE,
    _config_entries,
    _font,
    _get,
    _log,
    _read_text,
    build_table,
    cdn_dir,
    contact_sheets,
    download,
    index_src,
    load_config,
    load_map,
    make_parser,
    parse_panel,
    run,
    url_variants,
)

if __name__ == "__main__":
    raise SystemExit(run(make_parser().parse_args()))
