"""livepipe.parsing.selftest.roi_grouping — 行分组、徽章几何、并块拆分的断言。"""

from __future__ import annotations

import json
from pathlib import Path

from ..comments.input import parse_rows_any
from ..common.text import norm_text
from ..constants import BADGE_SPAN
from ..layout.paragraphs import parse_live_rows
from ..layout.roi import _roi_of
from ..layout.roi import badge_left_of
from ..layout.roi import refresh_badge_left
from ..ocr.fused import assemble_row
from ..ocr.fused import badge_box_of
from ..ocr.fused import badge_text_of
from ..ocr.fused import split_fused_rows
from ..ocr.grid import DEFAULT_PANELS
from ..ocr.grouping import group_rows
from ..ocr.overrides import resolve_panels
from ..ocr.row_ids import _row_key
from ..text.badges import _badge_digits
from ..text.classify import classify_line
from .roi_helpers import blk
from .roi_helpers import row


def _add_grouping_cases(cases):

    # ⑥ 徽章列过滤必须带**内容**判据（bug-204）。OCR 块左沿在 x_text 左边有两种清白的
    #    情况，丢它就是丢整条评论：徽章被并进正文块（'2。hubvg:' x1=34）、用户没徽章
    #    昵称直接贴面板最左（'梁红：' x1=9）。只有纯徽章碎片（'3'）才该丢。
    panel = {"row_pitch": 68, "y_phase": 15, "x_text": 70}


    kept = {s: assemble_row(b)[0] for s, b in group_rows(
        [blk("3", 48, 71, 15),                      # ① 独立徽章（槽0）⇒ 丢
         blk("2。hubvg:", 34, 219, 83),             # ② 徽章并进正文（槽1）⇒ 留
         blk("梁红：", 9, 81, 151)], panel).items()}   # ③ 无徽章贴左（槽2）⇒ 留
    cases.append(("徽章列只丢纯徽章碎片，不丢整行正文",
                  0 not in kept and kept.get(1) == "2。hubvg:" and kept.get(2) == "梁红："
                  and classify_line(kept.get(1) or "") == ("comment", ("hubvg", ""))))

    # ⑥b 右端条带里的纯 ASCII 短碎片（品牌水印/logo 被 OCR 读出来的）要丢；正文列里的
    #     同形块要留 —— 两条一起才是「不敢误伤的英文续行」那条边界（bug-261 连带）。
    panel_r = {"row_pitch": 68, "y_phase": 15, "x_text": 60, "rect": [1202, 1045, 2560, 1332]}
    kept_b = {s: assemble_row(b)[0] for s, b in group_rows(
        [blk("CHER", 1219, 1261, 15),        # 右端条带（1219 ≥ 1358×0.75≈1018）⇒ 丢
         blk("CHER", 80, 122, 83),           # 正文列的同形块 ⇒ 留
         blk("打酱油：真帅", 130, 302, 151)],  # 正文 ⇒ 留
        panel_r).items()}
    cases.append(("右端条带里的纯 ASCII 碎片（水印）丢、正文列的同形块留",
                  kept_b.get(0) is None and kept_b.get(1) == "CHER"
                  and kept_b.get(2) == "打酱油：真帅"))

    # ⑦ x_text 是裁切内坐标，换矩形必须按绝对锚点重算，否则徽章列整体右移、
    #    徽章块逃过滤网被拼进正文（实测 hybrid 矩形下用户名变成 `13打酱油`）。
    import tempfile
    d = DEFAULT_PANELS["left"]
    anchor = d["rect"][0] + d["x_text"]                     # 633，与矩形无关
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "p.json"
        f.write_text(json.dumps({"panels": {"left": [575, 669, 1150, 1423]}}))
        got = resolve_panels(str(f))["left"]["x_text"]
        cases.append(("换矩形后 x_text 按绝对锚点重算", got == anchor - 575 == 58))
        f.write_text(json.dumps({"panels": {"left": {"rect": [575, 669, 1150, 1423],
                                                       "x_text": 70}}}))
        cases.append(("显式给了 x_text 就不重算",
                      resolve_panels(str(f))["left"]["x_text"] == 70))
        #    **行距不归整**：它是「槽位 = 相位 + 槽号 × 行距」里逐行累加的间距 —— 手机 App 版
        #    实测 53.5，归整成 54 后每下一行多漂 0.5px，二十行就是 10px（0.19 个行距），网格线
        #    正好落到两行中间 ⇒ 两条评论并成一行（用户报的 t=107.2 第 7 条）。坐标/相位照旧归整。
        f.write_text(json.dumps({"panels": {"left": {"rect": [575, 669, 1150, 1423],
                                                       "row_pitch": 53.5,
                                                       "y_phase": 17.6}}}))
        pl = resolve_panels(str(f))["left"]
        cases.append(("行距保留小数（53.5 不归整成 54）；矩形与相位照旧归整",
                      pl["row_pitch"] == 53.5 and pl["y_phase"] == 18
                      and pl["rect"] == [575, 669, 1150, 1423]))

    # ⑦b 折行是**几何事实**，不许被文本判据否决：小数槽位只有一个来源 —— ``group_rows``
    #    里的块过了间距闸（``_is_true_wrapped_line``）才挂上的（见 ``_wrapped_socket``）。
    #    主播那种两行评论的第二行以时间开头、带冒号（`18：45盛大启幕！敬请期待！`），
    #    文本层看着就是「昵称 18 ：正文 45盛大启幕…」，交给 ``classify_line`` 就会多出一条
    #    凭空的评论（用户报的帧上正是这样，一条评论被拆成两条、还都带着框）。
    okw = parse_live_rows([row(4, "主播风云新能源汽车：风云T7上市发布会", 109, 559, 1020, 1047),
                           row(4.5, "18：45盛大启幕！敬请期待！", 41, 340, 1057, 1082)])
    cases.append(("小数槽位（几何上已证明是折行）即便带冒号也并进上一条，不另起评论",
                  len(okw) == 1 and okw[0]["username"] == "主播风云新能源汽车"
                  and okw[0]["content"] == "风云T7上市发布会 18：45盛大启幕！敬请期待！"
                  and okw[0]["lines"] == 2))

    # ⑧ 等级徽章要一起圈进 ROI：徽章块被 group_rows 从正文丢掉（它不是内容），但 ROI
    #    的左边界取它 —— 否则出图/裁图从昵称开始，等级标看不见。右边界同理由 x_right 兜住表情。
    ok8 = parse_live_rows([row(0, "时死你：真帅啊", 656, 851, 752, 781, badge=True)])[0]
    cases.append(("ROI 左边界扩到等级徽章（text_box 不动）",
                  ok8["roi"] == [601, 752, 1146, 781]
                  and ok8["text_box"] == [656, 752, 851, 781]
                  and ok8["badge_box"] == [601, 752, 632, 781]))
    ok8b = parse_live_rows([row(0, "时死你：真帅啊", 656, 851, 752, 781)])[0]
    cases.append(("没有徽章几何时 ROI 保持纯文字框",
                  ok8b["roi"] == [656, 752, 1146, 781] and ok8b["badge_box"] is None))
    # 续行合并：ROI 取并集，但**首行的徽章**要留着（续行自己没有徽章）
    ok8c = parse_live_rows([row(0, "时死你：真帅啊", 656, 851, 752, 781, badge=True),
                            row(1, "❤️❤️❤️", 577, 760, 819, 849)])[0]
    cases.append(("续行合并后徽章仍在 ROI 内",
                  ok8c["lines"] == 2 and ok8c["roi"] == [577, 752, 1146, 849]
                  and ok8c["badge_box"] == [601, 752, 632, 781]))
    # ⑧d 行**有自己的徽章**（文字从徽章列右边开始）⇒ 一律不并，哪怕它没有冒号。
    #     OCR 常把昵称末尾的冒号吃掉（'緣起：' → '緣起'），旧判据「没冒号 ⇒ 续行」于是把
    #     一条新评论并进上一条的正文里 —— 全片 614 个「被并的第二行」**全部**属于这一类
    #     （bug-228）。下面这对断言把判据钉在**几何**上，而不是文本上。
    ok8d = parse_live_rows([row(0, "风调雨顺：666 666 666", 653, 938, 1220, 1257, badge=True),
                            row(1, "緣起", 653, 720, 1290, 1321, badge=True)])
    cases.append(("带徽章的行不并入上一条（冒号被 OCR 吃掉也不并）",
                  len(ok8d) == 2 and ok8d[0]["content"] == "666 666 666"
                  and ok8d[1]["username"] is None and ok8d[1]["content"] == "緣起"))
    ok8e = parse_live_rows([row(0, "风调雨顺：666 666 666", 653, 938, 1220, 1257, badge=True),
                            row(1, "緣起", 577, 720, 1290, 1321)])
    cases.append(("同样的文本，没有徽章几何时按续行并入",
                  len(ok8e) == 1 and ok8e[0]["lines"] == 2
                  and ok8e[0]["content"] == "666 666 666 緣起"))
    # ⑧f 缩进续行要有**最小缩进量**（``_INDENT_MIN_PX``）：昵称列左沿自己就有 1~2px 的
    #     OCR 抖动（微博实测同帧极差中位 4 / 最大 9px），只判「比首行右移」就等于把抖动
    #     当缩进 —— 一屏互不相干的评论被串成一条（用户报的 t=3124.2：3 条并成 1 条、
    #     4 条并成 1 条，画出来的 ROI 一框圈三行）。真续行的缩进是**一个昵称宽**
    #     （抖音竖屏 656 → 720+），两者差着量级。
    ok8f = parse_live_rows([row(0, "画危鸭：这车前脸真好看", 656, 851, 752, 781),
                            row(1, "这车这么稳", 658, 851, 820, 849)])       # 抖 2px
    cases.append(("昵称列的 1~2px 抖动不算缩进（不并，各留各的）",
                  len(ok8f) == 2 and ok8f[0]["lines"] == 1
                  and ok8f[0]["content"] == "这车前脸真好看"
                  and ok8f[1]["username"] is None and ok8f[1]["content"] == "这车这么稳"))
    ok8g = parse_live_rows([row(0, "念口：一集一集解锁跟闯关一柱", 656, 851, 752, 781),
                            row(1, "有成就感，我好喜欢", 720, 851, 820, 849)])  # 缩 64px
    cases.append(("真缩进（一个昵称宽）仍按续行并入",
                  len(ok8g) == 1 and ok8g[0]["lines"] == 2
                  and ok8g[0]["username"] == "念口"
                  and ok8g[0]["content"] == "一集一集解锁跟闯关一柱 有成就感，我好喜欢"))
    # 徽章块确实被记录进 badge_boxes（而不是静默丢掉）
    pn2 = {"row_pitch": 68, "y_phase": 15, "x_text": 70}
    bb: dict = {}
    kept2 = group_rows([blk("3", 48, 71, 15)], pn2, bb)          # 纯徽章碎片，x1 < x_text
    cases.append(("被丢的徽章块按槽位记录（框 + 读数：供 ROI 与等级标对账用）",
                  kept2 == {} and bb == {0: [[48, 0, 71, 30, "3"]]}))
    cases.append(("不传 badge_boxes 时行为不变（兼容旧调用）",
                  group_rows([blk("3", 48, 71, 15)], pn2) == {}))
    # 徽章常被 OCR 切成两块（◆ 图标 + 数字）⇒ 按 x 拼回一串再取数字，等级标才读得出来。
    bb2: dict = {}
    group_rows([blk("◆", 48, 60, 15), blk("11", 62, 80, 15)], pn2, bb2)
    cases.append(("徽章被切成两块（◆ + 11）⇒ 按 x 拼成 `◆11`，取数字得 11",
                  badge_text_of(bb2, 0) == "◆11" and _badge_digits(badge_text_of(bb2, 0)) == "11"
                  and badge_box_of(bb2, 0) == [48, 0, 80, 30]))
    cases.append(("徽章读数的数字按 Unicode 类别取：＋29 / ２９ / ） 都认 / 取不到则空",
                  _badge_digits("＋29") == "29" and _badge_digits("２９") == "29"
                  and _badge_digits("）") == "" and _badge_digits(None) == ""))
    # ⑨ 徽章没被 OCR 单独检出（实测左面板 50 行里只有 25 行检出）⇒ 用 badge_left 兜底，
    #    否则一半的框从昵称开始，等级标圈不进去。三者取最小：徽章框 < 徽章列左沿 < 正文左沿。
    ok9 = parse_live_rows([row(0, "时死你：真帅啊", 656, 851, 752, 781, badge_left=595)],
                          right_pad=None)[0]
    cases.append(("没检出徽章块时用徽章列左沿兜底",
                  ok9["roi"] == [595, 752, 851, 781] and ok9["badge_box"] is None))
    ok9b = parse_live_rows([row(0, "梁红：真帅啊", 575, 851, 752, 781, badge_left=595)],
                           right_pad=None)[0]
    cases.append(("无徽章用户（昵称顶到面板左边）取正文左沿",
                  ok9b["roi"][0] == 575))
    # ⑩ BADGE_SPAN 是量出来的：x_text 的绝对锚点 633 往左 58 = 575，正好压在**自动定位
    #    拿到的面板左沿**上 —— 圈住徽章又不越出裁切区。锚点来自手工面板（bug-214），
    #    这里把「锚点 - 徽章列宽 = 575」钉死，免得日后调锚点忘了同时调宽度。
    cases.append((f"BADGE_SPAN 与 x_text 锚点自洽（633 - {BADGE_SPAN} = 575）",
                  633 - BADGE_SPAN == 575))
    # ⑪ badge_span 是**平台档案**的量：桌面版 58 刚好落在药丸左沿；手机 App 的 x_text
    #    在药丸右沿**外侧**，58 只到药丸中段（ROI 切在 ◆ 上）⇒ douyin_app 写 74。
    #    现场（风云T7）：rect x1=10 / x_text=96 / 药丸墨迹 x∈[22,95] ⇒ ROI 左沿要退到 22。
    pn_app = {"row_pitch": 53.5, "y_phase": 10, "x_text": 96, "badge_span": 74}
    pn_seg7 = {"row_pitch": 43.5, "y_phase": 5, "x_text": 86, "badge_span": 74}
    cases.append(("badge_span：手机 App 的 ROI 左沿退到药丸墨迹左沿（缺省 58 会切在药丸中段）",
                  badge_left_of(10, pn_app) == 32
                  and badge_left_of(10, pn_seg7) == 22
                  and badge_left_of(10, {"x_text": 96}) == 48))
    # ⑫ 旧行序（badge_left 是抽行时的 48）走 --rows 复算：按当前档案重算成 32，
    #    面板名对不上的行不动（不能因为解析只看当前面板就丢掉别的段的行）。
    old_rows = [{"panel": "comments", "x_text": 106, "badge_left": 48,
                 "box": [102, 0, 300, 30]},
                {"panel": "other", "x_text": 106, "badge_left": 48,
                 "box": [102, 0, 300, 30]}]
    n_fix = refresh_badge_left(old_rows, {"comments": {"rect": [10, 0, 700, 400],
                                                       "x_text": 96, "badge_span": 74}})
    cases.append(("旧行序按当前档案重算 badge_left（ROI 左沿 48 → 32）",
                  n_fix == 1 and old_rows[0]["badge_left"] == 32
                  and old_rows[1]["badge_left"] == 48))
    # ⑬ 纯表情评论：正文是图片，OCR 框只剩打码昵称那一小截（实测 h=10）⇒ ROI 必须把
    #    表情框也算进来，否则圈出来是一条细缝、表情全在框外（用户报的「没圈出来」）。
    cases.append(("ROI 含表情框（纯表情评论的文字框只有 10px 高）",
                  _roi_of({"box": [121, 1056, 155, 1066], "badge_box": None,
                           "badge_left": 32,
                           "emojis": [{"box": [189, 1047, 292, 1083]}]}) == [32, 1047, 292, 1083]
                  and _roi_of({"box": [121, 1056, 155, 1066], "badge_left": 32}) == [32, 1056, 155, 1066]))

    # ⑭ 纯表情评论的行**要留下**：OCR 只读出打码昵称的残渣（``***``）时，这行以前判
    #    「碎片行」丢掉 —— 表情明明认出来了（``emojis`` 有 token），评论却不存在、
    #    标注图上也不圈。通知/系统行不能借这个口子进来（它们也常带表情）。
    def erow(sock, text, tokens):
        r = row(sock, text, 30, 300, sock * 68, sock * 68 + 30)
        r["emojis"] = [{"box": [240, sock * 68, 300, sock * 68 + 30], "token": tk, "n": 1}
                       for tk in tokens]
        return r

    emo_only = parse_live_rows([erow(0, "***", ["[爱心]"] * 3)], max_username_len=24)
    cases.append(("纯表情评论（OCR 只剩 '***'）留着：正文空、emoji_text 补内容",
                  len(emo_only) == 1 and emo_only[0]["username"] is None
                  and emo_only[0]["content"] == ""
                  and emo_only[0]["emoji_text"] == "[爱心][爱心][爱心]"))
    cases.append(("系统行/通知即便带表情也不当纯表情评论",
                  parse_live_rows([erow(0, "欢迎小明来了", ["[玫瑰]"])],
                                  max_username_len=24) == []))
    cases.append(("纯表情行的跨帧身份键退化到 emoji_text（空 text 不参与滚动对齐）",
                  _row_key({"text": "", "emoji_text": "[爱心][爱心]"}) == norm_text("[爱心][爱心]")
                  and _row_key({"text": "咕***：", "emoji_text": "[赞]"}) == norm_text("咕***：")))

    # ⑭c 面板档案缺失不能让整条复算崩掉：``--only parse`` 本来由 build_parse_args 补写
    #     ``panels.resolved.json``，可它也可能**跑到一半**被删（两个进程共用一个输出目录，
    #     实测 2026-10-26 崩在 seg3，后面六段的产出全丢）。读不到就退化成「不重算
    #     badge_left、面板闸全关」，行序照样解析出评论 —— 派生文件不该带走整条 OCR。
    class _NoPanels:
        sep = "："
        max_username_len = 24
        no_merge = False
        rank_pill = fan_badge = badge_alias = overlay_cards = None
        right_pad = 0
        no_adjacency = False
        panels_file = "/nonexistent/段/panels.resolved.json"
    kept = parse_rows_any([erow(0, "***", ["[爱心]"])], _NoPanels())
    cases.append(("面板档案不存在 ⇒ 跳过 badge_left 重算、照常解析（不抛 FileNotFoundError）",
                  len(kept) == 1 and kept[0]["emoji_text"] == "[爱心]"))

    # ⑭e 一个槽里并了两行（用户报 t=107.2 第 7 条「2 个评论变一行」）：两条相邻评论的行中心
    #    本来差一个行距，网格线正卡在中间时两行各自偏 ~0.5 行距、都「没超」，``group_rows``
    #    就归进同一槽 ⇒ 一个框圈两行、正文粘成一串。拆回来靠几何：块高 > 1.3 行距且按 y 聚成
    #    两簇 ⇒ 上簇留本槽、下簇进整数槽 k+1。**没有冒号的下簇也照拆**（昵称被遮挡的行是真
    #    评论，如「外观真耐看」）；折行续行间距 ≤0.73 行距、块高进不了 1.3，压根到不了这里。
    fp = {"row_pitch": 54, "y_phase": 0}

    def fblk(x1, y1, x2, y2, text):
        return (x1, y1, x2, y2, text, 0.99)

    fused = {7: [fblk(40, 413, 336, 440, "苏***：续航真够跑"),
                 fblk(40, 466, 336, 492, "晨***：座椅支撑到位")]}
    got = split_fused_rows(fused, fp)
    cases.append(("并块行拆回两条评论（下簇有昵称冒号 ⇒ 整数槽 k+1）",
                  [(s, assemble_row(b)[0]) for s, b in got] ==
                  [(7, "苏***：续航真够跑"), (8, "晨***：座椅支撑到位")]))
    cases.append(("并块行里下簇没有昵称冒号也照拆成独立一条（匿名评论，不当续行并进上一条）",
                  [(s, assemble_row(b)[0]) for s, b in
                   split_fused_rows({8: [fblk(95, 413, 210, 440, "晨***：座椅支撑到位"),
                                         fblk(96, 466, 220, 492, "外观真耐看")]}, fp)] ==
                  [(8, "晨***：座椅支撑到位"), (9, "外观真耐看")]))
    cases.append(("并块的下簇若在 k+1 已有碎块 ⇒ 并回去（并块常只吞掉下一条评论的最左一块）",
                  [(s, assemble_row(b)[0]) for s, b in
                   split_fused_rows({4: [fblk(92, 216, 405, 243, "够***：T7这智驾，同级领先"),
                                         fblk(99, 269, 139, 291, "主播")],
                                     5: [fblk(150, 267, 425, 294, "风云新能源汽车：风云T7")]}, fp)]
                  == [(4, "够***：T7这智驾，同级领先"),
                      (5, "主播风云新能源汽车：风云T7")]))
    cases.append(("下簇要去的槽 k+1 坐的是**另一条行**（yc 差 ≈ 一个行距）⇒ 整槽不拆，保持原样",
                  split_fused_rows({3: [fblk(37, 197, 373, 228, "追***：颜值高"),
                                        fblk(37, 251, 373, 280, "11用***：这是在4s店直播？")],
                                    4: [fblk(99, 305, 140, 326, "主播"),
                                        fblk(150, 302, 548, 330,
                                             "风云新能源汽车：风云T7上市发布会")]}, fp)
                  == [(3, [fblk(37, 197, 373, 228, "追***：颜值高"),
                           fblk(37, 251, 373, 280, "11用***：这是在4s店直播？")]),
                      (4, [fblk(99, 305, 140, 326, "主播"),
                           fblk(150, 302, 548, 330,
                                "风云新能源汽车：风云T7上市发布会")])]))
    #    ⑭f 目标槽 k+1 坐的是**下簇这一行的折行续行**（用户报的视频号 seg1 t≈57「两个评论
    #       识别为一个」）：``恋恋：…`` 与 ``和和睦睦：…`` 被平滑滚动帧的半行距相位并进同一
    #       槽，而下簇要去的 k+1 坐着和睦睦的续行 ``来蹲守``（y 差 0.6 个行距、无冒号、贴左
    #       沿）⇒ 旧闸「yc 差 > 0.4 行距 ⇒ 不拆」把续行当成了另一条行，两条评论粘成一条。
    #       新规则：续行不是障碍，顺延到小数槽 k+1.5（它本来就排在首行后面），照拆。
    wp = {"row_pitch": 54, "y_phase": 30, "x_text": 30, "wrap_indent": True}

    got = split_fused_rows({2: [fblk(43, 94, 462, 121, "恋恋：关注很久了，今天特地过来看看"),
                                fblk(93, 142, 137, 165, "粉丝"),
                                fblk(146, 143, 509, 167, "和和睦睦：等这款车好久了，今天")],
                            3: [fblk(27, 172, 105, 201, "来蹲守")],
                            4: [fblk(44, 219, 267, 244, "晚风Ec：买库存去了")]}, wp)
    cases.append(("并块下簇的折行续行坐在 k+1 ⇒ 照拆，续行顺延到 k+1.5（两个评论不粘连）",
                  [(s, assemble_row(b)[0]) for s, b in got] ==
                  [(2, "恋恋：关注很久了，今天特地过来看看"),
                   (3, "粉丝和和睦睦：等这款车好久了，今天"),
                   (3.5, "来蹲守"),
                   (4, "晚风Ec：买库存去了")]))
    cases.append(("k+1 的块带冒号（是另一条评论，不是续行）⇒ 仍旧整槽不拆",
                  split_fused_rows({2: [fblk(43, 94, 462, 121, "恋恋：关注很久了，今天特地过来看看"),
                                        fblk(146, 143, 509, 167, "和和睦睦：等这款车好久了，今天")],
                                    3: [fblk(44, 172, 267, 201, "晚风Ec：买库存去了")]}, wp)
                  == [(2, [fblk(43, 94, 462, 121, "恋恋：关注很久了，今天特地过来看看"),
                           fblk(146, 143, 509, 167, "和和睦睦：等这款车好久了，今天")]),
                      (3, [fblk(44, 172, 267, 201, "晚风Ec：买库存去了")])]))
    cases.append(("单行里的碎块（同一 y，空档不到 0.3 行距）不拆",
                  split_fused_rows({5: [fblk(40, 413, 336, 440, "主播"),
                                        fblk(340, 411, 480, 441, "风云新能源汽车：风云T7")]},
                                   fp) == [(5, [fblk(40, 413, 336, 440, "主播"),
                                                fblk(340, 411, 480, 441,
                                                     "风云新能源汽车：风云T7")])]))
    cases.append(("正常单行不动（高度只有一行）",
                  split_fused_rows({3: [fblk(40, 413, 336, 440, "黄***：风云T7")]}, fp)
                  == [(3, [fblk(40, 413, 336, 440, "黄***：风云T7")])]))
