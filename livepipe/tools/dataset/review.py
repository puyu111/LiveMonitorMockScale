"""``livepipe.tools.dataset.review`` —— 出复核图、记对账、把人的删除变成指令。

**删除就是指令**：图还在 = 这条进数据集；图删了 = 这条不进。数据集每次都按复核
目录重算，所以「删了」是幂等的，重跑十次也不会回来。

两个位置、两种意思，都是文件管理器里挪一下：

============================  ==========================  ==========================
图在哪                        人的意思                    标签里少了什么
============================  ==========================  ==========================
``review/`` 原地               都对                        不少
哪都没有（删了）                这条不要                     这一类评论的框全没有
============================  ==========================  ==========================

等级标（``badge_box``）**不进数据集**，所以没有 ``badge_bad/`` 这个出口：只有等级标
不对的评论，现在和「都对」等价——它仍然是一条完整的评论。

**不能拿 ``frames/`` 当复核目录**：那是 parse 的产物，``render.py`` 每次重画前先把
整个目录清空（``stale.unlink()``）—— 删在那里，下一次 parse 全回来了。

``<out>/review/index.jsonl`` 是权威清单（复核图名 / 视频 / 帧 / 这一条的全部框），
``build`` 只认它 + 磁盘状态，不再去扫 run 目录。

口径变了（例如等级标退出数据集）时，用 ``review --redraw`` 刷新已有横条：默认只补新图，
旧图不会被覆盖；``--redraw`` 会重画保留的旧图，但仍尊重 ``rejected.jsonl``。
"""

from __future__ import annotations

from pathlib import Path

from .common import (CLASS_NAMES, _log, append_jsonl, boxes_of_row, frame_name,
                     read_jsonl, write_jsonl)

#: 复核图配色（与 ``parsing/render.py`` 同一套观感；按单元序号循环）。
PALETTE = ((255, 64, 64), (64, 200, 255), (255, 200, 0), (120, 255, 120),
           (255, 120, 255), (0, 220, 200), (255, 150, 60), (170, 150, 255))
COLOR_TEXT = (205, 205, 205)
COLOR_COLON = (0, 255, 255)
COLOR_EMOJI = (255, 220, 0)

INDEX = "index.jsonl"
REJECTED = "rejected.jsonl"


def _font(size: int):
    from PIL import ImageFont
    for p in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _label_width(text: str, size: int) -> int:
    return int(sum(size if ord(ch) > 0x2E80 else size * 0.55 for ch in text))


# --------------------------------------------------------------------------- #
# 清单
# --------------------------------------------------------------------------- #

def index_row(unit) -> dict:
    """一个复核单元 → ``index.jsonl`` 的一行（build 的唯一输入）。"""
    return {
        "name": unit.name,
        "video": unit.video,
        "video_key": unit.vk,
        "frame": unit.frame,
        "t": round(float(unit.t), 6),
        "wh": list(unit.recs[0].side.wh),
        "fps": float(unit.recs[0].side.fps),
        "mode": unit.recs[0].side.mode,
        "start": float(unit.recs[0].side.start),
        "split": unit.split,
        "roi": list(unit.roi),
        "records": [{
            "run": r.side.run, "seg": r.side.seg, "i": r.index, "t": round(float(r.t), 6),
            "username": r.row.get("username"), "content": r.row.get("content") or "",
            "roi": r.row.get("roi"),
            "text_box": r.row.get("text_box"),
            "colon_box": r.row.get("colon_box"),
            "emojis": [{"box": e.get("box"), "token": e.get("token"),
                        "n": e.get("n", 1)} for e in (r.row.get("emojis") or [])
                       if e.get("box")],
        } for r in unit.recs],
    }


# --------------------------------------------------------------------------- #
# 对账
# --------------------------------------------------------------------------- #

def reconcile(review_dir, *, log=_log) -> dict:
    """按「index 里有、盘上没有」判删除，把新删除的追进 ``rejected.jsonl``。

    幂等：已经在 ``rejected.jsonl`` 里的不会重复追加。
    """
    review_dir = Path(review_dir)
    index = read_jsonl(review_dir / INDEX)
    rej_path = review_dir / REJECTED
    rejected = {r["name"] for r in read_jsonl(rej_path)}
    on_disk = {p.name for p in review_dir.glob("*.jpg")}

    new = [r for r in index
           if r["name"] not in rejected and r["name"] not in on_disk]
    for r in new:
        append_jsonl(rej_path, {"name": r["name"], "video_key": r["video_key"],
                                "frame": r["frame"], "roi": r.get("roi")})
    if new:
        log(f"[review] 对账：{len(new)} 张复核图不在了 ⇒ 记进 {REJECTED}"
            f"（这些评论不再进数据集）")
    return {
        "index": index, "rejected": rejected | {r["name"] for r in new},
        "new_rejected": len(new),
        "missing_strips": sum(1 for r in index if r["name"] not in on_disk),
    }


