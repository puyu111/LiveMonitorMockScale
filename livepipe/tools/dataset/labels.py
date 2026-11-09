"""``livepipe.tools.dataset.labels`` —— 一个复核单元 / 一帧 → 若干行标签。

标签行只有五个字段：``<cls> <cx> <cy> <w> <h>``（归一化，6 位小数）。
一帧上的**所有**单元都写进同一个 ``.txt`` —— 同一时刻屏幕上的一串评论本来就该是
「一张图配多行标签」，不是一张图一条。

排序按 ``(类, 上沿, 左沿)``：同一个视频重跑时行序稳定，diff 只有真变化的那几行。
"""

from __future__ import annotations

from .common import (CLASS_NAMES, box_from_yolo, boxes_of_row, clip_box,
                     dedupe_boxes, iou, yolo_line)


def frame_boxes(units) -> list[tuple[int, tuple[int, int, int, int]]]:
    """一帧上的全部框：``[(cls, box), …]``，已按类去重、已排序。

    只产出 :data:`~livepipe.tools.dataset.common.CLASSES` 里的类；上游有
    ``badge_box`` 也不再写（等级标不进数据集）。
    """
    per: dict[int, list] = {i: [] for i in range(len(CLASS_NAMES))}
    for u in units:
        for rec in u.recs:
            for cls, box in boxes_of_row(rec.row):
                per[cls].append(box)
    out: list[tuple[int, tuple[int, int, int, int]]] = []
    for cls in range(len(CLASS_NAMES)):
        for box in dedupe_boxes(per[cls]):
            out.append((cls, box))
    out.sort(key=lambda cb: (cb[0], cb[1][1], cb[1][0]))
    return out


def frame_lines(units, wh) -> list[str]:
    """一帧 → 标签文本行（已裁剪、已去重、已排序）。"""
    lines: list[str] = []
    for cls, box in frame_boxes(units):
        clipped = clip_box(box, wh)
        if clipped is None:                  # 夹完不成框：宁可少一个，不写退化框
            continue
        lines.append(yolo_line(cls, clipped, wh))
    return lines


def lines_boxes(lines, wh) -> list[tuple[int, tuple[float, float, float, float]]]:
    """标签文本行 → ``[(cls, 像素框), …]``（闸 ROUNDTRIP 用）。"""
    out = []
    for line in lines:
        parts = line.split()
        cls = int(parts[0])
        out.append((cls, box_from_yolo(*(float(v) for v in parts[1:]), wh)))
    return out


def check_lines(lines, wh, *, thr: float = 0.6) -> list[str]:
    """逐行体检：越界 / 反序 / 同类仍挨太近。返回问题描述（空 = 全过）。"""
    bad: list[str] = []
    w, h = float(wh[0]), float(wh[1])
    seen: dict[int, list] = {}
    for n, line in enumerate(lines, 1):
        try:
            parts = line.split()
            cls = int(parts[0])
            cx, cy, bw, bh = (float(v) for v in parts[1:])
        except Exception:                    # noqa: BLE001
            bad.append(f"第 {n} 行读不了：{line!r}")
            continue
        x1, y1, x2, y2 = box_from_yolo(cx, cy, bw, bh, wh)
        if not (0 <= cx <= 1 and 0 <= cy <= 1 and 0 < bw <= 1 and 0 < bh <= 1):
            bad.append(f"第 {n} 行归一化越界：{line}")
        if x1 < -0.5 or y1 < -0.5 or x2 > w - 0.5 or y2 > h - 0.5:
            bad.append(f"第 {n} 行解回像素越界：{x1:.1f},{y1:.1f},{x2:.1f},{y2:.1f}（{w:.0f}×{h:.0f}）")
        if x2 < x1 or y2 < y1:
            bad.append(f"第 {n} 行坐标反序：{line}")
        for other in seen.get(cls, []):
            if iou((x1, y1, x2, y2), other) >= thr:
                bad.append(f"第 {n} 行与同类另一个框挨太近（IoU ≥ {thr}）：{line}")
                break
        seen.setdefault(cls, []).append((x1, y1, x2, y2))
    return bad
