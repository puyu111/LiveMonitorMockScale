"""livepipe.tools.wechat_emoji.records"""

from __future__ import annotations

from pathlib import Path

from .common import _log
from .source import FNAME_RE



def records_from_files(img_dir: Path) -> list[dict]:
    """``<NNN_名称>.png`` 文件名 → 记录。**没有人工标注这一步** —— 名字就在文件名里。

    顺带核一遍图确实是 128×128 带 alpha：``load_emoji_gallery`` 要把 alpha 合成到聊天
    背景色上做匹配，丢了 alpha（比如被谁转成了白底 jpg）匹配就会整体偏 —— 这是必须当场
    报出来的错，不然表现成「分数莫名低了一截」，很难往回查。
    """
    from PIL import Image

    recs: list[dict] = []
    for p in sorted(img_dir.glob("*.png")):
        m = FNAME_RE.match(p.name)
        if not m:
            continue
        idx, name = int(m.group(1)), m.group(2)
        with Image.open(p) as im:
            w, h = im.size
            mode = im.mode
        if mode not in ("RGBA", "LA", "P"):
            _log(f"[warn] {p.name} 没有 alpha（mode={mode}）—— 合成背景会不对")
        recs.append({
            "id": m.group(1),                 # "080" —— 身份（微信侧没有 hash）
            "index": idx,                     # 80
            "token": f"[{name}]",             # 官方 token 形态，与抖音表一致
            "name": name,
            "img": p.name,                    # 相对 <out>/img/ 的文件名
            "size": [w, h],
            "mode": mode,
            "bytes": p.stat().st_size,
        })
    recs.sort(key=lambda r: r["index"])
    return recs
