# LiveMonitorMockScale

直播截图处理的两个子工程，**上下游串在一起**：

```
录屏 mp4 ──▶ livepipe ──▶ 结构化评论 / 合格用户 / 交付语料（含 manifest.json）
                                              │
                                              ▼
                      mock_scale ──▶ 借底图批量出片（假名字 mock / 真实用户补图 expand）
                                     + 换评论正文（body：按像素宽挑句写回）
```

| 子工程 | 干什么 | 入口 | 依赖 |
| --- | --- | --- | --- |
| **`livepipe/`** | 抖音 / 视频号 / 快手 / 微博录屏 → OCR 抽行 → 评论组装去重 → 筛「正面」用户 → 交付图与 `manifest.json`（交付后可用**撞库**核对名单） | `python3 -m livepipe run <cfg.json>`（另有 `panels` / `check` / `match` / `steps` / `example`） | 纯文本/配置校验零第三方依赖；碰视频或模型要用带 rapidocr + torch 的环境；撞库读 xlsx 要 openpyxl |
| **`mock_scale/`** | 本地确定性 inpainting：把框内用户名换掉、其余像素不动；两条换名出片线与一条正文替换线共用 `core/` 一份管线 | `python3 mock/make_spec.py` · `python3 core/batch_produce.py` · `python3 core/batch_run.py` · `python3 expand/expand_produce.py` · 正文线 `python3 mock/measure_bodies.py` → `python3 mock/gen_bodies.py` → `python3 core/batch_body.py` | numpy、opencv-python-headless、Pillow |

详细文档：

| 主题 | 去哪看 |
| --- | --- |
| livepipe 包结构与改动定位 | [`livepipe/README.md`](livepipe/README.md) |
| livepipe 从零跑一遍（命令 / 产物 / 耗时 / 排错） | [`livepipe/全流程说明.md`](livepipe/全流程说明.md) |
| livepipe 每个配置参数的取值、默认、坑 | [`livepipe/CONFIG.md`](livepipe/CONFIG.md) |
| 数据集怎么建、复核按条还是按整帧（含 A/B 实测判定） | [`livepipe/docs/dataset.html`](livepipe/docs/dataset.html) · [`livepipe/docs/review_granularity.html`](livepipe/docs/review_granularity.html) |
| 数据集 5 类 → 4 类改造（等级标退出）实测报告 | [`livepipe/docs/dataset-4class-report.html`](livepipe/docs/dataset-4class-report.html) |
| 分隔冒号标注框为什么偏、怎么修、修后验证（真实像素 + 前后样张） | [`livepipe/docs/colon-fix-report.html`](livepipe/docs/colon-fix-report.html) |
| mock_scale 两条换名线怎么共用 `core/`、配置怎么解析、旧路径映射 | [`mock_scale/docs/工程结构.md`](mock_scale/docs/工程结构.md) |
| mock_scale 为什么这样做、边界在哪 | [`mock_scale/docs/技术说明.md`](mock_scale/docs/技术说明.md) |
| mock_scale 按执行顺序走一遍管线 | [`mock_scale/docs/全流程说明.md`](mock_scale/docs/全流程说明.md) |
| 一套管线怎么同时跑抖音 / 视频号 / 微博（+ 快手 App） | [`mock_scale/docs/多平台方案.md`](mock_scale/docs/多平台方案.md) |
| mock 用户线 / 正文线 / 扩展张数线逐项用法 | [`mock_scale/mock/README.md`](mock_scale/mock/README.md) · [`mock_scale/expand/README.md`](mock_scale/expand/README.md) |
| 正文线（换评论正文）方案 / 首批验收 | [`mock_scale/docs/mock正文.html`](mock_scale/docs/mock正文.html) · [`mock_scale/docs/mock正文验收.html`](mock_scale/docs/mock正文验收.html) |
| 扩展张数线的需求、口径、逐轮验证记录 | [`mock_scale/expand/扩展张数.md`](mock_scale/expand/扩展张数.md) |

## 目录

