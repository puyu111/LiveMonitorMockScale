#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量跑真实语料 —— 把 8 张上的结论放到 650 张上看还成不成立。

**共用管线**（2026-10-27 合并后只有 `core/` 一份）：它是 **mock 用户线的验收外壳**，
同时被扩展线 `expand/expand_produce.py` import（扩展线的七道闸复用这里的判据）。
两条线共用这一个文件，不存在「两边同步」的问题 —— 结构说明见 `docs/工程结构.md`。

语料路径等「这次怎么跑」写在同目录的 ``batch_run.config.json`` 里，命令行只用来
逐项覆盖（见 ``runconfig.py``）。从工程根敲::

    python3 core/batch_run.py /home/puyu/work/data/抖音奇瑞汽车/all_users_正面_3张以上
    python3 core/batch_run.py                  # 全部参数读 batch_run.config.json

## 为什么要批量

test2 原来的结论建立在 **8 张图**上，而这 8 张全部来自同一个用户目录
（``爱国者-9``）—— 是同一版式的 8 次重复，不是 8 个独立样本。语料里还有
112 个用户、650 张同平台的图，既有 emoji 昵称、也有长短不一的框。
**批量不是为了「跑得更多」，是为了让结论能承受这一句「你的样本是同一张图」。**

## 判据（每条都在**结果图**上量，不靠推理）

按「检测 → 定位 → 擦除 → 排版」四段分开数，才知道该改哪个文件：

    检测   NO_BOX      没找到任何彩色框
           MULTI_BOX   找到多个（记一笔，仍用**面积最大**的那个继续跑）
    定位   LOC_NONE    inkmask 在框里找不到「用户名 + 冒号」的结构
    擦除   DIRTY       残余墨迹 > 5%（理论上不该出现 —— 插值不会造字）
           LEAK        蒙版外有像素被改动（**构造保证**，出现即 bug）
    排版   GAP_BAD     名字↔冒号的距离 ≠ 原图的 join_gap
           TAIL_BAD    尾块不是原像素（重画了 / 被压住了）
           TAIL_RESID  尾巴没搬全：原地还留着墨（`tail_clean` 未过，见 §25.1）
           BOX_BAD     整条装配越过了标注框

`DIRTY` 与 `LEAK` 是**不该出现**的两类：本地确定性插值物理上不可能造字，
而「只贴蒙版内」让蒙版外零改动成为构造保证。它们出现就是代码 bug，
不像 work/test 那边的同名判据那样属于「模型随机性」。

## 「跳过」—— 不是失败，**不计入任何数量**

    检测/定位/擦除/排版 之外还有两种结果，含义是「**这张图不该出片**」，
    不是「这张图出错了」：

    SKIP_PURE_EN  昵称串**只有 ASCII**（判自目录名）—— 读图**之前**就跳过
    SKIP_EMOJI    原图用户名段里**有 emoji**（判自像素）—— 定位之后跳过，
                  不擦不画、不落结果图

规格原话（2026-09-30）：「**含有表情也跳过，跳过的不记入数量，跳过的要记绝对路径**」。

⚠️ **跳过的不计入任何数量**：不进 `failures`、不进 `failure_counts`、不进 `by_probe`
的分母，**也不进报告里「处理了几张」那个数** —— `summary.json` 的 `run.images` 是
**处理过的张数**（`images_scanned` 才是扫到的总数），报告末尾那几行也按这个口径打。
跳过数只在自己的块里出现（`skipped`），不参与任何比率。

⚠️ **跳过的图要留绝对路径**：`rows.csv` 的 `path` 列（每一行都有，不只有跳过的）
给的是 `Path.resolve()` 的绝对路径；另落一份 `skipped.txt`，一行一个绝对路径
（带跳过理由），方便直接拿去删/审。`--dump` 任何档都不给跳过图留文件
（连失败占位条都不留 —— 那是给「跑了但失败」用的）。

## 三档长度探针

同一张图跑三次，名字长度依次是 短 / 等长 / 超长：

============================  ==========================================
探针                           测什么
============================  ==========================================
``short`` （默认 ``A``）       名字比槽位**窄** ⇒ 随机补满槽位，尾巴不动、总长不变
``equal`` （目录名里的昵称）    长度相当 ⇒ 尾巴基本不动，最接近原图的基线
``long``  （默认 ``SuperUser``）名字变长 ⇒ 吃掉余量、余量不够就**截断**
============================  ==========================================

> ``short`` 的语义在 **2026-09-29** 按规格改过：旧行为是「名字变短 ⇒ 尾巴左移、
> 右边腾出的位置填背景色」。现在名字会被**随机补满**到原槽位宽（补的是常用中文 /
> ASCII 字母数字，不含 emoji），所以「腾出来的空隙」这一现象不再出现。想复现旧
> 行为加 ``--no-fill``。

⚠️ ``equal`` 探针用的昵称取自**目录名**，而目录名把昵称里的 emoji 转写成了
ASCII（``A淘气⇒`` 其实是 ``🦄淘气💨``）。所以它**不是**「逐像素还原原图」的
基线 —— 想验那一条得用 OCR 拿到真字符串，而本实验刻意没有 OCR 依赖。
它能验的是「长度相当时的排版」这一支。

## 输出

跑完在 ``--out`` 目录（默认 ``outputs/batch_<时间戳>``）落两份**报告**：

* ``rows.csv`` —— 每张图 × 每个探针一行，可直接丢进表格工具筛；
* ``summary.json`` —— 按探针分组的汇总 + 失败分类计数 + 锚点自检结果。

**默认不存结果图**：650 张 × 3 个探针 ≈ 1950 张 PNG，是几个 GB 的纯浪费。
要看图就按需要挑一档 ``--dump``：

==============  ===================================================================
``--dump``      落什么
==============  ===================================================================
``none``        不落图（默认）
``failures``    只落**有失败判据**的那几张：完整图 ``<序号>_<探针>_replaced.png``
                ＋ 裁剪对照 ``<序号>_<探针>_compare.png``（上原图 / 下结果）
``all``         每张图落一张 ``<序号>_probes.png`` —— **原图 + 三档探针摞成一张**，
                四行，左侧标名字（**框外的界面被裁掉了**）
``full``        每档**两张**：整图 ``<序号>_full_<探针>.png`` ＋ 裁剪条
                ``<序号>_<探针>.png``；另落一张原图裁剪条 ``<序号>_orig.png``
==============  ===================================================================

``full`` 现在的形态是 **2026-09-29 按用户反馈重做的**，推翻的是上一版的
「四行摞图嵌回原位置」：

* **替换只允许发生在绿框内**。上一版贴回去的是一块「绿框 + 留白」那么大的面板，
  于是它压住了上下的邻居评论、还有左侧的等级徽章 —— 那是**贴图**造成的遮挡，
  不是管线改的，但看图上分不出来。现在整图就是**管线自己的结果图**：框内是这一档
  的新名字，**框外逐像素等于原图**（本地路线的构造保证，再由 ``rows.csv`` 的
  ``outside_changed`` 逐行量出来）。
