#!/usr/bin/env python
"""把 JSON 里的矩形画到视频帧上，输出标注图片。

支持的 JSON 形态（自动识别）
----------------------------
1. ``locate_panels.py`` 的输出：``{"panels": {"left": [x1,y1,x2,y2], ...}, ...}``
   —— 画每个面板矩形。
2. ``parse_live_comments.py`` 的 ``comments.jsonl``（每行一个对象，含 ``roi``／``box``／``text_box``）
   —— 每行画一个评论框，只画 ``--at`` 时刻前后 ``--window`` 秒内的。
3. 通用：``{"boxes": [[x1,y1,x2,y2], ...]}``、``[[x1,y1,x2,y2], ...]``、
   或 ``{"name": [x1,y1,x2,y2]}`` 的映射 —— 一律画出来。

坐标一律是**原图像素** ``[x1,y1,x2,y2]``。

用法
----
    P=/home/puyu/anaconda3/envs/vlx/bin/python

    # 画面板矩形
    $P livepipe/draw_boxes.py --json out/panels.json --video video.mp4 --at 0 --out out/panels.jpg

    # 画某时刻附近的评论框
    $P livepipe/draw_boxes.py --json out/comments_ocr/comments.jsonl --video video.mp4 --at 60 --window 8 --out out/comments_60.jpg

    # **配置模式**：只给配置（+ 输出文件夹），按 input.videos 自动逐段找视频与面板
    $P livepipe/draw_boxes.py --config livepipe/风云T7.json --out out/风云T7_20260826_11_02/panels
    $P livepipe/draw_boxes.py --config livepipe/风云T7.json --segments seg6,seg7 --at 800 --out out/panels

配置模式（``--config``）
------------------------
段列表取自配置的 ``input.videos``（目录/通配符展开后按文件名排序 = 时间顺序）；
每段的视频是展开结果。

``panels.mode=manual`` 时**配置即唯一来源**：直接画 ``panels.manual`` 里的 rect，
改完配置重跑就生效（不必先同步面板文件）；面板文件落后于配置时打一行警告。
其他 mode 按下面顺序找面板文件，找到第一个就用：

1. ``<--out>/<段名>/panels.json``（输出文件夹正好是某次运行的根目录时）
2. ``<段目录>/panels.json``（本项目的标准落盘位置 —— ``output.dir`` 下的 segN/）
3. ``<段目录>/panels.resolved.json``（``parse`` 步展开平台档案后的产物）
4. ``panels.manual`` 里该段的手写条目（面板文件被删了也能画）

时间取 ``--at``（可逗号分隔多个，如 ``8,800``）；省略则用配置的 ``draw.at``，
超出视频时长会自动夹到片尾前 0.5 秒。输出命名 ``<段名>_panels_<秒>s.jpg``。
``--out`` 省略时默认 ``<output.dir>/panels/``。

不需 GPU、不需模型，只依赖 opencv + Pillow。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

# 每组框的颜色循环（RGB）
_PALETTE = [(255, 40, 40), (40, 140, 255), (40, 200, 80), (255, 170, 0),
            (200, 60, 220), (0, 200, 200), (255, 90, 160), (140, 140, 0)]


def load_boxes(path: Path, at: float, window: float) -> list[tuple[str, list[int]]]:
    """读 JSON/JSONL，返回 [(label, [x1,y1,x2,y2]), ...]（原图像素）。"""
    text = path.read_text(encoding="utf-8").strip()
    boxes: list[tuple[str, list[int]]] = []

    # JSONL：每行一个对象（comments.jsonl / rows.jsonl）
    if path.suffix == ".jsonl" or (text and text[0] != "{" and "\n" in text):
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
            # ``comments.jsonl`` 的整行框叫 ``roi``（含等级徽章 + 框外表情）；
            # 兼容旧/通用写法的 ``box``，以及只有文字框的 ``text_box``。
            box = obj.get("box") or obj.get("roi") or obj.get("text_box")
            if not box:
                continue
            t = obj.get("t")
            if t is not None and abs(t - at) > window:
                continue
            user, content = obj.get("username"), obj.get("content")
            label = (obj.get("text")
                     or (f"{user}: {content}" if user else content)
                     or obj.get("panel") or "")
            boxes.append((str(label), [int(round(float(v))) for v in box]))
        return boxes

    data = json.loads(text)

    # 面板 manifest：{"panels": {name: [x1,y1,x2,y2] | {"rect": [...]}}, ...}
    # 两种写法都要认 —— 定位产物落盘时写的是 {"rect": …, "platform": …}
    # （见 pipeline._step_panels / config._panel_entry）。
    if isinstance(data, dict) and "panels" in data and isinstance(data["panels"], dict):
        for name, b in data["panels"].items():
            try:
                boxes.append((str(name), [int(round(float(v))) for v in _as_box(b)]))
            except (KeyError, TypeError, ValueError):
                print(f"[warn] 面板 {name!r} 的框读不出来，已跳过：{b!r}",
                      file=sys.stderr)
        return boxes

    # 通用 dict 映射 {name: [x1,y1,x2,y2]}
    if isinstance(data, dict):
        # {"boxes": [...]}
        if "boxes" in data and isinstance(data["boxes"], list):
            for i, b in enumerate(data["boxes"]):
                boxes.append((str(i), [int(v) for v in _as_box(b)]))
            return boxes
        for name, b in data.items():
            if isinstance(b, dict) and "rect" in b:
                b = b["rect"]
            if isinstance(b, (list, tuple)) and len(b) == 4:
                boxes.append((str(name), [int(v) for v in b]))
        return boxes

    # 裸列表 [[x1,y1,x2,y2], ...]
    if isinstance(data, list):
        for i, b in enumerate(data):
            boxes.append((str(i), [int(v) for v in _as_box(b)]))
    return boxes


def _as_box(b) -> list[int]:
    """接受 [x1,y1,x2,y2] 或 {"x1":..,"y1":..,"x2":..,"y2":..} 或含 box/rect 的对象。"""
    if isinstance(b, dict):
        if "box" in b:
            return b["box"]
        if "rect" in b:  # 面板 manifest 的条目写法
            return b["rect"]
        return [b["x1"], b["y1"], b["x2"], b["y2"]]
    return b


# --------------------------------------------------------------------------- #
# 配置模式：从 livepipe 配置自动找「视频 + 面板」
# --------------------------------------------------------------------------- #

def _load_config(path: str):
    """读 livepipe 配置（JSONC），返回 ``(config 模块, cfg)``。

    脚本用 ``python livepipe/draw_boxes.py`` 直接跑时，仓库根不在 ``sys.path`` 上，
    所以先把根插进去，再按包导入 —— 相对路径/默认值/段展开就全都与管线一致。
    """
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from livepipe import config as C  # noqa: PLC0415

    return C, C.load(path)


def _times(value) -> list[float]:
    """``--at`` → 秒列表：接受 None / 数字 / ``"8,800"``。"""
    if value is None:
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    out: list[float] = []
    for v in str(value).split(","):
        if not v.strip():
            continue
        try:
            out.append(float(v))
        except ValueError:
            raise SystemExit(f"--at 要秒数（可逗号分隔），读不到：{v!r}") from None
    return out


def _boxes_from_entries(entries: dict) -> list[tuple[str, list[int]]]:
    """``panels.manual`` 那种 ``{面板名: {"rect": …}}`` → 画框用的列表。"""
    boxes: list[tuple[str, list[int]]] = []
    for name, b in (entries or {}).items():
        try:
            boxes.append((str(name), [int(round(float(v))) for v in _as_box(b)]))
        except (KeyError, TypeError, ValueError):
            print(f"[warn] 面板 {name!r} 的框读不出来，已跳过：{b!r}", file=sys.stderr)
    return boxes


def _panel_source(C, cfg: dict, seg: dict, out_dir: Path) -> tuple[Path | None, str]:
    """该段的面板文件（按优先级找第一个存在的），找不到返回 ``(None, "panels.manual")``。

    ``panels.mode=manual`` 时不走这里 —— 调用方直接画配置里的 ``panels.manual``。
    """
    cands: list[Path] = []
    name = seg.get("name")
    if name:  # --out 正好给了某次运行的根目录
        cands += [out_dir / name / "panels.json",
                  out_dir / name / "panels.resolved.json"]
    if seg.get("panels_file"):
        cands.append(Path(seg["panels_file"]))
    if seg.get("out"):
        cands.append(Path(seg["out"]) / "panels.resolved.json")
    seen: set[Path] = set()
    for p in cands:
        p = Path(p)
        if p in seen:
            continue
        seen.add(p)
        if p.exists():
            return p, str(p)
    return None, "panels.manual"


def _probe(video: str) -> tuple[float, tuple[int, int]]:
    """``(时长秒, (宽, 高))``；读不到时长时给 0（调用方据此不夹时间）。"""
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"无法打开视频：{video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    return (n / fps if fps > 0 and n > 0 else 0.0), size


def run_config(args: argparse.Namespace, C, cfg: dict) -> int:
    """配置模式：按段画面板框，一段一张（``--at`` 给多个时刻时一段多张）。"""
    segs = C.segments(cfg)
    if not segs:
        print("[error] 配置里没有可用的视频段", file=sys.stderr)
        return 2

    want = {s.strip() for s in str(args.segments).split(",") if s.strip()} \
        if args.segments else None
    if want:
        missing = want - {str(s.get("name")) for s in segs}
        if missing:
            print(f"[warn] --segments 里有不存在的段：{sorted(missing)}；"
                  f"可用：{[s.get('name') for s in segs]}", file=sys.stderr)

    out_dir = Path(args.out) if args.out else Path(cfg["output"]["dir"]) / "panels"
    out_dir.mkdir(parents=True, exist_ok=True)
    times = _times(args.at) or [float(cfg["draw"]["at"])]

    ok = failed = 0
    for seg in segs:
        name = str(seg.get("name") or Path(str(seg.get("video") or "video")).stem)
        if want and name not in want:
            continue
        video = seg.get("video")
        if not video or not Path(video).exists():
            print(f"[{name}][warn] 视频不存在：{video} —— 跳过", file=sys.stderr)
            failed += 1
            continue

        manual = C.manual_entries(cfg, seg)
        if cfg["panels"]["mode"] == "manual" and manual:
            # manual ⇒ 配置是唯一来源，直接画配置值（面板文件可能还没同步）。
            boxes = _boxes_from_entries(manual)
            src = "panels.manual（配置）"
            drift = C.manifest_drift(manual, str(seg.get("panels_file") or ""))
            if drift:
                # 注意：`--only panels` 是**管线**的参数，不是这个脚本的 —— 直接把
                # 能跑的命令写全，免得照着警告抄还报「unrecognized arguments」。
                print(f"[{name}][warn] 面板文件落后于配置（本图按配置画）。要同步落盘就跑："
                      f'python -m livepipe run "{args.config}" --only panels'
                      f"（文件：{seg.get('panels_file')}）", file=sys.stderr)
        else:
            pf, src = _panel_source(C, cfg, seg, out_dir)
            if pf is not None:
                boxes = load_boxes(pf, 0.0, args.window)
            else:
                boxes = _boxes_from_entries(manual)
        if not boxes:
            print(f"[{name}][warn] 找不到面板框（panels.mode={cfg['panels']['mode']}）"
                  f"—— 跳过", file=sys.stderr)
            failed += 1
            continue

        duration, size = _probe(video)
        for t in times:
            at = min(float(t), max(0.0, duration - 0.5)) if duration > 0 else float(t)
            if at != float(t):
                print(f"[{name}] t={t:g}s 超出时长 {duration:.0f}s ⇒ 夹到 {at:g}s",
                      file=sys.stderr)
            try:
                img = read_frame(str(video), at)
            except SystemExit as e:
                print(f"[{name}][warn] {e} —— 跳过", file=sys.stderr)
                failed += 1
                continue
            vis = draw(img, boxes, args.width, labels=not args.no_labels)
            dst = out_dir / f"{name}_panels_{at:g}s.jpg"
            vis.save(dst, quality=args.quality)
            print(f"[{name}] {src} · {len(boxes)} 框 · t={at:g}s "
                  f"({size[0]}x{size[1]}) -> {dst}", flush=True)
            ok += 1

    print(f"[done] {ok} 张 -> {out_dir}"
          + (f"；{failed} 段失败/跳过" if failed else ""), flush=True)
    return 0 if ok else 1


def read_frame(video: str, at: float) -> Image.Image:
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"无法打开视频：{video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(at * fps)))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"读不到 {at}s 处的帧")
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def draw(img: Image.Image, boxes: list[tuple[str, list[int]]], width: int,
         labels: bool) -> Image.Image:
    vis = img.copy()
    d = ImageDraw.Draw(vis)
    for i, (label, b) in enumerate(boxes):
        x1, y1, x2, y2 = b
        if x1 > x2:
            x1, x2 = x2, x1
        if y1 > y2:
            y1, y2 = y2, y1
        color = _PALETTE[i % len(_PALETTE)]
        d.rectangle([x1, y1, x2, y2], outline=color, width=width)
        if labels and label:
            ty = y1 - 14 if y1 >= 14 else y2 + 2
            d.text((x1 + 2, ty), label[:24], fill=color)
    return vis


def run(args: argparse.Namespace) -> int:
    if args.config:
        C, cfg = _load_config(args.config)
        return run_config(args, C, cfg)

    if not (args.json and args.video and args.out):
        raise SystemExit("单段模式需要 --json、--video、--out；"
                         "要按配置一次画全部段就给 --config")

    path = Path(args.json)
    if not path.exists():
        raise SystemExit(f"找不到 JSON：{path}")

    boxes = load_boxes(path, 0.0 if args.at is None else _times(args.at)[0], args.window)
    print(f"[read] {path.name} -> {len(boxes)} 个框"
          + (f"（t={_times(args.at)[0] if args.at is not None else 0:g}±{args.window}s 内）"
             if path.suffix == ".jsonl" else ""),
          flush=True)
    if not boxes:
        print("[warn] 没有可画的框", file=sys.stderr)
        return 1
    for label, b in boxes[:12]:
        print(f"    {b}  {label[:40]}", flush=True)

    times = _times(args.at) or [0.0]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    for t in times:
        tb = load_boxes(path, t, args.window) if len(times) > 1 else boxes
        img = read_frame(args.video, t)
        vis = draw(img, tb, args.width, labels=not args.no_labels)
        dst = out if len(times) == 1 else out.with_name(f"{out.stem}_{t:g}s{out.suffix}")
        vis.save(dst, quality=args.quality)
        print(f"[done] t={t:g}s {vis.size} -> {dst}", flush=True)
    return 0


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="livepipe 配置（JSONC）：按 input.videos 自动逐段找视频与面板")
    p.add_argument("--segments", help="配置模式下只画这些段（逗号分隔，如 seg6,seg7）")
    p.add_argument("--json", help="框来源：panels.json / comments.jsonl / 通用 JSON（单段模式必填）")
    p.add_argument("--video", help="用来取底帧的视频（单段模式必填）")
    p.add_argument("--out", help="单段模式：输出图片路径；配置模式：输出文件夹")
    p.add_argument("--at", default=None,
                   help="取第几秒的帧（逗号分隔可给多个）；省略 ⇒ 单段 0 / 配置用 draw.at")
    p.add_argument("--window", type=float, default=5.0,
                   help="JSONL 模式下只画 |t-at|<=window 的框（默认 5 秒）")
    p.add_argument("--width", type=int, default=5, help="框线宽（像素）")
    p.add_argument("--quality", type=int, default=90, help="JPEG 质量")
    p.add_argument("--no-labels", action="store_true", help="不画文字标签")
    return p


if __name__ == "__main__":
    raise SystemExit(run(make_parser().parse_args()))
