"""livepipe.tools.weibo_emoji.source"""

from __future__ import annotations

import re


SOURCE = "weibo-pc-panel"
SOURCE_DESC = ("微博 PC 端表情面板（weibo.com 发博框的表情面板）；key 与中文名取自面板 DOM，"
               "图片取自 face.t.sinajs.cn（img.t.sinajs.cn 为镜像）")
SOURCE_CFG = "weibo-config"
CDN_DIR = "https://face.t.sinajs.cn/t4/appstyle/expression/ext/normal"
#: 官方发博框 config（需要登录态）。
CONFIG_API = "https://weibo.com/ajax/statuses/config"
#: 公开镜像里的 config 快照（2025-10-17）；``--config`` 不带值时用它。
CONFIG_URL = ("https://raw.githubusercontent.com/EqualMa/weibo-emoji/main/"
              "src/data/config-20251017.json")
#: 只解析、不上面板的老表情分组。
PARSE_ONLY_GROUP = "仅解析"
# 同一条 URL 的两个 host（face 是主、img 是镜像）；后缀 _org（原图）/ _thumb（缩略图）
HOSTS = ("face.t.sinajs.cn", "img.t.sinajs.cn")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
REFERER = "https://weibo.com/"

# 分组标记：``class="m-box-center m-box-center-a face-wrap default"``。
# 注意**每个表情的 div 上都有它**，所以它同时也是「这条表情属于哪组」的分界线。
_GROUP_RE = re.compile(r'face-wrap\s+([A-Za-z0-9_-]+)')
# 表情本体：``class="face_wrapper face face-<key>">中文名</span>``
_FACE_RE = re.compile(r'class="[^"]*\bface_wrapper\b[^"]*\bface-([A-Za-z0-9_-]+)"[^>]*>([^<]*)<')
# 文件名里不该出现的字符（中文名直接当文件名，防一手分隔符/空白）
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f\s]+')

# 阈值：**部分标定**，见段首说明。两半成色不同 ——
#   height_ratio 实量得来（微博录屏上表情渲染 32px ÷ 行距 74 = 0.43）；
#   min_score / min_margin / min_saturation 是 2026-10-26 在
#   《2-2026-06-17艾瑞泽8征服版上市/微博/16：38-17：22.mp4》上按「报警器」流程量的
#   （15 个抽样帧、1251 个原始候选）：真表情（打call）37 个 ⇒ score 0.825~0.853、
#   margin 0.126~0.167；其余 1214 个 ⇒ score ≤0.820、margin ≤0.116。高分档的杂项
#   **全是白字**（平均饱和度 0，形近撞上 `[林致]`/`[乔殊]`/`[小夭]`），所以再加一道
#   颜色关 `min_saturation`（只在「分数不够命名」时生效，真表情与白色系模板不受影响）。
# 原来这四个数是照视频号抄的（0.50 / 0.85 / 0.80 / 0.10），在微博素材上把表情**全丢光**
# （bug-305）；0.80/0.10 又会让白字冒充表情（bug-466）。
THRESHOLDS = {
    "height_ratio": 0.43,
    "looks_like": 0.75,
    "min_score": 0.82,
    "min_margin": 0.12,
    "coarse_min": 0.55,
    "coarse_cap": 40,
    "min_saturation": 30.0,
}
#: 标定状态：``False`` = 没标 / ``"partial"`` = 部分标定（见段首）/ ``True`` = 完整标定。
CALIBRATED: bool | str = "partial"
THRESHOLDS_NOTE = {
    False: ("**未标定**：这组数是照视频号（微信）表抄的起点值。跑微博素材前先按 "
            "parse_live_comments.py 的『报警器』流程量一遍（压 looks_like、抬 min_score，"
            "扫几十帧看分数分布是不是干净双峰）；线不对的表现是**静默丢光**。"),
    "partial": ("**部分标定（2026-10-26 复核）**：`height_ratio` 0.43 是**实量**得来（微博录屏上"
                "表情渲染 32px ÷ 行距 74）——原值 0.50 照视频号抄，会让表情全体丢在第一道闸门"
                "（bug-305）；`min_score` 0.82 / `min_margin` 0.12 由**报警器**流程在本素材"
                "（39 帧 / 1251 个原始候选）上量的：真表情 37 个 ≥0.825 / ≥0.126，杂项 1214 个"
                "≤0.820 / ≤0.116；`min_saturation` 30 挡掉「白字撞表情模板」的假阳性（那些杂项"
                "里分数够高的**全是白字**，S=0，而真表情 S≥111）。仍只有**一种字形**（打call）"
                "交叉验证，拿到更多带表情的微博素材后应按同一流程复核。"),
    True: "已按微博侧素材标定。",
}[CALIBRATED]
