# livepipe — 抖音/视频号/微博 直播录屏 → 结构化评论

把原先散在 `scripts/` 下的 6 个脚本收进一个包，用**一个 JSON 文件**串成一条流水线。
**从零跑一遍的完整步骤（命令 / 产物 / 耗时 / 排错）见 [`全流程说明.md`](全流程说明.md)。**

```
livepipe/
├── __main__.py                 python -m livepipe（run / panels / check / steps / example）
├── config/                     JSON → argparse.Namespace（不改管线，只喂管线）
│   ├── __init__.py             全量 re-export（`from livepipe import config as C` 照旧）
│   ├── defaults.py             DEFAULTS
│   ├── args.py / panels.py     配置合并 / 平台档案展开
│   └── io.py / paths.py / steps.py / constants.py / errors.py
├── pipeline.py                 按顺序执行 8 个步骤（逐段步按段循环，全局步只跑一次）
├── parsing/                    ★ 解析核心（按层拆分，依赖单向向下）
│   ├── common/  text/  layout/  constants.py
│   ├── panels/                 面板定位（LocateAnything / OCR / hybrid）
│   ├── emoji/                  表情表加载 + 模板匹配
│   ├── ocr/                    抽帧 / RapidOCR / 行网格 / 药丸二次读数
│   ├── comments/               评论组装 / 浮层 / 跨帧去重
│   ├── render.py  reporting.py 标注图 / summary / 统计
│   ├── cli/                    run / parser / draw
│   └── selftest/               7 组内置断言（ROI 再拆 7 个 case 文件）
├── tools/                      独立 CLI 的实现（原路径保留为薄壳）
│   ├── weibo_emoji/  douyin_emoji/  wechat_emoji/  kuaishou_emoji/   建表情表
│   ├── export_user_images/     合格用户 → 交付图目录
│   ├── user_filter/            评论 → 合格用户（大模型判「正面」）
│   ├── remedy_badges/          按新口径重算已交付的树
│   ├── dataset/                识别结果 → YOLO 数据集（复核图 + 八道闸）
│   ├── merge_segments/         跨段 qualify / collect
│   └── list_match/             撞库：名单 × 直播全量账号（命中/未命中/匹配度）
├── parse_live_comments.py      兼容 facade：re-export `parsing.*` + 旧命令行
├── export_user_images.py       薄壳 → tools/export_user_images/
├── dataset.py                  薄壳 → tools/dataset/（识别结果 → YOLO 数据集）
├── user_filter.py              薄壳 → tools/user_filter/
├── remedy_badges.py            薄壳 → tools/remedy_badges/
├── merge_segments.py           薄壳 → tools/merge_segments/
├── list_match.py               薄壳 → tools/list_match/（撞库）
├── douyin_emoji_table.py       薄壳 → tools/douyin_emoji/
├── wechat_emoji_table.py       薄壳 → tools/wechat_emoji/
├── weibo_emoji_table.py        薄壳 → tools/weibo_emoji/
├── kuaishou_emoji_table.py     薄壳 → tools/kuaishou_emoji/
├── account_id.py               ★ 交付账号身份 = (昵称, 等级标 ◆N)：**唯一实现**（见 CONFIG.md）
├── locate_panels.py            面板定位（parse_live_comments 的兼容壳）
├── batch_locate_panels.py      批量定位 + 稳定性统计（挑固定矩形用）
├── draw_boxes.py               把框画到某一帧上
├── CONFIG.md                   ★ 配置逐参数详解
├── 全流程说明.md                ★ 从录屏到合格用户的完整跑法（命令 / 耗时 / 排错）
└── examples/
    ├── pipeline.example.json   完整注释样例（可直接跑）
    ├── pipeline.minimal.json   最小配置
    └── panels.example.json     面板文件样例（已标注 platform）
```

> 说明：`locate_anything_infer.py`（LocateAnything 通用 CLI）不属于本管线，留在原 `Embodied/scripts/` 仓库，不进本工程。

### 代码结构：为什么拆、改东西去哪找

`parse_live_comments.py` 原来是 7,844 行单体，2026-10-27 按 AST **机械拆**到 `parsing/`
（只搬代码、不改逻辑；循环依赖改成函数内延迟导入）。原文件保留为 486 行兼容 facade，
`from livepipe.parse_live_comments import …` 和原来的命令行都照旧。依赖方向是单向的：

```
common → text → layout → constants / panels / emoji → ocr → comments → render / reporting → cli → selftest
```

| 想改什么 | 去哪 |
|---|---|
| 行文本规则、通知/系统行清洗、徽章、`classify_line` | `parsing/text/` |
| ROI、续行/折行、段落组装 | `parsing/layout/` |
| 抽帧策略、RapidOCR、行网格、药丸二次读数 | `parsing/ocr/` |
| 表情表加载、模板匹配、空槽补扫 | `parsing/emoji/` |
| 评论组装、浮层过滤、跨帧去重 | `parsing/comments/` |
| 面板定位（model / ocr / hybrid） | `parsing/panels/` |
| 标注图、summary、统计 | `parsing/render.py` · `parsing/reporting.py` |
| 自检断言 | `parsing/selftest/`（每组一个文件，ROI 再分 `roi_*.py`） |
| 配置合并 / 平台档案 / 参数解析 | `config/` |
| 独立工具（导出、过滤、补救、表情表、撞库） | `tools/<name>/`；原 `livepipe/<name>.py` 只是薄壳 |
| 流水线编排 | `pipeline.py` |

每个 Python 文件保持小体量：144 个 `.py`，中位 **70 行**、118 个 <200 行；最大是
`parse_live_comments.py` 的 486 行（纯 re-export facade）。新增实现优先放进对应层的小文件，
不要往 facade 里加逻辑。

## 🚩 两阶段工作流（核心用法）

**评论区范围是可变的**（视频号尤其），而定位只能说「这里有一片评论」，说不出「这是哪个平台」。
平台又决定 **行距 + 表情表**（四套资产互不通用，见下节「表情表」），填错整片认不出 —— 所以这一步交给人工。

```bash
# ① 定位：产出待标注的面板文件（platform 留空）
python -m livepipe panels cfg.json

# ② 人工标注：打开 panels.file，给每个面板填 platform（douyin / wechat；微博见下节「表情表」）

# ③ 解析：面板文件已存在 ⇒ 阶段①自动跳过（不覆盖你的标注），按平台处理
python -m livepipe run cfg.json
```

