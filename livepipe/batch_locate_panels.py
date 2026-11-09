#!/usr/bin/env python
"""批量测试 locate_panels：对 30 个时间点各定位一次，输出 JSON + 图片。

为什么单独写
------------
``locate_panels.py`` 每次调用都重新加载模型（~11s）。批量跑 30 次时这会浪费
5 分钟以上。本脚本**只加载一次模型**，然后在 30 个时间点上复用，并额外做两件事：

1. 收集每次定位的两个面板矩形，统计**稳定性**（左右面板各自的位置极差），
   因为「固定录屏只在第一帧定位一次」的前提是定位结果跨帧稳定。
2. 输出 3 类产物：
   - ``runs/<idx>_<t>.json``：每次定位的原始 JSON（同 locate_panels 格式）
   - ``shots/<idx>_<t>.jpg``：每次定位的标注图（矩形画到该帧上）
   - ``montage.jpg``：30 张缩略图拼成的大图，一眼看全部结果
   - ``summary.json``：全部结果 + 稳定性统计

用法
----
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    /home/puyu/anaconda3/envs/vlx/bin/python livepipe/batch_locate_panels.py \
        --video "/home/puyu/work/data/抖音奇瑞汽车/抖音视频号.mp4" \
        --count 30 --span 600 --out out/panel_batch

默认在 ``[--start, --start+span]`` 内均匀取 ``--count`` 个时间点。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:  # 包导入（python -m livepipe.…）
    from .locate_panels import (
        PROMPT, PROMPT_INPUT, load_worker, locate_panel, parse_boxes,
    )
except ImportError:  # 直接跑脚本（python livepipe/batch_locate_panels.py …）
    from locate_panels import (  # noqa: E402
        PROMPT, PROMPT_INPUT, load_worker, locate_panel, parse_boxes,
    )

_ANCHORS = {"panel": PROMPT, "input": PROMPT_INPUT}
from PIL import ImageFont  # noqa: E402

_PALETTE = {"left": (255, 40, 40), "right": (40, 140, 255)}


def read_frame_rgb(video: str, at: float):
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"无法打开视频：{video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(at * fps)))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return None
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def locate_once(worker, frame_rgb, split_x: int, sub_width: int, prompt: str):
    """对一帧定位左右窗口的目标区域，返回 {name: [x1,y1,x2,y2](原图)}。"""
    import torch

    img = Image.fromarray(frame_rgb)
    W, H = img.size
    out: dict[str, list[int]] = {}
    for name, (x0, x1) in {"left": (0, split_x), "right": (split_x, W)}.items():
        sub = img.crop((x0, 0, x1, H))
        try:
            local = locate_panel(worker, sub, sub_width, prompt=prompt)
        except torch.OutOfMemoryError:
            torch.cuda.empty_cache()
            local = None
        if local is not None:
            out[name] = [local[0] + x0, local[1], local[2] + x0, local[3]]
    return out


def draw_boxes(img: Image.Image, panels: dict[str, list[int]], title: str) -> Image.Image:
    vis = img.copy()
    d = ImageDraw.Draw(vis)
    for name, (x1, y1, x2, y2) in panels.items():
        color = _PALETTE.get(name, (255, 170, 0))
        d.rectangle([x1, y1, x2, y2], outline=color, width=6)
        d.text((x1 + 6, y1 + 6), name, fill=color)
    d.text((20, 20), title, fill=(255, 255, 0))
    return vis


def main(args: argparse.Namespace) -> int:
    out = Path(args.out)
    (out / "runs").mkdir(parents=True, exist_ok=True)
    (out / "shots").mkdir(parents=True, exist_ok=True)

    times = [args.start + i * (args.span / max(args.count - 1, 1)) for i in range(args.count)]

    prompt = _ANCHORS[args.anchor]
    t0 = time.time()
    print(f"[load] 模型={args.model} 锚点={args.anchor} ...", flush=True)
    worker = load_worker(args.model, args.quant, args.device)
    print(f"[load] 完成 {time.time()-t0:.1f}s；开始 {args.count} 次定位", flush=True)

    results: list[dict] = []
    thumbs: list[Image.Image] = []
    for i, at in enumerate(times):
        t = time.time()
        frame = read_frame_rgb(args.video, at)
        if frame is None:
            print(f"  [{i:02d}] t={at:6.1f}s 读帧失败，跳过", flush=True)
            continue
        panels = locate_once(worker, frame, args.split_x, args.sub_width, prompt)
        dt = time.time() - t

        # 每次的 JSON
        (out / "runs" / f"{i:02d}_{at:.0f}s.json").write_text(json.dumps(
            {"_source": {"video": args.video, "at": at, "model": args.model,
                         "split_x": args.split_x, "anchor": args.anchor, "prompt": prompt},
             "panels": panels}, ensure_ascii=False, indent=2), encoding="utf-8")

        # 每次的标注图
        vis = draw_boxes(Image.fromarray(frame), panels, f"#{i:02d}  t={at:.0f}s  {dt:.1f}s")
        vis.save(out / "shots" / f"{i:02d}_{at:.0f}s.jpg", quality=85)
        thumbs.append(vis.resize((vis.width // 4, vis.height // 4), Image.LANCZOS))

        results.append({"idx": i, "t": at, "panels": panels, "seconds": round(dt, 2)})
        got = " ".join(f"{k}={v}" for k, v in panels.items())
        print(f"  [{i:02d}] t={at:6.1f}s {dt:4.1f}s  {got}", flush=True)

    # 拼图（6 列）
    if thumbs:
        cols, tw, th = 6, thumbs[0].width, thumbs[0].height
        rows = (len(thumbs) + cols - 1) // cols
        montage = Image.new("RGB", (cols * tw, rows * th), (20, 20, 20))
        for k, th_img in enumerate(thumbs):
            montage.paste(th_img, ((k % cols) * tw, (k // cols) * th))
        montage.save(out / "montage.jpg", quality=82)

    # 稳定性统计：每个面板的位置极差
    stats = {}
    for name in ("left", "right"):
        boxes = [r["panels"][name] for r in results if name in r["panels"]]
        if not boxes:
            stats[name] = {"found": 0}
            continue
        arr = list(zip(*boxes))
        stats[name] = {
            "found": len(boxes), "total": len(results),
            "x1": [min(arr[0]), max(arr[0])], "y1": [min(arr[1]), max(arr[1])],
            "x2": [min(arr[2]), max(arr[2])], "y2": [min(arr[3]), max(arr[3])],
            "max_span": max(max(a) - min(a) for a in arr),
        }
    summary = {"video": args.video, "count": len(results), "start": args.start,
               "span": args.span, "split_x": args.split_x, "sub_width": args.sub_width,
               "anchor": args.anchor,
               "elapsed_s": round(time.time() - t0, 1), "stability": stats,
               "results": results}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                      encoding="utf-8")

    print(f"\n[done] {len(results)} 次定位，用时 {time.time()-t0:.0f}s -> {out}", flush=True)
    for name, s in stats.items():
        if s.get("found"):
            print(f"  {name}: 命中 {s['found']}/{s['total']}，位置极差 {s['max_span']}px", flush=True)
    return 0


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--video", required=True)
    p.add_argument("--out", default="out/panel_batch")
    p.add_argument("--count", type=int, default=30, help="定位次数（默认 30）")
    p.add_argument("--start", type=float, default=0.0, help="起始时间（秒）")
    p.add_argument("--span", type=float, default=600.0, help="在这些秒内均匀取样")
    p.add_argument("--anchor", default="panel", choices=list(_ANCHORS),
                   help="定位目标：panel=评论区（默认）；input=聊天输入框")
    p.add_argument("--split-x", type=int, default=1150)
    p.add_argument("--sub-width", type=int, default=1000)
    p.add_argument("--model", default="nvidia/LocateAnything-3B")
    p.add_argument("--device", default="cuda")
    p.add_argument("--quant", default="4bit", choices=["none", "4bit", "8bit"])
    return p


if __name__ == "__main__":
    raise SystemExit(main(make_parser().parse_args()))
