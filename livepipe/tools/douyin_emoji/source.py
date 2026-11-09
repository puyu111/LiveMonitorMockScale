"""livepipe.tools.douyin_emoji.source"""

from __future__ import annotations

import re


# 抖音表情图的对象前缀。hash 是 32 位小写 hex（看着像 md5，但**不是**图片内容的 md5，
# 不能由图反推，只能从 DOM/接口里拿）。
OBJ_PREFIX = "tos-cn-i-tsj2vxp0zn/"
_URL_RE = re.compile(
    r"https://p3-pc-sign\.douyinpic\.com/obj/" + re.escape(OBJ_PREFIX) + r"([0-9a-f]{32})"
    r"(?:\?[^\"'\s<>&]*(?:&(?:amp;)?[^\"'\s<>&]*)*)?"
)
# 同一个表情在面板里可能出现多次；一张图只下载一次，出现次数记下来备查。

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")


API_URL = ("https://www.douyin.com/aweme/v1/web/emoji/list/"
           "?device_platform=webapp&aid=6383&channel=channel_pc_web")


# 该源的阈值（**跟着表走**，见 parse_live_comments.py 第 4 节 DEFAULT_THRESHOLDS 的说明）。
# 抖音侧这套数是量出来的：官方图是无损 webp 原生渲染，真表情 NCC ≥0.90、杂项 ≤0.65，
# 0.65~0.90 之间一个都没有，所以 looks_like 取山谷里的 0.78、min_score 取 0.85。
# **别把它套到别的源上** —— 微信（视频号）那套是 128px PNG 渲染到 ~30px 再录屏，
# 真表情只有 0.68~0.88，用这组数会全部静默丢掉。
DOUYIN_THRESHOLDS = {
    "height_ratio": 0.56,       # 表情高 = 0.56 × 行距（左面板 pitch 68 → 38px，实测 36~40）
    "looks_like": 0.78,
    "min_score": 0.85,
    "min_margin": 0.10,
    "coarse_min": 0.55,
    "coarse_cap": 400,
}
