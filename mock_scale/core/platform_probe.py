#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台探针 —— 10 秒回答「这份语料能不能跑、该用哪套 profile」。

    python3 core/platform_probe.py <语料根> --limit 5
    python3 core/platform_probe.py <语料根> --json > probe.json

**只有一份（2026-10-27 合并后）**：它不属于某一条线，是**两条线共用的一把尺子** ——
它量的是**管线**（``batch_run.detect_box`` / ``inkmask`` / ``platforms``），
管线与它同居 `core/`，所以下面仍把 ``Path(__file__).parent`` 放进 ``sys.path``，
保证量到的就是本工程实际在跑的那一份管线。

## 为什么要有它

连续两批语料（视频号 17 张、微博 8 张）跑出**整批 0 定位**，而每次定位原因都要
人工裁图 + 逐段量色半小时。本脚本把那几轮的手工活固化成一条命令，**只读不写**：

* 框检出率 / 多框（走的是**生产同一条**检测路径 ``batch_run.detect_box``）；
* 框内**众数色占比** —— 低于 profile 的下限就说明「背景不是近纯色」，这语料不该硬跑；
* 名字段**彩度直方图** —— 一眼看出是抖音型（60~80）还是白/灰名字型（0~5）；
* **冒号候选命中率** —— 冒号锚点（跨平台判据）在这份语料上认不认得出分界；
* 纯英文昵称**跳过率** —— 规格相关的量（抖音 9.2%，微博 60%，差 7 倍）；
* **当前策略 vs 冒号锚点**的对照 —— 「现在能不能跑」和「换锚点后能不能跑」并排。

⚠️ 它**不改变管线**、不落任何图、不进任何统计。判据全部直接调 ``inkmask`` 的
现成函数，保证量出来的数与生产一致。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))   # 管线代码就在 core/（合并后只有这一份）

import batch_run  # noqa: E402
import config  # noqa: E402
import inkmask  # noqa: E402
import platforms as plat  # noqa: E402
import upstream_geom  # noqa: E402

LOGGER = logging.getLogger("platform_probe")


# --------------------------------------------------------------------------
# 单张
# --------------------------------------------------------------------------


@dataclass
class ImageProbe:
    """一张图的探针结果（全是量出来的数，没有判断）。"""

    user: str
    file: str
    path: str
    boxes: int = 0
    box: Optional[tuple[int, int, int, int]] = None
    detect_flags: list[str] = field(default_factory=list)

    bg_mode: Optional[tuple[int, int, int]] = None
    bg_share: float = 0.0
    n_segs: int = 0

    current_ok: bool = False
    """当前策略（``inkmask.find_erase_region``）在这张图上成功与否。"""
    current_error: str = ""

    colon_hits: int = 0
    colon: Optional[dict] = None
    name_span: Optional[tuple[int, int]] = None
    name_chroma: Optional[int] = None
    name_lum: Optional[float] = None
    body_lum: Optional[float] = None
    name_width: int = 0

    @property
    def anchor_ok(self) -> bool:
        """冒号锚点在这张图上给出完整结构（名字 + 冒号 + 正文）。"""
        return self.colon is not None and self.name_span is not None


def _bg_share(patch: np.ndarray, tol: int = 12, quantize: int = 0) -> tuple[float, tuple[int, int, int]]:
    """框内**背景占比** —— 与众数色相差 ≤ ``tol`` 的像素所占比例。

    ⚠️ **不能用「众数色的精确占比」**：视频压缩噪声会把同一块底色的像素打散到许多
    相邻色值上 —— 实测抖音那批的**精确**众数色只占 **1%~14%**（而它明明是近乎纯色的
    暗底、是能跑的语料），而视频号那 2 张真的「不是纯色底」的也只有 0.8~1.6%。
    两个数**混在一起，根本分不开**。改成「相差 ≤ tol 的占比」之后才有区分度。

    Args:
        patch: ``(H, W, 3)`` RGB。
        tol:   与众数色的最大通道差上限。

    Returns:
        ``(占比, 众数色)``。
    """
    mode = inkmask.estimate_background(patch, quantize=quantize)
    diff = np.abs(patch.astype(np.int32) - mode.astype(np.int32)).max(axis=2)
    return float((diff <= tol).mean()), tuple(int(v) for v in mode)


