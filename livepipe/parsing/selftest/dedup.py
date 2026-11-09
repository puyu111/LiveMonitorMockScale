"""livepipe.parsing.selftest.dedup — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import sys

from ..comments.dedup import dedup_comments
from ..ocr.pills import _SampledFrameReader
from ..ocr.pills import _pill_vote
from ..ocr.pills import _sequential_ruler
from ..ocr.pills import pill_band_box
from ..ocr.pills import pill_reading
from ..ocr.row_ids import assign_row_ids



def _selftest_dedup() -> bool:
    """跨帧去重：文本去重不撞掉纯表情评论；滚动对齐能把同一条评论认成一条。"""
    def rec(user, content, t, sock, panel="left"):
        return {"username": user, "content": content, "t": t, "sockets": [sock],
                "panel": panel}

    cases = []
    same = [rec("时死你", "真帅啊", 0.0, 0), rec("时死你", "真帅啊", 8.0, 1)]
    empties = [rec("暗黑盛战", "", 0.0, 0), rec("胡杨", "", 0.0, 2),
               rec("社恐小老虎", "", 0.0, 4)]
    cases.append(("无 rid 时按文本去重", len(dedup_comments(same)) == 1
                  and dedup_comments(same)[0]["t"] == 0.0))
    cases.append(("无 rid 时空正文不互相撞", len(dedup_comments(empties)) == 3))
    # 对齐没对齐（rid 不同）但正文一模一样 ⇒ 仍是同一条
    two_rid = [dict(rec("打酱油", "真帅", 8.0, 2, panel="right"), rid=21),
               dict(rec("打酱油", "真帅", 24.0, 0, panel="right"), rid=22)]
    cases.append(("rid 不同但正文相同 ⇒ 合并", len(dedup_comments(two_rid)) == 1))

    # 同一条评论的多次拍摄：先拍到的可能是**截断**的（折行还没滚进可视区），也可能个别
    # 帧 OCR 抖一个字 —— 去重按「行数最多的一档 + 档内正文多数」选（2026-11-05 10.31
    # 视频号 seg1 t=4630.9 `摸鱼的西红柿`：`…正` / `…正合活` / `…正合适`）。
    parts = []
    for i, (cont, lines) in enumerate([
            ("在泉州开车逛古城，走走停停正", 1),
            ("在泉州开车逛古城，走走停停正 合活", 2),
            ("在泉州开车逛古城，走走停停正 合适", 2),
            ("在泉州开车逛古城，走走停停正 合适", 2)]):
        c = rec("摸鱼的西红柿", cont, 100.0 + i, 10)
        c.update(rid=7, lines=lines, roi=[0, 1000 + i, 500, 1050 + i])
        parts.append(c)
    got = dedup_comments(parts)
    cases.append(("多次拍摄取最完整/多数（截断行 + 单帧 OCR 抖动）",
                  len(got) == 1
                  and got[0]["content"] == "在泉州开车逛古城，走走停停正 合适"
                  and got[0]["lines"] == 2))
    # 反例：只有一帧多出一行（其余帧都是单行）⇒ 不拿那一帧（可能是被吞进来的系统行）
    odd = []
    for i in range(4):
        c = rec("某甲", "真不错", float(i), 0)
        c.update(rid=9, lines=1)
        odd.append(c)
    odd.append(dict(rec("某甲", "真不错", 9.0, 0), rid=9, lines=2))
    cases.append(("行数档不占多数（1/5）⇒ 不换（防被吞进来的系统行那一帧）",
                  len(dedup_comments(odd)) == 1
                  and dedup_comments(odd)[0]["content"] == "真不错"))

    # 滚动对齐：帧 2 = 帧 1 上移 2 行（胡杨/肘死你/社恐小老虎 三行对上），底部两条是新评论
    def row(t, sock, text):
        return {"t": t, "panel": "left", "socket": sock, "text": text,
                "box": [656, 752 + sock * 68, 1146, 781 + sock * 68],
                "x_text": 633, "x_right": 1146}

    f1 = [row(0.0, 0, "1 时死你: 真帅啊"), row(0.0, 1, "6 暗黑盛战: ❤️"),
          row(0.0, 2, "10 胡杨: 👍👍"), row(0.0, 3, "3 社恐小老虎: 🌹"),
          row(0.0, 4, "1 时死你: 🎉")]
    f2 = [row(8.0, 0, "10 胡杨: 👍👍"), row(8.0, 1, "3 社恐小老虎: 🌹"),
          row(8.0, 2, "1 时死你: 🎉"), row(8.0, 3, "3 xjbd: 👏"),
          row(8.0, 4, "1 时死你: 💐")]
    rid1 = {r["text"]: r["rid"] for r in assign_row_ids(f1 + f2) if r["t"] == 0.0}
    rid2 = {r["text"]: r["rid"] for r in assign_row_ids(f1 + f2) if r["t"] == 8.0}
    shared = {"10 胡杨: 👍👍", "3 社恐小老虎: 🌹", "1 时死你: 🎉"}
    cases.append(("滚动对齐：上移 2 行后同一条仍是同一 rid",
                  all(rid1[x] == rid2[x] for x in shared)
                  and rid2["3 xjbd: 👏"] not in rid1.values()
                  and rid2["1 时死你: 💐"] not in rid1.values()))
    # 对不齐（只有 1 行相同 + 全是重复文本）⇒ 不认，各自成条
    g1 = [row(0.0, 0, "赞了直播"), row(0.0, 1, "赞了直播"), row(0.0, 2, "赞了直播")]
    g2 = [row(8.0, 0, "赞了直播"), row(8.0, 1, "赞了直播"), row(8.0, 2, "打酱油: 真帅")]
    rr = assign_row_ids(g1 + g2)
    cases.append(("对不齐就不合并（一屏多个同名通知不算证据）",
                  len({r["rid"] for r in rr if r["t"] == 8.0}) == 3))

    # 位移**为负**也要认：滚动区顶槽位时有时无 —— 帧 2 顶上多一行（1221），其余不动，
    # 等价于内容下移一格 k=-1。只允许 k>=0 时这一对帧全对不齐，唯一评论会翻倍
    # （真实素材实测 19 → 27，全是重复）。这条守住它。
    h1 = [row(0.0, 0, "1 时死你: 真帅啊"), row(0.0, 1, "6 暗黑盛战: ❤️"),
          row(0.0, 2, "10 胡杨: 👍👍"), row(0.0, 3, "3 社恐小老虎: 🌹")]
    h2 = [row(8.0, 0, "2 1221：好帅哦大哥哥"), row(8.0, 1, "1 时死你: 真帅啊"),
          row(8.0, 2, "6 暗黑盛战: ❤️"), row(8.0, 3, "10 胡杨: 👍👍"),
          row(8.0, 4, "3 社恐小老虎: 🌹")]
    hr1 = {r["text"]: r["rid"] for r in assign_row_ids(h1 + h2) if r["t"] == 0.0}
    hr2 = {r["text"]: r["rid"] for r in assign_row_ids(h1 + h2) if r["t"] == 8.0}
    cases.append(("顶槽位时有时无（k=-1）也要认成同一条",
                  all(hr1[k] == hr2[k] for k in hr1)
                  and hr2["2 1221：好帅哦大哥哥"] not in hr1.values()))

    # 纯表情行：主键全是 ``***``（打码昵称残渣），整帧统一位移一旦差一格，就会把
    # **别人的评论**认成自己的 ⇒ 同一个 rid 只留最早一次，另外几条整条丢掉
    # （用户报的「比心没识别出来」在交付里就丢在这一步，`rows.jsonl` 里那行一直在）。
    def emo_row(t, sock, text, tokens):
        r = row(t, sock, text)
        r["emojis"] = [{"token": tk} for tk in tokens]
        return r

    # 帧里要有**两行不同文本**当地锚：``_best_shift`` 要求对齐跑长 ≥2、互异键 ≥2，
    # 单行帧算不出位移，整帧都当新行 —— 那样这几条断言会**空过**（第一版就是），
    # 所以每条都额外断言地锚行跨帧保住了 rid，证明位移真的算出来了。
    p1 = [row(0.0, 0, "1 时死你: 真帅啊"), row(0.0, 1, "6 暗黑盛战: ❤️"),
          emo_row(0.0, 2, "***", ["[比心]"] * 3)]
    p2 = [row(8.0, 0, "1 时死你: 真帅啊"), row(8.0, 1, "6 暗黑盛战: ❤️"),
          emo_row(8.0, 2, "***", ["[爱心]"] * 4)]
    pr = assign_row_ids(p1 + p2)
    cases.append(("纯表情行：表情集不相交就不认亲（同槽同文本也不行）",
                  pr[2]["rid"] != pr[5]["rid"]
                  and pr[0]["rid"] == pr[3]["rid"] and pr[1]["rid"] == pr[4]["rid"]))
    q1 = [row(0.0, 0, "1 时死你: 真帅啊"), row(0.0, 1, "6 暗黑盛战: ❤️"),
          emo_row(0.0, 2, "***", ["[比心]"] * 3)]
    q2 = [row(8.0, 0, "1 时死你: 真帅啊"), row(8.0, 1, "6 暗黑盛战: ❤️"),
          emo_row(8.0, 2, "***", ["[比心]"] * 2)]     # 同一张表情，个数识别抖动
    qr = assign_row_ids(q1 + q2)
    cases.append(("纯表情行：表情集有交集仍是同一条（个数抖动不算换人）",
                  qr[2]["rid"] == qr[5]["rid"] and qr[0]["rid"] == qr[3]["rid"]))
    # 快速滚动时整帧位移差一格 ⇒ 配到隔壁那条：邻行里有表情对得上的就认它，
    # 否则两行会互相串（比心的 rid 记成爱心的、爱心的记成比心的，各丢一条）
    s1 = [row(0.0, 0, "1 时死你: 真帅啊"), emo_row(0.0, 1, "***", ["[比心]"] * 3),
          emo_row(0.0, 2, "***", ["[爱心]"] * 4), row(0.0, 3, "6 暗黑盛战: ❤️")]
    s2 = [emo_row(8.0, 0, "***", ["[爱心]"] * 4),
          emo_row(8.0, 1, "***", ["[比心]"] * 3),
          row(8.0, 2, "6 暗黑盛战: ❤️"), row(8.0, 3, "10 胡杨: 👍👍")]
    sr = assign_row_ids(s1 + s2)
    rid_of = {id(r): r["rid"] for r in sr}
    cases.append(("纯表情行：位移差一格也不串人（邻行表情对得上就认它）",
                  rid_of[id(s1[1])] == rid_of[id(s2[1])]
                  and rid_of[id(s1[2])] == rid_of[id(s2[0])]))
    # 端到端：三条不同的纯表情评论叠在一屏、整屏上移一格、底下再冒一条新的 ⇒
    # 三条都要活下来且各归各，新来的那条另算。地锚（两条文本）放在顶上跟着一起上移。
    t1 = [row(0.0, 0, "1 时死你: 真帅啊"), row(0.0, 1, "6 暗黑盛战: ❤️"),
          emo_row(0.0, 2, "***", ["[比心]"] * 3),
          emo_row(0.0, 3, "***", ["[爱心]"] * 4),
          emo_row(0.0, 4, "***", ["[打call]"] * 2)]
    t2 = [row(8.0, 0, "6 暗黑盛战: ❤️"),
          emo_row(8.0, 1, "***", ["[比心]"] * 3),
          emo_row(8.0, 2, "***", ["[爱心]"] * 4),
          emo_row(8.0, 3, "***", ["[打call]"] * 2),
          emo_row(8.0, 4, "***", ["[赞]"] * 3)]
    tr = assign_row_ids(t1 + t2)
    rt = {id(r): r["rid"] for r in tr}
    cases.append(("纯表情行：叠在一屏的三条不同的评论上移一格后仍是三条（不吃掉、不串人）",
                  len({r["rid"] for r in tr}) == 6
                  and [rt[id(r)] for r in t2[1:4]] == [rt[id(r)] for r in t1[2:5]]
                  and rt[id(t1[1])] == rt[id(t2[0])]          # 地锚认上了 ⇒ 位移是真算出来的
                  and rt[id(t2[4])] not in [rt[id(r)] for r in t1]))

    # 内容触发模式：同一个 rid 留**位移最小**（拍得最清楚）的那次，而不是最早的那次
    mm = [dict(rec("a", "糊", 0.0, 0), rid=7, motion_px=40.0),
          dict(rec("a", "糊", 1.0, 0), rid=7, motion_px=0.0)]
    got = dedup_comments(mm)
    cases.append(("有 motion_px 时同 rid 留最清楚的一次（非最早）",
                  len(got) == 1 and got[0]["t"] == 1.0))

    # 等级标证据：留下的那次没检出徽章，同一条评论的其它帧读到了 ⇒ 补回来
    lv1 = [dict(rec("用***", "好车", 0.0, 0), rid=3),                     # 留下的（最早那次）
           dict(rec("用***", "好车", 2.0, 0), rid=3, badge_text="◆16"),
           dict(rec("用***", "好车", 4.0, 0), rid=3, badge_text="16")]
    st = {}
    got = dedup_comments(lv1, st)
    cases.append(("等级标：其它帧读到就补回来",
                  len(got) == 1 and got[0]["badge_text"] == "16" and st["badge_filled"] == 1))
    # 只补不改：留下的那次自己有读数，别的帧说什么都不动它
    lv2 = [dict(rec("用***", "好车", 0.0, 0), rid=4, badge_text="5"),
           dict(rec("用***", "好车", 2.0, 0), rid=4, badge_text="9")]
    cases.append(("等级标：有读数的不被别的帧改写",
                  dedup_comments(lv2)[0]["badge_text"] == "5"))
    # 同 rid 但**昵称不同** ⇒ 滚动对齐在 rid 尾端吸进来的下一条评论，它的读数绝不能借
    lv3 = [dict(rec("醉***", "没有车标？", 0.0, 0), rid=5),
           dict(rec("时***", "牛上天没？？？", 60.0, 0), rid=5, badge_text="13")]
    cases.append(("等级标：同 rid 不同昵称不借（不把别人的等级记到这条上）",
                  dedup_comments(lv3)[0].get("badge_text") is None))
    # 平票不猜
    lv4 = [dict(rec("用***", "好车", 0.0, 0), rid=6),
           dict(rec("用***", "好车", 2.0, 0), rid=6, badge_text="3"),
           dict(rec("用***", "好车", 4.0, 0), rid=6, badge_text="8")]
    cases.append(("等级标：平票不补（宁可漏归一）",
                  dedup_comments(lv4)[0].get("badge_text") is None))
    # 对齐**断链**：同一条评论跨了两个 rid（实测 seg5 `白***：喜欢` 跨 537/546/556，
    # 两次 `◆21` 读数全落在后一个 rid 上）—— 证据要跟着**去重认定的那条评论**走
    lv5 = [dict(rec("白***", "喜欢", 0.0, 0), rid=537),
           dict(rec("白***", "喜欢", 4.0, 0), rid=546, badge_text="21"),
           dict(rec("白***", "喜欢", 8.0, 0), rid=546, badge_text="21")]
    got = dedup_comments(lv5)
    cases.append(("等级标：同一评论断成两个 rid，读数照样认得回",
                  len(got) == 1 and got[0]["badge_text"] == "21"))

    # 药丸带几何：右界贴着正文框左沿（把正文第一个字排除在外），左界 = 右界 − 带宽
    cases.append(("药丸带：右界 = 正文框左沿 −2，左界 = 右界 −54",
                  pill_band_box((300, 100, 600, 118)) == (244, 98, 298, 120)))
    cases.append(("药丸带：贴着左边界裁不出足够宽 ⇒ None（宁可读不出）",
                  pill_band_box((20, 100, 600, 118)) is not None
                  and pill_band_box((4, 100, 600, 118)) is None))
    # 药丸列的左沿（= 该条 ROI 的 x0）：带子右沿必须进到列里，否则这一刀全在列左边
    cases.append(("药丸带：右沿没进药丸列 ⇒ None（药丸并进昵称那种行）",
                  pill_band_box((82, 100, 600, 118), x_min=38) == (26, 98, 80, 120)
                  and pill_band_box((38, 100, 196, 118), x_min=38) is None))
    cases.append(("药丸带：没有徽章列的版式（ROI 左沿贴着正文列）⇒ 一律 None",
                  pill_band_box((82, 100, 600, 118), x_min=82) is None))
    cases.append(("药丸带：没有正文框 ⇒ None", pill_band_box(None) is None))
    # 单帧读数：◆ 图标那一票（在左）掐掉、前导零砍掉 ⇒ 两种写法投同一票
    cases.append(("单帧读数：◆13 → 013 ⇒ 取 13（砍前导零）",
                  pill_reading("013", 0.9) == "13" and pill_reading("13", 0.9) == "13"))
    cases.append(("单帧读数：分数低 / 没数字 / 整段只是图标 ⇒ None",
                  pill_reading("13", 0.4) is None and pill_reading("◆", 0.9) is None
                  and pill_reading("0", 0.9) is None))
    # 跨帧投票：≥2 帧同一个值且过半才认；平票/单票不认
    cases.append(("投票：两帧同值 ⇒ 认",
                  _pill_vote(["13", "13", None, ""]) == "13"))
    cases.append(("投票：只有一帧读到 ⇒ 不认（宁可漏归一）",
                  _pill_vote(["13", None, None]) is None))
    cases.append(("投票：2:2 平票 ⇒ 不认", _pill_vote(["13", "13", "4", "4"]) is None))
    cases.append(("投票：2:1 ⇒ 认多数（严格过半）", _pill_vote(["13", "13", "4"]) == "13"))

    # 回读原帧那把尺子：抽帧模式定尺子（adaptive 是顺序解码的，回读也得顺序解码）
    cases.append(("取帧尺子：adaptive ⇒ 顺序解码",
                  _sequential_ruler("adaptive") and not _sequential_ruler("interval")
                  and not _sequential_ruler("backoff") and not _sequential_ruler(None)))

    class _FakeCap:
        """假相机：``set`` 落点精确、``read`` 给当前帧号再前进 —— 顺序取帧的账好对。"""

        def __init__(self):
            self.calls, self.n = [], 0

        def set(self, prop, val):
            self.calls.append(("set", int(val)))
            self.n = int(val)

        def grab(self):
            self.calls.append(("grab",))
            self.n += 1
            return True

        def read(self):
            self.calls.append(("read",))
            f, self.n = self.n, self.n + 1
            return True, f

    fc = _FakeCap()
    rd = _SampledFrameReader(fc, 10.0, start=0.0, sequential=True)
    got = [rd.read_at(i) for i in (3, 5, 6)]
    cases.append(("顺序取帧：取到的是**目标帧号**那一帧，中间只 grab 不读",
                  got == [3, 5, 6] and fc.calls.count(("grab",)) == 4
                  and ("set", 0) in fc.calls and ("set", 3) not in fc.calls))
    fc = _FakeCap()
    rd = _SampledFrameReader(fc, 10.0, start=2.0, sequential=True)
    cases.append(("顺序取帧：锚点 = round(start × fps)（与抽帧同一个起点）",
                  rd.anchor == 20 and ("set", 20) in fc.calls))
    fc = _FakeCap()
    rd = _SampledFrameReader(fc, 10.0, start=0.0, sequential=False)
    cases.append(("取帧尺子：interval/backoff ⇒ 按帧号 seek（与那两种抽帧同帧）",
                  [rd.read_at(i) for i in (3, 5)] == [3, 5]
                  and fc.calls.count(("grab",)) == 0 and ("set", 3) in fc.calls))
    msgs = []
    fc = _FakeCap()
    rd = _SampledFrameReader(fc, 10.0, start=0.0, sequential=True, log=msgs.append)
    rd.read_at(9)
    cases.append(("顺序取帧：要求回退 ⇒ 重锚 + 出声（不静默给错帧）",
                  rd.read_at(2) == 2 and fc.calls.count(("set", 0)) == 2 and bool(msgs)))
    # 二次读数落到去重后的账上：只补「自己没有读数」的，且照样受「同一条评论」约束
    lv6 = [dict(rec("用***", "好车", 0.0, 0), rid=9, text_box=(300, 100, 600, 118)),
           dict(rec("用***", "好车", 2.0, 0), rid=9, text_box=(300, 100, 600, 118))]
    st6 = {}
    got = dedup_comments(lv6, st6, reread={0: "23", 1: "23"})
    cases.append(("二次读数：两帧同值 ⇒ 补上（并记账）",
                  len(got) == 1 and got[0]["badge_text"] == "23"
                  and st6["badge_reread"] == 1))
    # 有面板读数 ⇒ 一个字节都不改，只把「对不对得上」记下来
    lv7 = [dict(rec("用***", "好车", 0.0, 0), rid=10, badge_text="4"),
           dict(rec("用***", "好车", 2.0, 0), rid=10)]
    st7 = {}
    got = dedup_comments(lv7, st7, reread={0: "4", 1: "4"})
    cases.append(("二次读数：与面板读数一致 ⇒ 不改 + 记 agree",
                  got[0]["badge_text"] == "4" and st7["badge_reread_agree"] == 1
                  and st7["badge_reread"] == 0))
    st8 = {}
    got = dedup_comments(lv7, st8, reread={0: "9", 1: "9"})
    cases.append(("二次读数：与面板读数不一致 ⇒ 仍不改 + 记 differ",
                  got[0]["badge_text"] == "4" and st8["badge_reread_differ"] == 1))
    # 「投不出票」不是「不一致」：只有一帧读到（票不够）⇒ 记 novote，differ 一个都不许加
    st8b = {}
    got = dedup_comments(lv7, st8b, reread={0: "9"})
    cases.append(("二次读数：有面板读数但票不够 ⇒ 记 novote（**不算不一致**）",
                  got[0]["badge_text"] == "4" and st8b["badge_reread_novote"] == 1
                  and st8b["badge_reread_differ"] == 0 and st8b["badge_reread_agree"] == 0))
    lv6b = [dict(rec("用***", "好车", 0.0, 0), rid=9, text_box=(300, 100, 600, 118)),
            dict(rec("用***", "好车", 2.0, 0), rid=9, text_box=(300, 100, 600, 118))]
    st9 = {}
    got = dedup_comments(lv6b, st9, reread={0: "23"})      # 只有留下那一次读到
    cases.append(("二次读数：只有一帧 ⇒ 不补（计平票）",
                  got[0].get("badge_text") is None and st9["badge_reread_tied"] == 1))
    # 去重时被丢掉的那几次，它们的二次读数要算到**留下那条**头上（同一条评论的其它次拍摄）：
    # 留下那次自己没读出来，靠另外两帧的读数凑够票 ⇒ 能补上
    lv8 = [dict(rec("用***", "好车", 0.0, 0), rid=11),
           dict(rec("用***", "好车", 2.0, 0), rid=11),
           dict(rec("用***", "好车", 4.0, 0), rid=11)]
    st10 = {}
    got = dedup_comments(lv8, st10, reread={1: "23", 2: "23"})
    cases.append(("二次读数：证据跟着去重认定的那条走（被丢掉的次也投它一票）",
                  len(got) == 1 and got[0]["badge_text"] == "23"
                  and st10["badge_reread"] == 1))
    # 同 rid 不同昵称（对齐在尾端吸进来的下一条）：它的药丸读数**不能借**
    lv9 = [dict(rec("醉***", "没有车标？", 0.0, 0), rid=12),
           dict(rec("时***", "牛上天没？？？", 60.0, 0), rid=12)]
    st11 = {}
    got = dedup_comments(lv9, st11, reread={1: "13", 1: "13"})
    cases.append(("二次读数：同 rid 不同昵称不借",
                  got[0].get("badge_text") is None
                  and st11["badge_reread"] + st11["badge_reread_tied"] == 0))
    # ★ 兄弟记录并票（bug-386 的后半）：rid 链一断，**纯表情评论没有文本键** ⇒ 同一条评论
    #   的两张药丸带票落在两个记录里、各一票 ⇒ 投票闸永远过不了（1.jpg 的现场：seg4 的
    #   ``好***`` ``[赞]`` 在 t=3762.293 与 3772.336 各读到一个 ``7``，中间那帧这一行
    #   det 掉了）。四样全同（panel/昵称/正文/表情文本）+ 抽帧相邻 + 票**众口一词**才并，
    #   而且只并票、不并记录（并记录会动到交付的张数门槛）。
    def _sib(*spec):                        # (昵称, 正文, t, socket, rid, 表情) 元组速写
        return [dict(rec(u, c, t, s), rid=r, emoji_text=e) for u, c, t, s, r, e in spec]

    st12 = {}
    got = dedup_comments(_sib(("好***", "", 10.0, 0, 787, "[赞]"),
                              ("好***", "", 15.0, 1, 788, "[赞]")),
                         st12, reread={0: "7", 1: "7"})
    cases.append(("兄弟并票：链断成两条、各一票 ⇒ 并起来照样读出 ◆7（两条都补，记录不并）",
                  len(got) == 2 and [c["badge_text"] for c in got] == ["7", "7"]
                  and st12["badge_reread"] == 2 and st12["badge_reread_pooled"] == 2))
    st13 = {}
    got = dedup_comments(_sib(("好***", "", 10.0, 0, 791, "[赞]"),
                              ("好***", "", 15.0, 1, 792, "[赞]")),
                         st13, reread={0: "7", 1: "9"})
    cases.append(("兄弟并票：票不一致 ⇒ 一个都不并（同昵称不同等级的两个人，宁可漏归一）",
                  [c.get("badge_text") for c in got] == [None, None]
                  and st13["badge_reread_pooled"] == 0))
    st14 = {}
    got = dedup_comments(_sib(("好***", "", 0.0, 0, 801, "[赞]"),
                              ("啊***", "", 10.0, 0, 802, ""),
                              ("哦***", "", 20.0, 0, 803, ""),
                              ("咦***", "", 30.0, 0, 804, ""),
                              ("呀***", "", 40.0, 0, 805, ""),
                              ("好***", "", 50.0, 1, 806, "[赞]")),
                         st14, reread={0: "7", 5: "7"})
    cases.append(("兄弟并票：抽帧隔太远（>3 帧）不并 —— 同一用户隔几分钟再发一条一样的不是同一条",
                  [c.get("badge_text") for c in got if c["username"] == "好***"] == [None, None]
                  and st14["badge_reread_pooled"] == 0))
    st15 = {}
    got = dedup_comments(_sib(("好***", "", 10.0, 0, 811, "[赞]"),
                              ("好***", "", 15.0, 1, 812, "[玫瑰]")),
                         st15, reread={0: "7", 1: "7"})
    cases.append(("兄弟并票：表情文本不同就不并（另一条评论，四样全同才算同一条）",
                  None in [c.get("badge_text") for c in got]
                  and st15["badge_reread_pooled"] == 0))
    # 有面板读数的**只当施主**：自己的读数一个字节不动，只把票借出去
    st16 = {}
    got = dedup_comments([dict(rec("好***", "", 10.0, 0), rid=821, emoji_text="[赞]",
                               badge_text="7"),
                          dict(rec("好***", "", 15.0, 1), rid=822, emoji_text="[赞]")],
                         st16, reread={0: "7", 1: "7"})
    cases.append(("兄弟并票：有读数的那条只借票不改读数（它自己那一票投不出结论 ⇒ novote），"
                  "另一条靠并来的两票补上",
                  [c["badge_text"] for c in got] == ["7", "7"]
                  and st16["badge_reread"] == 1 and st16["badge_reread_pooled"] == 1
                  and st16["badge_reread_novote"] == 1))

    bad = [name for name, passed in cases if not passed]
    if bad:
        print(f"[selftest] 去重断言失败：{bad}", file=sys.stderr)
        return False
    print(f"[selftest] 跨帧去重/滚动对齐 {len(cases)} 项断言通过 ✔")
    return True
