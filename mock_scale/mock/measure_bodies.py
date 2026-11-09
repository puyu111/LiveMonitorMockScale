#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量一整个语料的**正文几何**，写出两份 JSON（只读语料，不写图）。

    python3 mock/measure_bodies.py
    python3 mock/measure_bodies.py --source /tmp/review_corpus --limit 5 --dry-run

一条正文的五个数（左沿 / 右缘 / 行 / 字号 / 颜色）怎么量，见 ``core/body_text.py``
的文件头；本脚本只做三件事：**遍历语料 → 调 measure_body → 汇总成阶梯**。

几何来源按优先级自动找（``core/upstream_geom.py``）：``--geom`` 显式给的 →
``<source>/geom.json`` → 交付树 ``manifest.json`` + 各段 ``comments.jsonl`` 自动重建。

## 输出（默认落到 ``mock/data/``）

``body_measure.json``
    逐张的几何：每行的上下左右、em、颜色、擦除矩形、跳过理由，外加行带证据。
    ⚠️ **含语料目录名（= 用户昵称）**，只在本机流转，**不进交付包、不进 git**
    （与 ``measure_cache.json`` 同一口径）。

``bodies_need.json``
    **聚合**结果：字数阶梯（每个字数要几条句子）、行数分布、宽度分位、em 分布。
    不含任何昵称 ⇒ 可以进交付包，也是 ``gen_bodies.py`` 下订单的输入。

## 口径

* **只读**：全程 ``Image.open`` + ``np.array``，不写、不改、不移动语料；脚本跑完打印
  语料的 ``inputs_digest``（geom 字节 + 每张图大小），可用来对账「量测没动过语料」。
* **黑名单**（``core/source_blacklist.json``）单独记 ``status=blacklist``：
  这些张**量得到几何也不换**（官方横幅行，见 bug-371）。
* **可换 = 量全了 且 不在黑名单**。阶梯只统计可换的那批 —— 订单是给它们下的。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import batch_produce  # noqa: E402
import body_text  # noqa: E402
import config  # noqa: E402
import platforms  # noqa: E402
import runconfig  # noqa: E402
import upstream_geom  # noqa: E402

LOGGER = logging.getLogger("measure_bodies")

MEASURE_NAME = "body_measure.json"
NEED_NAME = "bodies_need.json"

DEFAULT_PER_BUCKET: int = 8
"""每个字数桶按几条句子下订单。

为什么是 8：句子按**像素宽**匹配，字数只提高命中率；同一个字数里能贴上的不会都贴得上
（标点半宽、字形宽窄不同）。8 条留出约 3~6 倍的挑选余量，同时不让远端调用数失控
（阶梯 9 个桶 ⇒ 72 条）。命中率实测见 ``docs/mock正文.html`` §03。
"""

DEFAULT_MODEL: str = "deepseek-v4-flash-0731"
"""远端模型的默认名 —— 与 ``livepipe/tools/user_filter`` 的 ``DEFAULT_MODEL`` 一致。

⚠️ 只是写进 ``bodies_need.json`` 交给 ``gen_bodies.py`` 的**意向**，本脚本不联网。
模型名写错时那边会 404，并把 ``/models`` 的现名捞出来印在日志里。
"""

