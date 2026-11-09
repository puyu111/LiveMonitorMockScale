"""livepipe.tools.export_user_images.frames"""

from __future__ import annotations

from .common import _log
from ...parsing.ocr.decode import read_recovering



# --------------------------------------------------------------------------- #
# 取原帧（与 draw_frames 同口径）
# --------------------------------------------------------------------------- #

class FrameReader:
    """按 ``sample_mode`` 取原帧的一把尺子，产出的帧与抽行那条路逐帧同源。

    ``adaptive``：从锚点 ``start`` 起**顺序解码、逐帧数计数器**（``grab()``），
    目标帧 ``read()`` —— 与 ``draw_frames`` 完全一致（bug-264）。
    ``interval`` / ``backoff``：抽行本身就是按 t seek 的，这里照样 seek 才同帧。
    """

    def __init__(self, video: str, fps: float, start: float, sample_mode: str):
        import cv2
        self.cv2 = cv2
        self.mode = sample_mode
        self.cap = cv2.VideoCapture(video)
        if not self.cap.isOpened():
            raise SystemExit(f"无法打开视频：{video}")
        self.fps = fps or self.cap.get(cv2.CAP_PROP_FPS) or 25.0
        self.anchor = int(round(start * self.fps))
        self.pos = self.anchor
        if self.mode == "adaptive":
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.anchor)

    def read_at(self, idx: int):
        """取帧计数器 ``idx`` 那一帧（BGR ndarray）或 None。``idx`` 必须非降序。"""
        cv2 = self.cv2
        if self.mode == "adaptive":
            if idx < self.pos:                    # 正常不会发生（t 升序）；防呆重锚
                _log(f"    [warn] 要回退取帧（idx={idx} < {self.pos}），重新对齐锚点")
                self.pos = self.anchor
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.anchor)
            while self.pos < idx and self.cap.grab():
                self.pos += 1
            ok, frame = self.cap.read()
            if ok:
                self.pos += 1
            else:
                # 文件里的坏 h264 段（A9L 抖音 seg1 t≈957s 实测）会让顺序解码中断；
                # 跳过坏段再读（见 parsing/ocr/decode.py），别把坏帧当成片尾。
                newpos, frame = read_recovering(self.cap, max(self.pos, idx))
                ok = frame is not None
                if ok:
                    self.pos = newpos + 1
        else:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = self.cap.read()
        return frame if ok else None

    def close(self) -> None:
        self.cap.release()
