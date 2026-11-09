"""livepipe.tools.wechat_emoji.common"""

from __future__ import annotations

from pathlib import Path
import sys
import urllib.request



def _log(*a) -> None:
    print(*a, file=sys.stderr, flush=True)


def _ua(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (emoji-table-builder)",
        "Accept": "*/*"})


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
