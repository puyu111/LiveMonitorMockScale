"""livepipe.parsing.selftest.panels — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import sys

from ..ocr.grid import DEFAULT_PANELS
from ..ocr.overrides import resolve_panels
from ..panels.locate import locate_panel_rect
from ..panels.model import parse_model_boxes
from ..panels.model import scroll_area_from_rows



def _selftest_panels() -> bool:
    """面板定位纯函数（不碰模型/GPU）：模型框解析、评论行并集、内容无关地板框、矩形复用。"""
    cases = []
    # ① 模型答案是 0~1000 归一化坐标，且可能反序 ⇒ 必须 min/max 归一化，多框时取面积最大
    boxes = parse_model_boxes("<box><500><400><100><200></box><box><10><20><30><40></box>")
    cases.append(("模型框解析并按 min/max 归一化",
                  boxes == [[100, 200, 500, 400], [10, 20, 30, 40]]))
    cases.append(("无框返回空", parse_model_boxes("no box here") == []))

    # ② 评论行并集：左右留白分开给（左给徽章列 90，右给框外表情 pad_x），并裁到窗口内
    rows = [[500, 700, 900, 730], [520, 770, 1080, 800]]
    got = scroll_area_from_rows(rows, [0, 0, 1150, 1520], pad_left=90, pad_right=180, pad_y=16)
    cases.append(("评论行并集 + 左右留白分开", got == [410, 684, 1150, 816]))
    # 右沿超窗 ⇒ 裁到窗口边；不给 pad_right ⇒ 跟随 pad_left
    cases.append(("并集裁到窗口内",
                  scroll_area_from_rows([[1000, 700, 1140, 730]], [0, 0, 1150, 1520],
                                        pad_left=90, pad_right=180) == [910, 684, 1150, 746]))
    cases.append(("不给 pad_right 时跟随 pad_left",
                  scroll_area_from_rows([[500, 700, 900, 730]], [0, 0, 1150, 1520],
                                        pad_left=90)[0] == 410
                  and scroll_area_from_rows([[500, 700, 900, 730]], [0, 0, 1150, 1520],
                                            pad_left=90)[2] == 990))
    cases.append(("没有评论行 ⇒ None（调用方跳过该窗口）",
                  scroll_area_from_rows([], [0, 0, 1150, 1520]) is None))
    # ②b 内容无关的地板框：零评论行时也不丢面板。chat_box 是模型给的（worker=None 时不调
    # 模型，只验证纯几何拼装），所以这里直接把模型左沿喂进来。
    floor, fmeta = locate_panel_rect(None, None, [0, 0, 1150, 1520],
                                     chat_box=[575, 348, 1144, 1417], pad_top=80)
    cases.append(("零评论 ⇒ 内容无关几何框（x1 用模型左沿 / y1 用扫描线上移 / x2 用窗口右沿）",
                  floor == [575, 604, 1150, 1520] and fmeta.get("degraded") is True))
    cases.append(("几何框没有内容行可精修 ⇒ n_rows=0 且标了降级理由",
                  fmeta["n_rows"] == 0 and fmeta["reason"] == "no_comment_rows"
                  and fmeta["scan_y"] == 684))
    cases.append(("没有聊天面板框可依 ⇒ rect=None（不硬造）",
                  locate_panel_rect(None, None, [0, 0, 1150, 1520])[0] is None))
    cases.append(("模型左沿落到窗口外 ⇒ 退化框不如不给",
                  locate_panel_rect(None, None, [0, 0, 1150, 1520],
                                    chat_box=[1200, 348, 1300, 1417])[0] is None))
    # ③ 定位结果直接当 rect 用：走 resolve_panels(rects=…)，x_text 按绝对锚点重算
    panels = resolve_panels(rects={"left": [479, 668, 1096, 1429]})
    anchor = DEFAULT_PANELS["left"]["rect"][0] + DEFAULT_PANELS["left"]["x_text"]
    cases.append(("自动矩形当面板用 + x_text 重算",
                  panels["left"]["rect"] == [479, 668, 1096, 1429]
                  and panels["left"]["x_text"] == anchor - 479))
    cases.append(("只覆盖 left，right 保持内置",
                  panels["right"]["rect"] == DEFAULT_PANELS["right"]["rect"]))

    bad = [n for n, ok in cases if not ok]
    if bad:
        print(f"[selftest] 面板定位断言失败：{bad}", file=sys.stderr)
        return False
    print(f"[selftest] 面板定位/裁切 {len(cases)} 项断言通过 ✔")
    return True
