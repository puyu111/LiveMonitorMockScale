"""原命令行工具的完整说明（``make_parser`` 的 description）。"""

from __future__ import annotations

DOC = """从抖音网页 DOM 里抽出表情表（图 URL + hash → 名称/含义）。

背景
----
抖音评论里的表情在 DOM 里长这样：

    <span class="uORo8cFf"><img src="https://p3-pc-sign.douyinpic.com/obj/
        tos-cn-i-tsj2vxp0zn/<32位hash>?lk3s=...&x-expires=...&x-signature=...&from=..."></span>

也就是说 **表情的身份就是那段 32 位 hash**（`tos-cn-i-tsj2vxp0zn/` 后面的对象名）。
拿到 hash → 表情的映射，评论的「表情提取」就是一次查表，完全不用碰像素；
反过来这批图本身又是像素侧模板匹配的素材库。

实测（2026-10-06）：
  * 不带 ``x-signature`` 查询串 → **403**，签名必需，URL 必须整条保留。
  * 带签名 → 200，``image/webp``，**lossless + alpha**（约 6.8KB/张，200 张 ≈ 1.4MB）。
    alpha 很重要：模板可以直接合成到深色聊天背景上做匹配。

**首选走 ``--api``**（2026-10-06 实测可用）。官方接口直接给全表，连名字都不用猜：

    GET https://www.douyin.com/aweme/v1/web/emoji/list/?device_platform=webapp&aid=6383&channel=channel_pc_web

返回 ``{"status_code":0, "version":<毫秒时间戳>, "emoji_list":[...214 条...]}``，每条：

    {"origin_uri": "weixiao.png",          # 拼音 slug
     "display_name": "[微笑]",              # **官方名（带方括号的 token）**
     "hide": 0,
     "emoji_url": {"uri": "tos-cn-i-tsj2vxp0zn/<32位hash>",
                   "url_list": ["https://p3-pc-sign.douyinpic.com/obj/...", "https://p9-..."]}}

``uri`` 里的 hash 与网页 DOM 里 ``<img src>`` 的 hash **同一个命名空间**，且
**顺序一致**（实测 DOM 前三条 54a0f69d/27a1d35d/88ebe7b3 = [微笑]/[色]/[发呆]），
所以 API 表可以直接给 DOM 里的表情做反查，不需要人工看图命名。

流程
----
1. ``--html``     解析 DOM，按出现顺序去重抽出 (hash, signed_url)。
2. 下载到 ``<out>/img/<hash>.webp``（已存在则跳过；带 UA/Referer，失败重试）。
3. 拼 contact sheet ``<out>/sheet_01.png`` …：每格一张图 + 角标序号。
   序号是**人肉标注的连接键** —— 看着图写 ``{"1": {"token": "[微笑]", ...}}``，
   不用把 hash 抄一遍。
4. ``--labels`` + ``--merge``  把标注并进最终表 ``<out>/douyin-emoji.json``：
   ``byToken`` / ``byName`` / ``byHash`` 三个索引（沿用小红书那份表情表的形态），
   hash 与 url 一律从第 1 步的原始数据带过来，不经过人手。

用法
----
    # 1) 抽 URL + 下载 + 出 sheet
    python3 livepipe/douyin_emoji_table.py --html /tmp/douyin_emoji_panel.html \
        --out out/douyin_emoji

    # 2) 看 sheet_NN.png，写 labels.json（键是 sheet 上的序号）
    #    {"1": {"token": "[微笑]", "name": "微笑", "meaning": "礼貌性微笑/敷衍"},
    #     "2": {"token": "[色]",   "name": "色",   "meaning": "色眯眯/馋"}}
    # 3) 合并
    python3 livepipe/douyin_emoji_table.py --html /tmp/douyin_emoji_panel.html \
        --out out/douyin_emoji --labels labels.json --merge

注意
----
依赖：Python 3 标准库 + Pillow（本机已装）。下载走 ``urllib``，不需要 requests。

DOM 是从浏览器复制出来的，``&`` 被转义成 ``&amp;`` —— 解析时必须 ``html.unescape``，
否则 URL 里的查询串是坏的（这会表现成全部 403）。
"""
