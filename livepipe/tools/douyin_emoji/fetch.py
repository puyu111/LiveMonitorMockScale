"""livepipe.tools.douyin_emoji.fetch"""

from __future__ import annotations

from pathlib import Path
import html
import json
import re
import time
import urllib.error
import urllib.request

from .source import _UA
from .source import _URL_RE



def extract_urls(text: str) -> list[dict]:
    """按出现顺序抽出 (hash, url)，同 hash 只留首次，记录出现次数。

    返回 [{"hash", "url", "count", "first_pos"}, ...]。
    """
    # 先把 HTML 实体还原：粘贴来的 DOM 里 `&` 是 `&amp;`。
    text = html.unescape(text)
    seen: dict[str, dict] = {}
    order: list[str] = []
    for m in _URL_RE.finditer(text):
        h, url = m.group(1), m.group(0)
        if h not in seen:
            seen[h] = {"hash": h, "url": url, "count": 0, "first_pos": m.start()}
            order.append(h)
        seen[h]["count"] += 1
    return [seen[h] for h in order]


def fetch_api(api: str, dest: Path) -> dict:
    """取表情表接口 JSON。``api`` 可以是 URL，也可以是本地已存的 JSON 路径。"""
    if api.startswith("http"):
        req = urllib.request.Request(api, headers={
            "User-Agent": _UA, "Referer": "https://www.douyin.com/"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read()
        dest.write_bytes(raw)
        return json.loads(raw)
    return json.loads(Path(api).read_text(encoding="utf-8"))


def records_from_api(payload: dict) -> list[dict]:
    """把接口返回摊平成 [{idx, token, name, slug, hash, uri, urls}, ...]。

    ``display_name`` 是官方名，形如 ``[微笑]``；``name`` 去掉方括号。
    ``hide`` 非 0 的条目是面板上不展示的，仍保留但打标记 —— 别静默丢，
    否则「表里少了几个」将来无从对账。
    """
    lst = payload.get("emoji_list") or []
    if not lst:
        raise SystemExit(f"接口没返回 emoji_list（status_code={payload.get('status_code')}）")
    out = []
    for i, e in enumerate(lst, start=1):
        eu = e.get("emoji_url") or {}
        uri = eu.get("uri") or ""
        m = re.search(r"([0-9a-f]{32})", uri)
        token = (e.get("display_name") or "").strip()
        out.append({
            "idx": i,
            "token": token,
            "name": token.strip("[]"),
            "slug": (e.get("origin_uri") or "").removesuffix(".png"),
            "hash": m.group(1) if m else None,
            "uri": uri,
            "urls": eu.get("url_list") or [],
            "hide": e.get("hide", 0),
        })
    return out


def download_any(urls: list[str], dest: Path, retries: int = 2) -> tuple[bool, str]:
    """依次尝试候选 URL（p3 / p9 两个镜像签名不同，互为备份）。"""
    last = "无候选 URL"
    for u in urls:
        ok, note = download(u, dest, retries=retries)
        if ok:
            return True, note
        last = note
    return False, last


def download(url: str, dest: Path, retries: int = 3) -> tuple[bool, str]:
    """下载一张表情图。返回 (成功, 说明)。"""
    if dest.exists() and dest.stat().st_size > 0:
        return True, "cached"
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Referer": "https://www.douyin.com/",
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
    })
    last = ""
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
                ctype = resp.headers.get("Content-Type", "")
            if not data:
                last = "空响应"
            elif not ctype.startswith("image/"):
                last = f"非图片 Content-Type={ctype}"
            else:
                dest.write_bytes(data)
                return True, f"{ctype} {len(data)}B"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
        except Exception as e:                      # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(0.6 * (i + 1))
    return False, last
