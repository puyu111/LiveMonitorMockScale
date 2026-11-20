# Anatomy — 文件级索引（简版）

> 完整的目录结构以根 [`README.md`](../README.md)、[`livepipe/README.md`](../livepipe/README.md)、
> [`mock_scale/docs/工程结构.md`](../mock_scale/docs/工程结构.md) 为准。
> 这里只记**容易找错入口**的文件；新增/改名时追加一行。

## 顶层

| 路径 | 作用 |
|---|---|
| `livepipe/` | ① 录屏 → 结构化评论 → 合格用户 → 交付树 |
| `mock_scale/` | ② 借底图批量出片 + 换评论正文 |
| `out/` | 各批次产物（已 `.gitignore`；交付树在 `out/<批次>/<平台>/all_users_*`；直播全量账号在 `seg*/users.jsonl`；撞库结果含 `标记图/`）。**2026-11-17 起实体在 `/run/media/puyu/C0DE6A4BDE6A39B4/out/`**：配置里路径已改成该绝对根，且都带 `base_dir: /run/media/puyu/C0DE6A4BDE6A39B4`（缺省表情表路径仍写相对 `out/…`） |
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
| `livepipe/tools/list_match/matching.py` | 平台内匹配（名单名 × 账号 → 命中/未命中 + 分数，标 `in_delivery`）；未命中的 `best` 与命中同形（目录/评论数/仅直播标记），供导图复核 |
| `livepipe/tools/list_match/report.py` | 写 `matches.json` / CSV / xlsx / README；`export_images` 复制交付命中图，并把未命中最近账号图放到 `标记图/未命中/` |
| `livepipe/tools/list_match/render.py` | 给 `in_delivery=False` 的命中/未命中最近账号从视频原帧现补交付口径图（正面账号只取正面，零正面按中性/负面；`--video-map` 重映射失效的素材路径，旧路径 stat 无权限也不影响；`owners` 保证跨段/重跑不重复） |
| `livepipe/tools/list_match/html.py` | 人看的 HTML 交付清单（KPI/占比条/可排序表格/未命中复核 + 最近图链接；场次与日期自动从配置 `name` 取） |
| `livepipe/tools/list_match/cli.py` | `match` 子命令参数与编排（默认直播全量 + 标记图 + HTML；`--delivered-only` / `--no-images` / `--no-html` / `--video-map`） |
| `livepipe/tools/list_match/selftest.py` | 118 项纯函数自检 |
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

## 2026-11-10 新增：猎鹰智驾瑞虎9羌塘无人区（渠道）2026-09-09 配置

| 路径 | 作用 |
|---|---|
| `09-09猎鹰智驾_抖音.json` | 抖音 4 段（720×1560；行距 53.0、y_phase 35、x_text 96、rect [0,790,620,1390]、x_ignore_from 470）→ `out/2026-09-09猎鹰智驾瑞虎9羌塘无人区（渠道）/抖音` |
| `09-09猎鹰智驾_快手.json` | 快手 4 段：移动档 2 段（720×1560，行距 55.0、x_text 105、rect [0,770,720,1390]）+ 大档 2 段（1968×3150，行距 79.9、x_text 155、rect [0,1600,1968,3000]、x2_from=content）→ 同批次 `快手/` |
| `09-09猎鹰智驾_视频号.json` | 视频号 5 段（592×1280；行距 41.5、x_text 34、badge_span 44、rect [0,760,592,1128]、fan_badge_x_max 90）→ 同批次 `视频号/` |
| `out/2026-09-09猎鹰智驾瑞虎9羌塘无人区（渠道）/media/wx_morn_10-39-11-08_live.mp4` | 视频号上午末段只保留直播部分（PyAV stream copy，21.25min；原 29min） |
| `out/2026-09-09猎鹰智驾瑞虎9羌塘无人区（渠道）/media/wx_eve_15-41-16-02_live.mp4` | 视频号下午末段只保留直播部分（19.89min；原 21min） |
| `out/2026-09-09猎鹰智驾瑞虎9羌塘无人区（渠道）/media/ks_eve_15-55-16-02_live.mp4` | 快手下午末段只保留直播部分（6.79min；原 7.23min） |
| `/run/media/puyu/MyDisk/2026-09-09猎鹰智驾瑞虎9羌塘无人区（渠道）/` | 本批原始素材（外置盘，跑前确认已挂载） |

## 2026-11-10 新增：猎鹰智驾瑞虎9羌塘无人区（渠道）2026-09-10 配置

| 路径 | 作用 |
|---|---|
| `09-10猎鹰智驾_抖音.json` | 抖音 4 段（720×1560；行距 53.0、y_phase 22、x_text 96、rect [0,850,620,1380]、x_ignore_from 470）→ `out/2026-09-10猎鹰智驾瑞虎9羌塘无人区（渠道）/抖音` |
| `09-10猎鹰智驾_快手.json` | 快手 5 段：移动档 3 段（720×1560，行距 55.0、x_text 105、rect [0,780,720,1380]）+ 大档 2 段（1968×3150，行距 79.9、x_text 155、rect [0,1700,1968,3000]、x2_from=content、x_ignore_from **700** 丢「粉丝农场」小游戏卡）→ 同批次 `快手/` |
| `09-10猎鹰智驾_视频号.json` | 视频号 4 段（592×1280；行距 41.5、x_text 34、badge_span 44、rect [0,740,592,1128]、**x_ignore_from null**（本批无右侧卡）；`emoji.tables.wechat.build.enabled=true` ⇒ 远程 run.sh 自建表情表，因为上传脚本只传表 JSON 不传 img/）→ 同批次 `视频号/` |
| `/run/media/puyu/MyDisk/2026-09-10猎鹰智驾瑞虎9羌塘无人区（渠道）/` | 本批原始素材（外置盘，跑前确认已挂载）；视频号两个文件夹名误写成「快手」，内容是视频号 |