def probe_one(task: tuple, profile: plat.PlatformProfile) -> ImageProbe:
    """探一张图。**不抛异常** —— 任何一步失败都记进返回值。

    ``task`` = ``(图片路径, 昵称[, 上游geom])``。需要 geom 的平台（快手 App 的融合框）
    会把窗口收到 ``colon_box`` 那一行再量 —— 与生产路径（``inkmask.find_erase_region``）
    走同一套 ``_geom_clean`` / ``bg_quantize``，否则探针会把「能跑」误报成「先别跑」。
    """
    raw_path, nickname, geom = task if len(task) == 3 else (*task, None)
    path = Path(raw_path)
    rec = ImageProbe(user=path.parent.name, file=path.name, path=str(path.resolve()))

    box, n_boxes, flags = batch_run.detect_box(path)
    rec.boxes, rec.box, rec.detect_flags = n_boxes, box, list(flags)
    if box is None:
        return rec

    try:
        img = np.array(Image.open(path).convert("RGB"))
    except Exception as exc:  # noqa: BLE001
        rec.current_error = f"读取失败: {exc}"
        return rec

    # ---- 当前策略（生产路径；按这个平台的 profile + 上游 geom 走）----
    try:
        inkmask.find_erase_region(img, box, profile=profile, geom=geom)
        rec.current_ok = True
    except Exception as exc:  # noqa: BLE001 - 定位失败的原因很杂，原文照收
        rec.current_error = str(exc)

    # ---- 几何（与 inkmask 逐步对齐，保证量的是同一块地）----
    ix0, iy0, ix1, iy1 = inkmask.inset_box(box, config.BOX_INSET, img.shape[1], img.shape[0])
    ix0, iy0, ix1, iy1 = inkmask.shave_frame(img, (ix0, iy0, ix1, iy1))
    # 有 geom 的平台先把窗口收到冒号行（与 find_erase_region 同一条规则）
    g = inkmask._geom_clean(geom, (ix0, iy0, ix1, iy1),
                            row_from_colon=profile.geom_row_from_colon)
    if g is not None:
        ix0, iy0, ix1, iy1 = g["window"]
    patch = img[iy0:iy1, ix0:ix1]
    if patch.size == 0:
        return rec
    rec.bg_share, rec.bg_mode = _bg_share(patch, quantize=profile.bg_quantize)

    bg = inkmask.estimate_background(
        inkmask._geom_bg_sample(patch, g, ix0),
        quantize=profile.bg_quantize,
    )
    ink = inkmask.compute_ink_mask(patch, bg, config.BG_DIST_THRESH)
    segs = inkmask.column_segments(ink.any(axis=0))
    rec.n_segs = len(segs)
    if not segs:
        return rec

    stats = [inkmask._segment_stats(ink, patch, s) for s in segs]
    fills = [f for f, _ in stats]
    lums = [b for _, b in stats]
    cores = [inkmask._core_color(patch, ink, s) for s in segs]
    rows: list[tuple[int, int]] = []
    for s, e in segs:
        col = ink[:, s : e + 1].any(axis=1)
        idx = np.nonzero(col)[0]
        rows.append((int(idx.min()), int(idx.max())) if len(idx) else (0, 0))

    bands = inkmask.row_bands(ink.sum(axis=1).astype(int), config.GLYPH_ROW_GAP_PX)
    band = tuple(max(bands, key=lambda b: b[2])[:2]) if bands else (0, patch.shape[0] - 1)

    # ---- 冒号锚点（跨平台判据）----
    cands = plat.find_colon_candidates(segs, fills, rows, band, profile)
    rec.colon_hits = len(cands)
    if not cands:
        return rec
    # ⚠️ 这里必须用 `pick_colon`（与管线同一条挑法），否则探针报的「能不能跑」
    #    和管线实际挑的那个冒号会是两个候选（App 档就靠「前面的缝」换人，bug-358）。
    c = plat.pick_colon(cands, segs, profile)
    assert c is not None
    rec.colon = {
        "index": c.index,
        "span": list(c.span),
        "width": c.width,
        "height": c.height,
        "fill": round(c.fill, 3),
        "body_width": c.body_width,
        "score": round(c.score, 1),
        # 前面那道缝（px）—— App 档挑人的唯一依据，也是「名字是否被切短」的判据
        "gap_before": plat.preceding_gap(segs, c),
    }
    try:
        first, last = plat.name_run_left_of(segs, fills, c.index, profile)
    except ValueError:
        return rec
    rec.name_span = (segs[first][0], segs[last][1])
    rec.name_width = segs[last][1] - segs[first][0] + 1
    chromas = [max(cores[i]) - min(cores[i]) for i in range(first, last + 1)]
    rec.name_chroma = int(round(float(np.median(chromas))))
    rec.name_lum = float(np.mean([lums[i] for i in range(first, last + 1)]))
    if c.index + 1 < len(segs):
        rec.body_lum = float(np.mean(lums[c.index + 1 :]))
    return rec


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------


