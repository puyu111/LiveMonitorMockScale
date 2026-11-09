"""livepipe.parsing.ocr.slots — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from ..emoji.scan import emoji_text_of
from ..emoji.scan import extract_row_emojis
from ..layout.roi import _union
from ..layout.roi import badge_left_of
from ..ocr.fused import _offset_box
from ..ocr.fused import badge_box_of
from ..ocr.fused import badge_text_of



def sweep_emoji_slots(frame, *, t, n_frames, pname, panel, gy1, gx1, gx2,
                      rows_meta, badge_col, fired, gal) -> list[dict]:
    """在**行网格的空槽**上补扫表情，造出「纯表情评论」的行（见 rows_from_video）。

    表情是图片，OCR 只认文字 ⇒ 「只有表情」的评论（``◆4 S***：❤️❤️❤️``）经常连打码
    昵称都读不出来，那一行在网格里就是个**空槽**：没有行 ⇒ 永远不圈、也永远进不了
    comments.jsonl。所以在相邻两条已检出行之间的空槽上补扫一遍：认到 ≥1 个有名字的表情
    （``token`` 非空）才造一行，行框 = 表情框的并集（ROI 由它往左扩到徽章列、往右扩到面板边）。

    三条自我约束，都是为了不往交付里灌假评论：
      ① 槽中心必须落在**评论面板**的纵向区间内（上下各内缩半个行距），表情框也要整个
        落在区间内 —— 「xxx 加入了直播间」、广告卡、输入栏都在面板外，靠面板闸挡掉。
         **不能**改用「范围外必须有等级药丸」：纯表情评论恰恰是**没有**药丸、连打码昵称
        都读不出来的那种，一帧里连着几行（t=49.2 就是上半屏整排空槽），拿药丸当闸门
        等于把要救的行自己关在门外；
      ② 表情簇的 y 中心要落在槽中心 ±0.35 行距内（别人那行的表情溢进来不算）；
      ③ 只认**有名字**的表情（``token`` 非空），``guess`` 那种「像但没敢认」的一律不算
         （它们正是白字/杂项的常客，见 bug-466 的报警器）。

    槽中心**不用标定的 ``y_phase`` + 全局偏移**，改成「相邻两条已检出行之间线性插值」：
    手机 App 面板平滑滚动，相位逐帧变（实测同一帧里还会**分裂** —— t=48.2 上半屏 −10px、
    下半屏 +16px，两条 h=79 的并块行又把中位数偏移拽到 +12.5），拿单个偏移去扫，三行
    纯表情正好落在 ±0.35 行距的门槛外 ⇒ 整排漏扫。插值直接吃前后两行的实测位置，两头
    够不着时按**本地行距**外推。

    ``rows_meta`` 是 ``rows_from_video`` 的行元组 ``(socket, text, box, badge_box,
    badge_text, colon_box)``，``box`` 已是**整帧**坐标；返回的行也按整帧坐标写。
    """
    out: list[dict] = []
    if not gal or not rows_meta:
        return out
    pitch = panel["row_pitch"]
    anchors: list[tuple[int, float]] = []     # 锚点只用**单行**：折行/并块的行中心不在
    have: set[int] = set()                    # 网格上，当锚会带偏（见上面的 docstring）
    for m in rows_meta:
        s0 = m[0]
        if abs(s0 - round(s0)) > 1e-6:
            continue
        s_i = int(round(s0))
        have.add(s_i)
        box = m[2]
        if (box[3] - box[1]) <= pitch * 0.9:
            anchors.append((s_i, (box[1] + box[3]) / 2))
    anchors.sort()
    if not anchors:
        return out
    lo0, hi0 = anchors[0][0], anchors[-1][0]
    pitch_local = ((anchors[-1][1] - anchors[0][1]) / (hi0 - lo0) if hi0 > lo0 else pitch)

    def _slot_y(s: int) -> float:
        """该槽的行中心 y：相邻两条锚之间插值，超出两端按本地行距外推。"""
        left = right = None
        for a in anchors:
            if a[0] < s:
                left = a
            elif a[0] > s:
                right = a
                break
        if left and right:
            return left[1] + (right[1] - left[1]) * (s - left[0]) / (right[0] - left[0])
        if left:
            return left[1] + (s - left[0]) * pitch_local
        return right[1] - (right[0] - s) * pitch_local

    gy2 = gy1 + (panel["rect"][3] - panel["rect"][1])
    y_top, y_bot = gy1 + pitch * 0.5, gy2 - pitch * 0.5   # 面板闸：上下各内缩半个行距
    pad = int((gy2 - gy1) / max(pitch, 1.0)) + 2          # 够覆盖整个面板，再由 y_top/y_bot 收
    for s in range(lo0 - pad, hi0 + pad + 1):
        if s in have:
            continue
        yc = _slot_y(s)
        if not (y_top <= yc <= y_bot):
            continue
        badge = badge_box_of(badge_col, s)
        emo = [e for e in extract_row_emojis(
                   frame, yc, pitch, gx1 + panel["x_text"], gx2, gal)
               if e.get("token")]
        if not emo:
            continue
        cy = sum((e["box"][1] + e["box"][3]) / 2 for e in emo) / len(emo)
        if abs(cy - yc) > pitch * 0.35:
            continue
        ebox = [min(e["box"][0] for e in emo), min(e["box"][1] for e in emo),
                max(e["box"][2] for e in emo), max(e["box"][3] for e in emo)]
        if ebox[1] < y_top or ebox[3] > y_bot:            # 表情框自己也得整个在面板里
            continue
        # 药丸只在**同一条视觉行**上才并进来（④）：``badge_col`` 是按槽位建的，槽位本身会
        # 抖/错位，实测取到的是**下一条**评论的药丸（t=107.2 socket 10 的药丸 y 1376–1393，
        # 而这一行表情在 y 1315–1347）—— 并进来 ROI 就往下长出整整一个行距、压到下一条
        # 评论身上（画出来两个框叠在一起），等级标还会串给不相干的人（t=98.3 的纯表情行
        # 被记成 ◆19）。拿不到同一行的药丸就只留 ``badge_left``（列左沿）兜 ROI 左边界。
        bbox = _offset_box(badge, gx1, gy1) if badge else None
        if bbox and abs((bbox[1] + bbox[3]) / 2 - cy) > pitch * 0.5:
            bbox = None
        gbox = _union(ebox, bbox) if bbox else ebox
        out.append({
            "t": t, "frame": n_frames, "panel": pname, "socket": s,
            # text 留空：正文就是这些表情，内容由 emoji_text 补
            # （见 parse_live_rows 的「纯表情评论」分支与 _record）
            "text": "", "box": gbox, "colon_box": None,
            "motion_px": fired.get(pname) if isinstance(fired, dict) else None,
            "badge_box": bbox,
            "badge_text": badge_text_of(badge_col, s) if bbox else None,
            "badge_left": badge_left_of(gx1, panel),
            "x_text": gx1 + panel["x_text"], "x_right": gx2,
            "emojis": emo, "emoji_text": emoji_text_of(emo),
        })
    return out
