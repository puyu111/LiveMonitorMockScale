"""``livepipe.tools.oss_ingest`` —— OSS 入库工具（清单 / 审计 / 同步 / 收尾 / 体检）。

子命令入口（薄壳 ``livepipe/oss_ingest.py``）：

    python -m livepipe.tools.oss_ingest manifest <config...>
    python -m livepipe.tools.oss_ingest audit    <config...>
    python -m livepipe.tools.oss_ingest sync     <src> <dst>
    python -m livepipe.tools.oss_ingest ingest   <part>
    python -m livepipe.tools.oss_ingest check-env
    python -m livepipe.tools.oss_ingest --selftest

所有子模块只从 :mod:`.common` 导入公共函数；``common`` 只用标准库。
"""

from __future__ import annotations

from .common import (
    DEFAULT_OSS_ROOT,
    MANIFEST_VERSION,
    OssIngestError,
    file_size,
    human_bytes,
    iter_files,
    oss_root,
    probe_duration,
    read_json,
    rel_to,
    sha256_file,
    write_json_atomic,
)

__all__ = [
    "DEFAULT_OSS_ROOT",
    "MANIFEST_VERSION",
    "OssIngestError",
    "file_size",
    "human_bytes",
    "iter_files",
    "oss_root",
    "probe_duration",
    "read_json",
    "rel_to",
    "sha256_file",
    "write_json_atomic",
]