```
LiveMonitorMockScale/
├── livepipe/                       # ① 录屏 → 结构化评论 → 合格用户
│   ├── __main__.py                 #   python -m livepipe（run / panels / check / steps / example）
│   ├── pipeline.py                 #   8 个步骤的顺序执行
│   ├── config/                     #   JSON → argparse（DEFAULTS / 合并 / 路径 / 步骤表）
│   ├── parsing/                    #   解析核心（common → text → layout → panels/emoji → ocr
│   │                               #   → comments → render/reporting → cli → selftest，单向依赖）
│   ├── tools/                      #   独立 CLI：dataset（YOLO 数据集）/ 四张表情表 / export_user_images
│   │                               #   / user_filter / remedy_badges / merge_segments / list_match（撞库）
│   ├── parse_live_comments.py      #   兼容 facade（旧命令行 / 旧 import 照旧）
│   ├── account_id.py               #   账号身份 = (昵称, 等级标 ◆N) —— 唯一实现
│   ├── locate_panels.py · batch_locate_panels.py · draw_boxes.py
│   ├── examples/                   #   pipeline.example.json / minimal.json / panels.example.json
│   ├── *.json                      #   抖音 / 视频号 / 快手 / 微博各批次的真实配置
│   └── docs/                       #   platforms / dataset / review_granularity / dataset-4class-report / colon-fix-report
├── mock_scale/                     # ② 借底图批量出片 + 换评论正文
│   ├── core/                       #   三条线共用的唯一一份管线 + 工具 + 运行配置
│   ├── mock/                       #   mock 用户线（名单 → 假名字出片）+ 正文线（量 / 生句 / 替换）
│   ├── expand/                     #   扩展张数线：给真实用户补图（入口 + 3 份配置 + 全文）
│   ├── docs/                       #   工程结构 / 技术说明 / 全流程 / 多平台 / 正文方案与验收
│   ├── data/                       #   8 张演示底图（单张跑用；批量跑用外部真语料）
│   ├── outputs/                    #   产物（已 .gitignore，仅保留 .gitkeep）
│   ├── out/                        #   正文线的预演树（示例 out/body_run/，含昵称，不进交付包）
│   └── requirements.txt
├── requirements.txt                # 全仓依赖（livepipe + mock_scale，见「环境」）
├── .gitignore
└── README.md                       # 本文件
```

两个子工程**互不 import**：只通过磁盘上的语料目录形态衔接（见「子工程怎么接上（语料树）」）。

## 环境

### 全仓依赖：根 `requirements.txt`

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt          # 国内网慢加 -i https://pypi.tuna.tsinghua.edu.cn/simple
```

根目录这份覆盖 livepipe + mock_scale 的**全部**第三方依赖：numpy / opencv-python-headless /
Pillow / openpyxl（撞库读 xlsx）+ rapidocr_onnxruntime（OCR）+ torch / transformers /
accelerate / safetensors / bitsandbytes（面板定位模型，可选）。文件里有分段注释说明哪一段能省、
`libGL.so.1` 那个 headless OpenCV 的坑怎么补。`mock_scale/requirements.txt` 是它的子集，
只跑 mock_scale 时按那个装更轻。

两样不在 pip 里：面板模型加载器 `locateanything_worker.py`（仓库外，需拷到根目录；只走 OCR
定位时不需要）和 `ffmpeg`（只用于人工修素材，管线本身用 OpenCV 解码）。

### livepipe

| 用途 | 解释器 / 依赖 |
| --- | --- |
| 纯函数自检、`check`、配置生成 | 任意 `python3`，**零第三方依赖** |
| 抽帧 / OCR / 表情匹配 / 面板模型 | 带 `rapidocr` + `torch` + `transformers` 的环境；本机示例是 `P=/home/puyu/anaconda3/envs/vlx/bin/python` |
| 建表情表（`emoji_tables` 步骤） | 需要联网一次，之后可离线用现成表 |
| `filter` 步骤（评论 → 合格用户） | 需要 `NLP_API_KEY` 环境变量；**整条管线唯一花钱的一步** |

> 这些绝对路径是本机的环境约定，换机器要换成自己的解释器；命令里写 `$P` 就是为了只改一处。

### mock_scale

```bash
cd mock_scale
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# 三个依赖：numpy / opencv-python-headless / Pillow —— 刻意没有 dashscope 与 rapidocr
```

用 headless 版 OpenCV 是为了避开「非 headless 版缺 `libGL.so.1`」的装包坑；
本工程只做 inpainting，不弹窗口。

## ① livepipe：录屏 → 评论 → 合格用户

### 两阶段工作流（推荐）

评论区范围是可变的（视频号尤其），所以先定位、再人工标注平台：

```bash
# ① 定位评论区 → 产出 panels 文件（每个面板的 platform 留空）
python3 -m livepipe panels cfg.json

# ② 打开 panels.file，给每个面板填 "platform": "douyin" / "wechat" / "weibo"
#    平台决定行距与表情表；填错整片认不出。

