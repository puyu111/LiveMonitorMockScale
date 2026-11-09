#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 ``bodies.jsonl`` 里的句子按像素宽贴上语料，逐张过闸，写一份报告。

    python3 core/batch_body.py --source /tmp/review_corpus --out out/body_run
    python3 core/batch_body.py --dry-run            # 只挑句子、不过图、不落盘

几何来源：``--geom`` > ``<source>/geom.json`` > 交付树 manifest 自动重建
（:mod:`upstream_geom`）。

## 一条正文的一生（本文件就是这五步的调度）

    量    ``body_text.measure_body``        每行左沿/右缘/em/颜色/擦除矩形
    挑    ``replace_body.plan_body``        按**像素宽**从句子库里挑，同一句最多用 3 次
    擦    ``replace_body.erase_body``       逐行铺平；残余必须在**写字之前**量
    写    ``replace_body.render_body``      按墨迹包围盒对齐
    验    :func:`verify`                    拿**同一把尺子**再量一遍成品图

第五步是这条线唯一的验收口径：不靠「看着像」，靠 ``measure_body`` 在成品上重测。
它同时是**闸的复用** —— 量测阶段用来判「这行能不能换」的规则，成品上原样再用一遍。

## 八道闸（任一不过 = ``fail``，图不落盘）

====================  ==================================================
``REMEASURE[理由]``   成品图**重测不成立**（NO_INK / EMOJI_ONLY / …）—— 写完量不回来
``LINE_COUNT``        成品还是原来那么多行（单行不许变两行）
``RIGHT_EDGE``        每行右缘 |Δ| ≤ max(3px, 0.5em)（与方案的判据同值）
``BOX_MARGIN``        新右缘 ≤ 上游框右沿 − 2px（不顶到绿标注线）
``FIRST_LINE_FILL``   第一行宽 ≥ 可用宽 − 0.6em（不把一行写成半行）
``COLON``             冒号框里**一个像素**都没变
``OUTSIDE``           擦除矩形之外**一个像素**都没变
``RESIDUAL``          擦干净的每一行，残余墨迹 = 0
====================  ==================================================

``OUTSIDE`` 是构造保证（只写矩形内），出现即 bug；``RESIDUAL`` 是「擦干净了没」的
硬证据，量在**擦完、写字前**（量在成品上会把新字算成残余，见
``inpaint_local.region_ink`` 的说明）。

## 三种不是失败的结果

    skip        量测阶段就跳过（几何不全 / 纯表情 / 框跨下一条评论），见 ``body_text.REASON_TEXT``
    blacklist   在 ``source_blacklist.json`` 里（官方横幅行，bug-371）
    no_candidate 句子库里没有一条能落在这个宽度上 —— **不是错**，是库还不够宽

## 只读语料

预演树默认写 **PNG**（``--format png``）：JPEG 重编码会把边缘动 1px，报告里的数就
没法从盘上复核了。

``--out`` 是**另一棵树**，源图只读不写。``--out`` 指到语料里面会被拒（除非 ``--yes``），
与扩展线「预演树」的口径一致：验证永远不就地写回。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import batch_produce  # noqa: E402
import body_text  # noqa: E402
import config  # noqa: E402
import platforms  # noqa: E402
import replace_body  # noqa: E402
import runconfig  # noqa: E402
import upstream_geom  # noqa: E402

LOGGER = logging.getLogger("batch_body")

REPORT_NAME = "report.json"
GATE_RIGHT_EDGE_RATIO: float = 0.5
"""成品每行右缘与愿行右缘的允许差（**em 的倍数**）—— 与 ``replace_body.RIGHT_DELTA_RATIO``
同值，闸与方案的判据必须一致：方案收下的句子，闸不能再拿更严的尺子否掉它。

0.5em 不是宽松，是**字宽量化**的下限：墨迹宽按字形走，一个字 20~27px，同一个字数里
不同句子的宽度本来就能差好几分之一字。要 3px 以内，只能靠标点变体和窄字碰运气 ——
那是在给「碰巧对齐」发奖，不是把正文换对。实测平均 2.4px、最大 3px（见报告）。
"""

GATE_RIGHT_EDGE_PX: int = 3
"""右缘差的**绝对下限**（px）：em 很小时 0.5em 只有 10px，3px 是给抗锯齿边缘留的噪声。"""

