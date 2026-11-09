#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 ``bodies_need.json`` 的阶梯下一批正向句子，写进 ``bodies.jsonl``（只写句子，不动图）。

    python3 mock/gen_bodies.py                       # 本地池（无 NLP_API_KEY 时的默认路）
    python3 mock/gen_bodies.py --remote              # 远端跑一遍（要 NLP_API_KEY）
    python3 mock/gen_bodies.py --remote --dry-run    # 只看订单和覆盖，不写

## 为什么句子要「按字数下订单」

正文替换的硬约束是**像素宽**（见 ``core/replace_body.py``），但字数是可下单的先验：
语料里 5 字 15 行、6 字 18 行、7 字 17 行（``bodies_need.json`` 的 ``ladder``），
所以句子必须照着这张阶梯生成，而不是「随便写 100 条」。每个桶下 ``per_bucket`` 条，
是因为同一个字数里能贴上的宽度不止一种（标点半宽、字形宽窄），要留挑选余量 ——
真正挑哪条由 ``core/batch_body.py`` 按像素宽做，本脚本**不猜宽度**。

## 两条路，一份格式

``--remote``（有 ``NLP_API_KEY``）
    走 OpenAI 兼容接口（默认 DashScope），按桶要 ``per_bucket`` 条。
    模型名来自订单（``bodies_need.json`` 的 ``model``，默认 deepseek-v4-flash-0731）。

本地池（默认，没有 key 时也只有这条路）
    读 ``mock/data/pool_positive.jsonl`` —— 人写的 97 条短正向评论，长度 2~14 字、
    含标点变体（``真香`` / ``真香！``）。字数覆盖面比远端更可控，代价是没新意。

两条路写的是**同一种行**：``{model, prompt_version, bucket, idx, text, chars, src}``。
缓存键 = ``(model, prompt_version, text)`` —— ``bodies.jsonl`` 只追加、不覆盖；
改提示词就把 ``prompt_version`` +1，旧句子不会被新提示词当成命中（与
``livepipe/tools/user_filter`` 的缓存口径一致）。

## 合规检查（:func:`check_text`）

句子里**不许有**：数字（价格、年份、型号）、``@``、网址、表情、品牌、
「优惠 / 私信 / 加微信」这类引流词。理由不是洁癖：这是要对用户可见的假评论，
带数字和价格的句子会在放大后露馅，带引流的句子会把「正向评论」变成广告。
不合规的句子直接丢，**不写进缓存**，也不在日志里重复第二遍全文。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import config  # noqa: E402
import runconfig  # noqa: E402

LOGGER = logging.getLogger("gen_bodies")

DATA_DIR = Path(__file__).resolve().parent / "data"
NEED_NAME = "bodies_need.json"
POOL_NAME = "pool_positive.jsonl"
OUT_NAME = "bodies.jsonl"

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
"""OpenAI 兼容端点。换一家（月之暗面 / 硅基流动）只改这里或 ``--base-url``。"""

DEFAULT_API_KEY_ENV = "NLP_API_KEY"

MIN_CHARS = 2
MAX_CHARS = 16
"""句子长度的硬边界。

下界 2 是语料里最短的正文（``好靓`` 这类，4 行）；上界 16 是 13 字那行再留一点余量 ——
比这更长的句子在 24px 字号下要 400px 以上，语料里没有这么宽的正文。
"""

_PUNCT = "，。！？、…·"

_CJK = re.compile(r"[一-鿿]")
_ALLOWED = re.compile(r"^[一-鿿" + re.escape(_PUNCT) + r"]+$")
_FORBIDDEN = re.compile(
    r"[0-9０-９]"          # 数字：价格、年份、型号
    r"|@|https?://|www\."  # 联系方式和网址
    r"|[A-Za-z]"           # 字母：品牌缩写、型号
)
"""一票否决的字符。表情不需要单独列 —— 中文 + 标点的白名单已经把它们挡在外面。"""

_BANNED_WORDS = (
    "优惠", "折扣", "价格", "降价", "私信", "加微信", "微信", "链接", "官方",
    "客服", "定金", "咨询", "电话", "团购", "秒杀", "下单", "购买",
)
"""引流 / 交易词。正向评论说的是感受，不是价格和渠道。"""


# --------------------------------------------------------------------------
# 合规
# --------------------------------------------------------------------------