# ③ 解析（面板文件已存在 ⇒ 阶段①自动跳过，不会覆盖人工标注）
python3 -m livepipe run cfg.json
```

### 八步管线

`python3 -m livepipe steps` 可随时打印这张表：

| # | 步骤 | 干什么 |
| --- | --- | --- |
| 1 | `emoji_tables` | 建/刷新抖音、微信、微博、快手表情表（仅 `build.enabled=true` 的表） |
| 2 | `panels` | 按 `panels.mode` 定位 / 落盘 / 复用面板矩形，产出待人工标注的面板文件 |
| 3 | `parse` | 核心：OCR 抽行 → 表情识别 → 解析 → 跨帧去重 → 出图 |
| 4 | `filter` | 评论 → 合格用户：用户名 ≥N 条正面评论（大模型判定，表情一起发） |
| 5 | `qualify` | 跨段判合格（全局步）：各段按**合计**算，回写每段 `users_qualified.jsonl` |
| 6 | `export` | 逐段出交付图：按底帧画绿框、一人一个目录 |
| 7 | `collect` | 拼最终交付树：同名账号合并、图号跨段连续编号 |
| 8 | `draw` | 把某时刻附近的框画到一帧上（独立描框） |

逐段步按段循环，全局步只跑一次。`--only` / `--skip` 可只跑或跳过指定步骤，例如：

```bash
python3 -m livepipe run cfg.json --only parse          # 只重抽评论
python3 -m livepipe run cfg.json --skip emoji_tables,draw
python3 -m livepipe run cfg.json --dry-run             # 只过步骤，不落产物
python3 -m livepipe check cfg.json                     # 只校验配置
python3 -m livepipe run cfg.json --print-config        # 看合并后的最终配置
```

### 配置

- 格式是 **JSONC**（支持 `//`、`/* */` 与末尾多余逗号），样例见
  [`livepipe/examples/pipeline.example.json`](livepipe/examples/pipeline.example.json)。
- `base_dir` 是相对路径的基准，`null` = 运行命令时的工作目录（通常工程根）。
- 优先级：**命令行 > JSON > `DEFAULTS`**；逐参数说明见 [`livepipe/CONFIG.md`](livepipe/CONFIG.md)。
- 平台档案（行距 / 相位 / `x_text` / 表情表）写在配置的 `platforms` 段：
  加新平台 = 在这里加一条，再在面板文件里填平台名。
- **昵称打码（匿名）怎么算一个账号**：平台档案写 `account_key` ——
  `"username"`（默认，只看昵称）/ `"nickname_level"`（整场都打码，按 昵称+等级标）/
  `"nickname_level_if_masked"`（**匿名/非匿名混着出现**：打码的按 昵称+等级标、
  等级标一致才算同一个人；不打码的仍只看昵称）。配置示例与重跑方式见
  [`livepipe/README.md`](livepipe/README.md) 的「账号身份：匿名（打码昵称）怎么配置」。

### 产物

落在 `output.dir`（多段素材时逐段在 `<output.dir>/<段名>/`）：

| 文件 | 内容 |
| --- | --- |
| `rows.jsonl` | 行序；回喂 `input.rows` 可复算，**省一次 OCR** |
| `comments.jsonl` | 唯一评论（`username` / `content` / `emoji_text` / `roi` / `text_box` / `colon_box` / `badge_box` / `emojis` / `t` / `panel`） |
| `panels.json` | 面板文件（**人工标注 platform 的那个**） |
| `panels.resolved.json` | 按平台档案展开后的实际裁切窗口 |
| `summary.json` | 统计 + 出图清单 + 定位/表情取证（adaptive 抽帧另有 `sample_mode` / `sample_stats`） |
| `frames/<t>_roi.jpg` | 逐帧标注图（`output.frames` 控制；全片很大，建议按需开） |
| `users.jsonl` | `filter` 出的逐段合格用户（含逐条判定，可一条条核） |
| `users_qualified.jsonl` | `qualify` 判完合格后回写的每段名单 |
| `merged_users.json` | 跨段合计口径的名单与取证 |
| `<平台>/all_users_正面_<N>张以上/` | **交付图**：每用户一个目录，带 `manifest.json` / `正面账号.txt` / `README.md` |

账号身份是 **(昵称, 等级标 ◆N)**，所以同一个打码昵称下的人会拆成
`白___◆19-5/`、`白___◆21-3/`、`白___-6/` 这类目录。改了认人口径后，用
`python3 -m livepipe.remedy_badges <cfg.json> [--apply]` 按新口径重算已交付的树。

### 独立工具

`python3 livepipe/<tool>.py` 与 `python3 -m livepipe.<tool>` 等价（原路径是薄壳）：

