"""livepipe.tools.user_filter.comments"""

from __future__ import annotations

import json



def load_comments(path: str) -> list[dict]:
    cs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cs.append(json.loads(line))
    return cs


def _excluded_by(username: str, keywords: list[str]) -> str | None:
    """命中哪个排除词（不区分大小写）；没命中返回 None。"""
    low = username.lower()
    for k in keywords:
        if k.lower() in low:
            return k
    return None


def split_panels_of(spec: str | None) -> set[str]:
    """``--identity-panels`` 的取值（逗号分隔）→ 要按 (昵称, 等级标) 认人的面板名集合。"""
    return {s.strip() for s in str(spec or "").split(",") if s.strip()}
