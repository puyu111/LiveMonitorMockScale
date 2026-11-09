"""``livepipe.tools.dataset.write`` —— 落盘：图、标签、``data.yaml``、``manifest.json``。

**一张图 = 一帧原图**（不裁），同一帧上的每条评论各出一到五行标签。图名
``<视频键>_f<帧号>.jpg``，标签同名 ``.txt`` —— 标准 YOLO 结构，不加私有前缀；
额外的信息全放 ``manifest.json``（每张图来自哪段视频、哪一帧、切到哪个 split）。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .common import CLASS_NAMES, _log, frame_name
from .labels import frame_lines
from .review import INDEX, state


def _yaml(out: Path, names) -> str:
    return (f"path: {Path(out).resolve()}\n"
            "train: images/train\n"
            "val: images/val\n"
            "test: images/test\n"
            f"nc: {len(names)}\n"
            "names: [" + ", ".join(names) + "]\n")


def group_frames(rows) -> dict[tuple[str, int], list[dict]]:
    """``index.jsonl`` 的保留下来的行 → ``{(video, frame): [行, …]}``。"""
    out: dict[tuple[str, int], list[dict]] = {}
    for r in rows:
        out.setdefault((r["video"], int(r["frame"])), []).append(r)
    return out


def build(out, *, review_dir, quality: int = 90, dry_run: bool = False,
          force: bool = False, log=_log) -> dict:
    """按复核目录出数据集。返回账本；``dry_run`` 只算不写。"""
    out = Path(out)
    review_dir = Path(review_dir) if review_dir else out / "review"
    if not (review_dir / INDEX).is_file():
        raise SystemExit(f"没有 {review_dir / INDEX} —— 先跑 `dataset review`")
    st = state(review_dir, log=log)
    kept = st["kept"]

    if any(r["split"] not in ("train", "val", "test") for r in kept):
        raise SystemExit("index.jsonl 里有 split 不在 train/val/test —— 清单被人改过？")

    frames = group_frames(kept)

    # 账本：按 split 统计图与框（先算，dry-run 也只做这一步）
    counts = {s: {"images": 0, "boxes": {c: 0 for c in CLASS_NAMES}} for s in ("train", "val", "test")}
    video_split: dict[str, str] = {}
    frames_of_video: dict[str, int] = {}
    plan = []
    for (video, idx), rows in sorted(frames.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        split = rows[0]["split"]
        video_split.setdefault(video, split)
        if video_split[video] != split:
            raise SystemExit(f"同一个视频被切到两个 split：{video} → "
                             f"{video_split[video]} / {split}")
        wh = rows[0]["wh"]
        units = [dict(name=r["name"], records=r["records"]) for r in rows]
        lines = frame_lines([_unit_view(u) for u in units], wh)
        if not lines:
            continue
        name = frame_name(rows[0]["video_key"], idx)
        plan.append({"video": video, "idx": idx, "split": split, "name": name,
                     "wh": wh, "lines": lines})
        counts[split]["images"] += 1
        for line in lines:
            counts[split]["boxes"][CLASS_NAMES[int(line.split()[0])]] += 1
        frames_of_video[video] = frames_of_video.get(video, 0) + 1

    stats = {"units": len(kept), "rejected": len(st["rejected"]),
             "images": len(plan), "splits": counts}
    log(f"[build] 复核 {len(st['index'])} · 删除 {len(st['rejected'])} · "
        f"单元 {len(kept)} · 图 {len(plan)}")
    for s in ("train", "val", "test"):
        b = counts[s]["boxes"]
        log(f"    {s:5s} {counts[s]['images']:5d} 图 · 框 " +
            " ".join(f"{k}={v}" for k, v in b.items()))

    if dry_run:
        log("[build] dry-run，没写任何文件")
        return stats

    if out.exists() and any(out.glob("images/*/*")) and not force:
        raise SystemExit(f"{out} 里已经有图；要覆盖加 --force")

    # 先清掉上一轮的过期文件：人删掉一条评论后，这一帧可能整张不再成立；
    # 旧图旧标签不删就会留在数据集里冒充「还在」，闸 REVIEW_MATCH 也会报
    # 「有标签但清单里没有」。删文件不算写数据集 —— --dry-run 不会走到这儿。
    stale = 0
    for split in ("train", "val", "test"):
        keep = {p["name"] for p in plan if p["split"] == split}
        for kind in ("images", "labels"):
            d = out / kind / split
            if not d.is_dir():
                continue
            for f in d.iterdir():
                if f.suffix in (".jpg", ".txt") and f.stem not in keep:
                    f.unlink()
                    stale += 1
    if stale:
        log(f"[build] 清掉上一轮的 {stale} 个过期文件（这一轮不再成立的帧/标签）")

    for s in ("train", "val", "test"):
        (out / "images" / s).mkdir(parents=True, exist_ok=True)
        (out / "labels" / s).mkdir(parents=True, exist_ok=True)

    from .frames import iter_frames, to_rgb
    from PIL import Image

    by_video: dict[str, list[dict]] = {}
    for p in plan:
        by_video.setdefault(p["video"], []).append(p)

    written = missing = 0
    digest = hashlib.sha256()
    for video, items in by_video.items():
        row0 = next(r for r in kept if r["video"] == video)
        for idx, frame in iter_frames(video, float(row0.get("fps") or 0.0),
                                      float(row0.get("start") or 0.0),
                                      str(row0.get("mode") or "adaptive"),
                                      [p["idx"] for p in items], log=log):
            hit = next((p for p in items if p["idx"] == idx), None)
            if hit is None:
                continue
            if frame is None:
                missing += 1
                log(f"    [warn] 取不到帧 idx={idx} —— {hit['name']} 跳过")
                continue
            img = Image.fromarray(to_rgb(frame))
            img.save(out / "images" / hit["split"] / f"{hit['name']}.jpg", quality=quality)
            text = "\n".join(hit["lines"]) + "\n"
            (out / "labels" / hit["split"] / f"{hit['name']}.txt").write_text(text, encoding="utf-8")
            digest.update(hit["name"].encode())
            digest.update(text.encode())
            written += 1
            if written % 500 == 0:
                log(f"    … {written}/{len(plan)} 张")

    (out / "data.yaml").write_text(_yaml(out, CLASS_NAMES), encoding="utf-8")
    manifest = {
        "schema": 1,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "classes": list(CLASS_NAMES),
        "review": {"dir": str(review_dir), "units": len(st["index"]),
                   "kept": len(kept), "rejected": len(st["rejected"])},
        "splits": counts,
        "videos": [{"name": Path(v).name, "path": v,
                    "split": video_split[v], "images": frames_of_video[v],
                    "wh": next(p["wh"] for p in plan if p["video"] == v)}
                   for v in sorted(video_split)],
        "counts": {"images": written, "missing_frames": missing},
        "digest": digest.hexdigest(),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[build] 写出 {written} 张 → {out}（data.yaml / manifest.json 已更新）")
    if missing:
        log(f"[warn] {missing} 帧没取到，已跳过（闸 IN_FRAME 会照实量）")
    return {**stats, "written": written, "missing": missing, "digest": manifest["digest"]}


class _unit_view:
    """把 ``index.jsonl`` 的一行包装成 ``labels.frame_boxes`` 认的「单元」形状。"""

    __slots__ = ("name", "recs")

    def __init__(self, row: dict):
        self.name = row["name"]
        self.recs = [_rec(r) for r in row.get("records") or []]


class _rec:
    __slots__ = ("row",)

    def __init__(self, row: dict):
        self.row = row
