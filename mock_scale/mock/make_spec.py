#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 `batch_produce.py` 要的名单 JSON —— 给路径、给数量，名字由脚本生成。

**分线（2026-10-11）后住在 ``mock/``**：本文件属于 mock 用户线。「这次怎么跑」
写在同目录的 ``make_spec.config.json`` 里，命令行只用来逐项覆盖（见 ``runconfig.py``）。
默认名单落在 ``mock/names/``（**不再落 cwd**）。从工程根敲::

    python3 mock/make_spec.py
    python3 mock/make_spec.py --source /home/puyu/work/data/抖音奇瑞汽车/all_users_正面_3张以上 \
                                      --users 20 --per-user 3

只问三件事：**数据路径** · **要几个名字** · **每个名字出几张**。其余由脚本定：

1. **平台从路径里认**（`platforms.detect_platform_from_path`，与批量脚本同一张别名表）——
   路径里含「抖音」就写 `douyin`，含「视频号」/「微博」同理（含在复合目录名里也认，
   例如 `抖音奇瑞汽车`）。认不出来就**不写这个字段**并在控制台说一声 —— 批量脚本自己
   还会从路径判一次，结果一样。
2. **名字全部是中文**：由「前缀 / 主体 / 后缀」三个词池组合，生成后**逐字校验**
   （只接受 CJK 统一汉字），去重后乱序输出。词池是通用词，**刻意不从语料里抄真昵称**
   —— 那是别人的身份，不该被拿去做合成身份。
3. **名字长度默认 2~5 字**，不是拍的：拿抖音语料量过 48 张素材（`--fit-corpus` 干的就是
   这件事），槽位按原字号**中位数只容得下 3.1 个字**；`batch_produce` 的字号判据是
   「缩到原字号 60% 以下就换素材」，于是硬上限约 `槽位字数 / 0.6` ⇒ **5 字左右**。
   再长（比如 9 字）就会所有素材都报 `FONT_SMALL`、一个名字也产不出片。

## 输出

```json
{"source": "/home/puyu/work/data/抖音奇瑞汽车/all_users_正面_3张以上",
 "platform": "douyin", "default_count": 3,
 "users": ["小柚子", "月亮爱笑", "猫不哭", ...]}
```

字段规格见 `README.md`「名单 JSON 的格式」。文件名默认
`names_<平台>_<N>人x<K>张.json`，写在当前目录；`source` 一律写成**绝对路径**
（这样 JSON 搬到哪都能用）。写完会打印下一条该跑的命令。

## `--fit-corpus`：先量一遍素材再定名字（可选）

打开后先量 48 张素材（检测 + 定位，不出片），只保留**至少能被一张素材按原字号容下**的名字
—— 名字太长时它会一直生成到凑够 N 个为止；实在凑不够就**如实报缺口**（退出码 3）并写下手头
这批，而不是假装凑齐了。

⚠️ `multiprocessing` 用的是 forkserver 启动方式，所以进程池只能在
``if __name__ == "__main__":`` 里面用（本文件已按此结构写）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import batch_produce  # noqa: E402
import config  # noqa: E402
import draw_text  # noqa: E402
import platforms  # noqa: E402
import runconfig  # noqa: E402

LOGGER = logging.getLogger("make_spec")

DEFAULT_PER_USER: int = 3
DEFAULT_MIN_LEN: int = 2
DEFAULT_MAX_LEN: int = 5
DEFAULT_SEED: int = 20260930
MIN_LEN_LIMIT: int = 2
MAX_LEN_LIMIT: int = 6
"""生成规则只覆盖 2~6 字（见 :data:`RULES`）。6 字已经接近语料的硬上限，默认不给到。"""

PROBE_SAMPLE: int = 48
"""``--fit-corpus`` 量多少张素材。48 张 ≈ 2s（8 进程），够看出槽位容量。"""

MAX_TRIES_PER_NAME: int = 200
"""``--fit-corpus`` 下每个名字最多重抽多少次，防止在「这种长度根本放不下」时死转。"""

CJK_ONLY = re.compile(r"^[一-鿿]+$")
"""名字的校验：整串都是 CJK 统一汉字（不含数字、字母、emoji、标点）。"""