| 工具 | 作用 |
| --- | --- |
| `douyin_emoji_table.py` · `wechat_emoji_table.py` · `weibo_emoji_table.py` · `kuaishou_emoji_table.py` | 建/刷新四套表情表（快手从 live.kuaishou.com 页面内嵌 emojiPanel 取） |
| `export_user_images.py` | 合格用户表 → 交付图目录（可单独重导） |
| `user_filter.py` | 评论 → 合格用户（大模型判「正面」，可 `--dry-run`） |
| `remedy_badges.py` | 已交付的树按新口径重切（先印单子，`--apply` 才落盘） |
| `merge_segments.py` | 跨段 qualify / collect |
| `batch_locate_panels.py` · `locate_panels.py` · `draw_boxes.py` | 面板定位与描框辅助 |

### YOLO 数据集（4 类，2026-10-29）

`python3 -m livepipe.dataset` 把各段 `comments.jsonl` 折成标准 YOLO 结构。
中间夹一步人：`review` 出复核横条 → 人工删掉不对的 → `build` 落盘并过八道闸。
当前 schema **4 类**，等级标（`level_badge` / `badge_box`）不再写入：

| cls | 类 | 来源字段 |
|---:|---|---|
| 0 | `comment_row` | `roi` |
| 1 | `body_text` | `text_box` |
| 2 | `colon` | `colon_box` |
| 3 | `emoji_run` | `emojis[].box` |

```bash
# ① 出复核图（每个视频顺序解码一次，每条看得见的评论一张横条）
python3 -m livepipe.dataset review --runs out/风云T7_20260826_11 out/风云T7_20260826_11_02 out/征服8 \
    --exclude 风云T7_20260826_11_02/seg7 --out out/dataset_v1
# 口径变了要刷新旧横条：同一命令加 --redraw（仍尊重 rejected.jsonl，删掉的不会回来）

# ② 人在 out/dataset_v1/review/ 里看，删除就是指令（幂等，重跑不会画回来）：
python3 -m livepipe.dataset build --out out/dataset_v1 --reject-frame 12-01-13-00_f010946
# 整帧丢弃：这一帧的全部评论都不进数据集
python3 -m livepipe.dataset build --out out/dataset_v1 --restore 12-01-13-00_f010466_03.jpg
# 撤回误删：从 rejected.jsonl 划掉，下次 review 重画这张

# ③ 算账 → 落盘 + 八道闸
python3 -m livepipe.dataset build --out out/dataset_v1 --dry-run   # 只算复核/删除/图数，不写
python3 -m livepipe.dataset build --out out/dataset_v1             # 落盘并自动过闸
python3 -m livepipe.dataset --selftest                             # 37 项纯函数自检（不读盘）
```

| 子命令 | 选项 |
|---|---|
| `review` | `--runs`（必填，可多段）· `--out` · `--exclude`（子串匹配 `run/seg` 或路径，可重复）· `--split-file`（默认：路径含「微博」→ test、`17：13-17：33` → val、其余 train）· `--scale 1.6`（横条放大）· `--pad 10` · `--quality 90` · `--redraw`（已有复核图也重画，仍尊重 `rejected.jsonl`） |
| `build` | `--out` · `--review`（默认 `<out>/review`）· `--dry-run` · `--force`（输出里已有图也覆盖）· `--restore <复核图名>` · `--reject-frame <视频键>_f<帧号>` · `--preview-n 20` · `--no-verify`（仅调试）· `--quality 90` |

产物是标准 YOLO 结构（不加私有前缀）：

```
out/dataset_v1/
├── images/{train,val,test}/<视频键>_f<帧号>.jpg   一帧一张（整帧原图，不裁）
├── labels/{train,val,test}/<同名>.txt             <cls> <cx> <cy> <w> <h>，6 位小数
├── review/                                       复核横条 + index.jsonl（权威清单）+ rejected.jsonl
├── preview/                                      PREVIEW 闸从标签文件反画 20 张
├── data.yaml                                     nc=4 + names
└── manifest.json                                 每张图的视频/帧/split、各类框数、digest
```

几条口径：

- **一个复核单元 = 一条看得见的评论**：同一 (视频, 帧) 下 ROI IoU ≥ 0.6 的记录并成一条
  （同一批录屏常被两个 run 各解析一遍），删一次就全没了。
- **`username` / `colon_box` 为空的行直接过滤**：昵称或冒号没读出来的行，`body_text` 起点和
  `colon` 标签不可信；`review` 前就丢掉，`LABEL_SOURCE` 闸再从盘上反查一遍。
- **split 按视频切**，不按帧随机切（相邻帧只差一条新评论，随机切等于把答案抄进验证集）；
  `build` 落盘后自动过八道闸（`REVIEW_MATCH` / `LABEL_SOURCE` / `ROUNDTRIP` / `IN_FRAME` /
  `SPLIT_LEAK` / `DUP_IMAGE` / `CLASS_COVER` / `PREVIEW`），不过整批不许用。