> `panels.mode` **不用手写**：不写 = 自动 —— `file` 在就复用、不在就定位。所以①之后直接③即可。

面板文件（人工编辑的就是它）：

```jsonc
{
  "panels": {
    "left":  { "rect": [575, 604, 1137, 1429], "platform": "douyin" },
    "right": { "rect": [1202, 1052, 1688, 1311], "platform": "wechat" }
  }
}
```

「不同平台走不一样的处理方式」= 配置里的 `platforms` 段（每个平台一行网格 + 一张表情表 + 裁切策略）：

```jsonc
"platforms": {
  "douyin": { "emoji": "douyin", "row_pitch": 68, "y_phase": 17, "x_text": 70, "x2_from": "content" },
  "wechat": { "emoji": "wechat", "row_pitch": 61, "y_phase": 21, "x_text": 60, "x2_from": "window" },
  // 微博：等级标在昵称右边（「榜N」药丸）⇒ rank_pill；没有左侧徽章列 ⇒ wrap_at_text_left
  "weibo":  { "emoji": "weibo", "row_pitch": 74, "y_phase": 45, "x_text": 38, "x2_from": "content",
              "rank_pill": true, "wrap_at_text_left": true }
}
```

`rank_pill` 只有「等级标跟在昵称右边」的平台要开（微博「榜一…榜九」药丸）：开了之后
该行按药丸切成 昵称/正文，**药丸本身不记录在任何字段里**；不开就会被并进用户名，冒号再被
OCR 吃掉时两个用户的评论还会搅成一条。判据与量法见 [`CONFIG.md`](CONFIG.md)。

`wrap_at_text_left` 只有**没有左侧徽章列**的平台要开（微博）：那种版式下折行续行跟首行
一样从正文列左沿起（实测两簇 x1 完全重叠），形状认不出折行，改按「与上一行的间距」认
（折行 ≈0.6 个行距 vs 独立评论 ≈1.0），认下的挂小数行槽再合并。同样是版式事实、不开就
一条都并不上；详见 [`CONFIG.md`](CONFIG.md)。

`x2_from` 决定**裁切右沿**怎么定：`content` = 跟内容走（面板固定宽的平台，裁得紧、OCR 省）；
`window` = 固定取窗口右沿当超集。视频号的面板**右边跟着内容伸缩**（实测整片内容右沿摆动 686px），
右沿由内容定就必然在某一帧开始**裁内容**（被裁的行会误读，不只是截断），所以它用 `window`。

`badge_span`（默认 58）决定 **ROI 左沿** = `x_text − badge_span`，也就是标注图/交付裁图
圈进去的左侧徽章列有多宽。默认值量的是桌面网页版（`x_text` 落在药丸右端内侧）；手机 App
的 `x_text` 在药丸右沿**外侧**，得写更大的值（`douyin_app` = 74），否则框会把 `◆` 菱形切掉。
代价是该面板裁切面积 ×2.8、全片 OCR 约 +38%。`window` 还会把 ROI 右边界换成「该帧内容右沿 + pad」，
免得每条评论的框都被拉到窗口边。

## 快速开始

```bash
# 干跑：打印将要执行什么（含还原出的命令行），一行都不跑
python -m livepipe run livepipe/examples/pipeline.example.json --dry-run

# 真跑样例（只抽 3 帧，约 22 秒）
python -m livepipe run livepipe/examples/pipeline.example.json

# 校验配置 / 看某配置合并默认值后的全量取值
python -m livepipe check livepipe/examples/pipeline.example.json
python -m livepipe run cfg.json --print-config

# 列出可用步骤 / 导出样例配置
python -m livepipe steps
python -m livepipe example my.json

# 描框（配置模式）：只给配置 + 输出文件夹，自动按 input.videos 找每段视频与面板
python livepipe/draw_boxes.py --config livepipe/风云T7.json --out out/风云T7_20260826_11_02/panels
python livepipe/draw_boxes.py --config livepipe/风云T7.json --segments seg6,seg7 --at 800 \
    --out out/panels        # → seg6_panels_800s.jpg / seg7_panels_800s.jpg
```

需要 OCR/模型时用带 `rapidocr`/`torch` 的解释器（如 conda env `vlx`）：

```bash
P=/home/puyu/anaconda3/envs/vlx/bin/python
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 $P -m livepipe panels cfg.json   # 定位（用模型）
```

命令行 `--only`/`--skip` 可临时覆盖配置里的步骤（`--only` 点名的步骤一律执行，见下）：

```bash
python -m livepipe run cfg.json --only parse      # 只重抽评论
python -m livepipe run cfg.json --skip emoji_tables,draw
```

## 流水线的八步

| 步骤 | 范围 | 做什么 | 谁在干 |
|---|---|---|---|
| `emoji_tables` | 全局 | 建/刷新表情表（`build.enabled=true` 的表） | `douyin_emoji_table` / `wechat_emoji_table` / `weibo_emoji_table` / `kuaishou_emoji_table` |
| `panels` | 逐段 | 按 `panels.mode` 定位 / 落盘 / 复用面板（产出待人工标注的面板文件） | `locate_panels_auto` / `batch_locate_panels` / 手写 |
| `parse` | 逐段 | **核心**：按平台展开面板 → OCR 抽行 → 表情识别 → 解析 → 跨帧去重 → 出图（抽帧策略见下） | `parse_live_comments.run` |
| `filter` | 逐段 | 评论 → 合格用户：用户名 ≥N 条**正面**评论（大模型判定，含表情），排除官方侧账号 | `user_filter` |
| `qualify` | 全局 | **跨段判合格**：各段正面条数按用户名**合计** ≥N 才算合格，回写每段 `users_qualified.jsonl` | `merge_segments.qualify` |
| `export` | 逐段 | 出交付图：按**原帧**画绿框，一人一个目录 | `export_user_images` |
| `collect` | 全局 | **拼交付树**：同名账号并成一个目录、图号跨段连续编号 | `merge_segments.collect` |
| `draw` | 逐段 | 把某时刻附近的框画到一帧上（独立描框） | `draw_boxes` |

