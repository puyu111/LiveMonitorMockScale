"""``livepipe.tools.dataset.verify`` —— 八道闸 + 预览反画。

数据集错了训练会「安静地」变差，所以每一道都**从盘上**量，不信内存里的中间结果：

========================  ====================================================
REVIEW_MATCH              数据集里每条评论都有一张未删的复核图；被删的评论在
                          标签里出现 0 次；保留的评论每个框都在
LABEL_SOURCE              保留的每个单元都有 ``username`` 和 ``colon_box`` ——
                          缺一个，``body_text`` / ``colon`` 标签就不可信
ROUNDTRIP                 标签反解回像素，与源框逐项相等（6 位小数 ⇒ ≤ 0.5px）
IN_FRAME                  反解后 0 ≤ x1 ≤ x2 ≤ W-1、0 ≤ y1 ≤ y2 ≤ H-1
SPLIT_LEAK                同一段视频只出现在一个 split
DUP_IMAGE                 同一 (视频, 帧) 只有一张图；同帧同类 IoU ≥ 0.6 只留一个
CLASS_COVER               逐 split 逐类计数；val/test 出现 train 没有的类就报错
PREVIEW                   从 **label 文件**反画 20 张（不从源框画），人眼过一遍
========================  ====================================================

PREVIEW 必须从标签反画：拿源框画图只能证明「框本来是对的」，要证明的是
「写进 txt 的那串数能还原成同一个框」。
"""

from __future__ import annotations

from pathlib import Path

from .common import CLASS_NAMES, _log, clip_box, frame_name, iou, read_jsonl
from .labels import check_lines, lines_boxes
from .review import INDEX, REJECTED

#: 预览图配色（与复核图同一套，外加类名小字）。
COLORS = ((255, 64, 64), (64, 200, 255), (0, 255, 255), (255, 220, 0))
SPLITS = ("train", "val", "test")


def _near(a, b, tol: float = 0.5) -> bool:
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))


def _rows_index(rows) -> dict[str, list[dict]]:
    """``index.jsonl`` → ``{图名: [单元行, …]}``。"""
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(frame_name(r["video_key"], int(r["frame"])), []).append(r)
    return out


def _unit_boxes(row: dict):
    from .common import boxes_of_row
    out = []
    for rec in row.get("records") or []:
        out += boxes_of_row(rec)
    return out


