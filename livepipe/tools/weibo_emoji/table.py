"""livepipe.tools.weibo_emoji.table"""

from __future__ import annotations

from pathlib import Path
import time

from .source import CALIBRATED
from .source import CDN_DIR
from .source import HOSTS
from .source import SOURCE
from .source import SOURCE_DESC
from .source import THRESHOLDS
from .source import THRESHOLDS_NOTE



def _font(size: int, cjk: bool = False):
    from PIL import ImageFont
    cands = (["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if not cjk else []) + [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for c in cands:
        try:
            return ImageFont.truetype(c, size)
        except OSError:
            continue
    return ImageFont.load_default()


def contact_sheets(items: list[dict], out_dir: Path, per_sheet: int = 56,
                   cell: int = 128, cols: int = 8) -> list[Path]:
    """每格一张图 + 序号 + 名字 —— **只给人眼核一遍**「序号对应哪个表情」。"""
    from PIL import Image, ImageDraw

    f_num, f_name = _font(20), _font(16, cjk=True)
    written: list[Path] = []
    for s in range((len(items) + per_sheet - 1) // per_sheet):
        chunk = items[s * per_sheet:(s + 1) * per_sheet]
        rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * cell, rows * cell), (28, 28, 32))
        dr = ImageDraw.Draw(sheet)
        for k, rec in enumerate(chunk):
            cx, cy = (k % cols) * cell, (k // cols) * cell
            with Image.open(out_dir / "img" / rec["img"]) as im:
                ic = im.convert("RGBA")
                ic.thumbnail((cell - 40, cell - 40))
                sheet.paste(ic, (cx + (cell - ic.width) // 2,
                                 cy + 22 + (cell - 40 - ic.height) // 2), ic)
            dr.text((cx + 4, cy + 2), f"{rec['index']:03d}", font=f_num, fill=(255, 210, 0))
            dr.text((cx + 4, cy + cell - 20), rec["name"], font=f_name, fill=(220, 220, 220))
        p = out_dir / f"sheet_{s + 1:02d}.png"
        sheet.save(p)
        written.append(p)
    return written


def build_table(recs: list[dict], groups: dict[str, int], *,
                source: str = SOURCE, source_desc: str = SOURCE_DESC,
                extra: dict | None = None) -> dict:
    """记录 → 表。五个索引：``byId`` / ``byIndex`` / ``byToken`` / ``byName`` / ``byCdn``。

    ``byId`` 是 ``load_emoji_gallery`` 的主索引；``byCdn`` 是**录屏外**那把钥匙
    （网页评论的 ``<img src>`` 里只有 CDN 文件名，没有面板 key）。
    """
    by_cdn: dict[str, dict] = {}
    for r in recs:
        by_cdn[r["cdn"]] = r                       # 原名：2018new_aini_org.png
        by_cdn[r["stem"]] = r                      # 去后缀+扩展名：2018new_aini
    table = {
        "source": source,
        "source_desc": source_desc,
        "captured": time.strftime("%Y-%m-%d"),
        "img": {"dir": "img"},                     # 文件名逐条写在记录的 img 字段里
        "cdn": {"dir_rule": "md5(stem)[-1] 两位十六进制",
                "hosts": list(HOSTS),
                "path": CDN_DIR + "/<dir>/<stem>_<org|thumb>.<png|gif>"},
        "groups": dict(groups),
        "n_total": sum(groups.values()),           # 面板上的一共几个（含没解析出来的）
        "n_indexed": len(recs),
        "thresholds": dict(THRESHOLDS),
        "thresholds_calibrated": CALIBRATED,
        "thresholds_note": THRESHOLDS_NOTE,
        "byId": {r["id"]: r for r in recs},
        "byIndex": {str(r["index"]): r for r in recs},
        "byToken": {r["token"]: r for r in recs},
        "byName": {r["name"]: r for r in recs},
        "byCdn": by_cdn,
    }
    if extra:
        table.update(extra)
    return table