DEFAULT_PROMPT_VERSION: int = 1
"""提示词版本。进缓存键 ``(model, prompt_version, 桶, 条号)`` —— 改提示词就 +1，
旧条目不会被新提示词"命中"（``bodies.jsonl`` 只追加）。"""


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="measure_bodies",
        description="量语料里每条评论正文的几何，写出 body_measure.json 与 bodies_need.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例：\n"
            "  python3 mock/measure_bodies.py                       # 全读 measure_bodies.config.json\n"
            "  python3 mock/measure_bodies.py --source <语料根> --limit 5 --dry-run\n"
            "  python3 mock/measure_bodies.py --no-blacklist        # 连黑名单也量成可换（只对账用）\n"
        ),
    )
    runconfig.add_argument(p)
    # ⚠️ 可被运行配置 JSON 控制的参数一律 ``default=None``（见 runconfig.py 模块说明）。
    p.add_argument("--source", type=Path, default=None, help="语料根目录（必填）")
    p.add_argument(
        "--geom", type=Path, default=None,
        help="上游几何 JSON（不给就依次试 <source>/geom.json、交付树 manifest 自动重建）",
    )
    p.add_argument(
        "--out", type=Path, default=None,
        help=f"两份 JSON 写到哪个目录（默认本脚本旁边的 data/：{MEASURE_NAME} · {NEED_NAME}）",
    )
    p.add_argument(
        "--platform", default=None,
        help=f"平台键（决定黑名单怎么生效，默认 generic；认得的：{', '.join(sorted(platforms.PROFILES))}）",
    )
    p.add_argument(
        "--em", type=int, default=None,
        help="强制字号（0 = 每张自己量墨迹字高；批量阶段会用名字的 em 覆盖）",
    )
    p.add_argument("--limit", type=int, default=None, help="只量前 N 张（0 = 全量）")
    p.add_argument(
        "--blacklist", action=argparse.BooleanOptionalAction, default=None,
        help="套用 source_blacklist.json（默认开）；--no-blacklist 连黑名单也标成可换（只对账用）",
    )
    p.add_argument("--per-bucket", type=int, default=None, help="每个字数桶几条句子（写进 bodies_need.json）")
    p.add_argument("--model", default=None, help="远端模型名（只写进 bodies_need.json，不联网）")
    p.add_argument("--prompt-version", type=int, default=None, help="提示词版本（缓存键的一部分）")
    p.add_argument("--dry-run", action="store_true", default=None, help="只量、只打印，不写文件")
    p.add_argument("--quiet", action="store_true", default=None, help="少打日志")
    p.add_argument("--verbose", action="store_true", default=None, help="打调试日志")
    return p


DEFAULTS: dict = {
    "source": None,
    "geom": None,
    "out": None,
    "platform": "generic",
    "em": 0,
    "limit": 0,
    "blacklist": True,
    "per_bucket": DEFAULT_PER_BUCKET,
    "model": DEFAULT_MODEL,
    "prompt_version": DEFAULT_PROMPT_VERSION,
    "dry_run": False,
    "quiet": False,
    "verbose": False,
}
"""命令行与 JSON 都没给时用的值（**同时是运行配置的合法键清单与类型表**）。"""


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------


def _percentile(values: list[int], q: float) -> int:
    """最近秩分位（小样本够用，不引 numpy 的插值口径）。"""
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return int(ordered[idx])


def _distribution(values: list[int]) -> dict:
    """一组像素量的五个数：min / p25 / 中位 / p75 / max。"""
    return {
        "min": _percentile(values, 0.0),
        "p25": _percentile(values, 0.25),
        "median": _percentile(values, 0.5),
        "p75": _percentile(values, 0.75),
        "max": _percentile(values, 1.0),
    }


def _digest(source: Path, geom_paths: tuple[Path, ...], rels: list[str]) -> str:
    """语料指纹：几何来源的字节 + 每张图的字节。两次量测一致 = 语料没被动过。"""
    h = hashlib.sha256()
    for path in geom_paths:
        try:
            h.update(path.read_bytes())
        except OSError:  # pragma: no cover - 取几何前已经确认过存在
            h.update(b"?")
    for rel in rels:
        path = source / rel
        try:
            h.update(rel.encode("utf-8"))
            h.update(path.read_bytes())
        except OSError:  # pragma: no cover - 量之前已经确认过存在
            h.update(b"?")
    return h.hexdigest()[:16]


def _ladder(items: list[dict]) -> dict[str, int]:
    """字数阶梯：``{"5": 15, ...}``，只数 ``status == ok`` 的条目（按整段字数）。"""
    out: dict[str, int] = {}
    for item in items:
        if item["status"] != "ok":
            continue
        key = str(item["chars"])
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: int(kv[0])))


def _ladder_all(items: list[dict]) -> dict[str, int]:
    """同一把尺子，但**所有量到正文的条目**都数（含黑名单 / 跨行的）—— 只用来对账。"""
    out: dict[str, int] = {}
    for item in items:
        if not item["lines"]:
            continue
        key = str(item["chars"])
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: int(kv[0])))