> **范围** = 多段素材（`input.videos`：一场直播切成几段录屏）下这一步跑几遍：**逐段**步骤每段各跑
> 一遍、各自写 `<output.dir>/<段名>/`；**全局**步骤整批只跑一次（它要看所有段的产物）。
> 单段素材下两者没有区别。顺序有依赖：`filter` → `qualify` → `export` → `collect`，别拆开。
> 详见 [`CONFIG.md`](CONFIG.md) 的「多段素材」与「`qualify` / `export` / `collect`」两节。
>
> `--only` 也**拉不回** `steps.skip` 里的步骤（skip 先算）：想让某步「默认不跑、要点名才跑」，
> 就把它的 `enabled` 留 `false`（别放进 `skip`），然后 `--only` 点名 —— 这也是「花钱的 `filter`
> 默认关着，但要跑时一条命令就能带上」的做法。

## 表情表：四套资产，互不通用

平台那一行里的 `emoji` 指的是**哪张表**。表建在 `out/<平台>_emoji/`，建一次长期复用
（`emoji_tables` 步骤只重建 `build.enabled=true` 的表；建表要联网）。

| 源名 | 身份（查表的钥匙） | 条数 | 表 | 建表命令 |
|---|---|---|---|---|
| `douyin` | `byHash`：图片 URL 里那 32 位十六进制 | 214 | `out/douyin_emoji/douyin-emoji.json` | `python3 livepipe/douyin_emoji_table.py --api --out out/douyin_emoji` |
| `wechat` | `byId`：序号 `001`~`109` | 109 | `out/wechat_emoji/wechat-emoji.json` | `python3 livepipe/wechat_emoji_table.py --fetch --out out/wechat_emoji` |
| `weibo` | `byId`：面板 key（手工模式）/ CDN stem（`--config` 自动模式）+ `byCdn` | 338（556 含仅解析） | `out/weibo_emoji/weibo-emoji.json` | `python3 livepipe/weibo_emoji_table.py --config --out out/weibo_emoji` |
| `kuaishou` | `byId`：`001_名称`（token 来自 live.kuaishou.com 页面内嵌的 emojiPanel） | 207 | `out/kuaishou_emoji/kuaishou-emoji.json` | `python3 livepipe/kuaishou_emoji_table.py --out out/kuaishou_emoji` |

**四套身份各不相同**是这几家 CDN 的现实，不是设计选择：抖音的图 URL 里带内容哈希，微信那套只有
序号，微博的面板 DOM 里只有 key，快手只有 token→图片 URL 的页面配置（`[666]` → yximgs PNG）——
而 key 推不出 CDN 文件名，所以微博表另加 `byCdn`（CDN 文件名与去掉 `_org`/`_thumb` 的 stem 都查得到），
用来认网页评论 `<img src>` 里那种只有文件名的情形。
微博的自动模式（`--config`）拿不到面板 key，`byId` 改用 CDN stem（`2021_yuhangyuan` 这种）；
`byToken` / `byName` / `byCdn` 与模板匹配都不受影响。

**阈值跟表走，不跟代码走。** 每张表自带 `thresholds`，`load_emoji_gallery` 直接读走（表里的值优先于
脚本内置默认；表里缺这一块就退回**抖音侧**那组默认值，并往 stderr 打警告）。别把某一平台的阈值当
全局常量 —— 抖音是官方无损 webp（能要求 0.85），视频号是 128px PNG 被渲染成 ~30px 再录屏压一道
（真表情最高 0.876），拿抖音那组数去卡视频号，结果是**一条都认不出来**，而且是**静默**的：
产物看起来像「这一屏没有表情」。

**纯表情评论要活着走完全程**（表情是图片、OCR 只认文字，这类行常常连打码昵称都读不出来）：
① 有文字的行走正常路径；② 只剩碎片（`***`）的行 —— 只要这行认出了表情就当评论收下
（正文空、内容由 `emoji_text` 补；系统行/通知照旧丢）；③ 一个 OCR 行都没有的**空槽** ——
在相邻两条已检出的行之间按行网格补扫表情，认到有名字的表情才造行。ROI 取「文字框 ∪ 徽章 ∪
表情框」的并集，否则 ②的框只有 10px 高、表情全在框外。⚠ 这三步都在**抽行那一刻**做
（进 `rows.jsonl`）：已有行序走 `--rows` 复算不会补出新行，要补得重抽那一段。

> ⚠️ **微博那张表的 `thresholds` 是「部分标定」**（表里 `"thresholds_calibrated": "partial"`）：
> `min_score=0.82` / `min_margin=0.12` / `min_saturation=30` 是 2026-10-26 在微博录屏上按
> `parsing/emoji/scan.py` 的「报警器」流程量的（15 帧 / 1251 个候选：真 `[打call]` 37 个
> 分数 0.825~0.853、区分度 0.126~0.167；其余 1214 个 ≤0.820 / ≤0.116），`height_ratio=0.43`
> 更早量过（表情 32px ÷ 行距 74）。但只有 **`[打call]` 一种字形**交叉验证 ⇒ 新素材仍要复核。
> 阈值不对的表现是**静默丢光**（产物像「这一屏没有表情」）或**满屏假框**：整块白字与某些模板的
> NCC 能到 0.75~0.82，会冒出 `?[林致]` 圈在「车」字上，所以新表还要填 `min_saturation`
> （见 [`tools/weibo_emoji/doc.py`](tools/weibo_emoji/doc.py) 段首）。

选表两种写法（`面板名=源名或表路径`，逗号分隔、可重复；源名在 `parse_live_comments.SOURCE_TABLES` 里查）：

```jsonc
// ① 配置里：平台档案带表（面板填 platform，表跟着平台走）
"platforms": {
  "douyin": { "emoji": "douyin", "row_pitch": 68, … },
  "wechat": { "emoji": "wechat", "row_pitch": 61, … }
}
```

```bash
# ② 命令行点表（不经平台档案）
python livepipe/parse_live_comments.py --video V.mp4 --emoji-table left=douyin,right=weibo
```

