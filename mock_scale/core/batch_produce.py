#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量生产 —— 按 JSON 名单给每个「生产用户」出 N 张**假图**。

**共用管线**（2026-10-27 合并后只有 `core/` 一份）：它是 **mock 用户线的入口**，
同时被扩展线 `expand/expand_produce.py` import。两条线共用这一个文件，
不存在「两边同步」的问题 —— 结构说明见 `docs/工程结构.md`。

    python3 core/batch_produce.py                          # 读运行配置
    python3 core/batch_produce.py mock/names/names.json    # 名单走位置参数
    python3 core/batch_produce.py --limit 3 --dry-run      # 命令行覆盖配置

**运行配置**：`core/batch_produce.config.json`（素材根 / 输出 / 平台 / 名单 / 字号 / 并发…），
优先级 **命令行 > JSON > 本文件的 `DEFAULTS`**，细节见 `runconfig.py`。位置参数 `spec`
（名单）与配置里的 `names` 是同一件东西，给哪个都行。

## 这个脚本干什么

名单（JSON）给的是**要生产的那些用户**——每个名字要出几张截图。素材是**真图**：
真实语料里那些已经过检测 / 定位 / 擦除 / 排版四段验证的截图。生产就是拿真图当底子，
**把框内原本的用户名换成名单上的名字**，框外一个像素不动（管线见 `docs/全流程说明.md`）。

管线本身一行没改，用的是 `replace_username.process_image` —— 也就是批量跑（`batch_run.py`）
每天在验的那一条。本脚本只做**选材与装配**：哪张真图配哪个名字、配几张、怎么配得
不像同一张片子重复出来的。

## 「尽量不重复」落成三条可量的规则

1. **同一张素材不重复用** —— 一张源图只出一个成品（素材不够时才会被复用，且复用次数
   在 `summary.json` 里如实报出来）。这是硬的那条：两张成品长得一样，只可能是它们
   出自同一张源图。
2. **同一个人的几张尽量来自不同原始用户** —— 同一个原始用户目录下的帧是同一台手机、
   同一个头像、同一段视频，肉眼一看就是「同一张片子的不同滚动位置」。挑素材时按
   「源目录已用过几次」加惩罚项，于是同一个名字的三张通常来自三个不同的人。
3. **成品按 md5 自检** —— 收尾时逐张算 md5，把重复数写进 `summary.json`
   （`reuse.duplicate_files`）。「不重复」这条不靠推理，有实测值。

## 选材不是随便抓一张：按「名字放不放得下」打分

**规格（2026-09-30 用户定）：名字超过原来的就截断、不够就用随机中文补满，字号不缩。**

* 排版取 **`fit=clip`** —— 名字超过原槽位就**截断**；**不缩字号**。为什么不缩：
  同一个名字配不同素材时会缩出不同字号，15 张摆在一起**大小不一**（实测 20~39px）；
* **补满槽位**（`fill_slot=True`），且**只补中文**（`fill_ascii_weight=0`）——
  不够长时随机补到原槽位宽，于是「总长不变」、右边的评论不动；
  名单是中文名，跟一串随机 ASCII 会很跳；
* 代价（明说）：**图上显示的名字可能与名单不完全一致**（截了几个字 / 后面多了几个补字）。
  这是规格内的，逐张记进 `metrics.truncated_chars` / `metrics.filled_chars`，
  并在 `summary.json` 的 ``text_fit`` 里汇总 —— 别让它静默。
* 选材仍按「名字的自然宽度 vs 素材槽位宽度」打分：**槽位够宽的优先**（名字不截断），
  实在不够才用窄的（截断）。

字号缩得太狠（`--fit width` 时才可能）的素材才**换一张**：见 `gate_reason`。

## 出的片长什么样

    <输出根>/<名字>-<实际张数>/1.jpg、2.jpg …      ← **名字与名单逐字一致**的（可以直接用）
    <输出根>/_名字有改动/<名字>-<张数>/1.jpg …      ← **被截断过或补过字**的（图上名字与名单
                                                    不完全一致 ⇒ 单独放，别混进去）
    <输出根>/manifest.json                         ← 逐张：名字 / 源图 / 度量值 / 落在哪个目录
    <输出根>/summary.json                          ← 汇总：名单 / 实际 / 复用 / 拒绝原因 / text_fit

⚠️ **两份都要交**：主目录不是「全部成品」，`text_fit.named_altered` 就是被分出去的张数。
加 `--no-split-altered` 可以退回「全混在一个目录」的旧形态。

目录名取**实际产出的张数**（不是名单里要的张数）—— 名字是 `昵称-张数` 编的，
写虚了就跟 `batch_run.collect_images` 对不上。空号不会有：产不出来的张数在
`summary.json` 的 `targets[].shortfall` 里，且有短张时**进程退出码 = 3**。

## 跳过与拒绝（都不算「生产失败」）

素材侧沿用语料的规格：**昵称只有 ASCII**（判自目录名，读图前）与**名字段里有 emoji**
（判自像素，定位后）的源图不出片 —— 这两条是 `batch_run` 一直在守的「这张图不该出片」。
另有渲染后的九条判据（越框 / 间距 / 尾块 / 漏改 / 擦不干净 / 名字被截断 / 溢出 /
emoji 兜底 / 字号太小），任一不过就**换一张素材重出**。全部落在 `summary.json` 的
`material.rejected_by_reason` 与 `rejected_after_render` 里，**按原因计数**。
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import logging
import os
import random
import sys
import time
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
from PIL import Image

import batch_run
import config
import draw_text
import inkmask
import platforms
import replace_username
import runconfig
import upstream_geom

LOGGER = logging.getLogger("batch_produce")


# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

DEFAULT_COUNT: int = 3
"""名单里没写张数时，每个名字出几张。"""

DEFAULT_SEED: int = 20260930
"""选材乱序的种子。**只影响「哪张素材配哪个名字」**，不影响单张渲染。

单张渲染的确定性由管线自己保证（补字种子由「源图名 + 新名字」派生，见
``replace_username._derive_seed``）—— 补字开了也照样逐张可复现。" 
"""

MATERIAL_FACTOR: float = 2.5
MATERIAL_MIN_EXTRA: int = 16
"""测量素材的批量：够 ``max(待产张数 × MATERIAL_FACTOR, 待产张数 + 上者)`` 张就开工。

要留富余，是因为**量过 ≠ 能出片**：渲染后还有七条判据会拒掉一部分（放不下、
字号太小…），拒一张就要换一张。量得刚好等量，第一轮就会有一半槽位空转。
"""

POOL_CHUNK: int = 48
"""一次并行测量多少张素材。检测 + 定位是这条链上最贵的一步，攒一批再并发。"""

ALTERED_DIR: str = "_名字有改动"
"""**名字被改动过**的成品放这个子文件夹（截断过、或补过字）。

图上显示的名字与名单**不完全一致** ⇒ 不能跟「名字原样」的混在一起（2026-09-30 用户要求）。
主目录只放名字与名单**逐字一致**的那些。两边都进 `summary.json` 与 `manifest.json`。
"""

MAX_ROUNDS: int = 3
"""最多几轮「产出 → 被拒 → 换素材重出」。三轮还出不来的槽位，多半是名单本身有问题。"""

FONT_SIZE_AUTO: str = "auto"
"""``--font-size`` 的默认值：**按量过的素材定一个语料级字号**（见 :func:`corpus_font_size`）。

为什么不是「每张各量各的」：字高是从**名字槽位**的墨迹行范围量的，槽位里除了文字还可能有
**比文字高的非文字元素**（emoji）。实测 ``00口谋財0口渡凡尘-4/3``：名字里 💕 高 35、3️⃣ 高 31，
而同一行的汉字只有 26~28、同行正文 27 ⇒ 字号算成 35，新名字比原文字大一圈；
同语料里还有反向的（字高量成 8、17 ⇒ 名字小得看不清）。
而**文字高度在同一个语料里是恒定的**（量 99/118 张：众数 28，占 56%/82%）——
取众数既躲开了那 3~4% 被非文字撑大的图，又让**同一个名字跨素材大小一致**。
"""

MIN_FONT_RATIO: float = 0.60
"""字号判据：成品字号 < 原名字号 × 这个值就换素材。

⚠️ **默认的 ``--fit clip`` 下这条判据不会触发**（字号固定 = 原字高，不缩）。
只有显式用 ``--fit width`` 时才需要它 —— 那种模式会一直缩到塞得下（下限
``config.FONT_SIZE_MIN`` = 10px），缩到 10px 的「名字」在截图上就是一条墨线。
0 = 关掉这条判据。
"""

JPEG_QUALITY: int = 95
"""默认存 jpg 的质量 —— 交付语料是真图，本来就是 jpg（600 KB 上下）。

⚠️ **jpg 是有损的**：整图重编码 ⇒ **框外的像素也会变**。实测 6 张（quality 95 /
subsampling 0）：框外最大像素差 **18**，同批 png 是 **0**（逐像素相等）。肉眼都看不出，
但「框外一个像素不变」那条承诺只有 `--format png` 给得起 —— 要复量就用 `metrics.box`。
"""

REF_FONT_SIZE: int = 40
"""给「名字有多宽」定序用的参考字号（长名字优先挑素材，见 `plan_round`）。"""

DIVERSITY_WEIGHT: float = 0.20
"""「源目录已用过几次」在选材打分里的权重 —— 这条就是「同一个人别都从同一个人那儿出」。"""

OVER_WEIGHT: float = 2.0
UNDER_WEIGHT: float = 0.6
"""名字宽 / 槽位宽 的偏离代价：**宽了比窄了更糟**。

`clip` 口径下：宽了（ratio > 1）⇒ 名字**被截断**（名单上的字丢了）；窄了 ⇒ 名字**被补满**
（后面多几个随机中文）。丢字比多字糟，所以 `OVER_WEIGHT`(=over) 比 `UNDER_WEIGHT`(=under) 重。
"""

