"""livepipe.tools.user_filter.api"""

from __future__ import annotations

import json
import urllib.error
import urllib.request



# --------------------------------------------------------------------------- #
# 大模型判定（OpenAI 兼容 /chat/completions，只用标准库）
# --------------------------------------------------------------------------- #

def _post(base_url: str, api_key: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _extract_json(text: str) -> dict | None:
    """从模型回复里抠出那个 JSON 对象（容忍 ``` 包裹和前后废话）。"""
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(text[i:j + 1])
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None