# --------------------------------------------------------------------------
# 词池与生成规则
# --------------------------------------------------------------------------
#
# ⚠️ 这些词是**通用词**，不是从语料里抄的昵称 —— 语料里的昵称属于真实用户，拿来做
# 合成身份不合适。组合出来的名字是「别人没用过的」这一批里随机的一批。

PREFIX_1: tuple[str, ...] = ("小", "老", "大", "阿")
"""单字前缀（搭单字主体成 2 字名，搭双字主体成 3 字名）。"""

CORE_1: tuple[str, ...] = tuple("猫狗鹿兔熊鲸鱼燕山海月星风雨雪竹石茶糖米花草")
"""单字主体 —— 能被前缀领、也能被后缀接（``小猫`` / ``猫不哭`` 都读得通）。"""

NOUN_2: tuple[str, ...] = (
    "柚子", "橙子", "柠檬", "布丁", "芋圆", "咖啡", "奶茶", "包子", "汤圆", "米粒",
    "月亮", "星河", "太阳", "晚风", "山茶", "竹子", "石头", "燕子", "熊猫", "鲸鱼",
    "兔子", "半夏", "小满", "云朵", "棉花", "山楂", "青梅", "麦芽",
)
"""双字**名词**（食物 / 动物 / 自然物）—— 前缀和后缀都能接（``小柚子`` / ``柚子不哭``）。"""

NICK_2: tuple[str, ...] = (
    "点点", "多多", "圆圆", "笑笑", "三三", "木木", "北北", "南南", "绵绵", "小七", "小鹿",
)
"""双字**昵称芯**（叠字 / 自带前缀的那种）—— **只能接后缀**，前面再加前缀会读不通
（``点点不困`` ✓，``大点点`` ✗）。"""

SUFFIX_2: tuple[str, ...] = ("不哭", "不乖", "很乖", "爱笑", "会飞", "不困", "不吵", "不闹", "爱睡")
SUFFIX_3: tuple[str, ...] = (
    "不加班", "爱睡觉", "在路上", "要加油", "会发光", "有点甜", "不打烊",
    "慢慢来", "不太冷", "有点困", "不熬夜", "爱吃饭",
)

# 长度 → 拼法（每种拼法都恰好产出该长度；同一长度给几种拼法，是为了让名字别一个模子刻出来）
RULES: dict[int, tuple[str, ...]] = {
    2: ("prefix1+core1",),
    3: ("prefix1+noun2", "core1+suffix2"),
    4: ("noun2+suffix2", "nick2+suffix2", "prefix1+core1+suffix2"),
    5: ("noun2+suffix3", "nick2+suffix3", "prefix1+core1+suffix3"),
    6: ("prefix1+noun2+suffix3",),
}


def _compose(rule: str, rng: random.Random) -> str:
    """按拼法名拼一个名字。"""
    parts = {
        "prefix1": lambda: rng.choice(PREFIX_1),
        "core1": lambda: rng.choice(CORE_1),
        "noun2": lambda: rng.choice(NOUN_2),
        "nick2": lambda: rng.choice(NICK_2),
        "suffix2": lambda: rng.choice(SUFFIX_2),
        "suffix3": lambda: rng.choice(SUFFIX_3),
    }
    return "".join(parts[name]() for name in rule.split("+"))


