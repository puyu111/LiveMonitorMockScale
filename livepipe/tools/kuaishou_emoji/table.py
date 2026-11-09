"""livepipe.tools.kuaishou_emoji.table — 建最终表 + 人眼验收的 contact sheet。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import json
import sys
import time

from .source import KUAISHOU_THRESHOLDS
from .source import PANEL_KEY


@lru_cache(maxsize=32)
def _font(size: int, cjk: bool = True):
    from PIL import ImageFont

    cands = (["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"] if cjk else []) + [
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for p in cands:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def contact_sheets(recs: list[dict], out_dir: Path, per_sheet: int = 100,
                   cols: int = 10, cell: int = 96) -> list[Path]:
    """把表情拼成验收表：每格下面是 token（直接从官方面板拿到的名字，无需人工标注）。"""
    from PIL import Image, ImageDraw

    font = _font(15)
    paths: list[Path] = []
    for s, start in enumerate(range(0, len(recs), per_sheet)):
        chunk = recs[start:start + per_sheet]
        rows = (len(chunk) + cols - 1) // cols
        pad, cap = 6, 20
        w = cols * (cell + pad) + pad
        h = rows * (cell + pad + cap) + pad
        sheet = Image.new("RGB", (w, h), (246, 246, 246))
        d = ImageDraw.Draw(sheet)
        for i, it in enumerate(chunk):
            r, c = divmod(i, cols)
            x = pad + c * (cell + pad)
            y = pad + r * (cell + pad + cap)
            d.rectangle([x, y, x + cell, y + cell], fill=(255, 255, 255),
                        outline=(210, 210, 210))
            f = out_dir / "img" / it["img"]
            if f.exists():
                try:
                    im = Image.open(f).convert("RGBA")
                    im.thumbnail((cell - 8, cell - 8), Image.LANCZOS)
                    bg = Image.new("RGBA", (cell, cell), (32, 32, 36, 255))
                    bg.alpha_composite(im, ((cell - im.width) // 2,
                                            (cell - im.height) // 2))
                    sheet.paste(bg.convert("RGB"), (x, y))
                except Exception as e:              # noqa: BLE001
                    d.text((x + 4, y + 4), f"ERR {e}"[:18], fill=(200, 0, 0), font=font)
            label = it["token"]
            capfont = font
            while capfont.size > 9 and capfont.getlength(label) > cell:
                capfont = _font(capfont.size - 1)
            d.text((x + 2, y + cell + 3), label, fill=(20, 20, 20), font=capfont)
        p = out_dir / f"sheet_{s + 1:02d}.png"
        sheet.save(p)
        paths.append(p)
    return paths


def build_table(recs: list[dict], source: str) -> dict:
    """逐条记录 → 最终表。

    ``byId`` 是 ``gallery.load_emoji_gallery`` 直接吃的索引（记录里带 ``img``，
    所以图名不受 ``<id>.webp`` 约定限制）；``byToken`` / ``byName`` 是给人查的。
    """
    by_id: dict[str, dict] = {}
    by_token: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for r in recs:
        if not r.get("id"):
            continue
        rec = {"token": r["token"], "name": r["name"], "img": r["img"],
               "url": r["url"]}
        by_id[r["id"]] = rec
        by_token.setdefault(r["token"], rec)
        by_name.setdefault(r["name"], rec)
    missing = [r["id"] for r in recs if not r.get("img")]
    if missing:
        print(f"[warn] {len(missing)} 条没有图名：{missing[:5]}", file=sys.stderr)
    return {
        "source": source,
        "source_desc": f"{PANEL_KEY}（快手直播 Web 页面内嵌 token→图片映射）",
        "captured": time.strftime("%Y-%m-%d"),
        "n_total": len(recs),
        "n_indexed": len(by_id),
        # 阈值跟表走（2026-11-03 在 CTCC 8.8 快手 App 录屏上量：`[赞]` 0.94~0.96、
        # `[666]` 0.845~0.868、纯文字最高 0.80）。
        "thresholds": dict(KUAISHOU_THRESHOLDS),
        "byId": by_id,
        "byToken": by_token,
        "byName": by_name,
    }


def write_table(table: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