* **一档一张，不再摞成一张**。摞图的初衷是「一张图看四档」，但代价是那张图的
  评论区根本不是任何一张真实截图的样子。

``all`` 与 ``full`` 都给人看名字那一段，区别在**看什么**：``all`` 把四档摞成一张、
只留名字那一小段，比长度影响最省事；``full`` 每档一整张截图，看的是「换完名字跟
右边的按钮/头像挤不挤、整条评论有没有失衡」—— 那些恰恰在框外。

     python core/batch_run.py <根目录> --limit 20 --dump all    # 只看名字那一段
     python core/batch_run.py <根目录> --limit 20 --dump full   # 每档一整图 + 一条裁剪条

⚠️ 检测不到框的图**没有裁剪窗**，``full`` 下不落图（那种图看 ``rows.csv`` 的 ``NO_BOX``）。

⚠️ 探针**没出图**（定位失败等）时**不落整图，但要落裁剪条** —— 拿原图那一条加红框
写下失败标记。少了这一条，「这一档没跑」和「跑了但失败」在目录里长得一模一样
（bug-156 的教训：静默零结果和真成功在报告上区分不开）。

⚠️ ``full`` 落的是整张尺寸的 PNG，实测 ~2.9 MB/张，**一档一张** ⇒ 一张原图 ≈ 8.7 MB
（三档）：33 张 ≈ 290 MB，650 张全量 ≈ 5.7 GB。要么配 ``--limit`` 跑，要么先跑
``--dump all`` 挑出要看的那几张。
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import box_detect
import platforms
import config
import replace_username
import runconfig
import upstream_geom

LOGGER = logging.getLogger("batch_run")

PROBES: tuple[str, ...] = ("short", "equal", "long")
"""三档长度探针，见模块 docstring。"""

ANCHOR_DIR: str = "爱国者-9"
"""锚点用户目录 —— test2 原来那 8 张图就出自这里（md5 逐个对过）。

批量跑完会单独看这一组：它们已经有单张跑出来的定论（7 项判据全零），
所以只要批量在同一批图上给出**不一样**的结论，就是**批量外壳**写错了，
不是管线坏了。这是这个脚本唯一的自检手段，别省。
"""


# --------------------------------------------------------------------------
# 一行结果
# --------------------------------------------------------------------------


@dataclass
class Row:
    """一张图 × 一个探针的结果。"""

    user: str
    file: str
    probe: str
    new_name: str
    path: str = ""
    """这张图的**绝对路径**（``Path.resolve()``）—— 每一行都有。

    跳过的行尤其要它：跳过的图不落任何文件、不进任何统计，只在这一列（和
    ``skipped.txt``）里留得下「到底是哪一张」。
    """
    boxes: int = 0
    box: Optional[tuple[int, int, int, int]] = None
    failures: list[str] = field(default_factory=list)
    slot_width: int = 0
    text_width: int = 0
    max_name_width: int = 0
    join_gap: int = 0
    gap_now: int = -1
    tail_shift_px: int = 0
    tail_diff_px: int = -1
    tail_leftover_px: int = -1
    """搬完尾巴后**原地**还剩的墨迹像素（``-1`` = 没量到）。

    ⚠️ 与 :attr:`tail_diff_px` **不是**一条：那条比「目的地那块像不像原图」，
    这条数「原地留下了多少墨」。搬全之前原地留墨的那 222 张里，残墨全是这条看得见、
    那条看不见的（见 :attr:`tail_clean` 与 expand/扩展张数.md §25.1）。
    """
    ink_before: int = 0
    ink_after: int = 0
    outside_changed: int = 0
    truncated_chars: int = 0
    stripped_chars: int = 0
    filled_chars: int = 0
    glyph_height: int = 0
    font_size: int = 0
    multi_line: bool = False
    """检测框里是否装了**不止一行**文字（定位已按行带切开，只动名字那一行）。"""
    elapsed_s: float = 0.0
    error: str = ""
    skip: str = ""
    """跳过这张图的理由，空 = 没跳过。取值 ``SKIP_PURE_EN`` / ``SKIP_EMOJI``。

    ⚠️ **跳过不是失败，也不计入任何数量**：它不进 ``failures``、不进 ``by_probe``
    的分母，也不进 ``run.images`` —— 这条图的正确结局就是「不出片」
    （见模块 docstring「跳过」）。**但它的绝对路径要留下**（``Row.path``）。
    """
    skip_note: str = ""
    """跳过的证据（给人看的）：ASCII 那条给昵称，emoji 那条给段的实测值。"""

    @property
    def located(self) -> bool:
        """定位成功（有框、且 inkmask 找得到名字+冒号）。"""
        return self.box is not None and "LOC_NONE" not in self.failures

    @property
    def clean(self) -> bool:
        """擦除合格：残余墨迹 ≤ 5%，且蒙版外零改动。"""
        return (
            self.located
            and self.outside_changed == 0
            and self.ink_after <= max(8, int(self.ink_before * 0.05))
        )

    @property
    def gap_ok(self) -> bool:
        """名字↔冒号的距离回到了原值。"""
        return self.located and self.gap_now == self.join_gap

    @property
    def tail_ok(self) -> bool:
        """尾块是原图像素（平移而非重画）。"""
        return self.located and self.tail_diff_px == 0

    @property
    def tail_clean(self) -> bool:
        """尾巴**搬干净**了：原地的真实尾行里一像素墨都不剩。

        2026-10-11 用户报「移动正文，没有完全移动」之前，搬家矩形的纵向被夹在标注框
        里，尾巴墨迹探出框的那半截留在原地 —— 残墨就挂在这条判据上
        （见 expand/扩展张数.md §25.1）。**只报不判**：不进 :attr:`spec_ok`，
        因为 ``spec_ok`` 是「规格内成立」，而这条是「物理上搬全了没有」。
        """
        return self.located and self.tail_leftover_px == 0

    @property
    def box_ok(self) -> bool:
        """整条装配没越框。"""
        return self.located and "BOX_BAD" not in self.failures

    @property
    def spec_ok(self) -> bool:
        """排版规格整体成立。"""
        return self.gap_ok and self.tail_ok and self.box_ok

    @property
    def width_ratio(self) -> float:
        """新名字宽度 / 原名字槽位宽度。1.0 = 长度相当，>1 = 变长了。"""
        if self.slot_width <= 0:
            return 0.0
        return self.text_width / self.slot_width


# --------------------------------------------------------------------------
# 语料扫描
# --------------------------------------------------------------------------


def split_user_dir(dirname: str) -> tuple[str, int]:
    """把 ``昵称-张数`` 拆成 ``(昵称, 张数)``。

    目录名是 ``交付清单.txt`` 的编码方式，昵称本身可能含 ``-``
    （``一麻袋开心(沧海一舟)-6``）或 ``@``（``@余生都是你-4``），所以只能
    **从右边**切第一个 ``-``。

    Returns:
        (昵称, 张数)；张数解析不出来时给 0。
    """
    name, _, count = dirname.rpartition("-")
    if not name:  # 目录名里没有 '-'，整个当昵称
        return dirname, 0
    try:
        return name, int(count)
    except ValueError:
        return dirname, 0


