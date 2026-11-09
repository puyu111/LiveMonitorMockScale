"""livepipe.parsing.selftest.roi — ROI 与「socket 必须相邻」守卫的断言。"""

from __future__ import annotations

import sys

from .roi_alias import _add_alias_cases
from .roi_cards import _add_card_cases
from .roi_colon import _add_colon_cases
from .roi_grouping import _add_grouping_cases
from .roi_slots import _add_slot_cases
from .roi_wrap import _add_wrap_cases


def _selftest_roi() -> bool:
    """ROI 与「socket 必须相邻」守卫的断言（合成数据，不依赖真实录屏）。"""
    cases = []
    _add_colon_cases(cases)
    _add_card_cases(cases)
    _add_alias_cases(cases)
    _add_grouping_cases(cases)
    _add_slot_cases(cases)
    _add_wrap_cases(cases)

    bad = [name for name, passed in cases if not passed]
    if bad:
        print(f"[selftest] ROI 断言失败：{bad}", file=sys.stderr)
        return False
    print(f"[selftest] ROI/相邻守卫 {len(cases)} 项断言通过 ✔")
    return True
