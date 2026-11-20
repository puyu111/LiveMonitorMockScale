#!/usr/bin/env python
"""``livepipe/verify_run.py``（实现在 ``tools/verify_run``）。"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "livepipe"

from .tools.verify_run import (
    Check,
    Report,
    effective_steps,
    main,
    make_parser,
    selftest,
    step_enabled,
    verify,
)


if __name__ == "__main__":
    raise SystemExit(main())
