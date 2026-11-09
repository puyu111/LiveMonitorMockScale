"""livepipe.parsing.text.cleaning — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import re

from ..text.patterns import _TAIL_FRAGMENT_MAX
from ..text.patterns import _TAIL_FRAGMENT_RE


# 纯数字行（可以带空白）⇒ 丢弃。
_PURE_NUM_RE = re.compile(r"^[\s\d]+$")

# 「算内容」的字符：\w 覆盖汉字/字母/数字（Python 的 \w 默认匹配 Unicode 字母，汉字算），
# 后面几段是 emoji 常用区（☀-➿ · ⬀-⯿ · ️变体选择符 · 🤀-🫿 等）。
_CONTENT_RE = re.compile(r"[\w☀-➿⬀-⯿️❤\U0001F000-\U0001FAFF]")

# OCR 噪声：零宽字符。
_ZW_TRANS = dict.fromkeys(map(ord, "​‌‍﻿"), None)


def _clean(s: str) -> str:
    return s.translate(_ZW_TRANS).strip()


def _is_junk(s: str) -> bool:
    """整行没有可当内容的东西（纯数字 / 纯标点 / 空白）⇒ True。emoji 不算 junk。"""
    if not s:
        return True
    if _PURE_NUM_RE.match(s):
        return True
    return not _CONTENT_RE.search(s)


def _is_tail_fragment(text: str) -> bool:
    """「短残句」的形状判据（≤ ``_TAIL_FRAGMENT_MAX`` 字 + 以句末标点结尾）。

    **只靠它不成立** —— 真评论的折行残片长这样（实测 '看。' '造。' '得真好！'），
    所以调用方（``parse_live_rows``）必须再叠一条「紧跟在一行系统行之后」才算数。
    """
    t = _clean(text)
    return bool(t) and len(t) <= _TAIL_FRAGMENT_MAX and _TAIL_FRAGMENT_RE.search(t) is not None