## 2026-11-10 新增：猎鹰智驾瑞虎9羌塘无人区（渠道）2026-09-08 配置

| 路径 | 作用 |
|---|---|
| `09-08猎鹰智驾_抖音.json` | 抖音 6 段（720×1560；行距 53.5、y_phase 42、x_text 96、rect [0,770,620,1380]、x2_from=content、x_ignore_from 470）→ `out/2026-09-08猎鹰智驾瑞虎9羌塘无人区（渠道）/抖音`；文件夹名误写「快手」，内容是抖音 |
| `09-08猎鹰智驾_快手.json` | 快手 **7 段**：移动档 3 段（720×1560，行距 55.0、x_text 105、rect [0,740,720,1380]）+ 大档 4 段（1968×3150，行距 80.0、x_text 155、rect [0,1600,1968,3000]、x2_from=content、x_ignore_from 1400）→ 同批次 `快手/` |
| `09-08猎鹰智驾_视频号.json` | 视频号 6 段（592×1280；行距 41.5、y_phase 22、x_text 34、badge_span 44、rect [0,675,592,1128]、fan_badge_x_max 32、x_ignore_from 350）→ 同批次 `视频号/` |
| `out/2026-09-08猎鹰智驾瑞虎9羌塘无人区（渠道）/media/ks_eve_16-11-17-06_a_upright.mp4` | 快手晚场首段前 260.6s（正立部分，`-t 260.6186 -c copy` 无损） |
| `out/2026-09-08猎鹰智驾瑞虎9羌塘无人区（渠道）/media/ks_eve_16-11-17-06_b_rot180.mp4` | 快手晚场首段余下 50.9min（原画面倒置，无 rotation 元数据）：`-ss 260.6186 -display_rotation 180 -c copy`，读取时自动转正 |
| `/run/media/puyu/MyDisk/2026-09-08猎鹰智驾瑞虎9羌塘无人区（渠道）/` | 本批原始素材（外置盘，跑前确认已挂载） |

## 2026-11-10 新增：艾瑞泽8CTCC职业联赛（上海站）（渠道七七）2026-09-13 配置

| 路径 | 作用 |
|---|---|
| `09-13艾瑞泽8CTCC_抖音.json` | 抖音 3 段（590×1280；行距 43.8、y_phase 37、x_text 83、badge_span 48、rect [0,672,590,1189]、x2_from=content、x_ignore_from null）→ `out/2026-09-13艾瑞泽8CTCC职业联赛（上海站）（渠道七七）/抖音` |
| `09-13艾瑞泽8CTCC_快手.json` | 快手 3 段（590×1280；行距 45.0、x_text 81、badge_span 47、rect [0,668,590,1189]、x2_from=window、x_ignore_from null）→ 同批次 `快手/` |
| `09-13艾瑞泽8CTCC_视频号.json` | 视频号 2 段（592×1280；行距 41.9、x_text 34、badge_span 44、fan_badge_x_max 32、rect [0,658,592,1190]、底部广告卡 overlay/skip）→ 同批次 `视频号/` |
| `out/2026-09-13…/media/dy_16-00-16-31_live.mp4` | 抖音末段只保留直播部分（`-t 1849.6 -c copy`；原 31.2min，直播在 1849.69s 结束） |
| `out/2026-09-13…/media/ks_15-15-16-31_live.mp4` | 快手末段只保留直播部分（`-t 4557.4 -c copy`；原 76.2min，直播在 4557.44s 结束） |
| `/run/media/puyu/MyDisk/2026-09-13艾瑞泽8CTCC职业联赛（上海站）（渠道七七）/` | 本批原始素材（外置盘，跑前确认已挂载） |
| `09-12CTCC_{抖音,快手,视频号}.json` | 同赛事 09-12 的旧配置（590/592×1280 但**缩放不同**：抖音行距 35、快手 45、视频号 33）⇒ 只能参考结构，数字不能照抄 |

## 2026-11-10 新增：艾瑞泽8CTCC职业联赛（上海站）（渠道七七）2026-09-12 配置

