"""livepipe.tools.kuaishou_emoji.fetch — 抓页面 / 读本地源 / 下载表情图。"""

from __future__ import annotations

from pathlib import Path
import json
import time
import urllib.error
import urllib.request

from .source import PANEL_KEY
from .source import PAGE_URL
from .source import _UA
from .source import _panel_from_obj
from .source import extract_panel
from .source import normalize_url


def fetch_page(url: str = PAGE_URL, dest: Path | None = None) -> str:
    """抓 live.kuaishou.com 首页 HTML；``dest`` 给了就存一份（便于离线复跑）。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Referer": "https://live.kuaishou.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read()
    if dest is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
    return raw.decode("utf-8", errors="replace")


def load_panel(src: str | None) -> tuple[dict[str, str], str]:
    """取 ``{token: url}`` 与来源说明。

    ``src`` 可以是：
      * ``None`` ⇒ 现抓 ``https://live.kuaishou.com``；
      * 页面 HTML 文件（``*.html`` / ``*.htm``）⇒ 从源码抽；
      * JSON 文件 ⇒ 裸映射、``{"emojiPanel": {...}}`` 或整页配置对象都认。
    """
    if not src:
        return extract_panel(fetch_page(PAGE_URL)), PAGE_URL
    p = Path(src)
    if not p.is_file():
        raise SystemExit(f"--src 不存在：{p}")
    raw = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix.lower() in (".json",):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--src 不是合法 JSON：{p}（{exc}）") from exc
        panel = _panel_from_obj(obj)
        if panel is None:
            raise SystemExit(
                f"--src 里没有 {PANEL_KEY} / emojiPanel / token→URL 映射：{p}")
        return panel, str(p)
    return extract_panel(raw), str(p)


def records_from_panel(panel: dict[str, str]) -> list[dict]:
    """token→URL → 逐条记录（id 是 ``001_666`` 这种稳定键，图名也用它）。"""
    out: list[dict] = []
    for i, (token, url) in enumerate(panel.items(), start=1):
        name = token.strip("[]")
        rid = f"{i:03d}_{name}"
        out.append({
            "idx": i,
            "id": rid,
            "token": token,
            "name": name,
            "url": normalize_url(url),
            "img": f"{rid}.png",
        })
    return out


def download(url: str, dest: Path, retries: int = 3) -> tuple[bool, str]:
    """下载一张表情图；已存在且非空 ⇒ 视为缓存命中。"""
    if dest.exists() and dest.stat().st_size > 0:
        return True, "cached"
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Referer": "https://live.kuaishou.com/",
        "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
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
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                return True, f"{ctype} {len(data)}B"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
        except Exception as e:                      # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(0.6 * (i + 1))
    return False, last