def collect_images(root: Path, users: Optional[str] = None) -> list[tuple[Path, str]]:
    """递归收集 ``<根>/<昵称-张数>/<n>.jpg``，返回 ``[(路径, 昵称), ...]``。

    Args:
        root:  语料根目录。
        users: 只跑名字匹配这个 glob 的用户目录（``--users``），None 表示全跑。

    Raises:
        NotADirectoryError: 根目录不存在或不是目录。
    """
    if not root.is_dir():
        raise NotADirectoryError(f"语料根目录不存在或不是目录：{root}")

    out: list[tuple[Path, str]] = []
    for user_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if users and not user_dir.match(users):
            continue
        nickname, _count = split_user_dir(user_dir.name)
        for img in sorted(user_dir.iterdir()):
            if img.is_file() and img.suffix.lower() in config.IMAGE_SUFFIXES:
                out.append((img, nickname))
    return out


# --------------------------------------------------------------------------
# 单张图
# --------------------------------------------------------------------------


def probe_name(probe: str, nickname: str, args: "_Args") -> str:
    """探针 → 实际要写的新名字。"""
    if probe == "short":
        return args.short_name
    if probe == "long":
        return args.long_name
    if probe == "equal":
        return nickname
    raise ValueError(f"不认识的探针 {probe!r}")


def nickname_is_ascii(nickname: str) -> bool:
    """昵称串是不是**只有 ASCII** —— 批量流程据此跳过整张图（``SKIP_PURE_EN``）。

    规格原话：「原图框内有纯英文 … 直接跳过」。判据取**目录名里的昵称**
    （``collect_images`` 已经把 ``昵称-张数`` 的张数尾巴切掉了），**不读图** ——
    整条链（检测 → 定位 → 擦除 → 排版）全省下。

    ⚠️ 这条判据的方向是**安全**的：目录名会把昵称里的 emoji **丢掉或转写成 ASCII**
    （``A淘气⇒`` 的真身是 ``🦄淘气💨``），所以「目录名是 ASCII」推不出「图里也是 ASCII」。
    但它只用来**跳过**，不命中就照常往下走，图里真有 emoji 由 ``SKIP_EMOJI`` 兜住 ——
    两条判据互为兜底，谁漏了另一条还在。

    ⚠️ 数字、``@``、``_`` 也是 ASCII，所以 ``123456``、``@abc`` 会按这条跳过 ——
    与规格里「名字串只有 ASCII」这句**字面一致**。本语料命中的是 ``Czy`` / ``cai`` /
    ``qzuser`` / ``xw`` 这类共 11 个用户目录。
    """
    return nickname.isascii()


def detect_box(path: Path) -> tuple[Optional[tuple[int, int, int, int]], int, list[str]]:
    """检测这张图的标注框。

    读图**用 cv2**（检测要 BGR），和 ``process_image`` 里的 PIL 各读一次 ——
    多一次解码换的是「检测这一段的代码路径与 work/test 完全一致」，值。

    Returns:
        (框 or None, 框个数, 失败标记列表)。
    """
    image_bgr = cv2.imread(str(path))
    if image_bgr is None:
        return None, 0, ["READ_FAIL"]
    try:
        boxes = box_detect.detect_boxes(image_bgr)
    except Exception as exc:  # noqa: BLE001 - 检测里什么都可能抛，统一上报
        return None, 0, [f"DETECT_ERROR:{exc}"]
    if not boxes:
        return None, 0, ["NO_BOX"]
    flags = ["MULTI_BOX"] if len(boxes) > 1 else []
    return box_detect.pick_box(boxes).as_tuple(), len(boxes), flags


def auto_geom_map(root: Path, images: Sequence[tuple[Path, str]],
                  profile: "platforms.PlatformProfile") -> dict[str, dict]:
    """平台要求上游几何时，从交付树自动重建 ``{图片绝对路径: geom}``。

    快手 App 的交付框是**多行融合框**，目标行不一定是第一行（750 张里 91 张不是）
    ⇒ 只有 ``comments.jsonl`` 的 ``colon_box`` 能确定目标行。重建失败只警告、不中断。
    """
    try:
        gs = upstream_geom.load(root)
    except upstream_geom.GeomError as exc:
        LOGGER.warning(
            "平台 %s 需要上游几何，但自动重建失败：%s —— 退回像素启发式（会大量 LOC_NONE）",
            profile.key, exc,
        )
        return {}
    root_resolved = Path(root).resolve()
    out: dict[str, dict] = {}
    for path, _nick in images:
        try:
            rel = Path(path).resolve().relative_to(root_resolved).as_posix()
        except ValueError:
            continue
        g = gs.items.get(rel)
        if g:
            out[str(Path(path).resolve())] = g
    LOGGER.info("上游几何（自动重建）：%s；图片命中 %d/%d",
                gs.describe(), len(out), len(images))
    return out


