"""``livepipe.tools.dataset.sources`` —— 扫 run 目录，把评论折成「复核单元」。

一个 **复核单元**（``Unit``）= 屏幕上看得见的一条评论：

* 帧 = ``round(t × fps)``（``t`` 是文件内绝对秒）；
* 同一个 ``(视频, 帧)`` 里 ROI IoU ≥ 0.6 的记录算同一条（同一批录屏被两个 run
  解析过，同一条评论会出两次，框差一两像素）；
* 同一个单元的所有记录共用一张复核图 —— **删一次就全没了**，不会「删了这张、
  另一张还在往数据集里灌」。这是人删图能真正生效的前提。

记录本身**不去重**：同一个单元里的多条记录各带各的框，落标签时再按类去重
（见 :func:`livepipe.tools.dataset.labels.frame_lines`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .common import _log, frame_index, iou, video_key


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #

@dataclass
class Side:
    """一段素材（一个 run 里的一个 seg）—— 一份 ``comments.jsonl`` 的出身。"""
    run: str
    seg: str
    path: Path
    video: str
    start: float
    mode: str
    fps: float
    wh: tuple[int, int]
    rows: list[dict] = field(default_factory=list)
    dropped: int = 0
    """过滤掉的行数（``username`` 或 ``colon_box`` 为空）。"""

    @property
    def key(self) -> str:
        return f"{self.run}/{self.seg}"


def labelable(row: dict) -> bool:
    """这一行能不能当标签源：昵称和冒号都必须有。

    ``username`` 为空 = 昵称被 OCR 丢了（徽章/昵称并进正文）；``colon_box`` 为空 =
    分隔冒号没找到。这两种行的 ``body_text`` 起点和 ``colon`` 标签都不可信，宁可不进
    数据集 —— 见 ``verify`` 的 ``LABEL_SOURCE`` 闸。
    """
    return bool(str(row.get("username") or "").strip()) and bool(row.get("colon_box"))


@dataclass
class Rec:
    """一条评论记录：哪一段、第几行、属于哪一帧。"""
    side: Side
    index: int          # 在 comments.jsonl 里的行号（0 起）—— reviews 里对账用
    row: dict
    t: float
    frame: int

    @property
    def roi(self):
        return self.row.get("roi")


@dataclass
class Unit:
    """复核单元 = 屏幕上那一条评论（可能由多条记录叠成）。"""
    video: str
    vk: str
    frame: int
    t: float
    i: int              # 同帧内从 1 起，按 (上沿, 左沿) 排
    recs: list[Rec]
    split: str

    @property
    def roi(self):
        for r in self.recs:
            if r.row.get("roi"):
                return r.row["roi"]
        return None

    @property
    def name(self) -> str:
        from .common import review_name
        return review_name(self.vk, self.frame, self.i)


# --------------------------------------------------------------------------- #
# 扫盘
# --------------------------------------------------------------------------- #

def _video_meta(video: str) -> tuple[float, tuple[int, int]]:
    """视频 → ``(fps, (宽, 高))``。帧号全靠这把尺子，取不到就停机。"""
    import cv2
    cap = cv2.VideoCapture(video)
    try:
        if not cap.isOpened():
            raise SystemExit(f"打不开视频：{video}")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        cap.release()
    if fps <= 0 or w <= 0 or h <= 0:
        raise SystemExit(f"视频读不出 fps/尺寸：{video}（fps={fps}, {w}×{h}）")
    return fps, (w, h)


def _excluded(side_key: str, path: Path, patterns) -> str | None:
    for pat in patterns or ():
        if pat in side_key or pat in str(path):
            return pat
    return None


def scan_runs(run_dirs, *, exclude=(), log=_log) -> list[Side]:
    """扫 ``<run>/seg*/`` → ``Side`` 列表（含 fps / 尺寸 / 全部行）。"""
    sides: list[Side] = []
    meta: dict[str, tuple[float, tuple[int, int]]] = {}
    for rd in run_dirs:
        run = Path(rd)
        if not run.is_dir():
            raise SystemExit(f"run 目录不存在：{run}")
        segs = sorted(p for p in run.iterdir()
                      if p.is_dir() and (p / "comments.jsonl").is_file())
        if not segs:
            log(f"[scan][warn] {run} 下没有 seg*/comments.jsonl —— 跳过")
            continue
        for sd in segs:
            import json
            summ = sd / "summary.json"
            if not summ.is_file():
                log(f"[scan][warn] 缺 {summ}（不知道 fps/视频）—— 跳过这一段")
                continue
            doc = json.loads(summ.read_text(encoding="utf-8"))
            video = doc.get("video")
            if not video:
                log(f"[scan][warn] {summ} 里没有 video —— 跳过这一段")
                continue
            side_key = f"{run.name}/{sd.name}"
            hit = _excluded(side_key, sd, exclude)
            if hit:
                log(f"[scan] 排除 {side_key}（--exclude {hit}）")
                continue
            if video not in meta:
                meta[video] = _video_meta(video)
            fps, wh = meta[video]
            raw_rows = [json.loads(l) for l in
                        (sd / "comments.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
            rows = [r for r in raw_rows if labelable(r)]
            dropped = len(raw_rows) - len(rows)
            if dropped:
                log(f"[scan] {side_key} 过滤 {dropped} 行（username/colon 为空）")
            sides.append(Side(run=run.name, seg=sd.name, path=sd, video=video,
                              start=float(doc.get("start") or 0.0),
                              mode=str(doc.get("sample_mode") or "adaptive"),
                              fps=fps, wh=wh, rows=rows, dropped=dropped))
    return sides


# --------------------------------------------------------------------------- #
# 记录 → 帧 → 复核单元
# --------------------------------------------------------------------------- #

def frame_groups(sides) -> dict[tuple[str, int], list[Rec]]:
    """按 ``(视频, 帧号)`` 归组。帧号一律 ``round(t × fps)`` 现算 ——
    ``rows.jsonl`` 里的 ``frame`` 是「本次抽样的第几张」，不是解码器帧号。"""
    groups: dict[tuple[str, int], list[Rec]] = {}
    for s in sides:
        for i, row in enumerate(s.rows):
            t = float(row.get("t") or 0.0)
            rec = Rec(side=s, index=i, row=row, t=t, frame=frame_index(t, s.fps))
            groups.setdefault((s.video, rec.frame), []).append(rec)
    return groups


def units_of(groups, rules, *, thr: float = 0.6, log=_log) -> list[Unit]:
    """``(视频, 帧)`` → 复核单元：同帧内 ROI IoU ≥ ``thr`` 的记录并成一条。"""
    from .common import split_of
    units: list[Unit] = []
    for (video, frame), recs in groups.items():
        reps: list[list[Rec]] = []
        for rec in recs:
            roi = rec.roi
            if not roi:
                continue
            for cluster in reps:
                if any(iou(roi, c.roi) >= thr for c in cluster if c.roi):
                    cluster.append(rec)
                    break
            else:
                reps.append([rec])
        reps.sort(key=lambda c: (c[0].roi[1], c[0].roi[0]))
        vk = video_key(video)
        split = split_of(video, rules)
        for i, cluster in enumerate(reps, 1):
            units.append(Unit(video=video, vk=vk, frame=frame, t=float(cluster[0].t),
                              i=i, recs=cluster, split=split))
    units.sort(key=lambda u: (u.vk, u.frame, u.i))
    return units


def frame_index_of(units) -> dict[tuple[str, int], list[Unit]]:
    """``(视频, 帧)`` → 这一帧上的全部单元（一张图配多行标签）。"""
    out: dict[tuple[str, int], list[Unit]] = {}
    for u in units:
        out.setdefault((u.video, u.frame), []).append(u)
    return out


def summarize(units) -> dict:
    """账本：记录数 / 单元数 / 帧数 / 各类框数（去重前）。"""
    from .common import CLASS_NAMES, boxes_of_row
    by_class = {c: 0 for c in CLASS_NAMES}
    recs = 0
    dup = 0
    for u in units:
        recs += len(u.recs)
        dup += len(u.recs) - 1
        for r in u.recs:
            for cls, _ in boxes_of_row(r.row):
                by_class[CLASS_NAMES[cls]] += 1
    return {"records": recs, "units": len(units), "merged": dup,
            "frames": len({(u.video, u.frame) for u in units}),
            "videos": len({u.video for u in units}), "boxes_raw": by_class}