微博的平台档案是 **`platforms.weibo`**（手机 App 竖屏，行距 74 / 相位 45 /
`x_text` 38 / `rank_pill: true` / `wrap_at_text_left: true`，从 `/home/puyu/work/data/微博-奇瑞汽车/16：38-17：22.mp4` 量出来的）。
**档案跟着配置走**，不是全局注册表 —— 当前 checkout 里它写在 **`livepipe/征服8.json`**
（那份的面板 rect x1=10 ⇒ 档案里 `x_text` 取 **28**；上面的 38 按 rect x1=0 量，两者是同一个
原图位置。**换成自己的 rect 时记得一起平移**，写大了会把「榜N」行的昵称当徽章列丢掉）。
别的配置想用微博，得把 `platforms.weibo` 那段抄过去，否则就只能用写法 ② 点表；而**只点表给不出行距、
也给不出药丸/折行开关**。系统行（横幅 / 进房通知 / 开播公告）按 `_NOCOLON_NOTICE_RE` 清掉
（进房通知认核心词「播间」、点赞通知认「为主播点」，**都不锚行尾** —— OCR 会把两条通知并成
一行、把「赞」读成「带」），「昵称 + 榜N 药丸」按 `rank_pill` 拆开（全量 450 → **481** 条，
含「榜」的 307 → **0** 条），续行判据补了最小缩进 `_INDENT_MIN_PX`（原来昵称列的 1~2px
抖动会把一屏评论并成一条）、折行改走 `wrap_at_text_left` 的**间距入口**（没有徽章列 ⇒ 形状
判据两簇 x1 完全重叠，全片 91 对折行原本一对都并不上）。
**表情按 `[打call]` 一种字形标定过**（`min_score=0.82` / `min_margin=0.12` / `min_saturation=30`，
见上节「表情表」的警告框）。表情是**锦上添花**：表不在只会打警告降级，不会让主流程跑不动。

## 抽帧策略（给 `parse` 提速）

`parse` 是最慢的一步，耗时 ≈ **抽的帧数 × 每帧 OCR 时间**。默认每 `interval` 秒固定抽一帧，
同时踩两个坑：**静止时**对着一模一样的画面反复 OCR（白拍），**快滚时**又可能整条评论溜过两次
拍摄之间（漏条）。`input.sample.mode` 换个策略能同时治这两个：

> **每帧里谁慢（实测 2560×1520 / 左右两面板 / 单进程，逐环节计时）**：**表情模板匹配 ≈4.9s/帧（68%）**
> · RapidOCR ≈2.1s/帧（29%）· 抽帧 seek ≈0.2s/帧。所以「给 parse 提速」的大头是**表情识别**不是 OCR
> —— 少抽帧两边一起省。OCR 引擎本身已调过（`use_cls=False` + 8 算子线程，见 `parsing/ocr/engine.py`），
> 实测无损（同一 30s 切片逐行完全一致，10.96 → 8.82 s/帧）。

| mode | 行为 | 适用 |
|---|---|---|
| `interval` | 固定间隔（默认，行为与从前逐字节一致） | 短片段 / 需要固定帧复现 |
| `backoff` | 仍按 `interval` 抽，但**只在画面真变了才拍**、按变化自适应间隔 | `adaptive` 嫌重时的降级版；便宜，突发爆量只能事后反应 |
| `adaptive` | **顺序解码**，按面板位移/停稳/入口**内容触发**；只 OCR 触发了的面板 | 长录屏（推荐）：不漏且少重复 |

```jsonc
"input": { "video": "…", "sample": { "mode": "adaptive" } }
```

`adaptive` 的三个触发（每面板各自判）：**位移安全网**（累计位移 ≥ 若干行 ⇒ 拍，保证不漏，
阈值还会被半个可见屏收紧，短面板不漏）· **停稳触发**（动过之后静止几帧 ⇒ 拍，一次抓全一屏）·
**入口触发**（没在滚但底部入口行变了 ⇒ 判「新评论弹进来」⇒ 拍）。位移用面板 ROI 的**灰度行投影
互相关**估 —— 礼物/点赞动画不平移行网格，天然不触发。

实测（3 秒滚动 + 27 秒静止的一段）：`adaptive` 拍 **4 帧**、固定间隔拍 15 帧（**少 73% 的 OCR**），
且滚动那段一条不漏。每次拍摄把瞬时位移写进行（`motion_px`），跨帧去重时同一条评论改留**拍得最清楚**
的那次。逐参数见 [`CONFIG.md`](CONFIG.md)。

## 标注图圈出分隔冒号（`parse.colon_marks`）

解析层切「昵称 | 正文」的唯一依据就是那个分隔冒号（`:` / `：`），所以标注图默认把它**圈出来**
（青色小方框，和 ROI 彩框、灰文字框、黄表情框并存）—— 昵称被 OCR 啃掉一截、或冒号被吃掉
导致整条评论并进上一条时，看这个框就知道切点落在哪。

```jsonc
"parse": { "colon_marks": true }        // 默认 true；只影响标注图，交付图不动
```

```bash
python livepipe/parse_live_comments.py --video V.mp4 --out out/x                  # 默认就画
python livepipe/parse_live_comments.py --video V.mp4 --out out/x --no-colon-marks # 关掉
```

- **先按像素找，OCR 插值兜底**：RapidOCR 只给整块文字框、不给字符框，而且 `：` 常被整块吃掉
  （微博「榜N」药丸行实测 178/212 行）—— 所以先回**原帧**找冒号的那两个小圆点（`colon_box_by_pixels`：
  暗底上两团 4×5px 上下叠放、间距 7px），找得到就直接用它；找不到才按「冒号是本块第几个字」在块宽内
  插值（`colon_box_in_row`；旧行序没有 `colon_box` 时按整行框插值 `colon_box_in_span`）。块级位置在
  抽行时算好写进 `rows.jsonl` 的 `colon_box`，再带进 `comments.jsonl`。
- **搜索框右沿比 OCR 文字块放宽 48px**（`_COLON_PROBE_RIGHT`）：药丸行里 OCR 的文字块止于药丸
  右沿（冒号与表情都不算「文字」），真冒号落在块外那段 ≈44px 的版式空隙里 —— 实测
  `用户6861943086榜一：😭×4` 块右沿 331、冒号 360。不放宽，**这一整类行永远是「没画冒号」**。
- **`--rows` 复算不用重跑 OCR**：旧行序没有 `colon_box` 时按整行文字框插值兜底（精度略低，
  但足够圈住冒号）。只想给已有产物补标注图：`input.rows` 指向 `rows.jsonl` + `output.frames: true`，
  跑 `python -m livepipe run cfg.json --only parse`。
- 续行没有冒号；合并后保留首行那一份。**只影响 `frames/` 标注图**，交付图（`export`）不动。
- **打码昵称的 `*` 贴着冒号时**：像素法会拿插值位置当先验（`hint`，±40px）挑最近的点对，
  否则「左右 3px 内有字」的旧判据会把这一整类真冒号挡掉；插值也从按字数等分改成按字宽加权
  （全角 1 / 半角 0.55）。修复前后的实测数据（4633 个框、中位 −17px）见
  [`docs/colon-fix-report.html`](docs/colon-fix-report.html)。