GATE_NOISE_PX: int = 2
"""闸的噪声余量（px）。

方案里的宽度是**字体表**算出来的（``draw_text.measure_ink`` 在临时画布上量墨迹），
闸里的宽度是**成品图**上重新分的段 —— 两者的抗锯齿边缘判在哪一列会差 1~2px
（实测 3 例：方案 12px、成品 13px）。把这点口径差算进闸，否则每张都要
「差 1px 失败」一次，等于把量具的刻度当成业务判据。
"""

GATE_FILL_SLACK_RATIO: float = 0.6
"""``FIRST_LINE_FILL`` 的容差（em 的倍数）—— 与 ``replace_body.WRAP_SLACK_RATIO`` 同值。

plan 阶段已经按这条筛过，这里在成品上**再量一遍**：plan 是「按字体表算的」，
成品是「按像素量的」，两者对不上时以像素为准。
"""

DEFAULT_MAX_REUSE: int = 3
"""同一句在整批里最多用几次。

为什么有上限：语料里 5~7 字占 50 行（``bodies_need.json``），池子里每个字数只有
十来条；不限次会看到「隔音超出预期」刷满一屏 —— 那不是 mock，那是穿帮。
"""


# --------------------------------------------------------------------------
# 句子库
# --------------------------------------------------------------------------


def load_bodies(path: Path) -> list[str]:
    """读 ``bodies.jsonl`` 的句子（去重、保持文件顺序 —— 挑句子靠宽度不靠顺序）。"""
    if not path.is_file():
        raise FileNotFoundError(f"句子库不在：{path}（先跑 mock/gen_bodies.py）")
    texts: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            LOGGER.warning("bodies.jsonl 里有一行不是 JSON，跳过：%.40s", line)
            continue
        text = str(obj.get("text") or "").strip()
        if text and text not in seen:
            seen.add(text)
            texts.append(text)
    if not texts:
        raise ValueError(f"{path} 里没有一条能用的句子")
    return texts


def choose_plan(
    texts: Sequence[str], region: body_text.BodyRegion, used: dict[str, int], max_reuse: int,
) -> tuple[Optional[replace_body.BodyPlan], int]:
    """挑最贴的一条：**第一行宽度差最小**的胜出。

    Returns:
        ``(方案, 候选条数)``。候选条数 = 过得了 ``plan_body`` 的句子数，用来区分
        「库不够宽」和「库压根没覆盖这个宽度」。
    """
    best: Optional[replace_body.BodyPlan] = None
    best_key: Optional[tuple[int, int]] = None
    feasible = 0
    for text in texts:
        if used.get(text, 0) >= max_reuse:
            continue
        plan = replace_body.plan_body(text, region)
        if plan is None:
            continue
        feasible += 1
        key = (abs(plan.widths[0] - region.lines[0].width), -plan.widths[0])
        if best_key is None or key < best_key:
            best, best_key = plan, key
    return best, feasible


# --------------------------------------------------------------------------
# 验收
# --------------------------------------------------------------------------


def _rects(region: body_text.BodyRegion, *, width: int, height: int) -> list[tuple[int, int, int, int]]:
    """擦除矩形（半开区间）—— **与 :func:`replace_body.erase_body` 同一份**。

    闸和擦除必须用同一组矩形：闸比擦得宽 = 白放过漏出来的像素；闸比擦得窄 =
    把「本来就要擦的地方」判成越界。所以这里只是转调，不自己算。
    """
    return replace_body.erase_rects(region, width=width, height=height)


def _outside_changed(
    before, after, rects: list[tuple[int, int, int, int]],
) -> tuple[int, list[int], list[int]]:
    """数「擦除矩形之外」被改动的像素。

    Returns:
        ``(改动像素数, 左上角坐标, 矩形外第一个改动点)`` —— 后两个只用于日志，
        直接告诉人去哪儿看图。
    """
    import numpy as np

    changed = np.any(before != after, axis=2)
    mask = np.zeros(changed.shape, dtype=bool)
    h, w = changed.shape
    for x0, y0, x1, y1 in rects:
        mask[max(0, y0):min(h, y1), max(0, x0):min(w, x1)] = True
    leak = changed & ~mask
    total = int(leak.sum())
    if not total:
        return 0, [], []
    ys, xs = np.nonzero(leak)
    return total, [int(xs.min()), int(ys.min())], [int(xs.max()), int(ys.max())]


