"""``livepipe.tools.dataset.selftest`` —— 纯函数自检（不读盘、不要 cv2/Pillow）。"""

from __future__ import annotations

from .common import (CLASS_NAMES, box_from_yolo, boxes_of_row, clip_box, dedupe_boxes,
                     frame_index, frame_name, iou, normalize_split_rules, review_name,
                     split_of, DEFAULT_SPLIT_RULES, video_key, yolo_line, yolo_tokens)
from .labels import check_lines, frame_lines
from .sources import labelable


class _R:
    __slots__ = ("row",)

    def __init__(self, row):
        self.row = row


class _U:
    """最小「单元」替身：``frame_boxes`` 只认 ``.name`` 与 ``.recs[].row``。"""

    def __init__(self, name, rows):
        self.name = name
        self.recs = [_R(r) for r in rows]


def selftest() -> int:
    n = 0

    def eq(got, want, what):
        nonlocal n
        n += 1
        assert got == want, f"{what}: 得到 {got!r}，期望 {want!r}"

    def near(got, want, tol, what):
        nonlocal n
        n += 1
        assert abs(got - want) <= tol, f"{what}: 得到 {got!r}，期望 {want}±{tol}"

    # 帧号 = round(t × fps)（与 sample_frames_adaptive 同一把尺子）
    eq(frame_index(0.0, 35.774306399902436), 0, "idx(0)")
    eq(frame_index(292.55633590784424, 35.774306399902436), 10466, "idx(292.556) = 10466")
    eq(frame_index(261.86, 35.774306399902436), 9368, "idx(261.86) = 9368")

    # 视频键 / 文件名
    eq(video_key("/x/12：01-13：00.mp4"), "12-01-13-00", "全角冒号 → -")
    eq(video_key("17:20-18:19.mp4"), "17-20-18-19", "半角冒号 → -")
    eq(video_key("a/b c?.mp4"), "b-c", "非法字符并成 -")
    eq(frame_name("12-01-13-00", 10466), "12-01-13-00_f010466", "图名")
    eq(review_name("12-01-13-00", 10466, 3), "12-01-13-00_f010466_03.jpg", "复核图名")

    # 类表：等级标不进数据集（badge_box 缺框率太高）
    eq(CLASS_NAMES, ("comment_row", "body_text", "colon", "emoji_run"), "四类")

    # 标签源：username / colon_box 缺一个就不进数据集
    eq(labelable({"username": "王***", "colon_box": [165, 1412, 175, 1424]}), True, "有昵称+冒号")
    eq(labelable({"username": None, "colon_box": [165, 1412, 175, 1424]}), False, "缺昵称")
    eq(labelable({"username": "王***", "colon_box": None}), False, "缺冒号")
    eq(labelable({"username": "  ", "colon_box": [165, 1412, 175, 1424]}), False, "昵称是空白")

    # 折算：方案 §02 里那四条，逐位对齐
    wh = (720, 1600)
    eq(yolo_line(0, [32, 1400, 700, 1430], wh), "0 0.508333 0.884375 0.929167 0.019375", "comment_row")
    eq(yolo_line(1, [101, 1400, 462, 1430], wh), "1 0.390972 0.884375 0.502778 0.019375", "body_text")
    eq(yolo_line(2, [165, 1412, 175, 1424], wh), "2 0.236111 0.886250 0.015278 0.008125", "colon")
    eq(yolo_line(3, [50, 1405, 95, 1425], wh), "3 0.100694 0.884375 0.063889 0.013125", "emoji_run")
    eq(yolo_tokens([32, 1400, 700, 1430], wh)[2], 669 / 720, "宽按 x2 含端点：669 不是 668")

    # 反解：精确反函数
    for box in ([32, 1400, 700, 1430], [50, 1405, 95, 1425], [0, 0, 719, 1599],
                [101, 1400, 462, 1430], [165, 1412, 175, 1424]):
        line = yolo_line(0, box, wh)
        got = box_from_yolo(*(float(v) for v in line.split()[1:]), wh)
        n += 1
        assert all(abs(a - b) <= 0.5 for a, b in zip(got, box)), f"反解 {box} → {got}"

    # IoU / 去重
    eq(iou([0, 0, 10, 10], [0, 0, 10, 10]), 1.0, "同一框 IoU=1")
    eq(iou([0, 0, 10, 10], [20, 0, 30, 10]), 0.0, "不相交 IoU=0")
    near(iou([0, 0, 10, 10], [0, 0, 10, 9]), 110 / 121, 1e-12, "差一行（含端点：11×11 对 11×10）")
    eq(len(dedupe_boxes([(0, 0, 10, 10), (0, 0, 10, 9), (50, 50, 60, 60)])), 2, "IoU≥0.6 只留一个")

    # 裁剪
    eq(clip_box([-5, -5, 800, 1700], wh), (0, 0, 719, 1599), "夹进画面")
    eq(clip_box([10, 10, 9, 20], wh), None, "夹完不成框 ⇒ None")
    eq(clip_box([10.4, 20.6, 110.5, 50.4], (4000, 4000)), (10, 21, 110, 50), "浮点取整")

    # split：按视频、先命中先用
    rules = normalize_split_rules(DEFAULT_SPLIT_RULES)
    eq(split_of("/d/微博/16：38-17：22.mp4", rules), "test", "微博 → test")
    eq(split_of("/d/抖音/17：13-17：33.mp4", rules), "val", "17：13-17：33 → val")
    eq(split_of("/d/抖音/12：01-13：00.mp4", rules), "train", "其余 → train")
    for bad in ({"default": "dev"}, {"rules": [{"match": "x", "split": "dev"}]}):
        n += 1
        try:
            normalize_split_rules(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"坏规则该报错：{bad}")

    # 一行评论 → 框（表情逐个展开；badge_box 不再进数据集）
    row = {"roi": [32, 1400, 700, 1430], "text_box": [101, 1400, 462, 1430],
           "colon_box": None, "badge_box": [50, 1405, 95, 1425],
           "emojis": [{"box": [600, 1400, 620, 1430]}, {"box": [630, 1400, 650, 1430]}]}
    got = [CLASS_NAMES[c] for c, _ in boxes_of_row(row)]
    eq(got, ["comment_row", "body_text", "emoji_run", "emoji_run"], "badge_box 不再进数据集")

    # 一帧多单元：同一条的两次解析要在标签里合成一个框
    a = {"roi": [32, 1400, 700, 1430], "text_box": [101, 1400, 462, 1430],
         "colon_box": [165, 1412, 175, 1424], "emojis": []}
    a2 = dict(a, roi=[33, 1400, 700, 1430])          # 另一个 run 少一像素
    b = {"roi": [32, 1470, 700, 1500], "text_box": [101, 1470, 462, 1500],
         "colon_box": None, "emojis": []}
    lines = frame_lines([_U("u1", [a, a2]), _U("u2", [b])], wh)
    eq(len(lines), 5, "两条评论 × (行带+正文) + 一条冒号 = 5 行（a 与 a2 合成一个框）")
    eq([l.split()[0] for l in lines], ["0", "0", "1", "1", "2"], "先按类排，再按上沿")
    near(float(lines[0].split()[3]), 669 / 720, 1e-5, "合并后仍取第一条的框")

    # 体检：重复框要被抓出来
    eq(check_lines(lines, wh), [], "正常标签没有抱怨")
    dup = [yolo_line(0, [32, 1400, 700, 1430], wh)] * 2
    eq(bool(check_lines(dup, wh)), True, "同类重复框 ⇒ 报错")

    print(f"自检 {n} 项全过 ✅")
    return 0