## 评论 → 合格用户（`filter` 步骤）

默认**关**（要花钱调模型）。打开后按规则筛人：

```jsonc
"filter": {
  "enabled": true,
  "min_positive": 3,          // 用户名至少 3 条正面评论
  "exclude_keywords": ["奇瑞", "4s", "店", "qq3", "汽车", "尹同跃", …],
  "nlp": { "model": "deepseek-v4-flash-0731", "batch_size": 20, "concurrency": 4 }
}
```

「正面」由大模型逐条判（`正面`/`负面`/`中性`），**表情一起发给它** —— 纯表情评论的
`content` 是空串，不看 `emoji_text` 就全被判成中性了。两处省钱机制（都精确、不影响结果）：
评论数 < N 的用户根本不可能合格 ⇒ 不送模型；判定结果按内容哈希缓存 ⇒ 重跑不花钱。

```bash
NLP_API_KEY=sk-… python -m livepipe run cfg.json --only filter   # 跑（key 用环境变量，别写进配置）
python livepipe/user_filter.py --comments out/x/comments.jsonl --dry-run   # 先估算调用量
```

产出 `users.jsonl`（合格用户 + 逐条判定）· `users.json`（汇总 + 被排除的用户名）· `judgments.jsonl`（缓存）。

## 撞库（`match` 子命令 / `list_match.py`）

拿**活动方给的名单**撞一遍：名单上的人在**直播里**有没有、匹配度多少、图在哪。
范围默认是**直播全量** —— 发过**任意一条**评论（正面/负面/中性）就算，不要求达到
交付阈值（≥3 张）；只要交付树里的人：`--delivered-only`。

```bash
python -m livepipe match 撞库名单/10.31人名单.xlsx out/10.31            # 阈值默认 0.8
python -m livepipe match 撞库名单/10.31人名单.xlsx out/10.31 -t 0.9    # 更严
python -m livepipe match 撞库名单/10.31人名单.xlsx out/10.31 --delivered-only  # 只撞交付树
python -m livepipe match 名单.txt out/10.31 --platform 抖音            # 纯文本名单
python livepipe/list_match.py --selftest                              # 纯函数自检（106 项）
```

- **名单格式**：xlsx / csv 表里平台表头下面（或右边）是人名，例如
  `序号 | 抖音 | 序号 | 视频号 | 序号 | 快手`。`--layout auto` 自己判断；
  `.txt` 一行一个名字，用 `--platform` 指定平台。**按平台各撞各的**，不跨平台凑。
- **直播路径**：`run` 产出的直播目录（`out/10.31`）。撞库账号 = 各段
  `<平台>/seg*/comments.jsonl` 按 (昵称, 等级标) 去重后的**全部账号**
  （发过任意一条评论就算，正负面不看；排除词/认人口径与 `filter` 同源）∪ 交付树
  `<平台>/all_users_正面_<N>张以上/`：同一个人以交付树身份为准（保留账号目录），
  只在直播里的记 `in_delivery=False`。评论数/正面数按平台共用的
  `<平台>/judgments.jsonl` 判定缓存回填；没有 `seg*/comments.jsonl` 的老产物
  退回 `seg*/users.jsonl`（只有正面评论的账号）并告警。
  同一平台有多棵交付树（不同阈值）时用 `--tree` 指定。
- **匹配度**：名字先归一化（NFKC、忽略大小写、去空白/零宽；标点/数字/emoji 保留），
  默认 `--metric partial` = 整名相似度与「短名在长名上逐窗口比」取大（容忍昵称被截断：
  `期待`↔`期待sj`、`喜乐哥`↔`喜乐哥554`）；`--metric ratio` 只比整名，更保守。
  直播全量账号多，防误撞：短名做前后缀时，多余那段是汉字就只按整体 ratio 算
  （`用户`↔`用户6758305` ✅、`天后`↔`明天后天` ❌）；夹在中间的片段只对 ≥3 字的短名做窗口比。
  ≥ 阈值的算命中；一个名单名可命中多个账号（同名不同等级标/OCR 近似档）。
- **产出**（默认 `<直播路径>/撞库结果/`）：`matches.json` · `命中明细.csv` ·
  `未命中明细.csv`（**没命中的也逐条留下最近账号和分数**，方便复核是阈值卡掉还是真没拍到）·
  `撞库结果.xlsx`（装了 openpyxl 才写）· 生成的 `README.md`；**标记图**默认导出到
  `标记图/<平台>/<名单名>/<账号目录>/1.jpg …` —— 就是交付树里的图**原样**复制
  （整帧原图 + 该条评论一个绿框，不裁、不加字），按名单姓名重新归档，可直接发活动方核对。
  只要表格不要图：`--no-images`。**只在直播里、没进交付树**的命中没有现成图，
  会按同样的交付口径从视频原帧**现补**（整帧 + 绿框、顺序解码，见 bug-264），
  有正面评论的取正面、零正面的按中性/负面评论补（否则一张都挑不出来）；
  几个账号就要几十秒到几分钟；同一账号跨段出现加段名前缀，重跑会覆盖同段的图。
- **HTML 交付清单**（默认写）：`撞库清单/<日期>_<场次>_<平台>.html` —— 一平台一页，
  顶部 5 个 KPI + 人数/图片两条占比条，中间是可搜索、可点表头排序的「撞库用户名单」，
  下面附「未命中名单」与口径说明；多平台再写 `汇总.html`。场次/日期自动从
  `cwd` 里 `output.dir` 指向本平台的配置取 `name`（找不到就用直播目录名），
  `--title` / `--date` 可强制。清单不嵌图，只给「首图」链接指向 `../标记图/…`。
  不要清单：`--no-html`。
- 读 `.xlsx` 需要 openpyxl；csv/txt 与匹配本身零第三方依赖。

## 配置格式

**逐参数详解见 [`CONFIG.md`](CONFIG.md)**（类型 / 默认值 / 含义 / 对应 CLI）；
带逐参数中文注释、可直接跑的实例见 `examples/pipeline.example.json`。
顶层节：`platforms` · `input` · `emoji` · `panels` · `parse` · `output` · `filter` ·
`qualify` · `export` · `collect` · `draw` · `steps`。

配置写成 **JSONC**（支持 `//`、`/* */` 注释与末尾多余逗号），所以例子里的每个参数都能就地备注。

### `panels.mode` 一览