- `review` 重跑只补新单元，已删的不会回来；`build` 每次按复核目录重算，并清掉上一轮的过期图/标签。

当前实测（2026-10-29 重建，8 个视频）：3,989 个复核单元 / 2,865 图 / 12,384 框
（train 1,904 图 / val 519 / test 442），八道闸全过。从 5 类迁到 4 类时移除了
1,818 个等级标框（train 1,372 · val 426 · test 20）。
原因与前后样张见 [`livepipe/docs/dataset-4class-report.html`](livepipe/docs/dataset-4class-report.html)；
复核口径（按条 vs 按整帧）见 [`livepipe/docs/dataset.html`](livepipe/docs/dataset.html) 与
[`livepipe/docs/review_granularity.html`](livepipe/docs/review_granularity.html)。

## ② mock_scale：借底图批量出片 + 换评论正文

### `core/` 里有什么

| 文件 | 作用 |
| --- | --- |
| `config.py` | 常量与路径（`PROJECT_ROOT = mock_scale/`、`data/`、`outputs/`、`BLACKLIST_PATH`） |
| `box_detect.py` | 检测彩色空心矩形描边（移植件，批量跑真实语料时用） |
| `inkmask.py` | 框内定位用户名 / 冒号 / 正文，产出擦除蒙版与度量 |
| `inpaint_local.py` | 本地确定性擦除（邻域填充 + OpenCV inpaint，只贴蒙版内） |
| `draw_text.py` | 去 emoji、量槽位、计划字号与截断/补字、Pillow 写回 |
| `replace_username.py` | 单张端到端：框 → 定位 → 擦除 → 写新名（CLI + 可 import） |
| `platforms.py` | 平台 profile（抖音 / 视频号网页版 / 视频号 App / 微博） |
| `runconfig.py` | 运行配置层：JSON > 命令行 > `DEFAULTS` |
| `batch_produce.py` | mock 线入口：名单 + 真语料 → 成品树（也被扩展线 import） |
| `batch_run.py` | 验收外壳：真实语料 × 短/等长/超长三探针，逐段判据 |
| `body_text.py` | 正文线：在 `text_box` 行带内量正文左沿 / 右缘 / 行 / em / 颜色（`measure_body`） |
| `upstream_geom.py` | 正文线：几何来源 —— `--geom` > `<source>/geom.json` > 交付树 manifest 自动重建 |
| `replace_body.py` | 正文线：按像素宽挑句、逐行铺平擦除、断行写回（`plan_body` / `erase_body` / `render_body`） |
| `batch_body.py` | 正文线入口：批量替换 + **八道闸** → 预演树 + `report.json` |
| `platform_probe.py` | 只读探针：这份语料能不能跑、该用哪套 profile |
| `source_blacklist.json` | 素材黑名单 |
| `batch_produce.config.json` · `batch_run.config.json` · `batch_body.config.json` | 三个入口的「这次怎么跑」 |

管线的四段（全本地、不调接口）：

```
检测 box_detect.py → 定位 inkmask.py → 擦除 inpaint_local.py → 排版/写字 draw_text.py
```

### mock 用户线（`mock/`）

场景：**假名字出假图** —— 验这条管线能不能批量出片。

```bash
python3 mock/make_spec.py            # 生成名单 → mock/names/
python3 core/batch_produce.py        # 按名单出片 → outputs/produce_<时间戳>/
python3 core/batch_run.py            # 拿真语料验收（检测 → 定位 → 擦除 → 排版）
```

- `make_spec` 只问三件事：素材路径、要几个名字、每个名字几张；名字由中文词池组合生成，
  **刻意不抄真昵称**。
- `batch_produce` 的两条硬规则：**一张源图只出一个成品**（不够才复用，且计入
  `summary.json`）；同一个人的几张尽量来自不同原始用户。收尾按 md5 自检重复。
- 规格：名字超过原槽位**截断**、不够**随机补中文**，**不缩字号**；被截断/补过字的成品
  单独放 `_名字有改动/`，**两份都要交**。有短张时进程**退出码 3**。
- 跳过不是失败：纯 ASCII 昵称（看目录名，读图前）、名字段含 emoji（看图，定位后）——
  不出片、不计入任何数量，但 `rows.csv` / `skipped.txt` 留绝对路径。

产物：

```
outputs/produce_<时间戳>/
├── <名字>-<实际张数>/1.jpg 2.jpg …      # 名字与名单逐字一致
├── _名字有改动/<名字>-<张数>/…           # 被截断或补过字
├── manifest.json                        # 逐张：名字 / 源图 / 度量 / 落在哪个目录
└── summary.json                         # 名单 / 实际 / 复用 / 拒绝原因 / text_fit
```

