"""原命令行工具的完整说明（``make_parser`` 的 description）。"""

from __future__ import annotations

DOC = """从 GitHub 拿微信「新版」表情（109 个）→ 建表 ``out/wechat_emoji/wechat-emoji.json``。

背景：微信有两代表情，**视频号/网页聊天用的是 2021 那代（109 个）**
------------------------------------------------------------------
  * 老代（2007 那批，105 个）：24×24 动图 GIF，公开挂在
    ``https://res.wx.qq.com/mpres/htmledition/images/icon/emotion/{0..104}.gif``；
  * 新代（2021，109 个）：128×128 PNG，视频号、``web.wechat.com`` 聊天窗都用它。

为什么必须分清：两代的**同一个表情画得不是一个东西**（老的 79 号「强」是竖大拇指、
新的 ``080_强`` 也是竖大拇指但配色/手势全变）。实测拿错代去匹配视频号的 👍，最高只有
0.756；拿对代能到 **0.876**。差这 0.12 正好落在阈值线上 —— 认成「没有表情」。

身份是什么
----------
抖音那套的身份是图床对象名里的 32 位 hash（内容寻址、天然唯一）；微信这套**没有 hash**，
身份就是**序号 001~109**（官方顺序，和官方 ``display_name`` 一一对应）。所以：

    token = "[强]"      name = "强"      id = "080"      img = "080_强.png"
    index = 80          （token 由文件名直接给，不用人工标注 —— 没有猜名字这一步）

这是**表结构上的一个分叉**：抖音表的索引键是 ``byHash``，这份是 ``byId`` / ``byIndex``。
``parse_live_comments.load_emoji_gallery`` 两个都认，靠 ``byId`` 优先、``byHash`` 兜底。

资产从哪来
----------
官方 CDN 没找到能匿名枚举的接口（视频号助手 ``channels.weixin.qq.com`` 的 JS 里
根本没有表情资源引用）。最终用的是一个公开的镜像仓：

    repo  airinghost/wechat-emoji      path  web.wechat.com/uncompressed/<NNN_名称>.png

**用 tarball 拉，别逐个 raw**（2026-10-06 实测）：``raw.githubusercontent.com`` 在这台机器上
超时（20s 无响应），而 ``codeload.github.com`` 1.4s 下完整个仓（4.6MB，109 张都够）。
所以 ``fetch_tarball`` 走 codeload，只解出 ``web.wechat.com/uncompressed/*.png``。

流程
----
1. ``--src DIR``   直接用手上已有的目录（离线、可重复）；
   ``--fetch``     否则从 codeload 下 tarball 解出来。
2. 资产落到 ``<out>/img/<NNN_名称>.png``（**文件名就是 token 的来源**，别改名）。
3. contact sheet ``<out>/sheet_01.png``：每格一张图 + 序号 —— 只为了**人眼核一遍**
   「序号对不对应这个名字」，不参与建表。
4. 写 ``<out>/wechat-emoji.json``：``byId`` / ``byIndex`` / ``byToken`` / ``byName``
   四个索引 + ``thresholds``（**按源配的阈值**，见下）。

thresholds 为什么在表里
-----------------------
三个阈值（``looks_like``/``min_score``/``min_margin``）都是拿某个源**量出来**的：
抖音官方图是无损 webp，真表情 NCC ≥0.90；微信这套是 128px PNG 被渲染到 ~30px 再录屏，
真表情只有 0.68~0.88。**同一组数字跨源用不了** —— 抖音的 0.85 卡在微信侧就是「一个都不认」，
而且会静默降级成「这屏没表情」。所以阈值跟表走，``--emoji-table`` 选了哪张表就用哪组的。

用法
----
    # 建表（联网一次；也可以 --src 指向已下好的目录）
    python3 livepipe/wechat_emoji_table.py --fetch --out out/wechat_emoji

    # 拿它跑评论抽取（面板级的表选择见 parse_live_comments.py --emoji-table）
    python3 livepipe/parse_live_comments.py --video xxx.mp4 --emoji-table right=wechat

注意
----
依赖：Python 3 标准库 + Pillow。下载走 ``urllib``，不需要 requests。
"""