| mode | 含义 |
|---|---|
| `locate` | **① 定位**：产出待人工标注的面板文件（platform 留空） |
| `reuse` | **② 复用**人工标注过的面板文件 |
| `manual` | 手写面板（rect + platform）写进 `panels.file` |
| `auto` | 解析时自己定位（需 GPU）；平台按 `auto.platforms` 归属（无人值守快捷路径） |
| `batch` | 批量定位 + 稳定性统计（挑固定矩形用；不动 `panels.file`） |
| `builtin` | 用脚本内置的 `DEFAULT_PANELS`（只对特定录屏有效） |

## 产物

落在 `output.dir`（**多段素材**下逐段产物都在 `<output.dir>/<段名>/` 里，见「多段素材」）：

- `comments.jsonl` — 唯一评论（含 `roi`/`text_box`/`colon_box`/`badge_box`/`emojis`/`emoji_text`；`adaptive` 抽帧下另带 `motion_px`）
- `rows.jsonl` — 行序（可回喂复算、省一次 OCR）
- `panels.json` — 面板文件（**人工标注 platform 的那个**）
- `panels.resolved.json` — 按平台展开后的完整面板（喂给核心脚本）
- `summary.json` — 统计 + 出图清单 + 定位/表情取证（`adaptive` 抽帧下另有 `sample_mode`/`sample_stats`）
- `frames/<t>_roi.jpg` — 逐帧标注图（`output.frames` 控制；每条评论的 ROI + 编号 + 用户名，`parse.colon_marks` 默认把分隔冒号也圈出来；全片时建议关，3147 张 ≈1.7GB）
- `users.jsonl` — `filter` 出的合格用户（**逐段**；喂给 `qualify`）
- `users_qualified.jsonl` — `qualify` 判完合格后回写的每段名单（喂给 `export`）
- `merged_users.json` — **跨段**合计口径的名单（每个账号各段各几条），`qualify` 的取证
- `<平台中文名>/all_users_正面_3张以上/` — **交付图**（`export` 步骤 / `export_user_images.py`）

多段素材另有**交付树**（`collect` 步骤拼的）：`<output.dir>/<平台中文名>/all_users_正面_<合计阈值>张以上/`
—— 同名账号**并成一个目录**、图号跨段连续编号，带 `正面账号.txt`、`manifest.json` 与 **`README.md`**。
那份 `README.md`（讲「目录名里的 `◆` 怎么读」+ 本批的账号/张数/分段账）由 `collect` **生成**，
每次重跑都现算 —— 因为这棵树每次 collect 都被 rmtree 重建，**手写一份放进去下次就没了**。
账号身份是 **(昵称, 等级标 ◆N)**（见 `account_id.py`）⇒ 同一个**打码**昵称底下的人会拆成
`白___◆19-5/`、`白___◆21-3/`、`白___-6/` 这样的多个目录。改了认人口径（或解析层的等级标读数）
之后，用 **`python3 -m livepipe.remedy_badges <cfg.json> [--apply]`** 把已经交出去的树按新口径
重算一遍：先印「旧目录 → 新档 + 逐图归属 + 哪些档会因为拆开而不足阈值」的单子，
`--apply` 才 export/collect 落盘，并按**身份**（段·时刻·正文·表情）核账 —— **不是 sha1**：
**只改阈值**（`qualify.min_positive`）时加 **`--no-parse`**：读数一个字节没变（parse 的产物就是各段
`comments.jsonl`），没必要再回原帧重读一遍（七段约半小时）；脚本会逐段印出复用的那份 `comments.jsonl`
的**时间戳**，缺一段就停 —— 复用旧产物可以，**看不出有多旧**不行。⚠️ 交付目录名跟着阈值走
（`all_users_正面_<阈值>张以上/`）⇒ 改阈值前先把旧树 `mv` 成新名字，它才好当「冻结的现状」核账。
图每次 export 都重画，字节会漂（零 OCR；判定缓存全命中时零模型调用）。
开了 `parse.badge_reread`（`--badge-reread`）时唯一例外：parse 会按每行的**药丸带**回原帧把
等级标重读一遍（只跑识别、跳过 det；**不重抽行**）—— 面板整屏那一遍读不出的药丸（实测只有
三到四成读得出）靠它补；补上的是**等级数字**，直接决定「谁和谁是一个账号」。

## 直接跑单个脚本

合并后各脚本仍可独立运行（与合并前一致）。这些 `livepipe/<tool>.py` 现在都是薄壳
（实现见 `tools/<tool>/`），`python livepipe/<tool>.py` 与 `python -m livepipe.<tool>` 等价：

```bash
python livepipe/parse_live_comments.py --video V.mp4 --auto-panels hybrid --out out/x
python livepipe/parse_live_comments.py --video V.mp4 --sample-mode adaptive --out out/x  # 内容触发抽帧
python livepipe/parse_live_comments.py            # 无参 = 内置自检（零第三方依赖）

python livepipe/export_user_images.py --users out/x/users.jsonl   # 导出交付图（见下节）
python livepipe/export_user_images.py --selftest                  # 纯函数自检（同上，零第三方依赖）
```

## 导出交付图（按平台分目录 · 每用户一个文件夹 · 每条评论一张图）

`filter` 出完合格用户后，把他们的评论导成交付形态。管线里这是 **`export` 步骤**
（`export.enabled=true`，跑 `run` 时会跟着走）；下面是直接调脚本的等价命令：

```bash
python livepipe/export_user_images.py --users out/livepipe_example3/users.jsonl
# → out/livepipe_example3/抖音/all_users_正面_3张以上/<用户名>-<条数>/1.jpg 2.jpg …
#   out/livepipe_example3/视频号/all_users_正面_3张以上/…
#   每个平台目录里各带 manifest.json + 正面账号.txt

# 留白（默认 max(0px, 0.25×行高)）：紧贴 / 更宽松 / 固定像素
python livepipe/export_user_images.py --users …/users.jsonl --pad-ratio 0      # 参考语料那种紧贴
python livepipe/export_user_images.py --users …/users.jsonl --pad-ratio 0.4    # 更宽松
python livepipe/export_user_images.py --users …/users.jsonl --pad 10           # 不管行高，一律 10px

# 只想看会出哪些用户、几张图（不写文件；输出目录非空时先看一眼再决定 --force）
python livepipe/export_user_images.py --users …/users.jsonl --dry-run
```

