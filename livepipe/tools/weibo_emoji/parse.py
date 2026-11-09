"""livepipe.tools.weibo_emoji.parse"""

from __future__ import annotations

from pathlib import Path
import html
import json
import re

from .common import _log
from .source import PARSE_ONLY_GROUP
from .source import _FACE_RE
from .source import _GROUP_RE



def parse_panel(text: str) -> list[dict]:
    """面板 DOM → ``[{"index","group","key","name"}, …]``（按面板出现顺序）。

    这就是这张表**全部的人手输入**：分组、key、中文名。图不在里面。
    """
    text = html.unescape(text)                     # 粘贴来的 DOM 里 ``&`` 是 ``&amp;``
    marks = [(m.start(), m.group(1)) for m in _GROUP_RE.finditer(text)]
    if not marks:
        raise SystemExit(
            "没找到 ``face-wrap <分组>`` —— 这不是微博 PC 端表情面板的 DOM？\n"
            "  面板长这样：<div class=\"... face-wrap default\">"
            "<span class=\"face_wrapper face face-cnxyhy\">航天员</span></div>")
    rows: list[dict] = []
    seen: set[str] = set()
    for m in _FACE_RE.finditer(text):
        name = m.group(2).strip()
        key = m.group(1)
        if not name:
            continue
        if key in seen:                            # 面板里同一条表情出现两次就只留一次
            _log(f"[warn] 面板里 key={key} 重复出现，只留第一条")
            continue
        seen.add(key)
        grp = marks[0][1]
        for pos, g in marks:                       # 取「最近的那个分组标记」
            if pos < m.start():
                grp = g
            else:
                break
        rows.append({"group": grp, "key": key, "name": name})
    if not rows:
        raise SystemExit("解析出 0 条表情 —— DOM 里有 face-wrap，但没有 "
                         "``class=\"... face_wrapper ... face-<key>\">中文名</span>``")
    for i, r in enumerate(rows, start=1):
        r["index"] = i
    return rows


def load_map(path: Path) -> dict[str, dict]:
    """读 ``key → URL`` 的解析表。值可以是字符串，也可以是 ``{url, confidence, how}``。"""
    if not path.exists():
        raise SystemExit(
            f"解析表不存在：{path}\n"
            f"  它是 key → 图片 URL 的映射（以前放在 out/weibo_emoji/panel_map.json，不在 git 里）。\n"
            f"  没有它就只能拿到「面板上有什么」，拿不到任何图；\n"
            f"  不想手工攒表就用自动模式：--config（见段首）。")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
        raw = raw["entries"]                        # 容一手 {"entries": {...}} 的包法
    out: dict[str, dict] = {}
    for k, v in raw.items():
        if isinstance(v, str):
            out[k] = {"url": v, "confidence": "unknown", "how": "map"}
        elif isinstance(v, dict) and v.get("url"):
            out[k] = {"url": v["url"],
                      "confidence": v.get("confidence", "unknown"),
                      "how": v.get("how", "map")}
        else:
            _log(f"[warn] 解析表里 {k!r} 没有可用的 url，跳过")
    return out


def _config_entries(doc: dict):
    """config JSON → ``(group, phrase, url)``，兼容官方新形态与 2021 ``data.json``。"""
    root = doc.get("data") if isinstance(doc.get("data"), dict) else doc
    em = root.get("emoticon") if isinstance(root, dict) else None
    if not isinstance(em, dict):
        if isinstance(doc, dict) and doc.get("ok") == -100:
            raise SystemExit(
                "官方 config 接口要求登录态（ok:-100）。\n"
                "  用 --cookie 'SUB=…; SUBP=…'（或环境变量 WEIBO_COOKIE），\n"
                "  或改用公开镜像：--config（不带值）。")
        raise SystemExit("config 里没有 emoticon 段 —— 这不是 /ajax/statuses/config 的响应？")
    # 新形态：emoticon.ZH_CN = {"默认": [{phrase, url}, …], …}
    lang = em.get("ZH_CN") or next(
        (v for k, v in em.items() if str(k).startswith("ZH") and isinstance(v, dict)), None)
    if isinstance(lang, dict) and all(isinstance(v, list) for v in lang.values()):
        for group, arr in lang.items():
            for e in arr:
                if isinstance(e, dict):
                    yield group, e.get("phrase") or e.get("value"), e.get("url")
        return
    # 2021 data.json：usual = {norm: […], hot: […]}；more = {组: […]}; brand.norm = {组: […]}
    for top, val in em.items():
        if not isinstance(val, dict):
            continue
        for sub, arr in val.items():
            if isinstance(arr, list):
                for e in arr:
                    if isinstance(e, dict):
                        yield sub, e.get("phrase") or e.get("value"), e.get("url")
            elif isinstance(arr, dict):
                for sub2, arr2 in arr.items():
                    if isinstance(arr2, list):
                        for e in arr2:
                            if isinstance(e, dict):
                                yield sub2, e.get("phrase") or e.get("value"), e.get("url")


def load_config(text: str, origin: str, include_parse_only: bool = False,
                ) -> tuple[list[dict], dict[str, dict]]:
    """config JSON → (面板行, ``key → URL`` 表)，形态与 ``parse_panel`` + ``load_map`` 一致。

    ``id`` 用 CDN stem：config 里**没有**面板的 ``face-<key>``。同名表情（多是「默认」与
    「仅解析」各一条）只留先出现的；默认丢掉「仅解析」分组（它不在面板上）。
    """
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit(f"config 不是合法 JSON：{e}") from e

    cands: list[tuple[int, int, str, str, str]] = []   # (parse_only, order, group, name, url)
    n_raw = n_parse = 0
    for order, (group, phrase, url) in enumerate(_config_entries(doc)):
        n_raw += 1
        if not phrase or not url or not str(url).startswith("http"):
            continue
        phrase = str(phrase).strip()
        m = re.match(r"^\[(.*)\]$", phrase)
        name = (m.group(1) if m else phrase).strip()
        if not name:
            continue
        parse_only = group == PARSE_ONLY_GROUP
        if parse_only:
            n_parse += 1
            if not include_parse_only:
                continue
        cands.append((1 if parse_only else 0, order, group, name, str(url)))

    # 面板项优先于「仅解析」；同优先级保持 config 原顺序。
    cands.sort(key=lambda t: (t[0], t[1]))
    rows: list[dict] = []
    mp: dict[str, dict] = {}
    seen_name: set[str] = set()
    n_dup = 0
    for _, _, group, name, url in cands:
        if name in seen_name:
            n_dup += 1
            continue
        seen_name.add(name)
        cdn = url.rsplit("/", 1)[-1]
        stem = re.sub(r"_(thumb|org)\.(png|gif)$", "", cdn)
        key = stem or re.sub(r"[^A-Za-z0-9_-]+", "_", name)
        while key in mp:                           # 理论上不会撞；撞了就加后缀，别覆盖
            key += "_"
        rows.append({"group": group, "key": key, "name": name, "token": f"[{name}]"})
        mp[key] = {"url": url, "confidence": "high", "how": f"config:{origin}"}

    if not rows:
        raise SystemExit("config 里解析出 0 条可用表情 —— 确认是 /ajax/statuses/config 的响应")
    for i, r in enumerate(rows, start=1):
        r["index"] = i
    _log(f"[config] {origin}：{n_raw} 条 → {len(rows)} 条"
         + (f"（跳过仅解析 {n_parse} 条）" if n_parse and not include_parse_only else "")
         + (f"（同名去重 {n_dup} 条）" if n_dup else ""))
    return rows, mp