| 路径 | 作用 |
|---|---|
| `09-12CTCC_抖音.json` | 抖音 2 段（590×1280；行距 35.0、y_phase 20、x_text 78、badge_span 58、wrap_indent、wrap_gap_ratio 0.95、rect [0,655,590,1210]、x2_from=content、x_ignore_from 380、account_key=nickname_level_if_masked）→ `out/2026-09-12艾瑞泽8CTCC职业联赛（上海站）（渠道七七）/抖音` |
| `09-12CTCC_快手.json` | 快手 2 段（590×1280；行距 45.0、y_phase 26、x_text 81、badge_span 58、wrap_indent、wrap_gap_ratio 0.85、rect [0,680,590,1200]、x2_from=window、无 x_ignore_from、account_key=username、emoji_workers 1）→ 同批次 `快手/` |
| `09-12CTCC_视频号.json` | 视频号 1 段（592×1280；行距 33.0、y_phase 16、x_text 66、badge_span 44、wrap_indent、wrap_gap_ratio 0.85、rect [0,660,592,1190]、x_ignore_from 500、fan_badge_x_max 66、account_key=nickname_level_if_masked、底部广告卡 overlay/skip）→ 同批次 `视频号/` |
| `out/2026-09-12…/media/dy_eve_16-05-17-49_live.mp4` | 抖音末段只保留直播部分（PyAV stream copy，`-t` 到 1505.1s；原 103.8min；输入栏最后出现在 1505.34s） |
| `out/2026-09-12…/media/ks_eve_16-02-17-49_live.mp4` | 快手末段只保留直播部分（PyAV stream copy，`-t` 到 1738.0s；原 106.8min；输入栏最后出现在 1738.26s） |
| `out/2026-09-12…/media/wx_14-35-17-47_live.mp4` | 视频号单段只保留直播部分（PyAV stream copy，`-t` 到 6902.7s；原 193.6min，尾部约 78min 是广告页；`聊一聊` 最后出现在 6903.02s） |
| `/run/media/puyu/MyDisk/2026-09-12艾瑞泽8CTCC职业联赛（上海站）（渠道七七）/` | 本批原始素材（外置盘，跑前确认已挂载） |

## 2026-11-11 新增：艾瑞泽8CTCC职业联赛（上海站）（渠道七七）2026-09-15 配置

| 路径 | 作用 |
|---|---|
| `09-15艾瑞泽8CTCC_抖音.json` | 抖音 5 段（上午/下午两场；590×1280；行距 43.8、y_phase 26、x_text 84、badge_span 50、rect [0,668,590,1190]、x2_from=content、x_ignore_from 380）→ `out/2026-09-15艾瑞泽8CTCC职业联赛（上海站）（渠道七七）/抖音` |
| `09-15艾瑞泽8CTCC_快手.json` | 快手 4 段（590×1280；行距 45.0、y_phase 15、x_text 81、badge_span 47、rect [0,655,590,1200]、x2_from=window、无 x_ignore_from）→ 同批次 `快手` |
| `09-15艾瑞泽8CTCC_视频号.json` | 视频号 5 段（590×1280；行距 41.9、y_phase 14、x_text 64、badge_span 46、wrap_gap_ratio 0.75、rect [0,686,590,1190]、x_ignore_from 350、fan_badge_x_max 90）→ 同批次 `视频号` |
| `out/2026-09-15…/media/dy_morn_10-11-11-09_live.mp4` | 抖音上午末段裁到 2981.7s（输入栏最后 2981.91s） |
| `out/2026-09-15…/media/dy_eve_16-19-17-18_live.mp4` | 抖音下午末段裁到 2479.2s（输入栏最后 2479.38s） |
| `out/2026-09-15…/media/ks_morn_10-40-11-09_live.mp4` | 快手上午末段裁到 1235.5s（输入栏最后 1235.72s） |
| `out/2026-09-15…/media/ks_eve_15-36-17-18_live.mp4` | 快手下午末段裁到 5027.2s（输入栏最后 5027.37s） |
| `out/2026-09-15…/media/wx_morn_10-17-11-09_live.mp4` | 视频号上午末段裁到 2639.0s（输入栏最后 2639.22s） |
| `out/2026-09-15…/media/wx_eve_16-24-17-04_live.mp4` | 视频号下午末段裁到 2189.8s（输入栏最后 2190.02s） |
| `/run/media/puyu/MyDisk/2026-09-15艾瑞泽8CTCC职业联赛（上海站）（渠道七七）/` | 本批原始素材（外置盘，跑前确认已挂载） |

## 2026-11-11 新增：艾瑞泽8CTCC职业联赛（上海站）（渠道七七）2026-09-14 配置

| 路径 | 作用 |
|---|---|
| `09-14艾瑞泽8CTCC_抖音.json` | 抖音 3 段（590×1280；行距 43.8、y_phase 26、x_text 84、badge_span 50、rect [0,668,590,1190]、x2_from=content、x_ignore_from 380 丢右侧商品讲解卡）→ `out/2026-09-14艾瑞泽8CTCC职业联赛（上海站）（渠道七七）/抖音` |
| `09-14艾瑞泽8CTCC_快手.json` | 快手 3 段（590×1280；行距 45.0、y_phase 18、x_text 81、badge_span 47、rect [0,655,590,1200]、x2_from=window、无 x_ignore_from）→ 同批次 `快手` |
| `09-14艾瑞泽8CTCC_视频号.json` | 视频号 4 段（590×1280；行距 41.9、y_phase 14、x_text 64、badge_span 46、wrap_indent、wrap_gap_ratio 0.75、rect [0,686,590,1190]、x_ignore_from 350、fan_badge_x_max 90）→ 同批次 `视频号` |
| `out/2026-09-14…/media/dy_18-14-19-00_live.mp4` | 抖音末段只保留直播部分（PyAV 流拷贝到 2805.2s；输入栏最后出现在 2805.37s） |
| `out/2026-09-14…/media/ks_17-59-19-00_live.mp4` | 快手末段只保留直播部分（PyAV 流拷贝到 3711.8s；输入栏最后出现在 3711.97s） |
| `out/2026-09-14…/media/wx_18-38-19-00_live.mp4` | 视频号末段只保留直播部分（PyAV 流拷贝到 1335.1s；输入栏最后出现在 1335.30s） |
| `/run/media/puyu/MyDisk/2026-09-14艾瑞泽8CTCC职业联赛（上海站）（渠道七七）/` | 本批原始素材（外置盘，跑前确认已挂载） |

