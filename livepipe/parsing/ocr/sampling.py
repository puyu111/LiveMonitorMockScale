"""livepipe.parsing.ocr.sampling — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from .decode import read_recovering



def sample_frames(video: str, start: float, duration: float | None,
                  interval: float, max_frames: int | None):
    """每隔 ``interval`` 秒产出一帧 ``(t_秒, frame_bgr)``。"""
    import cv2

    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"无法打开视频：{video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    end = start + duration if duration else (total / fps)
    t, n = start, 0
    try:
        while t < end:
            if max_frames is not None and n >= max_frames:
                break
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
            ok, frame = cap.read()
            if not ok:
                # 坏段恢复（见 decode.py）：按 t seek 正好落进坏帧时，跳过去接着抽。
                _idx, frame = read_recovering(cap, int(round(t * fps)))
                if frame is None:
                    break
            yield t, frame
            t += interval
            n += 1
    finally:
        cap.release()


# --------------------------------------------------------------------------- #
# 5b. 自适应抽帧（内容触发）—— 治固定 interval 的两个毛病
# --------------------------------------------------------------------------- #
# 固定 ``interval`` 有两个互相矛盾的毛病：静止时对着一模一样的画面反复 OCR（白拍），
# 快滚时又可能整条评论溜过两次拍摄之间（漏条）。正解是**按面板内容的位移**决定何时拍：
#   · 位移累计到 ``safety_rows`` 行 ⇒ 拍（不漏的安全网；阈值 < _best_shift 的 max_shift=8，
#     所以连续两次拍摄的间隔永远对得齐 —— 超了会让跨帧对齐放弃、重复评论冒到输出里）。
#   · 动过之后停稳 ``settle_frames`` 帧 ⇒ 拍（一次抓全一屏，且抓的是最清楚的那一帧）。
#   · 没在滚、但底部入口行变了 ⇒ 判「新评论弹进来」⇒ 拍（C）。
# 位移用**行投影的互相关**估（不是整帧 diff）—— 礼物/点赞动画不平移行网格，score 低，
# 天然不触发，不会为动画白拍一帧。
SAMPLE_MODES = ("interval", "backoff", "adaptive")

SAMPLE_DEFAULTS: dict = {
    "stride": 1,          # 每 N 个解码帧估一次位移（1 = 每帧都估）
    "safety_rows": 3.0,   # 累计位移 ≥ 这么多行 ⇒ 强制拍（不漏；上限 5，见 config._validate）
    "settle_frames": 3,   # 停稳多少帧后拍（主力触发，一次抓全一屏）
    "eps_px": 1.0,        # 相邻帧位移 < 它算「没动」（噪声地板）
    "entry_thr": 8.0,     # 底带灰度均差 > 它且没在滚 ⇒ 判入口有新评论
    "max_gap_s": 5.0,     # 距上次拍超过这么久也拍一次（0 = 关；防长时间不触发）
    "min_interval": 0.2,  # backoff 的间隔下限
    "max_interval": 6.0,  # backoff 的间隔上限
    "min_score": 0.3,     # 位移估计互相关下限，低于它当作「没有平移」
}


def _roi_profile(frame_bgr, rect) -> list:
    """面板 ROI 的灰度**行投影**（每行一个数 = 该行亮度均值，长度 = 面板高）。

    只留纵向结构 —— 评论是整体**上移**的，行投影正好把「移了几像素」暴露成一维平移。
    """
    import numpy as np
    x1, y1, x2, y2 = (int(v) for v in rect)
    roi = np.asarray(frame_bgr[y1:y2, x1:x2], dtype=np.float32)
    gray = roi @ np.array([0.114, 0.587, 0.299], dtype=np.float32)   # BGR→灰度（Rec.601）
    return gray.mean(axis=1).tolist()


def _est_shift_px(a, b, max_px: int) -> tuple:
    """估 ``b`` 相对 ``a`` 的纵向平移 ``s``（px）：内容上移 s ⇒ ``b[i] ≈ a[i+s]``。

    返回 ``(s, score)``；score 是重叠段去均值后的归一化互相关 ∈[-1,1]。用行投影做，
    **非平移的变化（动画、某条老评论被补心）不产生高 score** —— 调用方按 ``min_score``
    过滤，这正是它比整帧 diff 抗噪的地方。
    """
    import numpy as np
    A = np.asarray(a, dtype=np.float32)
    B = np.asarray(b, dtype=np.float32)
    best_s, best = 0, -2.0
    for s in range(-max_px, max_px + 1):
        lo = max(0, -s)
        hi = min(len(B), len(A) - s)
        if hi - lo < 8:                       # 重叠太短，不可信
            continue
        x = B[lo:hi]
        y = A[lo + s:hi + s]
        x = x - x.mean()
        y = y - y.mean()
        den = float(np.sqrt(float((x * x).sum()) * float((y * y).sum())))
        sc = float((x * y).sum() / den) if den > 1e-6 else -2.0
        if sc > best:
            best, best_s = sc, s
    return best_s, best


class _AdaptiveSampler:
    """内容触发抽帧的**状态机**（纯逻辑：只吃行投影和标量，不碰视频/OCR，便于自检）。

    ``observe(t, {面板: 行投影})`` 每前进一个解码帧调一次，返回
    ``(fire, {面板: 瞬时位移px}, {面板: (类别, 说明)})``；类别 ∈ move / settle / entry / gap。
    """

    def __init__(self, pitches: dict, opts: dict | None = None, estimate=_est_shift_px,
                 heights: dict | None = None):
        o = dict(SAMPLE_DEFAULTS)
        o.update(opts or {})
        self.o = o
        self.estimate = estimate
        self.pitches = {p: float(v or 1) for p, v in pitches.items()}
        # 位移搜索半径：滚动是逐帧小位移，2 个行距足够（也把估值压得很便宜）。
        self.max_px = {p: int(max(8, round(2.2 * v))) for p, v in self.pitches.items()}
        # 位移安全网的**像素**阈值：默认 safety_rows 行，但**不超过半个可见屏**——
        # 右面板只有 ~4 行，若安全网取 3 行，一条只可见 3 行的评论可能整条溜过两次
        # 拍摄之间（漏）。给 heights（面板高 px）就按「行数 = 高/行距」把阈值收紧。
        self.safety_px = {}
        for p, pitch in self.pitches.items():
            cap_rows = None
            if heights and heights.get(p):
                cap_rows = max(1.0, (float(heights[p]) / pitch) / 2.0)
            eff = o["safety_rows"] if cap_rows is None else min(o["safety_rows"], cap_rows)
            self.safety_px[p] = eff * pitch
        self.st = {p: {"prev": None, "cap": None, "acc": 0.0, "still": 0,
                       "moved": False, "cap_t": None} for p in self.pitches}

    def observe(self, t, profiles: dict) -> tuple:
        fire = False
        fired: dict = {}          # {面板: 这一帧的瞬时位移 px}（越接近 0 拍得越清楚）
        reasons: dict = {}
        for p, prof in profiles.items():
            if p not in self.st:                  # 不在初始化集合里的面板：忽略
                continue
            got = self._step(p, t, prof)
            if got is not None:
                cat, why, motion = got
                fire = True
                fired[p] = motion
                reasons[p] = (cat, why)
        return fire, fired, reasons

    def _step(self, p, t, prof):
        st = self.st[p]
        if st["prev"] is None:                    # 首帧：只记基线，不触发
            st["prev"] = st["cap"] = prof
            st["cap_t"] = t
            return None
        pitch = self.pitches[p]
        s, score = self.estimate(st["prev"], prof, self.max_px[p])
        if score < self.o["min_score"]:
            s = 0                                 # 平移证据不足 ⇒ 当没动（不为动画白拍）
        st["prev"] = prof
        st["acc"] += s
        if abs(s) >= self.o["eps_px"]:
            st["moved"] = True
            st["still"] = 0
        else:
            st["still"] += 1
        cat = why = None
        if abs(st["acc"]) >= self.safety_px[p]:
            cat, why = "move", f"位移{st['acc']:+.0f}px≥{self.safety_px[p] / pitch:g}行"
        elif st["moved"] and st["still"] >= self.o["settle_frames"]:
            cat, why = "settle", f"停稳{st['still']}帧"
        elif (abs(s) < self.o["eps_px"]          # 这一帧**没在平移**（不是「累计还小」）
              and self._band_diff(st["cap"], prof, pitch) > self.o["entry_thr"]):
            cat, why = "entry", "入口有新评论"
        elif (self.o["max_gap_s"] and st["cap_t"] is not None
              and (t - st["cap_t"]) >= self.o["max_gap_s"]):
            cat, why = "gap", f"超时{self.o['max_gap_s']:g}s"
        if cat is not None:
            st["acc"], st["moved"], st["still"] = 0.0, False, 0
            st["cap"], st["cap_t"] = prof, t
            return cat, why, abs(s)               # 附带这一帧的瞬时位移（供去重挑最清楚的一次）
        return None

    @staticmethod
    def _band_diff(a, b, pitch, ratio: float = 1.2) -> float:
        """底带（入口行，约 1.2 个行距）的灰度均差 —— 判「底部有没有新墨迹」。"""
        n = min(max(1, int(ratio * pitch)), len(a), len(b))
        return sum(abs(x - y) for x, y in zip(a[-n:], b[-n:])) / n


def sample_frames_adaptive(video, start, duration, panels, *, opts: dict | None = None,
                           max_frames: int | None = None, stats: dict | None = None):
    """内容触发抽帧：顺序解码，按面板位移/停稳/入口决定何时交给下游 OCR。

    产出 ``(t, frame_bgr, fired)``；``fired`` 是 ``{面板: 瞬时位移 px}``，键就是**这一帧
    要处理的面板**（首帧为 ``None`` = 全部）。两侧窗口独立滚动、触发节奏不同，所以按面板
    过滤 —— 这一帧没触发的面板不必再 OCR（每面板一次 OCR 才是这项优化真正省的地方）。
    位移值随行带下去（``motion_px``），去重时用来在同一个 ``rid`` 里挑**拍得最清楚**的一次。
    """
    import cv2
    o = dict(SAMPLE_DEFAULTS)
    o.update(opts or {})
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"无法打开视频：{video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    end = start + duration if duration else (total / fps if total else float("inf"))
    rects = {p: pan["rect"] for p, pan in panels.items()}
    smp = _AdaptiveSampler(
        {p: pan.get("row_pitch") for p, pan in panels.items()}, o,
        heights={p: pan["rect"][3] - pan["rect"][1] for p, pan in panels.items()})
    stride = max(1, int(o["stride"]))
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(start * fps)))
    idx, k, emitted, first = int(round(start * fps)), 0, 0, True
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                # 文件里的坏 h264 段（A9L 抖音 seg1 帧 42912 / t≈957s 实测）会让顺序解码
                # 中途失败；跳过去接着读，别把「读到坏帧」当成「视频结束」。
                newidx, frame = read_recovering(cap, idx)
                if frame is None:
                    break
                idx = newidx
            t = idx / fps
            if t >= end:
                break
            if first:
                yield t, frame, None                # 首帧：先拍一帧给下游一个基线
                smp.observe(t, {p: _roi_profile(frame, r) for p, r in rects.items()})
                first, emitted = False, emitted + 1
            elif k % stride == 0:
                fire, fired, reasons = smp.observe(
                    t, {p: _roi_profile(frame, r) for p, r in rects.items()})
                if fire and (max_frames is None or emitted < max_frames):
                    yield t, frame, dict(fired)      # fired = {面板: 瞬时位移 px}
                    emitted += 1
                    if stats is not None:
                        for p, (cat, why) in reasons.items():
                            bucket = stats.setdefault(p, {})
                            bucket[cat] = bucket.get(cat, 0) + 1
                            bucket["last"] = f"t={t:.1f} {why}"
            k += 1
            idx += 1
            if max_frames is not None and emitted >= max_frames:
                break
    finally:
        cap.release()


def sample_frames_backoff(video, start, duration, interval, panels=None, *,
                          opts: dict | None = None, max_frames: int | None = None):
    """B 降级版：仍按 ``interval`` 抽样，但**只在画面真变了才拍**、并按变化自适应间隔。

    比 ``adaptive`` 便宜（不做逐帧解码，每 ``interval`` 才 seek 一帧）；代价是对「突然
    爆量」只能事后反应 —— 所以当 ``adaptive`` 嫌重时的降级。产出 ``(t, frame, None)``，
    即每次都交给下游处理**所有**面板（B 不做面板级过滤）。
    """
    import cv2
    o = dict(SAMPLE_DEFAULTS)
    o.update(opts or {})
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"无法打开视频：{video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    end = start + duration if duration else (total / fps if total else float("inf"))
    rects = {p: pan["rect"] for p, pan in panels.items()} if panels else {}
    max_px = {p: int(max(8, round(2.2 * (panels[p].get("row_pitch") or 68))))
              for p in rects}
    lo, hi = float(o["min_interval"]), float(o["max_interval"])
    cur = max(lo, min(hi, float(interval)))
    prev, t, emitted = None, start, 0
    try:
        while t < end:
            if max_frames is not None and emitted >= max_frames:
                break
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
            ok, frame = cap.read()
            if not ok:
                _idx, frame = read_recovering(cap, int(round(t * fps)))
                if frame is None:
                    break
            moved = True
            if prev is not None:
                moved = False
                for p, r in rects.items():
                    s, sc = _est_shift_px(prev[p], _roi_profile(frame, r), max_px[p])
                    if sc >= o["min_score"] and abs(s) >= o["eps_px"]:
                        moved = True
                        break
            if moved:
                yield t, frame, None
                emitted += 1
                cur = max(lo, cur / 2)
            else:
                cur = min(hi, cur * 2)
            if rects:
                prev = {p: _roi_profile(frame, r) for p, r in rects.items()}
            t += cur
    finally:
        cap.release()
