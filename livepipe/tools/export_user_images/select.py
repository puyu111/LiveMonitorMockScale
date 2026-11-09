"""livepipe.tools.export_user_images.select"""

from __future__ import annotations

from .common import pick_comments
from .common import platform_of



def select(users: list[dict], panel_platform: dict[str, str], labels: set[str],
           min_positive: int, scope: str = "platform") -> dict[str, list[tuple[dict, list[dict]]]]:
    """用户 × 标签 → 按平台分组的待出图清单 ``{平台: [(用户记录, 评论列表), …]}``。

    ``scope="platform"``（默认）**在每个平台内各自数够 ``min_positive`` 条** ——
    交付是发到平台上的，一个账号在抖音 2 条、视频号 2 条，两个平台都不该被算成
    「正面账号 ≥3」；``scope="global"`` 则是「整段视频够数就进每个平台」（旧口径）。
    """
    out: dict[str, list[tuple[dict, list[dict]]]] = {}
    for rec in users:
        cs = pick_comments(rec, labels)
        if not cs:
            continue
        by_p: dict[str, list[dict]] = {}
        for c in cs:
            by_p.setdefault(platform_of(c, panel_platform), []).append(c)
        if scope == "global":
            if len(cs) < min_positive:
                continue
            for p, lst in by_p.items():
                out.setdefault(p, []).append((rec, lst))
        else:
            for p, lst in by_p.items():
                if len(lst) >= min_positive:
                    out.setdefault(p, []).append((rec, lst))
    return out