def run_one(task: tuple, args: "_Args") -> list[Row]:
    """跑一张图的全部探针。

    检测**只做一次**（它是这条链上最贵的一步），三个探针共用同一个框。
    两条跳过判据在这里拦（见模块 docstring「跳过」）：纯英文昵称在**读图之前**拦，
    emoji 必须等**定位之后**才判得出，所以搭第一档探针的车。跳过的图**不留文件**，
    但**留绝对路径**（``Row.path``，进 ``rows.csv`` 与 ``skipped.txt``）。

    Args:
        task: ``(图片路径, 昵称[, 上游geom])``。第三项只有 ``profile.needs_geom``
            的平台（快手 App）才有 —— 它的交付框是多行融合框，靠 ``colon_box`` 定目标行。
        args: 命令行参数（进程池里要显式传，不能靠闭包）。

    Returns:
        每个探针一行；读图/没框/跳过时只有一行（``probe="-"``，带跳过或失败标记）。
    """
    raw_path, nickname, geom = task if len(task) == 3 else (*task, None)
    path = Path(raw_path)
    base = dict(user=path.parent.name, file=path.name, path=str(path.resolve()))

    # ---- 跳过：昵称只有 ASCII ⇒ 连图都不用读（**规格**开关，按平台来）----
    if args.skip_pure_en and nickname_is_ascii(nickname):
        LOGGER.info("跳过 %s/%s：昵称「%s」只有 ASCII", path.parent.name, path.name, nickname)
        return [
            Row(**base, probe="-", new_name="", skip="SKIP_PURE_EN",
                skip_note=f"昵称「{nickname}」只有 ASCII")
        ]

    box, n_boxes, flags = detect_box(path)
    base["boxes"] = n_boxes
    if box is None:
        LOGGER.warning("检测失败 %s/%s：%s", path.parent.name, path.name, flags)
        return [
            Row(**base, probe="-", new_name="", box=None, failures=list(flags),
                error=";".join(flags))
        ]

    rows: list[Row] = []
    records: list[tuple[Row, object]] = []
    for probe in args.probes:
        name = probe_name(probe, nickname, args)
        outcome = replace_username.process_image(
            path,
            name,
            bbox=box,
            fit=args.fit,
            erase_badge=args.erase_badge,
            fill_slot=args.fill_slot,
            seed=args.seed,
            save_output=False,
            save_compare=False,
            save_mask=False,
            keep_result=args.dump_dir is not None,
            profile=platforms.profile_for(args.profile_key),
            geom=geom,
        )

        # ---- 跳过②：原图用户名段里有 emoji ----
        # 必须先**定位**才判得出（emoji 是图上的像素事实），而定位在 ``process_image``
        # 内部 ⇒ 借第一档探针的结果看一眼。emoji 是**图**的属性、与探针无关，所以第一档
        # 命中就够，不必跑完三档。这一档的擦除/渲染白做了一次，但**没有落任何图**、
        # 也没进任何数量 —— 「不擦不画」这条规格守的是**产出**，不是内存里的中间量。
        if outcome.has_emoji:
            LOGGER.info(
                "跳过 %s/%s：原图用户名段里有 emoji（%s）",
                path.parent.name, path.name, outcome.name_emoji,
            )
            return [
                Row(**base, probe="-", new_name="", box=box, skip="SKIP_EMOJI",
                    skip_note=outcome.name_emoji)
            ]

        row = Row(
            **base,
            probe=probe,
            new_name=name,
            box=box,
            failures=list(flags),
            slot_width=outcome.slot_width,
            text_width=outcome.text_width,
            max_name_width=outcome.max_name_width,
            join_gap=outcome.join_gap,
            gap_now=outcome.gap_now,
            tail_shift_px=outcome.tail_shift_px,
            tail_diff_px=outcome.tail_diff_px,
            tail_leftover_px=outcome.tail_leftover_px,
            ink_before=outcome.ink_before,
            ink_after=outcome.ink_after,
            outside_changed=outcome.outside_changed,
            truncated_chars=outcome.truncated_chars,
            stripped_chars=outcome.stripped_chars,
            filled_chars=outcome.filled_chars,
            glyph_height=outcome.glyph_height,
            font_size=outcome.font_size,
            multi_line=outcome.multi_line,
            elapsed_s=outcome.elapsed_s,
            error=outcome.error,
        )
        if not outcome.ok:
            row.failures.append("LOC_NONE")
        else:
            if not row.clean:
                row.failures.append("LEAK" if outcome.outside_changed else "DIRTY")
            if not row.gap_ok:
                row.failures.append("GAP_BAD")
            if not row.tail_ok:
                row.failures.append("TAIL_BAD")
            if not row.tail_clean:
                row.failures.append("TAIL_RESID")
            if not outcome.box_ok:
                row.failures.append("BOX_BAD")
        rows.append(row)
        records.append((row, outcome))

        if args.dump_dir is not None and not args.dump_full:
            _dump(args.dump_dir, path, outcome, row)

    # 落图放在循环**之外**：``all`` 的摞图、``full`` 的原图基准条，都要一次拿到
    # 同一张图的全部探针才做得出来。
    if args.dump_full and args.dump_dir is not None:
        _dump_full(args.dump_dir, path, records)
    elif args.dump_dir is not None and (args.dump_all or any(not r.spec_ok for r in rows)):
        _dump_montage(args.dump_dir, path, records)
    return rows


def _detected_box(records: list[tuple[Row, object]]) -> Optional[tuple[int, int, int, int]]:
    """这批探针共用的**检测框**。

    用 ``row.box``（检测阶段填的），**不是** ``outcome.box`` —— 后者由管线在定位
    成功时才填，于是定位失败的图会整个没有裁剪条，而**失败那几张恰恰是最该看的**。
    框是检测做的，与定位成败无关；检测只跑一次，三个探针共用同一个。
    """
    return next((r.box for r, _ in records if r.box), None)


def _crop_rect(
    box: tuple[int, int, int, int], w: int, h: int
) -> tuple[int, int, int, int]:
    """「框 + ``COMPARISON_PAD`` 留白」的裁剪窗，并夹进图像边界。

    落裁剪条的三处（``all`` 的摞图、``failures`` 的对照条、``full`` 的裁剪条）
    共用这一条 —— 各算各的迟早会算出三种不一样的窗，那时「图对不上」会被当成
    管线的问题，而不是裁剪窗的问题。

    留白必须留着：评论行紧挨着上下邻居，裁到框沿就看不出「有没有碰到人家」。
    框贴着屏幕边时（评论常在底部）留白会伸出去，所以必须夹。
    """
    x0, y0, x1, y1 = box
    pad = config.COMPARISON_PAD
    return max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)


