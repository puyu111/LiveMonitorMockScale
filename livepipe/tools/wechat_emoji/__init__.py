"""``wechat_emoji_table.py`` 的实现（入口见 ``livepipe/wechat_emoji_table.py``）。"""

from __future__ import annotations

from .source import (
    BRANCH,
    DEFAULT_THRESHOLDS,
    FNAME_RE,
    REPO,
    SOURCE,
    SOURCE_DESC,
    SUBDIR,
    TARBALL,
)

from .common import (
    _font,
    _log,
    _ua,
    contact_sheets,
)

from .fetch import (
    copy_src,
    extract_pngs,
    fetch_tarball,
)

from .records import (
    records_from_files,
)

from .table import (
    build_table,
)

from .run import (
    run,
)

from .parser import (
    make_parser,
)