## 2026-11-11 约定：09-10 及以后配置的远程表情表

- 远程批次的表情表目录只有 `*-emoji.json`、没有 `img/`（上传脚本只传表文件）⇒
  所有 09-10 及以后的配置都显式打开 `emoji.tables.<平台>.build.enabled=true`，
  run.sh 的预建表步骤会在远程重建：抖音=官方 API（214 张）、快手=live.kuaishou.com（207 张）、
  视频号=codeload tarball（109 张）。
- 新建配置照此写；不想每次重下用 `LIVEPIPE_SKIP_EMOJI=1 ./run.sh …`。
- 10-31 素材在外置盘 `/run/media/puyu/A1B7-2982/`，不挂载时 `check` 会报视频不存在（与配置无关）。

## 2026-11-11 新增：奇瑞2026全球创新大会（CGIC 2026·芜湖）2026-10-19~24 配置

| 路径 | 作用 |
|---|---|
| `1019-1024奇瑞创新大会_抖音.json` | 抖音 4 段（590×1280；行距 43.8、y_phase 22、x_text 86、badge_span 74、wrap_gap_ratio 0.85、rect [0,672,590,1196]、x2_from=content、x_ignore_from 380 丢右侧商品讲解卡）→ `out/2026-1019-1024奇瑞创新大会/抖音` |
| `out/2026-1019-1024奇瑞创新大会/抖音/seg1..seg4/panels.json` | 四段共用的手写面板（[0,672,590,1196]；y1 取在列表顶部 ~70px 渐隐带之下） |
| `/run/media/puyu/MyDisk/2026-1019-1024奇瑞创新大会/抖音-奇瑞汽车/` | 本批原始素材（外置盘，跑前确认已挂载；只有抖音一个平台，4 段 7：58~13：15 连续录屏，末段末帧仍在直播、无需裁尾） |

## 2026-11-15 素材路径迁移（8 份配置）

| 配置 | 旧前缀 → 新前缀 |
|---|---|
| `09-05风云A9L渠道_抖音.json` · `09-05风云A9L渠道_视频号.json` | `/home/puyu/work/data/` → `/run/media/puyu/MyDisk/` |
| `09-06猎鹰智驾_{抖音,视频号,快手}.json` | `/home/puyu/work/data/` → `/run/media/puyu/MyDisk/` |
| `09-07猎鹰智驾_快手.json` · `09-07猎鹰智驾_视频号.json` | `/home/puyu/work/data/` → `/run/media/puyu/MyDisk/` |
| `09-07猎鹰智驾_抖音.json` | `~/LiveMonitorMockScale/data/` → `/run/media/puyu/MyDisk/` |

合计 34 个视频路径常量 + 8 处文件头注释；改完 `C.load` 严格模式全部通过，
所有 `input.videos` 都存在。`08-08CTCC*` / `10-31_*` / 风云A9L 年型车等仍在未挂载的
`/run/media/puyu/A1B7-2982`，没有同名新文件，未改。

## 2026-11-15 PLATFORM_CN 补档案名别名

| 路径 | 改动 |
|---|---|
| `livepipe/tools/export_user_images/common.py` | `PLATFORM_CN` 补 5 个档案名别名：`kuaishou_480` / `kuaishou_720` / `kuaishou_large` → 快手，`douyin_app_480` → 抖音，`weibo_app` → 微博（各带出处注释） |
| `livepipe/tools/verify_run/common.py` | 同名副本同步（两处要求逐项一致） |
| `livepipe/tools/export_user_images/selftest.py` | 补 5 条 `platform_dir` 断言（自检 57 项） |
| `out/` 24 个段级导出目录 | `mv` 成中文平台名（09-06~09-10 快手、09-07 抖音、09-22 微博）；manifest 的 `rel` 不含平台段，改名安全 |



| 路径 | 作用 |
|---|---|
| `livepipe/tools/verify_run/` | verify 实现：`common.py`（报告结构 / 读盘 / 交付口径复刻）、`checks.py`（按步骤核对）、`cli.py`、`doc.py`、`selftest.py` |
| `livepipe/verify_run.py` | 薄壳 → `tools/verify_run/` |
| `livepipe/__main__.py` | 新增 `verify` 子命令（与 run/check/panels/match 同级） |
| `livepipe/config/paths.py` · `io.py` | `_expand_videos(..., check=False)` / `load(..., check_videos=False)`：素材盘没挂载也能读配置（verify 只核对已落盘产物） |



| 路径 | 作用 |
|---|---|
| `撞库名单/split_account_lists.py` | 一次性拆分脚本：读 `撞库名单/【渠道】账号清单截止10.20.xlsx`，按「场次 × 平台」写到 `撞库名单/可用名单/`。源表每一块的 sheet / 表头行 / 列坐标在 `blocks()` 里显式列出，换表时按同样形状补 |
| `撞库名单/可用名单/` | 产物：每场 `<slug>.csv`（平台并排宽表，`livepipe match` 直接读）+ `<slug>_<平台>.txt`（一行一个昵称，配 `--platform`）+ `README.md` / `manifest.json`（人数、源表位置、对应 out 批次）。含真实昵称，随 `撞库名单/` 一起 gitignore |



