"""``livepipe/kuaishou_emoji_table.py``（实现在 ``tools/kuaishou_emoji``）。"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "livepipe"

from .tools.kuaishou_emoji import (  # noqa: F401
    DOC,
    KUAISHOU_THRESHOLDS,
    PAGE_URL,
    PANEL_KEY,
    _UA,
    _font,
    _panel_from_obj,
    build_table,
    contact_sheets,
    download,
    extract_panel,
    fetch_page,
    load_panel,
    make_parser,
    normalize_url,
    records_from_panel,
    run,
    write_table,
)

if __name__ == "__main__":
    raise SystemExit(run(make_parser().parse_args()))
