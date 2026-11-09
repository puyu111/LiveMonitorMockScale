"""livepipe.parsing.cli.draw — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from pathlib import Path
import argparse

from ..common.log import _log
from ..ocr.overrides import resolve_panels
from ..render import draw_frames



def _draw(args: argparse.Namespace, comments: list[dict], meta: dict | None) -> int:
    """产出标注图（默认开，``--no-frames`` 关）。没有 ``--out`` 就没地方放，跳过。

    出图清单（每张图的时刻 / 路径 / 画了几条）登记进 ``meta``，随 ``summary.json``
    落盘；路径写成**相对 ``--out``**，整份产物搬走也不会指错地方。
    """
    if args.no_frames or not args.out or not args.video:
        return 0
    fd = Path(args.out) / "frames"
    # 取帧口径得跟**产出这批行的那一趟**一致（帧身份由抽帧模式决定，见 draw_frames）。
    # 优先信 summary 里记的那一趟 —— ``--rows`` 复算时它就是这批行的出身证明；命令行
    # 只作兜底，免得「复算时忘了带 --start」把整批图又画错一帧。
    m = meta or {}
    mode = m.get("sample_mode") or getattr(args, "sample_mode", None) or "adaptive"
    start = m.get("start")
    if start is None:
        start = getattr(args, "start", 0.0) or 0.0
    # 冒号像素找法的阈值：只有平台档案**显式写了** colon_* 的面板才传（其余面板保持旧行为，
    # 也不允许「补」新框，见 refresh_colon_box）。
    colon_params: dict[str, dict] = {}
    pf = getattr(args, "panels_file", None)
    if pf and Path(pf).exists():
        for name, p in resolve_panels(pf).items():
            ks = {k: p[k] for k in ("colon_bg_max_v", "colon_v_min", "colon_v_delta")
                  if p.get(k) is not None}
            if ks:
                colon_params[name] = ks
    manifest = draw_frames(args.video, comments, fd,
                           start=float(start), sample_mode=str(mode),
                           colon_marks=not getattr(args, "no_colon_marks", False),
                           colon_params=colon_params or None)
    if manifest:
        base = Path(args.out)
        for e in manifest:
            p = Path(e["path"])
            try:
                e["path"] = str(p.relative_to(base))
            except ValueError:                  # 不在 --out 下就留绝对路径
                pass
        _log(f"[frames] {len(manifest)} 张标注图 -> {fd}/（每条评论的 ROI + 编号 + 用户名"
             f"{' + 冒号框' if not getattr(args, 'no_colon_marks', False) else ''}）")
        if meta is not None:
            meta["frames"] = manifest
            meta["frames_drawn"] = len(manifest)
    return len(manifest)
