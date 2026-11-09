"""livepipe.tools.wechat_emoji.fetch"""

from __future__ import annotations

from pathlib import Path
import tarfile
import time
import urllib.error
import urllib.request

from .common import _log
from .common import _ua
from .source import FNAME_RE
from .source import SUBDIR
from .source import TARBALL



def fetch_tarball(dest: Path, retries: int = 2) -> Path:
    """下整仓 tarball（一次请求拿全部 109 张 —— 逐张 raw 在这台机器上会超时）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for k in range(retries + 1):
        try:
            with urllib.request.urlopen(_ua(TARBALL), timeout=120) as r:
                data = r.read()
            dest.write_bytes(data)
            _log(f"[fetch] {TARBALL}\n        -> {dest}（{len(data)/1e6:.1f}MB）")
            return dest
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last = e
            _log(f"[fetch] 第 {k+1} 次失败：{e}")
            time.sleep(2 * (k + 1))
    raise SystemExit(f"下 tarball 失败：{last}\n也可以手工下好再用 --src 指向目录。")


def extract_pngs(tar_path: Path, out_img: Path) -> list[str]:
    """从 tarball 里只解 ``SUBDIR/*.png`` → ``out_img/``（已存在的跳过，不改名）。

    **文件名保持原样**是硬要求：``001_微笑.png`` 里就带着 token，改名等于把表弄丢。
    """
    out_img.mkdir(parents=True, exist_ok=True)
    got: list[str] = []
    with tarfile.open(tar_path, "r:gz") as tf:
        for m in tf.getmembers():
            if not m.isfile() or not m.name.endswith(".png"):
                continue
            parts = m.name.split("/")
            # tarball 里的成员名形如 ``wechat-emoji-main/web.wechat.com/uncompressed/001_微笑.png``
            if len(parts) < 3 or "/".join(parts[-3:-1]) != SUBDIR:
                continue
            name = parts[-1]
            if not FNAME_RE.match(name):
                continue
            dst = out_img / name
            if not dst.exists():
                f = tf.extractfile(m)
                if f is None:
                    continue
                dst.write_bytes(f.read())
            got.append(name)
    got.sort()
    _log(f"[extract] {len(got)} 张 -> {out_img}")
    return got


def copy_src(src: Path, out_img: Path) -> list[str]:
    """``--src``：直接用手上已有的目录（离线复现建表，不必再联网）。"""
    out_img.mkdir(parents=True, exist_ok=True)
    got = []
    for p in sorted(src.glob("*.png")):
        if not FNAME_RE.match(p.name):
            _log(f"[src] 跳过不合规文件名：{p.name}（要 NNN_名称.png）")
            continue
        dst = out_img / p.name
        if not dst.exists() or dst.stat().st_size != p.stat().st_size:
            dst.write_bytes(p.read_bytes())
        got.append(p.name)
    _log(f"[src] {len(got)} 张 -> {out_img}")
    return got
