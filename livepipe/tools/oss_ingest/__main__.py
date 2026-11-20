"""``python -m livepipe.tools.oss_ingest`` 的模块入口。"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
