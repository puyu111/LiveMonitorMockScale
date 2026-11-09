"""config 子模块共用的外部依赖（包内 / 脚本直跑两种方式都行）。"""

from __future__ import annotations

try:
    from .. import account_id
except ImportError:                      # 直接以脚本方式运行本包内文件时
    import account_id  # type: ignore
