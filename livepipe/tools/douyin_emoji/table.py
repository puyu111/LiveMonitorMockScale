"""livepipe.tools.douyin_emoji.table"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import json
import sys
import time

from .source import DOUYIN_THRESHOLDS
from .source import OBJ_PREFIX



@lru_cache(maxsize=64)
def _font(size: int, cjk: bool = False):
    """取字体。DejaVu 没有汉字，画中文标题必须走 Noto CJK（本机已装 .ttc）。"""
    from PIL import ImageFont
    cands = (["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"] if cjk else []) + [
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for p in cands:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def contact_sheets(items: list[dict], out_dir: Path, per_sheet: int = 100,
                   cols: int = 10, cell: int = 96,
                   captions: list[str] | None = None) -> list[Path]:
    """把表情图拼成 contact sheet。

    DOM 模式：角标是**序号**，供人肉标注（也是 `--labels` 的键）。
    API 模式：`captions` 传官方名（如 ``[微笑]``），角标直接写名字 ⇒ 出图即验收表。
    """
    from PIL import Image, ImageDraw

    font = _font(15, cjk=bool(captions))
    paths = []
    for s, start in enumerate(range(0, len(items), per_sheet)):
        chunk = items[start:start + per_sheet]
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
            f = out_dir / "img" / f"{it['hash']}.webp"
            if f.exists():
                try:
                    im = Image.open(f).convert("RGBA")
                    im.thumbnail((cell - 8, cell - 8), Image.LANCZOS)
                    # 深色底更接近聊天面板，透明边缘看得清
                    bg = Image.new("RGBA", (cell, cell), (32, 32, 36, 255))
                    bg.alpha_composite(im, ((cell - im.width) // 2,
                                            (cell - im.height) // 2))
                    sheet.paste(bg.convert("RGB"), (x, y))
                except Exception as e:              # noqa: BLE001
                    d.text((x + 4, y + 4), f"ERR {e}"[:18], fill=(200, 0, 0), font=font)
            label = captions[start + i] if captions else str(start + i + 1)
            capfont = font
            if captions:      # 长名字（如 [不失礼貌的微笑不看]）会压到隔壁格子，缩到放得下
                while capfont.size > 9 and capfont.getlength(label) > cell:
                    capfont = _font(capfont.size - 1, cjk=True)
            d.text((x + 2, y + cell + 3), label, fill=(20, 20, 20), font=capfont)
        p = out_dir / f"sheet_{s + 1:02d}.png"
        sheet.save(p)
        paths.append(p)
    return paths


def merge(raw: list[dict], labels: dict, out_path: Path, source: str) -> dict:
    """把标注并进最终表：byToken / byName / byHash 三个索引。

    labels 的键是 contact sheet 上的序号（字符串）。
    """
    by_token: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    by_hash: dict[str, dict] = {}
    missing, unknown = [], []
    for i, it in enumerate(raw, start=1):
        lab = labels.get(str(i))
        if not lab:
            missing.append(i)
            continue
        rec = {
            "token": lab.get("token"),
            "name": lab.get("name") or (lab.get("token") or "").strip("[]"),
            "meaning": lab.get("meaning", ""),
            "hash": it["hash"],
            "url": it["url"],
        }
        by_hash[it["hash"]] = rec
        if rec["token"]:
            by_token[rec["token"]] = rec
        if rec["name"]:
            by_name[rec["name"]] = rec
    for k in labels:
        if not (k.isdigit() and 1 <= int(k) <= len(raw)):
            unknown.append(k)
    table = {
        "source": source,
        "captured": time.strftime("%Y-%m-%d"),
        "object_prefix": OBJ_PREFIX,
        "n_raw": len(raw),
        "n_labelled": len(by_hash),
        "byToken": by_token,
        "byName": by_name,
        "byHash": by_hash,
    }
    out_path.write_text(json.dumps(table, ensure_ascii=False, indent=2))
    if missing:
        print(f"[warn] 未标注 {len(missing)} 个：序号 {missing[:20]}"
              f"{' …' if len(missing) > 20 else ''}", file=sys.stderr)
    if unknown:
        print(f"[warn] labels 里有越界/非法键：{unknown[:20]}", file=sys.stderr)
    return table


def build_table(recs: list[dict], source: str) -> dict:
    """API 记录 → 最终表。byHash 是给评论反查用的那把钥匙。"""
    rec = lambda r: {                                    # noqa: E731
        "token": r["token"], "name": r["name"], "slug": r["slug"],
        "hash": r["hash"], "uri": r["uri"],
        "url": r["urls"][0] if r["urls"] else None,
    }
    by_token, by_name, by_slug, by_hash = {}, {}, {}, {}
    for r in recs:
        if not r["hash"]:
            continue
        e = rec(r)
        if r["token"]:
            by_token.setdefault(r["token"], e)
        if r["name"]:
            by_name.setdefault(r["name"], e)
        if r["slug"]:
            by_slug.setdefault(r["slug"], e)
        by_hash[r["hash"]] = e
    return {
        "source": source,
        "captured": time.strftime("%Y-%m-%d"),
        "object_prefix": OBJ_PREFIX,
        "n_total": len(recs),
        "n_indexed": len(by_hash),
        # thresholds：这张表的**匹配阈值**。带上它，换源时才不会拿错组的数（这是个真
        # 会踩的坑：视频号那套资产在这组阈值下一个都认不出来，而且不报错）
        "thresholds": dict(DOUYIN_THRESHOLDS),
        "byToken": by_token,
        "byName": by_name,
        "bySlug": by_slug,
        "byHash": by_hash,
    }
