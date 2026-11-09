"""livepipe.parsing.common.log — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import sys



def _log(*a, **kw) -> None:
    """人看的进度/统计一律走 stderr —— stdout 留给数据（JSON 可以随便重定向/管道）。"""
    print(*a, file=sys.stderr, **kw)
