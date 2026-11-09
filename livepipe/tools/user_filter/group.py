"""livepipe.tools.user_filter.group"""

from __future__ import annotations

from ... import account_id as aid

from .comments import _excluded_by



def group_accounts(comments: list[dict], keywords: list[str], split_panels: set[str],
                   masked_panels: set[str] | frozenset[str] = frozenset(),
                   ) -> tuple[dict[tuple[str, str], list[dict]], int, list[dict]]:
    """评论 → ``{(昵称, 等级标): [评论…]}``；再给出（匿名条数, 被排除的账号）。

    **账号身份只在这一处实现**（判据本身在 :mod:`livepipe.account_id`）—— 跑管线、量名单、
    写单测都走它，免得「量的口径」和「跑的口径」各是一份代码。

    ``masked_panels``：这些面板走 ``nickname_level_if_masked`` —— 打码昵称
    （`爱**`）按 (昵称, 等级标) 认人，不打码昵称（`微风许甜`）仍只看昵称。
    """
    by_user: dict[tuple[str, str], list[dict]] = {}
    anon = 0
    excluded: list[dict] = []
    for c in comments:
        name = (c.get("username") or "").strip()
        if not name:
            anon += 1
            continue
        hit = _excluded_by(name, keywords)
        if hit:
            excluded.append({"username": name, "keyword": hit})
            continue
        lv = aid.level_of(c, split_panels, masked_panels)
        by_user.setdefault(aid.account_key(name, lv), []).append(c)
    return by_user, anon, excluded
