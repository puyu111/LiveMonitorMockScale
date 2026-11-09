#!/usr/bin/env python3
"""扩展张数：给语料里**每个真实用户**补图。

与 `batch_produce.py`（mock 线：名单假名 + 正文重画 + 一张底图只出一个成品）的关系：
**同一套度量与出片函数，口径 7 处不同** —— 都写在下面 `口径表` 里，`batch_produce.py` 一行不改。

数据形态（`~/github/eagle/Embodied/out/livepipe_example3/<平台>/all_users_正面_3张以上/`）：
每个用户一个目录 `<用户名>-<张数>/`，里面是真实截图（行结构 = `[等级徽章药丸][昵称][：][正文]`），
`manifest.json` 逐图带 `rel / t / roi / label / content / emoji_text`。

口径（用户 2026-10-10 拍板，全文见 `expand/扩展张数.md`）：
  ① 张数 `T = max(min_images, ceil(ratio × N))`（**默认 5 / 1.5**，本次 App 线是 1.6/5，报告里那行会印**这次真用的**数），缺口 ≤5% 不阻断、>5% 退 3；
  ② 底图从**其他用户**借（`base.user_dir ≠ target.dir`），**允许复用**（上限见 `--base-cap`）；
  ③ 擦除 = 框内 + 徽章左沿外扩（约 8px）；④ **徽章一起换成目标用户自己的 —— 整颗药丸**，
     含**落在标注框外**的左圆头（抖音实测 16~22px 在框外）：贴图与度量都允许向框左外
     探 `BADGE_OUT_PX`（"修改可以扩展到外部一定范围"）；药丸右沿 = 名字槽位的起点，
     两者**一起算**（向右扩不许吃进 `slot[0]`），见 :func:`pill_box`；
  ⑤ 纯 ASCII 昵称的素材目录整目录跳过（`collect_material` 的 `SKIP_PURE_EN`，保留）；
  ⑥ 就地写回原目录、文件名 `扩展N.jpg`、目录名改成 `<用户名>-<新张数>`；
  ⑦ **标注框取自 `manifest.json` 的 `roi`（表式），不做像素检测** —— 照 `replace_username.resolve_box`
     那条线的"故意不做自动检测"（用户 2026-10-10：「参考换用户名功能的方式」）；上游把绿框改成
     **留白框**（2026-10-10 20:08，`--pad-ratio 0.25`）之后，画的框长什么样与我们的量测彻底无关。
     见 :func:`resolve_box`。语料目录以磁盘为准（`manifest.json` 里有、磁盘没有的条目剔除）。

**出片口径 = 上游**（用户 2026-10-11「全盘照上游」）：:func:`render_one` 只做
「调 `replace_username.process_image`（`fill_slot=False`）+ 贴徽章」，2026-10-10/11 上午
在扩展线自己造的三处**画法**偏离**全部删掉** —— ① 语料标定一个平台字号（连 `FIXED_FONT_SIZE`
/ `calibrate_font_size` 一起删）、② `adv_k` 横向重采样、③ `ERASE_EXT_PX` 多擦上下几行
（连同 `fringe_ink` 残墨闸）。字号因此**逐张跟底图的 `region.glyph_height`**、墨宽就是
Noto 原样墨宽、擦除就是 `inkmask` 原蒙版。⚠️ 这三处各自对应一个用户报过的问题，
删掉的代价**已按语料量过**（抖音宽度 1.000→0.679、视频号 0.976→0.712，等等），见
`expand/扩展张数.md` §22 —— 那一节是这一版的口径账，改动前先读它。
留下的只有**扩展线自己的护栏**（判"底图/名字本身行不行"，不是画法）：名字段结构
（:data:`JOIN_GAP_MIN` / :data:`JOIN_GAP_MAX`）、名字被截断（`TRUNCATED`）、
`bp.gate_reason` 那九道（`SKIP_EMOJI` 豁免）。

用法（**分线 2026-10-11 后住在 ``expand/``**；「这次怎么跑」写在同一目录的
``expand_produce.config.json``（抖音）/ ``expand_produce.视频号.config.json``（视频号）里，
命令行只用来逐项覆盖，见 `runconfig.py`）：:

    python3 expand/expand_produce.py                                    # P1：只读账（读配置）
    python3 expand/expand_produce.py --mode sample                      # P2：样张
    python3 expand/expand_produce.py --mode run --only 肘死你 --limit 10 # P3
    python3 expand/expand_produce.py --config expand/expand_produce.视频号.config.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import batch_produce as bp  # noqa: E402
import batch_run  # noqa: E402
import config  # noqa: E402
import draw_text  # noqa: E402
import inpaint_local  # noqa: E402
import inkmask  # noqa: E402
import platforms  # noqa: E402
import replace_username as ru  # noqa: E402
import runconfig  # noqa: E402
import upstream_geom  # noqa: E402
# 出片直接调 `process_image`（2026-10-11「全盘照上游」）⇒ 它逐张打的 WARNING
# （"底图名字段里有 emoji" / "框里装了不止一行" / "名字放不下被截断"）会跟着刷屏。
# 这三件事在扩展线都是**底图的常态**（emoji 那段本就该连名字一起消失，见 §12.6），
# 488 张的量会把出片日志埋掉。压到 ERROR —— 真正该看的错误一条不少。
logging.getLogger("replace_username").setLevel(logging.ERROR)

# ---------------------------------------------------------------- 口径表（与 batch_produce 的差异）

RATIO = 1.5
"""② 数量口径：`T = max(--min-images, ceil(RATIO × 现张数))`。"""

BADGE_MIN_PX = 40
BADGE_MAX_PX = 100
"""等级徽章药丸的**名义宽度区间** —— 抖音实测 54px 恒定、视频号 1 位 63~65 / 2 位 82~85；
下界 40 是 `inkmask` 的 `badge_min_width`（汉字 26~32px、emoji 26~28px 都在它下面 ⇒ 认得出"这不是药丸"）。

为什么要上限：药丸背景在视频号是**半透明叠加**，有时整段不过 ink 阈值 ⇒ 列段会
"退化成"名字首字/整条名字（实测 `风过无恨-15/11.jpg` 的 `segments[0]` 宽 **428px** 被当成药丸，
于是徽章档判成 3 档、底图全被同档约束挡掉 ⇒ 该用户 8 张只出 1 张）。药丸比这窄得多，直接卡死。
"""

BADGE_MERGE_GAP_PX = 2
"""并药丸碎片时允许的列缝 —— 视频号 `✦` 与等级数字之间常夹 1~2px 缝（实测 63/83px 是**并起来**的宽度）。"""

BADGE_SEP_MIN_PX = 2
"""药丸与**后面**的段之间必须空够的列数 —— 这是"它是一块独立药丸"而不是"名字被切下来的前半截"的判据。"""

BADGE_W_TOL = 3
"""C1（**像素级**）：目标贴图宽 − 底图徽章宽 的容差 —— 与 `paste_badge` 的硬闸同值。

为什么不用 `badge_class` 的档位就够：档位在抖音上太松（54px 恒定，而"名字头两个字粘成一段"
会是 40~52px，同档 ⇒ 选进来才在贴图时炸 `BADGE_MISMATCH`，白出一张）。
按**宽度差**筛就成了"选材即像素级"，实测抖音 638/645 徽章宽都在 53~55（同档内 ±1）。"""

BADGE_GAP_FRAC = 0.5
"""药丸右沿到**名字槽位**之间允许空出的距离，按药丸宽的占比算（`gap ≤ width × 0.5`）。

为什么这条是"是不是药丸"的硬判据（2026-10-10 抓到）：视频号 `时仪-32/1.jpg` 把**背景里那根
浅青色柱子**（47px）当成了药丸，`pill_box` 又给它补了 10px 左圆头 ⇒ 宽 63 **恰好落进合法族
[64, 84] 的 ±3 里**，宽度族闸拦不住；拿它当贴图源 ⇒ 出片把底图**真正的药丸盖上背景色**
（放大 6× 看过：那块里连 `✦N` 都没有）。

药丸和名字是同一行 UI 的两个相邻元素，间距**远小于药丸宽**。全语料实测（视频号 99 + 抖音 702 个
认定徽章）：`gap/width` 中位 0.108 / 0.141、p90 0.141 / 0.157、**最大 0.225**；被这条拦下的只有
0.81（时仪那根柱子）和 0.93（抖音 `浅夏梦星-3/2.jpg`、`5XW-3/*` —— 那几张是**名字槽位少圈了字**、
槽位起点偏右，同一用户的别的图 gap 仍是 10/6，换一张当贴图源即可）。0.5 两边各有 2 倍余量。
⚠️ 用**比值**不用绝对像素：药丸宽随平台/位数变（63~92），间距是"半颗药丸之内"这件事才是不变量。"""

BADGE_GROW_PX = 8
"""③ 擦除蒙版向**徽章左沿之外**多外扩的像素数（用户已定；报告里显式声明这处豁免）。"""

BADGE_OUT_PX = 26
"""④ 徽章药丸的**左圆头会被标注框切在框外** —— 实测抖音药丸左沿 x≈576、`box_rect` 左沿 592
⇒ 16~22px 在框外，只在框内做列投影就只量到"星+数字"。要"整颗药丸来自原图"（用户 2026-10-10 口径）
就必须**越出标注框**去量/去贴：允许向框左外探 `BADGE_OUT_PX` 像素（这也是"修改可以扩展到外部
一定范围"的**实现边界** —— 越框只允许徽章这一处，且只允许向左、只允许这么多）。

为什么是 26：抖音实测圆头 22px（p10=p50=22）、视频号 3~6px；留 4px 余量，同时不让它探到
上一行/头像等其他元素上去。"""

BADGE_VOUT_PX = 3
"""药丸**上下**允许外扩的像素数 —— 星/数字的墨迹行带比药丸底矮 1~3px（抖音药丸 20px 高、
星墨 18px），不扩就留底图那 1~3 行药丸底色（同 UI 同色调，肉眼几乎看不出，但"整颗来自原图"
就该连这几行一起换）。"""

PILL_FRAC_MIN = 0.6
"""一列被判成"药丸底"所需的行占比 —— 药丸是把整列底色抬起来的一块，圆头处占比略低，
取 0.6 既收得住圆头、又不会被面板噪声咬住（面板噪声那几列占比 <0.3）。"""

ROI_INSET = 5
"""**框来自 `manifest.roi` 时**的内缩量（像素）—— 与 :data:`config.BOX_INSET` 是两个口径。

`config.BOX_INSET = 6` 是给**画在图上、含彩色描边**的框用的（`replace_username` 的 `BOX_TABLE`
就是那种框：坐标是描边外沿，内缩 6 是为了把描边和它内缘的抗锯齿排除掉）。而 `manifest.json`
的 `roi` 是**紧贴那一行字**的框、**不含描边**（2026-10-10 实测 抖音 `肘死你-133/1.jpg`：
`roi=[587,751,1087,781]` 高 30px，画出来的框 `(578,742,1097,791)` = 每边外扩 9px）——
直接照搬 6px 会**从字里切掉 6px**：实测视频号 120 张里名字定位失败率 **6/120 → 88/120**、
字高 25 → 17（偏小 ⇒ `name_ratio` 虚高 ⇒ 2026-10-11 那版标出来的字形宽度比 1.40 → 1.63）。
⚠️ 2026-10-11「全盘照上游」之后出片不再走内缩 5 的这条路（`render_one` 把框交给
`process_image`，内缩量由 :func:`inset_for` 决定，仍然是 5）；这一条保留的是**为什么是这个值**。

取值怎么定的（2026-10-10）：拿**已交付那一版**的计划（`/tmp/plan_backup/`，抖音 481 / 视频号 91 张）
逐张回放，比它记的 `base_slot` / `base_gh` 与 `expand/扩展张数.md` §18.3 的成品指标 ——
`slot` **每个内缩量都逐张相同**（列量测不受纵向窗口影响），只有 `gh` 分得开：

===========  ======================  =========================
内缩量        视频号（旧 gh 中位 21）  抖音（旧 gh 中位 20）
===========  ======================  =========================
0 / 2 / 6    27 / 26 / 18            27 / 25 / 17
**5**        **20**（22/87 逐张相同）  **19**
4            22                      21
===========  ======================  =========================

再对成品指标（`--roi-inset` 全量重跑）：**5** 是唯一同时复现两平台徽章宽合法族
（视频号 `[64,84]`、抖音 `[71]`）与"有贴图源的用户数"（视频号 26 → 28）的取值。

⚠️ 2026-10-11（用户「擦除不干净」）修正：**横向 5 保留，纵向改为 0**（:data:`ROI_VINSET`）。
上面那张表里 `0 / 2 / 6 → 27 / 26 / 18` 一行其实就是证据：**紧贴文字的 roi 里，真实字高就是
27~28**，内缩 5 量到的 20 是**被自己切出来的**。当时选 5 只是为了复现"旧账"的 `base_gh`，
而旧账的 `base_gh` 本身就是量错的（同一份 roi 上，名字的墨占 1258~1285 共 28 行，
内缩 5 之后那块地是 1262~1283 共 22 行）—— 拿错的账当基准，标定出来的只能是错的基准。
"""


ROI_VINSET = 0
"""**框来自 `manifest.roi` 时**的纵向内缩量（像素）—— 见 :data:`ROI_INSET` 末尾的修正说明。

为什么是 0：`manifest.roi` 是**紧贴那一行字**的框（实测高 27~32px 装 27~28px 的字，上下只富余
1~2px），纵向内缩 5px 等于从字里切掉 5px。后果是同一个根因的两个症状：

* **残墨**：擦除蒙版被夹在「内缩后的那块地」里（`inkmask.find_erase_region` 的 `keep`）
  ⇒ 名字最上/最下那几行**永远擦不到**。全语料终值（`/tmp/residue4.py`，抖音 279 张 /
  视频号 61 张唯一底图）：纵向内缩 5 时，抖音残墨中位 **68px**/张、总量 **36122px**、
  只有 84/279 张为 0；视频号中位 **160px**、总量 **10810px**、**0/61** 张为 0。
  改为 0 之后：两平台 **全部 0px**（340/340 张为 0）。
* **画小**：字带（`glyph_band`）被同一块地夹着 ⇒ 字高量成 20、真值 27~28
  ⇒ 新名字按 20 画 ⇒ **小 26~29%**（抖音 0.714 / 视频号 0.741）。改后字高中位 28（抖音）/ 27（视频号）。

产品侧的判据（`/tmp/product_check_dy2.py` · `/tmp/product_check_vx.py`）：成品图上名字列的墨行跨度
必须等于报告里的 `glyph_height`，差为正 = 残墨。旧口径抖音 5/488、视频号 0/102 满足；新口径
抖音 414/486（比 1.000、max +1）、视频号 20/82（比 0.963、**max 0**）。

为什么横向不跟着放：roi 横跨整行（徽章 + 名字 + 冒号 + 正文），名字离左右缘 100px 上下，
横向内缩只削到首尾两端的**背景**；而它挪动 `slot` 的列投影 ⇒ 徽章宽的合法族与
"有贴图源的用户数"跟着变（那是 2026-10-10 标定过的口径）。**只动纵向 = 只修坏掉的那一件。**

⚠️ roi **之外**还有极少数几行名字墨（抖音 61/279 张上界外 ≤3 行、3/279 下界外 ≤1 行；
视频号 0/61 上、3/61 下）—— 那是标注框本身画紧了，不在本口径能管的范围内（纵向再放就会
啃到上/下相邻的评论行，正文不许动）。见 `expand/扩展张数.md` §24。
"""


NAME_STRIP_DIGITS = True
"""目标名字：`manifest.username` 可能带**等级徽章的数字前缀**（上游把徽章数字并进了昵称，
实测视频号 8/58：`11天空` 的真实昵称是 `天空`、`6李妥妥呀` 的真实昵称是 `李妥妥呀` ——
徽章段实测 83px / 65px = 2 位 / 1 位，与用户名前缀的数字个数**逐一对上**）。
处置：按**宽度对账** —— 从用户名里删掉前缀数字，直到名字墨迹宽最接近该用户自己图里量到的槽位宽。
这是**数据口径的对账**，不是猜字符串（`193` / `0x0` 这类真数字昵称宽度对得上就不会被删）。

2026-10-11 补（尺子会失灵的地方 ⇒ 第二条证据）：宽度尺子只在"槽位宽 ⟺ 文字宽"一一对应时有效，
实测有**三处会把它带偏**，全部落到"该删却没删"（出片 = 药丸 ✦6 + 名字 `6李妥妥呀` ⇒ 等级数字写两遍，
bug-292 同症状）：

| 情形 | 例（视频号） | 尺子差多少 |
| --- | --- | --- |
| 名字里带**表情**：槽位含表情宽、我们只算文字 | `6李妥妥呀`🦋 槽位 132 全被"文字刚好填满" | 差 0.2px（看着最贴 ⇒ 挑中带 6 的）✗ |
| 名字里带**我们字体偏窄的符号**（`&`） | `7余生&默走` 槽位 134 | 差 5px ✗ |
| 定位把**药丸并进了槽位**（`最左段压到名字槽位`） | `1顺心` / `7生活百般滋味` 槽位 = 药丸 + 名字 | 差 30~60px ✗ |

所以补一条**直接证据**：:func:`resolve_display_names` 顺手把该用户自己图上量到的**药丸宽**
（`badge_found` 的那些图的中位数）带进来；**只要量到过药丸**、用户名又是数字开头（后面还有东西），
就直接删掉那段前缀数字 —— 图上有丸 ⇒ 捕获串开头的数字就是丸里的等级。视觉真值核对过 8 户：
`✦1 顺心`、`✦6 李妥妥呀`🦋、`✦7 余生&默走`、`✦7 生活百般滋味`、`✦11 天空`、`✦13 yuyuyu`、
`✦8 万事顺意`、抖音 `🏠18 ⑤ XW` **全部前缀数字都不在名字里**；而 `0x0`（图上**没有**药丸）
与 `Fingerprints`（无数字前缀）都没被这条动过 ⇒ 与"宽度对账"是**并集**，不是替代。"""

PURE_ALNUM_RE = re.compile(r"^[0-9A-Za-z]+$")
"""**原名字纯数字/字母**的判定（用户 2026-10-10：「原名字纯数字和字母跳过」）。

背景是为交付质量做的取舍：纯 ASCII 名字（`cc` / `594315972` / `Kevin` / `13yuyuyu`）上的
擦除最不稳 —— 灰白细笔画在列投影里碎成笔画级细条（同 `expand/扩展张数.md` §12.8 的根因），
槽位/冒号锚点会被挑错 ⇒ 出片留残字。这类底图**整张不进池**（不是"擦得差一点也出"）。
只判 `[0-9A-Za-z]+`：带 `_ . & 空格 中文` 的名字不算纯数字字母（`Kevin.yang` / `余生&默走` 照用）。"""


JOIN_GAP_MIN = 3
"""名字右缘↔"冒号"之间的缝**至少**要这么大（`join_gap`，用户 2026-10-10：「没有擦除干净」的第二处）。

缝 ≤ 2px 的含义：紧挨名字的那一段**不是冒号**，而是**末字的笔画碎段**（`心` 的左侧点、
`的` 的右半）—— 上游列投影把字切成笔画级细条时就会这样，于是**槽位少了最后那个字**
（`勇敢的心` 实测槽位 81px = `勇敢的`，真名 ~110px），擦除蒙版盖不住它 ⇒ 成品里多出半个
别人的名字（实测 `肘死你` 那张写成 `肘死你心：`）。上游各道闸**全都看不见**：
残字被算成"尾巴的第一段"，`join_gap` 和结果图的 `gap_now` 用的是同一把尺子，两边一致 ⇒ `gap_ok` 绿。

量出来的分界：抖音 `join_gap` ∈ {1,2}=16 张、{3..10}=629 张、{13..15}=3、{18..43}=108；
视频号 {1,2}=15、{3..8}=228、{11,12}=4。**3 是干净的边界**（4 及以上都有 5~6px 的正常缝）。"""

JOIN_GAP_MAX = 10
"""名字右缘↔"冒号"之间的缝**至多**这么大 —— **只对"按饱和度认名字"的平台（抖音）生效**。

`platforms.name_strategy == "saturation_run"`（抖音）的定位规则是"名字 = 连着的高饱和段、
**紧跟名字之后的那一段就是冒号**"，**不校验冒号的几何**（`inkmask._locate_by_saturation` 规则 5）。
名字、冒号、正文在抖音**都是同一套彩色的字**，所以：名字段一旦在末字上断成两截、
或冒号被并进名字段，那个"冒号"就变成了**正文的第一个字**（实测 `飞扬-3/1.jpg`：冒号 = `国`，
26px 宽；`肘死你-133/21.jpg`：冒号 = `这颜`，55px）⇒ 槽位 = 名字 **+ 冒号** ⇒ 擦除蒙版把冒号
一起擦掉、尾巴从正文起算 ⇒ 成品**没有冒号**、名字和正文之间空出一大截（实测交付 479 张里 37 张）。

为什么 10 是分界：抖音正常缝 5~6（454+89 张），4 与 7/8 是抗锯齿抖动，9/10 各 6~7 张；
而"冒号被并进槽位"的那批缝 = 名字↔正文的距离 ≈ 18~23（106 张），中间 11~12 是空的。

视频号/微博走 `colon_anchor`（按**冒号几何**定分界：宽 3~10px、高 0.35~0.85 行带），
那个"冒号"永远是冒号形状的窄段 ⇒ 缝偏大是**良性的**（实测 `李妥妥呀(X):` 的 emoji 被并进
槽位 ⇒ 缝 12px，成品干净）—— 所以上界**不适用于**它们，加了只会白扔图（视频号实测 3 张）。"""

COLOR_DEV_MAX = 4
"""名字色量测的**可接受偏离**（逐通道）—— 超过它就在规划报告里点名（**只报告，不做闸**）。

名字色不再是"逐张按底图量"，而是按**用户**汇总成一个钉死的值（见 :func:`user_text_color`）。
汇总前得先知道这个用户的样本有多散：`_core_color_of` 取的是"最亮那 25% 墨迹像素的均值"，
视频压缩 + 抗锯齿让同一块 UI 逐张差 1~3（实测抖音 `肘死你-133` 的 127 张自图有 62 种取值）。
4 是"量测噪声"与"这用户的名字真不是同一个色"之间的分界：噪声那一头 ≤3，
**超过就必须看得见** —— 那说明取色窗口里混进了别的东西（emoji 之类），是量测的锅，不是用户的锅。
"""

TIME_BUCKETS = 6
"""C4：按 `manifest.t` 把一天分 6 桶，选底图时给"该用户还没用过的桶"加分 ⇒ 合成图不挤在同一时段。"""

NAME_FIT_LO = 0.85
"""**名字对账闸（下界，硬）**：`槽位宽 ÷ 该图自然字号下显示名的墨迹宽` 必须 ≥ 0.85。

为什么这条是**质量硬闸**：擦除蒙版 = **槽位那几列**。槽位比显示名窄 ⇒ 名字有墨在蒙版外 ⇒
出片**留下残字**，而现有各道闸（`tail_exact` / `gap_ok` / `box_ok`）**全都会通过**
（残字被当成"尾巴的一部分"跟着平移）—— 实测 `1顺心-4` 槽位 99px（= 药丸 63 + `顺`），
`心` 整个留在蒙版外；`顺心-3` 槽位 26px 而 `顺心` 该有 44px。
根因（`expand/扩展张数.md` §12.8）：视频号字形在列投影上**碎成笔画级细条**时会挑错冒号锚点；
抖音则是**名字被列缝切成多段**（`Java土豆` 只把 `土豆` 圈进名字段）。

