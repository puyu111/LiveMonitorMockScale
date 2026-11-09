"""livepipe.parsing.selftest.sampling — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import sys

from ..ocr.sampling import _AdaptiveSampler
from ..ocr.sampling import _est_shift_px



def _selftest_sampling() -> bool:
    """自适应抽帧的**纯逻辑**断言（注入 stub 位移估计，不需要 cv2，位移估计项要 numpy）。"""
    cases: list[tuple] = []

    class Est:
        """可脚本化的位移估计：直接返回预设的 (shift, score)。"""
        def __init__(self, shift=0, score=1.0):
            self.shift, self.score = shift, score

        def __call__(self, a, b, mx):
            return self.shift, self.score

    def prof(v=0.0, n=40):
        return [v] * n

    # ① 首帧不触发；随后一直静止（且从没动过）也不触发 —— settle 要求「动过」
    e = Est()
    s = _AdaptiveSampler({"L": 10}, {"max_gap_s": 0}, estimate=e)
    fire, _, _ = s.observe(0.0, {"L": prof()})
    cases.append(("首帧不触发", not fire))
    for i in range(1, 6):
        fire, _, _ = s.observe(i * 0.1, {"L": prof()})
    cases.append(("一直没动过就不触发（不是每次静止都拍）", not fire))

    # ② 动一下后停稳 settle_frames 帧 ⇒ 触发一次 settle，之后继续静止不再触发
    e = Est(shift=10)
    s = _AdaptiveSampler({"L": 10}, {"settle_frames": 3, "max_gap_s": 0}, estimate=e)
    s.observe(0.0, {"L": prof()})
    fire, _, _ = s.observe(0.1, {"L": prof()})
    cases.append(("刚动、还没停稳 ⇒ 不触发", not fire))
    e.shift = 0
    got = None
    for i in range(3):
        fire, _, reasons = s.observe(0.2 + i * 0.1, {"L": prof()})
        if fire:
            got = (i, reasons["L"][0])
    cases.append(("停稳 3 帧后触发一次、类别 settle", got == (2, "settle")))
    fire, _, _ = s.observe(0.6, {"L": prof()})
    cases.append(("已拍过、继续静止不再触发", not fire))

    # ③ 位移安全网：持续 +4px/帧 不停 → 累计到 safety_rows(2)×pitch(10)=20px 才触发
    e = Est(shift=4)
    s = _AdaptiveSampler({"L": 10}, {"safety_rows": 2.0, "settle_frames": 99,
                                     "max_gap_s": 0}, estimate=e)
    s.observe(0.0, {"L": prof()})
    cats = []
    for i in range(1, 8):
        fire, _, reasons = s.observe(i * 0.1, {"L": prof()})
        if fire:
            cats.append(reasons["L"][0])
    cases.append(("持续滚动到安全网就拍、类别 move", cats == ["move"]))

    # ④ 入口触发（C）：没滚动（shift 0）但底部入口行变亮 ⇒ entry
    s = _AdaptiveSampler({"L": 10}, {"entry_thr": 5.0, "max_gap_s": 0}, estimate=Est())
    base = prof(100.0)
    s.observe(0.0, {"L": base})
    changed = base[:-12] + [130.0] * 12          # 底部约 1.2 行距变亮
    fire, _, reasons = s.observe(0.1, {"L": changed})
    cases.append(("没滚但入口底带变了 ⇒ entry", fire and reasons["L"][0] == "entry"))

    # ⑤ 超时兜底：一直不动，超过 max_gap_s 也拍一次
    s = _AdaptiveSampler({"L": 10}, {"max_gap_s": 1.0, "settle_frames": 99}, estimate=Est())
    s.observe(0.0, {"L": prof()})
    fire, _, _ = s.observe(0.5, {"L": prof()})
    cases.append(("没到 max_gap 不拍", not fire))
    fire, _, reasons = s.observe(1.5, {"L": prof()})
    cases.append(("到 max_gap 拍一次、类别 gap", fire and reasons["L"][0] == "gap"))

    # ⑥ 非平移变化（score 低）不算位移 ⇒ 不为动画触发
    s = _AdaptiveSampler({"L": 10}, {"max_gap_s": 0}, estimate=Est(shift=30, score=0.05))
    s.observe(0.0, {"L": prof()})
    fire, _, _ = s.observe(0.1, {"L": prof()})
    cases.append(("score 低（动画/噪声）当没动、不触发", not fire))

    # ⑦ 安全网按可见行数收紧：面板高 40px / 行距 10 = 4 行 ⇒ 阈值收到半屏 2 行 = 20px
    s = _AdaptiveSampler({"L": 10}, {"safety_rows": 3.0}, estimate=Est(), heights={"L": 40})
    cases.append(("安全网被「半屏」收紧（短面板不漏）", abs(s.safety_px["L"] - 20.0) < 1e-9))

    # ⑧ 真实位移估计（需要 numpy；没有就跳过这一项）
    try:
        import numpy as np
        rng = np.random.RandomState(0)
        base = rng.randn(200) * 10.0
        shifted = np.concatenate([base[7:], base[:7]])       # 上移 7px
        s_est, sc = _est_shift_px(base.tolist(), shifted.tolist(), 30)
        cases.append(("行投影互相关估出上移 7px 且 score 高", s_est == 7 and sc > 0.9))
        noisy = base.copy()
        noisy[90:130] += 200.0                                # 局部动画（非平移）
        _, sc2 = _est_shift_px(base.tolist(), noisy.tolist(), 30)
        cases.append(("局部动画的 score 明显低于真平移", sc2 < sc - 0.05))
    except Exception as exc:                                  # numpy 缺失只在自检里降级
        print(f"[selftest] 抽帧：位移估计项跳过（{exc}）")

    bad = [n for n, ok in cases if not ok]
    if bad:
        print(f"[selftest] 抽帧断言失败：{bad}", file=sys.stderr)
        return False
    print(f"[selftest] 自适应抽帧 {len(cases)} 项断言通过 ✔")
    return True