def check_text(text: str) -> Optional[str]:
    """句子能不能用；能用返回 ``None``，不能用返回理由（中文，直接进日志）。"""
    text = text.strip()
    if len(text) < MIN_CHARS:
        return "太短"
    if len(text) > MAX_CHARS:
        return "太长"
    if not _ALLOWED.match(text):
        return "有数字/字母/表情/标点以外字符"
    if _FORBIDDEN.search(text):
        return "有数字/字母"
    if not _CJK.search(text):
        return "没有汉字"
    for word in _BANNED_WORDS:
        if word in text:
            return f"含引流词「{word}」"
    return None


PUNCT_VARIANTS: tuple[str, ...] = ("！", "。")
"""句末标点变体 —— 给句子库补上**半个字**的粒度。

为什么需要：断行是按**像素宽**挑句子的，而墨迹宽按整字走（em 26 时 5 字 = 130px、
6 字 = 156px，中间 26px 是个洞）。语料里正好有落在洞里的行（实测 6 行：
宽 146 / 152 / 170 / 172 / 172 / 225 @ em 26），一句都不上去。加一个句末标点，
宽度多半个字（13px），洞就从 26px 缩到 13px —— 这 6 行全部有了候选。

为什么算「自然」：短评里带感叹号本来就常见（``真香！``、``好看！``），
不是机器痕迹。⚠️ 只给**本来没有句末标点**的句子加，绝不加第二个。
"""


def with_variants(texts: list[str]) -> list[str]:
    """句子 + 句末标点变体（**不**改原句，只往后加）。"""
    out = list(texts)
    for mark in PUNCT_VARIANTS:
        for text in texts:
            if text[-1:] in _PUNCT:
                continue
            out.append(text + mark)
    return out


def _clean_remote_line(line: str) -> str:
    """把远端返回的一行洗成候选句子。

    模型爱加序号、引号、解释（``1. 空间真大`` / ``「隔音不错」``），
    这些都不是句子的一部分；洗不掉的行在 :func:`check_text` 那关会被挡下。
    """
    line = line.strip().strip("-*` \t")
    line = re.sub(r"^\d+[.、)．]\s*", "", line)
    line = line.strip("“”「」『』\"' \t")
    return line.strip()


# --------------------------------------------------------------------------
# 订单
# --------------------------------------------------------------------------


def load_need(path: Path) -> dict:
    """读订单；缺字段就报错（不猜默认值 —— 订单是量测给的）。"""
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("kind") != "bodies_need":
        raise ValueError(f"{path} 不是 bodies_need（kind={doc.get('kind')!r}）")
    if not isinstance(doc.get("ladder"), dict) or not doc["ladder"]:
        raise ValueError(f"{path} 的 ladder 是空的：先跑 measure_bodies.py")
    return doc


def load_pool(path: Path) -> list[str]:
    """读本地池；跳过空行和 ``#`` 注释行。"""
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        out.append(str(obj["text"]))
    return out


def load_cache(path: Path) -> list[dict]:
    """读已有句子（没有文件 = 空）。坏行跳过并计数，不整份报错。"""
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            LOGGER.warning("bodies.jsonl 里有一行不是 JSON，跳过：%.40s", line)
    return rows


# --------------------------------------------------------------------------
# 远端
# --------------------------------------------------------------------------


def _prompt(bucket: int, count: int) -> str:
    return (
        f"写 {count} 条中文汽车/家电类短视频下的真实用户好评，每条 {bucket} 个汉字左右。\n"
        "要求：口语、短、各不相同；只写句子本身，一行一条，不要序号和解释。\n"
        "禁止出现：数字、英文、表情、标点以外的符号、品牌名、价格、优惠、联系方式。"
    )