NAME_KEYS: tuple[str, ...] = (
    "name", "nickname", "nick_name", "nickName", "user_name", "userName",
    "username", "昵称", "用户名", "名字",
)
COUNT_KEYS: tuple[str, ...] = ("count", "n", "num", "images", "张数", "数量")
WRAPPER_KEYS: tuple[str, ...] = (
    "users", "names", "targets", "list", "用户", "名单", "生产用户",
)
DEFAULT_COUNT_KEYS: tuple[str, ...] = ("default_count", "each", "每张", "默认张数")
SOURCE_KEYS: tuple[str, ...] = ("source", "素材", "语料", "root")
OUT_KEYS: tuple[str, ...] = ("out", "output", "输出")
PLATFORM_KEYS: tuple[str, ...] = ("platform", "平台")


# --------------------------------------------------------------------------
# 名单（JSON）
# --------------------------------------------------------------------------


@dataclass
class Target:
    """名单上的一个生产用户。"""

    name: str
    count: int


@dataclass
class Spec:
    """解析后的名单。"""

    targets: list[Target]
    source: Optional[Path] = None
    out: Optional[Path] = None
    platform: Optional[str] = None
    notes: list[str] = field(default_factory=list)
    """解析时发现的可疑之处（重复名字、字段认不出但能猜…）—— 给人看，不影响运行。"""


def _pick(mapping: dict, keys: Sequence[str]) -> Any:
    """按候选键名取值：先精确、再大小写不敏感（Java 那侧可能给 ``nickName``）。"""
    for key in keys:
        if mapping.get(key) is not None:
            return mapping[key]
    low = {str(k).lower(): v for k, v in mapping.items()}
    for key in keys:
        if low.get(key.lower()) is not None:
            return low[key.lower()]
    return None


def _as_count(value: Any, default: int, where: str) -> int:
    """把张数解析成正整数；认不出就报错（**不静默退回默认值**）。"""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"{where} 的张数不能是布尔值：{value!r}")
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where} 的张数不是整数：{value!r}") from exc
    if count <= 0:
        raise ValueError(f"{where} 的张数必须 ≥ 1，收到 {count}")
    return count


def _parse_entry(entry: Any, default_count: int, where: str, notes: list[str]) -> Target:
    """名单里的一项 → :class:`Target`。

    容忍三种写法（名单是手写的，字段名不该成为门槛）：

    * ``"张三"``                       —— 字符串，张数取默认；
    * ``{"name": "张三", "count": 3}`` —— 对象，键名认 ``NAME_KEYS`` / ``COUNT_KEYS``；
    * ``{"张三": 3}``                  —— 单键映射。
    """
    if isinstance(entry, str):
        name, raw_count = entry, None
    elif isinstance(entry, dict):
        name = _pick(entry, NAME_KEYS)
        if name is None:
            # 单键写成「名字: 张数」时走这条路。⚠️ 但键名本身要是**张数类字段名**
            # （``{"张数": 2}``）就不能这么读 —— 那是一个**漏了名字**的项，硬读下去
            # 会产出一个叫「张数」的用户（实测踩到），必须报错让它显形。
            count_keys = {k.lower() for k in COUNT_KEYS}
            single = list(entry.items())[0] if len(entry) == 1 else None
            if single is not None and str(single[0]).lower() not in count_keys:
                only_key, only_val = single
                if isinstance(only_val, dict):
                    # {"张三": {"count": 3}} —— 名字在外层、属性在内层
                    name = only_key
                    raw_count = _pick(only_val, COUNT_KEYS)
                else:
                    name, raw_count = only_key, only_val
            else:
                raise ValueError(
                    f"{where} 里认不出名字字段（可用：{'/'.join(NAME_KEYS[:4])} …），"
                    f"也不是「名字: 张数」的单键写法：{entry!r}"
                )
        else:
            raw_count = _pick(entry, COUNT_KEYS)
    else:
        raise ValueError(f"{where} 既不是字符串也不是对象：{entry!r}")

    name = str(name).strip()
    if not name:
        raise ValueError(f"{where} 的名字是空的：{entry!r}")
    if "/" in name or "\x00" in name:
        notes.append(f"名字「{name}」里有路径分隔符，已换成「_」（目录名不能带它）")
        name = name.replace("/", "_").replace("\x00", "_")
    return Target(name=name, count=_as_count(raw_count, default_count, where))


def _parse_entries(entries: Any, default_count: int, notes: list[str]) -> list[Target]:
    """把「一串名单」解析成 Target 列表；支持列表与 ``名字 → 张数`` 映射两种形态。"""
    if isinstance(entries, list):
        return [
            _parse_entry(e, default_count, f"名单第 {i} 项", notes)
            for i, e in enumerate(entries, start=1)
        ]
    if isinstance(entries, dict):
        out: list[Target] = []
        for key, val in entries.items():
            # ⚠️ 映射形态下**值就是张数**，得原样带进去。写成 ``entry = key`` 会把
            # 值丢掉、全体退回默认张数 —— 而默认值恰好等于映射里的数时还看不出错
            # （实测：``{"张三": 2}`` + default_count=2 时结果一模一样，纯属巧合）。
            entry: Any = {"name": key, **val} if isinstance(val, dict) else {"name": key, "count": val}
            out.append(_parse_entry(entry, default_count, f"名单项「{key}」", notes))
        return out
    raise ValueError(f"名单的主体既不是数组也不是对象：{type(entries).__name__}")