def _colon_probe(region: body_text.BodyRegion) -> tuple[Optional[tuple[int, int, int, int]], str]:
    """冒号闸的探针矩形（**半开**区间）与来源。

    优先用墨迹认出来的 ``colon_x``（``colon_source == "ink"``），退路才是上游 ``colon_box``。
    两条理由，都是实测：

    * 590 宽那批的 ``colon_box`` 整体右偏 17px，拿它当探针会把正文前两个字圈进去；
    * 探针的上下沿取**第一行行带**（±2px），因为冒号就是这一行里的一个字符。
    """
    if not region.lines:
        return None, ""
    line = region.lines[0]
    if region.colon_source == "ink" and region.colon_x:
        x0, x1 = region.colon_x
        return (x0 - 1, line.top - 2, x1 + 2, line.bottom + 3), "ink"
    if region.colon_box:
        x0, y0, x1, y1 = region.colon_box
        return (x0, y0, x1 + 1, y1 + 1), "box"
    return None, ""


def verify(
    before, after, region: body_text.BodyRegion, residual: list[int], geom: dict,
) -> tuple[list[str], dict]:
    """在**成品图**上重测一遍，跑八道闸。

    Returns:
        ``(没过闸的名字列表, 度量字典)``。名字空 = 全过。
    """
    import numpy as np

    failed: list[str] = []
    metrics: dict = {"residual": residual}

    # ---- 量：同一把尺子 ----
    again = body_text.measure_body(after, geom, em=region.em)
    metrics["measured_ok"] = again.ok
    metrics["measured_reason"] = again.reason
    metrics["line_count_before"] = region.line_count
    metrics["line_count_after"] = again.line_count
    metrics["widths_before"] = [line.width for line in region.lines]
    metrics["widths_after"] = [line.width for line in again.lines]
    if again.ok:
        metrics["rights_before"] = [line.right for line in region.lines]
        metrics["rights_after"] = [line.right for line in again.lines]
        metrics["deltas"] = [
            b.right - a.right for b, a in zip(region.lines, again.lines)
        ]

    # ---- 1 重测 ----
    # 重测失败**不等于**行数变了：可能整行没量到墨（NO_INK）、量到表情、量到别的行带。
    # 分开记：``REMEASURE[理由]`` 才告诉人去查哪一步；``LINE_COUNT`` 只留给真变了行数。
    if not again.ok:
        failed.append(f"REMEASURE[{again.reason}]")
        return failed, metrics  # 重测都不成立，后面的比较没有意义
    if again.line_count != region.line_count:
        failed.append("LINE_COUNT")
        return failed, metrics

    em = region.em
    box_right = region.text_box[2] if region.text_box else None

    # ---- 2/3/4 每行的右缘、框余量、顶满 ----
    right_edge_max = max(GATE_RIGHT_EDGE_PX, int(round(GATE_RIGHT_EDGE_RATIO * em))) + GATE_NOISE_PX
    metrics["right_edge_max_px"] = right_edge_max
    for i, (line0, line1) in enumerate(zip(region.lines, again.lines)):
        if abs(line1.right - line0.right) > right_edge_max:
            failed.append(f"RIGHT_EDGE[{i}]")
        if box_right is not None and line1.right > box_right - replace_body.BOX_MARGIN_PX:
            failed.append(f"BOX_MARGIN[{i}]")
        if i == 0:
            avail = line0.width
            if line1.width < avail - int(round(GATE_FILL_SLACK_RATIO * em)):
                failed.append("FIRST_LINE_FILL")

    # ---- 5 冒号一个像素都不许动 ----
    # ⚠️ 探针打在**墨迹冒号**（``colon_x``）上，不是上游 ``colon_box``：
    # 590 宽那批截图的 ``colon_box`` 整体**右偏 17px**（实测墨迹 135–138、框 152–170），
    # 拿框当探针会把正文的前两个字一起圈进去 —— 正文本来就该变，于是这条闸永远不过。
    probe, probe_src = _colon_probe(region)
    metrics["colon_probe"] = list(probe) if probe else None
    metrics["colon_probe_source"] = probe_src
    if probe:
        x0, y0, x1, y1 = probe
        h, w = before.shape[:2]
        patch_before = before[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]
        patch_after = after[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]
        if patch_before.shape != patch_after.shape or not np.array_equal(patch_before, patch_after):
            failed.append("COLON")

    # ---- 6 矩形外一个像素都不许动 ----
    leak, leak_min, leak_max = _outside_changed(
        before, after, _rects(region, width=before.shape[1], height=before.shape[0]))
    metrics["outside_changed"] = leak
    if leak:
        metrics["outside_bbox"] = [leak_min, leak_max]
        failed.append("OUTSIDE")

    # ---- 7 擦干净了没（写字之前量的）----
    if any(value > 0 for value in residual):
        failed.append("RESIDUAL")

    return failed, metrics


