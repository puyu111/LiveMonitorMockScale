"""``merge_segments.py`` 的实现（入口见 ``livepipe/merge_segments.py``）。"""

from __future__ import annotations

from .common import (
    _BAD,
    _WS,
    _label,
    _ukey,
    _uname,
    log,
    read_jsonl,
    sanitize,
    write_jsonl,
)

from .qualify import (
    qualify,
)

from .collect import (
    _README_HEAD,
    _readme,
    _stage_dirs,
    collect,
)

from .cli import (
    _Args,
    main,
)

