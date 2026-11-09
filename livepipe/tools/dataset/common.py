"""``livepipe.tools.dataset.common`` —— 纯函数（自检覆盖，不读盘、不依赖三方库）。

这一层只有「算」：坐标怎么折成 YOLO 那串数、拆回来的精确反函数、同类框怎么去重、
视频怎么切 split、文件名怎么起。所有会读盘/开视频/画图的都放在别的模块里 ——
这样 ``--selftest`` 能在没有 cv2/Pillow 的机器上跑。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# --------------------------------------------------------------------------- #
# 类表
# --------------------------------------------------------------------------- #
#: 类 → 上游字段。**顺序就是 YOLO 的 cls 编号**（写进 ``data.yaml`` 的 names，
#: 改顺序等于改数据集的含义）。``emoji_run`` 的来源是 ``emojis[].box`` 这个列表，
#: 所以字段名给 None，由 :func:`boxes_of_row` 单独展开。
#:
#: ``level_badge``（``badge_box``）**不进数据集**：上游 850 条「有读数没坐标」，
#: 写进去就是 32.2% 的可见等级标假负；要继续用它，先修上游的框检测。
CLASSES: tuple[tuple[str, str | None], ...] = (
    ("comment_row", "roi"),         # 整行带（左右两端都在内）
    ("body_text", "text_box"),      # 昵称 + 正文（纯 OCR 文字框）
    ("colon", "colon_box"),         # 昵称与正文之间的分隔冒号
    ("emoji_run", None),            # 每个表情各一个框
)
CLASS_NAMES: tuple[str, ...] = tuple(c for c, _ in CLASSES)
CLASS_ID: dict[str, int] = {c: i for i, c in enumerate(CLASS_NAMES)}

SPLITS: tuple[str, ...] = ("train", "val", "test")

#: 默认按**视频**切 split 的规则：子串匹配视频路径，先命中先用，匹配不上落 ``default``。
#: test 故意给微博那一段（版式、分辨率都不同，正好当泛化检查）；val 取一段中等长度的；
#: 其余全 train。**同一个视频只能落一个 split** —— 按帧随机切就是把相邻帧抄进验证集。
DEFAULT_SPLIT_RULES: dict = {
    "default": "train",
    "rules": [
        {"match": "微博", "split": "test"},
        {"match": "17：13-17：33", "split": "val"},
    ],
}


def _log(*a) -> None:
    print(*a, flush=True)


# --------------------------------------------------------------------------- #
# 归帧 / 命名
# --------------------------------------------------------------------------- #

def frame_index(t: float, fps: float) -> int:
    """t → 帧计数器。与 ``sample_frames_adaptive`` / ``draw_frames`` 同一把尺子。"""
    return int(round(float(t) * float(fps)))


_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\s]+')


def video_key(video: str) -> str:
    """视频 → 文件名里用的键：``12：01-13：00.mp4`` → ``12-01-13-00``。

    全角/半角冒号都换成 ``-``（Linux 上 ``:`` 合法，但拷到 Windows/共享盘就废）；
    路径分隔符、空白、控制字符一律并成一个 ``-``。
    """
    stem = Path(str(video)).stem
    s = stem.replace("：", "-").replace(":", "-")
    s = _UNSAFE.sub("-", s).strip("-.")
    return s or "video"


def frame_name(vk: str, idx: int) -> str:
    """一张数据集图的名字（不含后缀）：``12-01-13-00_f010466``。"""
    return f"{vk}_f{int(idx):06d}"


def review_name(vk: str, idx: int, i: int) -> str:
    """一张复核横条的名字：``12-01-13-00_f010466_01.jpg``（i 从 1 起，同帧内按上沿排）。"""
    return f"{frame_name(vk, idx)}_{int(i):02d}.jpg"


# --------------------------------------------------------------------------- #
# 坐标：折算 / 反解 / 裁剪 / 去重
# --------------------------------------------------------------------------- #

def iou(a, b) -> float:
    """两个**含端点**的像素框的 IoU。"""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    x1, y1 = max(ax1, bx1), max(ay1, by1)
    x2, y2 = min(ax2, bx2), min(ay2, by2)
    if x2 < x1 or y2 < y1:
        return 0.0
    inter = (x2 - x1 + 1) * (y2 - y1 + 1)
    union = ((ax2 - ax1 + 1) * (ay2 - ay1 + 1)
             + (bx2 - bx1 + 1) * (by2 - by1 + 1) - inter)
    return inter / union if union else 0.0


def yolo_tokens(box, wh) -> tuple[float, float, float, float]:
    """像素框 → ``(cx, cy, w, h)``（归一化）。

    ``x2 / y2 是含的``：宽 = ``x2 - x1 + 1``。行带右沿正好等于面板右沿时，宽是 669
    不是 668 —— 少这 1px 每一行都窄半个像素，而且是**系统性**偏窄。
    中心用 ``(x1 + x2) / 2``（不是 ``(x1 + x2 + 1) / 2``）：与方案里量出来的那组数
    逐位一致，反解时补回这半像素（见 :func:`box_from_yolo`）。
    """
    w, h = float(wh[0]), float(wh[1])
    x1, y1, x2, y2 = (float(v) for v in box)
    return ((x1 + x2) / (2 * w), (y1 + y2) / (2 * h),
            (x2 - x1 + 1) / w, (y2 - y1 + 1) / h)


def yolo_line(cls: int, box, wh, *, digits: int = 6) -> str:
    """一行标签：``<cls> <cx> <cy> <w> <h>``，缺省 6 位小数（⇒ 反解误差 ≤ 0.5px）。"""
    cx, cy, w, h = yolo_tokens(box, wh)
    return f"{int(cls)} {cx:.{digits}f} {cy:.{digits}f} {w:.{digits}f} {h:.{digits}f}"


def box_from_yolo(cx: float, cy: float, bw: float, bh: float, wh):
    """``(cx, cy, w, h)`` → 像素框（含端点，浮点）。:func:`yolo_tokens` 的**精确反函数**。

    ``x1 + x2 = 2·cx·W``、``x2 - x1 + 1 = w·W`` 联立，再补回编码时省掉的那半像素 ——
    所以解出来的数与写进去的整数坐标逐位相等（不是「大约相等」）。
    """
    w, h = float(wh[0]), float(wh[1])
    hw, hh = bw * w / 2, bh * h / 2
    return (cx * w - hw + 0.5, cy * h - hh + 0.5,
            cx * w + hw - 0.5, cy * h + hh - 0.5)


def parse_label_line(line: str, wh) -> tuple[int, tuple[float, float, float, float]]:
    """标签文件里的一行 → ``(cls, 像素框)``。"""
    parts = line.split()
    if len(parts) != 5:
        raise ValueError(f"标签行要 5 个字段，这行有 {len(parts)} 个：{line!r}")
    cls = int(parts[0])
    cx, cy, bw, bh = (float(v) for v in parts[1:])
    return cls, box_from_yolo(cx, cy, bw, bh, wh)


def clip_box(box, wh):
    """夹进画面内；夹完不成框（宽或高 < 1）就返回 None —— 宁可少一个框，不写退化框。"""
    w, h = int(wh[0]), int(wh[1])
    x1, y1, x2, y2 = (int(round(float(v))) for v in box)
    x1, x2 = max(0, min(x1, w - 1)), max(0, min(x2, w - 1))
    y1, y2 = max(0, min(y1, h - 1)), max(0, min(y2, h - 1))
    if x2 < x1 or y2 < y1:
        return None
    return (x1, y1, x2, y2)


def dedupe_boxes(boxes, thr: float = 0.6) -> list:
    """同类框去重：按顺序保留，与已保留的任一框 IoU ≥ ``thr`` 就丢弃。

    同一批录屏被解析过两次（两个 run），同一条评论会出两次、框只差一两像素；
    这里判「是不是同一个框」用 IoU 而不是坐标相等 —— 两次 OCR 的框本来就不会逐位相同。
    """
    keep: list = []
    for b in boxes:
        if all(iou(b, k) < thr for k in keep):
            keep.append(b)
    return keep


def boxes_of_row(row: dict) -> list[tuple[int, tuple[int, int, int, int]]]:
    """一行 ``comments.jsonl`` → ``[(cls 编号, 像素框), …]``。没框的类不写（不猜）。"""
    out: list[tuple[int, tuple[int, int, int, int]]] = []
    for name, field in CLASSES:
        if field is None:
            for e in row.get("emojis") or []:
                box = e.get("box")
                if box:
                    out.append((CLASS_ID[name], tuple(int(v) for v in box)))
            continue
        box = row.get(field)
        if box:
            out.append((CLASS_ID[name], tuple(int(v) for v in box)))
    return out


# --------------------------------------------------------------------------- #
# split
# --------------------------------------------------------------------------- #

def normalize_split_rules(doc: dict | None) -> dict:
    """校验/规范化 split 规则表。写错一个字就报错，别静默全落 train。"""
    if not doc:
        doc = DEFAULT_SPLIT_RULES
    default = str(doc.get("default") or "train")
    if default not in SPLITS:
        raise ValueError(f"split 规则的 default 只能是 {SPLITS}，收到 {default!r}")
    rules = []
    for r in doc.get("rules") or []:
        match, split = r.get("match"), r.get("split")
        if not match or split not in SPLITS:
            raise ValueError(f"split 规则要 {{'match': 子串, 'split': {SPLITS}}}，收到 {r!r}")
        rules.append({"match": str(match), "split": str(split)})
    return {"default": default, "rules": rules}


def split_of(video: str, rules: dict) -> str:
    """视频 → split。先命中先用（规则顺序有意义），匹配不上落 default。"""
    text = str(video)
    for r in rules["rules"]:
        if r["match"] in text:
            return r["split"]
    return rules["default"]


# --------------------------------------------------------------------------- #
# 读写小工具
# --------------------------------------------------------------------------- #

def read_jsonl(path) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def write_jsonl(path, rows) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def append_jsonl(path, row: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