`batch_run` 默认不存结果图，只出 `rows.csv` + `summary.json`（650 张 × 3 探针 ≈ 1950 张图
没必要落盘）；`--dump` 可按档落裁剪条/整图/失败图。

### 扩展张数线（`expand/`）

场景：**给真实语料里每个真实用户按他自己的名字补图，就地交付**。底图从别的真实用户借，
框内「用户名 + 等级徽章」一起换，正文一行不动。

```bash
python3 expand/expand_produce.py                            # P1 只读账（写 expand_plan.json）
python3 expand/expand_produce.py --mode sample              # P2 样张 + 上下对照条
python3 expand/expand_produce.py --mode run --out /tmp/exp  # P3 预演树，不动语料
python3 expand/expand_produce.py --mode run --yes           # P4 就地写回（必须显式 --yes）

# 平台由配置决定，不靠记忆里的 flag
python3 expand/expand_produce.py --config expand/expand_produce.视频号.config.json
python3 expand/expand_produce.py --config expand/expand_produce.视频号app.config.json
python3 expand/expand_produce.py --config expand/expand_produce.快手.config.json
#   ↑ App 端（720×1600）必须写死 platform=wechat_app：目录名只叫「视频号」，auto 判不出。
```

- 张数口径 `T = max(min_images, ceil(ratio × 现张数))`，三份配置现在 `ratio=1.6`；
  只补 `T − 现张数`。
- 交付前过**七道闸**（结构、截断、药丸、emoji、底图质量等），不过就换底图；
  缺口逐用户落在 `expand_reconcile.csv`，>5% 时 `run` 退出码 3。
- **P4 会改语料目录名与文件**；先跑 P1/P3 看账。

### 正文线（`mock/` + `core/`，2026-10-28）

场景：**把评论正文换掉**。换名那一路只改绿框左半段，正文一直原样跟着平移；这条线换的是
「冒号右边那句话」。正文宽度由原作者写了多少字决定，所以不是「按槽位生成」，而是：
量出每行正文的几何 → 按字数阶梯出句子 → 按**像素宽**挑一句 → 逐行铺平擦掉原文、断行写回 →
在成品图上用同一把尺子过**八道闸**。语料全程只读，成品写到另一棵树。

```bash
cd mock_scale

# ① 量：→ mock/data/body_measure.json（逐张，含昵称）+ bodies_need.json（聚合阶梯）
python3 mock/measure_bodies.py --source <语料根>

# ② 生句：按阶梯出句子 → mock/data/bodies.jsonl（没有 NLP_API_KEY 时走本地池）
python3 mock/gen_bodies.py

# ③ 换：挑句 + 擦 + 写 + 过闸 → out/body_run/ + report.json
python3 core/batch_body.py --source <语料根> --out out/body_run
```

⚠️ **上游几何按优先级自动找**：① `--geom` 显式给的文件；② `<语料根>/geom.json`
（v1 形状：`{"schema_version": 1, "kind": "geom", "items": {相对路径: {text_box, colon_box, badge_box, emojis}}}`，
字段与 livepipe `comments.jsonl` 一致）；③ 都没有时，按交付树 `manifest.json` 的
`(seg, t, roi)` 去各段导出目录的 `comments.jsonl` **自动重建**。显式给了但读不了，或自动重建
也拿不到，两个脚本都直接退出码 2，不会去猜框。

- **量测只看墨迹**：`text_box` / `colon_box` 只定「这一行在哪、正文从哪起」，右缘与 em 仍在框内重量
  （`text_box.x2` 比墨迹宽 1~7px，拿它当右缘新字会顶到绿框）。
- **跳过（不猜）**：框跨下一条评论、正文只有表情、认不清行数 —— 换错比不换贵得多。
- **八道闸**：`REMEASURE` / `LINE_COUNT` / `RIGHT_EDGE` / `BOX_MARGIN` / `FIRST_LINE_FILL` /
  `COLON`（冒号像素零改动）/ `OUTSIDE`（擦除矩形外零改动）/ `RESIDUAL`（擦完、写字前残余墨迹 = 0）。
- **预演树默认 PNG**（`--format png`）：JPEG 重编码会把边缘动 1px，报告里的数就没法从盘上复核。
- `--out` 指进语料目录会被拒（除非 `--yes`）：验证永远写另一棵树。

