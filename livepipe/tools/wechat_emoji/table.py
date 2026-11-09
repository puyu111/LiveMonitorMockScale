"""livepipe.tools.wechat_emoji.table"""

from __future__ import annotations

import time

from .source import REPO
from .source import SOURCE
from .source import SOURCE_DESC



def build_table(recs: list[dict], thresholds: dict) -> dict:
    """记录 → 表。四个索引：``byId`` / ``byIndex`` / ``byToken`` / ``byName``。

    ``byId`` 是 ``load_emoji_gallery`` 的主索引（与抖音表的 ``byHash`` 同位）；
    另外三个是给人查的（下游 ``--emoji-inventory`` 之类也复用）。
    """
    return {
        "source": SOURCE,
        "source_desc": SOURCE_DESC,
        "captured": time.strftime("%Y-%m-%d"),
        "repo": f"https://github.com/{REPO}",
        "img": {"dir": "img"},                 # 文件名逐条写在记录的 img 字段里
        "n_total": len(recs),
        "n_indexed": len(recs),
        "thresholds": dict(thresholds),
        "byId": {r["id"]: r for r in recs},
        "byIndex": {str(r["index"]): r for r in recs},
        "byToken": {r["token"]: r for r in recs},
        "byName": {r["name"]: r for r in recs},
    }
