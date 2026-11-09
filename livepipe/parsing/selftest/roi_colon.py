"""livepipe.parsing.selftest.roi_colon — 分隔冒号小框与相邻续行守卫的断言。"""

from __future__ import annotations

from ..layout.paragraphs import parse_live_rows
from ..ocr.colon import _COLON_PROBE_RIGHT
from ..ocr.colon import colon_box_by_pixels
from ..ocr.colon import colon_box_in_row
from ..ocr.colon import insert_colon_by_pixels
from ..render import refresh_colon_box
from .roi_helpers import row


def _add_colon_cases(cases):
    # ⓪ 分隔冒号的小框（标注图圈它）：OCR 只给整块文字框 ⇒ 按「冒号是本块第几个字」
    #    在块宽里插值。昵称一块、正文一块的常见情形，冒号落在第二块开头。
    cblk = [(656, 752, 760, 781, "时死你", 0.99),
            (760, 752, 900, 781, "：真帅啊", 0.99)]
    cases.append(("冒号框按块内字符比例估（冒号在第二块开头 ⇒ 贴着它的左沿）",
                  colon_box_in_row(cblk, "时死你：真帅啊") == [760, 752, 795, 781]))
    cases.append(("没有冒号的行（续行/系统行）⇒ 不画框",
                  colon_box_in_row([(577, 819, 760, 849, "❤️❤️❤️", 0.99)],
                                   "❤️❤️❤️") is None))
    # 冒号落在**第一块中间**（昵称与正文被 OCR 并成一块）时，位置按字数往里推。
    cb = colon_box_in_row([(656, 752, 856, 781, "时死你：真帅啊", 0.99)], "时死你：真帅啊")
    cases.append(("同一块里也定位得到（冒号在第 4 个字 ⇒ 落在块内 3.5 个字宽处）",
                  cb is not None and cb[0] > 700 and cb[2] < 800))
    # 行序里带着的 colon_box 要一路进评论记录；续行合并**不动**首行那一份。
    r0 = row(0, "时死你：真帅啊", 656, 851, 752, 781)
    r0["colon_box"] = [700, 752, 716, 781]
    r1 = row(1, "❤️❤️❤️", 577, 760, 819, 849)
    r1["colon_box"] = [999, 999, 999, 999]          # 续行不该有这个，给了也不许覆盖首行
    ok0 = parse_live_rows([r0, r1])
    cases.append(("评论记录带 colon_box；续行合并保留首行那一份",
                  len(ok0) == 1 and ok0[0]["colon_box"] == [700, 752, 716, 781]))
    # 旧 rows.jsonl 没有 colon_box：按整行框插值当退路（不必为标注框重跑 OCR）。
    old = parse_live_rows([row(0, "时死你：真帅啊", 656, 851, 752, 781)])[0]["colon_box"]
    cases.append(("旧行序没有 colon_box ⇒ 按整行框插值补上（--rows 复算也能圈）",
                  old is not None and 656 <= old[0] < old[2] <= 851))
    cases.append(("没有冒号的行仍然不给框",
                  parse_live_rows([row(0, "❤️❤️❤️", 577, 760, 819, 849)])[0]
                  ["colon_box"] is None))

    # ⓪b 像素冒号（--video 抽行时的首选）：OCR 吃掉冒号也圈得到 —— 冒号的签名是
    #     「同一列上下两个小方点」，而汉字/数字是整块高得多的连通块。
    try:
        import cv2
        import numpy as np
    except ImportError:                 # 零依赖模式下跳过（纯文本/--rows 用不到像素）
        cv2 = None
    if cv2 is not None:
        band = np.full((44, 200, 3), 32, np.uint8)
        band[8:36, 4:30] = 235          # 昵称一个字（整块高）
        band[8:36, 120:148] = 235       # 正文第一个字
        band[18:23, 60:64] = 235        # 冒号上点
        band[26:31, 60:64] = 235        # 冒号下点
        pcb = colon_box_by_pixels(band, [0, 0, 200, 44])
        cases.append(("冒号按像素找（两个小方点，OCR 没认出来也圈得到）",
                      pcb is not None and pcb[0] <= 60 < pcb[2]
                      and pcb[1] >= 16 and pcb[3] <= 33))
        # 灰点抗锯齿更狠时下点只剩 2×1（面积 2）：面积闸写 3 会把下点整颗滤掉、点对
        # 凑不齐（实测 10.31 视频号 seg1 t=4637 `摸鱼的西红柿🍅：…`）。
        tiny = np.full((44, 200, 3), 32, np.uint8)
        tiny[8:36, 4:30] = 235              # 昵称一个字
        tiny[8:36, 120:148] = 235           # 正文首字
        tiny[18:20, 60:62] = 160            # 冒号上点 2×2（灰）
        tiny[26:27, 60:62] = 160            # 冒号下点只剩 2×1
        ptiny = colon_box_by_pixels(tiny, [0, 0, 200, 44],
                                    bg_max_v=255, v_delta=30)
        cases.append(("灰色冒号下点只剩 2×1 ⇒ 仍凑成点对（面积闸放到 2）",
                      ptiny is not None and ptiny[0] <= 60 < ptiny[2]))
        cases.append(("框里没有点对（只有整块字）⇒ 不给像素框",
                      colon_box_by_pixels(band, [0, 0, 50, 44]) is None))
        # 药丸行：OCR 文字块止于药丸右沿，冒号在块外那段版式空隙里（微博实测块右沿
        # 331 / 冒号 360）⇒ 只放宽右沿就找得到；不放宽就整类行拿不到冒号框。
        pcb_w = colon_box_by_pixels(band, [0, 0, 50 + _COLON_PROBE_RIGHT, 44])
        cases.append(("冒号在 OCR 块**外**（药丸行）⇒ 右沿放宽后找得到",
                      pcb_w is not None and pcb_w[0] <= 60 < pcb_w[2]))
        cases.append(("浅色底不给像素框（掩码前提不成立，退回插值）",
                      colon_box_by_pixels(np.full((44, 200, 3), 235, np.uint8),
                                          [0, 0, 200, 44]) is None))
        # 视频号手机端：昵称与冒号是灰色（正文才是纯白），实测 V≈126–145。
        # 亮度下限写死 150 会把这一对灰点滤掉 ⇒ 退回按字数插值，框成一个全角字符宽。
        gray = np.full((44, 200, 3), 32, np.uint8)
        gray[8:36, 4:30] = 235           # 正文/昵称里的亮块
        gray[18:23, 60:64] = 130         # 灰色冒号上点
        gray[26:31, 60:64] = 130         # 灰色冒号下点
        pcb_gray = colon_box_by_pixels(gray, [0, 0, 200, 44])
        cases.append(("灰色冒号（V≈130）也圈得到 —— 亮度下限不能写死 150",
                      pcb_gray is not None and pcb_gray[0] <= 60 < pcb_gray[2]))

        # 视频号的灰冒号点只有 2×2，抗锯齿后常「缺一个角」⇒ 面积 3。面积闸写 4 会整类
        # 漏掉（实测 seg2 的 `哎哎：…` / `星星：…` / `团子：…` 全落到字数插值）；点对几何
        # 仍然很严（同列 ±2px、同宽高、竖向间隙 3~14px），噪声凑不出这样一对。
        ldot = np.full((44, 200, 3), 32, np.uint8)
        ldot[10:20, 94:103] = 235        # 昵称末尾的字（整块，9×10）
        ldot[18, 108:110] = 140          # 上点：2×2 只亮 3 个像素（缺右脚）
        ldot[19, 108] = 140
        ldot[27, 108:110] = 140          # 下点同理
        ldot[28, 108] = 140
        pcb_l = colon_box_by_pixels(ldot, [0, 0, 200, 44])
        cases.append(("灰色 2×2 冒号点缺角（面积 3）也认得到 —— 面积闸不能写 4",
                      pcb_l is not None and pcb_l[0] <= 108 < pcb_l[2]))

        # 浅灰气泡底（直播画面亮、气泡半透明，A9L 实测背景 V≈146~189）：默认
        # bg_max_v=110 直接放弃像素法，冒号只能靠 OCR 认。平台档案放开两个阈值后，
        # 「两个 2px 小点、只比背景亮 30~55」的冒号也成对（A9L 实测上点 V≈205、
        # 下点 ≈165、背景 146）。
        light = np.full((44, 200, 3), 150, np.uint8)
        light[8:36, 4:30] = 240          # 昵称整块
        light[18:23, 60:64] = 205        # 冒号上点（比背景亮 55）
        light[26:31, 60:64] = 190        # 冒号下点（比背景亮 40）
        cases.append(("浅灰底：默认阈值下像素法放弃（旧行为不变）",
                      colon_box_by_pixels(light, [0, 0, 200, 44]) is None))
        pcb_light = colon_box_by_pixels(light, [0, 0, 200, 44],
                                        bg_max_v=255, v_delta=30)
        cases.append(("浅灰底 + 档案放开阈值 ⇒ 冒号照样圈得到",
                      pcb_light is not None and pcb_light[0] <= 60 < pcb_light[2]))

        # OCR 把冒号整个吃掉时，文本里没有「：」⇒ 用户名切不开。像素已找到位置，
        # 按字宽把冒号补回文本（opt-in，见 colon_text_fix）。
        cases.append(("像素冒号补回文本：星号后半角处插入「：」",
                      insert_colon_by_pixels("简***坐等后面的干货",
                                             [100, 1209, 360, 1240],
                                             [163, 1221, 173, 1234])
                      == "简***：坐等后面的干货"))
        # 整行昵称全是半角星号时，按字宽等分会把冒号算到正文首字之后
        # （实测 `T***落：地什么价位`）；星号串右沿才是昵称右沿。
        cases.append(("星号串右沿优先于字宽等分（不把正文首字并进用户名）",
                      insert_colon_by_pixels("T***落地什么价位",
                                             [105, 1291, 335, 1318],
                                             [163, 1300, 173, 1313])
                      == "T***：落地什么价位"))
        cases.append(("文本里已有冒号 ⇒ 不再插第二个",
                      insert_colon_by_pixels("简***：坐等", [100, 1209, 360, 1240],
                                             [163, 1221, 173, 1234]) == "简***：坐等"))

        # 昵称连同冒号被并进**药丸块**（抖音 t=191.2 实测：药丸块 `121***` 右沿 167、
        # 正文块左沿 188、冒号 163~173）⇒ 重画时向正文框左边多找一段，给这类行补框。
        # 只对显式写了 colon_* 的面板生效（params 才传进来）。
        band2 = np.full((44, 300, 3), 150, np.uint8)       # 浅灰气泡底（V=150）
        band2[8:36, 190:220] = 240                          # 正文首字
        band2[18:23, 146:150] = 205                         # 冒号上点
        band2[26:31, 146:150] = 190                         # 冒号下点
        c_add = {"text_box": [190, 0, 300, 44], "colon_box": None,
                 "username": None, "badge_text": "6许***", "panel": "comments"}
        refresh_colon_box(band2, c_add, {"colon_bg_max_v": 255, "colon_v_delta": 30})
        cases.append(("昵称+冒号被并进药丸块 ⇒ 重画时补冒号框（向左多找一段）",
                      c_add["colon_box"] is not None
                      and c_add["colon_box"][0] <= 146 < c_add["colon_box"][2]))
        c_nostar = {"text_box": [190, 0, 300, 44], "colon_box": None,
                    "username": None, "badge_text": "◆16", "panel": "comments"}
        refresh_colon_box(band2, c_nostar, {"colon_bg_max_v": 255, "colon_v_delta": 30})
        cases.append(("药丸块里没有掩码昵称 ⇒ 不补框（防误标）",
                      c_nostar["colon_box"] is None))
        c_noparams = {"text_box": [190, 0, 300, 44], "colon_box": None,
                      "username": None, "badge_text": "6许***", "panel": "comments"}
        refresh_colon_box(band2, c_noparams)
        cases.append(("没开 colon_* 的面板 ⇒ 不补框（既有语料行为不变）",
                      c_noparams["colon_box"] is None))

        # 掩码徽章的昵称被 badge_name_recover 拼成用户名后，文本里仍然没有冒号 ——
        # 「已有用户名就不补框」的守卫不能把这一整类挡掉（A9L 抖音 seg1 t=80.1 的
        # `4冰***` / `7哭***` / `8顺***` 三行就是这么丢的框）。
        c_named = {"text_box": [190, 0, 300, 44], "colon_box": None,
                   "username": "6许***", "badge_text": "6许***", "panel": "comments"}
        refresh_colon_box(band2, c_named, {"colon_bg_max_v": 255, "colon_v_delta": 30})
        cases.append(("掩码徽章已补回用户名 ⇒ 冒号框照补（用户名不是不补框的理由）",
                      c_named["colon_box"] is not None
                      and c_named["colon_box"][0] <= 146 < c_named["colon_box"][2]))
        c_named2 = {"text_box": [190, 0, 300, 44], "colon_box": None,
                    "username": "某甲", "badge_text": "◆16", "panel": "comments"}
        refresh_colon_box(band2, c_named2, {"colon_bg_max_v": 255, "colon_v_delta": 30})
        cases.append(("普通用户名 + 非掩码徽章 ⇒ 仍不补框（旧行为）",
                      c_named2["colon_box"] is None))

        # 昵称块**整块**没被 OCR 检出（视频号实测 t=106.4：`6**：比同级别合资强多了`
        # 的正文块左沿 106、x_text 30）⇒ 正文块离 x_text 很远本身就是证据，向左边补框。
        c_drop = {"text_box": [190, 0, 300, 44], "colon_box": None,
                  "username": None, "badge_text": None, "x_text": 30,
                  "panel": "comments"}
        refresh_colon_box(band2, c_drop, {"colon_bg_max_v": 255, "colon_v_delta": 30})
        cases.append(("正文块从 x_text 右边很远才开始（昵称整块丢了）⇒ 补冒号框",
                      c_drop["colon_box"] is not None
                      and c_drop["colon_box"][0] <= 146 < c_drop["colon_box"][2]))
        c_cont = {"text_box": [42, 0, 300, 44], "colon_box": None,
                  "username": None, "badge_text": None, "x_text": 30,
                  "panel": "comments"}
        refresh_colon_box(band2, c_cont, {"colon_bg_max_v": 255, "colon_v_delta": 30})
        cases.append(("续行（正文紧贴 x_text）⇒ 不补框（防误标）",
                      c_cont["colon_box"] is None))

        # masked_only：视频号昵称后紧跟的标点会被 OCR 一起吃掉，按字宽等分补会偏一格
        # （实测 `梁，：内饰看着简约时尚` 读成 `梁内饰看着简约时尚`、`平安：边吃饭边看`
        # 读成 `平安边吃饭边看`）⇒ 只认打码星号锚点；单星号（视频号常见）也认。
        cases.append(("masked_only：单星号打码昵称照插（苗苗*）",
                      insert_colon_by_pixels("苗苗*同价位很能打",
                                             [43, 1271, 274, 1298],
                                             [105, 1282, 113, 1294],
                                             masked_only=True)
                      == "苗苗*：同价位很能打"))
        cases.append(("masked_only：没有星号锚点 ⇒ 不赌等分（不把正文首字并进用户名）",
                      insert_colon_by_pixels("梁内饰看着简约时尚",
                                             [44, 1198, 338, 1224],
                                             [118, 1207, 128, 1220],
                                             masked_only=True)
                      == "梁内饰看着简约时尚"))
        cases.append(("默认（masked_only 关）保持旧行为：等分仍会插",
                      insert_colon_by_pixels("梁内饰看着简约时尚",
                                             [44, 1198, 338, 1224],
                                             [118, 1207, 128, 1220])
                      == "梁内饰：看着简约时尚"))

        # 昵称块与正文块之间夹着表情（表情与冒号都不是「文字」，OCR 不检）⇒ 块缝实测
        # 42px。冒号像素落在缝里时插在**块边界**：按字宽等分会把表情（≈2 个汉字宽）
        # 摊进字宽、把「：」推进正文一个字（10.31 视频号 seg1 t=4637
        # `摸鱼的西红柿🍅：在泉州…` 插成 `摸鱼的西红柿在：泉州…`）。
        gap_blocks = [(34, 1076, 156, 1097, "摸鱼的西红柿", 0.99),
                      (198, 1076, 478, 1097, "在泉州开车逛古城，走走停停正", 0.99)]
        cases.append(("冒号落在块缝里 ⇒ 插在块边界（表情宽度不影响落点）",
                      insert_colon_by_pixels(
                          "摸鱼的西红柿在泉州开车逛古城，走走停停正",
                          [34, 1076, 478, 1097], [179, 1084, 187, 1094],
                          masked_only=True, blocks=gap_blocks)
                      == "摸鱼的西红柿：在泉州开车逛古城，走走停停正"))
        cases.append(("冒号不在块缝里 ⇒ 块锚点不生效（masked_only 下不插）",
                      insert_colon_by_pixels(
                          "摸鱼的西红柿在泉州开车逛古城，走走停停正",
                          [34, 1076, 478, 1097], [300, 1084, 308, 1094],
                          masked_only=True, blocks=gap_blocks)
                      == "摸鱼的西红柿在泉州开车逛古城，走走停停正"))

        # 抖音手机版的打码昵称：`***` 是**半角**、正文是汉字，按字数等分会把冒号往右推
        # （实测 t=0 的 `恬***：给力`：等分给 145、真冒号 135，差 10px；梦***差 17、
        # 一***差 20）—— 按字符宽度加权（全角 1 / 半角 0.55）后落到 133，圈得住。
        cases.append(("打码昵称（半角星号）按字宽加权插值，冒号不再被推到正文首字上",
                      colon_box_in_row([(78, 806, 196, 834, "恬***：给力", 0.99)],
                                       "恬***：给力") == [133, 806, 154, 834]))

        # 星号贴着冒号：星号右沿 132、冒号左沿 135，只差 3px ⇒「孤立」判据把这一整类
        # 真冒号全挡掉（用户报的「冒号识别错误，大量」就是这个）。插值给的 hint 能救回来。
        aster = np.full((44, 200, 3), 32, np.uint8)
        aster[8:17, 124:132] = 235       # 昵称末尾的半角星号（8×9，贴冒号 3px）
        aster[14:18, 135:139] = 235      # 冒号上点
        aster[22:26, 135:139] = 235      # 冒号下点
        aster[8:36, 160:190] = 235       # 正文首字
        cases.append(("星号贴着冒号（差 3px）⇒ 孤立判据找不到，这是旧行为",
                      colon_box_by_pixels(aster, [0, 0, 200, 44]) is None))
        pcb_hint = colon_box_by_pixels(aster, [0, 0, 200, 44], hint=140.0)
        cases.append(("给了插值 hint ⇒ 贴着字的真冒号也认得到（只认 hint 附近的那一对）",
                      pcb_hint is not None and pcb_hint[0] <= 135 < pcb_hint[2]))
        # 同一个行带里还有一对「歪打正着」的点（昵称里的 氵 之类）：hint 在右边 ⇒ 挑右边的
        # 真冒号，不会被左边那对更早被找到的骗走。
        aster2 = aster.copy()
        aster2[14:18, 86:90] = 235
        aster2[20:24, 86:90] = 235
        pcb2 = colon_box_by_pixels(aster2, [0, 0, 200, 44], hint=140.0)
        cases.append(("hint 附近有多对点时挑最近的（氵 那对不被选中）",
                      pcb2 is not None and pcb2[0] <= 135 < pcb2[2]))

    # ① 相邻的续行 ⇒ 合并，ROI 取并集，右边界扩到面板边（1146）
    #    **续行的 x1 在 x_text 左边**（实测 t=252：'念口：一集一集解锁跟闯关一样' 折行后
    #    '有成就感，我好喜欢…' 从面板左沿 577 起）—— 这正是它「没有徽章」的几何表现，
    #    也是 _has_own_badge 的判据。写成 656 就变成「带徽章的新评论」了，测不到续行。
    ok1 = parse_live_rows([row(0, "时死你：真帅啊", 656, 851, 752, 781),
                           row(1, "❤️❤️❤️", 577, 760, 819, 849)])
    cases.append(("相邻续行合并", len(ok1) == 1 and ok1[0]["lines"] == 2
                  and ok1[0]["roi"] == [577, 752, 1146, 849]
                  and ok1[0]["content"] == "真帅啊 ❤️❤️❤️"))
    # ② 槽位不相邻（中间隔着别的行）⇒ **不合并**，各自成条
    ok2 = parse_live_rows([row(0, "时死你：真帅啊", 656, 851, 752, 781),
                           row(3, "大家研究一下", 577, 880, 950, 980)])
    cases.append(("槽位不相邻不合并", len(ok2) == 2 and ok2[1]["username"] is None))
    # ③ 关掉守卫 ⇒ 回到纯文本行为（会合并）
    ok3 = parse_live_rows([row(0, "时死你：真帅啊", 656, 851, 752, 781),
                           row(3, "大家研究一下", 577, 880, 950, 980)],
                          require_adjacent_socket=False)
    cases.append(("关掉守卫则合并", len(ok3) == 1 and ok3[0]["lines"] == 2))
    # ④ right_pad=None ⇒ ROI 保持纯文字框（不扩到表情区）
    ok4 = parse_live_rows([row(0, "时死你：真帅啊", 656, 851, 752, 781)], right_pad=None)
    cases.append(("right_pad=None 不扩", ok4[0]["roi"] == [656, 752, 851, 781]))
    # ⑤ 丢行要断开续接链：通知行夹在中间，后面的无冒号行不该并进上一条
    ok5 = parse_live_rows([row(0, "时死你：真帅啊", 656, 851, 752, 781),
                           row(1, "灵** 赞了直播", 656, 851, 819, 849),
                           row(2, "❤️❤️❤️", 577, 760, 886, 916)])
    cases.append(("丢行断开续接链（孤行降级为匿名评论而非并入）",
                  len(ok5) == 2 and ok5[0]["lines"] == 1
                  and ok5[1]["username"] is None and ok5[1]["content"] == "❤️❤️❤️"))
    # ⑤b 微博开播公告的**残句尾巴**：整段公告被折行切成几块（都是「系统行」，丢掉），
    #     最后一块只剩「失。」，不含任何短语、只能按形状认。丢它的收益是**别让它把下面
    #     那条真评论吞掉** —— 它自成一匿名评论的话，'用户6861943086榜二这车这么稳'
    #     （冒号被 OCR 吃了 ⇒ 判成续行）就会并进来，变成 '失。 用户6861…'。
    ok5b = parse_live_rows([row(0, "欢迎使用微博直播服务。微博直播禁止包含色情、违", 40, 773, 600, 630),
                            row(1, "物袋按钮，请勿私下交易，请谨慎判断以防人身财产损", 40, 805, 674, 704),
                            row(2, "失。", 38, 98, 748, 790),
                            row(3, "用户6861943086榜二这车这么稳", 41, 547, 822, 856)])
    cases.append(("系统行的残句尾巴跟着丢，下面的真评论不被吞",
                  len(ok5b) == 1 and ok5b[0]["content"] == "用户6861943086榜二这车这么稳"))
    # ⑤c **形状本身不算数**：同一句「失。」，前面是别的丢弃行（抖音通知，不是系统行）
    #     ⇒ 它是有内容的孤行，照旧留成匿名评论。全量抖音语料里这种「≤4 字 + 句末标点」的
    #     无冒号行有 27/56 条是真评论的折行残片（'看。' '造。' '得真好！'）—— 只按形状丢
    #     就是真丢数据。
    ok5c = parse_live_rows([row(0, "时死你：真帅啊", 656, 851, 752, 781),
                            row(1, "灵** 赞了直播", 656, 851, 819, 849),
                            row(2, "失。", 38, 98, 886, 916)])
    cases.append(("形状判据只在系统行之后生效（前面是抖音通知 ⇒ 留成匿名评论）",
                  len(ok5c) == 2 and ok5c[1]["username"] is None and ok5c[1]["content"] == "失。"))

    # ⑤d 系统行的**折行续块**：槽位是小数（group_rows 过了间距闸）+ 紧跟丢掉的那一行
    #     ⇒ 整块都是系统行的另一半。微博开播公告实测被折成 5 块，只有第一块命中短语
    #     （「欢迎使用微博直播服务…」），后面几块没有特征词、也接不上任何评论 —— 修这个
    #     之前它们自成一匿名评论，用户报的标注图 00039.4 第 1 框就是它（4 行并成一条）。
    ok5d = parse_live_rows([
        row(7, "欢迎使用微博直播服务。微博直播禁止包含色情、违", 40, 773, 600, 630),
        row(7.5, "法、侵权等性质内容，对于发布违规内容的用户，微博", 40, 804, 649, 680),
        row(8.5, "物袋按钮，请勿私下交易，请谨慎判断以防人身财产损", 40, 805, 694, 727),
        row(9.5, "失。", 38, 98, 748, 790),
        row(10, "用户6861943086榜二这车这么稳", 41, 547, 822, 856),
    ], rank_pill=True)
    cases.append(("系统行折行后的无特征词续块跟着丢（公告不再自成一匿名评论）",
                  len(ok5d) == 1 and ok5d[0]["content"] == "这车这么稳"))

    # ⑤e 同一条门的另一现场：进房通知「…进入直播间」自己是一整块系统行，它下面那条
    #     折行槽位的「雪夜初霁进入了直播间」也照旧收进去（用户报的同一张图第 3 框）。
    ok5e = parse_live_rows([
        row(10, "用户6861943086榜二这车这么稳", 41, 547, 822, 856),
        row(11, "吃一口橙子ing通过直播预约进入直播间", 43, 621, 887, 920),
        row(11.5, "雪夜初霁进入了直播间", 44, 367, 923, 946),
    ], rank_pill=True)
    cases.append(("紧跟进房通知的折行续块也丢（不再变成匿名『评论』）",
                  len(ok5e) == 1 and ok5e[0]["content"] == "这车这么稳"))

    # ⑤f 闸门是「上一条丢掉的是**系统行**」＋「小数槽位」：整数槽位的孤行照旧留成
    #     匿名评论（那是「昵称被 OCR 丢了」的真评论，短形状判据也不敢丢，见 ⑤c）。
    ok5f = parse_live_rows([
        row(7, "欢迎使用微博直播服务。微博直播禁止包含色情、违", 40, 773, 600, 630),
        row(8, "大家研究一下", 40, 300, 649, 680),
    ], rank_pill=True)
    cases.append(("整数槽位的孤行不跟丢（没有折行几何 ⇒ 可能是昵称丢了的真评论）",
                  len(ok5f) == 1 and ok5f[0]["username"] is None
                  and ok5f[0]["content"] == "大家研究一下"))

    # ⑤g 通知行**没有自己的整数行**（group_rows 只把它挂成小数槽位、紧跟评论下面）时，
    #     它走不到 classify_line ⇒ 会被当折行续行并进上一条评论的正文。实测 t=44.1
    #     「这车这么稳」后面粘了两条进房通知。无冒号 + 短语表已经量过（445 行全是通知）。
    ok5g = parse_live_rows([
        row(10, "用户6861943086榜二这车这么稳", 41, 547, 822, 856),
        row(10.5, "雪夜初霁进入了直播间", 45, 366, 880, 906),
        row(11.5, "打酱油100通过直播预约进入直播间", 41, 562, 910, 940),
    ], rank_pill=True)
    cases.append(("没有自己行槽的通知（折行槽位）不并进上一条评论的正文",
                  len(ok5g) == 1 and ok5g[0]["content"] == "这车这么稳"))

    # ⑤h 反过来：没命中通知短语的折行续行照旧并进正文，别把这条门扩大成「小数槽位一律丢」。
    ok5h = parse_live_rows([
        row(10, "用户6861943086榜二这车这么稳", 41, 547, 822, 856),
        row(10.5, "真的稳得很", 45, 366, 880, 906),
    ], rank_pill=True)
    cases.append(("普通折行续行照旧并进正文",
                  len(ok5h) == 1 and ok5h[0]["content"] == "这车这么稳 真的稳得很"))