73 张评审语料的实测：**65 / 65 条可换正文替换成功、八道闸 0 失败**；不换 8 张
（黑名单 4 · 纯表情 2 · 框跨下一条评论 2）。右缘偏差中位 3px、残余墨迹 0、矩形外改动 0、
语料指纹不变。逐张对照与判据表见
[`mock_scale/docs/mock正文验收.html`](mock_scale/docs/mock正文验收.html)；完整参数与口径见
[`mock_scale/mock/README.md`](mock_scale/mock/README.md)。

### 运行配置

| 目录 | 配置文件 | 管什么 |
| --- | --- | --- |
| `core/` | `batch_produce.config.json` | 名单 / 素材根 / 输出 / 平台 / 字号 / 并发 |
| `core/` | `batch_run.config.json` | 语料根 / 探针 / 落图档 / 并发 |
| `core/` | `batch_body.config.json` | 正文替换：语料 / 几何 / 句子库 / 输出 / 复用上限 / 格式 |
| `mock/` | `make_spec.config.json` | 素材根 / 名字个数与长度 / 输出 |
| `mock/` | `measure_bodies.config.json` | 正文量测：语料 / 几何 / 平台 / 每桶条数 / 黑名单 |
| `mock/` | `gen_bodies.config.json` | 句子生成：订单 / 本地池 / 远端端点 / 标点变体 |
| `expand/` | `expand_produce.config.json` | **抖音** |
| `expand/` | `expand_produce.视频号.config.json` | **视频号（网页版）**，`platform=generic` |
| `expand/` | `expand_produce.视频号app.config.json` | **视频号（App 端）**，`platform=wechat_app` |
| `expand/` | `expand_produce.快手.config.json` | **快手（App 端）**，`platform=kuaishou_app`，自动重建 geom |

- 每个入口自动读**脚本同目录**的同名 `<脚本>.config.json`，`--config` 可换一份。
- 优先级：**命令行显式给 > JSON > 脚本 `DEFAULTS`**。
- 配置里的相对路径以 `mock_scale/` 为基准，**在哪个目录敲命令结果一样**。
- 每个键旁有 `_<键名>` 备注（干什么 / 能填什么 / 默认 / 坑），**配置本身就是参数手册**。
- ⚠️ 仓库里几份配置的 `source` / `root` 是**本机真实语料**的绝对路径
  （`~/github/eagle/Embodied/out/...`、`~/work/data/...`、`~/交付/...`）。换机器或换批次时
  改配置，或用命令行 `--source` / `--root` 覆盖。

## 子工程怎么接上（语料树）

`livepipe` 的 `export` / `collect` 交出这样一棵树：

```
<livepipe output.dir>/<平台>/all_users_正面_<N>张以上/
├── <用户名>-<张数>/1.jpg  2.jpg …
├── manifest.json          # 逐图：rel / t / roi / label / content / emoji_text
├── 正面账号.txt
└── README.md              # collect 现算生成（讲 ◆ 等级标怎么读、本批账）
```

`mock_scale` 的 `expand/expand_produce.py` 直接读这棵树（`manifest.json` 的 `roi`
表就是标注框，**不做像素检测**），按目标用户自己的名字补图并写回。所以：

```bash
# livepipe 交出语料后
cd mock_scale
python3 expand/expand_produce.py --source <上面那棵树的路径> --mode plan
```

`mock` 用户线**不依赖** livepipe 的 manifest（它只需要一批真图 + 一份名单），
但同样能从 livepipe 的交付树借底图。

正文线要再往前一步：它需要一份上游几何（按语料相对路径索引的 `text_box` / `colon_box` /
`badge_box` / `emojis`，字段与 livepipe `comments.jsonl` 对应）。这份几何可以跟图放在同一棵
语料树的根下（`geom.json`），也可以由正文线按交付树 `manifest.json` 的 `(seg, t, roi)` 从各段
`comments.jsonl` 自动重建；语料本身仍只读，替换结果写 `out/body_run/`。

## 常用任务：改东西去哪