# --------------------------------------------------------------------------
# 一张图
# --------------------------------------------------------------------------


def process_one(
    frame, rel: str, geom: dict, texts: Sequence[str], used: dict[str, int],
    max_reuse: int,
) -> dict:
    """处理一张图：量 → 挑 → 擦 → 写 → 验。返回报告里的那一条（可能带 ``image``）。"""
    record: dict = {"rel": rel, "status": "ok", "reason": "", "text": "", "gates": [], "metrics": {}}

    region = body_text.measure_body(frame, geom)
    record["metrics"]["body"] = {
        "line_count": region.line_count,
        "widths": [line.width for line in region.lines],
        "chars": region.chars,
        "em": region.em,
        "colon_source": region.colon_source,
    }
    if not region.ok:
        record["status"] = "skip"
        record["reason"] = region.reason
        return record

    plan, feasible = choose_plan(texts, region, used, max_reuse)
    record["metrics"]["feasible"] = feasible
    if plan is None:
        record["status"] = "no_candidate"
        record["reason"] = "没有句子能落在这一行的宽度上"
        return record

    new_frame, plan, residual = replace_body.replace_body(frame, region, plan.text)
    if new_frame is None:  # pragma: no cover - plan 已过，replace 里同一判据不会再拒
        record["status"] = "no_candidate"
        record["reason"] = "plan 过了但替换拒绝（判据不一致，属 bug）"
        return record

    record["text"] = plan.text
    record["lines"] = list(plan.lines)
    record["metrics"]["plan_widths"] = list(plan.widths)
    failed, metrics = verify(frame, new_frame, region, residual, geom)
    record["metrics"].update(metrics)
    record["gates"] = failed
    if failed:
        record["status"] = "fail"
        record["reason"] = ",".join(failed)
        return record

    record["image"] = new_frame
    return record


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="batch_body",
        description="把 bodies.jsonl 的句子按像素宽贴到语料上，过八道闸，写预演树 + 报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例：\n"
            "  python3 core/batch_body.py --source <语料根> --out out/body_run\n"
            "  python3 core/batch_body.py --dry-run --verbose\n"
        ),
    )
    runconfig.add_argument(p)
    p.add_argument("--source", type=Path, default=None, help="语料根目录（必填）")
    p.add_argument(
        "--geom", type=Path, default=None,
        help="上游几何 JSON（不给就依次试 <source>/geom.json、交付树 manifest 自动重建）",
    )
    p.add_argument("--bodies", type=Path, default=None, help="句子库（默认 mock/data/bodies.jsonl）")
    p.add_argument("--out", type=Path, default=None, help="预演树写到哪（必填；不能是语料目录）")
    p.add_argument("--platform", default=None, help="平台键（决定黑名单怎么生效，默认 generic）")
    p.add_argument("--limit", type=int, default=None, help="只跑前 N 张（0 = 全量）")
    p.add_argument("--max-reuse", type=int, default=None, help="同一句最多用几次（默认 3）")
    p.add_argument(
        "--blacklist", action=argparse.BooleanOptionalAction, default=None,
        help="套用 source_blacklist.json（默认开）",
    )
    p.add_argument(
        "--format", default=None, choices=["png", "source"],
        help="预演树写什么格式：png（默认，无损、可复核）/ source（跟源文件后缀，JPEG 会重编码）",
    )
    p.add_argument("--dry-run", action="store_true", default=None, help="只挑句子、不写图")
    p.add_argument("--yes", action="store_true", default=None, help="允许 --out 指进语料目录（危险）")
    p.add_argument("--quiet", action="store_true", default=None, help="少打日志")
    p.add_argument("--verbose", action="store_true", default=None, help="每张打一行")
    return p


