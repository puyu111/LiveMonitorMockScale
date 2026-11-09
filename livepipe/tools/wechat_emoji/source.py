"""livepipe.tools.wechat_emoji.source"""

from __future__ import annotations

import re


REPO = "airinghost/wechat-emoji"
BRANCH = "main"
SUBDIR = "web.wechat.com/uncompressed"       # 仓库里 128×128 无压缩 PNG 的目录
TARBALL = f"https://codeload.github.com/{REPO}/tar.gz/refs/heads/{BRANCH}"
SOURCE = "wechat-web-2021"
SOURCE_DESC = ("微信 2021 新版表情（视频号 / web.wechat.com 聊天窗都用它）；"
               f"资产 {REPO} {SUBDIR}")
# 文件名形如 ``080_强.png`` —— 序号 + 官方中文名，token 直接由它拼出来
FNAME_RE = re.compile(r"^(\d{3})_(.+)\.png$")

# 按源配的阈值（**量出来的**，不是拍的）。标定方法：把 looks_like 压到 0、min_score 抬到
# 天上（一律不写 token），在右面板上扫 210 帧 / 498 行，把每个候选的原始分数拉出来看分布
# （就是 parse_live_comments.py 里说的「报警器」）。结果**双峰干净**：
#
#   精验分：  ≥0.85 的 208 个（多数 0.96，就是那一片 👍 [强]、🌹 [玫瑰]、[微笑]…）
#             0.70~0.85 只有 9 个，0.65~0.70 有 557 个，0.55~0.65 有 2734 个
#   山谷 0.72~0.84 很宽 —— 左边是杂项（**空背景被 NCC 匹配出来的假峰**：TM_CCOEFF_NORMED
#   在近乎平坦的窗口上分母趋零，随便就 0.68~0.72，认出来全是 [红包]/[菜刀] 这种，
#   框里是纯背景）；右边是真表情（最低的一个是真 [爱心] 0.84）。
#
#   height_ratio 0.50：右面板行距 61px，表情实测高 30px（61×0.50=30.5→30，梯子 28/30/32）。
#                      拿抖音那套 0.56 会试 32/34/36 —— 全比真字形大，NCC 掉下去。
#   looks_like   0.80：杂项实测最高 0.72，真表情最低 0.84 ⇒ 线放 0.80 两边各留 0.08/0.04。
#   min_score    0.85：208 个真表情全在这条线以上（[强] 0.964、[玫瑰] 0.946）；
#                      0.84 那个 [爱心] 会以 token=null + guess 上报（宁可不说，别说错）。
#   min_margin   0.10：真表情的 margin 分布是**断的** —— 192 个 ≥0.122（[强] 0.23、
#                      [玫瑰] 0.58），另外 16 个全挤在 0.041~0.055（[微笑]/[呲牙] 这族
#                      本来就长得一样）。0.10 落在这道缝里，正好把「认不准的那族」留下不写。
#   coarse_min   0.55：跟抖音同值。**故意留松** —— 粗筛只负责「提议」，判分一律看精验；
#                      抬高它只会让「精验分很高但粗筛分不高」的真表情连提议都拿不到。
#   coarse_cap   40  ：杂项把候选数顶到每行 390 个（中位），cap 是唯一的花钱闸门；
#                      真表情的粗筛分 ~0.95 排第一，cap=40 切不到它。
DEFAULT_THRESHOLDS = {
    "height_ratio": 0.50,
    "looks_like": 0.80,
    "min_score": 0.85,
    "min_margin": 0.10,
    "coarse_min": 0.55,
    "coarse_cap": 40,
}