| 想改什么 | 去哪 |
| --- | --- |
| OCR 抽行 / 行分组 / 跨帧去重 | `livepipe/parsing/ocr/` · `livepipe/parsing/comments/` |
| 文本规则、通知/系统行、昵称清洗 | `livepipe/parsing/text/` |
| ROI、段落地、面板定位 | `livepipe/parsing/layout/` · `livepipe/parsing/panels/` |
| 表情识别与表情表 | `livepipe/parsing/emoji/` · `livepipe/tools/*_emoji/` |
| 账号身份（昵称 + 等级标） | `livepipe/account_id.py`（唯一实现） |
| YOLO 数据集类表 / 复核 / 八道闸 | `livepipe/tools/dataset/`（类表在 `common.py` 的 `CLASSES`） |
| livepipe 配置参数 | `livepipe/config/defaults.py` + `livepipe/CONFIG.md` |
| 框检测 | `mock_scale/core/box_detect.py` |
| 用户名定位 / 擦除蒙版 | `mock_scale/core/inkmask.py` |
| 擦除算法 | `mock_scale/core/inpaint_local.py` |
| 字号、截断、补字、写字 | `mock_scale/core/draw_text.py` |
| 平台差异（行距 / 彩度 / 徽章） | `mock_scale/core/platforms.py` |
| 批量选材、配对、重试 | `mock_scale/core/batch_produce.py` |
| 验收判据 | `mock_scale/core/batch_run.py` |
| 正文量测（左沿 / 右缘 / 行带 / em / 颜色） | `mock_scale/core/body_text.py` |
| 正文挑句判据 / 擦除矩形 / 断行写回 | `mock_scale/core/replace_body.py` |
| 正文批量 + 八道闸 | `mock_scale/core/batch_body.py` |
| 正文句子生成（本地池 / 远端 NLP） | `mock_scale/mock/gen_bodies.py` |
| mock_scale 运行配置 | `core/` `mock/` `expand/` 下的 `*.config.json`（连 `_备注` 一起改） |

## 自检

```bash
# ---- livepipe（不依赖外部素材）----
cd livepipe
python3 -m livepipe.parse_live_comments     # 7 组内置断言（无参 = 自检）
python3 -m livepipe.account_id              # 账号身份 20 项断言
python3 -m livepipe --help                  # 入口可用性（run / panels / check / steps / example）
# 完整干跑（示例配置）：
cd .. && python3 -m livepipe check livepipe/examples/pipeline.example.json
python3 -m livepipe run livepipe/examples/pipeline.example.json --dry-run

# ---- mock_scale ----
cd mock_scale
python3 -m compileall -q core mock expand
python3 core/batch_run.py --help
python3 core/batch_body.py --help
python3 core/platform_probe.py <语料根> --limit 5   # 先量「这份语料能不能跑」
```

`batch_run` 的判据按「检测 / 定位 / 擦除 / 排版」分段计数：
`NO_BOX`、`MULTI_BOX`、`LOC_NONE`、`DIRTY`、`LEAK`、`GAP_BAD`、`TAIL_BAD`、
`TAIL_RESID`、`BOX_BAD`。其中 `DIRTY` / `LEAK` 是**构造上不该出现**的，出现即 bug；
`SKIP_PURE_EN` / `SKIP_EMOJI` 是「这张图不该出片」，不计入任何数量。

## 合并说明（2026-10-27）

- `livepipe/` 来自 `eagle/Embodied/livepipe`：单体 7,844 行已按 AST 机械拆成 `parsing/` 分层包，
  原文件保留为兼容 facade，**旧命令与旧 import 全部照旧**。
- `mock_scale/` 来自 `work/test2`：原先 `mock/` 与 `expand/` 各存一份**逐字节相同**的管线，
  用 `check_shared.py` + `shared-modules.json` 守漂移；合并后两份合成 **`core/` 一份**，
  漂移守卫退役（`core/` 直接挂在 `mock_scale/` 下，`PROJECT_ROOT` 等路径语义不变）。
- 旧路径映射（`mock/batch_run.py` → `core/batch_run.py`、`test2/` → `mock_scale/` 等）见
  [`mock_scale/docs/工程结构.md`](mock_scale/docs/工程结构.md#迁移对照2026-10-27-从-test2-并进来)。
- 两个子工程**互不 import**：只通过磁盘上的语料目录形态衔接。
- 历史实验记录 `expand/扩展张数.md` 保留当时的命令与路径，顶部已注明当前跑法。

## 注意与边界

- **`expand --mode run --yes` 会改写语料**（文件与目录名）。不确定时先 `--mode plan`、
  再 `--mode run --out /tmp/exp` 预演。
- `manifest.json` 是扩展线的**硬依赖**：缺它 `expand_produce` 会直接报错（用 livepipe
  `export`/`collect` 重新生成）。
- `batch_produce` 默认读 `core/` 下配置里的真实语料路径；路径不存在时会在选材阶段报错，
  用 `--source` 指到自己的语料。
- 正文线的 `--source` 指向的树里：几何按 `--geom` > `<source>/geom.json` > 交付树 manifest
  自动重建的顺序取；三条都拿不到时两个入口才退出码 2。`--out` 必填、要另给一棵树，指进语料
  目录会被拒（除非 `--yes`）。
- `outputs/`、`out/`、`.venv/`、`__pycache__/` 都已忽略；交付语料本身不建议放进本仓库。
