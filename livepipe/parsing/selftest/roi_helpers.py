"""roi 自检的公共行构造器（RapidOCR 的 (box, text, score) 形状）。"""

from __future__ import annotations

from ..ocr.fused import assemble_row


def row(sock, text, x1, x2, y1, y2, t=0.0, panel="left", badge=None, badge_left=None):
    return {"t": t, "panel": panel, "socket": sock, "text": text,
            "box": [x1, y1, x2, y2], "x_text": 633, "x_right": 1146,
            "badge_box": ([601, y1, 632, y2] if badge else None),
            "badge_left": badge_left}


def blk(text, x1, x2, yc):                      # RapidOCR 的 (box, text, score)
    return [[x1, yc - 15], [x2, yc - 15], [x2, yc + 15], [x1, yc + 15]], text, 0.99