| 路径 | 作用 |
|---|---|
| `09-16老章拆车_抖音.json` | 抖音 5 段（590×1280；行距 43.8、y_phase 10、x_text 84、badge_span 52、wrap_gap_ratio 0.85、rect [0,920,590,1190]、x2_from=content、x_ignore_from null、account_key=nickname_level_if_masked）→ `out/2026-09-16“汽车人老章”A9拆车（渠道七七）/抖音` |
| `09-16老章拆车_视频号.json` | 视频号 6 段（590×1280；行距 41.9、y_phase 15、x_text 64、badge_span 46、wrap_indent、wrap_gap_ratio 0.75、rect [0,740,590,1190]、x_ignore_from 350、fan_badge_x_max 90、account_key=nickname_level_if_masked）→ 同批次 `视频号/` |
| `out/2026-09-16…/media/dy_22-09-22-17_live.mp4` | 抖音末段只保留直播部分（PyAV 流拷贝到 131.7s；输入栏最后出现在 131.88s） |
| `out/2026-09-16…/media/wx_22-03-22-18_live.mp4` | 视频号末段只保留直播部分（PyAV 流拷贝到 515.1s；输入栏最后出现在 515.31s） |
| `/run/media/puyu/MyDisk/2026-09-16“汽车人老章”A9拆车（渠道七七）/` | 本批原始素材（外置盘，跑前确认已挂载；抖音 `老章说车` 5 段 + 视频号 `老章拆车` 6 段，均 590×1280，无旋转） |

## 2026-11-11 新增：奇瑞QQ3摩登版上市（渠道七七）2026-09-22 配置（多账号，一账号一份）

| 路径 | 作用 |
|---|---|
| `09-22奇瑞QQ3_抖音QQ汽车.json` | 抖音 6 段（616×1280；行距 45.5、y_phase 26、x_text 84、badge_span 50、wrap_gap_ratio 0.80、wrap_indent、rect [0,665,616,1205]、x2_from=content、x_ignore_from 395 丢右侧讲解卡、account_key=username）→ `out/2026-9-22奇瑞QQ3摩登版上市（渠道七七）/抖音/QQ汽车` |
| `09-22奇瑞QQ3_抖音奇瑞集团.json` | 抖音 1 段（590×1280；行距 43.5、y_phase 21、x_text 80、badge_span 50、wrap_gap_ratio 0.78、rect [0,665,590,1205]、x2_from=content、无 x_ignore_from）→ 同批次 `抖音/奇瑞集团` |
| `09-22奇瑞QQ3_视频号奇瑞QQ.json` | 视频号 7 段（590×1280；行距 41.5、y_phase 16、x_text 30、badge_span 44、wrap_indent、wrap_gap_ratio 0.78、rect [0,645,590,1190]、x2_from=window、account_key=nickname_level_if_masked）→ 同批次 `视频号/奇瑞QQ` |
| `09-22奇瑞QQ3_视频号奇瑞汽车.json` | 视频号 1 段（592×1280；行距 41.5、y_phase 15、x_text 30、wrap_gap_ratio 0.75、rect [0,650,592,1190]、底部广告卡 overlay）→ 同批次 `视频号/奇瑞汽车` |
| `09-22奇瑞QQ3_视频号奇瑞集团.json` | 视频号 1 段（**800×1280**；行距 **32.0**、y_phase 12、x_text 25、badge_span 44、wrap_gap_ratio 0.78、rect [0,690,800,1205]）→ 同批次 `视频号/奇瑞集团` |
| `09-22奇瑞QQ3_视频号奇瑞瑞享生活.json` | 视频号 1 段（**580×1280**；行距 40.5、y_phase 16、x_text 20、badge_span 44、wrap_gap_ratio 0.78、rect [0,645,580,1190]）→ 同批次 `视频号/奇瑞瑞享生活` |
| `09-22奇瑞QQ3_视频号EXEED星图汽车.json` | 视频号 1 段（590×1280；行距 41.5、y_phase 14、x_text 30、badge_span 44、wrap_gap_ratio 0.75、rect [0,665,590,1190]）→ 同批次 `视频号/EXEED星图汽车` |
| `09-22奇瑞QQ3_视频号风云新能源汽车.json` | 视频号 1 段（**888×1920**；行距 **57.5**、y_phase 18、x_text 48、badge_span 44、**wrap_gap_ratio 0.90**（续行 0.82）、rect [0,1050,888,1745]、emoji_workers 1）→ 同批次 `视频号/风云新能源汽车` |
| `09-22奇瑞QQ3_微博奇瑞QQ.json` | 微博 1 段（590×1280；自定义 `weibo_app` 档案：行距 **39.2**（=评论行距 78.5 的一半，见 buglog 2026-11-11）、y_phase 30、x_text 24、badge_span 44、`rank_pill`「榜N」在昵称右边、`wrap_at_text_left`、rect [0,645,590,1190]）→ 同批次 `微博/奇瑞QQ` |
| `out/2026-9-22…/media/dy_qq_22-03-23-18_live.mp4` | 抖音 QQ汽车末段只保留直播部分（PyAV 流拷贝到 3867.7s；输入栏最后出现在 3867.9s；原 74.5min） |
| `out/2026-9-22…/media/dy_jt_18-12-19-48_live.mp4` | 抖音 奇瑞集团单段只保留直播部分（裁到 4780.8s；输入栏最后 4781.0s；原 98.9min） |
| `out/2026-9-22…/media/wx_qq_21-51-22-15_live.mp4` | 视频号 奇瑞QQ 末段只保留直播部分（裁到 1172.6s；输入栏最后 1172.8s；原 24.3min） |
| `out/2026-9-22…/media/wx_qc_18-29-22-09_live.mp4` | 视频号 奇瑞汽车单段只保留直播部分（裁到 5141.7s；输入栏最后 5141.9s；原 241.1min 里只有前 85.7min 是直播） |
| `out/2026-9-22…/media/wx_fy_18-12-22-03_live.mp4` | 视频号 风云新能源汽车单段只保留直播部分（裁到 13677.8s；输入栏最后 13678.0s；原 231.1min） |
| `/run/media/puyu/MyDisk/2026-9-22奇瑞QQ3摩登版上市（渠道七七）/` | 本批原始素材（外置盘，跑前确认已挂载；抖音 2 账号 + 视频号 6 账号 + 微博 1 账号，共 20 段） |

