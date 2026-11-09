"""原命令行工具的完整说明（``make_parser`` 的 description）。"""

from __future__ import annotations

DOC = """从微博 PC 端表情面板建表 ``out/weibo_emoji/weibo-emoji.json``。

背景：微博这边多一步 —— 面板上**没有图片 URL**
---------------------------------------------
微博 PC 端（``weibo.com`` 发博框的表情面板）在 DOM 里长这样：

    <div class="... face-wrap default">
      <span class="face_wrapper face face-cnxyhy">航天员</span>
    </div>
    <div class="... face-wrap default">
      <span class="face_wrapper face face-cnxzqgjc">千古玦尘</span>
    </div>

和另外两个源都不一样：
  * 抖音的 DOM 里**直接写着图的 URL**（``tos-cn-i-tsj2vxp0zn/<32位hash>``）；
  * 微信的镜像仓里**文件名就带着名字**（``080_强.png``）；
  * 微博只有 ``face-<key>`` 这个内部 key 加一个中文名，**图得另找**。

图的 CDN 是新浪的老图床：::

    https://face.t.sinajs.cn/t4/appstyle/expression/ext/normal/<dir>/<stem>_<thumb|org>.png|gif
    dir = md5(stem)[-1] 的两位十六进制            # 面板那套 262/262 实测吻合

``_org`` / ``_thumb`` **实测是同一张图**（逐字节相同，尺寸也一样 —— 抽了 4 个 stem 逐个比对
过 md5），后缀只是历史习惯，不是两种分辨率；``img.t.sinajs.cn`` 是镜像（内容基本一致，
``lxhchijing`` 这种只剩镜像还在）。所以每条 URL 的候选有 2 个 host × 2 种后缀，
但**真正独立的只有 2 个**（后缀互换是空转），全 404 才算失败。

md5 目录规则不是**全局**真理：config 的 564 条 URL 里有 3 条（``2025_HappyDet`` /
``2024_Deadpool`` / ``2023_S_melons``）目录对不上，但**原 URL 实测 200**。所以对不上
只警告、**不改 URL** —— 按规则改目录反而 404。

身份是什么
----------
抖音的身份是图床对象名里那段 32 位 hash（内容寻址）；微信没有 hash，身份是顺序号 001~109；
微博是**第三种**：身份是面板的 ``key``（``cnxyhy`` / ``mickey01`` / ``2018new_aini``…）。
key 与 CDN 文件名是**两套命名，不能互推** —— 面板 key ``9zhuamulan``（花木兰）在 CDN 上
没有同名文件，反过来 CDN 的 ``2023_Batman`` 也对不上任何 key 的拼写。于是：

    token = "[航天员]"   name = "航天员"   id = "cnxyhy"
    index = 1            img  = "001_航天员.png"    cdn = "cnxyhy_org.png"

``byId`` 是 ``load_emoji_gallery`` 认的主索引（与微信表的 ``byId`` 同位，抖音表是 ``byHash``）。
另外多给一个 ``byCdn``：键是 **CDN 文件名**（``2018new_aini_org.png`` 这样的原名，以及去掉
``_thumb``/``_org`` 与扩展名的 stem，两种都查得到）。理由：录屏之外的路子（比如扒网页评论的
``<img src>``）拿到的是 CDN 名而不是面板 key，那把钥匙就是 ``byCdn``。

资产从哪来
----------
1. **面板定义**：``--panel`` 读一份从浏览器复制出来的面板 DOM，以前放在
   ``out/weibo_emoji/panel.html``（280 行，5 个分组）。``out/`` **不在 git 里**，
   被清掉就没了 —— 没有它就走下面的 ``--config`` 自动模式。
2. **key → URL 的解析表**：``--map``，键是面板 key，值是 URL + 置信度。以前放在
   ``out/weibo_emoji/panel_map.json``（同样不在 git 里）。这份表是**攒出来的**，
   微博没有可匿名调的接口：
     * 主体（225 条）来自三份公开映射的合并 —— npm ``weibo-face``、
       ``EqualMa/weibo-emoji`` 的 ``data.json`` / ``config-20251017.json``；
     * 4 条来自 ``Taengstagram/Weiboticons`` 的 2004 条 ``[短语] → URL`` 映射（那是微博旧
       API 的原始对应关系，``[萌] → kawayi`` / ``[康乃馨] → muqinjie`` 这种）；
     * 8 条是把含义拼成文件名后按 md5 规则**一次验证**命中（``蝙蝠侠 → 2023_Batman``、
       ``米奇比心 → mickey_bixin``…）；23 条是**受控爆破**命中（年/前缀/大小写/拼音变体
       逐个试，约 3.5 万次探测里剩下来的）。
   ``confidence`` 两档：``high``（映射表给的，或文件名与含义一一对应）/ ``guess``
   （文件确实在，但关联是推断的）。``guess`` 那几条**别当准的用**。
3. **图**：按 URL 下到 ``<out>/img/<NNN_名称>.<ext>``；``--src`` 给了就只从本地目录取，不联网。

自动模式（``--config``）
------------------------
上面两份输入都是**手工产物**、又不在 git 里，丢了就建不了表。所以另给一条不依赖它们的路：
直接读发博框的 **config JSON**（线上地址 ``/ajax/statuses/config``），每个表情就是
``{"phrase": "[航天员]", "url": "https://face.t.sinajs.cn/..."}`` —— 分组、中文名、URL
全在里面。唯一没有的是面板的 ``face-<key>``，所以自动模式下 ``id`` 改用 CDN stem
（``2021_yuhangyuan``）；``byToken`` / ``byName`` / ``byCdn`` 和模板匹配都不受影响。

取 config 的三条路：

  * ``--config``（不带值）→ 公开镜像 ``EqualMa/weibo-emoji`` 的 ``config-20251017.json``，
    无需登录（快照，可能比线上少几条）；
  * ``--config <文件>`` → 你自己保存的 config JSON；
  * ``--config https://weibo.com/ajax/statuses/config --cookie 'SUB=…; SUBP=…'`` →
    官方接口。**必须登录态**：匿名请求返回 ``{"ok":-100,"url":"…login.php…"}``。

默认只收面板上会显示的分组；``--include-parse-only`` 把「仅解析」分组（老表情，不在面板上，
但仍可能出现在评论里）也收进表 —— 模板越多扫描越慢、假阳性风险也略高。

流程
----
1. ``--panel``  解析面板 DOM → (group, key, 中文名)，按面板出现顺序编号 1..N。
2. ``--map``    逐行查 key → URL。**查不到的行不算失败**（微博 CDN 上就是没有这个素材），
   它们仍然留在 ``panel_raw.json`` 里并标 ``status=missing``。
3. 下载 → ``<out>/img/<NNN_名称>.<ext>``；顺带按 md5 规则核一遍 URL 里的目录对不对，
   对不上就出声 —— 目录写错的表现是** 404**，而 404 又和「这素材下架了」长得一模一样。
4. contact sheet ``<out>/sheet_NN.png``：每格一张图 + 序号 + 名字，给人眼核一遍。
5. 写 ``<out>/weibo-emoji.json``：``byId`` / ``byIndex`` / ``byToken`` / ``byName`` / ``byCdn``。

thresholds 为什么在表里，以及**这张表只「部分标定」**
------------------------------------------------------
道理跟抖音/微信两张表一样：阈值是**按源量出来**的，跨源用不了（抖音那套 0.85 卡在视频号上
是一个都不认，而且会**静默**降级成「这屏没表情」，见 ``parse_live_comments.py`` 第 4 节）。

微博这张**已经吃过一次这个亏**（bug-305）：起点值里的 ``height_ratio`` 是照视频号抄的 0.50，
× 微博行距 74 推出渲染高 37 ⇒ 高度梯子 (35,37,39)，而微博录屏上表情**实际渲染 32px** ——
三档 NCC 只有 0.680/0.554/0.451，**全低于 ``looks_like``**，于是 4 个 [打call] 在第一道闸门
就被当文字丢掉，日志报「表情 0 个 / 未识别 0 个」—— 和「这屏真没表情」长得一模一样。

现在这组数是**部分标定**（``CALIBRATED = "partial"``），两半的成色不一样，用之前分清：

* ``height_ratio`` **实量得来、站得住**：它是 UI 尺度、跟行距同源（同一个字号缩放），
  量法 =「行带里非背景色块的高 ÷ 行距」（`/home/puyu/work/data/微博-奇瑞汽车/…mp4` 上
  实测 32 ÷ 74 = 0.432），换素材重跑同一条命令即可复核。
* ``min_score`` / ``min_margin`` / ``min_saturation`` 是 2026-10-26 按**报警器**流程在本素材
  （`2-2026-06-17艾瑞泽8征服版上市/微博/…mp4`，15 个抽样帧）上量的：真表情（打call）37 个
  ⇒ score 0.825~0.853、margin 0.126~0.167；其余 1214 个原始候选 ⇒ ≤0.820 / ≤0.116。
  杂项里分数最高的那批**全是整块白字**（`车` 撞 `[林致]` 0.765 这类），平均饱和度 0，
  所以另加一道颜色关 ``min_saturation = 30``（真表情 ≥111）。**仍只有一种字形交叉验证**，
  拿到更多带表情的微博素材后按同一流程复核、把 ``CALIBRATED`` 置 ``True``。

用法
----
    # 自动建表（不需要面板 DOM / 解析表；默认从公开镜像取 config）
    python3 livepipe/weibo_emoji_table.py --out out/weibo_emoji --config

    # 实时 config（需要登录 cookie，最全最新）
    python3 livepipe/weibo_emoji_table.py --out out/weibo_emoji \
        --config https://weibo.com/ajax/statuses/config --cookie 'SUB=…; SUBP=…'

    # 手工模式（面板与解析表都在 <out>/ 下）
    python3 livepipe/weibo_emoji_table.py --out out/weibo_emoji

    # 离线重建（--src 指向按 CDN 原名存放的目录，子目录无所谓）
    python3 livepipe/weibo_emoji_table.py --out out/weibo_emoji --src /path/to/cdn_images

    # 拿它跑评论抽取（源名 weibo 见 parse_live_comments.SOURCE_TABLES）
    python3 livepipe/parse_live_comments.py --video xxx.mp4 --emoji-table right=weibo

注意
----
依赖：Python 3 标准库 + Pillow。下载走 ``urllib``，不需要 requests。

面板 DOM 是粘贴来的，解析前先 ``html.unescape`` —— 和抖音那份同一个坑。
"""
