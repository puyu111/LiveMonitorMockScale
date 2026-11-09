"""livepipe.parsing.selftest.roi_slots — 空槽表情补扫的断言。"""

from __future__ import annotations

from ..ocr.slots import sweep_emoji_slots


def _add_slot_cases(cases):

    # ⑭d 空槽补扫（``sweep_emoji_slots``）：纯表情评论在行网格上是**空槽** —— 没有 OCR
    #    行就没有「这一行」，表情匹配这一轮压根不会被调用（用户报的 t=48.2 整排比心/
    #    爱心/打call 就是这么漏的）。槽中心靠相邻**单行**插值：那一帧上半屏 −10px、
    #    下半屏 +16px，还夹着 h=79 的并块行，用中位数偏移整排会落在 ±0.35 行距的门外。
    from ..ocr import slots as _slots
    _real_ex = _slots.extract_row_emojis

    def _fake_ex(_frame, yc, pitch, _x1, _x2, _gal, **_kw):
        """假表情匹配：``frame`` 是 {行中心 y: [token, ...]}，命中槽中心才吐框。"""
        out = []
        for ey, toks in _frame.items():
            if abs(ey - yc) <= pitch * 0.35:
                for i, tk in enumerate(toks):
                    out.append({"box": [200 + 34 * i, int(ey) - 16, 234 + 34 * i,
                                        int(ey) + 16], "token": tk,
                                "score": 0.95, "margin": 0.3})
        return out

    _slots.extract_row_emojis = _fake_ex
    try:
        sp = {"rect": [10, 800, 700, 1500], "row_pitch": 53.5, "x_text": 96}

        def sm(sock, y, h=30):
            return (sock, "", [32, y - h // 2, 320, y + h // 2], None, None, None)

        # 行网格照抄 t=48.2：socket 7 是 h=79 的并块行（中心离网格 +39.5，不能当锚）
        meta = [sm(0, 819), sm(1, 856), sm(5, 1066), sm(6, 1118), sm(7, 1196, 79),
                sm(11, 1415), sm(12, 1468)]

        def _sweep(fake, badge_col=None, **kw):
            return sweep_emoji_slots(fake, t=48.2, n_frames=0, pname="comments",
                                     panel=sp, gy1=800, gx1=10, gx2=700,
                                     rows_meta=meta, badge_col=badge_col or {},
                                     fired=None, gal=["fake"], **kw)

        _sw = _sweep({905: ["[比心]"] * 3, 959: ["[爱心]"] * 4, 1012: ["[打call]"] * 4,
                      1066: ["[赞]"] * 3})   # 1066 = 已有 OCR 行的槽，不该重复造行
        cases.append(("空槽补扫：纯表情评论在空槽上造行（插值定槽中心、并块行不当锚）",
                      [(r["socket"], r["emoji_text"]) for r in _sw] ==
                      [(2, "[比心][比心][比心]"), (3, "[爱心][爱心][爱心][爱心]"),
                       (4, "[打call][打call][打call][打call]")]))
        cases.append(("空槽补扫：面板外的表情（入场/礼物动画）不造行",
                      _sweep({765: ["[比心]"] * 2}) == []))
        cases.append(("空槽补扫：没认出来的表情（token 为空）不当评论",
                      _sweep({905: [""]}) == []))
        # 药丸必须**同一条视觉行**才并进 ROI：``badge_col`` 按槽位建，槽位一抖就取到隔壁行
        # （实测 t=107.2 socket 10 的表情在 y 1315–1347，药丸却是 y 1376–1393 那条的）
        # ⇒ ROI 往下长一个行距、压住下一条评论，等级标还串给了不相干的人。
        _sw3 = _sweep({959: ["[爱心]"] * 4}, badge_col={3: [[30, 149, 73, 179, "6"]]})
        cases.append(("空槽补扫：同一行的药丸并进 ROI（左沿到 x=40、等级标保留）",
                      len(_sw3) == 1 and _sw3[0]["box"][0] == 40
                      and _sw3[0]["badge_box"] is not None
                      and _sw3[0]["badge_text"] is not None))
        _sw4 = _sweep({959: ["[爱心]"] * 4}, badge_col={3: [[30, 202, 73, 232, "19"]]})
        cases.append(("空槽补扫：隔壁那条行的药丸不并框、不读等级标（框只剩表情那一行高）",
                      len(_sw4) == 1 and _sw4[0]["box"][3] - _sw4[0]["box"][1] == 32
                      and _sw4[0]["badge_box"] is None
                      and _sw4[0]["badge_text"] is None))
    finally:
        _slots.extract_row_emojis = _real_ex
