#!/usr/bin/env python
"""``livepipe/list_match.py``（实现在 ``tools/list_match``）。

撞库：拿活动方给的名单去撞已经交付的直播账号目录。

    python livepipe/list_match.py 撞库名单/10.31人名单.xlsx out/10.31
    python -m livepipe match 撞库名单/10.31人名单.xlsx out/10.31 --threshold 0.8
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "livepipe"

from .tools.list_match import (
    DOC,
    HIT_HEADER,
    MIN_PARTIAL_LEN,
    PLATFORM_LABELS,
    PLATFORM_ORDER,
    SUMMARY_HEADER,
    UNHIT_HEADER,
    SourceError,
    build_index_html,
    build_platform_html,
    build_report,
    dir_count,
    export_images,
    dir_username,
    discover_platforms,
    display_account,
    hit_rows,
    is_number,
    load_accounts,
    load_manifest,
    load_stream_accounts,
    main,
    make_parser,
    merge_accounts,
    manifest_platform,
    match_platform,
    match_score,
    normalize_name,
    parse_name_table,
    partial_similarity,
    platform_label,
    platform_sort_key,
    read_name_list,
    read_rows,
    render_missing,
    run,
    safe_dirname,
    score_norm,
    selftest,
    similarity,
    strip_level,
    summary_rows,
    unhit_rows,
    write_html,
    write_reports,
)

if __name__ == "__main__":
    raise SystemExit(run(make_parser().parse_args()))
