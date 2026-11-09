"""livepipe.tools.weibo_emoji.fetch"""

from __future__ import annotations

from pathlib import Path
import hashlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from .common import _log
from .source import HOSTS
from .source import REFERER
from .source import UA



def cdn_dir(stem: str) -> str:
    """CDN 目录 = ``md5(stem)[-1]`` 的两位十六进制（``stem`` 不含 ``_org``/``_thumb`` 与扩展名）。

    这条规则是这张表能建起来的根本：知道文件名就能**一次请求**验证它存不存在，
    不用在 256 个目录里试。262/262 实测吻合。
    """
    return format(hashlib.md5(stem.encode()).digest()[-1], "02x")


def _read_text(src: str, cookie: str | None = None, timeout: int = 60) -> str:
    """读 config：``-`` = stdin，``http(s)://`` = 下载，其余当文件路径。"""
    if src == "-":
        return sys.stdin.read()
    if re.match(r"^https?://", src):
        headers = {"User-Agent": UA, "Referer": REFERER,
                   "Accept": "application/json, text/plain, */*",
                   "X-Requested-With": "XMLHttpRequest"}
        if cookie:
            headers["Cookie"] = cookie
        req = urllib.request.Request(src, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise SystemExit(f"取 config 失败：HTTP {e.code}（{src}）") from e
        except Exception as e:                     # noqa: BLE001
            raise SystemExit(f"取 config 失败：{type(e).__name__}: {e}（{src}）") from e
        return raw.decode("utf-8", "replace")
    p = Path(src)
    if not p.exists():
        raise SystemExit(f"config 不存在：{p}")
    return p.read_text(encoding="utf-8", errors="replace")


def url_variants(url: str) -> list[str]:
    """一条 URL → 候选：两个 host × 两种后缀（``_org`` 优先，``_thumb`` 兜底）。

    后缀两种**实测是同图**（见段首），留着只是因为解析表里的 URL 两种写法都有。
    """
    cands: list[str] = []
    for host in HOSTS:
        base = re.sub(r"^https?://[^/]+/", f"https://{host}/", url)
        cands.append(base)
        if base.endswith(("_org.png", "_org.gif")):
            cands.append(re.sub(r"_org\.(png|gif)$", r"_thumb.\1", base))
    seen, out = set(), []
    for u in cands:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _get(url: str, timeout: int = 20) -> bytes:
    # 个别文件名带非 ASCII（2023_TheWanderingEarthⅡ）：urllib 的请求行只吃 ASCII，
    # 不转义会抛 UnicodeEncodeError。CDN 接 URL 转义后的写法（实测两种都 200）。
    url = urllib.parse.quote(url, safe=":/?&=#%")
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": REFERER,
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        ctype = resp.headers.get("Content-Type", "")
    if not data:
        raise OSError("空响应")
    if not ctype.startswith("image/"):
        raise OSError(f"非图片 Content-Type={ctype}")
    return data


def download(src_index: dict[str, Path], url: str, cdn: str, dest: Path,
             retries: int = 2) -> tuple[bool, str, str]:
    """取一张图（先 ``--src`` 本地目录，再 4 个 CDN 候选）。

    Returns:
        ``(成功, 说明, 实际用的 URL)``；本地命中时 URL 是空串。
    """
    if dest.exists() and dest.stat().st_size > 0:
        return True, "cached", ""
    local = src_index.get(cdn)
    if local is not None:
        dest.write_bytes(local.read_bytes())
        return True, f"src {local.name}", ""
    last = "无候选"
    for u in url_variants(url):
        for k in range(retries):
            try:
                data = _get(u)
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code} {u}"
                break                              # 404 换下一个候选，别重试
            except Exception as e:                 # noqa: BLE001
                last = f"{type(e).__name__}: {e} ({u})"
                time.sleep(0.6 * (k + 1))
                continue
            dest.write_bytes(data)
            return True, f"{len(data)}B", u
    return False, last, ""


def index_src(src: Path) -> dict[str, Path]:
    """``--src`` 目录 → ``{文件名: 路径}``（按 CDN 原名索引，子目录也认）。"""
    idx: dict[str, Path] = {}
    for p in sorted(src.rglob("*")):
        if p.is_file() and p.suffix.lower() in (".png", ".gif"):
            idx.setdefault(p.name, p)
    _log(f"[src] {src} 索引到 {len(idx)} 个文件")
    return idx