def generate_names(count: int, min_len: int, max_len: int, rng: random.Random) -> list[str]:
    """生成 ``count`` 个**互不相同**的中文名。

    长度在 ``[min_len, max_len]`` 里按「3~4 字优先」加权抽（语料里真昵称也是这个长度），
    再随机选一种该长度的拼法。

    Raises:
        ValueError: 张数要求为 0 或负数，或词池凑不出这么多不重名的组合。
    """
    lengths = [n for n in range(min_len, max_len + 1) if n in RULES]
    if not lengths:
        raise ValueError(f"长度区间 {min_len}~{max_len} 里没有可用的拼法（支持 {MIN_LEN_LIMIT}~{MAX_LEN_LIMIT} 字）")
    # 3~4 字优先：权重随「离 3.5 字有多远」衰减，但不为 0（否则 2 字名一个都出不来）
    weights = [1.0 / (1.0 + abs(n - 3.5)) for n in lengths]

    seen: set[str] = set()
    out: list[str] = []
    guard = count * 60 + 600  # 组合空间有限时别死转
    while len(out) < count and guard > 0:
        guard -= 1
        length = rng.choices(lengths, weights=weights, k=1)[0]
        name = _compose(rng.choice(RULES[length]), rng)
        if len(name) != length:
            raise AssertionError(f"拼法产出的长度不对：{length} != {len(name)}（{name}）")
        if not CJK_ONLY.match(name):
            raise AssertionError(f"生成的名字不是纯中文：{name!r} —— 词池里混进了非汉字")
        if name in seen:
            continue
        seen.add(name)
        out.append(name)

    if len(out) < count:
        raise ValueError(
            f"词池凑不出 {count} 个不重名的名字（{min_len}~{max_len} 字只凑到 {len(out)} 个）——"
            "把长度区间放宽，或减少名字数量"
        )
    rng.shuffle(out)
    return out


# --------------------------------------------------------------------------
# 平台：从路径里认
# --------------------------------------------------------------------------


@dataclass
class PlatformGuess:
    """路径里认出来的平台，以及**证据**（给控制台看的）。"""

    key: Optional[str]
    segment: str = ""
    """命中的那一段路径名（例如 ``抖音奇瑞汽车``）；认不出时为空。"""

    def describe(self) -> str:
        if self.key is None:
            return "认不出（路径里没有平台名）"
        label = platforms.profile_for(self.key).label
        how = f"含「{self.segment}」" if self.segment else "精确命中"
        return f"{label}（key={self.key}，路径段{how}）"


def alias_examples() -> str:
    """给「手写一个 platform」用的示例写法 —— 每个平台挑一个**给人看的**别名。

    ⚠️ 不能取「最长的三个别名」（第一版就这么写的，列出来是
    ``wechat_channels / channels / douyin``）—— 别名表里的顺序是
    「先中文、再 key、再英文缩写」，所以每个 key 取**第一个**别名才是 `抖音 / 视频号 / 微博`。
    """
    seen: set[str] = set()
    out: list[str] = []
    for alias, key in platforms.PLATFORM_ALIASES.items():
        if key in seen:
            continue
        seen.add(key)
        out.append(alias)
    return " / ".join(out)


def guess_platform(source: Path) -> PlatformGuess:
    """从路径里认平台，并把**命中的那一段**带回来。

    判定与批量脚本共用 ``platforms.detect_platform_from_path``（**不自己另写一套**），
    这里只是把「命中的是哪一段」找出来给人看 —— 两套判据一旦分头演化就会漂。
    """
    key = platforms.detect_platform_from_path(source)
    if key is None:
        return PlatformGuess(None)
    for part in [source.name, *(p.name for p in source.parents)]:
        if platforms.resolve_key(part) == key:
            return PlatformGuess(key, part)
    for part in [source.name, *(p.name for p in source.parents)]:
        low = part.lower()
        if any(alias in low and k == key for alias, k in platforms.PLATFORM_ALIASES.items()):
            return PlatformGuess(key, part)
    return PlatformGuess(key)


# --------------------------------------------------------------------------
# --fit-corpus：按素材的实际槽位校对名字长度
# --------------------------------------------------------------------------