def state(review_dir, *, log=_log) -> dict:
    """先对账、再汇总：``build`` 与 ``review`` 都从这儿拿「哪些留、哪些删」。"""
    review_dir = Path(review_dir)
    st = reconcile(review_dir, log=log)
    kept = [r for r in st["index"] if r["name"] not in st["rejected"]]
    return {**st, "kept": kept}


def restore(review_dir, name: str, *, log=_log) -> int:
    """撤回一次误删：从 ``rejected.jsonl`` 划掉（图下次 ``review`` 会重画）。"""
    review_dir = Path(review_dir)
    rej_path = review_dir / REJECTED
    rows = read_jsonl(rej_path)
    hit = [r for r in rows if Path(str(name)).name == r["name"]]
    if hit:
        write_jsonl(rej_path, [r for r in rows if r not in hit])
        log(f"[review] 撤回 {hit[0]['name']}：已从 {REJECTED} 划掉，下次 review 会重画这张")
        return 1
    log(f"[review][warn] {name} 不在 {REJECTED} 里 —— 没有可撤回的")
    return 0


def reject_frame(review_dir, key: str, *, log=_log) -> int:
    """整帧丢弃：给 ``<视频键>_f<帧号>``（可带 ``.jpg``），这一帧上的**所有**评论一起不进数据集。

    逐条复核是默认口径（删除单位 = 标签单位）；但人判断「这一帧整个都废了」时，
    不该逼他一张张删 —— 这条命令把「整图不要」表达成一条指令，效果与手删完全一致
    （都只是往 ``rejected.jsonl`` 里记，幂等）。
    """
    review_dir = Path(review_dir)
    stem = Path(str(key)).stem
    rows = read_jsonl(review_dir / INDEX)
    hit = [r for r in rows if frame_name(r["video_key"], int(r["frame"])) == stem]
    if not hit:
        log(f"[review][warn] {stem} 在 {INDEX} 里没有对应帧 —— 检查视频键/帧号")
        return 0
    rejected = {r["name"] for r in read_jsonl(review_dir / REJECTED)}
    new = [r for r in hit if r["name"] not in rejected]
    for r in new:
        append_jsonl(review_dir / REJECTED, {"name": r["name"], "video_key": r["video_key"],
                                             "frame": r["frame"], "roi": r.get("roi")})
        p = review_dir / r["name"]
        if p.is_file():
            p.unlink()
    log(f"[review] 整帧丢弃 {stem}：{len(new)} 条评论记进 {REJECTED}"
        + ("" if len(new) == len(hit) else f"（{len(hit) - len(new)} 条本来就已经删了）"))
    if not new:
        return 0
    return len(new)


# --------------------------------------------------------------------------- #
# 出图
# --------------------------------------------------------------------------- #

def _crop(frame, box):
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in box)
    return frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)], max(0, x1), max(0, y1)