def _build_montage(
    path: Path, records: list[tuple[Row, object]]
) -> Optional[Image.Image]:
    """把**同一张图的几档探针摞成一张图**，一眼看出长度的影响。

    ``--dump all`` 的产物。原先每档各存一个文件（``1_short_compare.png``、
    ``1_equal_compare.png``、``1_long_compare.png``），看的人得自己来回切三张图比对，
    而且三张图**尺寸还不一样** —— 名字越短，尾巴平移越多，裁出来的框区一样但内容对不齐，
    根本没法「一眼看出」。摞成一张、行高一致、左侧标名字，才是可看的。

    ``--dump full`` **不用它** —— 用户看过之后否掉了「把这块摞图贴回原图」的形态
    （面板带着留白，压住了上下邻居）。要「每档一张整图」就别走贴图这条路，
    直接落管线自己的结果图，见 :func:`_dump_full`。

    行序固定为 **原图 → 短 → 等长 → 超长**（``PROBES`` 的顺序）。原图放第一行是必须的：
    没有基准行，「尾块是否原像素」「间距是否 5px」都只能靠记忆比对。
    """
    if not records:
        return None
    box = _detected_box(records)
    if box is None:
        return None
    orig = np.array(Image.open(path).convert("RGB"))
    h, w = orig.shape[:2]
    cx0, cy0, cx1, cy1 = _crop_rect(box, w, h)
    ch = cy1 - cy0

    # 每行 = (探针名, 新名字, 像素块)。**分两行写**是必须的：合成一行
    # （``long: SuperUser ✂3``）会让标签要得很宽，窄图下只能截成 ``long: Sup…``，
    # 恰好把最该看的「用的什么名字」截掉。分两行后只要最长的那一段放得下即可。
    blocks: list[tuple[str, str, np.ndarray]] = [("原图", "", orig[cy0:cy1, cx0:cx1])]
    crop_w = cx1 - cx0
    for row, outcome in records:
        img = getattr(outcome, "result_image", None)
        # 截断标记放**上一行**：``long ✂3`` 短，放得下；名字那行留给名字本身。
        tail = f" ✂{row.truncated_chars}" if row.truncated_chars else ""
        if img is None:
            # 这个探针没出图（定位失败等）。**用灰块占位，不要 continue** ——
            # 少一行会让人以为「这一档没跑」，而真相是「跑了、失败了」。
            why = "+".join(row.failures[:2]) if row.failures else "FAIL"
            blocks.append((f"{row.probe} ✗", why, np.full((ch, crop_w, 3), 56, np.uint8)))
            continue
        blocks.append((f"{row.probe}{tail}", row.new_name, img[cy0:cy1, cx0:cx1]))

    gap = 6
    margin = 24
    # 字号跟着行高走，但要**封顶** —— 框又高又窄时（如礼物动画区 171×97），
    # 不封顶会算出 40px+ 的大字，标签比图还宽。
    font = _label_font(min(44, max(11, int(ch * 0.26))))
    probe_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    # ⚠️ 标签宽度必须**量出来**，不能猜。猜小了 ``draw.text`` 溢出到右边、
    # 把结果图上的新名字盖住（第一版就是这样：long 那行的「截3字」压住了 ``SuperU``，
    # 看上去像管线把名字写错了，其实只是标注画到人家头上）。
    needed = int(
        max(probe_draw.textlength(t, font=font) for a, b, _ in blocks for t in (a, b))
    )

    # 两种排布，**按量出来的宽度选**，不要硬套左侧栏：
    #   ① 左侧栏 —— 标签放得下时用（绝大多数评论框，宽扁形），紧凑；
    #   ② 顶栏   —— 放不下时用（窄高框：标签 196px 而裁剪区只有 219px，
    #                `0.38*crop_w` 只有 83px，左侧栏会把标签截成一个「…」）。
    # 判据是「标签 + 留白能否塞进裁剪区的 38%」，塞不进就换成整幅宽度的顶栏。
    use_column = needed + margin <= int(crop_w * 0.38)
    canvas_w = (needed + margin + crop_w) if use_column else max(crop_w, needed + margin)
    band = 0 if use_column else int(font.size * 1.9)
    row_h = band + ch + gap

    canvas = Image.new(
        "RGB", (canvas_w, len(blocks) * row_h - gap), (24, 24, 28)
    )
    draw = ImageDraw.Draw(canvas)
    line_h = font.size * 1.15

    for i, (top, bot, block) in enumerate(blocks):
        y = i * row_h
        offset_x = needed + margin if use_column else 0
        canvas.paste(Image.fromarray(block), (offset_x, y + band))
        failed = top.endswith("✗")
        c_top = (255, 120, 120) if failed else (235, 235, 240)
        c_bot = (255, 120, 120) if failed else (150, 200, 255)
        if use_column:
            mid = y + band + ch / 2
        else:
            mid = y + band / 2
        draw.text((10, mid - line_h / 2), top, fill=c_top, font=font, anchor="lm")
        if bot:
            draw.text((10, mid + line_h / 2), bot, fill=c_bot, font=font, anchor="lm")

    return canvas


def _dump_montage(dump_dir: Path, path: Path, records: list[tuple[Row, object]]) -> None:
    """落**裁剪**摞图 ``<用户>/<序号>_probes.png``（``--dump all``）。"""
    montage = _build_montage(path, records)
    if montage is None:
        return
    target = dump_dir / path.parent.name
    target.mkdir(parents=True, exist_ok=True)
    montage.save(target / f"{path.stem}_probes.png")


def _failed_strip(
    orig: Image.Image, crop: tuple[int, int, int, int], row: Row
) -> Image.Image:
    """探针没出图时的**占位裁剪条**：原图那条 + 红框 + 失败标记。

    不落这一条的话，失败的那一档在目录里**什么都没有** —— 「没跑」和「跑了但
    失败」长得一模一样（bug-156：静默零结果跟真成功在报告上区分不开）。
    """
    strip = orig.crop(crop).convert("RGB")
    draw = ImageDraw.Draw(strip)
    w, h = strip.size
    draw.rectangle([0, 0, w - 1, h - 1], outline=(255, 90, 90), width=3)
    why = "+".join(row.failures[:2]) if row.failures else "FAIL"
    font = _label_font(min(30, max(12, int(h * 0.22))))
    # 红底衬一条：评论区的底色深浅不定，纯描字会被背景吃掉。
    text = f"{row.probe} ✗ {why}"
    tw = draw.textlength(text, font=font)
    draw.rectangle([0, 0, min(w, tw + 14), font.size * 1.55], fill=(120, 20, 20))
    draw.text((7, font.size * 0.78), text, fill=(255, 235, 235), font=font, anchor="lm")
    return strip


def _dump_full(dump_dir: Path, path: Path, records: list[tuple[Row, object]]) -> None:
    """**每档两张**：整图 ``<序号>_full_<档>.png`` ＋ 裁剪条 ``<序号>_<档>.png``。

    两个规格是 2026-09-29 用户拍板的，各自否掉了上一版的一个做法：

    * **替换只发生在绿框内** —— 上一版贴回去的是一块「绿框 + 留白」大的面板，把
      上下的邻居评论和左侧等级徽章一起盖住了。现在整图**就是管线自己的结果图**：
      框内是这一档的新名字，框外逐像素等于原图。所以这里不做任何裁剪拼贴，
      ``save(result_image)`` 本身就是规格要求的那个「只换框内」。
    * **一档一张**，不再把四档摞成一张贴回去。

    裁剪条带 ``COMPARISON_PAD`` 留白（与 ``all`` 同一个裁剪窗）：整图看得出上下文，
    但 2560×1520 缩到屏幕上，5px 的间距和 1px 的描边都看不清了 —— 那是裁剪条的事。
    原图那一条 ``<序号>_orig.png`` 也要落：没有基准条，「尾块是否原像素」只能靠记忆比对。
    """
    box = _detected_box(records)
    if box is None:
        # 检测不到框就没有裁剪窗，也就无从「只换框内」—— rows.csv 里是 NO_BOX。
        return
    target = dump_dir / path.parent.name
    target.mkdir(parents=True, exist_ok=True)

    orig = Image.open(path).convert("RGB")
    w, h = orig.size
    crop = _crop_rect(box, w, h)
    orig.crop(crop).save(target / f"{path.stem}_orig.png")

    for row, outcome in records:
        img = getattr(outcome, "result_image", None)
        if img is None:
            _failed_strip(orig, crop, row).save(target / f"{path.stem}_{row.probe}.png")
            continue
        Image.fromarray(img[crop[1]:crop[3], crop[0]:crop[2]]).save(
            target / f"{path.stem}_{row.probe}.png"
        )
        Image.fromarray(img).save(target / f"{path.stem}_full_{row.probe}.png")


def _label_font(size: int):
    """标注用的字体，找不到 CJK 就退回位图默认字体（标签是 ASCII 也能读）。"""
    for candidate in config.FONT_PATHS:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size, index=config.FONT_FACE_INDEX)
            except OSError:
                continue
    return ImageFont.load_default()