## 2026-11-16 撞库结果落盘结构（全量 21 场）

| 路径 | 作用 |
|---|---|
| `out/<批次>/撞库结果/<场次>/` | 每场一套：`matches.json`、`命中明细.csv`、`未命中明细.csv`、`撞库结果.xlsx`、`README.md`、`撞库清单/*.html`、`标记图/` |
| `out/<批次>/撞库结果/<场次>/标记图/<平台>/<账号目录>/<账号目录-N>/*.jpg` | 命中账号的交付口径图（账号目录下按名单项再分一层） |
| `out/<批次>/撞库结果/<场次>/标记图/未命中/<平台>/<账号目录>/…` | 未命中名单的最近账号图，单独归档 |
| `out/2026-9-22奇瑞QQ3摩登版上市（渠道七七）/.撞库合并/` | 09-22 多账号批次的虚拟交付树（`<平台>/all_users_正面_3张以上` + `segN__<子账号>` 符号链接 + 合并 manifest/judgments）；`match` 的 live 根指到它，HTML 场次名要 `--title` 覆盖，否则取目录名「撞库合并」 |
| `out/10.31/撞库结果/` | 早期单层结果（无 `<场次>/` 层），不在 21 场全量里 |
| `out/2026-1019-1024奇瑞创新大会/撞库结果/10-20_主名单/` | 创新大会抖音场（素材 creation_time=2026-10-20 09:35）；名单用渠道表 10.20 sheet，视频前缀 `/root/livepipe_batches/1019-1024奇瑞创新大会_抖音/videos` → `/run/media/puyu/MyDisk/2026-1019-1024奇瑞创新大会` |

## 2026-11-16 新增：全球车的中国路 2026-09-30~10-06 配置（16 份）

| 路径 | 作用 |
|---|---|
| `09-30_全球车的中国路_{抖音,快手,视频号}.json` | 9.30 三平台（4/2/4 段）；「快手/」里是视频号、「视频号/」里是快手（已按画面换回）→ `out/2026-0930-1006全球车的中国路/09-30/<平台>` |
| `10-01_全球车的中国路_{抖音,快手,视频号}.json` | 10.01 三平台（5/3/4 段）→ 同批 `10-01/<平台>` |
| `10-02_全球车的中国路_{抖音,快手,视频号}.json` | 10.02 三平台（5/2/6 段；**「快手/1」是抖音**，归档到抖音配置）→ 同批 `10-02/<平台>` |
| `10-03_全球车的中国路_{抖音,快手,视频号}.json` | 10.03 三平台（3/2/4 段）→ 同批 `10-03/<平台>` |
| `10-04_全球车的中国路_{抖音,视频号}.json` | 10.04 两平台（各 1 段）→ 同批 `10-04/<平台>` |
| `10-06_全球车的中国路_{抖音,视频号}.json` | 10.06 两平台（各 3 段）→ 同批 `10-06/<平台>` |
| `/run/media/puyu/MyDisk/2026-0930-1006《全球车的中国路》/_live/` | 21 段裁到「直播已结束」之前的 stream-copy 副本（约 29GB），配置 `input.videos` 引用它 |
| `out/2026-0930-1006全球车的中国路/<MM-DD>/<平台>/segN/panels.json` | 16 份配置的 panel 副本（panels 步已跑，parse 前已就绪） |

- 平台档案：抖音 `douyin_app` 行距 44.0 · x_text 86；快手 `kuaishou` 42.0 · x_text 81 ·
  wrap_indent；视频号 `wechat_app` 41.0 · x_text 66 · badge_span 44 · fan_badge_x_max 66 ·
  x_ignore_from 440（丢「若主播7天」订阅卡）。
- 视频号 rect y1 按**场次横幅底 +3** 抬过 banner（`第N站（…）：…` 带冒号，否则被当评论）。

## 2026-11-16 新增：奇瑞「探秘局」2026-10-24 视频号配置（1 份）

| 路径 | 作用 |
|---|---|
| `10-24奇瑞探秘局_视频号.json` | 视频号 4 段（590×1280；seg2/seg4 用裁后副本）→ OSS `直播产生结果/2026-10-24奇瑞探秘局/视频号`；行距 41.5 / x_text 64 / rect [0,660,590,1190]；`input.videos` 读 OSS `web/1/…/直播录屏/奇瑞探秘局/10.24/`，结果写 `直播产生结果/2026-10-24奇瑞探秘局/`（2026-11-19 直写） |
| `out/2026-10-24奇瑞探秘局/media/` | seg2/seg4 裁掉「直播已结束」页的 stream-copy 副本（本地源；OSS 副本在 `奇瑞探秘局/10.24/_live/`） |
| `撞库名单/可用名单/10-24/` | 对应名单 `10-24_探秘局_昵称.txt`（19 个昵称，只有一列；`match` 需 `--platform 视频号`） |

