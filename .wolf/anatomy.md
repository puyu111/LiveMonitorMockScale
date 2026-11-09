# Anatomy — 文件级索引（简版）

> 完整的目录结构以根 [`README.md`](../README.md)、[`livepipe/README.md`](../livepipe/README.md)、
> [`mock_scale/docs/工程结构.md`](../mock_scale/docs/工程结构.md) 为准。
> 这里只记**容易找错入口**的文件；新增/改名时追加一行。

## 顶层

| 路径 | 作用 |
|---|---|
| `livepipe/` | ① 录屏 → 结构化评论 → 合格用户 → 交付树 |
| `mock_scale/` | ② 借底图批量出片 + 换评论正文 |
| `out/` | 各批次产物（已 `.gitignore`；交付树在 `out/<批次>/<平台>/all_users_*`；直播全量账号在 `seg*/users.jsonl`；撞库结果含 `标记图/`） |
| `撞库名单/` | 活动方名单（xlsx；平台表头下面是人名）。**含个人信息，已 `.gitignore`** |
| `requirements.txt` | 全仓第三方依赖（numpy/opencv-headless/Pillow/openpyxl + rapidocr + torch/transformers 可选段）；`mock_scale/requirements.txt` 是它的子集 |
| `weights/` · `runs/` | YOLO 权重与训练产物（已 `.gitignore`） |

## livepipe 入口

| 路径 | 作用 |
|---|---|
| `livepipe/__main__.py` | `python -m livepipe {run,check,panels,match,steps,example}` |
| `livepipe/pipeline.py` | 管线步骤编排 |
| `livepipe/config/` | JSON → 配置 / 平台档案 / 参数解析 |
| `livepipe/parsing/` | OCR / 行 / 评论 / 表情 / 面板 / 标注图 / 自检 |
| `livepipe/tools/<name>/` | 独立工具实现；`livepipe/<name>.py` 是薄壳 |
| `livepipe/account_id.py` | 交付账号身份 = (昵称, 等级标 ◆N) |
| `livepipe/CONFIG.md` · `全流程说明.md` | 参数详解 / 从零跑一遍 |

## 2026-11-06 新增：撞库（名单 × 直播全量账号）

| 路径 | 作用 |
|---|---|
| `livepipe/tools/list_match/common.py` | 名单归一化、`ratio`/`partial` 相似度（前后缀/CJK 防误撞）、平台标签、目录名解析 |
| `livepipe/tools/list_match/sources.py` | 读 xlsx/csv/txt 名单；发现 `<平台>/all_users_*` 交付树；`load_stream_accounts` 读 `seg*/comments.jsonl`（**全部评论账号**，排除词/认人口径从 `users.json` 抄 filter；无 comments 时退回 `users.jsonl`）+ `judgments.jsonl` 回填正负面数；`merge_accounts` 合并两个账号宇宙 |
| `livepipe/tools/list_match/matching.py` | 平台内匹配（名单名 × 账号 → 命中/未命中 + 分数，标 `in_delivery`） |
| `livepipe/tools/list_match/report.py` | 写 `matches.json` / CSV / xlsx / README；`export_images` 复制交付命中图 |
| `livepipe/tools/list_match/render.py` | 给 `in_delivery=False` 的命中从视频原帧现补交付口径图（正面账号只取正面，零正面按中性/负面；`--video-map` 重映射失效的素材路径；`owners` 保证跨段/重跑不重复） |
| `livepipe/tools/list_match/html.py` | 人看的 HTML 交付清单（KPI/占比条/可排序表格/未命中复核；场次与日期自动从配置 `name` 取） |
| `livepipe/tools/list_match/cli.py` | `match` 子命令参数与编排（默认直播全量 + 标记图 + HTML；`--delivered-only` / `--no-images` / `--no-html` / `--video-map`） |
| `livepipe/tools/list_match/selftest.py` | 106 项纯函数自检 |
| `livepipe/tools/list_match/doc.py` | `--help` 的完整说明 |
| `livepipe/list_match.py` | 薄壳 → `tools/list_match/` |
| `livepipe/__main__.py` | 新增 `match` 子命令（其余命令不动） |

相关文档：`livepipe/README.md`「撞库」小节 · `livepipe/全流程说明.md` §7 · 根 `README.md`。

## 2026-11-07 新增：风云A9L+OTA升级直播（渠道）2026-09-05 配置

| 路径 | 作用 |
|---|---|
| `09-05风云A9L渠道_抖音.json` | 抖音 3 段（720×1568/1560）→ `out/2026-09-05风云A9L+OTA升级直播（渠道）/抖音` |
| `09-05风云A9L渠道_视频号.json` | 视频号内部录屏 4 段（1206×2622）→ 同批次 `视频号/`；行距 76.7、wrap_indent |