@dataclass
class CorpusFit:
    """量过一遍的素材，以及「名字在它上面放得下放不下」。

    ⚠️ **素材只量一次**（量一次 48 张 ≈ 2s），之后每个名字的打分只在内存里量字宽。
    第一版是每个候选名字都调一次「量素材」，60 个候选就是 60 × 48 张 = 两分钟 ——
    而中间那 48 张的结果每次都一模一样。
    """

    candidates: list
    """`batch_produce.measure_one` 的产出（只用 ``slot_width`` / ``natural_font_size``）。"""

    rejected: dict[str, int]
    """测量阶段被拒的素材数（按原因）—— 与批量脚本同一套口径。"""

    per_name: dict[str, int] = field(default_factory=dict)
    """每个名字「能被多少张素材按原字号容下」。0 = 只能缩字号放。"""

    @property
    def sources(self) -> int:
        return len(self.candidates)

    def feasible(self, name: str) -> int:
        """这个名字能被多少张素材**按原字号**容下（0 = 一张都不用缩字号就能写下它）。

        判据与 `batch_produce` 选材时用的完全一致：``measure_ink(名字, 原字号字体) <= 槽位宽``。
        """
        total = 0
        for cand in self.candidates:
            font = _font(cand.natural_font_size)
            if draw_text.measure_ink(name, font)[0] <= cand.slot_width:
                total += 1
        return total

    def score(self, names: Sequence[str]) -> None:
        """把一批名字的可行张数算进 ``per_name``（幂等）。"""
        for name in names:
            if name not in self.per_name:
                self.per_name[name] = self.feasible(name)

    def fit_any(self, name: str) -> bool:
        return self.per_name.get(name, 0) > 0


_FONTS: dict[int, object] = {}


def _font(size: int):
    """按字号缓存字体对象 —— ``load_font`` 每次都读一次字体文件，打分时会被调上千次。"""
    if size not in _FONTS:
        _FONTS[size] = draw_text.load_font(size)
    return _FONTS[size]


def probe_corpus(source: Path, profile_key: str, seed: int, workers: int) -> CorpusFit:
    """量一批素材（检测 + 定位，不出片），供名字长度校对用。"""
    profile = platforms.profile_for(profile_key)
    material, _counts = batch_produce.collect_material(source, profile)
    order = batch_produce.spread_order(material, random.Random(seed))
    candidates, rejected = batch_produce.measure_pool(order[:PROBE_SAMPLE], profile.key, workers)
    return CorpusFit(candidates=candidates, rejected=rejected)


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="make_spec",
        description="生成 batch_produce.py 用的名单 JSON：给路径与数量，名字由脚本生成（纯中文）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例：\n"
            "  python mock/make_spec.py                            # 全读 make_spec.config.json\n"
            "  python mock/make_spec.py --source <语料根> --users 20 --per-user 3\n"
            "  python mock/make_spec.py --source <语料根> --users 50 --per-user 2 --fit-corpus\n"
            "  python mock/make_spec.py --source <语料根> --users 10 --dry-run\n"
            "  写完直接跑：python core/batch_produce.py mock/names/names_douyin_20x3.json\n"
        ),
    )
    runconfig.add_argument(p)
    # ⚠️ 可被运行配置 JSON 控制的参数一律 ``default=None``（见 runconfig.py 模块说明）。
    p.add_argument("--source", type=Path, default=None, help="数据路径（素材根目录；平台从它里面认）")
    p.add_argument("--users", type=int, default=None, help="生成几个名字")
    p.add_argument(
        "--per-user", type=int, default=None,
        help=f"每个名字出几张（写进 JSON 的 default_count，默认 {DEFAULT_PER_USER}）",
    )
    p.add_argument(
        "--min-len", type=int, default=None,
        help=f"名字最少几个字（默认 {DEFAULT_MIN_LEN}）",
    )
    p.add_argument(
        "--max-len", type=int, default=None,
        help=f"名字最多几个字（默认 {DEFAULT_MAX_LEN}；支持到 {MAX_LEN_LIMIT} 字，"
             "再长语料的槽位就放不下、会全被 FONT_SMALL 拒掉）",
    )
    p.add_argument("--out", type=Path, default=None, help="JSON 写到哪（默认 names_<平台>_<N>人x<K>张.json）")
    p.add_argument("--seed", type=int, default=None, help=f"随机种子（默认 {DEFAULT_SEED}）")
    p.add_argument(
        "--fit-corpus", action="store_true", default=None,
        help=f"先量 {PROBE_SAMPLE} 张素材，只保留「至少能被一张素材按原字号容下」的名字"
             "（名字太长时一直重抽到凑够；凑不够就报缺口）",
    )
    p.add_argument("--workers", type=int, default=None, help="并行进程数（只在 --fit-corpus 下用），0 = min(8, CPU)")
    p.add_argument("--dry-run", action="store_true", default=None, help="只打印平台与名字，不写文件")
    p.add_argument("--quiet", action="store_true", default=None, help="少打日志")
    p.add_argument("--verbose", action="store_true", default=None, help="打调试日志")
    return p