def _dump(dump_dir: Path, path: Path, outcome, row: Row) -> None:
    """把一张图的结果落盘（只在 ``--dump-failures`` 时调用）。

    ⚠️ 文件名必须带**用户目录**：语料里每张图都叫 ``1.jpg``/``2.jpg``，
    只按 ``stem`` 命名会让 112 个用户互相覆盖 —— work/test 那边踩过一次
    （bug-145：跑 4 张的目录里只剩一个 ``mask.png``）。
    """
    if not outcome.ok or outcome.result_image is None:
        return
    if row.spec_ok and row.clean:
        return
    target = dump_dir / path.parent.name
    target.mkdir(parents=True, exist_ok=True)
    stem = f"{path.stem}_{row.probe}"
    Image.fromarray(outcome.result_image).save(target / f"{stem}_replaced.png")
    if outcome.box is not None:
        h, w = outcome.result_image.shape[:2]
        cx0, cy0, cx1, cy1 = _crop_rect(outcome.box, w, h)
        filler = tuple(int(v) for v in outcome.bg_color) if outcome.bg_color else (0, 0, 0)
        canvas = Image.new("RGB", (cx1 - cx0, 2 * (cy1 - cy0) + 8), filler)
        orig = np.array(Image.open(path).convert("RGB"))[cy0:cy1, cx0:cx1]
        res = outcome.result_image[cy0:cy1, cx0:cx1]
        canvas.paste(Image.fromarray(orig), (0, 0))
        canvas.paste(Image.fromarray(res), (0, (cy1 - cy0) + 8))
        canvas.save(target / f"{stem}_compare.png")


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------


def summarize(rows: list[Row]) -> dict:
    """按探针分组汇总，并统计失败分类。

    ⚠️ **跳过的图不进任何一组**（见模块 docstring「跳过」）：既不进 ``by_probe``
    的分母、也不进 ``failure_counts``，只单独在 ``skipped`` 里按理由计数。
    否则「这张图不该出片」会被读成「这张图失败了」—— 那正是这两条的规格要区分的事。
    """
    scored = [r for r in rows if not r.skip]
    by_probe: dict[str, dict] = {}
    for probe in sorted({r.probe for r in scored}):
        group = [r for r in scored if r.probe == probe]
        located = [r for r in group if r.located]
        by_probe[probe] = {
            "n": len(group),
            "located": len(located),
            "clean": sum(1 for r in group if r.clean),
            "gap_ok": sum(1 for r in group if r.gap_ok),
            "tail_ok": sum(1 for r in group if r.tail_ok),
            "tail_clean": sum(1 for r in group if r.tail_clean),
            "box_ok": sum(1 for r in group if r.box_ok),
            "spec_ok": sum(1 for r in group if r.spec_ok),
            "truncated": sum(1 for r in group if r.truncated_chars > 0),
            "multi_line": sum(1 for r in group if r.multi_line),
            "mean_elapsed_s": (
                round(sum(r.elapsed_s for r in group) / len(group), 3) if group else 0.0
            ),
            "mean_width_ratio": (
                round(sum(r.width_ratio for r in located) / len(located), 3)
                if located
                else 0.0
            ),
        }

    counts: dict[str, int] = {}
    for row in scored:
        for flag in row.failures:
            key = flag.split(":")[0]
            counts[key] = counts.get(key, 0) + 1
        # 一张图三跑，检测类失败会重复计三次 —— 汇报时说明口径

    skipped: dict[str, int] = {}
    for row in rows:
        if row.skip:
            skipped[row.skip] = skipped.get(row.skip, 0) + 1

    return {
        "by_probe": by_probe,
        "failure_counts": dict(sorted(counts.items())),
        "skipped": {
            "by_reason": dict(sorted(skipped.items())),
            "images": sum(skipped.values()),
        },
    }


def anchor_check(rows: list[Row]) -> dict:
    """锚点自检：``爱国者-9`` 那 8 张必须复现单张跑的结论。

    这 8 张图在单张模式下已经验过（7 项判据全零，见 README）。批量在同一批图上
    给出不一样的结果 ⇒ **批量外壳**写错了，不是管线坏了。

    ⚠️ 只对 ``long`` 探针断言不了 —— 那 8 张里有 4 张放不下 ``SuperUser`` 会被
    截断，而截断是**规格内的行为**。所以判据取「``equal`` 探针上 gap/tail/box
    三项全过」，`equal` 用的昵称是 ``爱国者``，与原文同名同长。
    """
    group = [r for r in rows if r.user == ANCHOR_DIR and r.probe == "equal"]
    if not group:
        return {"ran": False, "reason": f"语料里没有 {ANCHOR_DIR}/（或用了 --users 过滤掉）"}
    bad = [r for r in group if not (r.located and r.clean and r.spec_ok)]
    return {
        "ran": True,
        "n": len(group),
        "all_green": not bad,
        "bad": [
            {"file": r.file, "failures": r.failures, "gap_now": r.gap_now,
             "join_gap": r.join_gap, "tail_diff_px": r.tail_diff_px}
            for r in bad
        ],
    }


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------