上界为什么不设成同样紧的硬闸（见 :data:`NAME_FIT_WARN` / :data:`NAME_FIT_HI`）：
槽位**偏宽**时多出来的墨**也在蒙版内**（一起被擦掉）⇒ 出片是干净的，只是 inpaint 面积大些；
而且 `勇往直前-3/2.jpg`（143px，含 🎵）、`3.jpg`（多行框 300px）是多行/emoji 撑宽的**假阳性**。"""

NAME_FIT_WARN = 2.0
"""上界（**软**）：超过就加打分惩罚 —— 偏宽不是错误，但优先用别的底图（inpaint 面积小、更稳）。"""

NAME_FIT_HI = 3.0
"""上界（硬）：槽位是显示名的 3 倍以上就不再"偏宽"而是**圈进了别的东西**（实测 `肘死你-133/78.jpg`
槽位 278px 而 `肘死你` 只有 57px、`爱国者-13/5.jpg` 336px、`蒋-3/3.jpg` 91px for `蒋`）。
这种底图擦完是一大片糊 —— 直接排除。"""


WIDTH_K_FALLBACK: dict[str, float] = {"cjk": 1.44, "latin": 1.30}
"""平台字形宽度比 **k**（`平台画的宽 ÷ 我们画的宽`）的兜底值，**按字形类分开**。

2026-10-11 深夜实测（`expand/扩展张数.md` §23，只拿"名字不以数字开头"的图 —— 那些图上显示名
就是用户名，槽宽 ÷ 我们画的宽就是 k）：
抖音 **1.474**（无字母 138 户）/ **1.286**（含字母 14 户）；视频号 **1.392**（43 户）/ **1.306**（5 户）。