- **按平台分目录**（`<平台中文名>/all_users_<标签>_<N>张以上/`），与参考交付语料
  `2-2026-06-17艾瑞泽8征服版上市/{视频号,抖音,微博}/all_users_正面_3张以上/` 同形 ——
  平台由评论的 `panel` 查 `panels.resolved.json` 得到。
- 每张图 = **整帧原图 + 该条评论一个绿框**：只圈这一条，**图上没有任何识别出来的文字/编号**。
  对齐参考语料实测：整帧不裁 · 绿框 RGB(0,255,0) · 线宽 3。
- **框留白**：外扩量 = `max(--pad, --pad-ratio × 行高)`，默认 `0px / 0.25`（行高 29px ⇒ 外扩
  7px），外扩后夹在画面内（贴面板边的评论坐标会越界，PIL 会把那条边静默裁掉）。按行高取比例是
  因为同一批语料行高 20~109px 差 5 倍（带表情、折行的高条），固定像素对矮行嫌紧、对高行看不见。
  调法见上面的例子；要参考语料那种**紧贴不留白**就 `--pad-ratio 0`。
- 底图从**视频**按 `summary.json` 记的 `sample_mode`/`start` 取：`adaptive` 就**顺序解码数
  计数器**（与抽行同一把尺子），`interval`/`backoff` 才 seek。**绝不把 adaptive 的行配上按 t
  seek 的取帧** —— 变帧率录屏上 seek 会漂（实测最多晚 218 帧），漂了框就画在另一时刻的画面上
  （bug-264）。`--base annotated` 可退回复制 `frames/` 里的标注图（调试用，带着编号和同帧
  **全部**评论的框，不是交付形态）。
- 阈值默认**按平台各算**（`--min-scope platform`）：一个账号抖音 2 条、视频号 2 条，两个平台
  都不该被算成「正面账号 ≥3」；`--min-scope global` 是「整段视频够数就进每个平台」的旧口径。
  因此**不够数的平台那一侧不出图**（日志会点名这种账号）。
- `--labels`（默认 `正面`）/ `--min-positive`（3）/ `--flat`（不分平台）/ `--no-box` /
  `--color`/`--width`/`--pad`/`--pad-ratio` / `--limit` / `--dry-run` / `--force` 见 `--help`；
  `--selftest` 跑纯函数自检（不读盘、不依赖 Pillow/OpenCV）。

### 账号身份：匿名（打码昵称）怎么配置

平台把观众昵称打码成 `爱**` / `用***`（**末尾星号**）时，同一个显示名底下可能是几十个真人 ——
只按昵称认人会把陌生人并成一个账号。交付这一层按**平台档案**的 `account_key` 决定身份：

| `account_key` | 身份怎么算 | 什么时候用 |
|---|---|---|
| `"username"`（默认） | 只看昵称 | 昵称完整、没有打码 |
| `"nickname_level"` | **(昵称, 等级标 `◆N`)**，等级不一致就是两个人 | **整场都打码**（抖音 App 常见） |
| `"nickname_level_if_masked"` | 打码昵称 `爱**` → (昵称, 等级标)；不打码昵称 `微风许甜` → 只看昵称 | **一场里匿名/非匿名混着出现**（视频号常见） |

配置写在 `platforms` 段（不是面板条目），加一行即可：

```jsonc
"platforms": {
  "wechat_app": {
    "label": "视频号直播（手机 App 竖屏）",
    // ……行距 / x_text / 表情表……
    "account_key": "nickname_level_if_masked"   // ← 匿名按等级拆、非匿名只看昵称
  }
}
```

**怎么判「匿名」**：昵称**末尾**是 `*` 或全角 `＊`（`爱**` / `用***` / `乐**`）；中间带 `*`
的真实昵称不算。判定在 `livepipe/account_id.py` 的 `is_masked()`。

**规矩**（2026-11-08 用户定）：

- **匿名（打码）**：先看**可见部分**（星号之前露出来的那几个字，通常就是第一个字，见
  `visible_name()`）—— 必须**逐字 100% 相同**才算同一个名字：`小***` 与 `健***` 是两个人；
  星号个数被 OCR 读错**不算差异**（`小***` / `小**` 的可见名都是 `小`）。名字对上之后，
  再要求**等级标一致**（两边都读到且相同）；等级标读不到 ⇒ 不并，自成一档 `(昵称, "")`，
  **不赌**（宁可漏归一，不可错归并）。
- 打码名底下本来就挂着几十个真人，模板化直播里任意两个号共用一句文案是常态 —— 所以
  **形近（相似度 ≥0.7）、大小写、共用正文这些通道对打码名一律关闭**，只走「可见部分逐字
  相同 + 等级标一致」。药丸前缀例外：`15木***` 是「徽章列里的药丸 `15` + 昵称 `木***`」，
  剥掉后名字逐字相同，前缀数字就是这张行读到的等级标。
- **非匿名**：只看昵称 —— OCR 把等级标读错/漏读**不会**把同一个人拆成两个账号
  （旧 `nickname_level` 一刀切会）。

**生效范围与重跑**：

- 写了这个键后，`panels` 步把口径带进 `<段目录>/panels.resolved.json`，`filter` 按它分组，
  `qualify` / `export` / `collect` 沿用 `users.jsonl` 里的 `level` 字段。
- **改口径不需要重跑 OCR**：`comments.jsonl` 里的 `badge_text` 已经够用 ——
  `python -m livepipe run cfg.json --only panels,filter,qualify,export,collect`。
- 单独跑 `user_filter` 的等价命令行：`--identity-panels comments --identity-masked comments`
  （前者=哪些面板要判身份，后者=其中哪些**只对打码昵称**拆等级）。
- 交付目录名：匿名的带等级标 `爱**◆1-3/`，非匿名的仍是 `微风许甜-5/`；`正面账号.txt` 同理。

⚠ 键写错（拼错取值）会在 `check` 阶段当场报错，不会静默退回「只看昵称」。
老配置不写这个键 ⇒ 行为与以前逐字节相同（opt-in）。

### 多段素材：出图之后还要拼成一棵树

