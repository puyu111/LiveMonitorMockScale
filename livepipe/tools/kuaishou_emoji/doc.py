"""livepipe.tools.kuaishou_emoji.doc — CLI 说明。"""

DOC = """快手直播官方表情表（共 207 个，来源 live.kuaishou.com 页面内嵌 emojiPanel）。

    python3 livepipe/kuaishou_emoji_table.py --out out/kuaishou_emoji
    python3 livepipe/kuaishou_emoji_table.py --out out/kuaishou_emoji --src /tmp/ks_live.html
    python3 livepipe/kuaishou_emoji_table.py --out out/kuaishou_emoji --src /tmp/panel.json

产物：
    out/kuaishou_emoji/kuaishou-emoji.json   表情表（byId / byToken / byName + thresholds）
    out/kuaishou_emoji/img/NNN_名称.png      官方表情原图
    out/kuaishou_emoji/sheet_01.png …        验收表（每格下面是 token）

为什么 PNG 是 100×100 而表里的 height_ratio=0.667：快手 App 把官方表情渲染成
30px 高（590×1280、行距 45 实测），30/45=0.667；表里还带了实测阈值
（真表情 NCC 0.845~0.964、纯文字最高 0.80）。别把抖音的 0.56/0.85 直接套过来。
"""