def _counts(items: list[dict]) -> dict:
    """状态、跳过理由、行数三张计数表。"""
    status: dict[str, int] = {}
    reasons: dict[str, int] = {}
    line_counts: dict[str, int] = {}
    for item in items:
        status[item["status"]] = status.get(item["status"], 0) + 1
        if item["reason"]:
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
        if item["status"] == "ok":
            key = str(item["line_count"])
            line_counts[key] = line_counts.get(key, 0) + 1
    return {"status": status, "skipped_by_reason": reasons, "line_count": line_counts}


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def measure_one(source: Path, rel: str, geom: dict, em: int) -> dict:
    """量一张图，返回写进 ``body_measure.json`` 的那一条（不含黑名单字段）。"""
    import numpy as np
    from PIL import Image

    path = source / rel
    frame = np.array(Image.open(path).convert("RGB"))
    region = body_text.measure_body(frame, geom, em=em or None)
    record = {
        "rel": rel,
        "size": [int(frame.shape[1]), int(frame.shape[0])],
    }
    record.update(region.to_dict())
    return record


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from_cli, from_json, cfg_path = runconfig.load(
            args, DEFAULTS, script_file=__file__)
    except runconfig.ConfigError as exc:
        print(f"运行配置有问题：{exc}", file=sys.stderr)
        return 2
    args.source = runconfig.path(args.source)
    args.geom = runconfig.path(args.geom)
    args.out = runconfig.path(args.out)

    level = logging.DEBUG if args.verbose else (logging.WARNING if args.quiet else logging.INFO)
    logging.basicConfig(level=level, format=config.LOG_FORMAT, datefmt=config.LOG_DATEFMT)
    if not args.verbose:
        quiet = logging.ERROR if args.quiet else logging.WARNING
        for name in ("inkmask", "PIL"):
            logging.getLogger(name).setLevel(quiet)

    LOGGER.info(runconfig.describe(cfg_path, from_cli, from_json))

    if args.source is None:
        LOGGER.error("没给语料根：命令行 `--source` 或 measure_bodies.config.json 里的 `\"source\"`")
        return 2
    if not args.source.is_dir():
        LOGGER.error("语料根不是目录：%s", args.source)
        return 2
    if args.platform not in platforms.PROFILES:
        LOGGER.error("不认识的平台 %r（认得的：%s）", args.platform, sorted(platforms.PROFILES))
        return 2

    try:
        geom_src = upstream_geom.load(args.source, args.geom)
    except upstream_geom.GeomError as exc:
        LOGGER.error("%s", exc)
        return 2
    items_in = geom_src.items

    blacklist: dict[str, str] = {}
    if args.blacklist:
        try:
            blacklist = batch_produce.load_blacklist(args.platform)
        except (FileNotFoundError, ValueError) as exc:
            LOGGER.error("黑名单读不了：%s", exc)
            return 2

    rels = sorted(items_in)
    missing = {rel for rel in rels if not (args.source / rel).is_file()}
    if missing:
        LOGGER.warning("%d 张在几何里有、在语料里找不到，先跳过：%s", len(missing), sorted(missing)[:3])
        rels = [rel for rel in rels if rel not in missing]
    if args.limit > 0:
        rels = rels[: args.limit]

    out_dir = args.out or (Path(__file__).resolve().parent / "data")
    LOGGER.info("量测 %d 张 · 语料 %s · 几何 %s", len(rels), args.source, geom_src.describe())

    t0 = time.perf_counter()
    measured: list[dict] = []
    for i, rel in enumerate(rels, 1):
        item = measure_one(args.source, rel, items_in[rel], args.em)
        blocked = blacklist.get(rel)
        item["blacklist"] = blocked
        if blocked:
            item["status"] = "blacklist"
        measured.append(item)
        if args.verbose:
            LOGGER.debug(
                "[%d/%d] %s → %s%s", i, len(rels), rel, item["status"],
                f"（{item['reason']}）" if item["reason"] else "",
            )

    elapsed = time.perf_counter() - t0
    counts = _counts(measured)
    ladder = _ladder(measured)
    ladder_all = _ladder_all(measured)
    ok_items = [item for item in measured if item["status"] == "ok"]

    widths = [line["width"] for item in ok_items for line in item["lines"]]
    line1_widths = [item["lines"][0]["width"] for item in ok_items]
    ems = [item["em"] for item in ok_items]
    two_line = [
        {"line_chars": [line["chars"] for line in item["lines"]],
         "line_widths": [line["width"] for line in item["lines"]]}
        for item in ok_items if item["line_count"] > 1
    ]
    digest = _digest(args.source, geom_src.checked, sorted(items_in))

    measure_doc = {
        "kind": "body_measure",
        "schema_version": 1,
        "_说明": (
            "逐张的正文几何。⚠️ rel 是语料目录名（= 用户昵称）：只在本机流转，"
            "不进交付包、不进 git（与 measure_cache.json 同口径）。"
            "status: ok=可换 / skip=几何跳过（见 reason）/ blacklist=在 source_blacklist.json 里。"
        ),
        "_怎么读": (
            "lines[0] 是冒号那一行；lines[1]（若有）是折行，left 是段落左沿。"
            "断行宽度用 right，不要用 text_box 的 x2（比墨迹宽 1~7px）。"
            "erase 是擦这一行要铺平的矩形（已外扩）。em 来自墨迹字高，"
            "批量阶段应改用这一行名字的 em。"
        ),
        "generator": {"script": "mock/measure_bodies.py", "module": "core/body_text.py"},
        "source": str(args.source),
        "geom": str(geom_src.path) if geom_src.path else "",
        "geom_source": geom_src.origin,
        "geom_stats": {
            "images": geom_src.images,
            "matched": geom_src.matched,
            "missing": geom_src.missing,
            "ambiguous": geom_src.ambiguous,
        },
        "platform": args.platform,
        "blacklist_enabled": bool(args.blacklist),
        "em_forced": args.em,
        "inputs_digest": digest,
        "summary": {
            "total": len(measured),
            "eligible": len(ok_items),
            **counts,
            "chars_ladder": ladder,
            "chars_ladder_all_rows_with_body": ladder_all,
            "body_width_px": _distribution(widths),
            "line1_width_px": _distribution(line1_widths),
            "em": _distribution(ems),
            "two_line": two_line,
        },
        "items": measured,
    }

    need_doc = {
        "kind": "bodies_need",
        "schema_version": 1,
        "_说明": (
            "正文的『要几条多长的句子』。**聚合结果、不含昵称**，可以进交付包；"
            "gen_bodies.py 读这份下订单，" \
            "replace_body.py 按像素宽度从 bodies.jsonl 里挑。"
        ),
        "_怎么读": (
            "ladder 的键是『整段正文几个字』，值是语料里有几行需要这个长度；"
            "per_bucket 是每个桶要下几条（字数只提高命中率，最后仍按像素宽度挑）。"
            "two_line 是折行行的行字数对，句子必须能恰好折在同一个位置。"
            "corpus.skipped = images − eligible（其中黑名单 4 张、纯表情 2 张、框跨下一条评论 2 张；"
            "skipped_by_reason 只数量测给出的几何理由，黑名单的 2 张官方横幅几何本身是好的，所以不在此列）。"
        ),
        "generator": {"script": "mock/measure_bodies.py", "module": "core/body_text.py"},
        "inputs_digest": digest,
        "corpus": {
            "images": len(measured),
            "eligible": len(ok_items),
            "skipped": len(measured) - len(ok_items),
            "line_count": counts["line_count"],
            "skipped_by_reason": counts["skipped_by_reason"],
            "blacklist": counts["status"].get("blacklist", 0),
        },
        "ladder": ladder,
        "per_bucket": args.per_bucket,
        "prompt_version": args.prompt_version,
        "model": args.model,
        "two_line": two_line,
        "body_width_px": _distribution(widths),
        "line1_width_px": _distribution(line1_widths),
        "em": _distribution(ems),
        "ladder_all_rows_with_body": ladder_all,
    }

    # ---- 报告 ----
    total = len(measured)
    LOGGER.info(
        "量完 %d 张（%.1fs）：可换 %d · 黑名单 %d · 几何跳过 %d",
        total, elapsed, len(ok_items),
        counts["status"].get("blacklist", 0),
        counts["status"].get("skip", 0),
    )
    if counts["skipped_by_reason"]:
        LOGGER.info(
            "  跳过理由：%s",
            " · ".join(f"{body_text.REASON_TEXT.get(k, k)} {v}" for k, v in counts["skipped_by_reason"].items()),
        )
    LOGGER.info(
        "  行数：%s · 字数阶梯（可换）：%s",
        " · ".join(f"{k} 行 {v}" for k, v in sorted(counts["line_count"].items())),
        " · ".join(f"{k} 字 {v}" for k, v in ladder.items()),
    )
    LOGGER.info(
        "  正文宽 px：min %d · p25 %d · 中位 %d · p75 %d · max %d · em 中位 %d",
        _percentile(widths, 0.0), _percentile(widths, 0.25), _percentile(widths, 0.5),
        _percentile(widths, 0.75), _percentile(widths, 1.0), _percentile(ems, 0.5),
    )
    LOGGER.info("  语料指纹 inputs_digest=%s（量测只读，指纹应与量之前一致）", digest)

    if args.dry_run:
        LOGGER.info("--dry-run：不写文件")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    measure_path = out_dir / MEASURE_NAME
    need_path = out_dir / NEED_NAME
    measure_path.write_text(
        json.dumps(measure_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    need_path.write_text(
        json.dumps(need_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LOGGER.info("写到 %s · %s", measure_path, need_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
