"""``livepipe.tools.verify_run`` —— 核对一次 ``livepipe run`` 的产物是否完整。

入口：``python -m livepipe verify <config.json>``（薄壳 ``livepipe/verify_run.py``）。
"""

from __future__ import annotations

from .checks import (
    effective_steps,
    step_enabled,
    verify,
)

from .cli import (
    main,
    make_parser,
)

from .common import (
    Check,
    Report,
)

from .selftest import (
    selftest,
)