@dataclass
class _Args:
    """跑批需要的参数（进程池要 pickle 它，所以是个 dataclass）。"""

    probes: tuple[str, ...]
    fit: str
    short_name: str
    long_name: str
    dump_dir: Optional[Path]
    dump_all: bool
    dump_full: bool
    fill_slot: Optional[bool] = None
    erase_badge: bool = True
    """等级标（✦14 那种药丸）连名字一起擦 —— 与成品线（``batch_produce``）同口径。"""
    seed: Optional[int] = None
    profile_key: str = "douyin"
    """平台 profile key（定位策略 + 阈值 + 跳过规格）。主进程解析好，随进程池传下去。"""
    skip_pure_en: bool = True
    """``SKIP_PURE_EN`` 这条**规格**是否启用。⚠️ 是规格不是阈值：抖音 9.2%、微博 3.7%。"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="batch_run",
        description="批量跑真实语料：检测框 → 定位 → 擦除 → 排版，逐段判对错。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例：\n"
            "  python core/batch_run.py                     # 全读 batch_run.config.json\n"
            "  python core/batch_run.py /home/puyu/work/data/抖音奇瑞汽车/all_users_正面_3张以上\n"
            "  python core/batch_run.py <根目录> --limit 30            # 先小步试\n"
            "  python core/batch_run.py <根目录> --users '爱国者-*'     # 只跑锚点\n"
            "  python core/batch_run.py <根目录> --limit 20 --dump all  # 20 张图，各存一张三档摞图（裁剪）\n"
            "  python core/batch_run.py <根目录> --limit 20 --dump full # 20 张图，每档一整图 + 一条裁剪条\n"
            "  python core/batch_run.py <根目录> --dump failures      # 只存有失败判据的图\n"
        ),
    )
    runconfig.add_argument(p)
    # ⚠️ 可被运行配置 JSON 控制的参数一律 ``default=None``（None = 这次没给），
    #    由 runconfig.fill 依次回填：命令行 > JSON > 下面的 DEFAULTS。别给实值默认。
    p.add_argument("root", type=Path, nargs="?", default=None,
                   help="语料根目录（里面是「昵称-张数」的子目录），缺省读配置")
    p.add_argument(
        "--probe", default=None, choices=(*PROBES, "all"),
        help="跑哪几档长度探针（默认 all）",
    )
    p.add_argument("--short-name", default=None, help="short 探针用的名字（默认 A）")
    p.add_argument("--long-name", default=None, help="long 探针用的名字")
    p.add_argument(
        "--fit", default=None, choices=("clip", "width", "height"),
        help="新名字放不下时的取舍（默认取 config.FIT_MODE）",
    )
    p.add_argument(
        "--no-fill", dest="fill_slot", action="store_false", default=None,
        help="名字比原槽位**窄**时不随机补满（复现 2026-09-29 前的旧行为："
             "尾巴左移、右边留背景色空隙）。默认补满，见 config.FILL_SLOT_ENABLED",
    )
    p.add_argument(
        "--no-erase-badge", dest="erase_badge", action="store_false", default=None,
        help="**不擦等级标**。默认**擦** —— 与成品线（`batch_produce.py`）同一个口径，"
             "验收壳要量的是**会交付的那条管线**。关掉它 = 复现 2026-10-11 之前的基线数字",
    )
    p.add_argument(
        "--seed", type=int, default=None,
        help="补齐用的随机种子；不传则由「图名 + 新名字」派生（同一张图永远同一结果）",
    )
    p.add_argument(
        "--platform", default=None,
        help="平台 profile（auto = 从语料路径判定，默认）。抖音走「按颜色认名字」；"
             "视频号/微博走「按冒号定位」，见 platforms.py",
    )
    p.add_argument("--limit", type=int, default=None, help="只跑前 N 张（按路径排序），0 = 全跑")
    p.add_argument("--users", default=None, help="只跑名字匹配这个 glob 的用户目录")
    p.add_argument(
        "--workers", type=int, default=None,
        help="并行进程数，0 = min(8, CPU 核数)",
    )
    p.add_argument("--out", type=Path, default=None, help="报告输出目录")
    p.add_argument(
        "--dump", default=None, choices=("none", "failures", "all", "full"),
        help=(
            "落盘哪些图（默认 none，省磁盘）：none 不存；"
            "failures 只存有失败判据的图；"
            "all 每张都存，落一张 <用户>/<序号>_probes.png —— 原图 + 三档探针摞成一张"
            "（**框外的界面被裁掉了**）；"
            "full 每张都存，**每档两张**：整图 <用户>/<序号>_full_<档>.png"
            "（尺寸与原图一致，**只有绿框内被换**，框外逐像素等于原图）"
            "＋ 裁剪条 <用户>/<序号>_<档>.png（框+留白，看 5px 间距这种细节），"
            "另落原图基准条 <用户>/<序号>_orig.png（约 8.7 MB/张原图）"
        ),
    )
    p.add_argument(
        "--dump-failures", action="store_true", default=None,
        help="等价于 --dump failures（保留旧写法）",
    )
    p.add_argument("--quiet", action="store_true", default=None, help="不打每张图的日志")
    p.add_argument("--verbose", action="store_true", default=None, help="打调试日志")
    return p


DEFAULTS: dict = {
    "root": None,
    "probe": "all",
    "short_name": "A",
    "long_name": "SuperUser",
    "fit": config.FIT_MODE,
    "fill_slot": None,                 # None = 用 config.FILL_SLOT_ENABLED
    "erase_badge": True,               # 与 batch_produce 同一个口径（mock 线：等级标一起换）
    "seed": None,
    "platform": "auto",
    "limit": 0,
    "users": None,
    "workers": 0,
    "out": None,
    "dump": "none",
    "dump_failures": False,
    "quiet": False,
    "verbose": False,
}
"""命令行与 JSON 都没给时用的值（**同时是运行配置的合法键清单与类型表**）。"""


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from_cli, from_json, cfg_path = runconfig.load(
            args, DEFAULTS, script_file=__file__)
    except runconfig.ConfigError as exc:
        print(f"运行配置有问题：{exc}", file=sys.stderr)
        return 2
    args.root = runconfig.path(args.root)
    args.out = runconfig.path(args.out)
    if args.root is None:
        print("没给语料根目录：命令行给一个，或在 batch_run.config.json 里写 `\"root\"`", file=sys.stderr)
        return 2

    level = logging.DEBUG if args.verbose else (logging.WARNING if args.quiet else logging.INFO)
    logging.basicConfig(
        level=level, format=config.LOG_FORMAT, datefmt=config.LOG_DATEFMT
    )
    if not args.verbose:
        # 管线内部的每张图日志由本脚本自己汇总，别刷两遍。
        # 非 quiet 时保留 WARNING —— 「新名字放不下、要截断」正是 WARNING，
        # 而截断率是这个批量最想看的数之一；quiet 时连它也压掉。
        quiet_level = logging.ERROR if args.quiet else logging.WARNING
        for name in ("replace_username", "inkmask", "draw_text"):
            logging.getLogger(name).setLevel(quiet_level)

    LOGGER.info(runconfig.describe(cfg_path, from_cli, from_json))

    try:
        images = collect_images(args.root, args.users)
    except NotADirectoryError as exc:
        LOGGER.error("%s", exc)
        return 2
    if args.limit > 0:
        images = images[: args.limit]
    if not images:
        LOGGER.error("没扫到任何图片，检查路径与 --users")
        return 2

    probes = PROBES if args.probe == "all" else (args.probe,)
    out_dir = args.out or (config.DEFAULT_OUTPUT_DIR / f"batch_{time.strftime('%Y%m%d_%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    dump_mode = "failures" if args.dump_failures else args.dump
    dump_dir = (out_dir / "dump") if dump_mode != "none" else None
    dump_all = dump_mode == "all"
    dump_full = dump_mode == "full"

    workers = args.workers or max(1, min(8, os.cpu_count() or 1))
    # 平台：从语料路径判（不猜），认不出用 generic
    platform_key = (
        platforms.detect_platform_from_path(args.root)
        if args.platform == "auto" else platforms.resolve_key(args.platform)
    ) or (args.platform if args.platform != "auto" else "generic")
    profile = platforms.profile_for(platform_key)
    # ⚠️ 存回 Namespace：``_write_reports`` 拿的就是这个 Namespace，而它只有 ``args.platform``
    #    （"auto"）—— 不存回来的话，报告里会**一律写成 douyin**（运行时对、报告错，实测踩到）。
    args.platform_key = profile.key

    LOGGER.info(
        "语料 %s · 扫到 %d 张图 · %d 个用户目录 · **平台 %s（%s）** · 探针 %s · fit=%s · 补满槽位=%s · 擦等级标=%s · %d 进程 · 输出 %s（跳过的**不计入**下列数量）",
        args.root, len(images), len({p.parent.name for p, _ in images}),
        profile.key, profile.name_strategy,
        "+".join(probes), args.fit,
        "开" if (config.FILL_SLOT_ENABLED if args.fill_slot is None else args.fill_slot) else "关",
        "开" if args.erase_badge else "关",
        workers, out_dir,
    )

    run_args = _Args(
        probes=probes,
        fit=args.fit,
        short_name=args.short_name,
        long_name=args.long_name,
        dump_dir=dump_dir,
        dump_all=dump_all,
        dump_full=dump_full,
        fill_slot=args.fill_slot,
        erase_badge=args.erase_badge,
        seed=args.seed,
        profile_key=profile.key,
        skip_pure_en=profile.skip_pure_en,
    )

    # 检测在**子进程**里做（它是最贵的一步），主进程只汇总。
    # 多行融合框的平台（快手 App）自动带上上游 geom —— 见 :func:`auto_geom_map`。
    geom_map = auto_geom_map(args.root, images, profile) if profile.needs_geom else {}
    tasks = [(str(p), nick, geom_map.get(str(Path(p).resolve())))
             for p, nick in images]

    rows: list[Row] = []
    t0 = time.perf_counter()
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_one, task, run_args) for task in tasks]
        for future in as_completed(futures):
            try:
                rows.extend(future.result())
            except Exception as exc:  # noqa: BLE001 - 单张崩了不该带塌整批
                # ⚠️ 必须**留下痕迹**，不能只打日志：子任务全崩时（bug-156 就是
                # 签名改了、任务元组没改）日志里只有几行 ERROR，报告却照常打印
                # 「失败分类：无 / 总耗时 0s」—— 静默零结果与真成功在报告上分不出来。
                LOGGER.error("子任务异常：%s", exc)
                rows.append(
                    Row(user="?", file="?", probe="-", new_name="",
                        failures=[f"CRASH:{type(exc).__name__}"], error=str(exc))
                )
            done += 1
            if done % 50 == 0 or done == len(tasks):
                LOGGER.info("  进度 %d/%d 张（%.0fs）", done, len(tasks), time.perf_counter() - t0)
    elapsed = time.perf_counter() - t0

    rows.sort(key=lambda r: (r.user, r.file, r.probe))
    _write_reports(rows, out_dir, elapsed, args, probes, len(images))

    report = summarize(rows)
    LOGGER.info("=" * 78)
    LOGGER.info("总耗时 %.0fs（%.2fs/张，含 %d 个探针）", elapsed, elapsed / max(1, len(images)), len(probes))
    n_skipped = report["skipped"]["images"]
    LOGGER.info(
        "  处理 %d 张（= 扫到的 %d − 跳过 %d）—— **跳过的不计入下面任何数量**",
        len(images) - n_skipped, len(images), n_skipped,
    )
    for probe, stat in report["by_probe"].items():
        LOGGER.info(
            "  %-6s n=%-4d 定位 %-4d 干净 %-4d 间距 %-4d 尾块 %-4d 尾净 %-4d 不越框 %-4d 合规 %-4d 截断 %-4d  宽度比 %.2f",
            probe, stat["n"], stat["located"], stat["clean"], stat["gap_ok"],
            stat["tail_ok"], stat["tail_clean"], stat["box_ok"], stat["spec_ok"],
            stat["truncated"], stat["mean_width_ratio"],
        )
    n_multi = len({(r.user, r.file) for r in rows if r.multi_line})   # 按**图**数，不是行数
    if n_multi:
        LOGGER.warning(
            "  多行框 %d 张（检测器圈大了）：已按名字那一行带切开，第二行原样留着（bug-168）",
            n_multi,
        )
    skipped = report["skipped"]
    if skipped["images"]:
        reasons = " ".join(f"{k}×{v}" for k, v in skipped["by_reason"].items())
        LOGGER.info(
            "  跳过 %d 张（**不是失败、不计入任何数量**；绝对路径见 skipped.txt）：%s",
            skipped["images"], reasons,
        )
    if report["failure_counts"]:
        LOGGER.info(
            "  失败分类（**按图×探针计**，检测类会被重复计 %d 次）：%s",
            len(probes), report["failure_counts"],
        )
    else:
        LOGGER.info("  失败分类：无")

    anchor = anchor_check(rows)
    if anchor["ran"]:
        if anchor["all_green"]:
            LOGGER.info("  锚点自检 %s：%d 张全绿 ✓（批量外壳与单张跑一致）", ANCHOR_DIR, anchor["n"])
        else:
            LOGGER.error(
                "  锚点自检 %s：**%d/%d 张不合规** —— 先怀疑批量外壳，再怀疑管线：%s",
                ANCHOR_DIR, len(anchor["bad"]), anchor["n"], anchor["bad"][:3],
            )
    else:
        LOGGER.warning("  锚点自检跳过：%s", anchor.get("reason"))
    return 0


def _write_reports(
    rows: list[Row], out_dir: Path, elapsed: float, args, probes: tuple[str, ...],
    n_images: int,
) -> None:
    """落 ``rows.csv`` 与 ``summary.json``。"""
    report = summarize(rows)
    n_skipped = report["skipped"]["images"]
    profile = platforms.profile_for(getattr(args, "platform_key", "douyin"))
    report["run"] = {
        "root": str(args.root),
        # ⚠️「跳过的不计入数量」：``images`` == ``images_processed``，都是**处理过的张数**；
        # 扫到的总数单列在 ``images_scanned``。跳过的图只出现在 ``skipped`` 块里，
        # 不参与任何比率。
        "images": n_images - n_skipped,
        "images_processed": n_images - n_skipped,
        "images_scanned": n_images,
        "images_skipped": n_skipped,
        "probes": list(probes),
        "platform": profile.key,
        "name_strategy": profile.name_strategy,
        "skip_pure_en": profile.skip_pure_en,
        "fit": args.fit,
        "short_name": args.short_name,
        "long_name": args.long_name,
        "fill_slot": config.FILL_SLOT_ENABLED if args.fill_slot is None else args.fill_slot,
        "erase_badge": args.erase_badge,
        "seed": args.seed,
        "elapsed_s": round(elapsed, 1),
        "rows": len(rows),
    }
    report["anchor"] = anchor_check(rows)
    (out_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 跳过的图**不落任何图片文件**，唯一留下来的是这里 —— 绝对路径 + 理由。
    skipped = sorted((r for r in rows if r.skip), key=lambda r: r.path)
    if skipped:
        (out_dir / "skipped.txt").write_text(
            "".join(f"{r.path}\t{r.skip}\t{r.skip_note}\n" for r in skipped),
            encoding="utf-8",
        )

    fieldnames = list(asdict(rows[0]).keys()) if rows else []
    with (out_dir / "rows.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            data = asdict(row)
            data["failures"] = "|".join(row.failures)
            data["box"] = "" if row.box is None else ",".join(str(v) for v in row.box)
            writer.writerow(data)
    LOGGER.info("报告已写到 %s（rows.csv / summary.json）", out_dir)


if __name__ == "__main__":
    sys.exit(main())