## 2026-11-16 新增：奇瑞「探秘局」2026-10-25 视频号配置（1 份）

| 路径 | 作用 |
|---|---|
| `10-25奇瑞探秘局_视频号.json` | 视频号 2 段（590×1280；seg2 用裁后副本）→ OSS `直播产生结果/2026-10-25奇瑞探秘局/视频号`；行距 41.5 / x_text 64 / rect [0,660,590,1190]；`input.videos` 读 OSS `web/1/…/直播录屏/奇瑞探秘局/10.25/`，结果写 `直播产生结果/2026-10-25奇瑞探秘局/`（2026-11-19 直写） |
| `out/2026-10-25奇瑞探秘局/media/` | seg2 裁掉「直播已结束」页的 stream-copy 副本（本地源；OSS 副本在 `奇瑞探秘局/10.25/_live/`） |
| `撞库名单/可用名单/10-25/` | 对应名单 `10-25_探秘局_昵称.txt`（20 个昵称，只有一列；`match` 需 `--platform 视频号`） |

## 2026-11-16 新增：奇瑞创新大会 2026-10-19（6 个账号 × 6 份配置）

| 路径 | 作用 |
|---|---|
| `10-19奇瑞创新大会_抖音QQ汽车.json` | 抖音 QQ汽车 6 段（590×1280，行距 43.8 / x_text 86 / rect [0,600,590,1196]）→ `out/2026-10-19奇瑞全球创新大会/抖音/QQ汽车` |
| `10-19奇瑞创新大会_抖音奇瑞汽车.json` | 抖音 奇瑞汽车 5 段（同版式；12:05-12:26 缺 21min 素材）→ `…/抖音/奇瑞汽车` |
| `10-19奇瑞创新大会_抖音风云新能源.json` | 抖音 风云新能源 6 段（**大字号**：行距 58.5 / x_text 98 / rect [0,635,590,1196]）→ `…/抖音/风云新能源` |
| `10-19奇瑞创新大会_视频号奇瑞QQ.json` | 视频号 奇瑞QQ 5 段（41.5 / x_text 64 / rect [0,650,590,1190]）→ `…/视频号/奇瑞QQ` |
| `10-19奇瑞创新大会_视频号奇瑞汽车.json` | 视频号 奇瑞汽车 3 段（**592×1280**；rect [0,650,592,1190]）→ `…/视频号/奇瑞汽车` |
| `10-19奇瑞创新大会_视频号风云新能源汽车.json` | 视频号 风云新能源汽车 5 段（同 590 版式）→ `…/视频号/风云新能源汽车` |
| `out/2026-10-19奇瑞全球创新大会/media/` | 6 条末段裁掉「直播已结束」页的 stream-copy 副本（6 条流都在 ≈17:00 结束直播） |
| `out/2026-10-19奇瑞全球创新大会/<平台>/<账号>/segN/panels.json` | 6 份配置的 panel 副本 + `panels.resolved.json`（panels 步已跑） |

- 老配置 `1019-1024奇瑞创新大会_抖音.json` 的素材目录
  `/run/media/puyu/MyDisk/2026-1019-1024奇瑞创新大会/` 已不在盘上；10-19 这批是同一天
  更完整的录制（8h），归档到新场次目录，不复用旧 out 树。

## 2026-11-17 新增：直播看管 OSS 化技术方案

| 路径 | 作用 |
|---|---|
| `livepipe/docs/oss-migration.html` | 技术方案：录屏/结果统一 OSS、dypy 直写、bypy+goofys 环境基线、实测边界（写 9.4–12.5MB/s、冷读 6.6MB/s、小文件 10–13 个/s、无软/硬链、mtime 忽略）、目录契约、P0–P4 迁移计划；含 221GB OSS 存量与 82 段引用的真实映射审计 |

## 2026-11-17 新增：OSS P0 映射清单与开发日志