一场直播切成几段录屏时（`input.videos`），各段各出一份导出目录，同一个账号会有
`张三-2`（seg1）和 `张三-2-2`（seg2）两个目录 —— 交付树里就成了两个账号。`collect` 步骤
按**账号身份**把它们并成一个 `张三-4`、图号 1..N **跨段连续**，并写 `正面账号.txt` + `manifest.json`
（账号身份默认是**昵称**；昵称打码/匿名的素材用平台档案的 `account_key` 改成
**(昵称, 等级标 ◆N)**，目录名就成了 `用***◆16-3` —— 三档口径与配置见上一节
[「账号身份：匿名（打码昵称）怎么配置」](#账号身份匿名打码昵称怎么配置)，
细节见 [`CONFIG.md`](CONFIG.md#account_key--交付这一层按什么认人抖音手机-app风云t7json) 与 `account_id.py`）。
**等级标读不到 = 匿名的那个显示名单独成档 `(昵称, "")`**（不会混进读到的某个等级，也不会
并回非匿名）；所以读数是这一层的命门：`parse.badge_reread`（等级标二次读数）不写时
**与 `account_key` 绑定** —— 按等级认人的配置自动开（没读到的回原帧按药丸带补读、只补
不改），只看昵称的配置自动关（读了也不参与身份）。要强制覆盖才显式写 `true`/`false`。
同理，合格判定（`qualify`）也是**跨段合计**判的 —— 两件事的来龙去脉见
[`CONFIG.md`](CONFIG.md#qualify--export--collect--跨段与交付多段素材)。

## 识别结果 → YOLO 数据集（`dataset`）

把各段的 `comments.jsonl` 折成标准 YOLO 结构。**中间夹一步人**：先出复核图让人删，
删掉的评论就不会进数据集（`review` → 人工 → `build`）。

```bash
# ① 出复核图（顺序解码一次，每条评论一张横条）
python3 -m livepipe.dataset review --runs out/风云T7_20260826_11 out/风云T7_20260826_11_02 out/征服8 \
    --exclude 风云T7_20260826_11_02/seg7 --out out/dataset_v1
#    口径变了要刷新旧横条：同一命令加 --redraw（仍尊重 rejected.jsonl，删掉的不会回来）
#    排除某段：--exclude 风云T7_20260826_11_02/seg7（子串匹配 run/seg）

# ② 人在 out/dataset_v1/review/ 里看：
#    · 删掉不对的          → 这条评论四个类都不进
#    · 整帧都不能用的      → python3 -m livepipe.dataset build --out out/dataset_v1 \
#                                --reject-frame 12-01-13-00_f010946（这一帧的全部评论一起丢）

# ③ 算账 → 落盘 + 八道闸
python3 -m livepipe.dataset build --out out/dataset_v1 --dry-run
python3 -m livepipe.dataset build --out out/dataset_v1
python3 -m livepipe.dataset build --out out/dataset_v1 --restore 12-01-13-00_f010466_03.jpg
python3 -m livepipe.dataset --selftest          # 纯函数自检（不读盘、不要 cv2/Pillow）
```

四个类（顺序 = `cls` 编号）：`comment_row`（`roi`）/ `body_text`（`text_box`）/
`colon`（`colon_box`）/ `emoji_run`（`emojis[].box`）。

当前实测（2026-10-29 重建）：3,989 个复核单元 / 2,865 图 / 12,384 框
（train 1,904 · val 519 · test 442），八道闸全过；从 5 类迁到 4 类时移除了
1,818 个等级标框（train 1,372 · val 426 · test 20）。

**等级标（`badge_box`）不进数据集**：上游 850 条「有读数没坐标」，写进去就是 32.2% 的
可见等级标假负。要继续用等级标，先修上游框检测，再把 `(level_badge, badge_box)` 加回
`tools/dataset/common.py` 的 `CLASSES`（cls 编号会整体后移）。改造实测见
[`docs/dataset-4class-report.html`](docs/dataset-4class-report.html)。

- **一张图 = 一帧原图**（不裁），同帧的多条评论各出一到五行标签 —— 图名
  `<视频键>_f<帧号>.jpg`，标签同名 `.txt`；`manifest.json` 记每张图来自哪段视频、哪一帧。
- **帧号只能 `round(t × fps)` 现算**，取帧走 `export_user_images` 那把 `FrameReader`
  （`adaptive` = 顺序解码，不 seek —— 变帧率录屏上 seek 会漂，bug-264）。
- **一个复核单元 = 一条看得见的评论**：同一 (视频, 帧) 下 ROI IoU ≥ 0.6 的记录并成一条
  （同一批录屏常被两个 run 各解析一遍），删一次就全没了；落标签时同类框再按 IoU 去重。
- **`username` / `colon_box` 为空的行直接过滤**：昵称或冒号没读出来的行，`body_text` 起点和
  `colon` 标签都不可信；`review` 前丢掉，`LABEL_SOURCE` 闸再从 `index.jsonl` 反查一遍。
- **split 按视频切，不按帧随机切**（相邻帧只差一条新评论，随机切 = 把答案抄进验证集）。
  默认规则：路径含「微博」→ test，`17：13-17：33` → val，其余 train；`--split-file` 可换。
- 复核图**放 `<out>/review/`，不能放 `frames/`**：那是 parse 的产物，下次 parse 会被
  `render.py` 的 `stale.unlink()` 整个清空。`review/index.jsonl` 是权威清单，
  `build` 只认它 + 磁盘状态（`rejected.jsonl`），不再扫 run 目录。
- `build` 跑完自动过**八道闸**（`REVIEW_MATCH` / `LABEL_SOURCE` / `ROUNDTRIP` / `IN_FRAME` /
  `SPLIT_LEAK` / `DUP_IMAGE` / `CLASS_COVER` / `PREVIEW`），并**从标签文件反画** 20 张到
  `preview/` —— 不过就整批不许用。方案与实测数字见 `docs/dataset.html`。
- **刻意不进 `pipeline.py` 的 `steps`**：它是跨 run 的全局步骤，而且 `review` 和 `build`
  之间必须夹人工复核；接进默认步骤顺序会把所有现有配置的跑法改掉。
- `--exclude` 用来剔掉同一段视频的复算段（如 `风云T7_20260826_11_02/seg7`）——
  不剔的话同一帧会被两个 run 各算一遍，图数翻倍；`docs/dataset.html` 的数字是剔掉之后量的。
- 为什么复核单位是「一条评论」而不是「一整帧」、两种口径各剩多少数据、逐条的假负与训练侧 ignore 策略：
  见 [`docs/review_granularity.html`](docs/review_granularity.html)（对称账本 + 触发条件敏感性 + 例子 + 训练侧依据
  + §10 A/B 实测；结论：保持逐条，整帧口径五组评测的整体 mAP 全部更低）。