## 2026-11-07 新增：猎鹰智驾瑞虎9羌塘无人区（渠道）2026-09-05 配置

| 路径 | 作用 |
|---|---|
| `09-05猎鹰智驾_抖音.json` | 抖音 3 段（720×1560）→ `out/2026-09-05猎鹰智驾瑞虎9羌塘无人区（渠道）/抖音` |
| `09-05猎鹰智驾_快手.json` | 快手 3 段（720×1560，行距 55.0、x_ignore_from 470）→ 同批次 `快手/` |
| `09-05猎鹰智驾_视频号.json` | 视频号内部录屏 2 段（1080×2400，行距 81.7、y1=1250 避开顶部渐隐带）→ 同批次 `视频号/` |

## 2026-11-09 新增：猎鹰智驾瑞虎9羌塘无人区（渠道）2026-09-07 配置

| 路径 | 作用 |
|---|---|
| `09-07猎鹰智驾_抖音.json` | 抖音 6 段：上午 720×1560（3 段，行距 53.0、x_text 96）+ 下午 480×1068（3 段，行距 35.6、x_text 66）→ `out/2026-09-07猎鹰智驾瑞虎9羌塘无人区（渠道）/抖音` |
| `09-07猎鹰智驾_快手.json` | 快手 6 段：上午 480×1068（3 段，行距 36.8、x_text 95）+ 下午 1968×3150（3 段，行距 79.9、x_text 155、x2_from=content）→ 同批次 `快手/` |
| `09-07猎鹰智驾_视频号.json` | 视频号 6 段（592×1280，行距 41.5、x_text 34、rect y1=740、x_ignore_from 350）→ 同批次 `视频号/` |
| `out/2026-09-07猎鹰智驾瑞虎9羌塘无人区（渠道）/media/wx_eve_18-49-19-00_live.mp4` | 视频号末段只保留直播部分（`-t 738 -c copy`）：原文件 65.8min，直播在 737s 结束，之后是推荐页 |

## 2026-11-09 新增：猎鹰智驾瑞虎9羌塘无人区（渠道）2026-09-06 配置

| 路径 | 作用 |
|---|---|
| `09-06猎鹰智驾_抖音.json` | 抖音 4 段（720×1560，午/晚两场；行距 53.0、x_text 96、rect y1=780）→ `out/2026-09-06猎鹰智驾瑞虎9羌塘无人区（渠道）/抖音` |
| `09-06猎鹰智驾_快手.json` | 快手 3 段：移动档 2 段（720×1560，行距 55.0）+ 大档 1 段（1968×3150，行距 79.5、x2_from=content）→ 同批次 `快手/` |
| `09-06猎鹰智驾_视频号.json` | 视频号 4 段（592×1280，行距 41.5、x_text 34、wrap_indent、fan_badge_x_max 32）→ 同批次 `视频号/` |
| `out/2026-09-06猎鹰智驾瑞虎9羌塘无人区（渠道）/media/ks_eve_17-15-18-11_rot0.mp4` | 快手晚场修正副本：`ffmpeg -ss 258 -display_rotation 0 -c copy`（原文件 rotation=90 且开头 258s 画面侧躺）|

## 2026-11-08 新增：交付认人三档口径

| 路径 | 作用 |
|---|---|
| `livepipe/account_id.py` | 身份键三档：`username` / `nickname_level` / `nickname_level_if_masked`；`is_masked()`、`visible_name()`（星号前可见部分）、`level_of(..., masked_panels)` |
| `livepipe/config/panels.py` | `identity_panels()`（两档并集）、`masked_identity_panels()`（只打码拆等级）、`level_identity_declared()`（决定 `parse.badge_reread` 自动开关）；`selftest()` 7 项 |
| `livepipe/tools/user_filter/*` | `--identity-masked` → `group_accounts(..., masked_panels)` |
| `livepipe/tools/merge_segments/align.py` | 打码名只认「可见部分逐字相同 + 等级标一致」；`promote_prefix_level` 把数字药丸前缀补回 level |
| `livepipe/parsing/text/badges.py` | `normalize_username_alias` 段内同口径；打码名不走 `_tail_variant` 形近通道 |
| `livepipe/config/defaults.py` | `parse.badge_reread` 默认 `null`（按 `account_key` 自动绑定） |
| `livepipe/config/args.py` | `build_parse_args`：`badge_reread is None` ⇒ `level_identity_declared(cfg, seg)` |
| `livepipe/tools/remedy_badges/cli.py` | 复算前逐段印二次读数实际开关（`None` 时同一条判据） |
