"""livepipe.parsing.ocr.pills — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from collections import Counter
import re

from ..common.log import _log
from ..ocr.engine import _rapidocr
from ..text.badges import _badge_digits



# --------------------------------------------------------------------------- #
# 5b. 等级标**二次读数**：把药丸带裁出来放大、只跑识别（跳过 det）
# --------------------------------------------------------------------------- #
# 为什么要有这一层：等级标是身份的一部分（交付层认的就是 ``(昵称, 等级标)``，见
# ``account_id``），可面板级 OCR 只在**三成上下**的行里把药丸里的数字检出成独立文本块
# —— 白字压在浅色药丸上、字又小，det 常常压根不提框，于是 ``_in_badge_gutter`` 什么也
# 收不到，这一条评论的等级标就是「没读出来」。交付层把「没读出来」当成一个档，同名的
# 几个人就被并进同一个目录（用户 2026-10-14 报的 ``飞___-4`` 里坐着 ◆23 / ◆4 / ◆9 / ◆4
# 四个人）。
#
# 药丸的位置是**版式常数**（正文框左沿往左 54px、行中心上下 11px），所以这一层不需要
# det：按行裁出来直接送 ``TextRecognizer``（支持批量，实测 **~15ms/张**，而 det+rec 要
# ~560ms/张 —— det 会把小图按 ``limit_side_len`` 的 ``min`` 规则上采样到 736，白付一整套
# 检测的钱）。
#
# 两条边界，都是为了「宁可漏归一，不可错归并」：
#   * **只补不改** —— 已有读数的记录一个字节都不动（面板读数更直接）；
#   * **跨帧一致才算数** —— 同一条评论至少两帧读出同一个值、且是严格多数才认
#     （见 :func:`_pill_vote`）。单帧读数会错（实测把 ◆ 图标读成数字：``◆2`` → ``22``），
#     而一个错的等级标 = 一个错的身份，比「没读出来」更坏。
#
# 还有一条前提，写在这里免得再踩：**回原帧必须用抽帧那把尺子**（见
# :class:`_SampledFrameReader`）。这一层最早按 ``CAP_PROP_POS_FRAMES`` 定位原帧，而这批
# 素材是 VFR，定位会偏**半秒 ≈ 一行**：药丸带正好搭到**下一行**的昵称/药丸上，读出来的
# 是**别人的等级标**（实测 seg4 与面板读数只对上 3.5%，且像素能直接看到裁错了行 ——
# bug-384）。偏一行比读不出来坏得多：它会把两个用户并成一个。
PILL_BAND_W = 54         # 药丸带宽度：从正文框左沿往左这么多（药丸实测宽 30~45）
PILL_BAND_PAD_R = 2      # 右界 = 正文框左沿 − 这么多，把正文第一个字排除在外
PILL_BAND_DY = 11        # 行中心上下各取多少（药丸高实测 15px，行高 25~35px）
PILL_BATCH = 32          # 一次送识别几张
PILL_MIN_SCORE = 0.6     # 单帧读数的分数下限
PILL_MIN_AGREE = 2       # 跨帧至少这么多帧读出**同一个值**才认

_PILL_DIGITS_RE = re.compile(r"\d+")


def pill_band_box(text_box, *, width: int | None = None, height: int | None = None,
                  x_min: int | None = None, w: int = PILL_BAND_W,
                  pad_r: int = PILL_BAND_PAD_R,
                  dy: int = PILL_BAND_DY) -> tuple[int, int, int, int] | None:
    """评论的**正文框** → 药丸带（原图坐标）；太窄/越界/裁不到药丸列 ⇒ ``None``。

    右界取正文框左沿而不是 ``x_text``：药丸被 OCR 并进昵称的行（``13白***``）正文框
    左沿已经在药丸左边，裁出来几乎是空的 ⇒ 读不出 ⇒ **不会误读**（那种行归
    ``normalize_badge_alias`` 管）。

    ``x_min`` 是这条评论的 ROI 左沿（= 药丸列的左边界）：裁出来的带子右沿必须**进到**
    药丸列里（``x2 > x_min``），否则这一刀全落在药丸列**左边**的空白/面板外 —— 那里
    没有药丸，读到什么都跟我们无关。没有徽章列的版式上 ROI 左沿就贴着正文列，
    此时一律返回 ``None``（那套版式本来也没有药丸可读）。
    """
    if not text_box:
        return None
    x2 = int(text_box[0]) - pad_r
    if x_min is not None and x2 <= int(x_min) + pad_r:
        return None
    x1 = max(0, x2 - w)
    yc = (float(text_box[1]) + float(text_box[3])) / 2
    y1, y2 = int(yc - dy), int(yc + dy)
    if width is not None:
        x2 = min(x2, int(width))
    if height is not None:
        y2 = min(y2, int(height))
    if x2 - x1 < 12 or y2 - y1 < 8:
        return None
    return x1, y1, x2, y2


def pill_reading(text: str, score: float) -> str | None:
    """识别结果 → 等级数字（分数太低 / 没数字 ⇒ ``None``）。

    取**最后一段连续数字**：药丸左端的 ◆ 图标常被读成数字（实测 ``◆13`` → ``013``、
    ``◆2`` → ``22``），它一定在左边，等级数字在右边。再把**前导零**砍掉 —— 那一位
    十有八九就是图标（``013`` ⇒ ``13``）；砍完空了（整段就是图标）就不算读数。
    这样「同一帧读成 ``013``、另一帧读成 ``13``」才会投成**同一票**，而不是平票。

    粉丝团等级从 1 起、没有 0 级，所以砍前导零不会碰坏任何真的等级。
    """
    if score < PILL_MIN_SCORE:
        return None
    runs = _PILL_DIGITS_RE.findall(text or "")
    return (runs[-1].lstrip("0") or None) if runs else None


def _pill_vote(readings, *, min_agree: int = PILL_MIN_AGREE) -> str | None:
    """跨帧投票：**至少 ``min_agree`` 帧读出同一个值**、且过半数 ⇒ 认；否则 ``None``。

    ``readings``：这条评论各帧的单帧读数（``None``/空串 = 那帧没读出来）。
    平票、只有一帧读出、多数不过半 —— 一律不认（「宁可漏归一」）。
    """
    cnt = Counter(r for r in readings if r)
    if not cnt:
        return None
    val, n = cnt.most_common(1)[0]
    if n < min_agree or n * 2 <= sum(cnt.values()):
        return None
    return val


def _sequential_ruler(sample_mode: str | None) -> bool:
    """回读原帧要不要**顺序解码** —— 拿抽帧模式定，因为尺子必须是同一把。

    ``adaptive`` 抽帧走的是 ``cap.read()`` 顺序解码（``sample_frames_adaptive``），
    ``interval`` / ``backoff`` 走的是 ``seek`` 后读（``sample_rows`` / ``sample_frames_backoff``）。
    判错的代价是读错一行 = 认错一个身份，所以这条判据单独成函数、单独有断言。
    """
    return (sample_mode or "") == "adaptive"


class _SampledFrameReader:
    """按**抽帧那把尺子**回读原帧 —— ``adaptive`` 抽帧是顺序解码，回读也必须顺序解码。

    ``sample_frames_adaptive`` 是「顺序解码 + 计数器」：``t = idx / fps``，``idx`` 是从
    ``round(start × fps)`` 数下来的**真实帧号**（所以 ``t × fps`` 恒为整数 —— seg1 的 947
    个采样点实测无一例外）。反过来说：**帧号本来就算得准，错的是 seek**。
    ``CAP_PROP_POS_FRAMES`` 的落点由容器的关键帧/时间基决定，而这批录屏是 **VFR**（cv2 对
    同一批文件报出 35.77 / 46.10 两种帧率），实测**偏到半秒前（≈一行）**：药丸带搭到下一行
    上，读出来的是别人的等级标（bug-384）。所以这里只认**顺序 grab**：从锚点一路数过去，
    落在目标帧号上。

    这把尺子与 ``export_user_images.FrameReader`` 是同一把（bug-264 的教训 —— 交付图的底帧
    也是这么取的），所以交付图上看到的那个药丸，就是这里读的那个药丸。
    ``interval`` / ``backoff`` 抽帧本身就是 ``seek`` 取帧的，那两种模式照旧 seek 才**同帧**。
    """

    def __init__(self, cap, fps: float, *, start: float = 0.0,
                 sequential: bool = True, log=None):
        import cv2
        self.cv2, self.cap = cv2, cap
        self.fps = float(fps) or 25.0
        self.sequential = bool(sequential)
        self.anchor = int(round(float(start) * self.fps))
        self.pos = self.anchor
        self._log = log
        if self.sequential:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.anchor)

    def read_at(self, idx: int):
        """取帧号 ``idx`` 的一帧（BGR）或 ``None``。顺序模式要求 ``idx`` **非降序**。"""
        if not self.sequential:
            self.cap.set(self.cv2.CAP_PROP_POS_FRAMES, int(idx))
            ok, frame = self.cap.read()
            return frame if ok else None
        if idx < self.pos:
            # 正常不会发生（调用方按升序走）。真发生了就重锚并**出声** —— 悄悄给错帧
            # 正是这一层要根除的毛病。
            if self._log:
                self._log(f"  [badge] 要回退取帧（idx={idx} < {self.pos}）—— 重锚到 "
                          f"{self.anchor} 再顺序数过去")
            self.pos = self.anchor
            self.cap.set(self.cv2.CAP_PROP_POS_FRAMES, self.anchor)
        while self.pos < idx and self.cap.grab():
            self.pos += 1
        ok, frame = self.cap.read()
        if ok:
            self.pos += 1
        return frame if ok else None


def reread_pills(video: str, comments: list[dict], *, start: float = 0.0,
                 sample_mode: str = "interval", log=None) -> dict[int, str]:
    """给**没有等级标读数**的评论补二次读数 → ``{comments 下标: 数字}``。

    ``comments`` 必须是**去重之前**的逐帧评论（每条带 ``t`` 与 ``text_box``）：去重之后
    就没有「同一条评论的其它帧」可以投票了（投票在 :func:`dedup_comments` 里做）。

    帧号按 ``round(t × fps)`` 算（``t`` 是**文件内绝对秒**，见抽帧那一段），但**取帧**要走
    :class:`_SampledFrameReader` —— 同一条评论的各次拍摄落在同一个帧上时共用一次解码。
    """
    log = log or _log
    need = [i for i, c in enumerate(comments)
            if not _badge_digits(c.get("badge_text")) and c.get("text_box")]
    if not need:
        return {}
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        log(f"  [badge] 打不开视频，二次读数跳过：{video}")
        return {}
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        sequential = _sequential_ruler(sample_mode)
        reader = _SampledFrameReader(cap, fps, start=start, sequential=sequential, log=log)
        eng = _rapidocr()
        eng.text_rec.rec_batch_num = PILL_BATCH
        by_idx: dict[int, list[int]] = {}
        for i in need:
            by_idx.setdefault(int(round(float(comments[i]["t"]) * fps)), []).append(i)
        out: dict[int, str] = {}
        batch: list = []
        keys: list[int] = []

        def flush() -> None:
            if not batch:
                return
            res = eng.text_rec(batch)[0]
            if res is not None and len(res) == len(batch):
                for k, hit in zip(keys, res):
                    if not isinstance(hit, (list, tuple)) or len(hit) < 2:
                        continue                  # 这一张没读出来（引擎给 None）—— 少一票
                    r = pill_reading(hit[0], float(hit[1]))
                    if r:
                        out[k] = r
            else:
                # 条数对不上就**整批作废**：错位会把 A 的读数记到 B 头上，
                # 那比读不出来严重得多（宁可漏归一）。
                log(f"  [badge] 识别返回 {0 if res is None else len(res)} 条、送进 {len(batch)} 张"
                    f" —— 这一批作废")
            batch.clear()
            keys.clear()

        n_frame = 0
        n_skip = 0
        for idx in sorted(by_idx):
            frame = reader.read_at(idx)
            if frame is None:
                log(f"  [badge] 取帧失败 idx={idx}（这帧 {len(by_idx[idx])} 条）—— 跳过")
                continue
            n_frame += 1
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)   # 与抽行同口径（RGB）
            h, w = frame.shape[:2]
            for i in by_idx[idx]:
                c = comments[i]
                roi = c.get("roi") or []
                box = pill_band_box(c.get("text_box"), width=w, height=h,
                                    x_min=roi[0] if roi else None)
                if box is None:
                    n_skip += 1
                    continue
                x1, y1, x2, y2 = box
                crop = frame[y1:y2, x1:x2]
                if crop.size:
                    batch.append(np.ascontiguousarray(crop))   # 切片是视图：交给引擎前先拷实
                    keys.append(i)
                    if len(batch) >= PILL_BATCH:
                        flush()
        flush()
        ruler = ("顺序解码取帧，与抽帧同源" if sequential
                 else f"按帧号 seek 取帧（抽帧模式 {sample_mode or 'interval'} 就是 seek 的）")
        log(f"  [badge] 二次读数：{len(need)} 条待补 / {n_frame} 帧 → 单帧读出 {len(out)} 条"
            + (f"（另有 {n_skip} 条裁不到药丸列，跳过）" if n_skip else "")
            + f"（{ruler}；只补不改 + 跨帧投票，见 dedup 那行账）")
        if n_skip == len(need):
            log("  [badge] 一条都裁不到药丸列 ⇒ 这套版式没有左侧药丸列（或 ROI 左沿贴着正文列）"
                "—— 二次读数这套在这份素材上不适用")
        return out
    finally:
        cap.release()


def _reread_for(args, comments: list[dict]) -> dict[int, str] | None:
    """按需跑二次读数（``--badge-reread``，必须有视频）。不跑 ⇒ ``None``。"""
    from ..reporting import _load_summary

    if not getattr(args, "badge_reread", False):
        return None
    if not getattr(args, "video", None):
        _log("  [badge] --badge-reread 要配 --video（药丸带要从原帧裁）—— 跳过")
        return None
    # ``start`` / ``sample_mode`` 决定**回读原帧那把尺子**（见 ``_sequential_ruler``）。
    # 优先信 ``summary.json`` —— 从 ``--rows`` 复算时它就是这批行的**出身证明**（抽行那一遍
    # 写下的），命令行只作兜底：配了 adaptive 却拿 seek 去回读，会整片读错一行，而且**无声**
    # （bug-264 的同一个坑，bug-384 又栽进去一次）。两边不一致就显形。
    prov = _load_summary(getattr(args, "out", None))
    cfg_mode = getattr(args, "sample_mode", None) or "interval"
    cfg_start = float(getattr(args, "start", 0.0) or 0.0)
    mode = prov.get("sample_mode") or cfg_mode
    start = float(prov["start"]) if prov.get("start") is not None else cfg_start
    if prov and (mode != cfg_mode or abs(start - cfg_start) > 1e-6):
        _log(f"  [badge] 取帧口径以 summary.json（这批行的出身证明）为准："
             f"sample_mode={mode}、start={start:g}"
             f"（命令行给的是 {cfg_mode} / {cfg_start:g}）")
    return reread_pills(args.video, comments, start=start, sample_mode=mode)