def call_remote(
    base_url: str, api_key: str, model: str, bucket: int, count: int,
    *, timeout: float = 60.0,
) -> list[str]:
    """调一次 OpenAI 兼容的 ``/chat/completions``，返回洗过的候选行。

    失败一律抛 :class:`RuntimeError`，由调用方决定降级，不在这里吞。
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": _prompt(bucket, count)}],
        "temperature": 0.9,
        "n": 1,
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"HTTP {exc.code}：{detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"请求失败：{exc}") from exc

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError(f"返回里没有 choices：{json.dumps(body, ensure_ascii=False)[:300]}")
    content = (choices[0].get("message") or {}).get("content") or ""
    return [_clean_remote_line(line) for line in content.splitlines() if line.strip()]


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------


def _buckets(need: dict) -> list[int]:
    return sorted(int(k) for k in need["ladder"])


def _coverage(texts: list[str], buckets: list[int]) -> dict[str, int]:
    """每个字数桶现在有几条可用句子（按 ``len(text)`` 精确数，不做近似）。"""
    out: dict[str, int] = {}
    for bucket in buckets:
        out[str(bucket)] = sum(1 for t in texts if len(t) == bucket)
    return out


def build_rows(
    texts: list[str], *, model: str, prompt_version: int, src: str,
) -> list[dict]:
    """把句子列表变成可写的行，同一字数桶内按出现顺序编号。"""
    seen: set[str] = set()
    counters: dict[int, int] = {}
    rows: list[dict] = []
    for text in texts:
        if text in seen:
            continue
        seen.add(text)
        bucket = len(text)
        idx = counters.get(bucket, 0)
        counters[bucket] = idx + 1
        rows.append({
            "model": model,
            "prompt_version": prompt_version,
            "bucket": bucket,
            "idx": idx,
            "text": text,
            "chars": len(text),
            "src": src,
        })
    return rows


def _key(row: dict) -> tuple:
    return (row.get("model"), row.get("prompt_version"), row.get("text"))


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gen_bodies",
        description="按 bodies_need.json 的阶梯生成正向评论句子，写进 bodies.jsonl",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例：\n"
            "  python3 mock/gen_bodies.py                    # 本地池（默认）\n"
            "  python3 mock/gen_bodies.py --remote           # 走远端（要 NLP_API_KEY）\n"
            "  python3 mock/gen_bodies.py --dry-run          # 只报覆盖\n"
        ),
    )
    runconfig.add_argument(p)
    p.add_argument("--need", type=Path, default=None, help=f"订单（默认 mock/data/{NEED_NAME}）")
    p.add_argument("--pool", type=Path, default=None, help=f"本地池（默认 mock/data/{POOL_NAME}）")
    p.add_argument("--out", type=Path, default=None, help=f"句子库（默认 mock/data/{OUT_NAME}）")
    p.add_argument("--remote", action="store_true", default=None, help="走远端 NLP（没有 key 会降级到本地池）")
    p.add_argument(
        "--variants", action=argparse.BooleanOptionalAction, default=None,
        help="本地池是否加句末标点变体（默认开：补半个字的宽度粒度）",
    )
    p.add_argument("--base-url", default=None, help=f"OpenAI 兼容端点（默认 {DEFAULT_BASE_URL}）")
    p.add_argument("--api-key-env", default=None, help=f"读哪个环境变量当 key（默认 {DEFAULT_API_KEY_ENV}）")
    p.add_argument("--model", default=None, help="覆盖订单里的模型名")
    p.add_argument("--per-bucket", type=int, default=None, help="覆盖每个桶几条（0 = 用订单里的值）")
    p.add_argument("--dry-run", action="store_true", default=None, help="不写文件，只打印覆盖")
    p.add_argument("--quiet", action="store_true", default=None, help="少打日志")
    p.add_argument("--verbose", action="store_true", default=None, help="打调试日志")
    return p


DEFAULTS: dict = {
    "need": None,
    "pool": None,
    "out": None,
    "remote": False,
    "variants": True,
    "base_url": DEFAULT_BASE_URL,
    "api_key_env": DEFAULT_API_KEY_ENV,
    "model": None,
    "per_bucket": 0,
    "dry_run": False,
    "quiet": False,
    "verbose": False,
}
"""命令行与 JSON 都没给时用的值（**同时是运行配置的合法键清单与类型表**）。"""


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from_cli, from_json, cfg_path = runconfig.load(
            args, DEFAULTS, script_file=__file__)
    except runconfig.ConfigError as exc:
        print(f"运行配置有问题：{exc}", file=sys.stderr)
        return 2
    args.need = runconfig.path(args.need)
    args.pool = runconfig.path(args.pool)
    args.out = runconfig.path(args.out)

    level = logging.DEBUG if args.verbose else (logging.WARNING if args.quiet else logging.INFO)
    logging.basicConfig(level=level, format=config.LOG_FORMAT, datefmt=config.LOG_DATEFMT)
    LOGGER.info(runconfig.describe(cfg_path, from_cli, from_json))

    need_path = args.need or (DATA_DIR / NEED_NAME)
    pool_path = args.pool or (DATA_DIR / POOL_NAME)
    out_path = args.out or (DATA_DIR / OUT_NAME)
    try:
        need = load_need(need_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        LOGGER.error("订单读不了：%s", exc)
        return 2

    buckets = _buckets(need)
    per_bucket = args.per_bucket or int(need.get("per_bucket") or 8)
    model = args.model or str(need.get("model") or "local-pool")
    prompt_version = int(need.get("prompt_version") or 1)
    LOGGER.info(
        "订单 %s：%d 个字数桶 · 每桶 %d 条 · 模型 %s · 提示词 v%d",
        need_path.name, len(buckets), per_bucket, model, prompt_version,
    )
    LOGGER.info(
        "  阶梯（语料里有几行）：%s",
        " · ".join(f"{k} 字 {v}" for k, v in sorted(need["ladder"].items(), key=lambda kv: int(kv[0]))),
    )

    cache = load_cache(out_path)
    known = {_key(row) for row in cache}
    LOGGER.info("  已有句子 %d 条（缓存键 model+prompt_version+text）", len(cache))

    new_rows: list[dict] = []
    remote_ok = False
    api_key = os.environ.get(args.api_key_env, "").strip() if args.remote else ""
    if args.remote and not api_key:
        LOGGER.warning("--remote 但环境里没有 %s → 降级到本地池", args.api_key_env)
    elif args.remote:
        t0 = time.perf_counter()
        for bucket in buckets:
            try:
                lines = call_remote(args.base_url, api_key, model, bucket, per_bucket)
            except RuntimeError as exc:
                LOGGER.warning("%d 字桶远端失败（%s）→ 这一段改用本地池", bucket, exc)
                break
            accepted = [t for t in (_clean_remote_line(x) for x in lines) if check_text(t) is None]
            if len(accepted) < per_bucket:
                LOGGER.warning("%d 字桶只拿到 %d/%d 条合规句子，先要这么多", bucket, len(accepted), per_bucket)
            new_rows.extend(accepted)
            LOGGER.info("%d 字桶：%d 条", bucket, len(accepted))
        else:
            remote_ok = True
        LOGGER.info("远端用时 %.1fs", time.perf_counter() - t0)

    if not remote_ok:
        try:
            pool = load_pool(pool_path)
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            LOGGER.error("本地池读不了：%s", exc)
            return 2
        rejected: dict[str, int] = {}
        accepted_pool: list[str] = []
        for text in pool:
            reason = check_text(text)
            if reason:
                rejected[reason] = rejected.get(reason, 0) + 1
                LOGGER.debug("池里丢一条（%s）：%s", reason, text)
                continue
            accepted_pool.append(text)
        model, src = "local-pool", "pool"
        bases = len(accepted_pool)
        if args.variants:
            accepted_pool = with_variants(accepted_pool)
        new_rows.extend(accepted_pool)
        if args.variants:
            LOGGER.info(
                "  句末标点变体（！/。）：%d → %d 条（补半个字的宽度粒度）",
                bases, len(accepted_pool),
            )
        LOGGER.info(
            "本地池 %s：%d 条可用%s",
            pool_path.name, len(accepted_pool),
            f"（丢 {len(pool) - len(accepted_pool)} 条：{' · '.join(f'{k} {v}' for k, v in rejected.items())}）"
            if rejected else "",
        )
    else:
        src = "remote"

    rows = build_rows(new_rows, model=model, prompt_version=prompt_version, src=src)
    fresh = [row for row in rows if _key(row) not in known]
    texts_now = sorted({row["text"] for row in cache} | {row["text"] for row in rows})
    coverage = _coverage(texts_now, buckets)

    LOGGER.info(
        "候选 %d 条 · 新增 %d 条 · 库里合计 %d 条",
        len(rows), len(fresh), len(texts_now),
    )
    short = [f"{k} 字 {v}/{need['ladder'][k]}" for k, v in coverage.items() if v == 0]
    if short:
        LOGGER.warning("这些字数桶还没有句子：%s", " · ".join(short))
    LOGGER.info(
        "覆盖（现有句子数 / 语料需求行数）：%s",
        " · ".join(f"{k}字 {v}/{need['ladder'][k]}" for k, v in coverage.items()),
    )

    if args.dry_run:
        LOGGER.info("--dry-run：不写文件")
        return 0
    if fresh:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("a", encoding="utf-8") as fh:
            for row in fresh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    LOGGER.info("写到 %s（追加 %d 条）", out_path, len(fresh))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