def _hist(values: Sequence[int]) -> dict[str, int]:
    buckets = {"0~5": 0, "6~59": 0, "60~100": 0, ">100": 0}
    for v in values:
        if v <= 5:
            buckets["0~5"] += 1
        elif v < 60:
            buckets["6~59"] += 1
        elif v <= 100:
            buckets["60~100"] += 1
        else:
            buckets[">100"] += 1
    return buckets


def summarize(records: Sequence[ImageProbe], n_scanned: int, n_skipped: int) -> dict:
    """把逐图结果汇总成一份「能不能跑 + 该用哪套 profile」的判定。"""
    with_box = [r for r in records if r.box is not None]
    anchors = [r for r in with_box if r.anchor_ok]
    chroma = [r.name_chroma for r in anchors if r.name_chroma is not None]
    name_lum = [r.name_lum for r in anchors if r.name_lum is not None]
    body_lum = [r.body_lum for r in anchors if r.body_lum is not None]
    bg = sorted(r.bg_share for r in with_box)

    strategy, suggested, reason = plat.classify_strategy(chroma, name_lum, body_lum)
    return {
        "images_scanned": n_scanned,
        "images_skipped_pure_en": n_skipped,
        "images_probed": len(records),
        "box_found": len(with_box),
        "box_multi": sum(1 for r in records if r.boxes > 1),
        "box_missing": sum(1 for r in records if r.box is None and not r.detect_flags or
                           ("NO_BOX" in r.detect_flags)),
        "bg_share_median": round(bg[len(bg) // 2], 3) if bg else 0.0,
        "bg_share_min": round(bg[0], 3) if bg else 0.0,
        "current_ok": sum(1 for r in records if r.current_ok),
        "anchor_ok": len(anchors),
        "anchor_rate": round(len(anchors) / len(with_box), 3) if with_box else 0.0,
        "name_chroma_hist": _hist(chroma),
        "colon_hits_total": sum(r.colon_hits for r in records),
        "strategy": strategy,
        "suggested_profile": suggested,
        "reason": reason,
    }


def render(report: dict, records: Sequence[ImageProbe], root: Path,
           path_key: Optional[str], profile: plat.PlatformProfile) -> str:
    """把汇总渲染成人看的一段话。"""
    L: list[str] = []
    n_box = report["box_found"]
    L.append(f"语料 {root}")
    L.append(f"  扫到 {report['images_scanned']} 张 · 纯英文跳过 {report['images_skipped_pure_en']} 张"
             f" · 探了 {report['images_probed']} 张")
    if report["images_scanned"] and report["images_skipped_pure_en"]:
        rate = report["images_skipped_pure_en"] / report["images_scanned"]
        flag = "   ⚠️ 这条规格在本平台会吃掉大比例" if rate >= 0.3 else ""
        L.append(f"  纯英文跳过率 {rate:.0%}{flag}")
    L.append("")
    L.append(f"检测   {n_box}/{report['images_probed']} 检出 · 多框 {report['box_multi']} · "
             f"没框 {report['images_probed'] - n_box}")
    if report["bg_share_median"]:
        L.append(f"背景   框内众数色占比：中位 {report['bg_share_median']:.0%} "
                 f"· 最低 {report['bg_share_min']:.0%}（下限 {profile.bg_mode_share_min:.0%}）")
    L.append(f"列段   每图段数中位 —— 见 --json（段太少 ⇒ 框里切不出字）")
    L.append("")
    L.append(f"当前策略  成功 {report['current_ok']}/{report['images_probed']}"
             f"（{profile.name_strategy}；需要 geom 的平台已按冒号行窗口量）")
    L.append(f"冒号锚点  结构完整 {report['anchor_ok']}/{n_box}"
             f"（命中率 {report['anchor_rate']:.0%}）· 候选段合计 {report['colon_hits_total']}")
    L.append(f"名字段彩度 {report['name_chroma_hist']}")
    L.append("")
    L.append(f"⇒ 建议策略：{report['strategy']}（{report['reason']}）")
    if path_key:
        L.append(f"⇒ 路径判定的平台：{path_key}（{plat.PROFILES[path_key].label}）"
                 f"· skip_pure_en={plat.PROFILES[path_key].skip_pure_en}")
    else:
        L.append(f"⇒ 路径里没认出平台，建议 profile：{report['suggested_profile']}"
                 f"（skip_pure_en={plat.PROFILES[report['suggested_profile']].skip_pure_en}）")
    if n_box and report["anchor_rate"] >= 0.6 and report["bg_share_median"] >= profile.bg_mode_share_min:
        L.append("⇒ 判定：**可以跑**（背景达标 + 冒号锚点认得出结构）")
    else:
        why = []
        if n_box and report["anchor_rate"] < 0.6:
            why.append(f"冒号锚点只认得 {report['anchor_rate']:.0%}")
        if report["bg_share_median"] and report["bg_share_median"] < profile.bg_mode_share_min:
            why.append("背景众数色占比过低（不是近纯色底）")
        if not n_box:
            why.append("框都检不出来")
        L.append(f"⇒ 判定：**先别跑**（{'；'.join(why) or '样本不足'}）")
    return "\n".join(L)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="platform_probe",
        description="探一份语料的平台特征：能不能跑、该用哪套 profile（只读，不动管线）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例：\n"
            "  python platform_probe.py <语料根> --limit 5\n"
            "  python platform_probe.py <语料根> --users '爱国者-*' --json\n"
        ),
    )
    p.add_argument("root", type=Path, help="语料根目录（里面是「昵称-张数」的子目录）")
    p.add_argument("--limit", type=int, default=5, help="每份语料探前 N 张（默认 5，0 = 全探）")
    p.add_argument("--users", default=None, help="只探名字匹配这个 glob 的用户目录")
    p.add_argument("--platform", default="auto", help="平台名或 auto（默认 auto：从路径判定）")
    p.add_argument("--json", action="store_true", help="输出 JSON（含逐图明细）")
    p.add_argument("--verbose", action="store_true", help="打 DEBUG 日志")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.ERROR,
        format=config.LOG_FORMAT, datefmt=config.LOG_DATEFMT,
    )

    try:
        images = batch_run.collect_images(args.root, args.users)
    except NotADirectoryError as exc:
        LOGGER.error("%s", exc)
        return 2
    if not images:
        LOGGER.error("没扫到任何图片，检查路径与 --users")
        return 2

    path_key = None if args.platform == "auto" else plat.resolve_key(args.platform)
    if args.platform == "auto":
        path_key = plat.detect_platform_from_path(args.root)
    profile = plat.profile_for(path_key or args.platform)

    # 需要上游几何的平台（快手 App 的融合框）：从交付树重建，与两条生产线的口径一致
    geom_map: dict[str, dict] = {}
    if profile.needs_geom:
        try:
            gs = upstream_geom.load(args.root)
        except upstream_geom.GeomError as exc:
            LOGGER.warning("平台 %s 需要上游几何，但自动重建失败：%s —— 按像素启发式量",
                           profile.key, exc)
        else:
            root = Path(args.root).resolve()
            for p, _nick in images:
                try:
                    rel = Path(p).resolve().relative_to(root).as_posix()
                except ValueError:
                    continue
                if gs.items.get(rel):
                    geom_map[str(Path(p).resolve())] = gs.items[rel]
            LOGGER.warning("上游几何（自动重建）：%s", gs.describe())

    # 跳过判定要走生产同一把尺子（判自目录名，不读图）
    skipped = [t for t in images if batch_run.nickname_is_ascii(t[1])]
    todo = [t for t in images if not batch_run.nickname_is_ascii(t[1])]
    if args.limit > 0:
        todo = todo[: args.limit]

    records = [probe_one((*t, geom_map.get(str(Path(t[0]).resolve()))), profile)
               for t in todo]
    report = summarize(records, len(images), len(skipped))

    if args.json:
        print(json.dumps(
            {"report": report, "profile": asdict(profile),
             "records": [asdict(r) for r in records]},
            ensure_ascii=False, indent=2,
        ))
        return 0

    print(render(report, records, args.root, path_key, profile))
    return 0


if __name__ == "__main__":
    sys.exit(main())
