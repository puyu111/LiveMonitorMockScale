"""livepipe.parsing.selftest.emoji — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import sys

from ..emoji.constants import DEFAULT_THRESHOLDS
from ..emoji.constants import SOURCE_TABLES
from ..emoji.gallery import EmojiGallery
from ..emoji.gallery import emoji_heights
from ..emoji.gallery import parse_emoji_tables
from ..emoji.scan import _box_saturation
from ..emoji.scan import emoji_text_of
from ..emoji.scan import merge_emoji_runs
from ..layout.paragraphs import parse_live_rows



def _selftest_emoji() -> bool:
    """表情层的**纯逻辑**断言（合并 / 串行化 / 传递），不需要 cv2、不需要真录屏。

    匹配本身（NCC + 官方表）没法在自检里跑 —— 那要 214 张图和一次视频解码。这里守的是
    匹配**之后**的事：相邻同款怎么数、跨行会不会误并、空正文能不能被补上、走 --rows
    复算时会不会丢。这些都是「不测就会静默错」的地方（错了产物看着还挺正常）。
    """
    def e(x1, y1, x2, y2, token="[赞]", score=0.9, guess=None):
        return {"box": [x1, y1, x2, y2], "score": score,
                "token": token, "guess": guess or token, "name": None, "id": None}

    cases = []
    # ① 高度的梯子跟行距走（本素材 pitch=68 → 38±2）
    cases.append(("表情高度梯子 = 0.56×pitch ± 2", emoji_heights(68) == (36, 38, 40)))
    # ①b 梯子**按表**：视频号那张表是 0.50（右面板行距 61 → 30，实测表情高 30）
    g_wx = EmojiGallery([], path="x", source="wechat",
                        thresholds={"height_ratio": 0.50})
    g_dy = EmojiGallery([], path="y", source="douyin",
                        thresholds={"height_ratio": 0.56})
    cases.append(("梯子按表取 ratio（微信 0.50×61=30）",
                  emoji_heights(61, g_wx) == (28, 30, 32)
                  and emoji_heights(68, g_dy) == (36, 38, 40)))

    # ①c 阈值挂在**表**上，不是全局常量：同一个 pitch、两张表给出不同的梯子，
    #     扫描函数读的也是 gallery.thresholds（跨源拿错阈值 = 全部静默丢掉）
    g_a = EmojiGallery([], path="a", source="douyin", thresholds=dict(DEFAULT_THRESHOLDS))
    g_b = EmojiGallery([], path="b", source="wechat",
                       thresholds=dict(DEFAULT_THRESHOLDS, looks_like=0.50, min_score=0.70))
    cases.append(("阈值按表取（不是全局常量）",
                  g_a.thresholds["min_score"] == 0.85 and g_b.thresholds["min_score"] == 0.70
                  and g_a.thresholds is not g_b.thresholds))

    # ①c2 颜色关的判据：白字 S≈0、彩色表情 S 高 —— 白字撞上表情模板（`车` 对 `[林致]`
    #     0.765）时，只有饱和度这一刀分得开。默认 0 = 关，只有按源标定过的表才开。
    try:
        import numpy as np
        white = np.full((20, 20, 3), 235, np.uint8)          # 白字：三通道相等
        color = np.zeros((20, 20, 3), np.uint8)              # 橙药丸色：只有 R 高
        color[:, :] = (30, 120, 240)                         # BGR 顺序
        sat_ok = (_box_saturation(white, [2, 2, 18, 18]) < 1.0
                  and _box_saturation(color, [2, 2, 18, 18]) > 100.0)
    except ImportError:
        sat_ok = True
    cases.append(("颜色关：白字 S≈0、彩色 S 高（默认 min_saturation=0 ⇒ 老表行为不变）",
                  sat_ok and DEFAULT_THRESHOLDS["min_saturation"] == 0))

    # ①d --emoji-table 的三种写法：路径 / 源名 / 面板=…（面板或源名写错必须当场报错，不猜）
    cases.append(("--emoji-table 按面板/按源解析",
                  parse_emoji_tables("left=douyin,right=wechat", ["left", "right"])
                  == {"left": SOURCE_TABLES["douyin"], "right": SOURCE_TABLES["wechat"]}
                  and parse_emoji_tables("wechat", ["left", "right"])
                  == {"left": SOURCE_TABLES["wechat"], "right": SOURCE_TABLES["wechat"]}
                  and parse_emoji_tables("out/x/y.json", ["left"])
                  == {"left": "out/x/y.json"}
                  and parse_emoji_tables("kuaishou", ["comments"])
                  == {"comments": SOURCE_TABLES["kuaishou"]}
                  and parse_emoji_tables(None, ["left"]) == {"left": None}))
    bad_spec = 0
    for spec in ("left=nope", "middle=douyin"):
        try:
            parse_emoji_tables(spec, ["left", "right"])
        except SystemExit:
            bad_spec += 1
    cases.append(("--emoji-table 写错面板/源名 ⇒ 报错而不是静默忽略", bad_spec == 2))

    # ② 相邻同款 ⇒ 一条 n=2（'👍👍' 是强调，不是两条独立语义），框取并集
    m = merge_emoji_runs([e(740, 750, 776, 788), e(783, 750, 819, 788)])
    cases.append(("相邻同款并成一条 n=2", len(m) == 1 and m[0]["n"] == 2
                  and m[0]["box"] == [740, 750, 819, 788]))
    # ③ 同款但**隔得远**（中间隔着文字）⇒ 两条：不是同一个 run
    m = merge_emoji_runs([e(740, 750, 776, 788), e(820, 750, 856, 788)])
    cases.append(("同款隔得远不并", len(m) == 2))
    # ③b 窄字形的同款（🌹 框宽 24、空隙 24）：间隙要按**框高**判，写死像素会把这一排
    #     5 朵玫瑰数成 5 条（真素材实测就是这个数）
    m = merge_emoji_runs([e(780, 750, 804, 788, "[玫瑰]"), e(828, 750, 852, 788, "[玫瑰]")])
    cases.append(("窄字形同款也要并（间隙按框高算）", len(m) == 1 and m[0]["n"] == 2))
    # ④ 同款但**在另一行**（y 差一个行距）⇒ 不并。跨行按 x 排会撞上（每行都从左往右排），
    #    没有 y 守卫时这条会错并成 n=2
    m = merge_emoji_runs([e(740, 750, 776, 788), e(741, 818, 777, 856)])
    cases.append(("跨行同款不并（y 守卫）", len(m) == 2))
    # ⑤ 不同款挨着 ⇒ 两条，顺序按 x
    m = merge_emoji_runs([e(740, 750, 776, 788, "[赞]"), e(791, 750, 827, 788, "[爱心]")])
    cases.append(("不同款挨着不并", len(m) == 2 and [z["token"] for z in m] == ["[赞]", "[爱心]"]))

    # ⑥ 串行化：token 按 n 展开；未识别写 '?'（**不能写 guess** —— 没敢认的东西不能
    #    伪装成认出来了）
    cases.append(("emoji_text 展开 n",
                  emoji_text_of([{"token": "[赞]", "n": 3}, {"token": "[爱心]", "n": 1}])
                  == "[赞][赞][赞][爱心]"))
    cases.append(("未识别写 '?' 而不是 guess",
                  emoji_text_of([{"token": None, "guess": "[比心]", "n": 1}]) == "?"))
    cases.append(("没有表情 ⇒ 空串", emoji_text_of(None) == "" and emoji_text_of([]) == ""))

    # ⑦ 传递：行上的 emojis 要进评论记录，空正文的纯表情评论靠 emoji_text 有内容
    def row(sock, text, box, **kw):
        r = {"t": 0.0, "panel": "left", "socket": sock, "text": text, "box": box,
             "x_text": 633, "x_right": 1149, "badge_box": None, "badge_left": 575}
        r.update(kw)
        return r

    em = [{"box": [740, 750, 776, 788], "score": 0.91, "token": "[赞]",
           "guess": "[赞]", "name": "赞", "id": "ab" * 16, "n": 1}]
    rec = parse_live_rows([row(0, "胡杨：", [652, 752, 700, 781], emojis=em)])[0]
    cases.append(("行上的表情进记录 + 补上空正文",
                  rec["content"] == "" and rec["emoji_text"] == "[赞]"
                  and rec["emojis"][0]["box"] == [740, 750, 776, 788]))
    # ⑧ 没跑表情识别（表不在 / --no-emoji）⇒ 字段在但是空的，不能崩
    rec = parse_live_rows([row(0, "胡杨：真帅", [652, 752, 760, 781])])[0]
    cases.append(("没跑表情 ⇒ 空字段不崩", rec["emojis"] == [] and rec["emoji_text"] == ""))
    # ⑨ 续行：末尾与续行开头同款 ⇒ 并成一条（折行的 👍×6 别数成两条）；
    #    不同款 ⇒ 按阅读顺序接在后面。续行的 x1 在 x_text **左边**（没有徽章，见
    #    _has_own_badge）—— 写成 652 会被判成「带徽章的新评论」，这条就测不到了。
    rec = parse_live_rows([
        row(0, "时死你：", [652, 752, 700, 781], emojis=[dict(em[0], n=4)]),
        row(1, "❤️❤️", [577, 819, 700, 849],
            emojis=[dict(em[0], n=2), dict(em[0], token="[爱心]", guess="[爱心]",
                                           box=[800, 818, 836, 856])]),
    ])[0]
    cases.append(("续行同款并 n、不同款按序接上",
                  rec["emoji_text"] == "[赞][赞][赞][赞][赞][赞][爱心]"
                  and rec["lines"] == 2))

    bad = [n for n, ok in cases if not ok]
    if bad:
        print(f"[selftest] 表情断言失败：{bad}", file=sys.stderr)
        return False
    print(f"[selftest] 表情（合并/串行化/传递）{len(cases)} 项断言通过 ✔")
    return True
