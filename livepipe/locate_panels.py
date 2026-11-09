#!/usr/bin/env python
"""兼容层：面板定位的实现已并入 ``parse_live_comments.py``（``--auto-panels``）。

为什么只剩一层壳
----------------
面板定位原来是独立脚本，后来并进 ``parse_live_comments.py`` —— 因为这两步本来就是
一件事的两半：**先定出该在哪 OCR，再只在那个区域里 OCR**。分开时矩形要落盘、再由
另一个脚本读回来（``out/panels.json`` 的中转），多一份会过期的中间产物；合起来后
矩形直接以 ``--panels-file`` 兼容的 dict 形态传给裁切那一步，还能顺手打印「整帧 ->
裁切」的面积占比。

所以本文件不再自己实现任何东西，只做两件事：
1. **转出**旧名字（``PROMPT`` / ``load_worker`` / ``locate_panel`` …），让
   ``batch_locate_panels.py`` 等既有调用方一行都不用改；
2. **保留命令行**：``--method {model,ocr,hybrid}`` 的行为与合并前一致，输出 JSON 也
   还是 ``{"panels": {名: [x1,y1,x2,y2]}, "meta": …}`` 那个形状（可直接喂
   ``parse_live_comments.py --panels-file``）。

新代码请直接用 ``parse_live_comments.py``：

    # 一步到位：自动定位 + 只在面板内 OCR
    python livepipe/parse_live_comments.py --video v.mp4 --auto-panels hybrid

    # 只要矩形，落盘后用 --panels-file 复用（等价于本脚本的 CLI）
    python livepipe/locate_panels.py --video v.mp4 --method hybrid --out out/panels.json

依赖不变：``model``/``hybrid`` 需要 conda env ``vlx``（torch + transformers +
bitsandbytes），``ocr`` 只需要 rapidocr + opencv。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from PIL import Image  # noqa: E402

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:  # 作为包导入（python -m livepipe.… / from livepipe import …）
    from .parse_live_comments import (
        DEFAULT_PANEL_MODEL as DEFAULT_MODEL,
        DEFAULT_SPLIT_X,
        PANEL_PROMPT as PROMPT,
        PANEL_PROMPT_INPUT as PROMPT_INPUT,
        collect_comment_rows,
        load_panel_worker as load_worker,
        locate_panel_in_window as locate_panel,
        locate_panel_rect,
        locate_panels_auto,
        parse_model_boxes as parse_boxes,
        read_frame_rgb,
        scroll_area_from_rows as _scroll_area_from_rows,
    )
except ImportError:  # 直接跑脚本（python livepipe/locate_panels.py …）
    from parse_live_comments import (  # noqa: E402
        DEFAULT_PANEL_MODEL as DEFAULT_MODEL,
        DEFAULT_SPLIT_X,
        PANEL_PROMPT as PROMPT,
        PANEL_PROMPT_INPUT as PROMPT_INPUT,
        collect_comment_rows,
        load_panel_worker as load_worker,
        locate_panel_in_window as locate_panel,
        locate_panel_rect,
        locate_panels_auto,
        parse_model_boxes as parse_boxes,
        read_frame_rgb,
        scroll_area_from_rows as _scroll_area_from_rows,
    )

__all__ = [
    "DEFAULT_MODEL", "DEFAULT_SPLIT_X", "PROMPT", "PROMPT_INPUT", "collect_comment_rows",
    "load_worker", "locate_panel", "locate_panel_rect", "locate_panels_auto",
    "parse_boxes", "read_frame",
    "run", "make_parser", "scroll_area_from_rows",
]


def read_frame(video: str, at: float) -> Image.Image:
    """读 ``at`` 秒处的帧，返回 **RGB PIL 图**（保持本模块旧的返回类型）。"""
    return Image.fromarray(read_frame_rgb(video, at))


def scroll_area_from_rows(rows: list[list[int]], win_box, pad_x: int | None = None,
                          pad_y: int = 16) -> list[int] | None:
    """旧签名（``pad_x`` 左右同一个留白）；新代码用 ``pad_left`` / ``pad_right`` 分开给。

    合并后左右留白是**两个旋钮**：左边给等级徽章列（90 够），右边给框外表情（跟
    ``--pad-x``，默认 180）。这里把旧的单个 ``pad_x`` 摊到两边，行为与合并前一致。
    """
    left = 90 if pad_x is None else pad_x
    return _scroll_area_from_rows(rows, win_box, pad_left=left, pad_y=pad_y)


def run(args: argparse.Namespace) -> int:
    """定位左右面板并输出 ``{"panels": …, "meta": …}``（走合并后的实现）。"""
    t0 = time.time()
    panels, meta = locate_panels_auto(
        args.video, method=args.method, at=args.at, split_x=args.split_x,
        frames=args.frames, span=args.span, sub_width=args.sub_width,
        pad_x=args.pad_x, pad_y=args.pad_y, pad_top=args.pad_top,
        model=args.model, quant=args.quant, device=args.device, verbose=True)
    payload = {
        "_source": {"video": args.video, "at": args.at, "method": args.method,
                    "model": args.model if args.method != "ocr" else None,
                    "quant": args.quant, "split_x": args.split_x},
        "panels": panels,
        "meta": meta,
        "elapsed_s": round(time.time() - t0, 1),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\n[done] {len(panels)} 个面板 -> {args.out}", file=sys.stderr, flush=True)
    else:
        print(text)
    return 0 if panels else 1


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--video", required=True)
    p.add_argument("--out", default=None, help="输出 JSON 路径；省略则打到 stdout")
    p.add_argument("--at", type=float, default=0.0, help="用第几秒的帧定位（默认 0）")
    p.add_argument("--method", default="model", choices=["model", "ocr", "hybrid"],
                   help="model=全用 LocateAnything；ocr=全用含冒号评论行；hybrid=x 用模型、y 用 OCR")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--pad-x", type=int, default=180,
                   help="hybrid：右边界在「文本右沿」之外再留多少（给框外的表情 + 采样帧里"
                        "没出现过的更长评论留余量；模型给的窗口宽度不能用）")
    p.add_argument("--pad-top", type=int, default=80,
                   help="hybrid：首行之上再放宽多少（滚动区顶行常常没有冒号或被裁半行）")
    p.add_argument("--pad-y", type=int, default=16, help="hybrid/ocr：评论行上下各留的边距")
    p.add_argument("--frames", type=int, default=5, help="跨几帧取样评论行（默认 5）")
    p.add_argument("--span", type=float, default=None,
                   help="取样时间跨度（秒，默认 frames-1 秒；用它盖住后面真正要解析的帧）")
    p.add_argument("--split-x", type=int, default=DEFAULT_SPLIT_X,
                   help="左右窗口分界 x（原图像素，默认 1150）；0 = 不切分（整帧一个窗口）")
    p.add_argument("--sub-width", type=int, default=1000,
                   help="每个窗口子图推理前缩到的宽度（避免 OOM，默认 1000）")
    p.add_argument("--device", default="cuda")
    p.add_argument("--quant", default="4bit", choices=["none", "4bit", "8bit"])
    return p


if __name__ == "__main__":
    raise SystemExit(run(make_parser().parse_args()))
