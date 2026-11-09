"""``dataset.py`` 的实现（入口见 ``livepipe/dataset.py``）。

两个子命令（``review`` / ``build``）与八道闸，说明见 :mod:`.doc`。
"""

from __future__ import annotations

from .common import (CLASSES, CLASS_ID, CLASS_NAMES, DEFAULT_SPLIT_RULES, SPLITS,
                     box_from_yolo, boxes_of_row, clip_box, dedupe_boxes,
                     frame_index, frame_name, iou, normalize_split_rules,
                     read_jsonl, review_name, split_of, video_key, write_jsonl,
                     yolo_line, yolo_tokens)
from .frames import FrameReader, iter_frames, to_rgb
from .labels import check_lines, frame_boxes, frame_lines, lines_boxes
from .parser import make_parser
from .review import (INDEX, REJECTED, index_row, reconcile, render,
                     restore, state)
from .run import run
from .selftest import selftest
from .sources import Rec, Side, Unit, frame_groups, labelable, summarize, units_of
from .verify import preview, verify
from .write import build, group_frames

__all__ = [
    "CLASSES", "CLASS_ID", "CLASS_NAMES", "SPLITS", "DEFAULT_SPLIT_RULES",
    "INDEX", "REJECTED",
    "Rec", "Side", "Unit", "FrameReader",
    "box_from_yolo", "boxes_of_row", "build", "check_lines", "clip_box",
    "dedupe_boxes", "frame_boxes", "frame_groups", "frame_index", "frame_lines",
    "frame_name", "group_frames", "index_row", "iou", "iter_frames", "labelable",
    "lines_boxes",
    "make_parser", "normalize_split_rules", "preview", "read_jsonl", "reconcile",
    "render", "restore", "review_name", "run", "selftest", "split_of", "state",
    "summarize", "to_rgb", "units_of", "verify", "video_key", "write_jsonl",
    "yolo_line", "yolo_tokens",
]
