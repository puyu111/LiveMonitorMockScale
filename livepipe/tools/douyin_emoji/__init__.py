"""``douyin_emoji_table.py`` 的实现（入口见 ``livepipe/douyin_emoji_table.py``）。"""

from __future__ import annotations

from .source import (
    API_URL,
    DOUYIN_THRESHOLDS,
    OBJ_PREFIX,
    _UA,
    _URL_RE,
)

from .fetch import (
    download,
    download_any,
    extract_urls,
    fetch_api,
    records_from_api,
)

from .table import (
    _font,
    build_table,
    contact_sheets,
    merge,
)

from .cli import (
    make_parser,
    run,
    run_api,
)

