"""livepipe.tools.export_user_images.images"""

from __future__ import annotations

from pathlib import Path
import shutil

from .common import _log
from .common import expand_box
from .common import frame_index
from .common import frame_name
from .frames import FrameReader
from .io import _job_base



def write_images(jobs: list[dict], *, base: str, frames: Path, video: str | None,
                 sample_mode: str, start: float, box: bool, color: tuple[int, int, int],
                 width: int, pad: int, pad_ratio: float, quality: int,
                 used_frames: set[str]) -> int:
    """按帧顺序写图，成功的在 job 上打 ``ok=True``。返回取不到帧的张数。"""
    from PIL import Image, ImageDraw

    def draw_on(im, roi):
        if not (box and roi):
            return im
        ImageDraw.Draw(im).rectangle(expand_box(roi, pad, pad_ratio, im.size),
                                     outline=color, width=width)
        return im

    missing, done = 0, 0
    if base == "annotated":
        for j in jobs:
            src = _job_base(j, frames)
            if src is None:
                missing += 1
                _log(f"    [warn] 缺标注图 {frame_name(j['t'])}（t={j['t']:.1f}）—— 这条跳过")
                continue
            j["dst"].parent.mkdir(parents=True, exist_ok=True)
            if box and j.get("roi"):
                draw_on(Image.open(src).convert("RGB"), j["roi"]).save(j["dst"], quality=quality)
            else:
                shutil.copyfile(src, j["dst"])
            used_frames.add(src.name)
            j["ok"], done = True, done + 1
            if done % 100 == 0:
                _log(f"    … {done} 张")
        return missing

    import cv2
    reader = FrameReader(video, 0.0, start, sample_mode)
    fps = reader.fps
    _log(f"[export] 视频 {video}（fps={fps:.4f}，{sample_mode} 口径，锚点帧 {reader.anchor}）")
    jobs = sorted(jobs, key=lambda j: frame_index(j["t"], fps))
    cur_idx, cur_im = None, None
    try:
        for j in jobs:
            idx = frame_index(j["t"], fps)
            if idx != cur_idx:                  # 同一帧的几张共用一次解码
                frame = reader.read_at(idx)
                cur_idx, cur_im = idx, (Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                                        if frame is not None else None)
                if cur_im is not None:
                    used_frames.add(idx)
            if cur_im is None:
                missing += 1
                _log(f"    [warn] 取不到帧 idx={idx}（t={j['t']:.1f}）—— 这条跳过")
                continue
            j["dst"].parent.mkdir(parents=True, exist_ok=True)
            draw_on(cur_im.copy(), j.get("roi")).save(j["dst"], quality=quality)
            j["ok"], done = True, done + 1
            if done % 100 == 0:
                _log(f"    … {done} 张（已解到帧 {cur_idx}）")
    finally:
        reader.close()
    return missing
