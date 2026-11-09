"""``list_match.py``（撞库）的实现（入口见 ``livepipe/list_match.py``）。

拿活动方给的**名单**（xlsx / csv / txt）去撞已经交付的直播账号目录：
按平台各撞各的，输出命中名单、匹配度、账号目录、未命中名单（带最近账号供复核）。
"""

from __future__ import annotations

from .common import (
    MIN_PARTIAL_LEN,
    PLATFORM_LABELS,
    PLATFORM_ORDER,
    dir_count,
    dir_username,
    display_account,
    is_number,
    match_score,
    normalize_name,
    partial_similarity,
    platform_label,
    platform_sort_key,
    safe_dirname,
    score_norm,
    similarity,
    strip_level,
)

from .doc import DOC

from .sources import (
    SourceError,
    discover_platforms,
    load_accounts,
    load_manifest,
    load_stream_accounts,
    manifest_platform,
    merge_accounts,
    parse_name_table,
    read_name_list,
    read_rows,
    stream_segments,
)

from .matching import (
    build_report,
    match_platform,
)

from .report import (
    HIT_HEADER,
    SUMMARY_HEADER,
    UNHIT_HEADER,
    export_images,
    hit_rows,
    summary_rows,
    unhit_rows,
    write_reports,
)

from .html import (
    build_index_html,
    build_platform_html,
    write_html,
)

from .render import (
    render_missing,
)

from .selftest import (
    selftest,
)

from .cli import (
    main,
    make_parser,
    run,
)
