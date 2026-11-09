"""livepipe.tools.weibo_emoji.common"""

from __future__ import annotations

import sys



def _log(*a) -> None:
    print(*a, file=sys.stderr, flush=True)
