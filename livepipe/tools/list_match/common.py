"""撞库的纯函数：名单名归一化、相似度、平台标签、账号名解析。

「撞库」= 拿**活动方给的名单**去撞 `export` / `collect` 产出的交付账号：
每个平台各撞各的，输出「名单上哪个人被拍到了、匹配度多少、图在哪个目录」。

这里只放**不读盘、不依赖三方库**的东西（自检覆盖）。
读 xlsx / 交付树在 :mod:`.sources`，匹配编排在 :mod:`.matching`。
"""

from __future__ import annotations

import difflib
import hashlib
import re
import unicodedata

# --------------------------------------------------------------------------- #
# 平台标签
# --------------------------------------------------------------------------- #
#: 表头 / 目录名里的平台写法 → 交付树目录名。
#: 与 ``tools/export_user_images/common.py`` 的 ``PLATFORM_CN`` 同一套中文名；
#: 这里额外接受表头常见写法（``抖音号`` / ``微信视频号`` / 英文档案名）。
PLATFORM_LABELS = {
    "抖音": "抖音",
    "抖音号": "抖音",
    "douyin": "抖音",
    "douyin_app": "抖音",
    "视频号": "视频号",
    "微信视频号": "视频号",
    "wechat": "视频号",
    "wechat_app": "视频号",
    "weixin": "视频号",
    "快手": "快手",
    "快手号": "快手",
    "kuaishou": "快手",
    "kuaishou_app": "快手",
    "微博": "微博",
    "weibo": "微博",
    "小红书": "小红书",
    "xiaohongshu": "小红书",
    "b站": "B站",
    "bilibili": "B站",
}

#: 平台中文名的稳定顺序（表里没出现的平台排后面）。
PLATFORM_ORDER = ("抖音", "视频号", "快手", "微博", "小红书", "B站")

_WS = re.compile(r"\s+")
_LEVEL_SUFFIX = re.compile(r"◆\s*\d*\s*$")
_TRAILING_COUNT = re.compile(r"^(?P<name>.+)-(?P<count>\d+)$")
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

#: ``partial`` 指标允许「短名被长名包含」的最短长度。
#: 单字名单只走精确 / 整体相似度 —— 否则「风」会撞上所有带「风」的账号。
MIN_PARTIAL_LEN = 2

#: 导出标记图 / 统计截图数时认的图片后缀（交付树里实测只有 .jpg；别的格式顺手带上）。
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def platform_label(text: object) -> str | None:
    """表头 / 目录名 → 平台中文名；不认识则 ``None``（区分大小写不敏感）。"""
    key = unicodedata.normalize("NFKC", str(text or "")).strip().casefold()
    return PLATFORM_LABELS.get(key)


def platform_sort_key(label: str) -> tuple[int, str]:
    """平台排序：先按常见平台顺序，未知平台按名字。"""
    try:
        return (PLATFORM_ORDER.index(label), label)
    except ValueError:
        return (len(PLATFORM_ORDER), label)


# --------------------------------------------------------------------------- #
# 名字归一化
# --------------------------------------------------------------------------- #

def normalize_name(name: object) -> str:
    """名单名 / 账号名 → 用来比较的规范串。

    - NFKC：全角字母数字 → 半角（``Ｗ`` → ``W``）；``：`` → ``:``
    - casefold：英文大小写不敏感
    - 去掉所有空白（含全角空格、换行）
    - 去掉零宽字符 / 变体选择符（OCR 常在昵称里带 ``\\u200b`` / ``️``）

    标点、数字、emoji **保留** —— ``岁月如初1213`` 的 1213、``西柚。`` 的句号都可能
    是名字的一部分；表情由 :func:`partial_similarity` 处理（名单带 🍅、账号不带也能撞上）。
    """
    s = unicodedata.normalize("NFKC", str(name or "")).casefold()
    out: list[str] = []
    for ch in s:
        if ch.isspace():
            continue
        if unicodedata.category(ch) in ("Cf", "Mn"):
            continue
        out.append(ch)
    return "".join(out)


def strip_level(text: object) -> str:
    """``用***◆16`` → ``用***``（``正面账号.txt`` 里的账号名带等级标，名单不会带）。"""
    return _LEVEL_SUFFIX.sub("", str(text or "")).strip()


def display_account(username: object, level: object = "") -> str:
    """给人看的账号名：``用***◆16``；没等级就是昵称本身（与 ``account_id.display`` 同形）。"""
    name = str(username or "")
    lv = strip_level(level)
    return f"{name}◆{lv}" if lv else name


# --------------------------------------------------------------------------- #
# 目录名 / 单元格
# --------------------------------------------------------------------------- #

