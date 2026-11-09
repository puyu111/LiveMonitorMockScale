"""livepipe.tools.weibo_emoji.cli"""

from __future__ import annotations

from .doc import DOC
from pathlib import Path
import argparse
import json
import os
import re
import sys

from .common import _log
from .fetch import _read_text
from .fetch import cdn_dir
from .fetch import download
from .fetch import index_src
from .parse import load_config
from .parse import load_map
from .parse import parse_panel
from .source import CALIBRATED
from .source import CONFIG_URL
from .source import SOURCE
from .source import SOURCE_CFG
from .source import SOURCE_DESC
from .source import _BAD
from .table import build_table
from .table import contact_sheets



def run(args: argparse.Namespace) -> int:
    from collections import Counter

    out = Path(args.out)
    img_dir = out / "img"
    out.mkdir(parents=True, exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)

    if args.config is not None:
        if args.panel or args.map:
            _log("[warn] --config 模式不使用 --panel/--map，已忽略")
        cookie = args.cookie or os.environ.get("WEIBO_COOKIE") or None
        text = _read_text(args.config, cookie=cookie)
        rows, mp = load_config(text, args.config,
                               include_parse_only=args.include_parse_only)
        groups = Counter(r["group"] for r in rows)
        _log(f"[config] {len(rows)} 个表情；分组 "
             + "、".join(f"{g} {c}" for g, c in groups.items()))
    else:
        panel_path = Path(args.panel) if args.panel else out / "panel.html"
        if args.panel == "-":
            text = sys.stdin.read()
        else:
            if not panel_path.exists():
                raise SystemExit(
                    f"面板 DOM 不存在：{panel_path}\n"
                    f"  out/ 不在 git 里，被清掉就没了。不想手工补面板与解析表就用自动模式：\n"
                    f"  python3 livepipe/weibo_emoji_table.py --out {out} --config")
            text = panel_path.read_text(encoding="utf-8", errors="replace")
        rows = parse_panel(text)
        groups = Counter(r["group"] for r in rows)
        _log(f"[panel] {len(rows)} 个表情；分组 "
             + "、".join(f"{g} {c}" for g, c in groups.items()))

        mp = load_map(Path(args.map) if args.map else out / "panel_map.json")
        hit_key = sum(1 for r in rows if r["key"] in mp)
        _log(f"[map] {len(mp)} 条；按 key 命中 {hit_key}/{len(rows)}")
        if mp and hit_key == 0:
            raise SystemExit(
                f"--map {args.map} 的键一条都对不上面板 key（面板 key 形如 "
                f"{'、'.join(r['key'] for r in rows[:3])}；map 的键形如 "
                f"{'、'.join(list(mp)[:3])}）—— 是不是把 label 当成 key 了？")

    src_index = index_src(Path(args.src)) if args.src else {}
    recs: list[dict] = []          # 下到图的（进表）
    raw: list[dict] = []           # 面板全量（含没图的），写 panel_raw.json 备查
    n_by_name = n_bad_dir = 0
    for r in rows:
        hit = mp.get(r["key"])
        how_key = "key"
        if hit is None and r["name"] in mp:        # 容一手：有人按 label 建的 map
            hit, how_key = mp[r["name"]], "name"
            n_by_name += 1
        row = dict(r, token=r.get("token") or f"[{r['name']}]",
                   url=None, cdn=None, stem=None, img=None,
                   confidence=None, how=None, status="missing", note="")
        if hit:
            url = hit["url"]
            cdn = url.rsplit("/", 1)[-1]
            stem = re.sub(r"_(thumb|org)\.(png|gif)$", "", cdn)
            row.update(url=url, cdn=cdn, stem=stem,
                       confidence=hit["confidence"], how=hit["how"])
            if url.rsplit("/", 2)[-2] != cdn_dir(stem):
                n_bad_dir += 1
                _log(f"[warn] {r['name']}: URL 里的目录 "
                     f"{url.rsplit('/', 2)[-2]} ≠ md5(«{stem}»)[-1]="
                     f"{cdn_dir(stem)} —— 要么规则变了，要么这条 URL 抄错了")
                row["note"] = "md5 dir mismatch"
            ext = Path(cdn).suffix.lower() or ".png"
            img = f"{r['index']:03d}_{_BAD.sub('_', r['name']).strip('_')}{ext}"
            row["img"] = img
            if args.no_download:
                row["status"] = "ok (not downloaded)"
                raw.append(row)
                continue
            ok, note, real = download(src_index, url, cdn, img_dir / img)
            row["note"] = note
            if ok:
                if real:                           # 实际用的 URL 可能与解析表里的不同（镜像/缩略图）
                    row["url_used"] = real
                from PIL import Image
                with Image.open(img_dir / img) as im:
                    w, h = im.size
                    mode = im.mode
                if mode not in ("RGBA", "LA", "P"):
                    _log(f"[warn] {img} 没有 alpha（mode={mode}）—— 合成背景会不对")
                rec = {"id": r["key"], "index": r["index"], "token": row["token"],
                       "name": r["name"], "group": r["group"],
                       "cdn": cdn, "stem": stem, "img": img, "url": url,
                       "size": [w, h], "mode": mode, "bytes": (img_dir / img).stat().st_size,
                       "confidence": hit["confidence"], "how": hit["how"],
                       "how_key": how_key}
                recs.append(rec)
                row["status"] = "ok"
            else:
                row["status"] = "failed"
                _log(f"[fail] {r['group']}/{r['key']} {r['name']}：{note}")
        raw.append(row)
        if len(raw) % 40 == 0:
            _log(f"  [{len(raw)}/{len(rows)}] 已下 {len(recs)}")

    (out / "panel_raw.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    miss = [r for r in raw if r["status"] == "missing"]
    fail = [r for r in raw if r["status"] == "failed"]
    _log(f"[panel_raw] -> {out/'panel_raw.json'}；"
         f"有图 {len(recs)}、解析不出 {len(miss)}、下载失败 {len(fail)}")
    if miss:
        _log("  解析不出（面板上有、CDN 上找不到）："
             + "、".join(f"{r['name']}({r['key']})" for r in miss))
    if n_by_name:
        _log(f"  [note] 有 {n_by_name} 条是按**中文名**命中的（不是 key）—— "
             f"重名时会有歧义，尽量用 key 建 map")
    if args.no_download:
        _log("[done] --no-download：只写了面板解析结果，**没有**建表（表里的图必须落地）")
        return 0
    if not recs:
        raise SystemExit("一张图都没下到 —— 检查 --map / --config / --src / 网络")

    unknown = [r for r in recs if r["confidence"] not in ("high",)]
    if unknown:
        _log(f"[note] {len(unknown)} 条置信度不是 high（{'、'.join(sorted({r['confidence'] for r in unknown}))}）"
             f"：{'、'.join(r['name'] for r in unknown[:8])}"
             f"{' …' if len(unknown) > 8 else ''}")

    if not args.no_sheet:
        for p in contact_sheets(recs, out):
            _log(f"[sheet] -> {p}（核一遍序号↔名字）")

    if args.config is not None:
        src_name = SOURCE_CFG
        src_desc = (f"微博表情 config（{args.config}）；phrase/URL 取自官方 "
                    "/ajax/statuses/config，id 用 CDN stem（config 没有面板 key）")
        extra = {"config": {"origin": args.config,
                            "include_parse_only": bool(args.include_parse_only)}}
    else:
        src_name, src_desc, extra = SOURCE, SOURCE_DESC, None
    table = build_table(sorted(recs, key=lambda r: r["index"]), dict(groups),
                        source=src_name, source_desc=src_desc, extra=extra)
    dst = out / "weibo-emoji.json"
    dst.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
    sizes = Counter(tuple(r["size"]) for r in recs)
    _log(f"[done] {table['n_indexed']}/{table['n_total']} 个表情 -> {dst}"
         f"（byId/byIndex/byToken/byName/byCdn）")
    _log(f"       尺寸分布 {sizes.most_common(6)}")
    if CALIBRATED is not True:
        _log(f"[warn] 这张表的 thresholds 是 **{CALIBRATED or '未标定'}**（片段见段首）；"
             "完整标定前，跑微博录屏后要核一遍认出来的是不是真表情")
    return 0


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=DOC, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="out/weibo_emoji", help="输出目录")
    p.add_argument("--config", nargs="?", const=CONFIG_URL, default=None,
                   help="自动模式：config JSON 的路径/URL/-（不带值 = 公开镜像 "
                        f"{CONFIG_URL}）；给了就不需要 --panel/--map")
    p.add_argument("--cookie", default=None,
                   help="取 config 时的 Cookie（官方 /ajax/statuses/config 需要登录态；"
                        "也可用环境变量 WEIBO_COOKIE）")
    p.add_argument("--include-parse-only", action="store_true",
                   help="把 config 的「仅解析」分组（老表情，不在面板上）也收进表")
    p.add_argument("--panel", default=None,
                   help="从浏览器复制出来的面板 DOM（默认 <out>/panel.html；- 表示 stdin）")
    p.add_argument("--map", default=None,
                   help="key → 图片 URL 的解析表（默认 <out>/panel_map.json）")
    p.add_argument("--src", default=None,
                   help="已下好的图目录（按 CDN 原名索引；给了就不联网）")
    p.add_argument("--no-download", action="store_true",
                   help="只解析面板 + 查表，不下图也不建表")
    p.add_argument("--no-sheet", action="store_true", help="不产出核对用的 sheet_NN.png")
    return p