def preview(out, names, *, n: int = 20, log=_log) -> list[str]:
    """从**标签文件**反画 n 张 → ``<out>/preview/``。返回写出的文件名。"""
    from PIL import Image, ImageDraw, ImageFont

    out = Path(out)
    (out / "preview").mkdir(parents=True, exist_ok=True)
    for f in (out / "preview").glob("*.jpg"):     # 上一轮反画的旧图不留
        f.unlink()
    picked = [names[i] for i in range(0, len(names), max(1, len(names) // n))][:n] if names else []
    font = None
    for p in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if Path(p).exists():
            font = ImageFont.truetype(p, 22)
            break
    font = font or ImageFont.load_default()
    written = []
    for split, name in picked:
        src = out / "images" / split / f"{name}.jpg"
        lbl = out / "labels" / split / f"{name}.txt"
        if not src.is_file() or not lbl.is_file():
            continue
        img = Image.open(src).convert("RGB")
        d = ImageDraw.Draw(img)
        wh = img.size
        for cls, box in lines_boxes(lbl.read_text(encoding="utf-8").splitlines(), wh):
            x1, y1, x2, y2 = (int(round(v)) for v in box)
            col = COLORS[cls % len(COLORS)]
            d.rectangle([x1, y1, x2, y2], outline=col, width=2)
            d.text((x1 + 2, max(0, y1 - 24)), CLASS_NAMES[cls], fill=col, font=font)
        img.save(out / "preview" / f"{name}.jpg", quality=85)
        written.append(name)
    log(f"[verify] PREVIEW 反画 {len(written)} 张 → {out / 'preview'}")
    return written


def verify(out, *, review_dir=None, preview_n: int = 20, log=_log) -> dict:
    """跑八道闸，返回 ``{闸名: {"ok": bool, "detail": str}}`` 与总判定。"""
    out = Path(out)
    review_dir = Path(review_dir) if review_dir else out / "review"
    index = read_jsonl(review_dir / INDEX)
    rejected = {r["name"] for r in read_jsonl(review_dir / REJECTED)}

    by_image = _rows_index(index)
    gates: dict[str, dict] = {}

    # 盘上的实际内容
    disk: dict[str, tuple[str, Path, Path]] = {}
    for split in SPLITS:
        for lbl in sorted((out / "labels" / split).glob("*.txt")):
            disk[lbl.stem] = (split, out / "images" / split / f"{lbl.stem}.jpg", lbl)

    # ---- 1 · REVIEW_MATCH ----
    # 判「这个框在不在标签里」用 **0.6**：与归帧/去重同一个阈值 —— 被去重丢掉的那个
    # 副本按定义与原框 IoU ≥ 0.6，它「在标签里」；不同评论之间 ROI 本来就 < 0.6，
    # 所以这个阈值不会把别人的框认成自己的。
    problems: list[str] = []
    for name, (split, img, lbl) in disk.items():
        units = by_image.get(name) or []
        if not units:
            problems.append(f"{name}: 有标签但清单里没有这条帧的单元")
            continue
        lines = lbl.read_text(encoding="utf-8").splitlines()
        boxes = lines_boxes(lines, units[0]["wh"])
        for u in units:
            if u["name"] in rejected:
                for cls, box in _unit_boxes(u):
                    if any(c == cls and iou(box, b) >= 0.6 for c, b in boxes):
                        problems.append(f"{name}: 被删的 {u['name']} 的框还在标签里")
            else:
                for cls, box in _unit_boxes(u):
                    if not any(c == cls and iou(box, b) >= 0.6 for c, b in boxes):
                        problems.append(f"{name}: 保留的 {u['name']} 少了 {CLASS_NAMES[cls]} 框")
                        break
    # 反向：清单里保留的单元，盘上必须有图
    for name, units in by_image.items():
        if name in disk:
            continue
        alive = [u for u in units if u["name"] not in rejected]
        if alive:
            problems.append(f"{name}: 有保留的单元却没有标签文件")
    gates["REVIEW_MATCH"] = {"ok": not problems,
                             "detail": (f"{len(disk)} 图 ↔ {len(index)} 单元"
                                        if not problems else "；".join(problems[:5]))}

    # ---- 2 · LABEL_SOURCE ----
    # 过滤发生在 review 之前的 scan 阶段（sources.labelable）；这道闸从**盘上的清单**
    # 反查，防止以后有人绕过 review 手改 index，或过滤规则被改回去。
    bad_src: list[str] = []
    for u in index:
        if u["name"] in rejected:
            continue
        for rec in u.get("records") or []:
            if not str(rec.get("username") or "").strip() or not rec.get("colon_box"):
                bad_src.append(u["name"])
                break
    gates["LABEL_SOURCE"] = {
        "ok": not bad_src,
        "detail": (f"{len([u for u in index if u['name'] not in rejected])} 个保留单元都有 username + colon"
                   if not bad_src else "；".join(f"{n} 缺 username/colon" for n in bad_src[:5])),
    }

    # ---- 3 · ROUNDTRIP / 4 · IN_FRAME / 6 · DUP_IMAGE（逐文件体检）----
    rt_bad: list[str] = []
    in_bad: list[str] = []
    dup_bad: list[str] = []
    for name, (split, img, lbl) in disk.items():
        units = by_image.get(name) or []
        wh = units[0]["wh"] if units else None
        if wh is None:
            dup_bad.append(f"{name}: 不知道画面尺寸")
            continue
        lines = lbl.read_text(encoding="utf-8").splitlines()
        bad = check_lines(lines, wh)
        if bad:
            in_bad += [f"{name}: {b}" for b in bad[:2]]
        if any("IoU" in b for b in bad):
            dup_bad.append(f"{name}: 同类框挨太近")
        # 源框 = 保留单元的框（**按写入时的裁剪切过**）—— 逐行找得到对应就过
        src = [(cls, clip_box(box, wh)) for u in units if u["name"] not in rejected
               for cls, box in _unit_boxes(u)]
        src = [(c, b) for c, b in src if b is not None]
        for n_line, (cls, box) in enumerate(lines_boxes(lines, wh), 1):
            if not any(c == cls and _near(box, s) for c, s in src):
                rt_bad.append(f"{name}:{n_line} 反解 {tuple(round(v, 1) for v in box)} 找不到源框")
    gates["ROUNDTRIP"] = {"ok": not rt_bad,
                          "detail": f"{sum(len(p.read_text(encoding='utf-8').splitlines()) for _, _, p in disk.values())} 行"
                                    if not rt_bad else "；".join(rt_bad[:5])}
    gates["IN_FRAME"] = {"ok": not in_bad, "detail": f"{len(disk)} 图" if not in_bad else "；".join(in_bad[:5])}
    gates["DUP_IMAGE"] = {"ok": not dup_bad, "detail": f"{len(disk)} 张图名唯一" if not dup_bad else "；".join(dup_bad[:5])}

    # ---- 5 · SPLIT_LEAK ----
    leak: list[str] = []
    video_split: dict[str, set] = {}
    for name, (split, _, _) in disk.items():
        units = by_image.get(name) or []
        if not units:
            continue
        vk = units[0]["video_key"]
        video_split.setdefault(vk, set()).add(split)
        if units[0]["split"] != split:
            leak.append(f"{name}: 清单写 {units[0]['split']}，文件在 {split}/")
    for vk, ss in video_split.items():
        if len(ss) > 1:
            leak.append(f"{vk} 同时出现在 {'/'.join(sorted(ss))}")
    gates["SPLIT_LEAK"] = {"ok": not leak,
                           "detail": f"{len(video_split)} 段素材各归一个 split" if not leak else "；".join(leak[:5])}

    # ---- 7 · CLASS_COVER ----
    cover: dict[str, dict] = {}
    for name, (split, _, lbl) in disk.items():
        for line in lbl.read_text(encoding="utf-8").splitlines():
            cls = int(line.split()[0])
            cover.setdefault(split, {}).setdefault(CLASS_NAMES[cls], 0)
            cover[split][CLASS_NAMES[cls]] += 1
    miss = []
    for split in ("val", "test"):
        for cls in cover.get(split, {}):
            if cls not in cover.get("train", {}):
                miss.append(f"{split} 有 {cls}，train 里没有")
    gates["CLASS_COVER"] = {"ok": not miss,
                            "detail": " · ".join(f"{s}:{len(c)}类" for s, c in sorted(cover.items()))
                                      if not miss else "；".join(miss)}

    # ---- 8 · PREVIEW ----
    names = sorted(disk.items())
    picked = [(s, n) for n, (s, _, _) in names]
    written = preview(out, picked, n=preview_n, log=log) if disk else []
    gates["PREVIEW"] = {"ok": bool(written), "detail": f"反画 {len(written)} 张 → preview/"}

    ok = all(g["ok"] for g in gates.values())
    for name, g in gates.items():
        log(f"[verify] {'✅' if g['ok'] else '❌'} {name:<12} {g['detail'][:110]}")
    log(f"[verify] {'八道闸全过' if ok else '有闸没过 —— 这批数据集不许用'}")
    return {"ok": ok, "gates": gates}
