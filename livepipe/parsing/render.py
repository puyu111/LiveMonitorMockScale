"""livepipe.parsing.render — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from pathlib import Path

from .common.log import _log
from .ocr.colon import _COLON_DROPPED_NICK_GAP
from .ocr.colon import _COLON_PROBE_LEFT
from .ocr.colon import _COLON_PROBE_RIGHT
from .ocr.colon import colon_box_by_pixels
from .ocr.decode import read_recovering



# --------------------------------------------------------------------------- #
# 7. 标注图：每条评论的 ROI 画回帧上
# --------------------------------------------------------------------------- #
# 相邻 ROI 用不同颜色，避免看成一整块。
_PALETTE = [(255, 64, 64), (64, 200, 255), (255, 200, 0), (120, 255, 120),
            (255, 120, 255), (0, 220, 200), (255, 150, 60), (170, 150, 255)]


def refresh_colon_box(frame, c: dict, params: dict | None = None) -> None:
    """用**这一帧的像素**重算一条评论的分隔冒号框，就地写回 ``c["colon_box"]``。

    抽行那趟已经算过一遍（``colon_box_by_pixels``），但 ``--rows`` 复算 / 重画时手上是
    旧 ``rows.jsonl`` 里带下来的框 —— 那时像素找法还没拿插值当先验（抖音手机版的打码
    昵称 `***` 贴着冒号，孤立判据把真冒号整类挡掉 ⇒ 退回插值，框偏 10~25px，圈在正文
    首字上）。重画时底帧就在手里，重算一次很便宜，还能把**旧产物**一并修对；而且画出来
    的框和 ``comments.jsonl`` 落盘的是同一个（就地写回）。找不到点对就保留原值 ——
    续行 / 系统行 / 浅色底本来就不该有新框，也不能凭空擦掉旧框。

    **一般只修已有的框，不新增**：没有框有两种可能（行里没有分隔冒号，或者解析层把这行当
    续行/系统行），这时按像素硬找一个框只会圈到昵称里的 `氵` 之类的点对（实测 t=0 的
    ``汐***`` 行就有一对）—— 那是误标，不是修标。

    例外（``params`` 给了才走）：``params`` 是这个面板的 ``colon_bg_max_v`` /
    ``colon_v_min`` / ``colon_v_delta``（平台档案显式写了才传）。两种「昵称块不在正文里」
    的行才向正文框**左边**多找一段（``_COLON_PROBE_LEFT``），给它们补上冒号框：

      * 药丸块 ``badge_text`` 带掩码昵称（``*``）—— 昵称连同冒号被并进药丸块
        （抖音实测 t=191.2：药丸块 `121***`、正文 `家用代步适合吗`）；
      * 正文块左沿距 ``x_text`` ≥ ``_COLON_DROPPED_NICK_GAP`` —— 昵称块**整块**没被
        OCR 检出（视频号实测 t=106.4：`6**：比同级别合资强多了` 的正文块左沿 106，
        正常行 42~46）。

    判据两头都硬：没有掩码昵称的药丸块、正文贴着 x_text 的行、已经有用户名的行（掩码徽章
    补回用户名的那种除外 —— 它的冒号确实丢了），一个框都不补；找到的点对还必须紧贴正文
    左沿（``_COLON_PROBE_LEFT`` 以内）。
    """
    tb = c.get("text_box")
    if not tb:
        return
    kw = {}
    if params:
        kw = {"bg_max_v": float(params.get("colon_bg_max_v", 110.0)),
              "v_min": float(params.get("colon_v_min", 100.0)),
              "v_delta": float(params.get("colon_v_delta", 60.0))}
    cb = c.get("colon_box")
    if cb:
        hint = (cb[0] + cb[2]) / 2
        probe = [tb[0], tb[1], tb[2] + _COLON_PROBE_RIGHT, tb[3]]
        pix = colon_box_by_pixels(frame, probe, hint=hint, **kw)
        if pix:
            c["colon_box"] = pix
        return
    bt = c.get("badge_text") or ""
    if not params:
        return
    # 「已有用户名」不再是「不补框」的理由：`badge_name_recover` 会从徽章块把掩码昵称
    # 拼成用户名，而昵称+冒号并进徽章块的行（`badge_text='4冰***'`、正文从冒号右边起）
    # 文本里根本没有冒号 —— 用户名有值、colon_box 却一直是 None（A9L 抖音 seg1 t=80.1
    # 第 2~4 行）。掩码徽章这条证据还在，照旧补框；其它有用户名的行仍不补（旧行为）。
    masked_badge = "*" in bt
    if c.get("username") and not masked_badge:
        return
    if masked_badge:
        probe = [tb[0] - _COLON_PROBE_LEFT, tb[1], tb[2] + _COLON_PROBE_RIGHT, tb[3]]
    else:
        # 昵称块**整块**没被 OCR 检出（正文块从 x_text 右边很远才开始）—— 同
        # ``video_rows`` 的 dropped_nick。只放宽到 x_text，别扫进徽章列；
        # 有任何徽章文本（等级/粉丝药丸）就不算这条证据。
        if bt:
            return
        x_text = int(c.get("x_text") or 0)
        if tb[0] - x_text < _COLON_DROPPED_NICK_GAP:
            return
        probe = [max(x_text, tb[0] - _COLON_PROBE_LEFT),
                 tb[1], tb[2] + _COLON_PROBE_RIGHT, tb[3]]
    hint = tb[0] - 8                  # 冒号就在正文块左沿左边几个像素
    pix = colon_box_by_pixels(frame, probe, hint=hint, **kw)
    # 补框只认紧贴正文左沿的那一对：徽章列/头像里的点对离得远，一律不要（防误标）。
    if pix and not (tb[0] - _COLON_PROBE_LEFT
                    <= (pix[0] + pix[2]) / 2 <= tb[0] + 5):
        return
    if pix:
        c["colon_box"] = pix


def _font(size: int):
    """带中文字形的字体 —— 用户名/正文里有汉字，DejaVu 那些会画成方块。"""
    from PIL import ImageFont
    for p in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _label_width(text: str, size: int = 20) -> int:
    """标签底框宽度：汉字/emoji 约一个字宽，ASCII 约半个。"""
    return sum(size if ord(ch) > 0x2E80 else size * 0.55 for ch in text)


def draw_frames(video: str, comments: list[dict], out_dir, *,
                quality: int = 90, start: float = 0.0,
                sample_mode: str = "adaptive",
                colon_marks: bool = True,
                recolon: bool = True,
                colon_params: dict[str, dict] | None = None) -> list[dict]:
    """把每条评论的 ROI 画回帧上，按帧输出 ``<out_dir>/<t>_roi.jpg``。

    每个 ROI 画粗彩框 + 编号 + 用户名（编号按屏幕从上到下）；`text_box`（纯 OCR 文字框）
    另画一道细灰框 —— **两者之差就是被扩进来的部分**（左边等级徽章 + 右边框外表情），
    扩没扩对一眼就能看出来。
    每个表情再画一道细黄框 + 它的 token（认不出的画 ``?``+guess）—— 表情是**认出来的**，
    不是读出来的，只有画出来才能一条条核（分数高不等于认对，形近对就是这么骗过阈值的）。
    ``colon_marks``（默认开）：每行把**昵称与正文之间的分隔冒号**用青色小方框圈出来
    （坐标见 ``colon_box_by_pixels`` / ``colon_box_in_row``）—— 解析层是按「冒号前的部分
    算不算昵称」切的，圈出来就能逐条核这个切点。它只影响标注图，交付图（export）不受影响。
    ``recolon``（默认开）：画之前用**这一帧的像素**把冒号框重算一遍并就地写回（见
    ``refresh_colon_box``）—— ``--rows`` 重画时行里带的是旧版本的框（打码昵称贴着冒号
    时会偏 10~25px），底帧在手就顺手修对，画的和落盘的一致。
    ``colon_params``：``{面板: {colon_bg_max_v, colon_v_min, colon_v_delta}}`` ——
    **只有平台档案显式写了这几个键的面板**才会传进来，用来在浅色底上开像素找冒号，
    并允许给「昵称+冒号被并进药丸块」的行**补**一个冒号框（见 ``refresh_colon_box``）。
    传进来的应该是**逐帧**的评论（去重前），这样每张图对得上当时屏幕上的内容。

    **底帧必须与抽行那条路取到同一帧**（``start`` / ``sample_mode`` 就是为此传进来的）：
    两条路认帧的方式本来就不一样 —— ``adaptive`` 抽行是**顺序解码、逐帧数计数器**
    （``sample_frames_adaptive`` 一帧一 ``read()``，``t = idx/fps``）；``interval`` /
    ``backoff`` 抽行是**按 t seek**（``set(POS_FRAMES, round(t*fps))``）。画图这边一旦
    一律 seek，在 adaptive 出的行上就会错帧：这条录屏是**变帧率**的（OpenCV 报的 fps
    是 总帧数/时长 的名义值），按索引 seek 的落点在关键帧处会跳，实测 t=82.6s 起晚
    27 帧、t=91.7s 晚 27 帧、t=206.5s 晚 218 帧（逐帧像素匹配，MAE=0）。屏幕还在滚，
    错帧 ⇒ 框和标签整体错行（bug-264）。

    返回**出图清单**（每张一条：``{"t","path","comments"}``），供 ``summary.json``
    登记 —— 图写在哪、哪一帧、画了几条，从 summary 就能反查，不用去猜文件名规则。
    """
    import cv2
    from PIL import Image, ImageDraw

    groups: dict[float, list[dict]] = {}
    for c in comments:
        if c.get("roi"):
            groups.setdefault(c.get("t"), []).append(c)
    out_dir = Path(out_dir)
    # **先清掉上一轮的图**：评论数变少（或变成 0）时旧图会留在原地，画着一张**已经不成立**
    # 的标注 —— 看的人只会以为还在误标（实测 2026-10-12：浮层卡修完之后 seg1 的 3 条全没了，
    # 而 frames/ 里 20 张旧图还在，用户报的那张 `00000.0_roi.jpg` 依旧画着三个卡框）。
    stale = sorted(out_dir.glob("*_roi.jpg")) if out_dir.is_dir() else []
    for p in stale:
        p.unlink()
    if not groups:
        if stale:
            _log(f"[frames] 0 条评论 ⇒ 清掉上一轮的 {len(stale)} 张旧标注图，不写新图")
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"无法打开视频（画标注图）：{video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    font = _font(20)
    font_s = _font(14)          # 表情 token 的小字
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1520)
    # 帧计数器与抽行同口径：抽行先 seek 到锚点 ``start``、再从那里一路数下来
    # （见 ``sample_frames_adaptive``），所以这里也从同一个锚点起数。
    pos = int(round(start * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
    _log(f"[frames] 取帧口径 {sample_mode}（锚点 t={start:g}s ⇒ 帧计数器 {pos}）")
    manifest: list[dict] = []
    try:
        for t in sorted(groups):
            want = int(round(t * fps))
            if sample_mode == "adaptive":
                # 顺序推到位，**不 seek**：seek 的落点会漂（见函数头），一旦漂了，
                # 框就画在另一时刻的画面上，而屏幕一直在滚 —— 这就是「乱标」。
                if want < pos:                  # t 升序，正常不会发生；防呆重锚
                    _log(f"[frames] 警告：t={t:g}s 要回退取帧，重新对齐锚点")
                    pos = int(round(start * fps))
                    cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                while pos < want and cap.grab():
                    pos += 1
                ok, frame = cap.read()
                if ok:
                    pos += 1                    # 这一帧已消费，计数器跟着走
                else:
                    # 文件里的坏 h264 段（A9L 抖音 seg1 t≈957s 实测）会让顺序解码中断；
                    # 跳过坏段再读（见 ocr/decode.py），别把坏帧当成片尾。
                    newpos, frame = read_recovering(cap, max(pos, want))
                    ok = frame is not None
                    if ok:
                        pos = newpos + 1
            else:
                # interval / backoff：抽行本身就是按 t seek 的，这里照样 seek 才同帧。
                cap.set(cv2.CAP_PROP_POS_FRAMES, want)
                ok, frame = cap.read()
            if not ok:
                continue
            # 底图必须是**没画过框**的原始帧：这些图是要当素材/验收件用的
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            d = ImageDraw.Draw(img)
            iw, ih = img.size
            # 编号**按面板各自从 1 排**（两个面板是两个直播间，全局编号会交错成
            # 左 1..6 右 7..8 左 9.. 这种读不顺的序列）
            for panel in sorted({str(c.get("panel")) for c in groups[t]}):
                cs = sorted([c for c in groups[t] if str(c.get("panel")) == panel],
                            key=lambda c: (c["roi"][1], c["roi"][0]))
                for i, c in enumerate(cs, 1):
                    x1, y1, x2, y2 = c["roi"]
                    col = _PALETTE[(i - 1) % len(_PALETTE)]
                    d.rectangle([x1, y1, x2, y2], outline=col, width=3)
                    tb = c.get("text_box")
                    if tb and list(tb) != list(c["roi"]):
                        d.rectangle(tb, outline=(205, 205, 205), width=1)
                    # 分隔冒号：青色小方框（坐标优先按像素找的两点，找不到才按字符比例估，
                    # 见 colon_box_by_pixels / colon_box_in_row）。圈的是「昵称 | 正文」这个切点。
                    if colon_marks and recolon:
                        refresh_colon_box(frame, c,
                                          (colon_params or {}).get(c.get("panel")))
                    cb = c.get("colon_box") if colon_marks else None
                    if cb:
                        cx1, cy1, cx2, cy2 = (int(v) for v in cb)
                        d.rectangle([max(0, cx1 - 2), max(0, cy1 - 2),
                                     min(iw - 1, cx2 + 2), min(ih - 1, cy2 + 2)],
                                    outline=(0, 255, 255), width=2)
                    label = f"{i} {c.get('username') or '?'}: {c.get('content', '')}"[:26]
                    ly = y1 - 24 if y1 >= 24 else y1 + 2
                    d.rectangle([x1, ly, x1 + _label_width(label) + 10, ly + 23],
                                fill=(0, 0, 0))
                    d.text((x1 + 4, ly + 2), label, fill=col, font=font)
                    for e in c.get("emojis") or []:
                        ex1, ey1, ex2, ey2 = e["box"]
                        d.rectangle([ex1, ey1, ex2, ey2], outline=(255, 220, 0), width=2)
                        tok = e.get("token") or f"?{e.get('guess', '')}"
                        if int(e.get("n", 1)) > 1:
                            tok = f"{tok}×{e['n']}"
                        # 标签贴框下沿；底部两行放不下就翻到框上面（别裁出画面）
                        ty = ey2 + 1
                        if ty + 16 > fh:
                            ty = max(0, ey1 - 16)
                        d.rectangle([ex1, ty, ex1 + _label_width(tok, 14) + 6, ty + 15],
                                    fill=(0, 0, 0))
                        d.text((ex1 + 3, ty), tok, fill=(255, 220, 0), font=font_s)
            dst = out_dir / f"{t:07.1f}_roi.jpg"
            img.save(dst, quality=quality)
            manifest.append({"t": t, "path": str(dst), "comments": len(groups[t])})
    finally:
        cap.release()
    return manifest