⚠️ 旧口径的 `FIXED_ADV_K`（抖音 1.40 / 视频号 1.32）就是这一类东西，只是**不分字形类**；
实测含字母的名字 k 明显更小（我方拉丁相对更宽）⇒ 分成两类才不把两类互相拽偏。"""

WIDTH_K_MIN_N = 8
"""标定 :data:`WIDTH_K_FALLBACK` 的**样本下限（户）** —— 低于它这一类直接用兜底值。"""


@dataclass
class Target:
    username: str
    user_dir: str
    rels: list[str]
    times: list[Optional[int]] = field(default_factory=list)
    name: str = ""
    """对账后**要写上去**的名字（见 :func:`resolve_name`）。"""
    self_slot: int = 0
    """自己图上槽位宽的中位数（对账 + 记录用）。"""
    self_name_note: str = ""

    @property
    def have(self) -> int:
        return len(self.rels)


@dataclass
class Base(bp.Candidate):
    """量过一遍的底图 = `batch_produce.Candidate` + 徽章几何 + 时间。"""

    badge_w: int = 0
    badge_rect: Optional[tuple[int, int, int, int]] = None
    badge_found: bool = False
    badge_note: str = ""
    t: Optional[int] = None
    has_emoji: bool = False
    display_name: str = ""
    """这张图**所在用户**在图上真正显示的名字（用于 :data:`NAME_FIT_LO` 的对账）。"""
    name_ratio: float = 0.0
    """`槽位宽 ÷ 该图自然字号下显示名的墨迹宽`；0 = 没量到。"""
    badge_cand_w: int = 0
    """**候选**徽章宽（被否的也记，排查/对账用；`badge_w` 只在认定成功时才有值）。"""
    badge_out_px: int = 0
    """`badge_rect` **向标注框左外**扩了几 px 才够到药丸真正的左沿（见 :func:`pill_box`）。"""
    box_src: str = ""
    """标注框的来路（:func:`resolve_box`）：正常是 `manifest.roi`；退回 `detect_box` 时才变。"""
    username: str = ""
    """这张图**所在目录的用户名**（`manifest.username`）。两处用：
    ① :func:`calibrate_width_k` 量平台字形比 k（只采不以数字开头的名字）；
    ② :func:`fit_font_size_for` 定这张底图自己那个名字的字号（见 :attr:`natural_font_size`）。"""
    join_gap: int = 0
    """用户名右缘→冒号左缘的列数（实测 5~6 是主流，见 `expand/扩展张数.md` §12.8）。"""
    struct_note: str = ""
    """结构坏掉的说明（空 = 好；见 :data:`JOIN_GAP_MIN` / :data:`JOIN_GAP_MAX`）。

    `join_gap` 的两头都是**结构性出错**的信号：太近 = 挨着名字的那一段不是冒号（末字碎段
    被当成了冒号）⇒ 出片留残字；太远 = 冒号被并进了槽位 ⇒ 出片丢冒号、名字与正文断开。
    坏图**只从底图池里剔掉**：用户自己的图仍然算数（贴图源只要徽章那几个像素，与名字无关）。"""
    text_color: Optional[tuple[int, int, int]] = None
    """这张图**原来的名字**是什么颜色（`inkmask` 量的核心色，已把名字段里的 emoji 剔出取色窗口）。

    规划时按**用户**汇总成众数（`self_color`）交给出片当**钉死的名字色** —— 见 :func:`user_text_color`。"""
    # ---- 评测埋点（2026-10-27）：**只记数，不参与任何判据** --------------
    # 计时在 worker 内取（跨进程只回整数毫秒）；0 = 未走到/旧路径。
    # 用它们算 `T_stage` 与 D 的收益，见 `docs/评测方案.html` §2/§3。
    ms_decode: float = 0.0
    """读图 + 转 RGB 的耗时（ms）。"""
    ms_locate: float = 0.0
    """`resolve_box` + `find_erase_region`（定位/擦除区）的耗时（ms）。"""
    ms_badge: float = 0.0
    """`badge_rect_of` + `pill_box`（徽章量测）的耗时（ms）。"""
    badge_checked: bool = False
    """是否真的调了 `badge_rect_of`（D 的 `N_badge` 计数用；没定位到就不算）。"""
    scan_px: int = 0
    """徽章量测**实际扫过的像素数**（D 的 `N_scan_px`）。

    只数 `badge_rect_of` 里 `compute_ink_mask` 处理的那块（行 × 列）；收窄后只扫名字槽位
    左边那一段 —— 药丸必须在槽位左边（判据④），再往后一列都不必看。缓存命中的行是 0
    （这次没扫），与 `ms_*` 同一条口径。"""


def badge_class(width: int) -> int:
    """徽章药丸按**位数档**归类（视频号 1 位 ≈64px、2 位 ≈84px；抖音 ≈71px 单档）。

    比"精确像素相等"稳得多：同一个位数档里 63/64/65 是**同一块药丸**在不同图上的抗锯齿差，
    贴图时按左对齐留 1~2px 原像素即可（同 UI ⇒ 看不出）；跨档（1 位 vs 2 位）才需要 Δ 平移。
    ⚠️ 宽度是 :func:`pill_box` 扩成**整颗药丸**之后的（含左圆头，可越出标注框）。
    """
    if width <= 0:
        return 0
    return 1 if width < 72 else 2 if width < 100 else 3


def plan_count(have: int, min_images: int, ratio: float) -> int:
    """`T = max(min_images, ceil(ratio × have))`。"""
    return max(min_images, math.ceil(ratio * have))


def base_name_is_pure_alnum(b: "Base") -> bool:
    """这张底图的**原始显示名**是不是"纯数字/字母"（见 :data:`PURE_ALNUM_RE`）。

    用 `display_name`（= 该图所在用户在图上真正显示的名字，`:func:`resolve_display_names`
    对账出来的），不是目录名 —— 目录名带 `-张数` 后缀、且 ASCII 目录被跳过后目录名不是
    显示名（`0x0-20` 的显示名是 `0x0`）。名字为空（没对账到）时**不跳**：
    没有证据就别替用户做决定。
    """
    return bool(b.display_name) and PURE_ALNUM_RE.match(b.display_name) is not None


def pill_box(img: np.ndarray, region: inkmask.EraseRegion,
             rect: tuple[int, int, int, int]) -> tuple[tuple[int, int, int, int], int]:
    """把"星+数字"的矩形扩成**整颗药丸**（含左右圆头、药丸底），返回 ``(矩形, 向框左外扩了几 px)``。

    为什么必须扩（用户 2026-10-10 的口径：「等级标得一致，用原图的，修改可以扩展到外部一定范围，
    用户名槽位是和等级标一起算的」）—— 实测抖音 `勇敢的心-4/1.jpg`：

    * 药丸本体 **x 576~647**（宽 72），而 `box_rect` 左沿在 **592** ⇒ **16px 的药丸左圆头在标注框外**，
      `badge_rect_of` 只在框内做列投影 ⇒ 只量到"星+数字"的 592~646（宽 54）；
    * 那 16px 圆头是**药丸底带等级色**（抖音按等级上色：1 级浅蓝、25 级粉星…），
      贴图只盖"星+数字"⇒ 成品 = **底图的圆头 + 目标用户的星/数字**，两截色，一眼看出等级标对不上
      （四张对照条 `/tmp/p8_strip_抖音_a.png` 上 25/8、1/8 两组肉眼可见）；
    * 所以：贴图必须**整颗**从目标自己的原图来，而这要求度量**越出标注框**去量 —— 这正是
      "修改可以扩展到外部一定范围"；"用户名槽位和等级标一起算"= 药丸右沿就是名字槽位的起点，
      向右扩不许吃进 `slot[0]`（名字槽位左沿），一起算才不会互相咬。

    判据用**药丸底的色偏**（`BG_DIST_THRESH`）：药丸底离背景 143~156，面板噪声 ≤34，刀口很干净。
    """
    x0, y0, x1, y1 = rect
    h_img, w_img = img.shape[:2]
    bg = np.asarray(region.bg_color, dtype=np.int16)

    def pill_frac(x: int, r0: int, r1: int) -> float:
        """这一列里"药丸底"的行占比（药丸是把整列底色抬起来的一块，不是只亮笔画）。"""
        col = img[max(0, r0):min(h_img, r1), x].astype(np.int16)
        if not col.size:
            return 0.0
        return float((np.abs(col - bg).max(axis=1) > config.BG_DIST_THRESH).mean())

    left, right = x0, x1
    limit_l = max(0, x0 - BADGE_OUT_PX)
    while left - 1 >= limit_l and pill_frac(left - 1, y0, y1) >= PILL_FRAC_MIN:
        left -= 1
    limit_r = min(w_img, region.slot[0])          # 不许吃进名字槽位（一起算的那一半）
    while right < limit_r and pill_frac(right, y0, y1) >= PILL_FRAC_MIN:
        right += 1
    top, bottom = y0, y1
    limit_t = max(0, y0 - BADGE_VOUT_PX)
    while top - 1 >= limit_t and pill_frac(x0, top - 1, top) >= PILL_FRAC_MIN:
        top -= 1
    limit_b = min(h_img, y1 + BADGE_VOUT_PX)
    while bottom < limit_b and pill_frac(x0, bottom, bottom + 1) >= PILL_FRAC_MIN:
        bottom += 1
    return (left, top, right, bottom), x0 - left


# ---------------------------------------------------------------- 度量（与 measure_one 同一把尺子，去掉 SKIP_EMOJI）

def badge_rect_of(img: np.ndarray, box: tuple[int, int, int, int],
                  region: inkmask.EraseRegion, narrow: bool = False,
                  scan_out: Optional[list] = None) -> tuple[Optional[tuple[int, int, int, int]], int, bool, str]:
    """底图/目标图里**等级徽章药丸**的像素矩形（图像坐标，半开）+ 宽度 + 是否可用 + 理由。

    **走图像锚点，不依赖 `region.badge_found` / 填充率**（2026-10-10 改，见 `expand/扩展张数.md` §12.7）：
    为什么原来那条路不稳 —— `badge_found` 与 `is_badge_like` 都要求**填充率 ≥0.60**，
    而视频号药丸的底色是**半透明叠加**，常常刚过不了 ink 阈值 ⇒ 要么药丸被并进名字槽位
    （`badge_found=False`，`segments[0]` 已经不是药丸了），要么 `segments[0]` 退化成
    名字首字甚至整条名字（实测 428px）。**半透明这件事对"几何"没有影响，只对"填充率"有影响**
    —— 所以换成纯几何判据：

      ① 只在**名字那一行**（`region.glyph_top/bottom`）上做列投影 ⇒ 两行框里第二行的墨不掺进来；
      ② 药丸 = **最左**那几段（缝 ≤ `BADGE_MERGE_GAP_PX` 就并，抗 `✦` 与数字之间的细缝）；
      ③ 宽度落在 `[BADGE_MIN_PX, BADGE_MAX_PX]`（汉字/emoji 26~32px 在下面 ⇒ 认得出"不是药丸"）；
      ④ **必须整体落在名字槽位左边**（`b < region.slot[0]`）—— 这条是"药丸与名字真的分开了"的硬证据，
         也顺手挡掉"名字前两个字在列上粘成一段（40~44px）"这种假阳性；
      ⑤ 与后面的段之间要空 ≥ `BADGE_SEP_MIN_PX` 列 —— 挡掉"粘连段被上限截断后剩下的头一截"。

    ⚠️ 这里量到的是**"星+数字"那一块**（列投影能看到的墨），**不是整颗药丸** —— 药丸的左圆头
    常常落在标注框外（抖音实测 16~22px），在框内根本看不见。所以 `measure_base` 认定成功后会
    再调 :func:`pill_box` 把它扩成整颗药丸（`badge_rect` / `badge_w` 都是扩完之后的）。

    Returns:
        ``(矩形, 名义宽度, 可用?, 理由)`` —— 不可用时矩形为 None，理由可直接进对账 CSV。
    """
    ix0, iy0, ix1, iy1 = region.box_rect
    if ix1 <= ix0 or iy1 <= iy0:
        return None, 0, False, "框退化"
    patch = img[iy0:iy1, ix0:ix1]
    if narrow:
        # D 档「收窄」（`--two-pass`）：药丸**必须整体落在名字槽位左边**（判据④），
        # 判据⑤ 只看紧挨着的那一段从哪一列开始 ⇒ 槽位之后再多扫 `BADGE_SEP_MIN_PX`
        # 列就够，其余列（名字/正文/空白）一个像素都不用读。**结论逐字节相同**：
        # 所有判据用到的只有 `segs[0..k]` 与 `ink[:, a:b+1]`，而 `b < slot[0]` 已经被判据④
        # 保证 —— 收窄只切掉了"永远用不到"的右侧列。
        hi = min(patch.shape[1], max(0, region.slot[0] - ix0) + BADGE_SEP_MIN_PX + 2)
        patch = patch[:, :max(1, hi)]
    if scan_out is not None:
        scan_out.append(int(patch.shape[0] * patch.shape[1]))
    ink = inkmask.compute_ink_mask(patch, np.asarray(region.bg_color, dtype=np.uint8),
                                   config.BG_DIST_THRESH)
    h, _w = ink.shape
    r0 = min(max(0, region.glyph_top - iy0), h)
    r1 = min(max(r0 + 1, region.glyph_bottom - iy0 + 1), h)
    segs = inkmask.column_segments(ink[r0:r1].any(axis=0))
    if not segs:
        return None, 0, False, "名字行上没有墨迹"
    a, b = segs[0]
    k = 1
    while (k < len(segs) and segs[k][0] - b - 1 <= BADGE_MERGE_GAP_PX
           and segs[k][1] - a + 1 <= BADGE_MAX_PX):
        b = segs[k][1]
        k += 1
    width = b - a + 1
    if width < BADGE_MIN_PX:
        return None, width, False, f"最左段只有 {width}px（<{BADGE_MIN_PX}）⇒ 是名字首字"
    if width > BADGE_MAX_PX:
        return None, width, False, f"最左段 {width}px（>{BADGE_MAX_PX}）⇒ 粘连段，不是药丸"
    # 上下：药丸那几列的墨迹行带（取墨量最大的那条）。**先算出来** —— 后面两条否决也要带
    # 着矩形返回：它们的处置是「把这块抹成背景再定位一次」（见 `measure_base`），要坐标。
    bands = inkmask.row_bands(ink[:, a:b + 1].sum(axis=1).astype(int), config.GLYPH_ROW_GAP_PX)
    if not bands:
        return None, width, False, "药丸那几列上没有墨迹行带"
    top, bottom, _mass = max(bands, key=lambda t: t[2])
    rect = (ix0 + a, iy0 + top, ix0 + b + 1, iy0 + bottom + 1)
    if b >= region.slot[0] - ix0:
        return rect, width, False, "最左段压到名字槽位 ⇒ 药丸与名字在列上没分开"
    if k < len(segs) and segs[k][0] - b - 1 < BADGE_SEP_MIN_PX:
        return rect, width, False, "最左段与后面的段粘着 ⇒ 不是独立药丸"
    # 与名字槽位的距离（列，半开区间）：药丸就在名字左边紧挨着，空出大半个药丸宽 ⇒ 那不是药丸。
    # 这条拦的第①类 = 整块背景（实测比例 0.81/0.93，见 `BADGE_GAP_FRAC`）；第②类是槽位起点
    # 偏右（`浅夏梦星-3/2.jpg`），那种图**不该当贴图源**，但同一用户别的图照旧可用。
    gap = region.slot[0] - (ix0 + b + 1)
    if gap > width * BADGE_GAP_FRAC:
        return rect, width, False, f"离名字 {gap}px（>半个药丸宽 {width * BADGE_GAP_FRAC:.0f}）⇒ 背景块/槽位偏右"
    return rect, width, True, "ok"


def resolve_box(path: Path, roi, shape) -> tuple[Optional[tuple[int, int, int, int]], str, list]:
    """决定这张图用哪个标注框 —— **表里给的（`manifest.json` 的 `roi`），不去像素里找**。

    照 `replace_username.resolve_box` 那条线（用户 2026-10-10：「参考换用户名功能的方式」）：
    那条线的原话是"**故意不做自动检测** —— 本实验要量的是 inpainting，不能让框检测的误差混进
    结论"。2026-10-10 20:08 上游把绿框改成**留白框**（`export_user_images.py` 的 `--pad-ratio`
    默认 0.25，画的框比 `roi` 每边大 ~8px）之后这条更硬：框一放宽，框内 mode 色就从"药丸底"
    变成"面板底" ⇒ `glyph_height` 从字墨高（~18）涨成框高（~28）、视频号贴图区左沿冒 1px 碎片
    ⇒ 徽章 58/58 全灭。**表式取框对"画出来的框长什么样"完全免疫。**

    `roi` 缺 / 越界才退回 `detect_box`，并且把来路写进 `Base.box_src`（账里逐类计数，不静默）。

    Returns:
        ``(框, 来路, detect_box 的 flags)`` —— 框为 None 时理由在来路里。
    """
    h, w = shape[:2]
    if roi is not None and len(roi) >= 4:
        x0, y0, x1, y1 = (int(v) for v in roi[:4])
        if 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h:
            return (x0, y0, x1, y1), "manifest.roi", []
        why = f"manifest 的 roi 越界 {(x0, y0, x1, y1)}"
    else:
        why = "manifest 里没有 roi"
    box, _n, flags = batch_run.detect_box(path)
    return box, f"detect_box（{why}）", flags


def _ms_since(t0: float) -> float:
    """`perf_counter` 起点 → 毫秒（**保留小数**：贴徽章常在 1ms 以下，取整就全成 0）。

    评测埋点专用（见 `docs/评测方案.html`）。
    """
    return round((time.perf_counter() - t0) * 1000.0, 3)


# ---------------------------------------------------------------- B 档：量测缓存
# 协议见 `docs/评测方案.html` §3 B：**key 绑死口径**（路径 + size/mtime + roi + 内缩 +
# profile + 代码版本 + 用户名），任一项变即整条失效；缓存只存**像素量测**，不存等级/配对
# （等级只影响选材，放进来会让 C 一调参整批作废）。命中一次省的是整条 `measure_base`
# （读图 + 定位 + 徽章），而不只是其中一段。
CACHE_VERSION = 2
CACHE_PATH = Path(__file__).with_name("measure_cache.json")
_CACHE_FIELDS = ("ok", "reason", "box", "slot_width", "glyph_height",
                 "natural_font_size", "tail_slack", "multi_line", "has_emoji",
                 "join_gap", "struct_note", "text_color", "badge_found", "badge_note",
                 "badge_rect", "badge_w", "badge_cand_w", "badge_out_px",
                 "box_src", "username")
_CACHE_TUPLE_FIELDS = {"box", "badge_rect", "text_color"}


def _product_fingerprint() -> str:
    """产品代码 hash（16 位）。缓存 key 里带上它 ⇒ **改一行画法，旧条目自然不命中**。

    两层都算：本文件的量测/选材，以及 `core/draw_text.py` 的字号拟合与墨迹量测 ——
    后者决定 :func:`fit_font_size_for` 的返回值，只 hash 本文件会让改了拟合语义的缓存
    **静默命中**（B 档跨轮复用时的正确性前提，见 `docs/评测方案.html` §3 B）。
    """
    h = hashlib.sha256()
    h.update(Path(__file__).read_bytes())
    h.update(Path(draw_text.__file__).read_bytes())
    return h.hexdigest()[:16]


# ---- 字号拟合扫描的缓存（B 档第二类像素测量） --------------------------------
# `fit_font_size_for` 在选材里是**逐（名字 × 底图字高）**调用的：一个目标对整池底图各算
# 一次，而字号拟合是「二分字号 → 真画一遍字 → 量墨高」，sample-73 上 1064 次调用 / 7039
# 次 `measure_ink`，占 plan 墙钟的大头（~3.6s/4.4s）。它和底图量测一样是**像素测量**，
# 且只由 (要写的文字, 目标墨高, 字体文件) 决定 —— 不含量级/配对，符合 B 档的口径。
_FIT_CACHE: Optional[dict] = None
_FIT_FP = ""
_FIT_STATS = {"hit": 0, "miss": 0}


def _font_fingerprint() -> str:
    """字体文件 + 字面索引：拟合结果只由它和 (文字, 字高) 决定。取不到 ⇒ 不启用。"""
    try:
        fp = draw_text.resolve_font_path()
        st = Path(fp).stat()
        return f"{fp}:{st.st_size}:{st.st_mtime_ns}:{config.FONT_FACE_INDEX}"
    except Exception:  # noqa: BLE001 - 字体找不到时由 fit_font_size 自己报错，这里只关缓存
        return ""


def enable_fit_cache(entries: dict) -> None:
    """打开进程内拟合缓存。**必须在 `build_pool` 之后**调用（见 cmd_plan 的注释）。"""
    global _FIT_CACHE, _FIT_FP
    _FIT_CACHE, _FIT_FP = entries, _font_fingerprint()
    _FIT_STATS["hit"] = _FIT_STATS["miss"] = 0


def fit_cache_stats() -> dict:
    return dict(_FIT_STATS)


def _fit_cache_key(text: str, glyph_height: int) -> str:
    payload = f"{_FIT_FP}\x00{text}\x00{int(glyph_height)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def measure_cache_key(item: tuple,
                      profile_key: str, roi_inset: int, roi_vinset: int,
                      code_fp: str) -> str:
    raw_path, user_dir, roi, username = item[:4]
    geom = item[4] if len(item) > 4 else None
    try:
        st = Path(raw_path).stat()
        size, mtime_ns = st.st_size, st.st_mtime_ns
    except OSError:                     # 文件不在：也给一个 key（结果会是 READ_FAIL）
        size, mtime_ns = -1, -1
    payload = {"v": CACHE_VERSION, "path": str(Path(raw_path).resolve()),
               "size": size, "mtime_ns": mtime_ns, "user_dir": user_dir,
               "roi": list(roi) if roi else None, "username": username,
               "profile": profile_key, "roi_inset": int(roi_inset),
               "roi_vinset": int(roi_vinset), "code": code_fp}
    # geom 只对需要它的平台进 key —— 不写进非快手平台的 payload，旧缓存条目原样命中
    if geom is not None:
        payload["geom"] = geom
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def load_measure_cache(code_fp: str, path: Path = CACHE_PATH) -> tuple[dict, dict]:
    """读缓存 → ``(底图量测, 字号拟合)``。**代码版本不同 ⇒ 整份丢掉**（key 里也带 code）。

    文件不存在/坏掉/版本不符都当空缓存 —— 缓存是加速手段，不能成为新的失败点。
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return {}, {}
    if data.get("code") != code_fp:
        return {}, {}
    entries = data.get("entries")
    fit = data.get("fit")
    return (entries if isinstance(entries, dict) else {},
            fit if isinstance(fit, dict) else {})


def save_measure_cache(entries: dict, fit: dict, code_fp: str,
                       path: Path = CACHE_PATH) -> None:
    """原子写（tmp + os.replace）：并发/中途失败都不会留下半份缓存。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {k: entries[k] for k in sorted(entries)}
    fit_ordered = {k: fit[k] for k in sorted(fit) if isinstance(fit[k], int)}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"version": CACHE_VERSION, "code": code_fp,
                               "entries": ordered, "fit": fit_ordered},
                              ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, path)


def _base_to_cache(c: Base) -> dict:
    return {k: getattr(c, k) for k in _CACHE_FIELDS}


def _base_from_cache(rec: dict, item: tuple, cached: str) -> Base:
    raw_path, user_dir, _roi, _name = item[:4]
    vals = {}
    for k in _CACHE_FIELDS:
        v = rec.get(k)
        if k in _CACHE_TUPLE_FIELDS and isinstance(v, list):
            v = tuple(v)
        vals[k] = v
    if "ok" not in rec:
        raise ValueError("cache row missing ok")
    cand = Base(path=raw_path, user_dir=user_dir, **vals)
    # geom 不进缓存（它来自交付树，跟着 manifest/comments 走）；命中时从入参补回
    cand.geom = item[4] if len(item) > 4 else None
    cand.cache_key = cached
    cand.cached = True              # 报告/timings 用：这一条没真的读图
    return cand


def measure_base(item: tuple, profile_key: str,
                 roi_inset: int = ROI_INSET, roi_vinset: int = ROI_VINSET,
                 two_pass: bool = False, target_dirs: Optional[frozenset] = None) -> Base:
    """量一张图。

    ⚠️ 与 `batch_produce.measure_one` 的四处**有意不同**：
    ① **不做 `SKIP_EMOJI` 闸** —— 实测（`expand/扩展张数.md` §12.6）390 张被标图绕开闸后
       出片自检 390/390 全干净：emoji 在名字段内 ⇒ 在蒙版内 ⇒ 与名字一起被擦掉、无残留；
    ② 连**徽章几何**一起量（出片要用）；
    ③ 不做 `skip_pure_en`（那在 `collect_material` 里，按目录名判，保留原样）；
    ④ **框来自 `manifest.json` 的 `roi`，不 `detect_box`** —— 见 :func:`resolve_box`。
    """
    raw_path, user_dir, roi, username = item[:4]
    geom = item[4] if len(item) > 4 else None
    path = Path(raw_path)
    cand = Base(path=raw_path, user_dir=user_dir, username=username)
    cand.geom = geom
    t0 = time.perf_counter()
    try:
        img = np.array(Image.open(path).convert("RGB"))
    except Exception as exc:  # noqa: BLE001 - 读图失败原因很杂，统一上报
        cand.ms_decode = _ms_since(t0)
        cand.ok, cand.reason = False, f"READ_FAIL:{type(exc).__name__}"
        return cand
    cand.ms_decode = _ms_since(t0)
    t0 = time.perf_counter()
    box, box_src, flags = resolve_box(path, roi, img.shape)
    cand.box_src = box_src
    if box is None:
        cand.ms_locate = _ms_since(t0)
        cand.ok, cand.reason = False, (flags[0] if flags else "NO_BOX")
        return cand
    profile = platforms.profile_for(profile_key)
    try:
        region = inkmask.find_erase_region(img, box, inset=inset_for(box_src, roi_inset),
                                          vinset=vinset_for(box_src, roi_vinset),
                                          profile=profile, geom=geom)
    except (inkmask.RegionNotFoundError, ValueError):
        cand.ms_locate = _ms_since(t0)
        cand.ok, cand.reason = False, "LOC_NONE"
        return cand
    except Exception as exc:  # noqa: BLE001
        cand.ms_locate = _ms_since(t0)
        cand.ok, cand.reason = False, f"CRASH:{type(exc).__name__}"
        return cand
    cand.ms_locate = _ms_since(t0)
    if region.slot_width <= 0:
        cand.ok, cand.reason = False, "SLOT_ZERO"
        return cand

    cand.box = box
    cand.slot_width = region.slot_width
    cand.glyph_height = region.glyph_height
    # 字号 = 把**这张图自己那个名字**的墨高对到本图字高（不是拿中文样例去对，见
    # `fit_font_size_for`）：名字是纯数字/纯英文时，这两者差 20% 以上。
    cand.natural_font_size = fit_font_size_for(username, region.glyph_height)
    cand.tail_slack = max(0, region.max_name_width - region.slot_width)
    cand.multi_line = region.multi_line
    cand.has_emoji = bool(region.has_emoji)
    cand.join_gap = int(region.join_gap)
    cand.text_color = (tuple(int(v) for v in region.text_color)
                       if region.text_color is not None else None)
    # 结构闸（用户 2026-10-10「没有擦除干净」的第二处）：缝的两头都不正常 ——
    # ≤ ``JOIN_GAP_MIN`` 是"末字碎段被当成冒号"（槽位少一个字 ⇒ 出片留残字），
    # > ``JOIN_GAP_MAX`` 只对抖音（`saturation_run`，冒号无几何校验）是"冒号被并进槽位"
    # （出片丢冒号）。**只剔底图池**，用户自己的图照算（贴图源与名字无关）—— 见 `Base.struct_note`。
    if cand.join_gap < JOIN_GAP_MIN:
        cand.struct_note = f"COLON_GLUED:{cand.join_gap}px（缝 ≤{JOIN_GAP_MIN - 1} ⇒ 末字碎段被当成冒号）"
    elif profile.name_strategy == "saturation_run" and cand.join_gap > JOIN_GAP_MAX:
        cand.struct_note = f"COLON_ABSORBED:{cand.join_gap}px（抖音缝 >{JOIN_GAP_MAX} ⇒ 冒号被并进槽位）"
    t0 = time.perf_counter()
    # D 档（`--two-pass`）：**第二遍（徽章）只对能进底图池的图跑**。结构闸已经在上面判完，
    # 过不了结构闸的图永远不会被 `allocate` 选中 ⇒ 它们的徽章只有两处会用到：
    # ① 当**贴图源**（只限目标用户自己的图）；② 参与 `badge_families` / `_pill_w` 的投票。
    # 所以这里**只延后非目标用户**的结构坏图（永远不会是贴图源）；目标用户自己的图
    # **每张都量**（bug-284 的红线：只许延后，不许跳过）。
    if two_pass and cand.struct_note and target_dirs is not None \
            and user_dir not in target_dirs:
        return cand
    scan: list[int] = []
    cand.badge_checked = True
    rect, width, found, why = badge_rect_of(img, box, region, narrow=two_pass, scan_out=scan)
    # ⚠️ `badge_rect` / `badge_w` **只在认定成功时**才落 —— `badge_rect_of` 在被否的两条路上
    # 也会把候选矩形带回来（那样"抹药丸重定位"实验才有坐标）。实测踩过：候选矩形 = **名字槽位**
    # 本身，漏到计划里之后出片时把贴图**盖在新写好的名字上**（视频号 `好__-13/11.jpg`）。
    if found and rect:
        # 认定成功后再把"星+数字"扩成**整颗药丸**（含框外的左圆头）—— 见 :func:`pill_box`。
        rect, cand.badge_out_px = pill_box(img, region, rect)
        width = rect[2] - rect[0]
        if width > BADGE_MAX_PX:
            found, why = False, f"整颗药丸 {width}px（>{BADGE_MAX_PX}）⇒ 扩到了别的东西上"
    cand.badge_found, cand.badge_note = found, why
    cand.badge_rect = rect if found else None
    cand.badge_w = width if found else 0
    cand.badge_cand_w = width
    cand.scan_px = int(sum(scan))
    cand.ms_badge = _ms_since(t0)
    return cand


def inset_for(box_src: str, roi_inset: int = ROI_INSET) -> int:
    """框**横向**内缩量 —— 见 :data:`ROI_INSET`：`manifest.roi` 是紧贴值（不含描边），描边框才内缩 6。"""
    return roi_inset if box_src == "manifest.roi" else config.BOX_INSET


def vinset_for(box_src: str, roi_vinset: int = ROI_VINSET) -> int:
    """框**纵向**内缩量 —— 见 :data:`ROI_VINSET`：`manifest.roi` 纵向 0（放字带走），描边框才内缩 6。

    ⚠️ 规划（`measure_base`）与出片（`render_one` → `process_image`）必须用**同一个值**，
    否则账上量到的字高/槽位与实际擦写用的框不是同一处（bug-300 同型）。
    """
    return roi_vinset if box_src == "manifest.roi" else config.BOX_INSET


def box_of_job(job: dict) -> Optional[tuple[int, int, int, int]]:
    """出片时用的框 = **规划时量的那个框**（`base_roi`，来自 `manifest.json`）。

    为什么不重新 `detect_box`：规划与出片必须用**同一个框**，否则"账上量到的槽位/徽章"
    与"实际擦写用的框"就可能不是同一处（留白框一放宽就会这样）。老计划没有 `base_roi`
    这一项 ⇒ 退回 `detect_box`（口径与旧版一致），不静默：调用方拿不到框会报 NO_BOX。
    """
    roi = job.get("base_roi") or []
    if len(roi) == 4:
        return (int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3]))
    box, _n, _f = batch_run.detect_box(Path(job["base"]))
    return box


def box_src_of_job(job: dict) -> str:
    """`box_of_job` 给的框的来路（决定内缩量，见 :func:`inset_for`）。"""
    return "manifest.roi" if len(job.get("base_roi") or []) == 4 else "detect_box"


def measure_all(paths: list[tuple],
                profile_key: str, workers: int, roi_inset: int = ROI_INSET,
                roi_vinset: int = ROI_VINSET,
                cache: Optional[dict] = None, cache_code: str = "",
                two_pass: bool = False,
                target_dirs: Optional[frozenset] = None) -> tuple[list[Base], dict]:
    """并行量一批图；`cache` 给了就先查缓存，只把未命中的丢进进程池。

    返回 `(候选, 统计)`。统计里的 `hit + miss == len(paths)`（**分母 = 所有需要量测的
    次数**，见 `docs/评测方案.html` §3.1），`cached_bad` 是缓存行读不出来的条数（会重测）。
    """
    out: list[Optional[Base]] = [None] * len(paths)
    stats = {"hit": 0, "miss": len(paths), "cached_bad": 0, "enabled": cache is not None}
    if cache is None:
        todo = list(enumerate(paths))
        keys: list[str] = []
    else:
        keys = [measure_cache_key(it, profile_key, roi_inset, roi_vinset, cache_code)
                for it in paths]
        todo = []
        for i, (it, k) in enumerate(zip(paths, keys)):
            rec = cache.get(k)
            if rec is not None:
                try:
                    out[i] = _base_from_cache(rec, it, k)
                    stats["hit"] += 1
                    continue
                except Exception:  # noqa: BLE001 - 坏缓存行 = 当作没命中，重测
                    stats["cached_bad"] += 1
            todo.append((i, it))
        stats["miss"] = len(todo)

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(measure_base, it, profile_key, roi_inset, roi_vinset,
                            two_pass, target_dirs): i
                for i, it in todo}
        for f in as_completed(futs):
            i = futs[f]
            try:
                rec = f.result()
            except Exception as exc:  # noqa: BLE001
                print(f"  量图崩了：{exc!r}", flush=True)
                continue
            out[i] = rec
            if cache is not None and keys:
                cache[keys[i]] = _base_to_cache(rec)
    return [c for c in out if c is not None], stats


# ---------------------------------------------------------------- 目标名字的对账与选材

def name_class(name: str) -> str:
    """名字的**字形类** —— :data:`WIDTH_K_FALLBACK` 的键。

    只分两类就够：实测（§23）含 ASCII 字母的名字整体 k 小一档（我方拉丁相对更宽），
    数字与中文同档（无字母那批 1.474 / 1.392 里数字名就在里面）。
    """
    return "latin" if re.search(r"[A-Za-z]", name) else "cjk"


def fit_font_size_for(text: str, glyph_height: int) -> int:
    """**这串字**写在这张底图上该用多大字号 = 把**它自己的墨高**对到这张底图的字高。

    ⚠️ 不能拿固定样例去对（`draw_text.fit_font_size` 的默认样例是中文「爱国者」）：
    **中文/数字/英文三类的墨高不一样** —— 我们字体里中文墨高 = em、数字只有 0.75em、
    英文 x 高 ~0.6em，而平台把数字画得**几乎和中文同高**（实测 `594315972` 九段全 20px 高、
    `莉莉123` 的「123」19px vs 中文 19px）⇒ 用中文样例给定数字/英文名的字号，写出来比
    平台**矮 18%~25%**（`expand/扩展张数.md` §23）。
    """
    if glyph_height <= 0:
        return 0
    sample = text or "爱国者"
    if _FIT_CACHE is None or not _FIT_FP:
        return draw_text.fit_font_size(glyph_height, sample=sample).size
    key = _fit_cache_key(sample, glyph_height)
    hit = _FIT_CACHE.get(key)
    if isinstance(hit, int):
        _FIT_STATS["hit"] += 1
        return hit
    size = draw_text.fit_font_size(glyph_height, sample=sample).size
    _FIT_CACHE[key] = int(size)
    _FIT_STATS["miss"] += 1
    return size


def platform_width(name: str, font_size: int, wk: Mapping[str, float]) -> int:
    """这个名字**平台会画多宽** = ``k[字形类] × 我们的墨宽``（**只用于名字对账**）。

    名字对账的两边必须同尺度：`self_slot` 是**平台**画的像素，而候选宽是**我们**的像素。
    不过 k 就等于把每个候选都算窄三成 ⇒ "多留一个数字"的候选反而更贴槽位（bug-292）。
    出片**不走这条路**（写出来就是我们自己的宽度，见 :func:`expected_width`）。
    """
    if not name or font_size <= 0:
        return 0
    return int(round(wk.get(name_class(name), 1.0) * bp._name_width(name, font_size)))


def calibrate_width_k(cands: list["Base"]) -> tuple[dict[str, float], dict[str, int]]:
    """从**本语料**标定平台字形比 k，**按字形类分开**（见 :data:`WIDTH_K_FALLBACK`）。

    只采**名字不以数字开头**的图：那些图上显示名 = 用户名（:data:`NAME_STRIP_DIGITS`
    只动"数字 + 非数字"的名字）⇒ `槽位宽 ÷ 我们画的宽` 就是该类 k 的直接测量。

    Returns:
        ``({类: k}, {类: 样本户数})``；某类样本 < :data:`WIDTH_K_MIN_N` 时给兜底值。
    """
    buckets: dict[str, list[float]] = defaultdict(list)
    for c in cands:
        if not c.ok or c.slot_width <= 0 or not c.natural_font_size:
            continue
        nm = c.username
        if not nm or re.match(r"^\d", nm):   # 前缀数字可能被平台删了 ⇒ 宽度不可比
            continue
        exp = bp._name_width(nm, c.natural_font_size)
        if exp > 0:
            buckets[name_class(nm)].append(c.slot_width / exp)
    kw: dict[str, float] = {}
    n: dict[str, int] = {}
    for cls, vals in sorted(buckets.items()):
        n[cls] = len(vals)
        kw[cls] = (float(np.median(vals)) if len(vals) >= WIDTH_K_MIN_N
                   else WIDTH_K_FALLBACK[cls])
    return kw, n


def resolve_name(username: str, self_slot: int, self_gh: int,
                 wk: Optional[Mapping[str, float]] = None, pill_w: int = 0) -> tuple[str, str]:
    """把 `manifest.username` 对成**图上显示的那个昵称**（见 :data:`NAME_STRIP_DIGITS`）。

    尺子 = :func:`platform_width` = **平台会画多宽**（我们字体的墨宽 × 该类 k）。
    候选**一律同一个字号**（按用户名本身定）—— 逐候选各自 fit 等于让"只有矮字形"的候选
    白拿一个更大的字号（`XW` 全字母 ⇒ 按 0.72em 对目标高），几个候选就不在同一把尺子上了。

    ⚠️ 为什么必须过 k（bug-292）：`self_slot` 是**平台**画的像素，我们字体的墨宽比平台窄
    25%~30% ⇒ 直接比等于把每个候选都算窄三成，"多留一个数字"反而更贴槽位。实测
    `11天空` 的底图上槽位 55px（平台画的是 `天空` —— 名字行**列分段**只有 2 段 27×21，§23）：
    纯 Noto 尺子下 `11天空`=61（差 6）、`天空`=39（差 16）⇒ 挑中 `11天空` ✗；
    过 k（1.44）⇒ `11天空`=88（差 33）、`天空`=56（差 1）⇒ 挑中 `天空` ✅。

    Args:
        username:  `manifest.username`（可能带等级徽章的数字前缀）。
        self_slot: 该用户**自己图上**槽位宽的中位数（像素）。
        self_gh:   该用户**自己图上**名字墨高的中位数（像素）—— 逐候选定字号用。
        wk:        :func:`calibrate_width_k` 的 ``{类: k}``；None ⇒ 不给 k（尺子退回纯 Noto，
                   只在标定不出来的病态语料上发生，账里会打出来）。
        pill_w:    该用户**自己图上量到的等级药丸宽**（中位数；0 = 一张都没量到）。尺子对不上
                   时的**直接证据**（图上有丸 ⇒ 前缀数字是丸里的等级）；见 :data:`NAME_STRIP_DIGITS`。

    Returns:
        ``(要写的名字, 说明)``；没删过东西时说明为空。
    """
    if not NAME_STRIP_DIGITS or self_slot <= 0:
        return username, ""
    wk = wk or {}
    m = re.match(r"^(\d+)(\D.*)$", username)  # 纯数字昵称（`193`）不删：后面没东西可留
    if not m:
        return username, ""
    digits, rest = m.group(1), m.group(2)
    # ⚠️ **所有候选共用同一个字号**（按用户名本身定）：逐候选各自 fit 会让"只有矮字形"的
    # 候选拿到更大的字号（`XW` 全是字母 ⇒ 按 0.72em 对目标高 ⇒ 字号比 `5XW` 大一截），
    # 那样几个候选的宽度就**不在同一把尺子上**，比出来的差没有意义。
    fs = fit_font_size_for(username, self_gh)

    def _w(s: str) -> int:
        return platform_width(s, fs, wk)

    best, best_gap = username, abs(_w(username) - self_slot)
    for k in range(1, len(digits) + 1):
        cand = digits[k:] + rest
        gap = abs(_w(cand) - self_slot)
        if gap < best_gap:
            best, best_gap = cand, gap
    if best != username:
        return best, (f"{username} → {best}（自己图上槽位宽 {self_slot}px、"
                      f"平台尺子（k={wk.get(name_class(best), 1.0):.2f}）差 {best_gap:.0f}px）")
    if pill_w > 0:
        # 尺子说"留着更贴"也可能是尺子失灵（表情占槽位 / `&` 偏窄 / 药丸并进槽位，见
        # NAME_STRIP_DIGITS 的表）⇒ 用**图上有没有药丸**这条直接证据兜底。
        return rest, (f"{username} → {rest}（自己图上有等级药丸（宽 {pill_w}px）⇒ "
                      f"开头那 {len(digits)} 个数字是丸里的等级，不是昵称的一部分）")
    return username, ""


def fit_ratio(slot_width: int, name: str, natural_font_size: int) -> float:
    """`槽位宽 ÷ 该图自然字号下显示名的墨迹宽`（见 :data:`NAME_FIT_LO`）。

    用**该图自己的自然字号**归一化，是为了避开"同名跨素材字号不一致"这个干扰
    （mock 线的口径是字号不缩 ⇒ 有的图文字比语料众数字号大，直接拿语料字号算宽度会误判）。
    """
    if slot_width <= 0 or natural_font_size <= 0 or not name:
        return 0.0
    exp = bp._name_width(name, natural_font_size)
    return slot_width / exp if exp else 0.0


def expected_width(name: str, font_size: int) -> int:
    """这个名字写在这张底图上**该占多宽**（`expand/扩展张数.md` §15 的口径）。

    = **我们字体在该字号下的墨宽**（`bp._name_width`）—— 也就是**出片真写出来的宽度**。
    字号是"**这张底图（+ 这串字）自己的**"：规划时 :func:`fit_font_size_for`，
    出片时 `process_image(font_size=None, fit_text=名字)` 用同一个函数 ⇒ **同源**。

    ⚠️ 名字对账**不用这个函数**（那边要用平台尺度，见 :func:`platform_width`）：这里是
    我们画的宽（比平台窄 25%~30%），拿去比 `self_slot` 会把候选都算窄三成（bug-292）。
    """
    if not name or font_size <= 0:
        return 0
    return int(round(bp._name_width(name, font_size)))


def apply_name_gate(cands: list[Base], names: dict[str, str]) -> Counter:
    """名字对账闸：把"槽位盖不住显示名"的图标成不可用（:data:`NAME_FIT_LO` / :data:`NAME_FIT_HI`）。

    必须在 `resolve_display_names` 之后、`allocate` 之前跑 —— 名字是**按用户**对账出来的
    （见 :data:`NAME_STRIP_DIGITS`），对账本身又用该用户自己图上槽位的中位数。

    Returns:
        计数（`比例过低` / `比例过高` / 过闸）。
    """
    stat: Counter = Counter()
    for c in cands:
        if not c.ok or c.slot_width <= 0:
            continue
        name = names.get(c.user_dir, "")
        c.display_name = name
        # 闸比的是"**这张图上写这个名字**会不会被切"⇒ 字号按**这串名字**定（`fit_font_size_for`），
        # 不用 `natural_font_size`（那是这张图**它自己**名字的字号）。
        c.name_ratio = fit_ratio(c.slot_width, name,
                                 fit_font_size_for(name, c.glyph_height))
        if not c.name_ratio:
            continue
        if c.name_ratio < NAME_FIT_LO:
            c.ok, c.reason = False, f"NAME_CUT:{c.name_ratio:.2f}"
            stat["比例过低（名字被切短 ⇒ 会留残字）"] += 1
        elif c.name_ratio > NAME_FIT_HI:
            c.ok, c.reason = False, f"NAME_WIDE:{c.name_ratio:.2f}"
            stat["比例过高（槽位圈进了别的墨）"] += 1
        else:
            stat["过闸"] += 1
    return stat


def badge_families(cands: list[Base], share: float = 0.25) -> list[int]:
    """从**本语料**实际认定出来的徽章宽里学出"合法宽度族"的中心（同 `corpus_font_size` 的思路）。

    为什么要学而不是写死常量：药丸宽是**平台 UI + 位数**决定的（抖音 70~72 恒定；视频号 1 位 63~65 /
    2 位 82~85），写死就得改 `platforms.py`（老路径一行不改）；而且实测有**少数派误认**：
    "名字头两个字在列上粘成一段"（抖音 44/66px）、"框左沿 + 名字首字"（视频号 52px）会混进来。
    众数族一卡就干净了，还不影响真·两位数的用户。

    ⚠️ 投票按**用户**数，不按**图数**（bug-286）：一个用户有几十张图时，图数投票等于让
    他一个人把直方图抬起来。实测视频号把纯 ASCII 目录的图也量进来之后（多 94 张），
    1 位数的族（63~65）占不到 top 的 25% 就被整个丢掉 —— 7 个**真用户**连带按「徽章宽不在
    合法族」判死、出片 96 → 80。按用户投票时那个族有 4/12 的用户，稳稳留下。
    一族是不是真的存在，本来就是"有多少**用户**是这个宽度"这件事。
    """
    per_user: dict[str, Counter] = defaultdict(Counter)
    for c in cands:
        if c.badge_found and c.badge_w > 0:
            per_user[c.user_dir][c.badge_w] += 1
    # 每个用户一票，投他自己图上的众数宽（票数相同时取窄的，保证确定性）
    hist = Counter(min(cc.items(), key=lambda kv: (-kv[1], kv[0]))[0]
                   for cc in per_user.values())
    if not hist:
        return []
    top = max(hist.values())
    keep = sorted(w for w, n in hist.items() if n >= share * top)
    fams: list[list[int]] = []
    for w in keep:
        if fams and w - fams[-1][-1] <= 4:
            fams[-1].append(w)
        else:
            fams.append([w])
    out = []
    for f in fams:
        tot = sum(hist[w] for w in f)
        out.append(round(sum(w * hist[w] for w in f) / tot))
    return out


def apply_badge_gate(cands: list[Base], fams: list[int]) -> Counter:
    """徽章宽不在任何"合法族"±`BADGE_W_TOL` 里的 ⇒ 那是误认（名字碎片/框边），标不可用。"""
    stat: Counter = Counter()
    for c in cands:
        if not c.ok or not c.badge_found:
            continue
        if fams and not any(abs(c.badge_w - f) <= BADGE_W_TOL for f in fams):
            c.ok, c.reason = False, f"BADGE_W_ODD:{c.badge_w}"
            stat["徽章宽不在合法族（误认）"] += 1
        else:
            stat["徽章可用"] += 1
    return stat


def user_text_color(self_recs: list[Base]) -> tuple[Optional[tuple[int, int, int]], int, int]:
    """一个用户的名字色 = **他自己图上量到的那些核心色的逐通道中位数**。

    返回 ``(色, 样本数, 最大偏离)``。

    用户 2026-10-11：「用户名颜色有问题，建议固定用户颜色」。两件事分开看：

    * **为什么会不对**：名字色过去是**逐张**在这张底图上量的，而取色窗口是名字段的所有墨迹 ——
      名字段里混进一个 emoji，它的彩色就被平均进来（实测 `神秘人-3/3.jpg`：名字段里有 🎇，
      量出来 `(175,223,196)`，而该平台上干净的名字是 `(156,220,240)`）⇒ 同一个用户在不同底图上
      颜色不一样。那一条已经在 `inkmask` 里从根上修了（取色窗口剔掉 emoji 段）。
    * **为什么要钉死**：与徽章同一条口径（`expand/扩展张数.md` 口径④）——**同一个用户的东西用他自己图上的**。
      底图是别人的图，别人的名字色凭什么决定这个用户的新名字色？逐张量还会把底图的量测噪声
      带进出片（抗锯齿、擦除边缘），钉死之后同一个用户在这批片里颜色恒定。

    **逐通道取中位数**，不取众数：样本在压缩噪声下是**连续**的 —— 实测抖音 `肘死你-133`
    自己 127 张图上量到 **62 种**取值，众数只有 9 票，挑出来的那个色凭什么代表这个用户？
    中位数对噪声稳健（`_core_color_of` 取的是"最亮那 25% 墨迹像素的均值"，
    视频压缩 + 抗锯齿让同一块 UI 逐张差 1~3）。**不取均值**：个别底图的 emoji/异物样本会把均值拽走。
    取完中位数再**剔一轮离群样本**（偏离 > ``COLOR_DEV_MAX``，见那里的账），剔完重取一次。

    Args:
        self_recs: 该用户**自己**那些可用图（`by_dir[user_dir]` 里 ok 且槽位非 0 的）。

    Returns:
        ``(色, 样本数, 最大偏离)``；``最大偏离`` = 所有样本里、离中位数最远的那个差多少
        （逐通道取最大绝对值）—— 它大就说明量测不稳，得看得见。一张都没量到色时 ``(None, 0, 0)``。
    """
    cols = [c.text_color for c in self_recs if c.text_color]
    if not cols:
        return None, 0, 0
    arr = np.asarray(cols, dtype=np.float64)
    med = np.median(arr, axis=0)
    # 再剔一轮**离群样本**：噪声 ≤ ``COLOR_DEV_MAX``，超出的就是这一张的取色被污染了
    # （名字段里混进 emoji、或定位把正文并进了名字段 —— 实测 `勇往直前-3/3.jpg`：
    # 量出 `(190,186,166)`，而同用户另两张是 `(160,221,240)`）。剔完剩不到一半就**认了**：
    # 样本自己就分成两派时，中位数（两边都不占）比任一派都中立，偏离照旧在报告里点名。
    keep = np.abs(arr - med).max(axis=1) <= COLOR_DEV_MAX
    if keep.sum() >= max(1, len(cols) // 2):
        med = np.median(arr[keep], axis=0)
    color = tuple(int(v) for v in np.round(med))
    dev = int(np.abs(arr - med).max())
    return color, len(cols), dev


def allocate(target: Target, name: str, pool: list[Base], *,
             base_cap: int, per_target_per_base: int,
             need: int, rng: random.Random, want_badge_w: int, require_base_badge: bool,
             pair_badge_absent: bool = False,
             dir_use: Counter, pair_use: Counter, bucket_use: Counter,
             path_use: Counter, skip_stat: Optional[Counter] = None,
             level_of: Optional[Mapping[str, Optional[str]]] = None,
             t_level: Optional[str] = None,
             level_soft: bool = False,
             excluded_out: Optional[list] = None) -> list[tuple[Base, int]]:
    """挑 `need` 张底图（C1 硬约束 + |w−槽位| 打分 + C3/C4/C5）。

    名字宽度**逐底图、逐名字**算：:func:`expected_width` + :func:`fit_font_size_for`
    （= 把**这串名字**的墨高对到**这张底图**的字高）。这个式子与出片**同源**：
    `render_one` 就是 `process_image(font_size=None, fit_text=名字)`（见 §23）。

    ⚠️ 2026-10-11「全盘照上游」之后这条式子**偏窄**（纯 Noto 宽、没有 `adv_k`，见
    :func:`expected_width`）⇒ 硬约束比出片实际需要的更松：更少的底图被判"名字出框"。
    反过来，`process_image` 真排版时若发现放不下会**截断名字** —— 那由 `render_one` 的
    `TRUNCATED` 护栏拦下、不出片，不会悄悄交付一张写着别人半个名字的图。
    这笔账见 `expand/扩展张数.md` §22。

    底图池的**四条排除**（都在这里一次说完）：原名字纯数字/字母（:data:`PURE_ALNUM_RE`）、
    槽位/冒号结构坏了（:attr:`Base.struct_note`）、徽章没认定、徽章宽对不上目标（C1）。
    被排除的计数进 `skip_stat`（每个目标一次）。

    ``pair_badge_absent``（**无丸配无丸**，用户 2026-10-12 定案）：目标自图**定不到徽章**时，
    底图也必须是**没徽章**那张 —— 出片只换名字、徽章位一个像素都不碰。为什么要这条：
    这份语料里「带丸的图」是极少数（App 语料 229 张里只有 4 张），而**大多数用户本来就是没有丸的**
    （行首直接是名字）⇒ 对他们而言「底图必须有一颗丸」这条闸既没有素材、也不是正确产物：
    正确产物就是**一颗丸都没有的图**。反过来，**放开口径④ 让它顶着底图那颗丸是错的**
    （那是别人的等级/粉丝数）。所以这里不是放宽，是**配对**：有丸配同档的有丸、无丸配无丸。
    默认 ``False`` ⇒ 与加这条之前**逐字节相同**（`want_badge_w` 为 0 时底图照旧必须有认定的徽章）。
    ⚠️ 这个形参由**调用方算好**（= 目标**确实没有**贴图源 **且** 配置开了这条口径），
    不是把配置键直接传进来 —— 否则「目标有丸 + `--allow-badge-delta`」（`want_badge_w` 也是 0）
    会被误判成"无丸"，把该贴的丸丢掉。

    C 档（`--level-pair`，默认关）：``level_of`` 是**逐图等级**（键 = 底图路径，见
    :func:`build_level_index`），``t_level`` 是目标等级。三级链在**宽度闸之后、徽章闸之前**
    生效（等级不是宽度的替代品 —— `BADGE_W_TOL` 在每一层都仍然生效）：

      ① 目标已知 + 底图已知且相同 ⇒ 首选（``same``）；
      ② 目标已知 + 底图未知 ⇒ 允许，退回宽度判据（``unknown``）；
      ③ 目标已知 + 底图已知且不同 ⇒ 排除（``excluded``，记进 ``excluded_out`` 供审计）；
      ④ 目标未知 ⇒ 整条链退化为现状（``degraded``，排序与关 C 时逐项相同）。

    排序先按层级、再按原成本 ⇒ 第 1 层总是排在第 2 层前面；关 C 时层级恒 0，与旧排序相同。

    Returns:
        ``([(底图, 时间桶), …], 候选池大小)``。候选池 = 过完所有硬闸的底图数（``len(fits)``），
        与 `picked` 的差是 C5 上限与去重造成的 —— 评测的 `R_blank` 用这个数（见 `docs/评测方案.html` §3.2）。
    """
    fits: list[tuple[int, float, Base, int]] = []
    for b in pool:
        if b.user_dir == target.user_dir or not b.ok:
            continue
        if base_name_is_pure_alnum(b):          # 用户 2026-10-10：原名字纯数字和字母 ⇒ 整张跳过
            if skip_stat is not None:
                skip_stat[b.path] = 1
            continue
        if b.struct_note:                       # 槽位/冒号结构坏了 ⇒ 整张跳过（见 `Base.struct_note`）
            if skip_stat is not None:
                skip_stat[b.path] = 1
            continue
        # 宽度按**这张底图 + 这串名字**逐对算（出片同源：`render_one` 传 `fit_text=name`）
        w = expected_width(name, fit_font_size_for(name, b.glyph_height))
        if not w:                                        # 名字/字号量不出来：不能用
            continue
        if w > b.slot_width + b.tail_slack:              # 硬约束：不出框
            continue
        # C 档：等级只在「能判断」时加一层排序；第 3 层**在此处判定**（先于徽章闸 ——
        # 明确不同就是明确不同，宽度/徽章都不该把它救回来）。
        b_level = level_of.get(str(b.path)) if level_of is not None else None
        tier = pair_tier(t_level, b_level) if level_of is not None else "same"
        if tier == "excluded":
            if level_soft:
                tier = "cross"          # 事后变体（`--level-pair-soft`）：明确不同**只排后**，不排除
            else:
                if excluded_out is not None:
                    excluded_out.append({"base": b, "b_level": b_level, "w_b": w})
                continue
        if want_badge_w:
            if not b.badge_found or abs(b.badge_w - want_badge_w) > BADGE_W_TOL:
                continue                                      # C1：底图必须有一颗**同档**的丸（像素级）
        elif pair_badge_absent:
            if b.badge_found:
                continue                                      # 无丸配无丸：底图**必须没有**丸，否则就是顶着别人的
        elif not b.badge_found:
            continue                                          # 底图徽章没认定：贴图没地方贴
        if require_base_badge and b.badge_rect is None and not pair_badge_absent:
            continue                                          # 徽章定位不到 ⇒ 贴图没地方贴，不出这张
                                                              # （无丸配无丸这条路要的正是"没有徽章位"的底图）
        bucket = -1 if not b.t else (b.t % 86400) * TIME_BUCKETS // 86400
        cost = float(abs(w - b.slot_width))              # 越贴原槽位越好（框右留白越小）
        cost += 40.0 * dir_use[b.user_dir]               # C3：少用同一个"原始用户"（软）
        cost += 25.0 * pair_use[(target.user_dir, b.path)]  # C5：每人同一底图 ≤ N
        cost += 12.0 * bucket_use[(target.user_dir, bucket)]  # C4：时段分散
        if b.name_ratio > NAME_FIT_WARN:                  # 槽位偏宽（多行/emoji/圈进别的墨）：软惩罚
            cost += 60.0 * (b.name_ratio - NAME_FIT_WARN)
        cost += rng.random() * 3.0
        fits.append(({"same": 0, "degraded": 0, "unknown": 1, "cross": 2}.get(tier, 0),
                     cost, b, bucket))
    fits.sort(key=lambda t: (t[0], t[1]))
    picked: list[tuple[Base, int]] = []
    used_paths: Counter = Counter()
    for _rank, _cost, b, bucket in fits:
        if len(picked) >= need:
            break
        if path_use[b.path] >= base_cap:                  # C5：一张**底图**最多被 N 个用户借（硬）
            continue
        if used_paths[b.path] >= per_target_per_base:     # C5：同一目标不用同一张底图超过 M 次
            continue
        picked.append((b, bucket))
        used_paths[b.path] += 1
        path_use[b.path] += 1
        dir_use[b.user_dir] += 1
        pair_use[(target.user_dir, b.path)] += 1
        bucket_use[(target.user_dir, bucket)] += 1
    return picked, len(fits)


# ---------------------------------------------------------------- 出片（P2：徽章一起换）

def paste_badge(result: np.ndarray, rect: tuple[int, int, int, int],
                sprite: np.ndarray) -> tuple[bool, str]:
    """把目标用户自己的徽章贴图**覆盖**（不是擦、不是 inpaint）到底图的徽章位上。

    同平台同 UI ⇒ 贴图与底图同分辨率同抗锯齿；**同宽时零平移**（抖音 54px 恒定）。
    尺寸不一致时**不硬贴**（会露边）——除非是"底图徽章更宽"且差值可由尾部平移吸收，见 `--badge-shift`。
    """
    x0, y0, x1, y1 = rect
    sh, sw = sprite.shape[:2]
    bh, bw = y1 - y0, x1 - x0
    if y0 < 0 or x0 < 0 or y1 > result.shape[0] or x1 > result.shape[1]:
        return False, "徽章矩形越界"
    if abs(sh - bh) > 2 or abs(sw - bw) > 3:
        return False, f"尺寸差太多 底图 {bw}×{bh} 贴图 {sw}×{sh}"
    h, w = min(sh, bh), min(sw, bw)
    result[y0:y0 + h, x0:x0 + w] = sprite[:h, :w]   # 左上对齐：差的那 1~2px 留底图原像素
    return True, ("完全覆盖" if (h, w) == (bh, bw) else f"差 {bw - w}×{bh - h}px 留底图原像素")



def render_one(base: Base, name: str, sprite: Optional[np.ndarray], *,
               profile_key: str, roi_inset: int = ROI_INSET,
               roi_vinset: int = ROI_VINSET,
               text_color: Optional[tuple[int, int, int]] = None) -> dict:
    """出一张扩展图 = **上游 `replace_username.process_image`** + 贴徽章。

    「全盘照上游」（用户 2026-10-11）：本函数 2026-10-10/11 上午做的三处偏离全部删掉，
    出片口径与 `batch_run.py` 逐字一致 —— 上游那条路自己就是**直接调 `process_image`**：

    * 字号：**逐张跟这张底图的 `region.glyph_height`**（`font_size=None` ⇒
      `draw_text.plan_name` ⇒ `fit_font_size`）—— 不再按语料标定一个平台常数；
    * 墨宽：**我们字体在那张图字号下的原样墨宽**（`measure_ink`）—— 不再 `adv_k` 横向重采样；
    * 擦除：**`inkmask` 原蒙版** —— 不再上下多擦 `ERASE_EXT_PX` 行。

    ⚠️ 三处偏离各自对应一个用户报过的问题（2026-10-10「字体有问题」= ②、「没有擦除干净」= ③、
    「每次运行都一定数量的图片定字号」= ①）。删掉的代价已按语料量过，见 `expand/扩展张数.md` §22。

    只剩**扩展线自己的护栏**（不是画法偏离 —— 上游那六道闸判的是"装配有没有被弄坏"，
    这几条判的是"底图/名字本身行不行"）：

    * 名字段结构坏了（:data:`JOIN_GAP_MIN` / :data:`JOIN_GAP_MAX`）⇒ 不出片（会留残字/丢冒号）；
    * 名字被截断（`out.truncated_chars`）⇒ 不出片 —— 交付要求图上显示的就是目标用户的名字，
      少一个字这批片就废了（mock 线允许截断补位，扩展线不允许）；
    * `bp.gate_reason` 那九道（与 `batch_produce` 同一套判据）照旧，`SKIP_EMOJI` 照旧豁免（§12.6）。

    名字**不补位**（`fill_slot=False`）：补位是 mock 线的规格（把槽位填满），
    扩展线写的就是目标用户的名字，多一个字都是错的。

    ``text_color`` = 规划时按**目标用户自己图**汇总出来的名字色（计划的 `self_color`），
    出片时**钉死**写上去。底图是**别人**的图 ⇒ 底图上量到的色凭什么决定这个用户的新名字色
    （用户 2026-10-11「建议固定用户颜色」）；与徽章同一条口径（口径④）。旧计划没有这一项
    ⇒ 传 None，退回上游的「逐张按底图量」。

    Returns:
        记录 dict；``image`` 只在**过关**时给（`render_job` 拿不到图就不落盘）。
    """
    profile = platforms.profile_for(profile_key)
    rec: dict = {"base": base.path, "base_user": base.user_dir, "name": name,
                 "ok": False, "reason": "", "tail_exact": None, "gap_ok": None,
                 "box_ok": None, "slot_width": 0, "text_width": 0, "font_size": 0,
                 "ms": {}}
    # 评测埋点：`process_image` 内部含读图+定位+画字，拆不开（上游共用），
    # 所以这里只分「process_image / 贴徽章」两段；自检与落盘在 `render_job` 里补。
    t_render = time.perf_counter()
    try:
        out = ru.process_image(Path(base.path), name, bbox=base.box,
                               inset=inset_for(base.box_src, roi_inset),
                               vinset=vinset_for(base.box_src, roi_vinset),
                               fill_slot=False, profile=profile,
                               geom=base.geom,
                               text_color=text_color,          # 目标用户自己的名字色（钉死）
                               # 字号按**要写的这串名字**的墨高对（中文/数字/英文三类不同，§23）
                               fit_text=name,
                               save_output=False, save_compare=False, save_mask=False,
                               keep_result=True)
    except Exception as exc:  # noqa: BLE001 - 上游单张失败走 ok=False，这里兜的是意外
        rec["ms"]["render"] = _ms_since(t_render)
        rec["reason"] = f"{type(exc).__name__}: {exc}"
        return rec
    rec["ms"]["render"] = _ms_since(t_render)
    if not out.ok or out.result_image is None:
        rec["reason"] = out.error or "PROCESS_FAILED"
        return rec
    rec.update({"slot_width": int(out.slot_width), "font_size": int(out.font_size),
                "text_width": int(out.text_width), "glyph_height": int(out.glyph_height),
                "ink_after": int(out.ink_after), "join_gap": int(out.join_gap),
                "truncated_chars": int(out.truncated_chars),
                "name_emoji": out.name_emoji, "multi_line": out.multi_line})
    if out.stripped_chars:
        rec["stripped_chars"] = int(out.stripped_chars)
    if out.name_emoji:
        # 底图名字段里的 emoji：上游 `gate_reason` 会判 `SKIP_EMOJI`，扩展线**豁免**
        # （§12.6）—— 底图那段 emoji 在名字槽位内 ⇒ 与旧名字一起被擦掉，本就该消失。
        rec["base_had_emoji"] = True

    # ---- 护栏：每条都在**任何东西落盘之前**判（`image` 不填 ⇒ `render_job` 不写文件）----
    if rec["join_gap"] < JOIN_GAP_MIN:
        rec["reason"] = f"COLON_GLUED:{rec['join_gap']}px（末字碎段被当成冒号 ⇒ 会留残字）"
        return rec
    if profile.name_strategy == "saturation_run" and rec["join_gap"] > JOIN_GAP_MAX:
        rec["reason"] = f"COLON_ABSORBED:{rec['join_gap']}px（冒号被并进槽位 ⇒ 会丢冒号）"
        return rec
    if out.truncated_chars:
        rec["reason"] = f"TRUNCATED:{out.truncated_chars}字（图上名字与目标名字对不上）"
        return rec

    rec.update({"ok": True, "tail_exact": bool(out.tail_exact), "gap_ok": bool(out.gap_ok),
                "box_ok": bool(out.box_ok), "tail_shift": int(out.tail_shift_px),
                # 留在原地的尾巴墨迹（2026-10-11 用户「移动正文，没有完全移动」）——
                # 与 `tail_exact` 同源但**分得清**「留了半截」和「新写的字压住了尾巴」。
                "tail_leftover": int(out.tail_leftover_px), "tail_clean": bool(out.tail_clean),
                "text_color": list(out.text_color) if out.text_color else []})
    reason = bp.gate_reason(out, 0.0, out.font_size)
    if reason == "SKIP_EMOJI":
        # D1（§12.6）：这条闸**不适用于扩展线** —— 见上面 `base_had_emoji` 那段。
        reason = ""
    if reason:
        rec["reason"] = reason
    result = out.result_image
    if sprite is not None and base.badge_rect is not None:
        t_badge = time.perf_counter()
        ok, why = paste_badge(result, base.badge_rect, sprite)
        rec["ms"]["badge"] = _ms_since(t_badge)
        rec["badge_pasted"], rec["badge_note"] = ok, why
        if not ok:
            rec["reason"] = rec["reason"] or "BADGE_MISMATCH"
    rec["image"] = result
    return rec


def save_jpeg(img: np.ndarray, path: Path, quality: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(path, quality=quality, subsampling=0)


# ---------------------------------------------------------------- 主流程

def load_rois(source: Path) -> dict[str, tuple[int, int, int, int]]:
    """`rel` → 标注框：`manifest.json` 里每张图的 `roi`（见 :func:`resolve_box`）。

    ⚠️ 是 `roi`（**紧贴**值），不是图上画出来的那个框 —— 2026-10-10 20:08 起上游画的是留白框
    （`--pad-ratio 0.25`，每边大 ~8px），我们要的一直是 `roi` 这一列。
    """
    man = json.loads((source / "manifest.json").read_text())
    return {im["rel"]: tuple(int(v) for v in im["roi"][:4])
            for u in man["users"] for im in u["images"] if im.get("roi")}


def load_geoms(source: Path, profile: "platforms.PlatformProfile") -> dict[str, dict]:
    """`rel` → 上游几何（`text_box` / `colon_box` / …），**只给需要它的平台**。

    快手 App 的交付框是**多行融合框**（ROI 常装 2~6 行），目标行不一定是第一行
    ⇒ 只有 `comments.jsonl` 的 `colon_box` 能定准。几何从交付树自动重建
    （`manifest.json` + 各段 `comments.jsonl`，见 :mod:`upstream_geom`）。
    重建失败**只警告**：退回像素启发式（会大量 LOC_NONE，但账上看得见）。
    """
    if not profile.needs_geom:
        return {}
    try:
        gs = upstream_geom.load(source)
    except upstream_geom.GeomError as exc:
        print(f"  ⚠️ 平台 {profile.key} 需要上游几何，但自动重建失败：{exc}"
              f" —— 退回像素启发式（会大量 LOC_NONE）", flush=True)
        return {}
    print(f"  上游几何（自动重建）：{gs.describe()}", flush=True)
    return gs.items


def load_name_of(source: Path) -> dict[str, str]:
    """`rel` → 该图所在目录的用户名（`manifest.username`）。

    与 :func:`load_rois` 同一张表的两列：框那一列给 :func:`resolve_box`，名字这一列给
    :func:`calibrate_width_k`（量平台字形比）与 :func:`fit_font_size_for`（定这张图自己
    那个名字的字号）—— 两者都要**逐图**知道自己画的是哪串字（见 `expand/扩展张数.md` §23）。
    """
    man = json.loads((source / "manifest.json").read_text())
    return {im["rel"]: u["username"] for u in man["users"] for im in u["images"]}


def load_corpus(source: Path):
    """读语料目录：`manifest.json` + 素材清单（`collect_material`，含 `SKIP_PURE_EN`）。

    **按磁盘为准**（用户 2026-10-10）：`manifest.json` 里有、磁盘上没有的条目**当它不存在**
    （实测 视频号 `0x0-20/4.jpg` 就是这么一条）—— 否则该用户的 `have` 多算一张，
    规划的张数、`扩展N.jpg` 的编号、`verify_manifest` 的对账全跟着错。剔掉的条目不静默。
    """
    man = json.loads((source / "manifest.json").read_text())
    missing: list[str] = []
    targets = []
    for u in man["users"]:
        rels, times = [], []
        for im in u["images"]:
            if (source / im["rel"]).exists():
                rels.append(im["rel"])
                times.append(im.get("t"))
            else:
                missing.append(im["rel"])
        targets.append(Target(u["username"],
                              u.get("dir") or u["images"][0]["rel"].split("/")[0],
                              rels, times))
    if missing:
        print(f"  ⚠️ manifest 有 {len(missing)} 条磁盘上没有（按磁盘为准，已剔除）："
              f"{missing[:4]}{' …' if len(missing) > 4 else ''}")
    meta = {im["rel"]: im for u in man["users"] for im in u["images"]}
    return targets, meta


def build_pool(source: Path, profile_key: str, workers: int, roi_inset: int = ROI_INSET,
               roi_vinset: int = ROI_VINSET, cache: Optional[dict] = None,
               cache_code: str = "", two_pass: bool = False,
               target_dirs: Optional[frozenset] = None):
    """所有图量一遍：底图池 + 每个目标自己的槽位宽/徽章（同一趟量出来）。

    框**跟着图走**：每张图配它自己在 `manifest.json` 里的 `roi`（见 :func:`resolve_box`）——
    底图、以及被 `collect_material` 跳过的 ASCII 目录补量的那张，都从同一张表取。

    `cache` 是主进程持有的量测缓存（B 档）；worker 只收到未命中的条目。
    """
    profile = platforms.profile_for(profile_key)
    mats, _ = bp.collect_material(source, profile)
    rois = load_rois(source)
    geoms = load_geoms(source, profile)

    def _rel(p) -> Optional[str]:
        try:
            return str(Path(p).resolve().relative_to(source))
        except ValueError:
            return None

    def _roi(p) -> Optional[tuple[int, int, int, int]]:
        rel = _rel(p)
        return rois.get(rel) if rel is not None else None

    def _geom(p) -> Optional[dict]:
        rel = _rel(p)
        return geoms.get(rel) if rel is not None else None

    names_of = load_name_of(source)

    def _name(p) -> str:
        rel = _rel(p)
        return names_of.get(rel, "") if rel is not None else ""

    items = [(str(m.path), m.user_dir, _roi(m.path), _name(m.path), _geom(m.path))
             for m in mats]
    # ASCII 昵称目录被 collect_material 跳过（保留的口径），但**它们自己是目标用户**、
    # 需要"自己图上的槽位宽 + 徽章"来做名字对账与当贴图源 ⇒ 补量。
    # ⚠️ **每张都量**（bug-284）：旧版只量每目录第一张，第一张定不到徽章就整户按口径④
    # 判「无贴图源 ⇒ 0 张」—— 实测这 9 户（视频号 5 / 抖音 4）里过半其实量得到，
    # 纯属误杀。这些目录是纯 ASCII 的，底图池本来就不用它们（`base_name_is_pure_alnum`），
    # 多量只进"自己的图"这一条路，代价 = 全量多量 ~700 张（12s 的量级）。
    have_dirs = {d for _p, d, _r, _n, _g in items}
    extra: list[tuple] = []
    for t in sorted(x for x in source.iterdir() if x.is_dir() and "-" in x.name):
        if t.name in have_dirs:
            continue
        for p in sorted(t.glob("*.jpg")) + sorted(t.glob("*.png")):
            if p.name.startswith("扩展"):     # 就地写回产出的新图不算素材（P4 之后重跑时）
                continue
            # ASCII 目录的名字：`manifest` 里没有这个目录（`collect_material` 整目录跳过）
            # ⇒ 用目录名当用户名（`13yuyuyu-6` → `13yuyuyuyu`… 见下面 `rsplit` 的老口径）
            extra.append((str(p), t.name, _roi(p), _name(p) or t.name.rsplit("-", 1)[0],
                          _geom(p)))
    if extra:
        print(f"  ASCII 昵称目录（collect_material 整目录跳过）：补量 {len(extra)} 张"
              f"（{len({it[1] for it in extra})} 个目录）")
    items += extra
    return measure_all(items, profile_key, workers, roi_inset, roi_vinset,
                       cache=cache, cache_code=cache_code,
                       two_pass=two_pass, target_dirs=target_dirs)


def resolve_display_names(cands: list[Base], targets: list[Target],
                          wk: Mapping[str, float]) -> tuple[dict[str, str], Counter]:
    """每个用户对一次账：用户名 → 图上真正显示的名字（见 :data:`NAME_STRIP_DIGITS`）。

    一步到位地把**两处**用量算出来：

    ① 目标侧写什么名字 —— 尺子用**他自己图上槽位宽 / 墨高的中位数**（出片也是逐张按底图写的，
       候选宽一律折成**平台尺度**，见 :func:`platform_width`）；尺子失灵时用**图上量到的药丸宽**
       兜底（③）；
    ② 每张图自己的 `display_name` / `name_ratio`（用**该图的自然字号**）⇒ 名字对账闸用；
    ③ 顺手聚合出该用户自己图上**量到过的药丸宽**（稀疏信号，见 :func:`_pill_w`）⇒ 传给
       :func:`resolve_name` 当"前缀数字是徽章数字"的直接证据。

    ⚠️ 尺子是 :func:`platform_width`（我们墨宽 × 该类 k，见 :func:`calibrate_width_k`）——
    2026-10-11 白天删掉 `adv_k` 之后这里一度退回纯 Noto 尺子 ⇒ 系统性偏窄、"删前缀数字"
    这一支挑错（3 户把等级数字写进了名字，bug-292）。连带代价与量法见 §22 / §23。
    """
    by_dir: dict[str, list[Base]] = defaultdict(list)
    for c in cands:
        by_dir[c.user_dir].append(c)

    def _slot_gh(recs: list[Base]) -> tuple[int, int]:
        """该用户自己图上的 **槽位宽 / 名字墨高** 中位数（逐候选定字号要用墨高，不是字号）。"""
        if not recs:
            return 0, 0
        return (int(np.median([c.slot_width for c in recs])),
                int(np.median([c.glyph_height for c in recs])))

    def _pill_w(recs: list[Base]) -> int:
        """该用户自己图上**量到的等级药丸宽**（中位数；0 = 一张都没量到）。

        药丸只在"与名字在列上真的分开"的图上量得到（`badge_found`），有的图因为框/定位把它
        并进了名字槽位 ⇒ 逐张是**稀疏**的。所以判据是"**量到过**就算这位的显示里有丸"，
        不要求每张都量到（实测 `1顺心` 4 张里只 1 张量到、`7余生&默走` 5 张里只 1 张）。
        """
        ws = [c.badge_w for c in recs if c.badge_found and c.badge_w > 0]
        return int(np.median(ws)) if ws else 0

    names: dict[str, str] = {}
    notes: Counter = Counter()
    for t in targets:
        recs = [c for c in by_dir.get(t.user_dir, []) if c.ok and c.slot_width > 0]
        slot, gh = _slot_gh(recs)
        name, note = (resolve_name(t.username, slot, gh, wk, _pill_w(recs)) if gh
                      else (t.username, ""))
        names[t.user_dir] = name
        t.name, t.self_slot, t.self_name_note = name, slot, note
        if note:
            notes["名字对账删了前缀数字"] += 1
    # ASCII 目录（被 collect_material 跳过、只量了第一张）也要有名字，否则那些底图全被闸掉
    empty = [c.user_dir for c in cands if c.user_dir not in names]
    for u in dict.fromkeys(empty):
        recs = [c for c in by_dir[u] if c.ok and c.slot_width > 0]
        slot, gh = _slot_gh(recs)
        name, _n = (resolve_name(u.rsplit("-", 1)[0], slot, gh, wk, _pill_w(recs)) if gh else (u, ""))
        names[u] = name
    return names, notes


def write_measure_timings(source: Path, cands: list[Base]) -> Path:
    """逐图量测分项 → `expand_measure_timings.jsonl`（评测用，**不参与判据**）。

    只在 `cmd_plan` 落一次盘（别让 worker 并发写文件）。`rel` 与 manifest 的 `rel` 同形，
    评测台拿它跟 plan/report 对账。见 `docs/评测方案.html` §2。
    """
    out = source / "expand_measure_timings.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for c in sorted(cands, key=lambda x: x.path):
            try:
                rel = str(Path(c.path).resolve().relative_to(source))
            except ValueError:
                rel = c.path
            fh.write(json.dumps({
                "rel": rel, "user_dir": c.user_dir, "ok": bool(c.ok),
                "reason": c.reason if not c.ok else "",
                "box_src": c.box_src,
                "ms_decode": c.ms_decode, "ms_locate": c.ms_locate,
                "ms_badge": c.ms_badge, "badge_checked": c.badge_checked,
                # D 档：这次徽章量测扫过的像素（收窄后应显著下降；延后/命中为 0）
                "scan_px": c.scan_px,
                # B 档：命中缓存的行三段时间恒 0 —— 评测台聚合分项时要排除它们，
                # 否则热臂的 p50 会被 0 拉低（不是"量得更快"，是"这次没量"）。
                "cached": bool(getattr(c, "cached", False)),
            }, ensure_ascii=False) + "\n")
    return out


def write_pairs(source: Path, pairs: list[dict]) -> Path:
    """配对轨迹 → `expand_pairs.jsonl`（评测/审计用）。

    每次配对一行；C 上线后由等级判据填 `tier`（same / unknown / excluded）与两级等级。
    """
    out = source / "expand_pairs.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for p in pairs:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    return out


# ---------------------------------------------------------------- A 档：livepipe 对账

def _jsonl_rows(path: Path) -> list[dict]:
    """读 JSONL；文件不在就是空表（A 档「缺了就降级、不报错」）。"""
    try:
        with path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except FileNotFoundError:
        return []


def _join_sources(source: Path, man: dict) -> list[dict]:
    """语料对应的 livepipe 导出目录。

    合并桶（`kind: all_users_positive_images_merged_segments`）在 `manifest.segments[].out`
    里记着每个片段的导出目录；单片段语料没有 `segments`，就看语料自己 / 上一层目录。
    `comments.jsonl` 与 `rows.jsonl` 都在**片段导出目录**，不在用户目录里。
    """
    out: list[dict] = []
    for s in man.get("segments") or []:
        if not s.get("out"):
            continue
        d = Path(s["out"])
        if d.is_dir():
            out.append({"seg": s.get("name") or "", "dir": d,
                        "comments": _jsonl_rows(d / "comments.jsonl"),
                        "rows": _jsonl_rows(d / "rows.jsonl")})
    if not out:
        for cand in (source, source.parent):
            if (cand / "comments.jsonl").exists() or (cand / "rows.jsonl").exists():
                out.append({"seg": "", "dir": cand,
                            "comments": _jsonl_rows(cand / "comments.jsonl"),
                            "rows": _jsonl_rows(cand / "rows.jsonl")})
                break
    return out


def _key_t(t) -> float:
    """时间戳取 6 位小数：JSON 往返是精确的，6 位足够区分同一帧里的不同行。"""
    try:
        return round(float(t), 6)
    except (TypeError, ValueError):
        return -1.0


def _row_text(rows: list[dict], crow: dict | None) -> str | None:
    """把落进 ROI 的 rows 拼回 comments 那一行（**只读对账**用）。

    `rows.jsonl` 的 `text` 和 `comments.jsonl` 有两点不同，不处理就会把正常命中
    误报成冲突：
      · 多行 ROI 是几行的并集，rows 把每行分开记，拼起来（按 y 自上而下、空格相连）
        才是 comments 里那一条完整的 `昵称：内容`；
      · 首行前面多一个**等级数字**（`3够***：…` ↔ comments 的 `够***`），
        那是渲染层抄的等级标，不是身份 —— 只在「昵称后缀 + 纯数字前缀」时剥掉，
        昵称本身以数字开头（`18`、`2凌***`）时原样保留。
    """
    def _yx(r: dict) -> tuple:
        bx = r.get("box") or [0, 0, 0, 0]
        return (bx[1], bx[0])

    srt = sorted(rows, key=_yx)
    texts = [str(r.get("text") or "") for r in srt]
    texts = [x for x in texts if x.strip()]
    if not texts:
        return None
    if crow:
        head, sep, body = texts[0].partition("：")
        user = str(crow.get("username") or "")
        if sep and user and head != user and head.endswith(user) \
                and head[: len(head) - len(user)].isdigit():
            texts[0] = f"{user}：{body}"
    return " ".join(texts)


def build_livepipe_join(source: Path, man: dict) -> dict:
    """A 档对账：把 manifest 里每张**导出图** join 回 livepipe 的 comments / rows。

    主键用**导出图自己带的那几个数**：
      · comments 侧 `(seg, panel, t, roi)` —— 实测 73/73 精确命中（roi 是原样抄过去的）；
      · rows 侧 `(seg, panel, t, badge_left, x_right)` + 纵向重叠（多行 roi 是各行的并集）。
    两条路拼出的 `昵称：内容` 一字不差才算**没冲突**（拼接与等级前缀见 `_row_text`）。

    本函数**只读**：不改计划、不出片、不参与任何判据（见 `docs/评测方案.html` §3 的 A 档）。
    """
    srcs = _join_sources(source, man)
    cidx: dict[tuple, list[dict]] = defaultdict(list)
    ridx: dict[tuple, list[dict]] = defaultdict(list)
    for s in srcs:
        for r in s["comments"]:
            cidx[(s["seg"], r.get("panel"), _key_t(r.get("t")),
                  tuple(r.get("roi") or []))].append(r)
        for r in s["rows"]:
            ridx[(s["seg"], r.get("panel"), _key_t(r.get("t")),
                  r.get("badge_left"), r.get("x_right"))].append(r)

    images = [(u.get("dir", ""), im) for u in man.get("users") or []
              for im in u.get("images") or [] if im.get("origin") != "synthesized"]
    rows_out, unmatched, conflicts = [], [], []
    n_exact = 0
    has_comments = any(s["comments"] for s in srcs)
    for user_dir, im in images:
        seg, panel, t = im.get("seg"), im.get("panel"), im.get("t")
        roi = tuple(im.get("roi") or [])
        rel = im.get("rel", "")
        crow = None
        cands = cidx.get((seg, panel, _key_t(t), roi), [])
        if len({(r.get("username"), r.get("content"), r.get("rid")) for r in cands}) > 1:
            unmatched.append({"rel": rel, "why": f"AMBIGUOUS({len(cands)})"})
        elif cands:
            crow = cands[0]
        else:
            unmatched.append({"rel": rel,
                              "why": "NO_COMMENTS_FILE" if not has_comments else "NO_MATCH"})
        rtext, ncand = None, 0
        if len(roi) == 4:
            y0, y1 = roi[1], roi[3]
            rcands = []
            for r in ridx.get((seg, panel, _key_t(t), roi[0], roi[2]), []):
                bx, bb = r.get("box") or [], r.get("badge_box") or []
                if (len(bx) == 4 and bx[1] < y1 and bx[3] > y0) or \
                   (len(bb) == 4 and bb[1] < y1 and bb[3] > y0):
                    rcands.append(r)
            ncand = len(rcands)
            rtext = _row_text(rcands, crow)
        ctext = f"{crow.get('username')}：{crow.get('content')}" if crow else None
        if crow and rtext and ctext != rtext:
            conflicts.append({"rel": rel, "comments": ctext, "rows": rtext})
        elif crow and rtext:
            n_exact += 1
        rows_out.append({
            "rel": rel, "user_dir": user_dir, "seg": seg, "t": t, "panel": panel,
            "roi": list(roi),
            "rid": (crow or {}).get("rid"),
            "username": (crow or {}).get("username"),
            "badge_text": (crow or {}).get("badge_text"),
            "rows_text": rtext,
            "rows_candidates": ncand,
        })

    n = len(rows_out)
    c_hit = sum(1 for r in rows_out if r["username"] is not None)
    rows_hit = sum(1 for r in rows_out if r["rows_text"])
    rows_multi = sum(1 for r in rows_out if r["rows_candidates"] > 1)
    badge_cov = sum(1 for r in rows_out if (r["badge_text"] or "").strip())
    return {
        "schema_version": 1, "kind": "livepipe_join", "source": str(source),
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "ok" if has_comments else "no_source",
        "sources": [{"seg": s["seg"], "dir": str(s["dir"]),
                     "comments_rows": len(s["comments"]), "rows_rows": len(s["rows"])}
                    for s in srcs],
        "images": n,
        "matched": c_hit, "coverage": (c_hit / n) if n else None,
        "rows_matched": rows_hit, "rows_coverage": (rows_hit / n) if n else None,
        "rows_exact": n_exact, "rows_multi": rows_multi,
        "conflict_count": len(conflicts), "conflicts": conflicts[:50],
        "unmatched_count": len(unmatched), "unmatched": unmatched[:50],
        "badge_text_covered": badge_cov,
        "badge_text_ratio": (badge_cov / c_hit) if c_hit else None,
        "rows": rows_out,
    }


# ---------------------------------------------------------------- C 档：等级配对

_LEVEL_RE = re.compile(r"\d{1,3}")


def _clean_level(text) -> Optional[str]:
    """脏值闸①：**1~3 位纯数字**才算一次可用读数（`docs/量测优化方案.html` §3.2）。

    `""` / `Lv.3` / `3级` / `1234` 一律 None —— 宁可当「未知」走宽度判据，也不让一次
    带前缀/超长的误读变成「等级不同」的排除。
    """
    s = str(text or "").strip()
    return s if _LEVEL_RE.fullmatch(s) else None


def pair_tier(t_level: Optional[str], b_level: Optional[str]) -> str:
    """三级降级链的一层（`same` / `unknown` / `excluded` / `degraded`）。

    ``degraded`` = **目标**等级未知 ⇒ 整条链退化为现状（只按宽度），与加 C 之前完全一致。
    """
    if not t_level:
        return "degraded"
    if not b_level:
        return "unknown"
    return "same" if b_level == t_level else "excluded"


def build_level_index(source: Path, man: dict) -> tuple[dict, dict, Counter]:
    """C 档：每张导出图 / 每个目标用户的等级，三道脏值闸**只降级、不排除**。

    ① `badge_text` 必须是 1~3 位纯数字（:func:`_clean_level`）；
    ② 同一 `rid` 的多次读数必须一致（实测 309 个 rid、0 个不一致）；
    ③ 与该用户的**账号级** `level`（manifest）不冲突。

    任一条不过 ⇒ 这一张的等级 = 未知（走第 2 层宽度判据）。**绝不能让一次 OCR 误读
    变成「等级不同」的排除** —— 那是硬损失，而等级对成品没有像素影响（贴的是目标自己的丸）。

    返回 ``(每张图等级, 每个目录的等级, 计数)``；图的键是 `manifest.images[].rel`，
    不在 manifest 里的图（ASCII 补量目录）没有等级 ⇒ 未知。
    """
    srcs = _join_sources(source, man)
    cidx: dict[tuple, list[dict]] = defaultdict(list)
    for s in srcs:
        for r in s["comments"]:
            cidx[(s["seg"], r.get("panel"), _key_t(r.get("t")),
                  tuple(r.get("roi") or []))].append(r)

    stats: Counter = Counter()
    # ② 同一 rid 的读数：comments.jsonl 是同一行评论的多张图共用的键。
    # ⚠️ rid 只在**一个片段内**唯一（`7段/seg3` 的 rid=1 与 `seg7` 的 rid=1 是两条评论）——
    #    键必须带 `seg`，否则跨片段撞 rid 会把整批图误判成「读数互相打架」（实测 73 张里 68 张）。
    rid_levels: dict = defaultdict(set)
    for s in srcs:                       # `seg` 只在来源侧有，rows 自己不带
        for r in s["comments"]:
            lv = _clean_level(r.get("badge_text"))
            if lv is not None and r.get("rid") is not None:
                rid_levels[(s["seg"], r.get("rid"))].add(lv)
    bad_rid = {rid for rid, lvs in rid_levels.items() if len(lvs) > 1}
    stats["rid_conflict"] = len(bad_rid)

    acc: dict[str, Optional[str]] = {}
    for u in man.get("users") or []:
        udir = u.get("dir") or ""
        acc[udir] = _clean_level(u.get("level"))
        if (u.get("level") or "").strip() and acc[udir] is None:
            stats["dirty_account_level"] += 1

    by_rel: dict[str, Optional[str]] = {}
    per_user: dict[str, list[str]] = defaultdict(list)
    for u in man.get("users") or []:
        udir = u.get("dir") or ""
        for im in u.get("images") or []:
            if im.get("origin") == "synthesized":
                continue
            rel = im.get("rel", "")
            cands = cidx.get((im.get("seg"), im.get("panel"), _key_t(im.get("t")),
                              tuple(im.get("roi") or [])), [])
            lv = None
            if len({(r.get("username"), r.get("content"), r.get("rid")) for r in cands}) > 1:
                stats["ambiguous"] += 1          # join 本身有歧义 ⇒ 不采信
            elif cands:
                rid = cands[0].get("rid")
                if rid is not None and (im.get("seg"), rid) in bad_rid:
                    stats["rid_conflict_img"] += 1
                else:
                    lv = _clean_level(cands[0].get("badge_text"))
                    if lv is None:
                        stats["dirty_or_empty"] += 1
            else:
                stats["no_comment"] += 1
            if lv is not None and acc.get(udir) and acc[udir] != lv:
                stats["account_conflict"] += 1   # ③ 两次读数打架 ⇒ 都当未知
                lv = None
            by_rel[rel] = lv
            if lv is not None:
                per_user[udir].append(lv)

    user_level: dict[str, Optional[str]] = {}
    for udir in acc:
        a, seen = acc[udir], set(per_user.get(udir) or [])
        if a is not None and seen and seen != {a}:
            stats["user_level_conflict"] += 1
            user_level[udir] = None
        elif a is not None:
            user_level[udir] = a
        elif len(seen) == 1:
            user_level[udir] = next(iter(seen))
        else:
            user_level[udir] = None
            if len(seen) > 1:
                stats["user_level_conflict"] += 1
    return by_rel, user_level, stats


def run_fingerprint(plan: dict, args) -> dict:
    """E 档的**复用指纹**：任何一项不同都不许复用旧产物。

    含产品代码 hash（改了画法就全量重出）、平台与内缩、出片质量、seed、以及**逐个 job 的**
    配对（`dir/name/base/self_image` + 框 + 底图徽章矩形）—— C 改了配对，指纹自然就变。
    """
    import hashlib
    product = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
    payload = {
        "product": product,
        "platform": plan.get("platform"),
        "roi_inset": plan.get("roi_inset"), "roi_vinset": plan.get("roi_vinset"),
        "ratio": plan.get("ratio"), "min_images": plan.get("min_images"),
        "seed": args.seed, "quality": args.quality,
        "jobs": [[j.get("dir"), j.get("name"), j.get("base"), j.get("self_image"),
                  j.get("base_roi"), j.get("base_badge_rect")]
                 + ([j["base_geom"]] if j.get("base_geom") else [])
                 for j in plan.get("jobs") or []],
    }
    return {"sha256": hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
        "product": product}


def cmd_plan(args) -> int:
    source = Path(args.source).expanduser().resolve()
    profile_key = platforms.resolve_key(args.platform) or platforms.detect_platform_from_path(str(source)) \
        or platforms.DEFAULT_KEY
    if args.platform == "generic":
        profile_key = "generic"
    targets, meta = load_corpus(source)
    man = json.loads((source / "manifest.json").read_text())
    print(f"语料 {source}\n平台 profile = {profile_key}；目标用户 {len(targets)}")
    # C 档（`--level-pair`）：逐图 / 逐目标的等级（三级降级链 + 脏值闸）。**只影响配对**，
    # 对成品没有像素影响（贴的仍是目标自己的丸，覆盖写）—— 见 `docs/量测优化方案.html` §3.2。
    _level_on = bool(args.level_pair or args.level_pair_soft)
    level_of: dict = {}
    user_level: dict = {}
    level_stats: Counter = Counter()
    if _level_on:
        img_level, user_level, level_stats = build_level_index(source, man)
        for _rel, _lv in img_level.items():
            _p = source / _rel
            level_of[str(_p)] = _lv             # `measure_all` 的路径就是 `str(source / rel)`，
            level_of[str(_p.resolve())] = _lv   # 但也接受 resolve 过的（与 roi 表同一把尺子）
        _known = sum(1 for _v in user_level.values() if _v)
        print(f"等级（C）：目标已知 {_known}/{len(user_level)}；图已知 "
              f"{sum(1 for _v in img_level.values() if _v)}/{len(img_level)}"
              + "".join(f"；{k} {v}" for k, v in level_stats.items() if v))
    # B 档（`--cache`）：量测缓存。**只在给了开关时才读写** —— 默认关，老账逐字节不变。
    # ⚠️ D 的 `--two-pass` 会把「延后没量」的行也写进缓存（徽章字段为空），与全量量测的记录
    # **不是同一把尺子** ⇒ 缓存代码串里带上 D 开关，两种模式的条目互不复用。
    cache_code = _product_fingerprint() + (":d2" if args.two_pass else "")
    cache_entries, fit_entries = (load_measure_cache(cache_code) if args.cache
                                  else (None, {}))
    cands, cache_stats = build_pool(source, profile_key, args.workers, args.roi_inset,
                                    args.roi_vinset,
                                    cache=cache_entries, cache_code=cache_code,
                                    two_pass=bool(args.two_pass),
                                    target_dirs=frozenset(t.user_dir for t in targets)
                                    if args.two_pass else None)
    if args.cache:
        # ⚠️ 必须在 `build_pool` **之后**再打开拟合缓存：量测子进程 fork 时会把模块全局
        # 一起继承，在父进程里打开等于让每个 worker 各写一份、命中率也失真。子进程里的
        # `fit_font_size_for`（`measure_base` 的自然字号）本来就被底图缓存覆盖。
        enable_fit_cache(fit_entries)
        _n = len(cands)
        print(f"量测缓存：命中 {cache_stats['hit']}/{_n}"
              f"（{(cache_stats['hit'] / _n) if _n else 0:.1%}）"
              f" · 新量 {cache_stats['miss']} 张"
              + (f" · 坏行 {cache_stats['cached_bad']}" if cache_stats["cached_bad"] else "")
              + f" → {CACHE_PATH.name}（代码 {cache_code[:8]}…）")
    ok = [c for c in cands if c.ok]
    print(f"量了 {len(cands)} 张：可用 {len(ok)}；拒绝 {dict(Counter(c.reason for c in cands if not c.ok))}")
    # 框的来源：正常全是 manifest.roi（见 resolve_box）。出现 detect_box 就是有图**没进对账表**
    # 或 roi 越界 —— 那几张的量测口径与其余图不同，必须看得见。
    _bs = Counter(c.box_src for c in cands)
    print(f"标注框来源：{dict(_bs)}"
          + ("" if set(_bs) <= {"manifest.roi"} else " ⚠️ 有图退回了像素检测，口径不一致"))
    given = load_sprite_rects(args.sprite_rects)
    if given:
        print(f"外部徽章矩形：{len(given)} 个用户（{args.sprite_rects}）")
    # 名字对账（把等级徽章的数字前缀删掉）用的尺子 = **该用户自己图上自然字号的 Noto 墨宽**。
    # ⚠️ 2026-10-11「全盘照上游」把 `adv_k` 与语料字号标定一起删了 ⇒ 这条尺子**系统性偏窄**
    # （平台字形比 Noto 宽 1.3~1.4 倍，见 :func:`resolve_name`）—— 连带代价见 `expand/扩展张数.md` §22。
    wk, wk_n = calibrate_width_k(cands)
    print(f"平台字形比 k（`槽位宽 ÷ 我们画的宽`，只用不以数字开头的名字量）："
          + "、".join(f"{_c} {wk[_c]:.3f}（n={wk_n[_c]}）" for _c in sorted(wk))
          + f"；样本 <{WIDTH_K_MIN_N} 的类取兜底 {WIDTH_K_FALLBACK}")
    names, notes = resolve_display_names(cands, targets, wk)
    gate = apply_name_gate(cands, names)
    ok = [c for c in cands if c.ok]
    print(f"名字对账闸（[{NAME_FIT_LO}, {NAME_FIT_HI}]）：{dict(gate)}；过闸后可用 {len(ok)} 张")
    fams = badge_families(ok)
    bgate = apply_badge_gate(ok, fams)
    ok = [c for c in cands if c.ok]
    print(f"徽章宽合法族 {fams}（±{BADGE_W_TOL}）：{dict(bgate)}；过闸后可用 {len(ok)} 张")
    print("字号：**逐张跟这张底图的墨高**，且按**要写的那串字自己**对（中文/数字/英文三类"
          "墨高不同 —— 数字/英文名的字号会更大，见 §23）；不再按语料标定一个平台常数")
    # 对账结果逐个列出来（用户 2026-10-10：这类"写上去的名字是哪个"必须是**看得见的决定**）
    _chg = [(t.user_dir, t.self_name_note) for t in targets if t.self_name_note]
    if _chg:
        print(f"  名字对账删掉前缀数字 {len(_chg)} 个用户：")
        for _u, _n in _chg:
            print(f"    {_u}：{_n}")

    # 用户 2026-10-10：「原名字纯数字和字母跳过」。**只跳"当底图"这一条路**（底图的旧名字要按字形
    # 擦掉再写新名字，而纯数字/字母名字的字宽/字高量测最不稳，出片容易出偏差）；
    # 目标自己的图仍然照用 —— 那是 ④ 要求的贴图源（只取徽章那几个像素，不碰名字）。
    alnum_base = [c for c in cands if base_name_is_pure_alnum(c)]
    if alnum_base:
        _bu = Counter(c.user_dir for c in alnum_base)
        _ex = list(dict.fromkeys(c.display_name for c in alnum_base))[:5]
        print(f"原名字纯数字/字母：底图跳过 {len(alnum_base)} 张（{len(_bu)} 个用户；"
              f"例 {_ex}；最多的 {[(u, n) for u, n in _bu.most_common(3)]}）")
    pool_all = [c for c in cands if not base_name_is_pure_alnum(c)]
    # 用户 2026-10-10「没有擦除干净」的第二处：槽位/冒号结构坏掉的底图（见 `JOIN_GAP_MIN/MAX`）。
    # 这两类**上游六道闸全绿、成品却是坏的**（残字 / 丢冒号），只能在选材这一步剔。
    bad_struct = [c for c in cands if c.ok and c.struct_note]
    if bad_struct:
        _bu = Counter(c.user_dir for c in bad_struct)
        _k = Counter(c.struct_note.split(":")[0] for c in bad_struct)
        print(f"结构坏掉的底图（缝 ≤{JOIN_GAP_MIN - 1} = 末字碎段当冒号 ⇒ 留残字；"
              f"抖音缝 >{JOIN_GAP_MAX} = 冒号被并进槽位 ⇒ 丢冒号）：{len(bad_struct)} 张 "
              f"（{len(_bu)} 个用户；{dict(_k)}）")
        pool_all = [c for c in pool_all if not c.struct_note]

    by_dir: dict[str, list[Base]] = defaultdict(list)
    for c in cands:
        by_dir[c.user_dir].append(c)

    rng = random.Random(args.seed)
    dir_use: Counter = Counter()
    pair_use: Counter = Counter()
    bucket_use: Counter = Counter()
    path_use: Counter = Counter()
    alnum_used: Counter = Counter()          # allocate 里的兜底计数：池子已预过滤，这里应恒为 0
    vote_stat: Counter = Counter()           # 贴图源的跨图票数分布（票数 → 用户数）
    users_out, jobs, short = [], [], []
    pairs: list[dict] = []          # 配对轨迹（评测/审计用），见 `docs/评测方案.html`
    color_disagree: list[dict] = []          # 自图之间名字色就不一致的用户（只报告，不做闸）
    stat = Counter()
    level_stat: Counter = Counter()          # C 档：层级命中 / 目标等级已知度 / 排除数
    total_need = 0
    for t in sorted(targets, key=lambda x: -x.have):
        want = plan_count(t.have, args.min_images, args.ratio)
        need = want - t.have
        total_need += max(0, need)
        self_recs = [c for c in by_dir.get(t.user_dir, []) if c.ok and c.slot_width > 0]
        self_slot, name, note = t.self_slot, t.name or t.username, t.self_name_note
        if not self_recs:
            stat["没有可用自图（拿不到贴图）"] += 1
        # 名字在我们尺子上的宽（用**该用户自己图上自然字号的中位数**算）—— 只进报告：与他自己
        # 的 `self_slot` 并排看，就能看出"这个名字在这批图上大概占多宽"。
        _tgh = int(np.median([c.glyph_height for c in self_recs])) if self_recs else 0
        _tfs = fit_font_size_for(name, _tgh)
        w_t = expected_width(name, _tfs)
        t_level = user_level.get(t.user_dir) if _level_on else None
        if t_level:
            level_stat["目标等级已知"] += 1

        _bi = [c for c in self_recs if c.badge_found and c.badge_rect]
        # 贴图源取**该用户自己图上出现次数最多的那个徽章宽**（不是"第一张碰上的"）：
        # 同一用户跨图恒定是这条线的实测事实，用众数才不会被个别误认带偏。
        # `_wv` = 这个众数拿到几票，只进报告（不是闸：视频号多数用户的自图只有 1~4 张能认定徽章，
        # 拿票数当闸会误伤正主；"是不是药丸"由 `badge_rect_of` 的几何判据把关，见 `BADGE_GAP_FRAC`）。
        _wm, _wv = (Counter(c.badge_w for c in _bi).most_common(1)[0] if _bi else (0, 0))
        self_img = next((c for c in _bi if c.badge_w == _wm), None)
        # 名字色 = **该用户自己图上量到的那些色的逐通道中位数**（用户 2026-10-11「建议固定用户颜色」）——
        # 与徽章同一条口径：同一个用户的东西用他自己的。见 :func:`user_text_color`。
        self_color, _cn, _cdev = user_text_color(self_recs)
        if self_color:
            stat["量到自己的名字色"] += 1
            if _cdev > COLOR_DEV_MAX:          # 自己图之间就差这么多 ⇒ 量测不稳，得看得见
                color_disagree.append({"dir": t.user_dir, "color": list(self_color),
                                       "n": _cn, "dev": _cdev})
        g = given.get(t.user_dir)
        if g:                       # 外部给了坐标 ⇒ 直接当贴图源（还能救"自己定位不到"的用户）
            img_rel = g["image"] or (Path(self_recs[0].path).name if self_recs else "")
            if img_rel:
                self_img = Base(path=str(source / t.user_dir / img_rel), user_dir=t.user_dir,
                                badge_rect=tuple(g["rect"]), badge_w=g["width"],
                                badge_found=True, ok=True)
                _wm = g["width"]                 # 外部矩形自己带宽度 ⇒ C1 跟着它
                stat["贴图源来自外部矩形"] += 1
        # 「无丸配无丸」（用户 2026-10-12 定案，配置键 `pair_badge_absent`）：自己图定不到徽章，
        # 且配置开了这条口径 ⇒ **照出**，但条件反过来 —— 底图也必须是**没有丸**那张，出片只换名字。
        # 与口径④ 不冲突：口径④ 挡的是「顶着**别人**的丸」，这条路一个徽章像素都不碰。
        # ⚠️ 传给 `allocate` 的是**算好的结果**（目标确实无丸 **且** 开了口径），不是配置键本身 ——
        #    否则「有丸 + `--allow-badge-delta`」也会把 `want_badge_w` 变成 0，被误判成无丸。
        pair_absent = bool(self_img is None and args.pair_badge_absent)
        if self_img is None and pair_absent:
            stat["无丸配无丸（自己图定不到徽章 ⇒ 配无丸底图，只换名字）"] += 1
        elif self_img is None and args.require_sprite:
            # 口径④（扩展徽章一起换）⇒ **贴图源只能是他自己图上的徽章**。自己一张都定不到 ⇒ 不规划。
            # 这不是"产不够"，是"没素材可换徽章"：宁可 0 张，也不出一张顶着**别人等级**的图。
            stat["无贴图源（自己图定不到徽章）⇒ 按口径④ 不出"] += 1
            need = 0
        # C1 的参照宽必须是**贴图自己那条宽**（`_wm`），不是"第一张碰上的宽"——
        # 贴图是按 `_wm` 裁的，用别的宽去筛底图就是让闸和贴图各说各话。
        self_badge = _wm if self_img is not None else 0
        if self_badge:
            stat["量到自己的徽章宽"] += 1
            vote_stat[_wv] += 1
        excluded: list[dict] = []        # C 档第 3 层排除的底图（审计用，只进 pairs）
        picked, pool_n = (allocate(t, name, pool_all, base_cap=args.base_cap,
                           per_target_per_base=args.per_target_per_base, need=max(0, need),
                           rng=rng,
                           want_badge_w=0 if args.allow_badge_delta or self_img is None
                           else self_badge,
                           require_base_badge=not args.allow_unlocated_badge,
                           pair_badge_absent=pair_absent,
                           level_of=level_of if _level_on else None,
                           t_level=t_level, level_soft=bool(args.level_pair_soft),
                           excluded_out=excluded,
                           dir_use=dir_use, pair_use=pair_use, bucket_use=bucket_use,
                           path_use=path_use, skip_stat=alnum_used)
                  if need > 0 else ([], 0))
        # C 的评测账（`R_blank`）：候选池 0 ⇒ 没有底图可配；池子有货但没配够 ⇒ 是 C5 上限/去重。
        # 这两件事在出片上完全不同（一个该补素材、一个该放上限），别混成「缺口」。
        short_reason = ""
        if len(picked) < max(0, need):
            short_reason = "NO_CANDIDATE" if pool_n == 0 else "CAPS"
        users_out.append({"name": name, "raw_name": t.username, "count": max(0, need),
                          "dir": t.user_dir, "have": t.have, "target": want,
                          "slot_width": self_slot, "name_width": w_t, "badge_w": self_badge,
                          "self_image": self_img.path if self_img else "",
                          "badge_rect": list(self_img.badge_rect) if self_img else [],
                          "badge_votes": _wv if self_img else 0,
                          "text_color": list(self_color) if self_color else [],
                          "text_color_n": _cn, "text_color_dev": _cdev,
                          "no_sprite": self_img is None, "name_note": note,
                          "pool": pool_n, "short_reason": short_reason,
                          **({"t_level": t_level, "level_excluded": len(excluded)}
                             if _level_on else {})})
        for b, _bucket in picked:
            # 这张底图上名字该占的宽 = **这张底图自己的字号**下的 Noto 墨宽（与出片同源：
            # `process_image` 就是逐张按这张底图的墨高定字号）。
            w_b = expected_width(name, fit_font_size_for(name, b.glyph_height))
            _tier = pair_tier(t_level, level_of.get(str(b.path))) if _level_on else None
            if _tier == "excluded" and args.level_pair_soft:
                _tier = "cross"     # C′：明确不同只排后，仍然是**真实配对**，要进 R_lvl 的分母
            if _tier is not None:
                level_stat[_tier] += 1
            jobs.append({"name": name, "dir": t.user_dir, "base": b.path, "base_user": b.user_dir,
                         "base_roi": list(b.box) if b.box else [],
                         "base_geom": b.geom,
                         "base_slot": b.slot_width, "base_badge_w": b.badge_w,
                         "base_name_ratio": round(b.name_ratio, 3),
                         "base_badge_rect": list(b.badge_rect) if b.badge_rect else [],
                         "base_t": b.t, "base_gh": b.glyph_height,
                         "w_b": w_b, "delta_w": w_b - b.slot_width, "w_t": w_t,
                         "self_image": self_img.path if self_img else "",
                         "badge_rect": list(self_img.badge_rect) if self_img else [],
                         "self_color": list(self_color) if self_color else [],
                         **({"tier": _tier, "t_level": t_level,
                             "b_level": level_of.get(str(b.path))}
                            if _level_on else {})})
            # 非快手平台没有 geom：不写这个键，旧计划的指纹/账保持逐字段不变
            if b.geom is None:
                jobs[-1].pop("base_geom", None)
            # 配对轨迹（评测/审计共用）：每次配对一行。`tier` 与两级等级先留 None，
            # C 上线后由等级判据填（same / unknown / excluded），见 `docs/评测方案.html` §3.2。
            pairs.append({"dir": t.user_dir, "name": name, "base": b.path,
                          "base_user": b.user_dir, "tier": _tier,
                          "t_level": t_level,
                          "b_level": level_of.get(str(b.path)) if _level_on else None,
                          "w_t": w_t, "w_b": w_b})
        # 第 3 层排除的也记一行（审计「为什么换了这张底图」）—— 它们不是配对，`R_lvl` 要跳过。
        for _e in excluded:
            level_stat["excluded"] += 1
            pairs.append({"dir": t.user_dir, "name": name, "base": _e["base"].path,
                          "base_user": _e["base"].user_dir, "tier": "excluded",
                          "t_level": t_level, "b_level": _e["b_level"],
                          "w_t": w_t, "w_b": _e["w_b"]})
        if len(picked) < max(0, need):
            short.append({"dir": t.user_dir, "name": name, "need": need, "got": len(picked),
                          "pool": pool_n, "reason": short_reason})
    got_total = len(jobs)
    print(f"\n要补 {total_need} 张，规划出 {got_total} 张；产不够的用户 {len(short)} 个")
    if short:
        print("  缺口最大的 5 个：", [(s["name"], f"{s['got']}/{s['need']}") for s in short[:5]])
    print("  名字对账：", dict(stat))
    if _level_on:
        print("  等级配对（C）：", dict(level_stat),
              "（same 首选 / unknown 退回宽度 / degraded 目标未知 / excluded 明确不同）")
    if color_disagree:
        print(f"  名字色量测不稳（自图样本偏离中位数 > {COLOR_DEV_MAX}）{len(color_disagree)} 个用户"
              f"（取中位数，偏离大的排前面）：")
        for _c in sorted(color_disagree, key=lambda x: -x["dev"])[:8]:
            print(f"    {_c['dir']}：取 {tuple(_c['color'])}（{_c['n']} 个样本，"
                  f"最大偏离 {_c['dev']}）")
    if vote_stat:
        print(f"  贴图源票数（同一张宽在自己图里点头的次数 → 用户数）："
              f"{dict(sorted(vote_stat.items()))}（只报告，不做闸）")
    print("  复用度：一张底图最多被借", max(path_use.values()) if path_use else 0,
          "次（上限", args.base_cap, "）；用到原始用户", len(dir_use), "个",
          "；其中最多的一个被借", max(dir_use.values()) if dir_use else 0, "次")
    # schema 5 → 6：新增**逐用户的名字色** `text_color`（`users`）与逐张的 `self_color`（`jobs`）。
    # 用户 2026-10-11「建议固定用户颜色」—— 出片时钉死用它；旧计划没有这一项 ⇒ 出片退回逐张量
    # （`process_image(text_color=None)` 的现状），所以 5 的计划照样能跑，不必重规划。
    # schema 6 → 7：评测账（2026-10-27）—— `meta.counters`、`short[]`、逐用户 `pool/short_reason`，
    # 外加两个旁挂文件（逐图分项 / 配对轨迹）。**只增不改**：旧读者不看新键，出片逐字节不变。
    _fit_stats = fit_cache_stats() if args.cache else {"hit": 0, "miss": 0}
    counters = {
        "measure": len(cands),
        "usable": len(ok),
        "reject": dict(Counter(c.reason for c in cands if not c.ok)),
        "box_src": dict(Counter(c.box_src for c in cands)),
        "badge_calls": sum(1 for c in cands if c.badge_checked),
        "badge_found": sum(1 for c in cands if c.badge_found),
        # D 档：徽章量测的**实际工作量**。`scan_px` = `badge_rect_of` 里 ink mask 处理的像素数，
        # 缓存命中的行是 0（这次没扫）；`badge_deferred` = 这一趟延后没量的（非目标用户 + 结构坏）。
        "scan_px": sum(c.scan_px for c in cands),
        "badge_deferred": sum(1 for c in cands
                              if not c.badge_checked and c.ok and c.box
                              and not getattr(c, "cached", False)),
        "targets": len(targets),
        "need": total_need,
        "planned": len(jobs),
        "short_users": len(short),
        # B 档：分母 = 所有需要量测的次数（底图 `hit + miss` + 字号拟合 `fit_hit + fit_miss`）；
        # 缓存关着时底图 miss = measure、hit = 0，拟合两项都是 0。
        "cache_hit": cache_stats["hit"],
        "cache_miss": cache_stats["miss"],
        "fit_hit": _fit_stats["hit"],
        "fit_miss": _fit_stats["miss"],
        "cache_enabled": bool(args.cache),
        # C 档：三层命中（same / unknown / degraded / excluded）与脏值闸计数。
        # 只有开 C 时才写键 —— 老计划逐字节不变（与 `_fit_stats` 同一条口径）。
        **({"level_pair": True, "level_tiers": dict(level_stat),
            "level_stats": dict(level_stats)} if _level_on else {}),
    }
    # A 档（`--livepipe-join`）：把每张导出图 join 回 livepipe 的 comments / rows，落
    # `livepipe_join.json`。**只读、不参与判据**；缺 comments.jsonl 就降级成 status=no_source，
    # 不报错、不影响出片（见 `docs/评测方案.html` §3 的 A 档）。
    join_summary = None
    if args.livepipe_join:
        jr = build_livepipe_join(source, man)
        _jp = source / "livepipe_join.json"
        _jp.write_text(json.dumps(jr, ensure_ascii=False, indent=1))
        print(f"  livepipe 对账（A 档）：{jr['status']} · 图 {jr['images']}"
              f" · comments 命中 {jr['matched']}"
              f"（{(jr['coverage'] or 0):.1%}）· rows 命中 {jr['rows_matched']}"
              f"（{(jr['rows_coverage'] or 0):.1%}，其中两路一字不差 {jr['rows_exact']}"
              f"、多行 {jr['rows_multi']}）"
              f" · 冲突 {jr['conflict_count']} · 未命中 {jr['unmatched_count']}"
              f" · 徽章文本 {jr['badge_text_covered']}")
        print(f"  → {_jp}")
        join_summary = {k: jr[k] for k in ("status", "images", "matched", "coverage",
                                           "rows_matched", "rows_coverage",
                                           "rows_exact", "rows_multi",
                                           "conflict_count", "unmatched_count",
                                           "badge_text_covered", "badge_text_ratio")}
    # schema 6 → 7：评测账（2026-10-27）—— `meta.counters`、`short[]`、逐用户 `pool/short_reason`，
    # 外加两个旁挂文件（逐图分项 / 配对轨迹）。**只增不改**：旧读者不看新键，出片逐字节不变。
    # schema 7 → 8：加 `meta.join`（A 档对账摘要）与 `fingerprint`（E 档复用指纹）。
    plan = {"schema_version": 8, "kind": "expand", "source": str(source),
            "platform": profile_key,
            # 名字对账用的平台字形比（按字形类）与它的样本数 —— 「为什么这个名字被删了数字」
            # 全靠它复核（见 `resolve_name` / `calibrate_width_k`）。
            "width_k": {k: round(v, 4) for k, v in wk.items()},
            "width_k_n": wk_n,
            # 字号口径 = **逐张跟底图墨高**（上游 `process_image(font_size=None)`）⇒ 账里没有
            # "整批一个字号"这一项；每张实际用的字号落在 `expand_run_report.json` 的逐张
            # `font_size` 列里（见 `expand/扩展张数.md` §22）。
            "roi_inset": args.roi_inset, "roi_vinset": args.roi_vinset,
            "ratio": args.ratio,
            "min_images": args.min_images,
            "meta": {"timings_file": "expand_measure_timings.jsonl",
                     "pairs_file": "expand_pairs.jsonl",
                     "join": join_summary,
                     "counters": counters},
            "short": short, "users": users_out, "jobs": jobs}
    # E 档：计划自带复用指纹（出片前拿它比对 journal；任何一项不同就不复用旧产物）
    plan["fingerprint"] = run_fingerprint(plan, args)
    if args.cache:
        save_measure_cache(cache_entries, fit_entries, cache_code)
        _fh, _fm = _fit_stats["hit"], _fit_stats["miss"]
        _h = cache_stats["hit"] + _fh
        _d = _h + cache_stats["miss"] + _fm
        print(f"量测缓存 → {CACHE_PATH.name}：底图 {cache_stats['hit']} 命中 / "
              f"{cache_stats['miss']} 新量 · 字号拟合 {_fh} 命中 / {_fm} 新算"
              f"（合计 {_h}/{_d} = {(_h / _d) if _d else 0:.1%}）")
    out = source / "expand_plan.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=1))
    t_out = write_measure_timings(source, cands)
    p_out = write_pairs(source, pairs)
    print(f"→ {out}（users {len(users_out)} · jobs {len(jobs)} · 量测 {len(cands)}）")
    print(f"→ {t_out} · {p_out}")
    return 0


def sprite_of(self_image: str, rect, min_h: int = 0) -> Optional[np.ndarray]:
    """从**目标用户自己的原图**里取徽章贴图（B4：贴图就是他自己那块的像素）。

    `min_h`：竖向**取成正好这么高**（以墨迹行带为中心上下取，多了就裁、少了就扩）。
    为什么需要：药丸的**墨**只有 `◆N`/`✦N` 那么高 —— 抖音实测 17~28px（`◆1` 矮、`◆8` 高），
    底图那块与贴图那块常差 1~6px。直接贴**盖不满**（底图药丸露出几条边），比自己高又会被
    `paste_badge` 的尺寸闸拦掉（实测 54×24 贴 54×21 ⇒ 白出一张）。
    药丸的**底**是纯色块 ⇒ 以墨带为中心裁/扩几行都不影响观感（差的那 1~2px 是抗锯齿边）。
    """
    if not self_image or not rect:
        return None
    img = np.array(Image.open(self_image).convert("RGB"))
    x0, y0, x1, y1 = [int(v) for v in rect]
    if min_h > 0 and (y1 - y0) != min_h:
        cy = (y0 + y1) // 2
        y0 = cy - min_h // 2
        y0 = max(0, min(y0, img.shape[0] - min_h))
        y1 = y0 + min_h
    return img[y0:y1, x0:x1].copy()


def cmd_sample(args) -> int:
    """P2：出样张 + 存"上底图 / 下成品"的对照条，供目视（徽章是否严丝合缝、框右留白是否可接受）。"""
    source = Path(args.source).expanduser().resolve()
    plan = json.loads((source / "expand_plan.json").read_text())
    roi_inset = int(plan.get("roi_inset", ROI_INSET))
    # 老计划（schema <= 4）没有 `roi_vinset` ⇒ 退回「纵 = 横」，逐张复现老账，不混口径。
    roi_vinset = int(plan.get("roi_vinset", roi_inset))
    profile_key = plan["platform"]
    users = {u["dir"]: u for u in plan["users"]}
    out_dir = Path(args.out) / "samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    seen, picked = set(), []
    for j in plan["jobs"]:
        if j["dir"] in seen:
            continue
        seen.add(j["dir"])
        picked.append(j)
        if len(picked) >= args.samples:
            break
    rows = []
    for j in picked:
        u = users[j["dir"]]
        badge_rect = u.get("badge_rect") or []
        base_rect = j.get("base_badge_rect") or []
        # 竖向取成**底图那个药丸的高**（与 :func:`render_job` 同口径）—— 不传 `min_h` 时贴图
        # 跟着自己那张图的高走，与底图差 3~5px ⇒ 样张里 3/4 张报「尺寸差太多」，
        # 而真出片（`render_job` 传了 `min_h`）根本没这个问题（bug-287）。样张必须与真出片同口径。
        need_h = (int(base_rect[3]) - int(base_rect[1]) + 1) if len(base_rect) == 4 else 0
        sprite = sprite_of(u.get("self_image", ""), badge_rect, min_h=need_h)

        base_box = box_of_job(j)
        rec = render_one(Base(path=j["base"], user_dir=j["base_user"], box=base_box,
                              box_src=box_src_of_job(j),
                              geom=j.get("base_geom"),
                              badge_rect=tuple(base_rect) if base_rect else None,
                              slot_width=j["base_slot"], badge_w=j["base_badge_w"]),
                         u["name"], sprite, profile_key=profile_key,
                         roi_inset=roi_inset, roi_vinset=roi_vinset)
        img = rec.pop("image", None)
        rec["user"] = u["name"]
        rec["sprite"] = list(sprite.shape[:2]) if sprite is not None else None
        rows.append(rec)
        if img is None:
            print("  渲染失败：", rec)
            continue
        # 徽章区像素级复核（E3 的第一半）：贴完必须与贴图逐像素相同
        if sprite is not None and rec.get("badge_pasted"):
            x0, y0, x1, y1 = [int(v) for v in base_rect]
            sh, sw = sprite.shape[:2]
            rec["badge_pixel_exact"] = bool(np.array_equal(
                img[y0:y0 + min(sh, y1 - y0), x0:x0 + min(sw, x1 - x0)],
                sprite[:min(sh, y1 - y0), :min(sw, x1 - x0)]))
        tag = f"{u['name']}_{Path(j['base']).parent.name}_{Path(j['base']).stem}"
        tag = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]", "_", tag)[:60]
        save_jpeg(img, out_dir / f"{tag}.jpg", args.quality)
        rec["out"] = str(out_dir / f"{tag}.jpg")
        # 对照条：上=底图原行、下=成品同行
        if base_box:
            x0, y0, x1, y1 = base_box
            m = 14
            a = Image.open(j["base"]).convert("RGB").crop((x0 - m, y0 - m, x1 + m, y1 + m))
            b = Image.fromarray(img).crop((x0 - m, y0 - m, x1 + m, y1 + m))
            c = Image.new("RGB", (a.width, a.height + b.height + 6), (255, 0, 0))
            c.paste(a, (0, 0)); c.paste(b, (0, a.height + 6))
            c = c.resize((int(c.width * 1.6), int(c.height * 1.6)), Image.LANCZOS)
            c.save(out_dir / f"cmp_{tag}.png")
            rec["cmp"] = str(out_dir / f"cmp_{tag}.png")
    print(f"\n样张 {len(rows)} 张 → {out_dir}")
    for r in rows:
        print(f"  {r['user']:<12} 底图 {r['base_user']:<12} 槽位 {r['slot_width']:>3}→字宽 {r['text_width']:>3}"
              f" | ok={r['ok']} 尾巴0差={r['tail_exact']} 间距={r['gap_ok']} 框内={r['box_ok']}"
              f" | 贴徽章={r.get('badge_pasted')}({r.get('badge_note','')}) 像素一致={r.get('badge_pixel_exact')}"
              f" | 理由={r['reason']}")
    (out_dir / "samples.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    return 0


def _frame_inner_rows(img: np.ndarray, box: tuple[int, int, int, int]) -> tuple[int, int]:
    """标注框上下各放开 ``config.TAIL_WIN_VEXT`` 行之后，**绿色描边内侧**的行范围（闭区间）。

    出片允许改动的**纵向**界就是这一条 —— 横向照旧钉死在标注框上（见 `render_job` 的
    ``diff_in_box``）。为什么纵向要放开：标注框（`manifest.roi`）是**紧贴那一行汉字**的，
    而正文里的 emoji 比汉字高，上下会探出框几个像素（实测 `天-3/1.jpg`：框 1223~1253、
    👍 墨迹 1222~1257），探出的那几行都在描边里面 —— 搬正文要搬全就必然改到它们。

    ⚠️ 放开量与削描边的判据必须与 :func:`inkmask._tail_real_rows` **同一套**
    （同一个 ``TAIL_WIN_VEXT``、同一个 ``FRAME_EDGE_RATIO``）：账上说的"能改到哪"
    与实际搬家的那个窗口要是两处，这一条就成了摆设。
    """
    h = img.shape[0]
    vext = int(config.TAIL_WIN_VEXT)
    rect = (int(box[0]), max(0, int(box[1]) - vext), int(box[2]), min(h, int(box[3]) + vext))
    x0, y0, x1, y1 = inkmask.shave_frame(img, rect, ratio=config.FRAME_EDGE_RATIO)
    if y1 <= y0:                      # 削没了（框小到只剩描边）⇒ 退回标注框自己，别放行
        return int(box[1]), int(box[3])
    return y0, y1 - 1


def diff_extent(img: np.ndarray, base_path: Path, box) -> dict:
    """成品相对底图的**改动范围**（列/行的最小最大 + 有没有落在允许的那块里）。

    原来是内联在 `render_job` 的徽章分支里的；2026-10-12「无丸配无丸」的片**没有徽章位可贴**
    ⇒ 那段一眼看去"用不上"，但这条几何护栏要管的是**擦名字/写名字/搬正文**有没有越界
    （改到邻行、右缘跑出框），与有没有徽章无关 ⇒ 搬出来给两条路共用。**只报告 + 复用给闸**，
    自己不做判定。

    纵向的允许范围 = **绿色描边的内侧**，不是标注框本身（2026-10-11）：标注框（`manifest.roi`）
    是**紧贴那一行汉字**的，而正文里的 emoji 比汉字高 —— 实测 `天-3/1.jpg`：框 1223~1253、
    👍 墨迹 1222~1257（上下各探出 1/4px），探出的那几行**也在描边里面**。搬正文要搬全
    （用户 2026-10-11「移动正文，没有完全移动」）⇒ 改动必然落在那几行上；照旧拿标注框当纵向界，
    这一条就会为"搬全了"而变红。横向**不放松**：尾巴右缘照旧钉在标注框右沿。
    """
    before = np.array(Image.open(base_path).convert("RGB"))
    diff = (np.abs(img.astype(int) - before.astype(int)).max(axis=2) > 2)
    cols = np.flatnonzero(diff.any(axis=0))
    rows = np.flatnonzero(diff.any(axis=1))
    wy0, wy1 = _frame_inner_rows(before, box)
    return {"diff_cols": [int(cols[0]), int(cols[-1])] if cols.size else [],
            "diff_rows": [int(rows[0]), int(rows[-1])] if rows.size else [],
            "over_box_left": int(box[0] - cols[0]) if cols.size else None,
            "diff_rows_allowed": [wy0, wy1],
            "diff_in_box": bool(cols.size and cols[-1] <= box[2]
                                and cols[0] >= box[0] - BADGE_OUT_PX
                                and rows[0] >= wy0 and rows[-1] <= wy1)}


def render_job(job: dict, profile_key: str, out_root: str,
               quality: int, dry: bool, roi_inset: int = ROI_INSET,
               roi_vinset: int = ROI_VINSET) -> dict:
    """worker：出一张 + 自检（E3）+ 落盘。**只回记录，不回图像**（大数组不过进程边界）。"""
    # 名字色：规划时按**目标用户自己图**汇总的（计划 schema ≥6）。旧计划没有这一项 ⇒ None，
    # 出片退回上游的「逐张按底图量」（与旧版一致，不静默变脸）。
    tc = job.get("self_color") or []
    rec = {"dir": job["dir"], "name": job["name"], "base": job["base"],
           "base_user": job["base_user"], "n": job.get("n", 0), "status": "ok", "reason": "",
           "sprite_from": job.get("self_image", ""),
           "out": "", "tail_exact": None, "gap_ok": None, "box_ok": None,
           "tail_leftover": None, "tail_clean": None, "text_color": list(tc),
           "badge_pasted": None, "badge_pixel_exact": None, "badge_grow_left": None,
           "diff_in_box": None, "w_t": job.get("w_t", 0), "base_slot": job.get("base_slot", 0),
           "ms": {}}
    br = job.get("base_badge_rect") or []
    sprite = None
    if not job.get("self_image"):
        # 没有贴图源。分两种（2026-10-12「无丸配无丸」）：
        #  * 底图**有**丸 ⇒ 贴不上自己的丸就是**顶着别人的**（口径④ 要挡的正是这个）⇒ 拒；
        #  * 底图**也没**丸 ⇒ 这张图本来就没有徽章位，只换名字就是**正确**产物
        #    （计划里这种 job 只在 `pair_badge_absent` 打开时才存在，见 :func:`allocate`）。
        if job.get("base_badge_w") or br:
            return {**rec, "status": "NO_SPRITE", "reason": "没有贴图源（口径④）"}
    else:
        need_h = (int(br[3]) - int(br[1]) + 1) if len(br) == 4 else 0
        t_sprite = time.perf_counter()
        sprite = sprite_of(job["self_image"], job.get("badge_rect") or [], min_h=need_h)
        rec["ms"]["sprite"] = _ms_since(t_sprite)
        if sprite is None:
            return {**rec, "status": "NO_SPRITE", "reason": "贴图源读不出来"}
    base_path = Path(job["base"])
    box = box_of_job(job)
    if box is None:
        return {**rec, "status": "NO_BOX", "reason": "NO_BOX"}
    base_rect = tuple(job["base_badge_rect"]) if job.get("base_badge_rect") else None
    b = Base(path=job["base"], user_dir=job["base_user"], box=box, badge_rect=base_rect,
             box_src=box_src_of_job(job),
             geom=job.get("base_geom"),
             badge_w=job.get("base_badge_w", 0), slot_width=job.get("base_slot", 0))
    try:
        got = render_one(b, job["name"], sprite, profile_key=profile_key,
                         roi_inset=roi_inset, roi_vinset=roi_vinset,
                         text_color=tuple(int(v) for v in tc) if len(tc) == 3 else None)
    except Exception as exc:  # noqa: BLE001
        return {**rec, "status": "ERR", "reason": f"{type(exc).__name__}: {exc}"}
    img = got.pop("image", None)
    # 评测埋点：`render_one` 的分段（process_image / 贴徽章）带过来。
    rec["ms"].update(got.pop("ms", None) or {})
    # ⚠️ `tail_shift` 也得抄进来（2026-10-11 补）：它在 `render_one` 里一直有，却**没进报告** ——
    # 「正文搬了几像素」是这条线最该被复核的数之一，账里没有，复核时只能回去重跑。
    rec.update({k: got.get(k) for k in ("tail_exact", "gap_ok", "box_ok", "badge_pasted",
                                        "tail_leftover", "tail_clean", "tail_shift",
                                        "text_color")})
    rec["reason"] = got.get("reason", "")
    rec["badge_note"] = got.get("badge_note", "")
    if sprite is None:
        # 「无丸配无丸」：这张片**没贴徽章**（底图没有徽章位、目标也没有丸）—— 不是失败，
        # 但必须在账里看得见，否则复核时会以为"徽章那一步被跳过了"。
        rec["badge_note"] = "无丸配无丸：未贴徽章（目标自图无丸，底图也没有徽章位）"
    # 出片实际用的字号 / 写出来的名字宽 / 底图那条缝 —— 逐张落进报告，交付复核时不必再回去猜。
    # `font_size` 现在是**这一张底图的**字号（上游口径：逐张按底图墨高定），同一批里每个用户
    # 都不同 —— 那正是这一版要的（账见 `expand/扩展张数.md` §22）。
    # `glyph_height` 是底图那行原字量出来的墨高，与 `font_size` 同源（`fit_font_size`）。
    # `join_gap` 是 `gap_ok` 的**实测值**，见 :data:`JOIN_GAP_MIN` / :data:`JOIN_GAP_MAX`。
    rec.update({k: got.get(k) for k in ("font_size", "text_width", "truncated_chars",
                                        "ink_after", "glyph_height", "join_gap")})
    if img is None:
        return {**rec, "status": "GATE", "reason": rec["reason"] or "LOC_NONE"}
    if rec["reason"]:
        rec["status"] = "GATE"
    t_check = time.perf_counter()
    if base_rect and rec.get("badge_pasted"):
        x0, y0, x1, y1 = base_rect
        sh, sw = sprite.shape[:2]
        h, w = min(sh, y1 - y0), min(sw, x1 - x0)
        rec["badge_pixel_exact"] = bool(np.array_equal(img[y0:y0 + h, x0:x0 + w], sprite[:h, :w]))
        # E3 另一半：**外扩只允许到徽章左沿外 `BADGE_GROW_PX`**，且改动不许越过框右界。
        ex = diff_extent(img, base_path, box)
        rec.update(ex)
        cols = ex["diff_cols"]
        rec["badge_grow_left"] = int(x0 - cols[0]) if cols else None
        if cols and cols[0] < x0 - BADGE_GROW_PX:
            rec["status"] = "GATE"
            rec["reason"] = rec["reason"] or f"擦除越到徽章左沿外 {x0 - int(cols[0])}px"
        if cols and cols[0] < box[0] - BADGE_OUT_PX:
            rec["status"] = "GATE"
            rec["reason"] = rec["reason"] or f"改动越到标注框左外 {box[0] - int(cols[0])}px（> {BADGE_OUT_PX}）"
    else:
        # 没贴徽章的片（「无丸配无丸」）也照量改动范围 —— 它同样会擦名字、写名字、搬正文，
        # 越界是同一种坏。只是没有"徽章左沿"这条参照，左界一律按标注框算。
        rec.update(diff_extent(img, base_path, box))
        cols = rec["diff_cols"]
        if cols and cols[0] < box[0] - BADGE_OUT_PX:
            rec["status"] = "GATE"
            rec["reason"] = rec["reason"] or f"改动越到标注框左外 {box[0] - int(cols[0])}px（> {BADGE_OUT_PX}）"
    rec["ms"]["check"] = _ms_since(t_check)
    if rec.get("badge_pixel_exact") is False and rec["status"] == "ok":
        rec["status"], rec["reason"] = "GATE", "贴完徽章像素与贴图不一致"
    # 只有**过关**的片才落盘（2026-10-11 起）：`render_one` 的结构护栏本来就不给图，
    # 但徽章那三条（左沿外扩 / 越框 / 贴完像素不一致）是在**图已合成之后**才判的 ——
    # 不过关却照样写一个 `扩展N.jpg` 进用户目录（就地写回时就是写进真语料！），
    # 报告里那张片却算"没出"，盘上和账上对不起来。这条一改，两边就一致了。
    if not dry and out_root and rec["status"] == "ok":
        t_save = time.perf_counter()
        dst = Path(out_root) / job["dir"] / f"扩展{job['n']}.jpg"
        save_jpeg(img, dst, quality)
        rec["out"] = str(dst)
        back = np.array(Image.open(dst).convert("RGB"))
        rec["roundtrip_shape"] = back.shape == img.shape
        rec["relocate_ok"] = _relocate_ok(dst, back, platforms.profile_for(profile_key))
        rec["ms"]["save"] = _ms_since(t_save)
    return rec


def _relocate_ok(path: Path, img: np.ndarray, profile: Optional[object]) -> bool:
    """成品能不能被**自家管线**再定位一次（`detect_box` + `find_erase_region`）。

    ⚠️ 这是**报告项，不是闸** —— 它失败**不**影响出片。原因见 `expand/扩展张数.md` §16：
    抖音的 `estimate_background` 取"精确 RGB 众数"，深色底和白色正文在框内各占 ~1%，
    两个众数只差个位数像素 ⇒ **任何一次 JPEG 重编码都可能把背景判成白色**，
    于是整框连成一段、徽章误判、定位失败。把底图**原样重编码**（不做任何修改）
    就已经有 14/27 张翻车，所以拿它当闸会把上游的刀刃算到我们头上。
    """
    try:
        box, _n, _f = batch_run.detect_box(path)
        if box is None:
            return False
        inkmask.find_erase_region(img, box, profile=profile)
        return True
    except Exception:  # noqa: BLE001
        return False


def write_reconcile(rows: list[dict], users: list[dict], out_csv: Path) -> None:
    """E2 对账 CSV：**每个用户一行**（要补多少 / 实际出多少 / 缺多少 / 为什么缺）。

    ⚠️ `要补` 写的是**口径要补** `目标张数 − 原张数`，**不是**本次规划数 ——
    规划数会把"无贴图源 ⇒ 按口径④ 不出"的用户置 0（bug-279），
    那样一列合计恰等于实出，**把缺口吃掉**（视频号 要补 91/实出 91，真口径是 280/91）。
    缺的 189 张要看得见，所以另给一列 `缺口`。
    """
    import csv
    by_dir: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_dir[r["dir"]].append(r)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["原始目录", "要写的名字", "原张数", "目标张数", "要补", "实际出片",
                    "缺口", "成品总张数", "达成率", "无贴图源", "失败原因"])
        for u in sorted(users, key=lambda x: x["dir"]):
            rs = by_dir.get(u["dir"], [])
            good = [r for r in rs if r["status"] == "ok"]
            why = Counter(r["reason"] or r["status"] for r in rs if r["status"] != "ok")
            need = u["target"] - u["have"]
            w.writerow([u["dir"], u["name"], u["have"], u["target"], need, len(good),
                        need - len(good), u["have"] + len(good),
                        f"{u['have'] + len(good)}/{u['target']}",
                        "是" if u.get("no_sprite") else "",
                        "; ".join(f"{k}×{v}" for k, v in why.most_common(3))])


def load_sprite_rects(path: str) -> dict[str, dict]:
    """读**外部提供的徽章矩形**（口径④ 的兜底来源：上游/素材版直接给坐标）。

    格式（`.json`）：``{"<用户目录名>": {"image": "<该目录下的文件名>",
    "rect": [x0, y0, x1, y1], "width": 63}, …}`` —— `width` 可省（由矩形算）。
    给到的用户就**不再**自己定位贴图源（`badge_rect_of` 在这个用户身上不可靠时用得上）。
    """
    if not path:
        return {}
    raw = json.loads(Path(path).expanduser().read_text())
    out: dict[str, dict] = {}
    for k, v in raw.items():
        rect = [int(t) for t in v["rect"]]
        out[k] = {"image": v.get("image", ""), "rect": rect,
                  "width": int(v.get("width") or (rect[2] - rect[0]))}
    return out


def verify_manifest(before: Optional[dict], man_path: Path,
                    renames: dict[str, str]) -> tuple[int, list[str]]:
    """§7 验收线 4：`manifest.json` **原有条目未变**（只追加、不改写）。

    Returns:
        ``(一致条数, 不一致清单)``。
    """
    if not before or not man_path.exists():
        return 0, []
    after = json.loads(man_path.read_text())
    inv = {v: k for k, v in renames.items()}
    after_by_dir = {inv.get(u.get("dir", ""), u.get("dir", "")): u for u in after.get("users", [])}
    ok, bad = 0, []
    for u in before.get("users", []):
        d = u.get("dir", "")
        nu = after_by_dir.get(d)
        if nu is None:
            bad.append(f"{d}: 用户没了")
            continue
        old = [json.dumps(im, sort_keys=True, ensure_ascii=False) for im in u.get("images", [])]
        new = [json.dumps(im, sort_keys=True, ensure_ascii=False) for im in nu.get("images", [])]
        if new[:len(old)] != old:
            bad.append(f"{d}: 原有条目被改了")
        else:
            ok += len(old)
    return ok, bad


def append_manifest(root: Path, src_root: Path, rows: list[dict],
                    renames: dict[str, str]) -> tuple[int, int]:
    """§6：`manifest.json` **追加**新条目（`origin:"synthesized"`），**原有条目原样保留**。

    新条目字段 = 底图那条原样搬（`label` 正面、`content`/`emoji_text`/`t`/`panel`/`roi` 都是底图的
    —— 正文就是底图原像素），外加 `origin` / `base_from` / `body_from` / `badge_from` / `target_user`。

    Returns:
        ``(追加条数, 用户数)``。
    """
    man_path = root / "manifest.json"
    if not man_path.exists():
        return 0, 0
    man = json.loads(man_path.read_text())
    by_dir: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["status"] == "ok":
            by_dir[r["dir"]].append(r)
    src_rel = {im["rel"]: im for u in man["users"] for im in u["images"]}
    root = src_root
    added = 0
    for u in man["users"]:
        rs = sorted(by_dir.get(u.get("dir", ""), []), key=lambda r: r["n"])
        if not rs:
            continue
        new_dir = renames.get(u["dir"], u["dir"])
        for r in rs:
            base_rel = str(Path(r["base"]).relative_to(root))
            src = src_rel.get(base_rel, {})
            entry = {k: src[k] for k in ("t", "roi", "panel", "platform", "label", "content",
                                         "emoji_text") if k in src}
            entry.update({"rel": f"{new_dir}/扩展{r['n']}.jpg", "origin": "synthesized",
                          "target_user": r["name"], "base_from": base_rel, "body_from": base_rel,
                          "badge_from": str(Path(r["sprite_from"]).relative_to(root))
                          if r.get("sprite_from") else ""})
            u.setdefault("images", []).append(entry)
            added += 1
        u["dir"] = new_dir
        u["count"] = len(u.get("images", []))
    man_path.write_text(json.dumps(man, ensure_ascii=False, indent=1))
    return added, len(by_dir)


def cmd_run(args) -> int:
    """P3/P4：按计划出片。`--out` 给出来就是**预演树**（不动语料），不给就**就地写回**。"""
    source = Path(args.source).expanduser().resolve()
    plan = json.loads((source / "expand_plan.json").read_text())
    profile_key = plan["platform"]
    roi_inset = int(plan.get("roi_inset", ROI_INSET))
    # 老计划（schema <= 4）没有 `roi_vinset` ⇒ 退回「纵 = 横」，逐张复现老账，不混口径。
    roi_vinset = int(plan.get("roi_vinset", roi_inset))
    only ={s.strip() for s in args.only.split(",") if s.strip()} if args.only else set()

    def _sel(j: dict) -> bool:
        d = j["dir"]
        return (not only) or d in only or j["name"] in only or any(
            d == s or d.startswith(s + "-") for s in only)

    jobs = [j for j in plan["jobs"] if _sel(j)]
    # 编号：每个目标用户从 扩展1 开始（和样张的 tag 命名区分开）
    cnt: Counter = Counter()
    for j in jobs:
        cnt[j["dir"]] += 1
        j["n"] = cnt[j["dir"]]
    if args.limit:                      # 每个用户最多出几张（P3 小批）
        keep: Counter = Counter()
        kept = []
        for j in jobs:
            keep[j["dir"]] += 1
            if keep[j["dir"]] <= args.limit:
                kept.append(j)
        jobs = kept
    in_place = not args.out
    out_root = str(source) if in_place else str(Path(args.out).expanduser().resolve())
    print(f"出片 {len(jobs)} 张（{len({j['dir'] for j in jobs})} 个用户）→ "
          f"{'就地写回 ' + str(source) if in_place else out_root}")
    if not args.yes and in_place:
        print("  就地写回要加 --yes 确认（怕误伤语料）。先跑 --out /tmp/xxx 预演一遍。")
        return 2

    # E2 前置：动语料前记下**原图 md5**（跑完复核，证明一张都没被改过）
    md5_before = {}
    if not args.skip_md5:
        import hashlib
        for d in sorted({j["dir"] for j in jobs}):
            for p in sorted((source / d).glob("*.[jp][pn]g")):
                if p.name.startswith("扩展"):
                    continue
                md5_before[str(p)] = hashlib.md5(p.read_bytes()).hexdigest()
        print(f"  记下 {len(md5_before)} 张原图的 md5")

    # E 档（`--resume`）：断点续跑。复用判据 = 指纹一致 + 产物文件还在，
    # 产物记录从**进度 journal**（`expand_run_progress.jsonl`，逐张完成即 flush）里读 ——
    # 报告是最后才写的，kill -9 之后只剩 journal 能证明「这张出完了」。
    fp = run_fingerprint(plan, args)
    plan_fp = (plan.get("fingerprint") or {}).get("sha256")
    if plan_fp and plan_fp != fp["sha256"]:
        print(f"  ⚠️ 计划指纹与当前参数/代码不一致（plan {plan_fp[:12]}… vs now {fp['sha256'][:12]}…）"
              f" —— 计划可能没重跑；`--resume` 不会复用旧产物")
    journal_path = Path(out_root) / "expand_run_progress.jsonl"
    # `--out` 指向一棵还不存在的预演树是常态（评测台每遍都会先 rmtree）——
    # 以前第一张图的 save_jpeg 会顺手建目录，现在 journal 先打开，就得自己建。
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    resume = {"enabled": bool(args.resume), "fingerprint": fp["sha256"], "product": fp["product"],
              "reused": 0, "redone": 0, "stale_fingerprint": 0, "missing_file": 0,
              "journal": journal_path.name}
    job_index = {(j["dir"], j["base"], j.get("n")): i for i, j in enumerate(jobs)}
    rows: list[dict] = [None] * len(jobs)  # type: ignore[list-item]
    if args.resume and journal_path.exists():
        for line in journal_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            r = rec.get("row") or {}
            if rec.get("fp") != fp["sha256"]:
                resume["stale_fingerprint"] += 1
                continue
            if r.get("status") != "ok" or not r.get("out") or not Path(r["out"]).is_file():
                resume["missing_file"] += 1
                continue
            i = job_index.get((r.get("dir"), r.get("base"), r.get("n")))
            if i is None:
                continue
            rows[i] = {**r, "reused": True}
            resume["reused"] += 1
    todo = [(i, j) for i, j in enumerate(jobs) if rows[i] is None]
    resume["redone"] = len(todo)
    if args.resume:
        print(f"  --resume：复用 {resume['reused']} 张，需重出 {len(todo)} 张"
              f"（指纹不符跳过 {resume['stale_fingerprint']} 行 · 产物缺失 {resume['missing_file']} 行）")
    with journal_path.open("a" if args.resume else "w", encoding="utf-8") as jh:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(render_job, j, profile_key, out_root, args.quality,
                                False, roi_inset, roi_vinset): i
                    for i, j in todo}
            done = 0
            for f in as_completed(futs):
                i = futs[f]
                try:
                    rec = f.result()
                except Exception as exc:  # noqa: BLE001
                    rec = {"dir": jobs[i]["dir"], "name": jobs[i]["name"],
                           "base": jobs[i]["base"], "n": jobs[i]["n"],
                           "status": "CRASH", "reason": f"{type(exc).__name__}: {exc}"}
                rows[i] = rec
                # 完成一张就落一行 —— kill -9 之后这是唯一的进度证据
                jh.write(json.dumps({"fp": fp["sha256"], "row": rec}, ensure_ascii=False) + "\n")
                jh.flush()
                done += 1
                if done % 20 == 0 or done == len(todo):
                    print(f"  {done}/{len(todo)}", flush=True)
    rows = [r for r in rows if r]

    good = [r for r in rows if r["status"] == "ok"]
    print(f"\n出片完成：成功 {len(good)}/{len(rows)}")
    print("  失败原因：", dict(Counter(r["reason"] or r["status"] for r in rows if r["status"] != "ok")
                            .most_common(10)))
    # ⚠️ `badge_*` 两道只在**有贴图源**的片上才有意义：分母若照旧用 `good`，
    #    「无丸配无丸」的片（本来就不该贴徽章）会全被算成"没通过"，读起来像 19 张全挂。
    _sp = [r for r in good if r.get("sprite_from")]
    _nb = [r for r in good if not r.get("sprite_from")]
    print("  各道闸通过率：",
          {k: (f"{sum(1 for r in (_sp if k.startswith('badge_') else good) if r.get(k))}/"
               f"{len(_sp if k.startswith('badge_') else good)}"
               + ("（分母 = 有贴图源的片）" if k.startswith("badge_") else ""))
           for k in ("tail_exact", "tail_clean", "gap_ok", "box_ok", "badge_pasted",
                     "badge_pixel_exact", "diff_in_box")})
    if _nb:
        print(f"  无丸配无丸（目标自图无丸 ⇒ 底图也没有徽章位，只换名字）："
              f"{len(_nb)}/{len(good)} 张")
    _lo = [r["tail_leftover"] for r in good if r.get("tail_leftover")]
    if _lo:
        print(f"  ⚠️ 尾巴留在原地（`tail_clean` 未过的那些）：{len(_lo)} 张，"
              f"max {max(_lo)}px；样例 "
              f"{[(r['dir'], r['base'].split('/')[-2], r['tail_leftover']) for r in good if r.get('tail_leftover')][:5]}")
    if good:
        gr = [r["badge_grow_left"] for r in good if r.get("badge_grow_left") is not None]
        print(f"  徽章左沿外扩（应 ≤{BADGE_GROW_PX}px）：",
              f"min {min(gr)} / 中位 {int(np.median(gr))} / max {max(gr)}" if gr else "无")
        ob = [r["over_box_left"] for r in good if r.get("over_box_left") is not None]
        if ob:
            print(f"  改动越出标注框左沿（徽章圆头，应 ≤{BADGE_OUT_PX}px）："
                  f"min {min(ob)} / 中位 {int(np.median(ob))} / max {max(ob)}"
                  f"；越框的 {sum(1 for v in ob if v > 0)}/{len(ob)} 张")
        print("  改动列范围样例：", [r.get("diff_cols") for r in good[:3]])
        rl = [r for r in good if r.get("relocate_ok") is not None]
        bad_rl = [r for r in rl if not r["relocate_ok"]]
        if rl:
            print(f"  成品可再定位（报告项，非闸）：{len(rl) - len(bad_rl)}/{len(rl)}"
                  f"；认不出 {len(bad_rl)} 张（上游众数取背景的刀刃，见 expand/扩展张数.md §16）")

    md5_changed = []
    if md5_before:
        import hashlib
        for p, h in md5_before.items():
            if not Path(p).exists() or hashlib.md5(Path(p).read_bytes()).hexdigest() != h:
                md5_changed.append(p)
        print(f"  原图 md5 复核：{len(md5_before)} 张，变了 {len(md5_changed)} 张"
              + (f" {md5_changed[:5]}" if md5_changed else " ✅"))

    # 目录改名：`<用户名>-<新张数>`（就地模式；预演树里保留原名，方便对照）
    renamed: dict[str, str] = {}
    if not args.no_rename:
        for u in plan["users"]:
            if u["dir"] not in {r["dir"] for r in rows}:
                continue
            got = sum(1 for r in good if r["dir"] == u["dir"])
            # 前缀取**目录名**自己的，不取 manifest 里的显示名：语料把 `*` 落成 `_`
            # （`深**-11` 在磁盘上是 `深__-11`），用显示名会把 `深__-11` 改名成 `深**-17`。
            stem = u["dir"].rsplit("-", 1)[0]
            if not stem or not u["dir"].rsplit("-", 1)[-1].isdigit():
                stem = u["raw_name"]
            new_name = f"{stem}-{u['have'] + got}"
            if not got or new_name == u["dir"]:
                continue
            if in_place:
                src_d, dst_d = source / u["dir"], source / new_name
                if dst_d.exists():
                    print(f"  ⚠️ 目录已存在，跳过改名：{new_name}")
                    continue
                src_d.rename(dst_d)
            renamed[u["dir"]] = new_name
        print(f"  目录改名 {'%d 个' % len(renamed) if in_place else '(预演：只记账) '}"
              f"{list(renamed.items())[:4]}")

    # §6：manifest 追加（原有条目原样保留）
    man_before = json.loads((source / "manifest.json").read_text()) \
        if (source / "manifest.json").exists() else None
    if not in_place:
        import shutil
        shutil.copy2(source / "manifest.json", Path(out_root) / "manifest.json")
    n_add, n_user = append_manifest(Path(out_root), source, rows,
                                    renamed if in_place else {})
    print(f"  manifest 追加 {n_add} 条（涉及 {n_user} 个用户；原有条目未动）")
    man_ok, man_bad = verify_manifest(man_before, Path(out_root) / "manifest.json", renamed)
    print(f"  manifest 原有条目复核：{man_ok} 条一致" + (f"，{len(man_bad)} 条不一致 {man_bad[:3]}" if man_bad else " ✅"))

    # E2/E3 落盘
    # schema 2 → 3：评测账（2026-10-27）。`timings` = 逐张分段的合计（秒）；
    # `counters` 在开关没开时写 0（**字段存在 = 测过**；缺字段才表示没测，见 `docs/评测方案.html` §2）。
    # schema 3 → 4：加 E 档的 `resume` 段（复用数 / 复出数 / 指纹）与 `counters.resume_reused`。
    # `timings` 只统计**这一轮真的做了的**张（复用的行不带 `ms` 进来，否则会把上一轮的 CPU 算到这一轮）。
    ms_sum: Counter = Counter()
    for _r in rows:
        if _r.get("reused"):
            continue
        for _k, _v in (_r.get("ms") or {}).items():
            ms_sum[_k] += float(_v or 0)
    timings = {k: round(v / 1000.0, 3) for k, v in sorted(ms_sum.items())}
    counters = {"cache_hit": 0, "cache_miss": 0, "badge_calls": 0,
                "scan_px": 0, "resume_reused": resume["reused"]}
    rep = {"schema_version": 4, "kind": "expand_run", "source": str(source),
           "in_place": in_place, "out_root": out_root, "platform": profile_key,
           "jobs": len(rows), "ok": len(good),
           "status": dict(Counter(r["status"] for r in rows)),
           "reasons": dict(Counter(r["reason"] or r["status"] for r in rows if r["status"] != "ok")),
           "renamed": renamed, "md5_checked": len(md5_before), "md5_changed": md5_changed,
           "manifest_added": n_add, "manifest_original_ok": man_ok, "manifest_bad": man_bad,
           "timings": timings, "counters": counters, "resume": resume,
           "rows": rows}
    # 口径容差（`expand/扩展张数.md` §3「缺口 ≤5% 不阻断、>5% 是错误」+ §11 退出码）：
    # 缺口**进报告**（`expand_reconcile.csv` 的 `缺口` 列）并且**说话**—— 只看退出码的调用方
    # 拿不到"这一批产够了没有"。这里与 `batch_produce` 的 `3` 对齐：0 全产够 / 3 缺口 >5%。
    # ⚠️ 缺口按**口径要补**算（`target − have`），不是按本次规划数 —— 后者会把
    # "无贴图源 ⇒ 按口径④ 不出"的用户置 0，缺口被吃掉（见 :func:`write_reconcile`，bug-279）。
    need_all = sum(u["target"] - u["have"] for u in plan["users"])
    gap = need_all - len(good)
    if in_place:
        (source / "expand_run_report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=1))
        write_reconcile(rows, plan["users"], source / "expand_reconcile.csv")
        print(f"  口径要补 {need_all} 张 · 实出 {len(good)} 张 · 缺口 {gap} 张"
              # ⚠️ 这两个数是**这次真用的**，别写死：`ratio` 与 `min_images` 都能被
              #    配置/命令行改（本次 App 线是 1.6/5，写死 1.5 会让人对不上账 ——
              #    缺口 147 是 1.6 算出来的，1.5 只有 118）。
              f"（口径 T = max({args.min_images}, ceil({args.ratio:g} × N))）")
        print(f"→ {source}/expand_run_report.json · {source}/expand_reconcile.csv")
    else:
        Path(out_root).mkdir(parents=True, exist_ok=True)
        (Path(out_root) / "expand_run_report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=1))
        write_reconcile(rows, plan["users"], Path(out_root) / "expand_reconcile.csv")
        print(f"  口径要补 {need_all} 张 · 实出 {len(good)} 张 · 缺口 {gap} 张"
              # ⚠️ 这两个数是**这次真用的**，别写死：`ratio` 与 `min_images` 都能被
              #    配置/命令行改（本次 App 线是 1.6/5，写死 1.5 会让人对不上账 ——
              #    缺口 147 是 1.6 算出来的，1.5 只有 118）。
              f"（口径 T = max({args.min_images}, ceil({args.ratio:g} × N))）")
        print(f"→ {out_root}/expand_run_report.json · {out_root}/expand_reconcile.csv")
    if args.only or args.limit:
        # P3 小批（单用户 / 每用户前 N 张）本来就不打算产够 —— 拿它判 5% 容差是拿错了尺子。
        print("  （--only / --limit 是小批预演，不做「缺口 >5%」判定）")
        return 0
    if need_all and gap > 0.05 * need_all:
        print(f"  ⚠️ 缺口 {gap}/{need_all} = {100 * gap / need_all:.1f}% 超过 5% 容差"
              f"（口径：缺口 >5% 是错误，退 3）—— 逐用户的缺口见 expand_reconcile.csv")
        return 3
    return 0


DEFAULTS: dict = {
    "source": None,
    "platform": "auto",
    "mode": "plan",
    "ratio": RATIO,
    "min_images": 5,
    "base_cap": 3,
    "per_target_per_base": 2,
    "allow_badge_delta": False,
    "allow_unlocated_badge": False,
    "require_sprite": True,
    "pair_badge_absent": False,
    "sprite_rects": "",
    "livepipe_join": False,   # A 档：plan 时落 livepipe 对账表（只读）
    "level_pair": False,      # C 档：三级等级链（只改配对，默认关）
    "level_pair_soft": False,  # C′（事后变体，未预注册）：明确不同只排后、不排除
    "two_pass": False,        # D 档：两遍量测（徽章延后给落选图 + 列扫描收窄，默认关）
    "resume": False,          # E 档：按进度 journal 复用已完成的片（默认关）
    "cache": False,           # B 档：量测缓存（plan 时读/写 measure_cache.json，默认关）
    "only": "",
    "limit": 0,
    "samples": 4,
    "out": "",                # "" / null = 就地写回语料（要 --yes）；给了就是**预演树**，不动语料
    "yes": False,
    "no_rename": False,
    "skip_md5": False,
    "quality": 95,
    "seed": 20261010,
    "workers": 8,
    "roi_inset": ROI_INSET,
    "roi_vinset": ROI_VINSET,
}
"""命令行与 JSON 都没给时用的值（**同时是运行配置的合法键清单与类型表**）。

⚠️ ``source`` 这里是 **None**（读作「没给」），不是 argparse 时代的 ``required=True`` ——
必须给够才跑（由 :func:`main` 显式报错），否则 ``required`` 会把「配置文件里写了 source」
这种最正常的用法直接判死。
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="扩展张数（给语料里每个真实用户补图）")
    runconfig.add_argument(ap)
    # ⚠️ 可被运行配置 JSON 控制的参数一律 ``default=None``（None = 这次没给），
    #    由 runconfig.fill 依次回填：命令行 > JSON > 下面的 DEFAULTS。
    ap.add_argument("--source", default=None, help="平台语料目录（含 manifest.json 的那一层）")
    ap.add_argument("--platform", default=None,
                    help="douyin / wechat_channels / wechat_app / weibo / generic / auto")
    ap.add_argument("--mode", default=None, choices=("plan", "sample", "run"))
    ap.add_argument("--ratio", type=float, default=None)
    ap.add_argument("--min-images", type=int, default=None)
    ap.add_argument("--base-cap", type=int, default=None, help="C5：一张底图最多被几个用户借")
    ap.add_argument("--per-target-per-base", type=int, default=None, help="C5：同一目标同一底图上限")
    ap.add_argument("--allow-badge-delta", action="store_true", default=None, help="C2：不要求徽章同档")
    ap.add_argument("--allow-unlocated-badge", action="store_true", default=None,
                    help="底图徽章定位不到也照出（样张/排错用；默认拦掉）")
    ap.add_argument("--require-sprite", dest="require_sprite", action="store_true", default=None,
                    help="口径④：目标自己图上定不到徽章就不出片（默认开）")
    ap.add_argument("--allow-no-sprite", dest="require_sprite", action="store_false", default=None,
                    help="没有贴图源也出片（徽章留底图那个 ⇒ **顶着别人的等级**，只用于排错/对账）")
    ap.add_argument("--pair-badge-absent", action="store_true", default=None,
                    help="**无丸配无丸**：目标自图定不到徽章时照出，但**底图也必须是没徽章那张**"
                         "（只换名字、不动徽章位）；默认关 = 目标无丸就不规划（口径④）")
    ap.add_argument("--sprite-rects", default=None,
                    help="外部徽章矩形 JSON（口径④ 的兜底：上游/素材版给坐标，见 load_sprite_rects）")
    ap.add_argument("--livepipe-join", action="store_true", default=None,
                    help="A 档：plan 时把每张导出图 join 回 livepipe 的 comments/rows，"
                         "落 `livepipe_join.json`（只读、不参与判据；缺 comments 就降级）")
    ap.add_argument("--level-pair", action="store_true", default=None,
                    help="C 档：三级等级链 —— 同等级首选 / 未知退回宽度 / 明确不同排除"
                         "（只改配对；脏值只降级不排除）")
    ap.add_argument("--level-pair-soft", action="store_true", default=None,
                    help="C′（事后变体，未预注册）：等级明确不同只排在同级/未知之后，不排除"
                         "（隐式开启 --level-pair）")
    ap.add_argument("--two-pass", action="store_true", default=None,
                    help="D 档：两遍量测 —— 结构闸不过的非目标图延后徽章（目标自己的图仍每张都量），"
                         "徽章列扫描收窄到名字槽位左侧")
    ap.add_argument("--resume", action="store_true", default=None,
                    help="E 档：断点续跑 —— 按 `expand_run_progress.jsonl` 复用已完成且指纹一致的片")
    ap.add_argument("--cache", action="store_true", default=None,
                    help="B 档：plan 时读/写量测缓存 `measure_cache.json`（默认关；"
                         "同文件同框第二次量测几乎为 0）")
    ap.add_argument("--only", default=None, help="只跑这些用户（目录名 / 显示名，逗号分隔；P3 用）")
    ap.add_argument("--limit", type=int, default=None, help="每个用户最多出几张")
    ap.add_argument("--samples", type=int, default=None, help="sample 模式出几张")
    ap.add_argument("--out", default=None, help="run 模式的**预演树**（给了就不动语料）；sample 模式默认 /tmp/expand_samples")
    ap.add_argument("--yes", action="store_true", default=None, help="就地写回语料要显式确认")
    ap.add_argument("--no-rename", action="store_true", default=None, help="就地写回时**不改目录名**")
    ap.add_argument("--skip-md5", action="store_true", default=None, help="不记原图 md5（省时间；默认记）")
    ap.add_argument("--quality", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--roi-inset", type=int, default=None,
                    help="框取自 manifest.roi 时的**横向**内缩量（roi 是紧贴值，见 ROI_INSET）")
    ap.add_argument("--roi-vinset", type=int, default=None,
                    help="框取自 manifest.roi 时的**纵向**内缩量（默认 0，见 ROI_VINSET）")
    args = ap.parse_args()
    try:
        from_cli, from_json, cfg_path = runconfig.load(
            args, DEFAULTS, script_file=__file__)
    except runconfig.ConfigError as exc:
        print(f"运行配置有问题：{exc}", file=sys.stderr)
        return 2
    args.source = runconfig.path(args.source)
    args.sprite_rects = runconfig.path(args.sprite_rects)
    args.out = runconfig.path(args.out)
    if args.source is None:
        print("没给语料目录：命令行 `--source` 或配置文件里的 `\"source\"`", file=sys.stderr)
        return 2
    print(runconfig.describe(cfg_path, from_cli, from_json))
    if args.mode == "sample":
        args.out = args.out or Path("/tmp/expand_samples")
        return cmd_sample(args)
    if args.mode == "run":
        return cmd_run(args)
    return cmd_plan(args)


if __name__ == "__main__":
    sys.exit(main())
