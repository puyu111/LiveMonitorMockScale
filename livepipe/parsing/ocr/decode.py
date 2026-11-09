"""livepipe.parsing.ocr.decode — 顺序解码遇到坏段时的恢复。

素材录屏是手机直接录的，文件里可能有**损坏的 h264 段**：A9L 抖音 seg1
（`8：35-9：34.mp4`）实测帧 42912（t≈957s）起 `Invalid NAL unit size`，
`cap.read()` 到这里返回 False。旧逻辑把它当「视频结束」⇒ 抽行 / 画标注图 / 导出
原帧全都停在 957s（两次全量跑都卡在 t≈955s）。实测 seek 跳过坏段之后又能正常解码
（跳 10 帧即可），所以这里按 1→4→16→64→256→1024→4096 帧递增试跳，恢复后继续。

健康文件上这套逻辑是 no-op：`read()` 成功就原样返回；真到片尾时往后跳也读不出，
返回 ``None``，调用方照旧结束。
"""

from __future__ import annotations

from ..common.log import _log

# 坏段恢复的试跳步长（帧）。从 1 开始：多数损坏只有几帧；到 4096 帧（约 90s@45fps）
# 还读不出就当文件真的到头了。
_SKIP_STEPS = (1, 4, 16, 64, 256, 1024, 4096)


def skip_bad(cap, base: int, *, steps=_SKIP_STEPS) -> int | None:
    """从 ``base`` 往后跳一段，找到一个能读的帧就返回**它的下一帧号**（位置）。

    返回 ``None`` = 往后到最大步长都读不出来（真到片尾 / 文件截断）。
    """
    import cv2
    for step in steps:
        cap.set(cv2.CAP_PROP_POS_FRAMES, base + step)
        if cap.grab():
            _log(f"  [decode] 坏段：帧 {base} 读不出，跳到 {base + step} 继续")
            return base + step + 1          # grab 已经消费了这一帧
    return None


def read_recovering(cap, idx: int, *, steps=_SKIP_STEPS):
    """读帧计数器 ``idx`` 那一帧；顺序解码失败时跳过坏段再读。

    返回 ``(实际帧号, frame)`` —— 跳段后实际帧号会大于入参；整段读不出来
    （片尾 / 截断）返回 ``(None, None)``。
    """
    ok, frame = cap.read()
    if ok:
        return idx, frame
    nxt = skip_bad(cap, idx, steps=steps)
    if nxt is None:
        return None, None
    ok, frame = cap.read()
    if not ok:
        return None, None
    return nxt, frame
