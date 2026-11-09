"""livepipe.parsing.common.text — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import re



def norm_text(s: str) -> str:
    """归一化文本用于跨帧去重：去掉空白与常见 OCR 噪声字符。"""
    s = re.sub(r"[\s​]+", "", s)
    return s.strip("：:·。.,，")