DEFAULTS: dict = {
    "source": None,
    "geom": None,
    "bodies": None,
    "out": None,
    "platform": "generic",
    "limit": 0,
    "max_reuse": DEFAULT_MAX_REUSE,
    "blacklist": True,
    "format": "png",
    "dry_run": False,
    "yes": False,
    "quiet": False,
    "verbose": False,
}
"""命令行与 JSON 都没给时用的值（**同时是运行配置的合法键清单与类型表**）。"""


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def _summary(records: list[dict]) -> dict:
    """报告里的汇总块（不含昵称，可单独贴给人看）。"""
    status: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for record in records:
        status[record["status"]] = status.get(record["status"], 0) + 1
        if record["reason"] and record["status"] != "ok":
            reasons[record["reason"]] = reasons.get(record["reason"], 0) + 1
    ok = [record for record in records if record["status"] == "ok"]
    deltas = [abs(d) for record in ok for d in record["metrics"].get("deltas", [])]
    return {
        "images": len(records),
        "status": status,
        "reasons": reasons,
        "replaced": len(ok),
        "gates_passed": len(ok),
        "right_edge_px": {
            "max": max(deltas) if deltas else 0,
            "mean": round(sum(deltas) / len(deltas), 2) if deltas else 0.0,
            "samples": len(deltas),
        },
        "residual_total": sum(
            int(v) for record in ok for v in record["metrics"].get("residual", [])
        ),
        "outside_changed_total": sum(
            int(record["metrics"].get("outside_changed") or 0) for record in records
        ),
        "texts_used": len({record["text"] for record in ok}),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from_cli, from_json, cfg_path = runconfig.load(args, DEFAULTS, script_file=__file__)
    except runconfig.ConfigError as exc:
        print(f"运行配置有问题：{exc}", file=sys.stderr)
        return 2
    args.source = runconfig.path(args.source)
    args.geom = runconfig.path(args.geom)
    args.bodies = runconfig.path(args.bodies)
    args.out = runconfig.path(args.out)

    level = logging.DEBUG if args.verbose else (logging.WARNING if args.quiet else logging.INFO)
    logging.basicConfig(level=level, format=config.LOG_FORMAT, datefmt=config.LOG_DATEFMT)
    if not args.verbose:
        quiet = logging.ERROR if args.quiet else logging.WARNING
        for name in ("inkmask", "PIL"):
            logging.getLogger(name).setLevel(quiet)
    LOGGER.info(runconfig.describe(cfg_path, from_cli, from_json))

    if args.source is None or not args.source.is_dir():
        LOGGER.error("语料根不对：%s（--source 指一个目录）", args.source)
        return 2
    try:
        geom_src = upstream_geom.load(args.source, args.geom)
    except upstream_geom.GeomError as exc:
        LOGGER.error("%s", exc)
        return 2
    items_in = geom_src.items
    bodies_path = args.bodies or (config.PROJECT_ROOT / "mock" / "data" / "bodies.jsonl")
    try:
        texts = load_bodies(bodies_path)
    except (OSError, ValueError) as exc:
        LOGGER.error("%s", exc)
        return 2
    if args.out is None:
        LOGGER.error("没给 --out：预演树必须写在语料之外（验证不就地写回）")
        return 2
    src = args.source.resolve()
    out_dir = args.out.resolve()
    if out_dir == src or src in out_dir.parents:
        if not args.yes:
            LOGGER.error("--out 在语料目录里面：%s —— 会覆盖语料。真要就地写回请加 --yes", out_dir)
            return 2
        LOGGER.warning("--out 在语料目录里面，且给了 --yes：**就地写回**")

    if args.platform not in platforms.PROFILES:
        LOGGER.error("不认识的平台 %r（认得的：%s）", args.platform, sorted(platforms.PROFILES))
        return 2
    blacklist: dict[str, str] = {}
    if args.blacklist:
        try:
            blacklist = batch_produce.load_blacklist(args.platform)
        except (FileNotFoundError, ValueError) as exc:
            LOGGER.error("黑名单读不了：%s", exc)
            return 2

    import numpy as np
    from PIL import Image

    rels = sorted(rel for rel in items_in if (src / rel).is_file())
    if args.limit > 0:
        rels = rels[: args.limit]
    LOGGER.info("几何 %s", geom_src.describe())
    LOGGER.info(
        "跑 %d 张 · 句子库 %d 条（同句上限 %d 次）· 输出 %s",
        len(rels), len(texts), args.max_reuse, out_dir,
    )

    t0 = time.perf_counter()
    used: dict[str, int] = {}
    records: list[dict] = []
    for i, rel in enumerate(rels, 1):
        frame = np.array(Image.open(src / rel).convert("RGB"))
        blocked = blacklist.get(rel)
        if blocked:
            records.append({
                "rel": rel, "status": "blacklist", "reason": blocked,
                "text": "", "gates": [], "metrics": {},
            })
            continue
        record = process_one(frame, rel, items_in[rel], texts, used, args.max_reuse)
        if record["status"] == "ok":
            used[record["text"]] = used.get(record["text"], 0) + 1
            if not args.dry_run:
                # ⚠️ 一律写 **PNG**（这里显式给 ``format``），不跟着源文件后缀走。
                # 按后缀存 JPEG 会把边缘重编码一遍：实测同一张 ``嗯***`` 在内存里
                # 右缘 146px、存成 JPEG 再量 145px —— 那样「报告里的数」和「从盘上
                # 复核出来的数」对不上，验收就没法复核了。预演树是给人看的，无损。
                target = out_dir / (rel if args.format == "source" else str(Path(rel).with_suffix(".png")))
                target.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(record.pop("image")).save(target, format="PNG")
            else:
                record.pop("image", None)
        else:
            record.pop("image", None)
        records.append(record)
        if args.verbose:
            LOGGER.debug(
                "[%d/%d] %s → %s%s %s", i, len(rels), rel, record["status"],
                f"（{record['reason']}）" if record["reason"] else "",
                record["text"],
            )

    elapsed = time.perf_counter() - t0
    summary = _summary(records)
    LOGGER.info(
        "跑完 %d 张（%.1fs）：替换成功 %d · 没句子 %d · 量测跳过 %d · 黑名单 %d · 过闸失败 %d",
        summary["images"], elapsed, summary["replaced"],
        summary["status"].get("no_candidate", 0),
        summary["status"].get("skip", 0),
        summary["status"].get("blacklist", 0),
        summary["status"].get("fail", 0),
    )
    if summary["reasons"]:
        LOGGER.info("  原因：%s", " · ".join(f"{k} {v}" for k, v in summary["reasons"].items()))
    LOGGER.info(
        "  右缘 |Δ|：最大 %dpx · 平均 %.2fpx（%d 行）· 残余墨迹 %d · 矩形外改动 %d",
        summary["right_edge_px"]["max"], summary["right_edge_px"]["mean"],
        summary["right_edge_px"]["samples"], summary["residual_total"],
        summary["outside_changed_total"],
    )

    if args.dry_run:
        LOGGER.info("--dry-run：不写文件")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "kind": "body_run",
        "schema_version": 1,
        "_说明": (
            "正文替换的预演报告。⚠️ rel 是语料目录名（= 用户昵称）：只在本机流转，"
            "不进交付包、不进 git。status: ok / no_candidate / skip / blacklist / fail。"
        ),
        "generator": {"script": "core/batch_body.py", "module": "core/replace_body.py"},
        "source": str(src),
        "geom": str(geom_src.path) if geom_src.path else "",
        "geom_source": geom_src.origin,
        "geom_stats": {
            "images": geom_src.images,
            "matched": geom_src.matched,
            "missing": geom_src.missing,
            "ambiguous": geom_src.ambiguous,
        },
        "bodies": str(bodies_path),
        "max_reuse": args.max_reuse,
        "format": args.format,
        "gates": {
            "right_edge_ratio": GATE_RIGHT_EDGE_RATIO,
            "right_edge_floor_px": GATE_RIGHT_EDGE_PX,
            "box_margin_px": replace_body.BOX_MARGIN_PX,
            "fill_slack_ratio": GATE_FILL_SLACK_RATIO,
        },
        "summary": summary,
        "used": dict(sorted(used.items())),
        "items": records,
    }
    report_path = out_dir / REPORT_NAME
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LOGGER.info("报告 %s", report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
