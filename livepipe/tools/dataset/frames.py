"""``livepipe.tools.dataset.frames`` —— 按帧号取原帧。

尺子直接用 ``export_user_images`` 那一把（``FrameReader``）：``adaptive`` 抽帧是
**顺序解码数计数器**，回读也必须顺序 ``grab()``；``interval`` / ``backoff`` 抽帧本身
是按 t seek 的，那两种才 seek。写错口径的代价是框画在另一时刻的画面上 ——
这批录屏是变帧率，seek 的落点会漂半秒（bug-264）。
"""

from __future__ import annotations

from ..export_user_images.frames import FrameReader

__all__ = ["FrameReader", "iter_frames", "to_rgb"]


def to_rgb(frame):
    """BGR(OpenCV) → RGB(PIL)。数据集和复核图都按 RGB 存。"""
    import cv2
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def iter_frames(video: str, fps: float, start: float, mode: str, indices, *, log=None):
    """按**升序**帧号逐帧产出 ``(idx, BGR ndarray 或 None)``。

    ``indices`` 会被 ``set`` 去重再排序 —— 同一帧上的多条评论共用一次解码。
    """
    reader = FrameReader(video, fps, start, mode)
    if log:
        log(f"    [frames] {video} fps={reader.fps:.4f} 口径={mode} 锚点={reader.anchor}")
    try:
        for idx in sorted(set(int(i) for i in indices)):
            yield idx, reader.read_at(idx)
    finally:
        reader.close()