def load_spec(path: Path) -> Spec:
    """读名单 JSON。

    认两种顶层形态：

    * **对象**（推荐，能顺带写源目录 / 输出目录 / 平台）::

          {"platform": "douyin", "source": "../语料/…", "out": "out/",
           "default_count": 3,
           "users": [{"name": "张三", "count": 3}, "李四"]}

    * **裸数组 / 裸映射**：``["张三", "李四"]`` 或 ``{"张三": 3, "李四": 2}`` ——
      源目录与输出目录走命令行。

    Raises:
        OSError / json.JSONDecodeError: 文件读不了或不是 JSON。
        ValueError: 结构对不上（消息里带出错的那一项）。
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    notes: list[str] = []
    base = path.resolve().parent

    source = out = platform = None
    default_count = DEFAULT_COUNT

    if isinstance(raw, dict):
        default_count = _as_count(
            _pick(raw, DEFAULT_COUNT_KEYS), DEFAULT_COUNT, "名单的默认张数"
        )
        for keys, slot in ((SOURCE_KEYS, "source"), (OUT_KEYS, "out"), (PLATFORM_KEYS, "platform")):
            val = _pick(raw, keys)
            if val is None:
                continue
            if slot == "platform":
                platform = str(val)
            else:
                # 相对路径按**名单文件所在目录**解析 —— 名单和素材常常一起搬
                p = Path(str(val)).expanduser()
                if not p.is_absolute():
                    p = base / p
                if slot == "source":
                    source = p
                else:
                    out = p

        entries: Any = None
        for key in WRAPPER_KEYS:
            if key in raw and isinstance(raw[key], (list, dict)):
                entries = raw[key]
                break
        if entries is None:
            # 没有 wrapper ⇒ 整份就是「名字 → 张数」映射；空对象除外（那是配置壳）
            entries = {
                k: v for k, v in raw.items()
                if not any(k in keys for keys in (NAME_KEYS, COUNT_KEYS, DEFAULT_COUNT_KEYS,
                                                  SOURCE_KEYS, OUT_KEYS, PLATFORM_KEYS))
            }
            if not entries:
                raise ValueError(f"名单里没有用户：{path}（可用 key：{'/'.join(WRAPPER_KEYS)}）")
            notes.append("名单没有 users 包一层，按「名字 → 张数」映射解析")
    else:
        entries = raw

    targets = _parse_entries(entries, default_count, notes)
    if not targets:
        raise ValueError(f"名单是空的：{path}")

    merged: dict[str, Target] = {}
    for target in targets:
        if target.name in merged:
            merged[target.name].count += target.count
            notes.append(f"名字「{target.name}」出现多次，张数已合并为 {merged[target.name].count}")
        else:
            merged[target.name] = target

    for target in merged.values():
        clean, stripped = draw_text.strip_emoji(target.name)
        if not clean.strip():
            raise ValueError(
                f"名字「{target.name}」去掉 emoji 后是空的 —— 规格要求新写的文本不含表情，"
                "这个名字产不出片"
            )
        if stripped:
            notes.append(
                f"名字「{target.name}」含 {len(stripped)} 个 emoji，出片时会**被剔掉**"
                f"（规格：新文本不含表情）⇒ 图上显示的是「{clean}」"
            )

    return Spec(
        targets=list(merged.values()),
        source=source, out=out, platform=platform, notes=notes,
    )


# --------------------------------------------------------------------------
# 素材
# --------------------------------------------------------------------------


@dataclass
class Material:
    """一张候选素材。"""

    path: Path
    user_dir: str
    """素材分组名（原始用户目录）。同一个人的几张帧几乎一样，选材时要叉开。

    ⚠️ 目录名里的**昵称不在这里存一份**：它只在扫描时判一次 ``SKIP_PURE_EN``
    （判完就没用了，取名字也不是它的职责），存下来只会让人以为生产时会用到它。
    """


@dataclass
class Candidate:
    """量过一遍的素材：能不能出片、名字能占多宽。"""

    path: str
    user_dir: str
    ok: bool = True
    reason: str = ""
    box: Optional[tuple[int, int, int, int]] = None
    slot_width: int = 0
    glyph_height: int = 0
    natural_font_size: int = 0
    """原字高对应的字号（``draw_text.fit_font_size``）—— 名字的打分与字号判据都用它。"""
    tail_slack: int = 0
    """尾巴还能往右让出多少（= ``region.max_name_width - region.slot_width``）。

    **「名字正好放得下」的判据要用它**：``clip`` 口径下，名字的墨迹宽 ``w`` 必须落在
    ``[slot_width, slot_width + tail_slack]`` 里，才既**不被截断**、又**不会被补字** ——
    也就是「图上显示的名字与名单逐字一致」。量素材时随槽位一起量出来，选材时拿它筛。
    """

    geom: Optional[dict] = None
    """上游几何（``text_box`` / ``colon_box`` / ``badge_box`` / ``emojis``）—— 量到就带着走，
    出片时原样交给 ``process_image(geom=...)``（2026-10-28「行带改用上游框」）。"""

    multi_line: bool = False
    reuse: int = 0
    """已经被拿去出片几次（0 = 还没用过）。**已经用过的素材不再进候选池**。"""


def load_blacklist(platform_key: str) -> dict[str, str]:
    """读 :data:`config.BLACKLIST_PATH`，返回 ``{"<素材目录名>/<文件名>": 原因}``。

    用途只有一个：**这一张源图不该进池子**。当前收录的是「名字行本来就叠着两层字」
    的帧（暗鬼影 + 亮字，2026-10-12 实测 7 张）：冒号闸只能挑「缝最宽」的候选，
    而这类帧**没有一个干净位置**，成品上总留一截别人的字 —— 闸救不了，只能整张不用。
    鬼影闸的判据还没做出来（暗带占比分不开，见 bug-368），先用名单按住。

    键是「素材目录名/文件名」（**相对素材根**，不是绝对路径）：语料在 SMB / 移动硬盘上
    换过挂载点，绝对路径会失效。⚠️ **平铺目录**（``<根>/<n>.jpg``）的键是
    ``<根目录名>/<文件名>``，跟着根名走 —— 别给平铺目录写条目。

    **只按平台生效**：条目写了 ``platforms`` 就只在这些平台上拦，不写 = 所有平台。

    文件缺失 / 不是合法 JSON / 形状不对 ⇒ **报错**（不静默退化成空名单：那等于悄悄
    把坏帧放回池子）。这是继承 ``runconfig`` 的口径。

    Args:
        platform_key: 当前平台（``PlatformProfile.key``，例如 ``wechat_channels``）。

    Raises:
        FileNotFoundError: 名单文件不在。
        ValueError: 不是合法 JSON / 缺 ``path`` / 平台名不认识。
    """
    path = config.BLACKLIST_PATH
    if not path.is_file():
        raise FileNotFoundError(
            f'源图黑名单不在：{path}（空名单也要建一个，内容 {{"entries": []}}；'
            f"理由与格式见 expand/扩展张数.md §30）"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"源图黑名单不是合法 JSON：{path}（{exc}）") from exc
    entries = raw.get("entries") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise ValueError(f'源图黑名单的顶层要是对象、且带 "entries" 列表：{path}')

    known = set(platforms.PROFILES)
    out: dict[str, str] = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or not entry.get("path"):
            raise ValueError(f"源图黑名单第 {i} 条缺 path：{path}")
        plats = entry.get("platforms") or []
        if not isinstance(plats, list):
            raise ValueError(f"源图黑名单第 {i} 条的 platforms 要是列表：{path}")
        unknown = [p for p in plats if p not in known]
        if unknown:
            raise ValueError(
                f"源图黑名单第 {i} 条写了不认识的平台 {unknown}：{path}"
                f"（认得的只有 {sorted(known)}）"
            )
        if plats and platform_key not in plats:
            continue
        key = str(entry["path"]).strip().replace("\\", "/").strip("/")
        out[key] = str(entry.get("reason") or "（没写原因）")
    return out


def collect_material(root: Path, profile: platforms.PlatformProfile) -> tuple[list[Material], dict[str, int]]:
    """扫素材目录，返回 ``(素材列表, 计数)``。

    认两种形态：

    * ``<根>/<昵称-张数>/<n>.jpg`` —— 语料/交付那一套（`batch_run.collect_images` 同款）。
      昵称只有 ASCII 的目录**整目录跳过**（``SKIP_PURE_EN``，读图前就判，一个像素不读）；
    * ``<根>/<n>.jpg`` —— **平铺**目录（例如 ``data/`` 那 8 张）。没有昵称，
      ``SKIP_PURE_EN`` 这条判据不适用（认不出就没有理由跳）。

    **黑名单**（``source_blacklist.json``，见 :func:`load_blacklist`）也在这里拦：
    和 ``SKIP_PURE_EN`` 一样**读图前**就跳，一张坏图不该花掉一次解码 + 测量。

    Raises:
        NotADirectoryError: 根目录不存在或不是目录。
        ValueError: 一个素材都没有 / 黑名单文件不自洽。
    """
    if not root.is_dir():
        raise NotADirectoryError(f"素材根目录不存在或不是目录：{root}")

    blacklist = load_blacklist(profile.key)
    counts = {"scanned": 0, "skip_pure_en": 0, "flat": 0, "blacklist": 0}
    out: list[Material] = []
    subdirs = sorted(p for p in root.iterdir() if p.is_dir())

    def images_of(folder: Path) -> list[Path]:
        return sorted(
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in config.IMAGE_SUFFIXES
        )

    def keep(user_dir: str, img: Path) -> bool:
        """黑名单闸：命中就计数并丢掉（返回 ``False``）。"""
        hit = blacklist.get(f"{user_dir}/{img.name}")
        if not hit:
            return True
        counts["blacklist"] += 1
        LOGGER.info("黑名单：%s/%s 不进池 —— %s", user_dir, img.name, hit)
        return False

    if subdirs:
        for user_dir in subdirs:
            nickname, _ = batch_run.split_user_dir(user_dir.name)
            files = images_of(user_dir)
            if profile.skip_pure_en and batch_run.nickname_is_ascii(nickname):
                counts["skip_pure_en"] += len(files)
                LOGGER.debug("跳过素材目录 %s：昵称「%s」只有 ASCII", user_dir.name, nickname)
                continue
            for img in files:
                if keep(user_dir.name, img):
                    out.append(Material(img, user_dir.name))
    else:
        for img in images_of(root):
            if keep(root.name, img):
                out.append(Material(img, root.name))
                counts["flat"] += 1
        if counts["flat"]:
            LOGGER.info(
                "素材是**平铺**目录：没有昵称可判 ⇒ SKIP_PURE_EN 这条判据不适用（%d 张全进池）",
                counts["flat"],
            )

    counts["scanned"] = len(out) + counts["skip_pure_en"] + counts["blacklist"]
    if not out:
        raise ValueError(
            f"素材目录 {root} 里没有可用图片"
            f"（跳过 {counts['skip_pure_en']} 张、黑名单 {counts['blacklist']} 张）"
        )
    return out, counts


def spread_order(materials: list[Material], rng: random.Random) -> list[Material]:
    """把素材排成「**轮流从不同原始用户目录取**」的顺序。

    直接按目录顺序取的话，同一个名字的 3 张会落在同一个人的 3 张帧上 —— 同头像、
    同背景、只差滚动位置，一眼就是同一张片子。这里先把目录打乱，再轮着每个目录取一张，
    于是相邻的候选天然来自不同的人。
    """
    by_dir: dict[str, list[Material]] = {}
    for material in materials:
        by_dir.setdefault(material.user_dir, []).append(material)

    dirs = sorted(by_dir)
    rng.shuffle(dirs)
    for d in dirs:
        rng.shuffle(by_dir[d])

    queues = {d: deque(by_dir[d]) for d in dirs}
    live = deque(dirs)
    out: list[Material] = []
    while live:
        d = live.popleft()
        out.append(queues[d].popleft())
        if queues[d]:
            live.append(d)
    return out


# --------------------------------------------------------------------------
# 阶段 1 · 测量（检测 + 定位，不擦不画）
# --------------------------------------------------------------------------


def measure_one(item: tuple, profile_key: str) -> Candidate:
    """量一张素材：有没有框、框里名字能占多宽、是不是该跳过。

    **只走到「定位」为止** —— 擦除与排版是每个「名字 × 素材」组合都要重做的一步，
    放在选材之前做等于白做（选不中的素材擦它干嘛）。这里量出来的几何量是
    **与名字无关**的，量一次全组合可用，所以只量一遍。

    Args:
        item: ``(路径, 素材目录名)`` —— 进程池传参要显式，不能靠闭包。
        profile_key: 平台 profile（定位策略）。

    Returns:
        :class:`Candidate`。**不抛异常** —— 一张坏素材不该带塌整批。
    """
    raw_path, user_dir, geom = item if len(item) == 3 else (*item, None)
    path = Path(raw_path)
    cand = Candidate(path=raw_path, user_dir=user_dir, geom=geom)

    box, _n_boxes, flags = batch_run.detect_box(path)
    if box is None:
        cand.ok, cand.reason = False, (flags[0] if flags else "NO_BOX")
        return cand

    profile = platforms.profile_for(profile_key)
    try:
        img = np.array(Image.open(path).convert("RGB"))
        region = inkmask.find_erase_region(img, box, profile=profile, geom=geom)
    except (inkmask.RegionNotFoundError, ValueError):
        cand.ok, cand.reason = False, "LOC_NONE"
        return cand
    except Exception as exc:  # noqa: BLE001 - 别的异常要**显形**，不能混进 LOC_NONE
        LOGGER.error("量素材崩了 %s：%r", path.name, exc)
        cand.ok, cand.reason = False, f"CRASH:{type(exc).__name__}"
        return cand

    if region.has_emoji:
        # 与 batch_run 同一把尺子：名字段里有 emoji ⇒ 这张图不该出片（规格）
        cand.ok, cand.reason = False, "SKIP_EMOJI"
        return cand
    if region.slot_width <= 0:
        cand.ok, cand.reason = False, "SLOT_ZERO"
        return cand

    cand.box = box
    cand.slot_width = region.slot_width
    cand.glyph_height = region.glyph_height
    cand.natural_font_size = draw_text.fit_font_size(region.glyph_height).size
    cand.tail_slack = max(0, region.max_name_width - region.slot_width)
    cand.multi_line = region.multi_line
    return cand


def _load_geom_sidecar(path, source) -> dict:
    """读上游几何 sidecar → ``{素材绝对路径: geom}``；读不了就空表（安静退回旧逻辑）。"""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 读不到就是没给，别把整批带塌
        LOGGER.warning("geom sidecar 读不了（%s）：%s —— 按旧的像素启发式跑", path, exc)
        return {}
    items = d.get("items") if isinstance(d, dict) else None
    if not isinstance(items, dict):
        LOGGER.warning("geom sidecar 结构不对（没有 items）—— 按旧的像素启发式跑")
        return {}
    root = Path(source).resolve() if source else None
    out: dict[str, dict] = {}
    for rel, g in items.items():
        q = Path(rel)
        full = q.resolve() if q.is_absolute() else ((root / q).resolve() if root else q)
        out[str(full)] = g
    LOGGER.info("geom sidecar：%d 条（%s）", len(out), path)
    return out


def _auto_geom_map(source_root, materials, profile) -> dict:
    """平台要求上游几何（``profile.needs_geom``）时，从交付树自动重建 ``geom``。

    快手 App 的交付框是**多行融合框**（一个 ROI 常装 2~6 行），而目标行不一定是第一行
    ⇒ 只有 ``comments.jsonl`` 的 ``colon_box`` 能把它定准（750 张实测：无几何时定位
    0~73%，有几何时 750/750，且冒号与 ``colon_box`` 逐张对位）。

    返回 ``{素材绝对路径: geom}``，与 ``measure_pool`` 的查表口径一致。重建失败只警告、
    不中断（退回像素启发式 —— 会大量 LOC_NONE，但账单里看得见）。
    """
    try:
        gs = upstream_geom.load(Path(source_root))
    except upstream_geom.GeomError as exc:
        LOGGER.warning(
            "平台 %s 需要上游几何，但自动重建失败：%s —— 退回像素启发式（会大量 LOC_NONE）",
            profile.key, exc,
        )
        return {}
    root = Path(source_root).resolve()
    out: dict[str, dict] = {}
    for m in materials:
        try:
            rel = Path(m.path).resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        g = gs.items.get(rel)
        if g:
            out[os.path.abspath(str(m.path))] = g
    LOGGER.info(
        "上游几何（自动重建）：%s；素材命中 %d/%d",
        gs.describe(), len(out), len(materials),
    )
    return out


def measure_pool(
    items: Sequence[Material], profile_key: str, workers: int,
    geom_map: Optional[dict] = None,
) -> tuple[list[Candidate], dict[str, int]]:
    """并行测量一批素材，返回 ``(可用候选, 按原因计数)``。

    ⚠️ 返回顺序 = **入参顺序**，不是完成顺序。这点不是洁癖：候选池的顺序会一路传到
    ``plan_round`` 的打分（并列时的那点种子抖动按遍历顺序取值），完成顺序一变，
    「同一份名单 + 同一份素材」就会挑出**不同的配对** —— 确定性没了，两次跑没法对比。
    """
    gm = geom_map or {}
    tasks = [(str(m.path), m.user_dir, gm.get(os.path.abspath(str(m.path)))) for m in items]
    slots: list[Optional[Candidate]] = [None] * len(tasks)
    rejected: dict[str, int] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(measure_one, task, profile_key): i
            for i, task in enumerate(tasks)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                slots[index] = future.result()
            except Exception as exc:  # noqa: BLE001
                key = f"CRASH:{type(exc).__name__}"
                rejected[key] = rejected.get(key, 0) + 1
                LOGGER.error("测量子任务异常：%s", exc)

    candidates: list[Candidate] = []
    for cand in slots:
        if cand is None:
            continue
        if cand.ok:
            candidates.append(cand)
        else:
            key = cand.reason.split(":")[0]
            rejected[key] = rejected.get(key, 0) + 1
    return candidates, rejected


# --------------------------------------------------------------------------
# 阶段 2 · 选材（名字 × 素材 的打分）
# --------------------------------------------------------------------------


@dataclass
class Slot:
    """一个待产出的位置：某人的第几张。"""

    name: str
    index: int


@functools.lru_cache(maxsize=8192)
def _name_width(name: str, font_size: int) -> int:
    """名字在给定字号下的墨迹宽度。缓存 —— 打分时每个「名字 × 素材」都要问一次。"""
    return draw_text.measure_ink(name, draw_text.load_font(font_size))[0]


def fits_exactly(cand: Candidate, name: str, font_size: int = 0) -> bool:
    """这张素材配这个名字，能不能做到**名字逐字原样**（既不截断、也不补字）。

    ``clip`` 口径下，名字墨迹宽 ``w`` 落在 ``[slot_width, slot_width + tail_slack]`` 里才算：
    窄了 ``fill_to_slot`` 会补随机字、宽了会被截断 —— 两种都会让**图上显示的名字与名单不一致**，
    而那种成品**不计入张数**（2026-09-30 用户规格）。所以选材阶段就得筛掉，
    不能等渲染完了再回头看（那等于先浪费一次擦除+排版）。

    Args:
        cand:      量过的素材。
        name:      名单上的名字。
        font_size: **实际会用的字号**（``--font-size`` 钉死时用它，0 = 用素材自己的自然字号）。
                   ⚠️ 必须与实际渲染一致：钉死字号后仍按素材自身的自然字号算宽度，
                   会把「到渲染时才被截断」的当成放得下（选材按错宽度做取舍）。

    Returns:
        能不能做到逐字原样。
    """
    size = font_size or cand.natural_font_size
    if not cand.ok or size <= 0:
        return False
    w = _name_width(name, size)
    return cand.slot_width <= w <= cand.slot_width + cand.tail_slack


def corpus_font_size(candidates: Sequence[Candidate]) -> int:
    """量过的素材 → **语料的文字高度**（众数），也就是要钉死的字号。

    用众数而不是平均值/中位数：字高被 emoji 撑大的那几张是**少数**（实测 3~4%），
    而文字本身的字高几乎全落在同一个值上（视频号 28 占 56/99、抖音 28 占 82/118）
    ⇒ 众数就是「文字的高度」，且**不被少数污染样本拉动**。并列时取较大的那个
    （宁可略大也不要因为个别图把整批字缩小）。

    Returns:
        字号；给不出（一张都没量到）时返回 0 = 退回「每张各量各的」。
    """
    counts = Counter(c.glyph_height for c in candidates if c.glyph_height > 0)
    if not counts:
        return 0
    height = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
    return draw_text.fit_font_size(height).size


def score(cand: Candidate, slot: Slot, dir_use: dict[str, int], rng: random.Random,
          font_size: int = 0) -> float:
    """「这张素材配这个名字」的代价，**越小越好**。

    三部分：

    * **宽度偏离** —— 名字的自然宽度与素材槽位宽度的差。宽了（名字**被截断**、名单上的字丢）
      比窄了（名字**被补满**、后面多几个随机中文）更糟，所以两边的权重不同；
    * **来源多样性** —— 这个素材的原始用户目录已经被用过几次；
    * **一点点种子随机** —— 只用来打散并列最优，让「同样好的一堆素材」不总是挑同一个。
    """
    # 量宽度要用**实际会用的字号**：钉死时不能用素材自己的自然字号，否则选材按错误的
    # 宽度做取舍（该判「截断」的当成放得下，到渲染才截）。
    width = _name_width(slot.name, font_size or cand.natural_font_size)
    slot_width = max(1, cand.slot_width)
    ratio = width / slot_width
    over = max(0.0, ratio - 1.0)
    under = max(0.0, 1.0 - ratio)
    return (
        OVER_WEIGHT * over
        + UNDER_WEIGHT * under
        + DIVERSITY_WEIGHT * dir_use.get(cand.user_dir, 0)
        + 0.05 * rng.random()
    )


def plan_round(
    slots: Sequence[Slot],
    pool: list[Candidate],
    blocked: dict[str, set[str]],
    dir_use: dict[str, int],
    rng: random.Random,
    font_size: int = 0,
) -> list[tuple[Slot, Candidate]]:
    """给这一轮的槽位各挑一张素材。

    两条顺序上的讲究：

    * **长名字先挑**（按名字宽度降序）—— 长名字能塞进去的素材最少，先让它挑走了
      合适的，短名字剩下的随便哪张都行；反过来先给短名字挑，长名字就会没得挑。
    * **同一轮里一张素材只用一次**，且挑完立刻把它的目录计数 +1 —— 于是同一个人的
      几张会落到不同人的素材上（这是「不重复」的第 2 条规则落地的地方）。

    素材不够时放宽到「复用最少的那张」，一个槽位都不空着；能挑出几张就返回几张。
    """
    remaining = list(pool)
    ordered = sorted(
        slots, key=lambda s: (-_name_width(s.name, REF_FONT_SIZE), s.name, s.index)
    )
    picks: list[tuple[Slot, Candidate]] = []
    for slot in ordered:
        burnt = blocked.get(slot.name, set())
        # **只挑「名字正好放得下」的**（`fits_exactly`）：放不下就不出片 ——
        # 「名字逐字原样」比「凑够张数」重要（用户 2026-09-30 规格：截断/补字的不计入张数）。
        cands = [
            c for c in remaining
            if c.reuse == 0 and c.path not in burnt and fits_exactly(c, slot.name, font_size)
        ]
        if not cands:  # 没用过的挑完了：退一步，允许复用（复用次数如实上报）
            cands = [
                c for c in remaining
                if c.path not in burnt and fits_exactly(c, slot.name, font_size)
            ]
        if not cands:
            continue  # 这一轮没有放得下的素材 ⇒ 留到下一轮（多量一批素材再看）
        best = min(cands, key=lambda c: score(c, slot, dir_use, rng, font_size))
        remaining.remove(best)
        dir_use[best.user_dir] = dir_use.get(best.user_dir, 0) + 1
        picks.append((slot, best))
    return picks


# --------------------------------------------------------------------------
# 阶段 3 · 出片
# --------------------------------------------------------------------------


@dataclass
class Job:
    """一次出片（进程池的参数，要能 pickle）。"""

    name: str
    index: int
    source: str
    source_user: str
    stage_name: str
    """暂存目录名（名字做了文件名安全化，见 :func:`safe_stage_name`）。"""

    box: tuple[int, int, int, int]
    """标注框 —— **从测量阶段带过来**，不在这一趟里重检。

    检测是这条链上最贵的一步，而它对同一张图是确定性的：量过的框就是这一趟要用的框。
    重检一遍等于把最贵的那步白跑第二次（`batch_run` 那边三个探针共用一次检测，是同一个道理）。
    """

    geom: Optional[dict] = None
    """上游几何，与 ``box`` 一起从测量阶段带过来（2026-10-28「行带改用上游框」）。"""


@dataclass
class Produced:
    """一张成品的结局。"""

    name: str
    index: int
    source: str
    source_user: str
    ok: bool
    reason: str = ""
    staged: str = ""
    metrics: dict = field(default_factory=dict)


@dataclass
class _Args:
    """进程池要的参数（dataclass 才 pickle 得动）。"""

    profile_key: str
    fit: str
    ext: str
    quality: int
    stage_dir: str
    min_font_ratio: float = MIN_FONT_RATIO
    font_size: int = 0
    """**钉死**字号（0 = 按每张素材各量各的，现状）。见 :data:`FONT_SIZE_AUTO`。"""
    erase_badge: bool = True
    """等级标（✦14 那种药丸）连名字一起擦掉 —— mock 线的规格，默认开。
    只有 ``--no-erase-badge`` / JSON 里 ``"erase_badge": false`` 才关。"""


def safe_stage_name(name: str) -> str:
    """名字 → 暂存目录名：替换掉路径分隔符，再缀 6 位 md5 防撞。

    ⚠️ 光做字符替换不够：``a/b`` 与 ``a_b`` 会撞进同一个暂存目录，两个名字的片
    互相覆盖。md5 那 6 位保证不同名字不同目录（最终目录名不受影响，用原名）。
    """
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
    return f"{name.replace('/', '_').replace(chr(0), '_')}_{digest}"


def gate_reason(outcome, min_font_ratio: float = MIN_FONT_RATIO, font_size: int = 0) -> str:
    """成品过不过关 —— 不过就返回原因（空串 = 过关）。

    九条，量的都是**结果图**上的量（前八条与 `batch_run` 的判据同一套定义，
    这样「生产出来的片」与「批量验过的片」是同一个合格标准）：

    ==============  ====================================================
    ``SKIP_EMOJI``  原图名字段里有 emoji（测量阶段就该拦掉，这里是兜底）
    ``TRUNCATED``   名字被砍了字 —— **假图上显示的名字跟名单对不上，必拒**
    ``OVERFLOW``    名字太宽，整条装配溢出（缩到最小字号还塞不下）
    ``BOX_BAD``     装配越过标注框
    ``GAP_BAD``     名字↔冒号的距离变了
    ``TAIL_BAD``    尾块不是原像素（重画了 / 被压住了）
    ``LEAK``        蒙版外有像素被改动
    ``DIRTY``       残余墨迹 > 5%
    ``FONT_SMALL``  字号缩到原字号 ``min_font_ratio`` 以下（名字小得不像话）
    ==============  ====================================================
    """
    if outcome.has_emoji:
        return "SKIP_EMOJI"
    # ⚠️ 截断（`TRUNCATED`）**不再拒片**：按 2026-09-30 的规格，名字超过原槽位就**截断**、
    # 不够就**用随机中文补满**（`fill_slot=True`）—— 所以「图上显示的名字与名单不完全一致」
    # 是**预期的**。截了几个字、补了几个字逐张记进 `metrics`，并在 `summary.json` 的
    # ``text_fit`` 里汇总，别让它静默。
    if outcome.overflow_px > 0:
        return "OVERFLOW"
    if not outcome.box_ok:
        return "BOX_BAD"
    if outcome.gap_now != outcome.join_gap:
        return "GAP_BAD"
    if outcome.tail_diff_px != 0:
        return "TAIL_BAD"
    if outcome.outside_changed > 0:
        return "LEAK"
    if outcome.ink_after > max(8, int(outcome.ink_before * 0.05)):
        return "DIRTY"
    if min_font_ratio > 0 and outcome.glyph_height > 0:
        # 基准取「本该用的字号」：钉死时就取钉死值（此时恒等、这条判据等于不生效 ——
        # 规格就是「字号不缩」），否则取本图字高对应的自然字号。
        natural = font_size or draw_text.fit_font_size(outcome.glyph_height).size
        if natural > 0 and outcome.font_size < natural * min_font_ratio:
            return "FONT_SMALL"
    return ""


def render_one(job: Job, args: _Args) -> Produced:
    """出一张片：定位 → 擦除 → 排版 → 存到**暂存目录**。

    ⚠️ 不在这里重跑检测 —— 框由测量阶段量好、随 :class:`Job` 带过来（见 ``Job.box``）。

    为什么不直接存到最终目录：交付形态是 ``<名字>-<实际张数>/``，而**实际张数要等
    全部出完才知道**（有人可能短一张）。所以先按「名字/序号」暂存，收尾时由主进程
    整批搬进最终目录并改名 —— 也顺手在那儿算 md5 做重复自检。

    Returns:
        :class:`Produced`。被拒时 ``staged`` 为空（**不留半成品**）。
    """
    path = Path(job.source)
    profile = platforms.profile_for(args.profile_key)
    box = job.box

    outcome = replace_username.process_image(
        path,
        job.name,
        bbox=box,
        # 规格（2026-10-11 用户）：mock 用户的图上不许留真用户的东西 —— 等级标
        # （✦14 那种药丸）连名字一起擦掉，新名字从药丸左沿起写。见 config.BADGE_SHADOW_PX。
        erase_badge=args.erase_badge,
        fit=args.fit,
        # 规格（2026-09-30 用户定）：名字**超过原槽位就截断、不够就用随机中文补满**，
        # 字号不缩 —— 于是同一个名字在不同素材上的字号一致（缩字号是「大小不一定」的来源）。
        # 补字只用中文（`fill_ascii_weight=0`）：名单是中文名，后面跟一串随机 ASCII 会很跳。
        fill_slot=True,
        fill_ascii_weight=0.0,
        font_size=args.font_size or None,
        save_output=False,
        save_compare=False,
        save_mask=False,
        keep_result=True,
        profile=profile,
        geom=job.geom,
    )
    # 标注框落进清单是为了让「只改了框内」**可复量** —— 拿成品与源图按这个框逐像素
    # 比一遍就是了，不必信流水线自己的 ``outside_changed``（bug-166 的教训：
    # 「蒙版外 0 改动」≠「框外 0 改动」，参照物不同）。
    metrics = {
        "box": list(box),
        "slot_width": outcome.slot_width,
        "text_width": outcome.text_width,
        "max_name_width": outcome.max_name_width,
        "font_size": outcome.font_size,
        "glyph_height": outcome.glyph_height,
        "truncated_chars": outcome.truncated_chars,
        "filled_chars": outcome.filled_chars,
        "stripped_chars": outcome.stripped_chars,
        "gap_now": outcome.gap_now,
        "join_gap": outcome.join_gap,
        "tail_diff_px": outcome.tail_diff_px,
        "tail_leftover_px": outcome.tail_leftover_px,
        "outside_changed": outcome.outside_changed,
        "ink_before": outcome.ink_before,
        "ink_after": outcome.ink_after,
        # 擦等级标那一路的两个几何量：量到的胶囊头、以及名字实际起写的列
        # （规格「离胶囊头的距离固定」= 这两者的差，别拿成品图上的墨迹左沿反推）。
        "capsule_head": outcome.capsule_head,
        "name_left": outcome.name_left,
        "multi_line": outcome.multi_line,
        "elapsed_s": round(outcome.elapsed_s, 3),
    }

    if not outcome.ok or outcome.result_image is None:
        return Produced(job.name, job.index, job.source, job.source_user, ok=False,
                        reason="LOC_NONE", metrics=metrics)

    reason = gate_reason(outcome, args.min_font_ratio, args.font_size)
    if reason:
        return Produced(job.name, job.index, job.source, job.source_user, ok=False,
                        reason=reason, metrics=metrics)

    stage = Path(args.stage_dir) / job.stage_name / f"{job.index}{args.ext}"
    stage.parent.mkdir(parents=True, exist_ok=True)
    image = Image.fromarray(outcome.result_image)
    if args.ext == ".jpg":
        image.save(stage, quality=args.quality, subsampling=0)
    else:
        image.save(stage)
    return Produced(job.name, job.index, job.source, job.source_user, ok=True,
                    staged=str(stage), metrics=metrics)


def render_round(picks: Sequence[tuple[Slot, Candidate]], args: _Args,
                 workers: int) -> list[tuple[Slot, Candidate, Produced]]:
    """并行出一轮片，返回 ``[(槽位, 素材, 结果), ...]``。

    ⚠️ 顺序 = **入参顺序**（同 :func:`measure_pool`）：落盘顺序、目录撞名后缀、
    `manifest.json` 的行序都从它来，用完成顺序的话同一份名单两次跑会得到不一样的清单。
    """
    jobs = [
        (Job(name=slot.name, index=slot.index, source=cand.path,
             source_user=cand.user_dir, stage_name=safe_stage_name(slot.name),
             box=cand.box, geom=cand.geom), slot, cand)
        for slot, cand in picks
    ]
    results: list[Optional[tuple[Slot, Candidate, Produced]]] = [None] * len(jobs)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(render_one, job, args): i
            for i, (job, _slot, _cand) in enumerate(jobs)
        }
        for future in as_completed(futures):
            i = futures[future]
            slot, cand = jobs[i][1], jobs[i][2]
            try:
                produced = future.result()
            except Exception as exc:  # noqa: BLE001 - 一张崩了不该带塌整批，但要显形
                LOGGER.error("出片子任务异常（%s/%d）：%s", slot.name, slot.index, exc)
                produced = Produced(slot.name, slot.index, cand.path, cand.user_dir,
                                    ok=False, reason=f"CRASH:{type(exc).__name__}")
            results[i] = (slot, cand, produced)
    return [r for r in results if r is not None]


# --------------------------------------------------------------------------
# 收尾：搬到交付形态 + 报告
# --------------------------------------------------------------------------


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_dir(root: Path, name: str, count: int) -> Path:
    """给名字找个还没被占用的交付目录 ``<名字>-<张数>``。

    撞名（同一个名字跑了两批、落到同一个输出根）时把后缀加在**张数左边**、
    不改张数那一段：``split_user_dir`` 是从右边切第一个 ``-`` 的，
    ``张三-3_2`` 会被读成昵称 ``张三-3`` —— 那一批就废了，``张三_2-3`` 才对。
    """
    base = root / f"{name}-{count}"
    if not base.exists():
        return base
    for i in range(2, 1000):
        cand = root / f"{name}_{i}-{count}"
        if not cand.exists():
            return cand
    raise RuntimeError(f"目录名 {name}-{count} 撞了 1000 次，放弃")


def _label(cand: Candidate) -> str:
    """素材的**可读标识** —— ``原始用户目录/文件名``。

    ⚠️ 不能只打文件名：语料里每个用户目录下都是 ``1.jpg``/``2.jpg``/``3.jpg``，
    只打这句的话，「一张素材被复用了 3 次」和「从 3 个人那儿各取了 1 张」在日志里
    **长得一模一样**（bug-145 同款）。要判重必须带上目录。
    """
    return f"{cand.user_dir}/{Path(cand.path).name}"


def name_exact(metrics: dict[str, Any]) -> bool:
    """这张成品上显示的名字**是否与名单逐字一致**（没被截断、也没补字）。"""
    return not metrics.get("truncated_chars") and not metrics.get("filled_chars")


def write_outputs(
    produced: list[Produced], out_root: Path, ext: str, *, split_altered: bool = True
) -> tuple[list[dict], dict[str, Any]]:
    """把暂存片搬进 ``<输出根>/<名字>-<实际张数>/``，返回 ``(逐张清单, 汇总)``。

    ⚠️ **名字被改动过的单独放一个文件夹**（`ALTERED_DIR`）：截断过 / 补过字的那些，
    图上显示的名字与名单不完全一致，不能跟「名字原样」的混在一起。
    主目录只剩逐字一致的那些 —— 于是「能直接用的片」与「要复核的片」一眼分得开。

    搬的同时算 md5：**「不重复」这条判据在成品上量**，不靠「源图不同所以成品不同」的推理。
    """
    by_name: dict[str, list[Produced]] = {}
    for item in produced:
        by_name.setdefault(item.name, []).append(item)

    rows: list[dict] = []
    reuse = {"distinct_sources": 0, "duplicate_files": 0, "max_source_reuse": 0}
    sources: dict[str, int] = {}
    digests: dict[str, list[str]] = {}

    for name, items in by_name.items():
        items.sort(key=lambda p: p.index)
        if split_altered:
            groups = [
                (out_root, [p for p in items if name_exact(p.metrics)], False),
                (out_root / ALTERED_DIR, [p for p in items if not name_exact(p.metrics)], True),
            ]
        else:
            groups = [(out_root, items, False)]
        for root, group, altered in groups:
            if not group:
                continue
            target = _unique_dir(root, name, len(group))
            target.mkdir(parents=True, exist_ok=True)
            for seq, item in enumerate(group, start=1):
                src = Path(item.staged)
                if not src.exists():
                    # 不该发生（出片成功就一定有暂存文件）；宁可少一张也不整批崩在收尾
                    LOGGER.error("暂存文件不见了，跳过：%s", src)
                    continue
                dest = target / f"{seq}{ext}"
                src.replace(dest)
                digest = _md5(dest)
                digests.setdefault(digest, []).append(str(dest))
                sources[item.source] = sources.get(item.source, 0) + 1
                rows.append({
                    "name": name,
                    "file": str(dest.relative_to(out_root)),
                    "folder": ALTERED_DIR if altered else ".",
                    "named_exact": not altered,
                    "seq": seq,
                    "planned_index": item.index,
                    "source": item.source,
                    "source_user": item.source_user,
                    "md5": digest,
                    "metrics": item.metrics,
                })

    reuse["distinct_sources"] = len(sources)
    reuse["max_source_reuse"] = max(sources.values()) if sources else 0
    dupes = {d: paths for d, paths in digests.items() if len(paths) > 1}
    reuse["duplicate_files"] = sum(len(p) - 1 for p in dupes.values())
    if dupes:
        LOGGER.warning(
            "有 %d 组成品**逐字节相同**（同一张源图被复用）：%s",
            len(dupes), [p for p in list(dupes.values())[:3]],
        )
    return rows, reuse


def build_summary(
    *, spec: Spec, spec_path: Path, targets: list[Target], out_root: Path,
    material_root: Path, material_counts: dict[str, int], measured: int,
    rejected_material: dict[str, int], rejected_render: dict[str, int],
    produced_counts: dict[str, int], rows: list[dict], reuse: dict, args,
    elapsed: float, dry_run: bool, font_size: int = 0,
) -> dict:
    """汇总（落 ``summary.json``）。

    ⚠️ 逐人张数取 ``produced_counts``（由调用方数），**不能从 ``rows`` 数** ——
    ``rows`` 是落盘之后才有的，dry-run 下它恒为空，于是「一个用户都没产够」
    会出现在一次完全正常的预演里（实测踩到）。
    """
    return {
        "run": {
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "elapsed_s": round(elapsed, 1),
            "out_dir": str(out_root),
            "dry_run": dry_run,
        },
        "spec": {
            "path": str(spec_path),
            "users_requested": len(targets),
            "images_requested": sum(t.count for t in targets),
            "notes": spec.notes,
        },
        "config": {
            "platform": args.profile_key,
            "fit": args.fit,
            "format": args.ext.lstrip("."),
            "fill_slot": True,
            "split_altered": bool(args.split_altered),
            "font_size": font_size or None,
            "font_size_arg": args.font_size,
            "jpeg_quality": args.quality if args.ext == ".jpg" else None,
            "min_font_ratio": args.min_font_ratio,
            "seed": args.seed,
        },
        "material": {
            "root": str(material_root),
            "scanned": material_counts.get("scanned", 0),
            "skip_pure_en": material_counts.get("skip_pure_en", 0),
            "blacklist": material_counts.get("blacklist", 0),
            "flat_dir": bool(material_counts.get("flat", 0)),
            "measured": measured,
            "rejected_by_reason": dict(sorted(rejected_material.items())),
        },
        "targets": [
            {
                "name": t.name,
                "requested": t.count,
                "produced": produced_counts.get(t.name, 0),
                "shortfall": max(0, t.count - produced_counts.get(t.name, 0)),
                "fallback": sum(1 for r in rows if r["name"] == t.name and not r["named_exact"]),
            }
            for t in targets
        ],
        "reuse": {
            **reuse,
            "images_produced": sum(produced_counts.values()),
            "users_produced": sum(1 for n in produced_counts.values() if n),
        },
        "rejected_after_render": dict(sorted(rejected_render.items())),
        "tail_move": {
            "leftover_images": sum(1 for r in rows
                                   if r["metrics"].get("tail_leftover_px", 0) > 0),
            "leftover_px_total": sum(max(0, r["metrics"].get("tail_leftover_px", 0))
                                     for r in rows),
            "note": "搬正文后**原地**剩下的墨迹像素（逐张在 images[].metrics.tail_leftover_px）。"
                    "0 = 冒号后面那截尾巴整块搬走了。**只报不拒**："
                    "这是「搬全了没有」，不是「规格内成不成立」——"
                    "钉死色/越框那些才是拒片判据。见 expand/扩展张数.md §25.1"
                    "（2026-10-11 用户「移动正文，没有完全移动」）。",
        },
        "text_fit": {
            "truncated": sum(1 for r in rows if r["metrics"].get("truncated_chars", 0) > 0),
            "filled": sum(1 for r in rows if r["metrics"].get("filled_chars", 0) > 0),
            "named_exact": sum(1 for r in rows if r["named_exact"]),
            "named_altered": sum(1 for r in rows if not r["named_exact"]),
            "altered_dir": ALTERED_DIR,
            "note": "**名字原样**（截 0 字、补 0 字）的才计入「有效张数」= targets[].produced；"
                    "截断过或补过字的属于**尽力补位**：落在 `" + ALTERED_DIR + "/` 子目录、"
                    "与主目录分开，且**不计入**张数（targets[].fallback 是它们的张数）。"
                    "截断/补字本身是规格内行为（名字超过槽位就截、不够就用中文补满），不是废片；"
                    "逐张的字数在 images[].metrics 里",
        },
        "images": rows,
    }


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="batch_produce",
        description="批量生产：按 JSON 名单给每个「生产用户」出 N 张假图（真图擦名字 + 写新名）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例：\n"
            "  python3 core/batch_produce.py                        # 读运行配置\n"
            "  python3 core/batch_produce.py mock/names/names.json  # 名单走位置参数\n"
            "  python3 core/batch_produce.py --limit 3 --dry-run    # 只看选材，不出片\n"
            "  python3 core/batch_produce.py --source <语料根> --out out/ --format png\n"
        ),
    )
    p.add_argument(
        "spec", type=Path, nargs="?", default=None,
        help="名单 JSON（含要生产的用户名与每人张数）。**不给就读运行配置里的 names**",
    )
    runconfig.add_argument(p)
    p.add_argument(
        "--source", type=Path, default=None,
        help="素材根目录（``<昵称-张数>/<n>.jpg`` 或平铺目录）。默认取名单里的 source",
    )
    p.add_argument("--out", type=Path, default=None, help="输出根目录，默认 outputs/produce_<时间戳>")
    p.add_argument(
        "--platform", default=None,
        help="平台 profile（auto = 从素材路径判定，默认）。抖音走「按颜色认名字」，"
             "视频号/微博走「按冒号定位」，见 platforms.py",
    )
    p.add_argument(
        "--fit", default=None, choices=("clip", "width", "height"),
        help="名字放不下时怎么办。**默认 clip（砍字）** —— 按规格：名字超过原来就截断、"
             "不够就用随机中文补满，**字号不缩**（缩字号会让同一个名字在不同素材上大小不一，"
             "而那正是要避免的观感）。要不要缩字号用 --fit width",
    )
    p.add_argument(
        "--format", default=None, choices=("jpg", "png"),
        help="成品格式，默认 jpg（与交付语料一致）。⚠️ jpg 有损 ⇒ **框外像素也会变**"
             "（实测最大差 18）；要「框外逐像素不变」用 png（实测 0）",
    )
    p.add_argument(
        "--no-split-altered", dest="split_altered", action="store_false", default=None,
        help=f"**默认把「名字被改动过」的成品单独放进 `{ALTERED_DIR}/`**（截断过或补过字的，"
             "图上显示的名字与名单不完全一致）；加这个开关就全部混在主目录里",
    )
    p.add_argument(
        "--no-erase-badge", dest="erase_badge", action="store_false", default=None,
        help="**不擦等级标**（✦14 / ◆N 那种药丸）。默认**擦** —— 规格：mock 用户的图上"
             "不许留真用户的东西，等级标连着名字一起换成假名（新名字从药丸左沿起写，"
             "原来那一段列区间整个变成名字的地盘）。见 mock/README.md 的「等级标」一节",
    )
    p.add_argument("--quality", type=int, default=None, help=f"jpg 质量，默认 {JPEG_QUALITY}")
    p.add_argument(
        "--font-size", default=None,
        help="字号：**auto（默认）= 按量过的素材取「语料文字高度」钉死一个字号**"
             "（同一个名字跨素材大小一致，且不会去参考某张图里 emoji 的高度，"
             "这类图实测占 3~4%%、会把字号撑大 25%%~40%%）；给整数 = 直接钉死；"
             "off = 每张素材各量各的（旧行为）",
    )
    p.add_argument(
        "--min-font-ratio", type=float, default=None,
        help=f"字号判据：成品字号 < 原名字号 × 这个值就换素材，0 = 关掉（默认 {MIN_FONT_RATIO}）",
    )
    p.add_argument("--seed", type=int, default=None, help="选材乱序的种子（只影响谁配谁）")
    p.add_argument("--limit", type=int, default=None, help="只跑名单里的前 N 个用户，0 = 全跑")
    p.add_argument("--workers", type=int, default=None, help="并行进程数，0 = min(8, CPU 核数)")
    p.add_argument(
        "--geom", type=Path, default=None,
        help="上游几何 sidecar（geom.json）：给了就按 text_box/colon_box 定位，"
             "并在行带内取样，缺省走旧的像素启发式",
    )
    p.add_argument("--dry-run", action="store_true", default=None, help="只选材并打印配对，不出片、不落图")
    p.add_argument("--quiet", action="store_true", default=None, help="不打每张图的日志")
    p.add_argument("--verbose", action="store_true", default=None, help="打调试日志")
    return p


# --------------------------------------------------------------------------
# 运行配置：能被 `<脚本名>.config.json` 控制的那些键（见 runconfig.py）
#
# ⚠️ 值同时是「命令行与 JSON 都没给」时的默认值 ⇒ **上面每个 add_argument 都必须
# `default=None`**，否则 JSON 会被静默忽略（runconfig 模块 docstring 里写了为什么）。
# --------------------------------------------------------------------------
DEFAULTS: dict = {
    "names": None,          # 名单 JSON 的路径（等价于位置参数 spec）
    "source": None, "out": None,
    "platform": "auto",
    "fit": "clip",
    "format": "jpg",
    "split_altered": True,
    "erase_badge": True,
    "quality": JPEG_QUALITY,
    "font_size": FONT_SIZE_AUTO,
    "min_font_ratio": MIN_FONT_RATIO,
    "seed": DEFAULT_SEED,
    "limit": 0,
    "workers": 0,
    "geom": None,
    "dry_run": False,
    "quiet": False,
    "verbose": False,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from_cli, from_json, cfg_path = runconfig.load(
            args, DEFAULTS, script_file=__file__, loose=("font_size",))
    except runconfig.ConfigError as exc:
        print(f"运行配置有问题：{exc}", file=sys.stderr)
        return 2
    args.source = runconfig.path(args.source)
    args.out = runconfig.path(args.out)
    if args.spec is None:
        args.spec = runconfig.path(args.names)

    level = logging.DEBUG if args.verbose else (logging.WARNING if args.quiet else logging.INFO)
    logging.basicConfig(level=level, format=config.LOG_FORMAT, datefmt=config.LOG_DATEFMT)
    if not args.verbose:
        # 管线内部每张图都会打日志，批量的进度由本脚本自己汇总，别刷两遍
        quiet_level = logging.ERROR if args.quiet else logging.WARNING
        for name in ("replace_username", "inkmask", "draw_text"):
            logging.getLogger(name).setLevel(quiet_level)

    LOGGER.info(runconfig.describe(cfg_path, from_cli, from_json))
    if args.spec is None:
        LOGGER.error("没给名单：位置参数 `<名单>.json` 或运行配置里的 `names`")
        return 2

    t0 = time.perf_counter()

    try:
        spec = load_spec(args.spec)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.error("名单读不了：%s", exc)
        return 2

    targets = spec.targets[: args.limit] if args.limit > 0 else spec.targets

    source_root = args.source or spec.source
    if source_root is None:
        LOGGER.error("没给素材目录：命令行 --source 或名单里的 source")
        return 2
    out_root = args.out or spec.out or (
        config.DEFAULT_OUTPUT_DIR / f"produce_{time.strftime('%Y%m%d_%H%M%S')}"
    )

    # 平台：名单 > 命令行 > 从素材路径判（不猜）
    platform_key = None
    if spec.platform and args.platform == "auto":
        platform_key = platforms.resolve_key(spec.platform)
        if platform_key is None:
            LOGGER.warning("名单里的平台 %r 认不出，改从素材路径判", spec.platform)
    if platform_key is None:
        platform_key = (
            platforms.detect_platform_from_path(source_root)
            if args.platform == "auto" else platforms.resolve_key(args.platform)
        ) or (args.platform if args.platform != "auto" else "generic")
    profile = platforms.profile_for(platform_key)
    args.profile_key = profile.key

    try:
        # 字体先探一下：找不到 CJK 字体时，画画那步才报错就太晚了（整批白跑）
        font_path = draw_text.resolve_font_path()
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("写名字用的字体找不到（%s）—— sudo apt install fonts-noto-cjk", exc)
        return 2

    try:
        material, counts = collect_material(source_root, profile)
    except (NotADirectoryError, ValueError) as exc:
        LOGGER.error("%s", exc)
        return 2

    if spec.notes:
        for note in spec.notes:
            LOGGER.warning("名单：%s", note)

    workers = args.workers or max(1, min(8, os.cpu_count() or 1))
    ext = ".jpg" if args.format == "jpg" else ".png"
    # --font-size：auto / off / 整数
    if isinstance(args.font_size, str) and args.font_size.strip().lower() in ("off", "none", "0"):
        args.font_size = 0
    elif isinstance(args.font_size, str) and args.font_size.strip().lower() != FONT_SIZE_AUTO:
        try:
            args.font_size = int(args.font_size)
        except ValueError:
            LOGGER.error("--font-size 只认 %s / off / 整数，收到 %r", FONT_SIZE_AUTO, args.font_size)
            return 2
    # 存回 Namespace：报告与进程池参数都从它取（``args`` 里只有 ``format``，
    # 没有后缀 ``ext`` —— batch_run 踩过同款：运行时对、报告错）
    args.ext = ext
    order = spread_order(material, random.Random(args.seed))
    demand = sum(t.count for t in targets)

    LOGGER.info("=" * 78)
    LOGGER.info(
        "生产 %d 个用户 / %d 张 · 素材 %s（扫到 %d 张，纯英文跳过 %d 张，黑名单 %d 张）"
        " · 平台 %s（%s）· fit=%s · 补满槽位=%s · 改动过的分开=%s · %s 进程",
        len(targets), demand, source_root, counts["scanned"], counts["skip_pure_en"],
        counts["blacklist"], profile.key, profile.name_strategy, args.fit,
        "开（只补中文）", "是" if args.split_altered else "否", workers,
    )
    LOGGER.info("字体 %s · 输出 %s%s", font_path, out_root, "（dry-run，不落图）" if args.dry_run else "")

    stage_dir = out_root / ".staging"

    def make_args(font_size: int = 0) -> _Args:
        """出片参数。字号是第一轮量完素材才定下来的，所以每轮现造一份。"""
        return _Args(
            profile_key=profile.key, fit=args.fit, ext=ext, quality=args.quality,
            stage_dir=str(stage_dir), min_font_ratio=args.min_font_ratio,
            font_size=font_size, erase_badge=args.erase_badge,
        )

    # 上游几何 sidecar（``--geom``）：键是素材的绝对路径；没给就空表，行为与以前一致
    geom_map = _load_geom_sidecar(args.geom, source_root) if getattr(args, "geom", None) else {}
    # 平台档案要求上游几何（快手 App 的融合框）⇒ 没显式给就自动重建
    if not geom_map and profile.needs_geom:
        geom_map = _auto_geom_map(source_root, material, profile)

    # ---- 主循环：量素材 → 选材 → 出片 → 被拒的槽位下一轮换素材 ----
    pending: list[Slot] = [
        Slot(name=t.name, index=i) for t in targets for i in range(1, t.count + 1)
    ]
    pool: list[Candidate] = []
    blocked: dict[str, set[str]] = {}
    dir_use: dict[str, int] = {}
    rejected_material: dict[str, int] = {}
    rejected_render: dict[str, int] = {}
    produced: list[Produced] = []
    fallbacks: list[Produced] = []
    """**名字与名单不一致**（截断/补字）的成品 —— 会被写进 `_名字有改动/`，但**不计入张数**。"""
    cursor = 0
    rng = random.Random(args.seed + 1)  # +1：与「素材乱序」那支随机流分开，互不扰动
    measured = 0
    pinned_font = 0
    """要**钉死**的字号（0 = 不钉，每张素材各量各的）。

    默认 ``--font-size auto``：等第一批素材量完，取**语料的文字高度**（字高众数）钉住 ——
    这样同一个名字跨素材**大小一致**，而且不会去参考某张图里 emoji 的高度（见 FONT_SIZE_AUTO）。
    """

    for round_no in range(1, MAX_ROUNDS + 1):
        if not pending:
            break
        def _free_fitting() -> int:
            """待产槽位里，「**没用过、且放得下**」的候选**最少**的那个还有几张可用。

            ⚠️ 只看「没用过」不够 —— 数量够但都不放得下，等于没有（那正是这一轮要解决的问题）。
            所以量素材的「够了没」要看**放得下**的张数。
            """
            free = [c for c in pool if c.reuse == 0]
            return min((sum(1 for c in free if fits_exactly(c, s.name, pinned_font)) for s in pending), default=0)

        need = max(len(pending) * MATERIAL_FACTOR, len(pending) + MATERIAL_MIN_EXTRA)
        while _free_fitting() < 2 * len(pending) and cursor < len(order):
            chunk = order[cursor: cursor + POOL_CHUNK]
            cursor += len(chunk)
            got, bad = measure_pool(chunk, profile.key, workers, geom_map)
            measured += len(chunk)
            pool.extend(got)
            for key, n in bad.items():
                rejected_material[key] = rejected_material.get(key, 0) + n
            LOGGER.info(
                "  量素材 %d/%d：本轮可用 %d 张%s",
                measured, len(order), len([c for c in pool if c.reuse == 0]),
                f"（拒 {' '.join(f'{k}×{v}' for k, v in sorted(bad.items()))}）" if bad else "",
            )
            if _free_fitting() >= min(2 * len(pending), len(order)):
                break

        if isinstance(args.font_size, int) and args.font_size > 0:
            pinned_font = args.font_size          # 命令行直接钉死
        elif args.font_size == FONT_SIZE_AUTO and pinned_font == 0 and pool:
            pinned_font = corpus_font_size(pool)
            if pinned_font:
                # ⚠️ 这里**不能**叫 `counts`：那会把上面 collect_material 的素材计数冲掉，
                # 于是 summary.material 里的 scanned / skip_pure_en / blacklist 全成 0
                # （每张图都对、只有报数错，2026-10-13 修，见 bug-369）
                height_hist = Counter(c.glyph_height for c in pool)
                LOGGER.info(
                    "字号钉死为 %d（语料文字高度：字高众数 %dpx，量了 %d 张；"
                    "被非文字元素撑大的少数张不再影响字号）",
                    pinned_font, max(height_hist.items(), key=lambda kv: (kv[1], kv[0]))[0], len(pool),
                )
        picks = plan_round(pending, pool, blocked, dir_use, rng, pinned_font)
        if not picks:
            LOGGER.error("素材用尽：还剩 %d 个槽位产不出来", len(pending))
            break

        LOGGER.info("第 %d 轮：出片 %d 张（待产 %d 张）", round_no, len(picks), len(pending))
        if args.dry_run:
            for slot, cand in sorted(picks, key=lambda p: (p[0].name, p[0].index)):
                LOGGER.info("  %s #%d ← %s", slot.name, slot.index, _label(cand))
            produced.extend(
                Produced(slot.name, slot.index, cand.path, cand.user_dir, ok=True)
                for slot, cand in picks
            )
            break

        results = render_round(picks, make_args(pinned_font), workers)
        # ⚠️ 待产里「**这一轮没被选中**」的槽位不能丢：它们只是这轮没找到放得下的素材，
        #    下一轮要继续找（量更多素材再看），最后实在凑不够才走「尽力补位」。
        picked = [slot for slot, _ in picks]
        pending = [s for s in pending if s not in picked]
        for slot, cand, result in results:
            cand.reuse += 1
            if result.ok and name_exact(result.metrics):
                produced.append(result)
                # 同一个名字**不再复用同一张源图**：源图 + 名字都相同 ⇒ 成品逐字节相同
                # （2026-10-28 实测 `小风` #1-#3 三张一模一样，重复自检报 2 张）。
                # 挡在这里，下一轮换别的素材；实在换不到就走「尽力补位」——
                # 补出来的名字有改动（进 `_名字有改动/`），但片子不再重复。
                blocked.setdefault(slot.name, set()).add(cand.path)
                LOGGER.info(
                    "  ✓ %s #%d ← %s（字号 %d，槽位 %d→名字 %d，%.2fs）",
                    slot.name, slot.index, _label(cand),
                    result.metrics.get("font_size", 0), result.metrics.get("slot_width", 0),
                    result.metrics.get("text_width", 0), result.metrics.get("elapsed_s", 0.0),
                )
            elif result.ok:
                # 渲染成功，但**名字与名单不一致**（被截断 / 被补字）：
                # 收进「有改动」那一堆**留档**，但**不计入张数**，槽位回待产换素材重出。
                fallbacks.append(result)
                rejected_render["NAME_ALTERED"] = rejected_render.get("NAME_ALTERED", 0) + 1
                blocked.setdefault(slot.name, set()).add(cand.path)
                pending.append(slot)
                LOGGER.info(
                    "  ▲ %s #%d ← %s：名字有改动（截 %d 字 / 补 %d 字）⇒ **不计张数**，换素材重出",
                    slot.name, slot.index, _label(cand),
                    result.metrics.get("truncated_chars", 0), result.metrics.get("filled_chars", 0),
                )
            else:
                rejected_render[result.reason] = rejected_render.get(result.reason, 0) + 1
                blocked.setdefault(slot.name, set()).add(cand.path)
                pending.append(slot)
                LOGGER.info(
                    "  ✗ %s #%d ← %s：%s（换一张素材重出）",
                    slot.name, slot.index, _label(cand), result.reason,
                )
        if pending:
            LOGGER.info("  被拒 %d 张，进入下一轮换素材", len(pending))

    # ---- 尽力补位：还没凑够的槽位，用「放不下」的素材也出一张，进「有改动」那一堆 ----
    #   规格（2026-09-30）：**总数要出满**，但「名字被截断/补字」的那些**不算每个名字的数量**，
    #   单独放 `_名字有改动/`。所以这里不再挑「放得下」，只保证不跟已用过的重复。
    if pending and not args.dry_run:
        refill: list[tuple[Slot, Candidate]] = []
        for slot in sorted(pending, key=lambda s: (s.name, s.index)):
            burnt = blocked.get(slot.name, set())
            free = [c for c in pool if c.reuse == 0 and c.path not in burnt]
            if not free:
                free = [c for c in pool if c.path not in burnt]
            if not free:
                continue
            best = min(free, key=lambda c: score(c, slot, dir_use, rng, pinned_font))
            best.reuse += 1
            dir_use[best.user_dir] = dir_use.get(best.user_dir, 0) + 1
            refill.append((slot, best))
        if refill:
            LOGGER.info(
                "尽力补位：%d 张（放不下的素材也用；截过/补过字的进 %s/、不计张数，"
                "字一个没改的仍计入张数）", len(refill), ALTERED_DIR,
            )
            for slot, cand, result in render_round(refill, make_args(pinned_font), workers):
                if result.ok:
                    fallbacks.append(result)
                    m = result.metrics
                    _exact = not m.get("truncated_chars") and not m.get("filled_chars")
                    LOGGER.info(
                        "  ▲ %s #%d ← %s：截 %d 字 / 补 %d 字（%s）",
                        slot.name, slot.index, _label(cand),
                        m.get("truncated_chars", 0), m.get("filled_chars", 0),
                        "名字原样，计入张数" if _exact else "不计入张数",
                    )
                else:
                    rejected_render[result.reason] = rejected_render.get(result.reason, 0) + 1

    # ---- 落盘 ----
    rows: list[dict] = []
    reuse = {"distinct_sources": 0, "duplicate_files": 0, "max_source_reuse": 0}
    if produced and not args.dry_run:
        rows, reuse = write_outputs(produced + fallbacks, out_root, ext, split_altered=args.split_altered)
        if stage_dir.exists():
            # 暂存目录里此时应只剩空目录；留着只会让人以为还有没搬完的片
            for leftover in sorted(stage_dir.rglob("*"), reverse=True):
                leftover.rmdir() if leftover.is_dir() else leftover.unlink()
            stage_dir.rmdir()
    elif produced and args.dry_run:
        reuse["distinct_sources"] = len({p.source for p in produced})

    # 逐人「产出」张数：**以落盘结果为准**（非 dry-run）。补位里有一批「本以为要补字、
    # 实际一个字也没补上」的（槽位只比名字宽 1~2px，fill_to_slot 找不到能正好补上的字）——
    # write_outputs 按 named_exact 把它们归进**主目录**，张数就必须算上；否则 summary
    # 说「大猫 0/3」、盘上却明明有 `大猫-2/`（2026-10-28 实测）。dry-run 没有 rows，
    # 仍按选材结果数。
    produced_counts: dict[str, int] = {}
    if rows and not args.dry_run:
        for r in rows:
            if r["named_exact"]:
                produced_counts[r["name"]] = produced_counts.get(r["name"], 0) + 1
    else:
        for item in produced:
            produced_counts[item.name] = produced_counts.get(item.name, 0) + 1

    elapsed = time.perf_counter() - t0
    summary = build_summary(
        spec=spec, spec_path=args.spec, targets=targets, out_root=out_root,
        material_root=source_root, material_counts=counts, measured=measured,
        rejected_material=rejected_material, rejected_render=rejected_render,
        produced_counts=produced_counts, rows=rows, reuse=reuse, args=args,
        elapsed=elapsed, dry_run=args.dry_run, font_size=pinned_font,
    )

    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not args.dry_run:
        (out_root / "manifest.json").write_text(
            json.dumps({"images": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---- 报数 ----
    n_produced = sum(produced_counts.values())
    n_short = sum(t["shortfall"] for t in summary["targets"])
    LOGGER.info("=" * 78)
    LOGGER.info(
        "总耗时 %.0fs · 产出 %d 张 / 请求 %d 张 · %d 个用户%s",
        elapsed, n_produced, demand, len(summary["targets"]),
        "（dry-run：只配对，没出片）" if args.dry_run else "",
    )
    LOGGER.info(
        "  素材：量了 %d 张（拒 %s）· 用的源图 %d 张互不相同 · 单张最多复用 %d 次",
        measured,
        " ".join(f"{k}×{v}" for k, v in sorted(rejected_material.items())) or "无",
        reuse["distinct_sources"], reuse["max_source_reuse"],
    )
    _tm = summary.get("tail_move") or {}
    if _tm.get("leftover_images"):
        LOGGER.warning(
            "  尾巴没搬全：%d 张原地留墨（合计 %dpx）—— 看 manifest 的 tail_leftover_px",
            _tm["leftover_images"], _tm["leftover_px_total"],
        )
    if rejected_render:
        LOGGER.info(
            "  出片后被拒（已换素材重出）：%s",
            " ".join(f"{k}×{v}" for k, v in sorted(rejected_render.items())),
        )
    if not args.dry_run:
        LOGGER.info(
            "  重复自检：逐张 md5 比对，**重复 %d 张**（0 = 没有两张是同一张片子）",
            reuse["duplicate_files"],
        )
    short = [t for t in summary["targets"] if t["shortfall"]]
    if short:
        LOGGER.error(
            "  **%d 个用户没产够**（素材不够或名字实在放不下）：%s",
            len(short), " ".join(f"{t['name']} {t['produced']}/{t['requested']}" for t in short[:8]),
        )
    LOGGER.info(
        "报告 → %s（summary.json%s）",
        out_root, " / manifest.json" if not args.dry_run else "",
    )
    return 3 if n_short else 0


if __name__ == "__main__":
    sys.exit(main())