def dir_username(dirname: object) -> str:
    """交付目录名 → 账号名：``6回头不是彼岸呀-4`` → ``6回头不是彼岸呀``。

    只剥**末尾的 ``-<条数>``**（``W-8`` → ``W``、``-风--22`` → ``-风-``）；
    名字里本来就有的连字符不动（``a-b`` 没有数字尾巴 ⇒ 原样）。
    """
    s = str(dirname or "").strip()
    m = _TRAILING_COUNT.match(s)
    return m.group("name") if m else s


def dir_count(dirname: object) -> int | None:
    """交付目录名末尾的条数：``某账号-12`` → ``12``；没有则 ``None``。"""
    m = _TRAILING_COUNT.match(str(dirname or "").strip())
    return int(m.group("count")) if m else None


def is_number(value: object) -> bool:
    """像不像序号：``1`` / ``1.0`` / ``"0176"`` 都算（只用于判断名单的列布局）。"""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    s = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not s:
        return False
    s = s.replace(",", "").replace(".", "", 1)
    return s.isdigit()


def safe_dirname(name: object, maxlen: int = 60) -> str:
    """名单名 → 能当目录名的字符串（``标记图/`` 归档用；与交付树的 sanitize 同口径）。"""
    s = _WS.sub(" ", str(name or "")).strip()
    s = _BAD.sub("_", s).strip(" .")
    if len(s) > maxlen:
        digest = hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]
        s = f"{s[:maxlen]}-{digest}"
    return s or "未命名"


# --------------------------------------------------------------------------- #
# 相似度
# --------------------------------------------------------------------------- #

def similarity(a: object, b: object) -> float:
    """整体相似度：``difflib.SequenceMatcher.ratio()``（0~1）。"""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()


#: 算「名字里的一类正文」用的码位区间：CJK 汉字 / 假名 / 韩文。
_CJK_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF),
               (0x3040, 0x30FF), (0xAC00, 0xD7AF), (0x20000, 0x2FA1F))


def _has_cjk(s: str) -> bool:
    """串里有没有「像名字正文」的字（汉字/假名/韩文）；数字、字母、符号、emoji 不算。"""
    return any(any(a <= ord(ch) <= b for a, b in _CJK_RANGES) for ch in s)


def _partial_norm(na: str, nb: str) -> float:
    """截断容忍分数（已归一化的名串）：前后缀截断给满，中间片段逐窗口比。

    交付昵称最常见的两种失真：**少尾巴**（``期待``→``期待sj``、``喜乐哥``→``喜乐哥554``）
    和**多一个徽章/编号前缀**（``希儿``→``★1希儿``、``一路顺风``→``8一路顺风``）。
    - 短名是长名的**前缀或后缀**：短名 ≥3 字，或多余那段里没有名字正文（纯数字/符号/
      字母，如 ``章衡``→``章衡9116``、``期待``→``期待sj``）⇒ 给 1.0；
      多余那段是**汉字**就只按整体 ratio 算 —— 否则 ``天后`` 会撞上 ``明天后天``。
    - 短名夹在长名**中间**：只对 ≥3 字的短名做逐窗口比（2 个字碰巧出现的概率太高）。
    """
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if len(na) > len(nb):
        na, nb = nb, na
    r = difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()
    if len(na) < MIN_PARTIAL_LEN:
        return r
    if len(na) == len(nb):
        return r
    if nb.startswith(na) or nb.endswith(na):
        residue = nb[len(na):] if nb.startswith(na) else nb[:-len(na)]
        if len(na) >= 3 or not _has_cjk(residue):
            return 1.0
        return r
    if len(na) < 3:
        return r
    m = len(na)
    best = r
    for i in range(len(nb) - m + 1):
        w = difflib.SequenceMatcher(None, na, nb[i:i + m], autojunk=False).ratio()
        if w > best:
            best = w
            if best >= 1.0:
                break
    return best


def partial_similarity(a: object, b: object) -> float:
    """截断容忍相似度（见 :func:`_partial_norm`）：整体 ratio 与截断窗口比取大。"""
    return _partial_norm(normalize_name(a), normalize_name(b))


def score_norm(na: str, nb: str, metric: str = "partial") -> float:
    """两个**已归一化**的名串的匹配度（``matching`` 的内层循环用它省重复归一）。"""
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if metric == "ratio":
        return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()
    return _partial_norm(na, nb)


def match_score(a: object, b: object, metric: str = "partial") -> float:
    """名单名 × 账号名 → 匹配度（0~1）；``metric`` 见 :func:`score_norm`。"""
    return score_norm(normalize_name(a), normalize_name(b), metric)
