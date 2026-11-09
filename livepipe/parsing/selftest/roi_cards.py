"""livepipe.parsing.selftest.roi_cards — 浮层卡、粉丝药丸、掩码昵称等版式守卫的断言。"""

from __future__ import annotations

import re

from ..comments.build import comments_from_rows
from ..comments.overlays import frozen_overlay_texts
from ..layout.paragraphs import parse_live_rows
from ..text.notices import overlay_card_re
from .roi_helpers import row


def _add_card_cases(cases):

    # ⑤d 视频号的「粉丝」药丸徽章被 OCR **并进正文** ⇒ 昵称被啃成「粉丝踏雪归来」。
    #     闸门是几何（实测：药丸行 x1 ∈ [32,35]、真昵称行 x1 ∈ [38,45]，取 37 分界）。
    def fanrow(sock, text, x1):
        r = row(sock, text, x1, 200, 1043 + sock * 50, 1076 + sock * 50)
        r["x_text"] = 30          # 视频号手机 App 的正文列（抖音夹具用的 633 是桌面版的）
        return r

    ok5d = parse_live_rows([fanrow(0, "粉丝踏雪归来：", 33)], fan_badge_x_max=37)
    cases.append(("『粉丝』并进正文 ⇒ 剥掉（药丸文字框在徽章区里）",
                  len(ok5d) == 1 and ok5d[0]["username"] == "踏雪归来"))
    # ⑤e **同一个形状、但文字框在昵称列**（没顶到徽章区）⇒ 不剥：真昵称就叫这个。
    #     只按文本剥会把它的头两个字啃掉，而这个名字跟他在别的帧里的读法对不上 ⇒ 算成两个人。
    ok5e = parse_live_rows([fanrow(0, "粉丝团团长：早上好", 43)], fan_badge_x_max=37)
    cases.append(("真昵称以『粉丝』开头（文字框在昵称列）⇒ 一个字都不啃",
                  len(ok5e) == 1 and ok5e[0]["username"] == "粉丝团团长"))
    # ⑤f 没配这个键的面板（抖音/微博）行为一字不变。
    ok5f = parse_live_rows([fanrow(0, "粉丝踏雪归来：", 33)], fan_badge_x_max=None)
    cases.append(("没配 fan_badge_x_max ⇒ 不剥（别的平台不受影响）",
                  len(ok5f) == 1 and ok5f[0]["username"] == "粉丝踏雪归来"))
    # ⑤g 药丸**自己成块**（OCR 没并进正文）⇒ 整行丢掉，且不计入内容。
    #     不丢的话它会被当续行粘到上一条评论的尾巴上（「粉丝」不是 junk）。
    ok5g = parse_live_rows([fanrow(0, "西西：有福利吗", 43),
                            fanrow(1, "粉丝", 33),
                            fanrow(2, "国货之光", 33)], fan_badge_x_max=37)
    cases.append(("药丸自己成块 ⇒ 丢掉并断开续接链（不当续行粘上去）",
                  len(ok5g) == 2 and ok5g[0]["content"] == "有福利吗"
                  and ok5g[1]["content"] == "国货之光" and ok5g[1]["sockets"] == [2]))

    # ⑤h 「等级药丸 + 粉丝药丸」**一起**并进正文（风云A9L 的三个视频号账号，2026-11-01）：
    #     `+6粉丝若云：…` 的块左沿 ~90（药丸在药丸右边），文本判据 `^粉丝` 也匹配不上。
    #     平台档案显式开 `fan_badge_with_level` 才剥（既有语料的面板没写这个键）。
    ok5h = parse_live_rows([fanrow(0, "+6粉丝若云：对这车挺感兴趣", 90)],
                           fan_badge_x_max=100, fan_badge_with_level=True)
    cases.append(("等级药丸+粉丝药丸一起并进正文 ⇒ 两个前缀都剥掉",
                  len(ok5h) == 1 and ok5h[0]["username"] == "若云"))
    ok5i = parse_live_rows([fanrow(0, "+6粉丝若云：对这车挺感兴趣", 90)],
                           fan_badge_x_max=100)
    cases.append(("没开 fan_badge_with_level ⇒ 保持旧行为（不剥）",
                  len(ok5i) == 1 and (ok5i[0]["username"] or "").endswith("粉丝若云")))

    # ⑤j 顶部 UI 通知与列表第一行**并成一块**（风云A9L 的视频号账号实测）：
    #     `非主驾开播请注意驾驶安全微微：家用车靠谱最重要` ⇒ 用户名会带着整条通知。
    #     平台档案给 `notice_prefix_strip` 才剥；只有「剥完还剩分隔冒号」才动手。
    np_rx = re.compile("开播请注意驾驶安全")
    ok5j = parse_live_rows([fanrow(0, "非主驾开播请注意驾驶安全微微：家用车靠谱最重要", 39)],
                           notice_prefix=np_rx)
    cases.append(("顶部通知与首行评论粘连 ⇒ 剥掉通知前缀",
                  len(ok5j) == 1 and ok5j[0]["username"] == "微微"))
    ok5k = parse_live_rows([fanrow(0, "非主驾开播请注意驾驶安全", 39)], notice_prefix=np_rx)
    cases.append(("只有通知本行 ⇒ 不在这里剥（仍交给 overlay_cards 丢弃）",
                  len(ok5k) == 1 and ok5k[0]["username"] is None))
    ok5l = parse_live_rows([fanrow(0, "闲来无事，看看新车咋样驾开播请注意驾驶安全", 39)],
                           notice_prefix=np_rx)
    cases.append(("通知粘在句尾（没有冒号）⇒ 不动文本",
                  len(ok5l) == 1 and "开播请注意" in (ok5l[0]["content"] or "")))
    # 三种前缀层层粘连（实测 `+1非主驾开播请注意驾驶安全粉丝专**：技术确实到位`）：
    # 先剥通知、再剥等级+粉丝药丸，顺序不能反（fan 剥离要拿**剥过通知的文本**）。
    ok5m = parse_live_rows([fanrow(0, "+1非主驾开播请注意驾驶安全粉丝专**：技术确实到位", 39)],
                           fan_badge_x_max=100, fan_badge_with_level=True,
                           notice_prefix=np_rx)
    cases.append(("通知 + 等级药丸 + 粉丝药丸层层粘连 ⇒ 全部剥掉",
                  len(ok5m) == 1 and ok5m[0]["username"] == "专**"))

    # ⑤n 等级药丸 + 掩码昵称被并成一个块（`12小***`）⇒ 昵称只剩在 badge_text 里。
    #     形状明确才恢复；`121***`（等级/昵称首字都是数字）歧义不猜。
    def brow(text, badge):
        r = row(0, text, 100, 200, 1120, 1148)
        r["x_text"] = 96
        r["badge_text"] = badge
        r["badge_box"] = [60, 1122, 150, 1146]
        return r

    ok5n = parse_live_rows([brow("家用代步适合吗", "12小***")], badge_name_recover=True)
    cases.append(("药丸块里的明确昵称拼回用户名",
                  len(ok5n) == 1 and ok5n[0]["username"] == "小***"
                  and ok5n[0]["content"] == "家用代步适合吗"))
    ok5o = parse_live_rows([brow("家用代步适合吗", "121***")], badge_name_recover=True)
    cases.append(("等级/昵称首字都是数字的歧义形状不猜（用户名留空）",
                  len(ok5o) == 1 and ok5o[0]["username"] is None))
    ok5p = parse_live_rows([brow("家用代步适合吗", "12小***")])
    cases.append(("没开 badge_name_recover ⇒ 不恢复（既有语料不变）",
                  len(ok5p) == 1 and ok5p[0]["username"] is None))
    ok5q = parse_live_rows([brow("期待了", "V***")], badge_name_recover=True)
    cases.append(("等级没读出来、只有掩码昵称（`V***`）⇒ 昵称照样恢复",
                  len(ok5q) == 1 and ok5q[0]["username"] == "V***"))
    ok5r = parse_live_rows([brow("风云的车性价比一直都在线", "9吸***·")], badge_name_recover=True)
    cases.append(("尾巴粘着冒号残迹（`9吸***·`）⇒ 剥掉残迹后恢复",
                  len(ok5r) == 1 and ok5r[0]["username"] == "吸***"))

    # ⑤s 本场专用词（商品卡文字被 OCR 接在行尾）：只剥**后缀**，整行都是它时当不存在。
    ok5s = parse_live_rows([row(0, "白日梦游家：孙正16人已预约", 102, 582, 1313, 1342)],
                           strip_phrases=["16人已预约", "艾瑞泽8PRO"])
    cases.append(("行尾的专用词剥掉，评论保留",
                  len(ok5s) == 1 and ok5s[0]["username"] == "白日梦游家"
                  and ok5s[0]["content"] == "孙正"))
    ok5t = parse_live_rows([row(0, "艾瑞泽8PRO真好看", 102, 400, 1313, 1342)],
                           strip_phrases=["16人已预约", "艾瑞泽8PRO"])
    cases.append(("正文中间的真提及不动（只剥后缀）",
                  len(ok5t) == 1 and ok5t[0]["content"] == "艾瑞泽8PRO真好看"))
    ok5u = parse_live_rows([row(0, "艾瑞泽8PRO", 479, 594, 1285, 1308)],
                           strip_phrases=["16人已预约", "艾瑞泽8PRO"])
    cases.append(("整行都是专用词 ⇒ 当作这一行不存在", ok5u == []))

    # ⑤u2 整行跳过词（`skip_phrases`）：公众号卡 / 主题卡整行匹配丢掉；正文提及不动。
    def vrow(sock, text):
        r = row(sock, text, 40, 400, 804 + sock * 50, 830 + sock * 50)
        r["x_text"] = 30          # 视频号手机 App 的正文列
        return r

    ok5u2 = parse_live_rows([vrow(0, "奇瑞汽车>"), vrow(1, "小兔子乖乖：大家好啊")],
                            skip_phrases=["奇瑞汽车>"])
    cases.append(("整行跳过词：公众号卡行丢掉，下一行评论保留",
                  len(ok5u2) == 1 and ok5u2[0]["username"] == "小兔子乖乖"))
    ok5u3 = parse_live_rows([vrow(0, "公众号 奇瑞汽车>")],
                            skip_phrases=["公众号奇瑞汽车>"])
    cases.append(("整行跳过词忽略空白（`公众号 奇瑞汽车>` 也算同一行）", ok5u3 == []))
    ok5u4 = parse_live_rows([vrow(0, "小王：奇瑞汽车真不错")],
                            skip_phrases=["奇瑞汽车>"])
    cases.append(("正文提到品牌名的真评论不受整行跳过词影响",
                  len(ok5u4) == 1 and ok5u4[0]["content"] == "奇瑞汽车真不错"))
    ok5u5 = parse_live_rows([vrow(0, "奇瑞汽车>")])
    cases.append(("没配 skip_phrases ⇒ 旧行为（卡行照旧进来）",
                  len(ok5u5) == 1 and ok5u5[0]["content"] == "奇瑞汽车>"))

    # ⑤v 纯表情续行：OCR 无文字、只有表情的行并进上一条评论（opt-in）。
    e0 = row(0, "铿锵玫瑰：", 103, 210, 918, 946)
    e0["emojis"] = [{"token": "[赞]", "n": 5, "score": 0.9, "box": [231, 916, 423, 948]}]
    e0["emoji_text"] = "[赞][赞][赞][赞][赞]"
    e1 = row(1, "", 124, 436, 958, 992)
    e1["emojis"] = [{"token": "[赞]", "n": 2, "score": 0.9, "box": [124, 959, 196, 991]},
                    {"token": "[鼓掌]", "n": 6, "score": 0.9, "box": [203, 958, 436, 992]}]
    e1["emoji_text"] = "[鼓掌][鼓掌][鼓掌][鼓掌][鼓掌][鼓掌][赞][赞]"
    ok5v = parse_live_rows([e0, e1], merge_emoji_continuation=True)
    cases.append(("纯表情续行并进上一条评论（lines=2，表情接在后面）",
                  len(ok5v) == 1 and ok5v[0]["lines"] == 2
                  and "鼓掌" in (ok5v[0].get("emoji_text") or "")))
    ok5w = parse_live_rows([e0, e1])
    cases.append(("没开 merge_emoji_continuation ⇒ 保持旧行为（各成一条）",
                  len(ok5w) == 2))
    # ⑤x `昵称：` + 纯表情：classify_line 因「冒号后没字」判成续行 ⇒ 会粘到上一条评论。
    #     开了 merge_emoji_continuation 后改判成一条「正文为表情」的评论。
    n0 = row(-1, "彭奶奶：", 39, 125, 870, 895)
    n0["emojis"] = [{"token": "[玫瑰]", "n": 1, "score": 0.9, "box": [148, 863, 171, 899]}]
    n0["emoji_text"] = "[玫瑰]"
    ok5x = parse_live_rows([n0], merge_emoji_continuation=True)
    cases.append(("`昵称：` 的纯表情行 ⇒ 是一条评论，不是续行",
                  len(ok5x) == 1 and ok5x[0]["username"] == "彭奶奶"
                  and ok5x[0]["content"] == "" and "[玫瑰]" in ok5x[0]["emoji_text"]))
    n1 = row(0, "囍财", 181, 231, 1218, 1243)          # 从正文列起头、没有冒号
    n1["x_text"] = 96
    n1["emojis"] = [{"token": "[比心]", "n": 2, "score": 0.9, "box": [344, 1213, 407, 1249]}]
    n1["emoji_text"] = "[比心][比心]"
    ok5y = parse_live_rows([n1], merge_emoji_continuation=True)
    cases.append(("正文列起头的短昵称 + 表情 ⇒ 纯表情评论",
                  len(ok5y) == 1 and ok5y[0]["username"] == "囍财"
                  and ok5y[0]["content"] == ""))
    # ⑤z 专用词剥离**没命中**时不能顺手改文本：`彭奶奶：` 的冒号被削掉就会判成续行、
    #     把不同人的评论粘成一条（实测 t=155.1）。
    ok5z = parse_live_rows([row(0, "彭奶奶：", 39, 125, 870, 895)],
                           strip_phrases=["16人已预约"])
    cases.append(("专用词没命中 ⇒ 文本一字不动（冒号还在、仍是一条评论）",
                  len(ok5z) == 1 and ok5z[0]["username"] == "彭奶奶"))

    # ⑤g2 **视频号浮层卡**（`_OVERLAY_CARD_RE`，2026-10-12 CTCC 实测）：压在评论列表上的
    #     UI 卡片不是评论 —— 可它长在评论 rect 里、又没有冒号，不判就被当匿名评论收进来
    #     （seg1 的 90s 样本里 172 行有 140 行是这种卡）。开关挂在平台档案上（`overlay_cards`）。
    def wrow(sock, text, x1, x2, y1, y2, panel="comments"):
        return {"t": 0.0, "panel": panel, "socket": sock, "text": text,
                "box": [x1, y1, x2, y2], "x_text": 30, "x_right": 720,
                "badge_box": None, "badge_left": None}

    card_rows = [wrow(7, "主题CTCC职业联赛鄂尔多斯站", 35, 393, 1122, 1152),
                 wrow(8, "2026CTCC中国汽车场地职业联赛", 27, 396, 1152, 1182),
                 wrow(9, "公众号奇瑞汽车>", 38, 242, 1199, 1229)]
    ok5g2 = parse_live_rows([dict(r) for r in card_rows], overlay_cards=overlay_card_re())
    cases.append(("浮层卡：主题卡两行（第二行没有特征词，靠「卡片续行」）+ 公众号卡 ⇒ 一条不出",
                  len(ok5g2) == 0))
    ok5g2b = parse_live_rows([dict(r) for r in card_rows], overlay_cards=None)
    cases.append(("没配 overlay_cards 的面板 ⇒ 浮层卡照旧进来（抖音/微博行为一字不变）",
                  len(ok5g2b) == 2))
    # 卡片续行**只吃一行**：浮层卡是叠着排的（seg1 t=0 四张卡的行槽位首尾相接），
    # 无限续链会把最下面那张卡底下紧挨着的真评论一起吃掉。
    ok5g3 = parse_live_rows([wrow(2, "HER奇瑞汽车占击了解详情", 34, 292, 1270, 1300),
                             wrow(3, "奇瑞汽车", 40, 200, 1300, 1330),
                             wrow(4, "这车真好看啊", 43, 300, 1350, 1380)],
                            overlay_cards=overlay_card_re())
    cases.append(("卡片续行只吃紧跟的一行（第三行照收，不许顺着往下续链）",
                  len(ok5g3) == 1 and ok5g3[0]["content"] == "这车真好看啊"))

    # ⑤g2b **素材补的短语**（``overlay_cards`` 给正则时，见 ``overlay_card_re``）：广告气泡
    #      的品牌行没有任何通用特征词。实测 seg2 t=20.07（真几何）：品牌行 ``奇瑞汽车``
    #      在 x=137、上一条真评论到 351 ⇒ 缩进成立，只能靠**短语**认，否则它要么并进上一条
    #      真评论、要么自成一匿名评论。而 ``奇瑞汽车`` 不能进内置表（三套语料里 25/40 行是
    #      真评论的折行续行 ``奇瑞汽车老品牌，值得信赖``）⇒ 挂在素材配置上。
    ad_rows = [wrow(9, "哎哎：这颜色真好看啊", 44, 290, 1199, 1225),
               wrow(10, "奇瑞汽车", 137, 240, 1270, 1300)]
    ok5g6 = parse_live_rows([dict(r) for r in ad_rows], overlay_cards=overlay_card_re(),
                            row_pitch=50.6)
    cases.append(("广告气泡的品牌行：内置表认不出（不当成卡，但也绝不并进上一条真评论）",
                  len(ok5g6) == 2 and ok5g6[0]["content"] == "这颜色真好看啊"
                  and ok5g6[1]["content"] == "奇瑞汽车"))
    ok5g7 = parse_live_rows([dict(r) for r in ad_rows],
                            overlay_cards=overlay_card_re("奇瑞汽车"), row_pitch=50.6)
    cases.append(("素材配置补上品牌名 ⇒ 品牌行也当卡丢掉",
                  len(ok5g7) == 1 and ok5g7[0]["content"] == "这颜色真好看啊"))
    ok5g8 = parse_live_rows([wrow(9, "哎哎：这颜色真好看啊", 43, 291, 1226, 1256),
                             wrow(10, "告奇瑞汽车", 52, 241, 1297, 1328)],
                            overlay_cards=overlay_card_re(r"^广?告[^知]"), row_pitch=50.6)
    cases.append(("只剩「广告」标签那一帧（``告奇瑞汽车``）也能认",
                  len(ok5g8) == 1 and ok5g8[0]["content"] == "这颜色真好看啊"))

    # ⑤g2c **缩进续行还要过间距闸**（``_INDENT_MAX_GAP_RATIO``）：昵称被 OCR 整块吃掉时
    #      （视频号把昵称打码成 ``**``、那两帧一个星号都没读出来），**独立评论**的正文正好
    #      落在昵称列右边，形状和折行一模一样。用**行距**（面板档案 ``row_pitch``，实测 50.6）
    #      量间距：独立评论 ≈1 个行距、折行 ≈0.75。下面两组都用 seg2 的**真几何**。
    pitch_rows = [wrow(6, "萌萌：五站八冠，这统治力可以", 43, 389, 1097, 1125),
                  wrow(7, "线下有品鉴会就好了", 109, 335, 1146, 1176)]      # 间距 50 = 0.99 行距
    ok5g9 = parse_live_rows([dict(r) for r in pitch_rows], row_pitch=50.6)
    cases.append(("两条独立评论（间距 ≈1 个行距，正文恰好缩进）⇒ 不许并成一条",
                  len(ok5g9) == 2 and ok5g9[0]["lines"] == 1
                  and ok5g9[1]["username"] is None
                  and ok5g9[1]["content"] == "线下有品鉴会就好了"))
    wrap_rows = [wrow(6, "萌萌：五站八冠，这统治力可以", 43, 389, 1097, 1125),
                 wrow(7, "线下有品鉴会就好了", 109, 335, 1135, 1163)]       # 间距 38 = 0.75 行距
    ok5g10 = parse_live_rows([dict(r) for r in wrap_rows], row_pitch=50.6)
    cases.append(("同样的缩进、间距贴着首行（0.75 行距）⇒ 照旧当折行并进去",
                  len(ok5g10) == 1 and ok5g10[0]["lines"] == 2))
    # 三行评论：第三行要拿**最后一行**（不是整条并集的中心）当基准，否则多行评论会被截断。
    ok5g11 = parse_live_rows([wrow(6, "念口：一集一集解锁跟闯关一柱", 43, 380, 1045, 1073),
                              wrow(7, "有成就感，我好喜欢，兄弟们下个广告见！", 109, 380, 1083, 1111),
                              wrow(8, "真的很好看", 109, 380, 1122, 1150)], row_pitch=50.6)
    cases.append(("三行折行：第三行按「最后一行」量间距 ⇒ 三行都得并上",
                  len(ok5g11) == 1 and ok5g11[0]["lines"] == 3))

    # ⑤g2d **浮层卡的第二条判据：列表滚了它没动**（``frozen_overlay_texts``）。短语表认不出
    #      品牌行（素材一换就失效），但气泡**不跟着列表滚**是通用的。下面用 8pro seg2/seg6 的
    #      **真几何**造 5 帧：评论每次整行距往下滚（+50.5），气泡行（品牌行 y=1284 + CTA
    #      y=1320）钉死不动。CTA 那行 ``了解详情`` 内置表也会认，所以这里换成不含通用词的
    #      ``2027款艾瑞泽8PRO`` 品牌行 + 一个无名气泡行，专测跨帧这条判据。
    def frozen_frames(scroll=True):
        base = [("哎哎：这颜色真好看啊", 43), ("星星：这配置表有点长", 44),
                ("苗苗*：赛车和马都好有范", 44), ("柠檬*：这画面绝了啊真的", 44)]
        out_ = []
        for i, t in enumerate([0.0, 5.0, 10.0, 15.0, 20.0]):
            shift = -50.5 * i if scroll else 0.0     # 列表每帧上滚一个行距（真方向）
            rs = [wrow(k, txt, x1, x1 + 250, 943 + k * 50 + shift, 972 + k * 50 + shift)
                  for k, (txt, x1) in enumerate(base)]
            # 气泡：**槽位紧挨着上一条评论**（4），y 钉死不动 —— 这样不认它时它才有机会
            # 被当匿名评论收进来或并进上一条。
            rs.append(wrow(4, "2027款艾瑞泽8PRO", 137, 369, 1272, 1297))
            out_ += [dict(r, t=t) for r in rs]
        return out_

    frz = frozen_overlay_texts(frozen_frames(), panels={"comments"}, row_pitch={"comments": 50.6})
    cases.append(("列表滚了 4 次、气泡行一次都没动 ⇒ 判浮层（不用任何素材词）",
                  frz.get("comments", {}).get("texts") == {"2027款艾瑞泽8PRO"}))
    # 文本每帧读得不一样时靠**盒键**兜底（8pro seg3 实测：同一条气泡读成三种、左沿还跟着
    # 「广告」标签飘 30~90px，但 x2 稳在 369/370、y 稳在 1284/1285）。
    varied = []
    for i, t in enumerate([0.0, 5.0, 10.0, 15.0, 20.0]):
        shift = -50.5 * i
        rs = [wrow(k, txt, x1, x1 + 250, 943 + k * 50 + shift, 972 + k * 50 + shift)
              for k, (txt, x1) in enumerate([("哎哎：这颜色真好看啊", 43),
                                             ("星星：这配置表有点长", 44),
                                             ("苗苗*：赛车和马都好有范", 44),
                                             ("柠檬*：这画面绝了啊真的", 44)])]
        rs.append(wrow(4, ["天生智领新境2027款艾瑞泽8PRO", "领新境2027款艾瑞泽8PRO",
                           "智领新境2027款艾瑞泽8PRO", "2027款艾瑞泽8PRO",
                           "智领新境2027款艾瑞泽8PRO"][i], 43 + 30 * i, 370, 1272, 1297))
        varied += [dict(r, t=t) for r in rs]
    ok5g13 = comments_from_rows(varied, overlay_cards={"comments": None},
                                row_pitch={"comments": 50.6})
    cases.append(("气泡每帧读得都不一样（文本配不上、两条竖边也在飘）⇒ 靠「带键」照样摘掉",
                  len(ok5g13) == 20 and all("艾瑞泽" not in (c["content"] or "")
                                            for c in ok5g13)))
    # **慢滚**也要算「滚了」：8pro seg4/seg5 的列表是一帧挪十几像素地滚（位移中位数
    # −4 ~ −35px），早先「一帧至少挪 0.8 行距」的闸把这种帧对全判成「没滚」⇒ 判据静默
    # 失效、顶部视频横幅行整段漏下来当匿名评论。这里用每帧 −15px 的慢滚复现那个坑。
    slow = []
    for i, t in enumerate([0.0, 5.0, 10.0, 15.0, 20.0]):
        shift = -15.0 * i
        rs = [wrow(k, txt, x1, x1 + 250, 943 + k * 50 + shift, 972 + k * 50 + shift)
              for k, (txt, x1) in enumerate([("哎哎：这颜色真好看啊", 43),
                                             ("星星：这配置表有点长", 44),
                                             ("苗苗*：赛车和马都好有范", 44),
                                             ("柠檬*：这画面绝了啊真的", 44)])]
        rs.append(wrow(4, "2027款艾瑞泽8PRO", 137, 369, 1272, 1297))
        slow += [dict(r, t=t) for r in rs]
    cases.append(("列表只是**慢滚**（每帧 15px < 一行距）⇒ 照样算「滚了」，气泡照摘",
                  comments_from_rows(slow, overlay_cards={"comments": None},
                                     row_pitch={"comments": 50.6}).__len__() == 20
                  and all("艾瑞泽" not in (c["content"] or "")
                          for c in comments_from_rows(slow, overlay_cards={"comments": None},
                                                      row_pitch={"comments": 50.6}))))
    ok5g12 = comments_from_rows(frozen_frames(), overlay_cards={"comments": None},
                                row_pitch={"comments": 50.6})
    ok5g12b = comments_from_rows(frozen_frames(), overlay_cards=None,
                                 row_pitch={"comments": 50.6})
    cases.append(("把气泡行摘在分组之前 ⇒ 5 帧 20 条人人有名、一条气泡都不剩（不摘则有）",
                  len(ok5g12) == 20 and all(c["username"] for c in ok5g12)
                  and all("艾瑞泽" not in (c["content"] or "") for c in ok5g12)
                  and any("艾瑞泽" in (c["content"] or "") for c in ok5g12b)))
    # 同一份行**列表一动没动**（整段停屏）⇒ 谁也不该被判浮层：「没动」在没滚动的帧上
    # 什么也证明不了，这条判据的全部安全性就在这儿。
    cases.append(("列表整段没滚 ⇒ 一条都不判（没滚动的帧证明不了「没动」）",
                  frozen_overlay_texts(frozen_frames(scroll=False), panels={"comments"},
                                       row_pitch={"comments": 50.6}) == {}))
    # 没开 overlay_cards 的面板（抖音/微博）⇒ 这条判据根本不跑，行为一字不变。
    cases.append(("没开 overlay_cards 的面板不跑这条判据",
                  frozen_overlay_texts(frozen_frames(), panels=set(),
                                       row_pitch={"comments": 50.6}) == {}))

    # ⑤g3 **掩码昵称**（`**` / `一**`：视频号打 1~2 颗、抖音打 3 颗）。冒号被 OCR 读成别的
    #     字符时它不是上一条的续行，是**另一个人**的新评论（实测 seg2 t=30.1 被并进「萌萌」
    #     那条，而同一句话在 t=45.5/t=52.8 都是独立一条）。
    ok5g4 = parse_live_rows([wrow(2, "萌萌：五站八冠，这统治力可以", 43, 390, 893, 921),
                             wrow(3, "**.线下有品鉴会就好了", 61, 335, 945, 973)])
    cases.append(("掩码昵称行不并入上一条，掩码前缀也不粘进正文",
                  len(ok5g4) == 2 and ok5g4[0]["content"] == "五站八冠，这统治力可以"
                  and ok5g4[1]["username"] is None
                  and ok5g4[1]["content"] == "线下有品鉴会就好了"))
    # 没有分隔符的（`**正文`）分不出「掩码昵称」和「真以星号开头的续行」，不当评论收，
    # 但**也不许并进上一条** —— 走 `_indented_continuation` 的 `_MASKED_NAME_RE` 那条闸。
    # 星号数写死 3 颗时这条会失败（视频号是 2 颗）：
    ok5g5 = parse_live_rows([wrow(2, "萌萌：五站八冠，这统治力可以", 43, 390, 893, 921),
                             wrow(3, "**线下有品鉴会就好了", 61, 335, 945, 973)])
    cases.append(("没有分隔符的掩码行也不并入上一条（两颗星也要挡住）", len(ok5g5) == 2))
