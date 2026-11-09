"""微博表情表工具的实现（入口见 ``livepipe/weibo_emoji_table.py``）。"""

from __future__ import annotations

from .common import (
    _log,
)

from .source import (
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
)

from .fetch import (
    _get,
    _read_text,
    cdn_dir,
    download,
    index_src,
    url_variants,
)

from .parse import (
    _config_entries,
    load_config,
    load_map,
    parse_panel,
)

from .table import (
    _font,
    build_table,
    contact_sheets,
)

from .cli import (
    make_parser,
    run,
)