DEFAULTS: dict = {
    "source": None,
    "users": 0,
    "per_user": DEFAULT_PER_USER,
    "min_len": DEFAULT_MIN_LEN,
    "max_len": DEFAULT_MAX_LEN,
    "out": None,
    "seed": DEFAULT_SEED,
    "fit_corpus": False,
    "workers": 0,
    "dry_run": False,
    "quiet": False,
    "verbose": False,
}
"""命令行与 JSON 都没给时用的值（**同时是运行配置的合法键清单与类型表**）。

⚠️ ``users`` 这里是 **0**（读作「没给」），不是 argparse 时代的 required ——
必须给够才跑，由 :func:`main` 显式报错，别让 ``required=True`` 与 JSON 打架。
"""


def default_out_path(out: Optional[Path], guess: PlatformGuess, users: int, per_user: int) -> Path:
    """默认文件名：``names_<平台>_<N>人x<K>张.json``（认不出平台就省掉那一段）。

    ⚠️ **2026-10-11 分线后落到 ``<本脚本目录>/names/``，不再落 cwd**：以前是
    ``Path.cwd()``，于是「在哪个目录下敲命令」决定名单落在哪 —— 从工程根敲和从
    ``mock/`` 里敲结果不同，复现命令也就没法写死。现在与 `names.json` 同居一处。
    """
    if out is not None:
        return out
    key = guess.key or "unknown"
    return Path(__file__).resolve().parent / "names" / f"names_{key}_{users}人x{per_user}张.json"


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from_cli, from_json, cfg_path = runconfig.load(
            args, DEFAULTS, script_file=__file__)
    except runconfig.ConfigError as exc:
        print(f"运行配置有问题：{exc}", file=sys.stderr)
        return 2
    args.source = runconfig.path(args.source)
    args.out = runconfig.path(args.out)

    level = logging.DEBUG if args.verbose else (logging.WARNING if args.quiet else logging.INFO)
    logging.basicConfig(level=level, format=config.LOG_FORMAT, datefmt=config.LOG_DATEFMT)
    if not args.verbose:
        # --fit-corpus 会走管线，管线内部每张图都打日志，这里只留 WARNING
        quiet = logging.ERROR if args.quiet else logging.WARNING
        for name in ("batch_produce", "replace_username", "inkmask", "draw_text"):
            logging.getLogger(name).setLevel(quiet)

    t0 = time.perf_counter()
    LOGGER.info(runconfig.describe(cfg_path, from_cli, from_json))

    # ---- 参数与路径 ----
    if args.source is None:
        LOGGER.error("没给素材路径：命令行 `--source` 或 make_spec.config.json 里的 `\"source\"`")
        return 2
    if args.users <= 0:
        LOGGER.error("--users 必须 ≥ 1，收到 %d", args.users)
        return 2
    if args.per_user <= 0:
        LOGGER.error("--per-user 必须 ≥ 1，收到 %d", args.per_user)
        return 2
    if not (MIN_LEN_LIMIT <= args.min_len <= args.max_len <= MAX_LEN_LIMIT):
        LOGGER.error(
            "--min-len/--max-len 要满足 %d ≤ min ≤ max ≤ %d，收到 %d/%d",
            MIN_LEN_LIMIT, MAX_LEN_LIMIT, args.min_len, args.max_len,
        )
        return 2

    source = args.source.expanduser()
    if not source.is_dir():
        LOGGER.error("数据路径不存在或不是目录：%s", source)
        return 2
    source = source.resolve()

    # ---- 平台：从路径里认 ----
    guess = guess_platform(source)
    profile_key = guess.key or "generic"
    LOGGER.info("数据路径 %s", source)
    LOGGER.info("平台 %s", guess.describe())
    if guess.key is None:
        LOGGER.warning(
            "路径里没认出平台 ⇒ JSON 里**不写 platform 字段**；批量脚本会自己再判一次（结果同样是"
            "generic 兜底档）。要指定就手写一个：\"platform\": \"%s\"",
            alias_examples(),
        )

    # ---- 生成名字 ----
    rng = random.Random(args.seed)
    try:
        names = generate_names(args.users, args.min_len, args.max_len, rng)
    except ValueError as exc:
        LOGGER.error("%s", exc)
        return 2

    fit: Optional[CorpusFit] = None
    if args.fit_corpus:
        workers = args.workers or max(1, min(8, os.cpu_count() or 1))
        LOGGER.info("按素材校对名字长度：量 %d 张（%d 进程）…", PROBE_SAMPLE, workers)
        fit = probe_corpus(source, profile_key, args.seed, workers)
        fit.score(names)
        LOGGER.info(
            "  量到 %d 张可用素材（拒 %s）",
            fit.sources,
            " ".join(f"{k}×{v}" for k, v in sorted(fit.rejected.items())) or "无",
        )
        # 只剔掉「一张素材都容不下」的名字 —— 那种必然缩字号或触发 FONT_SMALL。
        # 「放得下但不宽裕」的照留：批量脚本选材时会优先给它配宽槽位。
        dropped = [n for n in names if not fit.fit_any(n)]
        if dropped:
            LOGGER.warning(
                "%d 个名字在量过的素材里**一张都容不下**（只能缩字号或触发 FONT_SMALL）：%s —— 重抽",
                len(dropped), " ".join(dropped[:6]) + ("…" if len(dropped) > 6 else ""),
            )
            keep = [n for n in names if fit.fit_any(n)]
            tries = 0
            while len(keep) < args.users and tries < args.users * MAX_TRIES_PER_NAME:
                tries += 1
                cand = generate_names(1, args.min_len, args.max_len, rng)[0]
                if cand in fit.per_name:      # 抽到重复的（含刚被剔掉的）就跳过
                    continue
                if fit.feasible(cand) > 0:
                    fit.per_name[cand] = fit.feasible(cand)
                    keep.append(cand)
            if len(keep) < args.users:
                LOGGER.error(
                    "凑不够：只找到 %d 个「放得下」的名字（要 %d 个）—— %d~%d 字在这个语料上太长了。"
                    "把 --max-len 调小（量出来中位槽位只容得下 3 字左右，硬上限约 5 字）",
                    len(keep), args.users, args.min_len, args.max_len,
                )
            names = keep
            if not names:
                return 3
        rng.shuffle(names)
        LOGGER.info(
            "  校对结果：%d 个名字里 %d 个至少有一张素材能按原字号容下（其余只能缩字号出片）",
            len(names), sum(1 for n in names if fit.fit_any(n)),
        )

    # ---- 打印名字（给人过一眼） ----
    LOGGER.info("生成 %d 个名字（%d~%d 字）· 每个 %d 张 ⇒ 合计 %d 张：",
                len(names), args.min_len, args.max_len, args.per_user, len(names) * args.per_user)
    per_line = 6
    for i in range(0, len(names), per_line):
        chunk = names[i:i + per_line]
        if fit is not None:
            chunk = [f"{n}({fit.per_name.get(n, 0)}张)" for n in chunk]
        LOGGER.info("  %s", "  ".join(chunk))
    if fit is not None:
        LOGGER.info("  ⚠️ 括号里是「量过的 %d 张素材里有多少张不用缩字号就写得下」——0 表示只能缩字号",
                    fit.sources)

    out_path = default_out_path(args.out, guess, len(names), args.per_user)
    if args.dry_run:
        LOGGER.info("--dry-run：不写文件（本来会写到 %s）", out_path)
        return 0 if len(names) >= args.users else 3

    # ---- 写 JSON ----
    spec: dict = {"source": str(source)}
    if guess.key is not None:
        spec["platform"] = guess.key
    spec["default_count"] = args.per_user
    spec["users"] = names

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    LOGGER.info("名单已写到 %s（%.1fs）", out_path, time.perf_counter() - t0)
    LOGGER.info("下一步：python3 batch_produce.py %s", out_path.name)
    return 0 if len(names) >= args.users else 3


if __name__ == "__main__":
    sys.exit(main())