def _strip(frame, unit, row, *, pad: int, scale: float):
    """一条评论的横条：ROI 外扩一圈，画四种框 + 黑条编号。"""
    from PIL import Image, ImageDraw

    roi = row["roi"]
    x1, y1, x2, y2 = (int(v) for v in roi)
    cap_h = 22 if y1 >= 24 else 0          # 上面留得出黑条就留，留不出就不画标题
    crop_box = (x1 - pad, y1 - cap_h - pad, x2 + pad, y2 + pad)
    sub, ox, oy = _crop(frame, crop_box)
    img = Image.fromarray(sub)
    d = ImageDraw.Draw(img)
    col = PALETTE[(unit.i - 1) % len(PALETTE)]

    def rel(box):
        bx1, by1, bx2, by2 = (int(v) for v in box)
        return [bx1 - ox, by1 - oy, bx2 - ox, by2 - oy]

    d.rectangle(rel(roi), outline=col, width=3)
    tb = row.get("text_box")
    if tb and list(tb) != list(roi):
        d.rectangle(rel(tb), outline=COLOR_TEXT, width=1)
    cb = row.get("colon_box")
    if cb:
        cx1, cy1, cx2, cy2 = rel(cb)
        d.rectangle([cx1 - 2, cy1 - 2, cx2 + 2, cy2 + 2], outline=COLOR_COLON, width=2)
    for e in row.get("emojis") or []:
        if e.get("box"):
            d.rectangle(rel(e["box"]), outline=COLOR_EMOJI, width=2)

    if cap_h:
        text = f"{unit.i} {row.get('username') or '?'}: {row.get('content') or ''}"
        toks = [f"{e.get('token') or '?'}" + (f"×{e['n']}" if int(e.get("n", 1)) > 1 else "")
                for e in (row.get("emojis") or []) if e.get("box")]
        if toks:
            text += "  [" + " ".join(toks) + "]"
        text = text[:34]
        ly = y1 - cap_h - oy
        d.rectangle([0, ly, min(img.width, _label_width(text, 15) + 10), ly + cap_h - 2],
                    fill=(0, 0, 0))
        d.text((4, ly + 2), text, fill=col, font=_font(15))

    if scale != 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    return img


def render(units, review_dir, *, scale: float = 1.6, quality: int = 90, pad: int = 10,
           redraw: bool = False, log=_log) -> dict:
    """给**还没被处理过**的单元出复核图，并重写 ``index.jsonl``（全量清单）。

    默认只补新增的：已经在盘上 / 已删（``rejected.jsonl``）的一律跳过，不会把人删掉
    的图又画回来。``redraw=True`` 时**保留下来的旧图也重画**（口径变了要刷新旧横条），
    但仍然尊重 ``rejected.jsonl`` —— 删除永远是删除。
    """
    from .frames import iter_frames, to_rgb
    review_dir = Path(review_dir)
    review_dir.mkdir(parents=True, exist_ok=True)

    st = state(review_dir, log=log)
    done = set(st["rejected"])
    if not redraw:
        done |= {p.name for p in review_dir.glob("*.jpg")}
    todo = [u for u in units if u.name not in done]
    action = "重画" if redraw else "画"
    log(f"[review] 单元 {len(units)} 个：跳过 {len(units) - len(todo)}、"
        f"这次要{action} {len(todo)}（删除 {len(st['rejected'])}）")

    # 按视频分组，每个视频顺序解码一次
    by_video: dict[str, list] = {}
    for u in units:
        by_video.setdefault(u.video, []).append(u)

    written = 0
    failed: list[str] = []
    for video, us in by_video.items():
        todo_here = [u for u in us if u.name not in done]
        if not todo_here:
            continue
        # 同一个视频可能被两个 run 各解析一遍；取帧口径优先信 adaptive（顺序解码）
        side = next((u.recs[0].side for u in todo_here if u.recs[0].side.mode == "adaptive"),
                    todo_here[0].recs[0].side)
        pending: dict[int, list] = {}
        for u in todo_here:
            pending.setdefault(u.frame, []).append(u)
        for idx, frame in iter_frames(video, side.fps, side.start, side.mode,
                                      pending.keys(), log=log):
            if frame is None:
                for u in pending.get(idx, []):
                    failed.append(u.name)
                    log(f"    [warn] 取不到帧 idx={idx} —— {u.name} 跳过")
                continue
            rgb = to_rgb(frame)
            for u in pending.get(idx, []):
                row = u.recs[0].row
                img = _strip(rgb, u, row, pad=pad, scale=scale)
                img.save(review_dir / u.name, quality=quality)
                written += 1
            if written and written % 500 == 0:
                log(f"    … {written} 张（已解到帧 {idx}）")

    write_jsonl(review_dir / INDEX, [index_row(u) for u in units])
    log(f"[review] 写出 {written} 张复核图 → {review_dir}"
        f"（清单 {INDEX} 共 {len(units)} 行）")
    if failed:
        log(f"[warn] {len(failed)} 张没取到帧，下次 review 会再试")
    return {"units": len(units), "written": written, "failed": len(failed),
            "rejected": len(st["rejected"])}


def index_records(row: dict) -> list[dict]:
    """``index.jsonl`` 的一行 → 可以喂给 ``boxes_of_row`` 的记录列表。"""
    return row.get("records") or []


__all__ = ["INDEX", "REJECTED", "index_row", "index_records",
           "reconcile", "state", "restore", "reject_frame", "render",
           "boxes_of_row", "CLASS_NAMES"]
