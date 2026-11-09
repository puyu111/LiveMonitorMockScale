"""livepipe.tools.user_filter.cache"""

from __future__ import annotations

from pathlib import Path
import hashlib
import json



# --------------------------------------------------------------------------- #
# 主体
# --------------------------------------------------------------------------- #

def _key(content: str, emoji: str) -> str:
    return hashlib.sha1(f"{content}\x1f{emoji}".encode("utf-8")).hexdigest()


def _save_cache(cache_path: Path, cache: dict[str, str]) -> None:
    """把判定缓存**原子**写盘（先写 ``.tmp`` 再 ``replace``）—— 正常收尾和「每判完一批」都调它，
    于是中断/报错/被 Ctrl-C 时，已经判过的那些也留在盘上，重跑直接跳过（断点续跑）。"""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_name(cache_path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for h, lab in cache.items():
            f.write(json.dumps({"k": h, "label": lab}, ensure_ascii=False) + "\n")
    tmp.replace(cache_path)