| 路径 | 作用 |
|---|---|
| `livepipe/tools/oss_ingest/` | OSS 入库工具（stdlib）：`manifest`/`audit`/`sync`/`verify`/`verify-media`/`ingest`/`check-env`/`--selftest`；`common.py` 负责根解析与共享计数口径，`manifest.py` 按事件根建索引并做全片/裁尾匹配 |
| `livepipe/oss_ingest.py` | 薄壳入口（`python3 livepipe/oss_ingest.py ...`） |
| `scripts/build_oss_inventory.py` | P0 编排：两批事件根（0930 50 个可用视频 / 10.19 30 个，跳过 0 字节与临时名）各建一次索引，生成 22 份 manifest、`index.json`、`audit.json`、`summary.json`、`p1-upload-list.csv`（10 列）；`generated_at` 固定，重跑字节一致；P0/P1 两套基线自动切换，`--verify-uploads` 按 dst 核字节 |
| `scripts/p1_upload.py` | P1 补传器：按 `p1-upload-list.csv` 逐条 `.part` → 字节校验 → `os.replace`；已存在且字节相符则跳过；`--dry-run` / `--verify-only`，结束写 `p1-upload-report.json` |
| `scripts/p15_repoint.py` | P1.5 切读：整段取 manifest 的 `segments[].oss`、裁尾取 `p1-upload-list.csv` 的 `src_abs→dst`，只替换配置文本里 `input.videos` 的 JSON 字符串；`--verify` 只核对、`--apply` 幂等写入并备份到 `out/p1.5/backup/<UTC>/`、`--rollback` 整体回滚 |
| `scripts/oss_sync.py` | P2 结果同步：本地结果目录 → OSS 增量复制；size 跳过 + `--sha256`、跳过软链/隐藏文件、直接写正式名（goofys rename 对含 `+` 目录会 404）、3 次重试、4 并发；`--dry-run` / `--verify-only` / `--report`，结尾 `OSS_SYNC_RESULT {json}` |
| `scripts/selftest_oss_sync.py` | `oss_sync.py` 离线自检（30 项：增量/重跑跳过/哈希/软链/隐藏/dry-run/verify/extras/`+` 目录） |
| `scripts/oss_download.py` | P3 下载链路：`live` 跑下载器写 OSS `.part` → 校验字节/时长 → 改名 + manifest，失败不留正式文件；`baidu` bypy 授权/列目录 + PCS 单请求顺序流式写 OSS `.part`（**不落本地暂存**），支持 `--resume` 续传，同一套收尾 |
| `scripts/p2_backfill.py` | P2 回填：外置/本地 out 事件树 → `直播产生结果`；两阶段 `deliverables`（json/all_users/撞库结果）→ `all`；全球车的中国路统一到 `2026-0930-1006《全球车的中国路》/<MM.DD>`；可断点续传 |
| `scripts/selftest_oss_download.py` | `oss_download.py` 离线自检（32 项：live 成功/幂等/失败/违约/字节闸门/dry-run；baidu 直写/非视频/来源/中途断流留 `.part`/缺 flag 拒绝/resume/dry-run） |
| `scripts/p4_cleanup.py` | P4 清盘：`shadow-start/status` 7 天影子期；`audit` 覆盖审计；`plan`；`apply`（影子期满+0 不可删+`--yes`）；`rollback-check`；`migrate-emoji`（表情表迁出外置 out + 改 base_dir） |
| `scripts/oss_relocate_prefix.py` | OSS 前缀迁移：服务端 CopyObject（3GB 秒级；本桶 CopyPart 超时不可用）、同大小跳过、`--verify-only`、报告；从不删源。2026-11-19 把 P1/P2 误写的前缀 `奇瑞汽车/直播看管/{直播录屏,直播产生结果}` 复制到 `web/1/奇瑞汽车/直播看管/` |
| `scripts/oss_purge_prefix.py` | 旧前缀清空（迁移收尾）：先全量 list 源/目标，源里每个对象必须在 `--ensure` 前缀有同名同字节副本（`*/` 占位除外），否则退出 2 一个不删；默认 dry-run，`--delete` 走 `batch_delete_objects`（1000 键/批，并发 8），删完整段源前缀必须为空 |
| `scripts/oss_diff_prefixes.py` | 双向前缀比对：按相对路径分 identical / metadata_diff（同大小异 ETag）/ size_diff / only_old / only_new；`--fill` 只把 only_old 服务端 CopyObject 补到新前缀（永不覆盖 size_diff/metadata_diff），补完重列确认仍缺数 |
| `livepipe/docs/p4-audit-summary.json` | P4 审计摘要（影子期、staging/local_out/external_out 覆盖数字、inputs 检查、表情表迁移、孤儿录屏、回滚检查） |
| `~/livepipe_assets/out/` | P4 后 livepipe 的表情表新家（4 套，base_dir 指向这里；不参与清盘） |
| `out/oss_inventory/2026-11-17/` | 交付物（`out/` 已 gitignore）：22 manifests + 4 份汇总 + 上传清单（P0 29 行 → P1 后 27 行）+ `p1-upload-report.json` / `p1-verify-report.json` |
| `livepipe/docs/oss-inventory-summary.json` | 提交用摘要（totals/acceptance/缺失段/裁尾分析，不含逐段 items） |
| `livepipe/docs/oss-p0-devlog.html` | 本次开发日志（真实数据、内联 SVG/CSS） |
| `livepipe/docs/oss-p1-devlog.html` | P1 补传日志（29 对象 / 46.52GB；落盘流程、实测速度、四层验收、逐条状态） |
| `livepipe/docs/oss-p15-devlog.html` | P1.5 切读日志（22 份配置 / 82 段改读 OSS；22/22 check；两个样片实跑） |
| `livepipe/docs/oss-p2-devlog.html` | P2 结果链路日志（oss_sync 工具、批处理接线、真实任务 664 文件 / 122.94MB 的对象+字节+SHA256 验收、goofys `+` 目录 rename 坑） |
| `livepipe/docs/oss-p3-devlog.html` | P3 下载链路日志（live 直写 3.2GB `.part` 收尾；bypy 目录 → 暂存 → OSS；失败不留正式文件；22 项自检） |
| `livepipe/docs/oss-p4-devlog.html` | P4 影子期与清盘审计日志（覆盖口径、表情表迁移、孤儿录屏补 OSS、回滚检查、7 天后命令） |
| `~/脚本/ssh/{batch_local,batch_remote}.py` | 队列脚本：收/回传核对通过后按 `oss_results_root`/`oss_sync`/任务 `oss_result` 调 `oss_sync.py`；`--oss-only` 只补同步（文件在仓库外）。`local_target()`：`output.dir` 为绝对路径时也取最后一个 `/out/` 后缀，落到 `out/<场次>/<平台>` 事件树 |
