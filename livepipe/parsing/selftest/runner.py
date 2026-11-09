"""livepipe.parsing.selftest.runner — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import sys

from ..selftest.dedup import _selftest_dedup
from ..selftest.emoji import _selftest_emoji
from ..selftest.fixtures import EXAMPLE
from ..selftest.fixtures import EXPECTED
from ..selftest.panels import _selftest_panels
from ..selftest.roi import _selftest_roi
from ..selftest.rules import _selftest_rules
from ..selftest.sampling import _selftest_sampling
from ..text.classify import parse_live_comments



def _selftest() -> int:
    if not _selftest_rules():
        return 1
    if not _selftest_roi():
        return 1
    if not _selftest_panels():
        return 1
    if not _selftest_emoji():
        return 1
    if not _selftest_dedup():
        return 1
    if not _selftest_sampling():
        return 1
    got = parse_live_comments(EXAMPLE)
    if got == EXPECTED:
        print("[selftest] 内置示例与期望输出逐条一致 ✔")
        return 0
    print("[selftest] 不一致：", file=sys.stderr)
    for i in range(max(len(got), len(EXPECTED))):
        g = got[i] if i < len(got) else None
        e = EXPECTED[i] if i < len(EXPECTED) else None
        print(f"  {i}: {'OK ' if g == e else 'DIFF'} got={g} want={e}", file=sys.stderr)
    return 1
