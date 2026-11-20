## 2026-11-20 · 推送 GitHub + 更新 work1/2/3 — 完成

✅ 188 文件提交 `5746867` 推到 origin/main；work1/2/3 的 `/root/LiveMonitorMockScale`
均快进到 `5746867`。三台的本地改动（`09-07猎鹰智驾_抖音.json` 视频路径 →
`~/LiveMonitorMockScale/data/`）先 stash、拉完原样恢复（md5 不变），`data/` 未动。

🚀 下一步
- 服务器跑批前按批次素材实际位置改配置路径，或 `export --video-map 旧=新`。
- 旧 stash 条目可 `git stash drop` 清理（内容已回到工作区）。
- 待授权：两条 `oss_purge_prefix.py --delete`（见上一节）。

## 2026-11-19 · 复核桶根旧前缀 + 清空脚本 — 复制已核对，删除待授权

✅ 用户指出 `~/oss-bucket/奇瑞汽车/直播看管/` 位置不对，正确位置是
`~/oss-bucket/web/1/奇瑞汽车/直播看管/`。

- 重新全量核对（`oss_relocate_prefix.py --verify-only`）：
  - `直播录屏`：源 38 对象 / 42,081,575,164 B；目标 65 对象；`missing_or_mismatch=0`。
  - `直播产生结果`：源 406,616 对象 / 85,078,098,111 B；目标 422,733 对象 /
    88,006,738,224 B（多出的是迁移后新写的结果）；`missing_or_mismatch=0`。
  - 桶根 `奇瑞汽车/` 下共 406,654 个对象 = 38 + 406,616，**没有**落在这两个
    子前缀之外的遗留对象；父级无占位对象。
- 新增 `scripts/oss_purge_prefix.py`：先全量 list 源/目标并要求源里每个对象在
  `--ensure` 前缀有同名同字节副本（目录占位除外），任何不符退出 2 一个不删；
  默认 dry-run，`--delete` 用 `batch_delete_objects`（1000 键/批、8 并发），
  删完整段源前缀必须为空。`直播录屏` dry-run 已通过。
- 用户要求「对一下两个路径，缺了补」：新增 `scripts/oss_diff_prefixes.py` 双向比对
  整棵 `奇瑞汽车/直播看管` ↔ `web/1/奇瑞汽车/直播看管`（报告
  `out/oss_relocate/diff_fill_prefixes.json`）：
  - 旧 406,654 对象 / 127,159,673,275 B；新 423,135 对象 / 384,013,205,930 B；
  - **only_old = 0、size_diff = 0、metadata_diff = 0**（逐对象大小 + ETag 全同）；
  - only_new = 16,481 对象 / 256,853,532,655 B —— 迁移后新写到正确位置的数据
    （0930/1019 原始录屏、探秘局、素材等），不需要回填旧前缀。
  - `--fill` 已跑：copied 0 / failed 0 —— 新前缀什么都不缺。
- ⚠ 实际 `--delete` 被 Claude Code 自动模式分类器拦截（Cloud Storage Mass Delete），
  未执行。需要用户批准后运行（两条命令见下方「🚀 下一步」）。

🚀 下一步

```bash
python3 scripts/oss_purge_prefix.py --prefix '奇瑞汽车/直播看管/直播录屏' \
  --ensure 'web/1/奇瑞汽车/直播看管/直播录屏' --delete
python3 scripts/oss_purge_prefix.py --prefix '奇瑞汽车/直播看管/直播产生结果' \
  --ensure 'web/1/奇瑞汽车/直播看管/直播产生结果' --delete \
  --report out/oss_relocate/purge_results_prefix.json
```

- 删完后从 `p4_cleanup.py` 的 `OSS_ROOTS` 移除两个旧前缀，重跑 `audit` 复核覆盖面。
- goofys 有元数据缓存，删完 `ls ~/oss-bucket/奇瑞汽车/` 可能短暂仍显示空目录，
  以 `oss2` 列前缀为 0 为准（脚本退出码 0 即已清空）。

---

## 2026-11-19 · 探秘局素材直写 OSS + 两份配置切读 — 完成

✅ 用户要求「本地暂存不要存直接下载到oss」。原 `oss_download.py baidu` 是
bypy 下载 → 本地暂存 → 复制 OSS；实测 bypy 自带 20MB 分块下载在 goofys 上
1.28GB 文件从第二块起 HTTP 206 重试耗尽，只剩 20MB 截断文件。

- 改法：bypy 只做授权/列目录（`_walk_proceed_remote_dir`），内容走 PCS
  `Range: bytes=offset-(size-1)` 单请求 + `requests stream=True` 顺序写
  `<final>.part`（一次 open、顺序 write，同 `cp`）；`finalize_part` 校验字节/时长 →
  改名 + manifest；失败保留 `.part`，`--resume` 续传，`--overwrite` 重来。
  移除 `--staging/--bypy/--processes/--clean-staging`。
- 离线自检 `selftest_oss_download.py` **32/32**（直写/非视频/中途断流留 `.part`/
  缺 flag 拒绝/resume/dry-run）。

✅ 真实入库 `~/oss-bucket/web/1/奇瑞汽车/直播看管/直播录屏/奇瑞探秘局/`：

| 落点 | 字节 | 时长 | 来路 |
|---|---|---|---|
| `10.24/上午/8： 43-9： 40.mp4` | 3,225,179,289 | 3462.14s | 百度直写 |
| `10.24/上午/9： 41-10： 25.mp4` | 1,846,276,289 | 2696.94s | 百度直写 |
| `10.24/下午/12： 56-14： 10.mp4` | 3,225,564,914 | 4496.31s | 百度直写 |
| `10.24/下午/14： 10-14： 39.mp4` | 1,425,814,445 | 1790.92s | 百度直写 |
| `10.25/下午/14： 01-15： 17.mp4` | 3,225,691,953 | 4592.85s | 百度直写 |
| `10.25/下午/15： 17-15： 55.mp4` | 1,287,410,910 | 2292.96s | 百度直写 |
| `10.24/_live/wx_am_09-41-10-25_live.mp4` | 1,186,304,191 | 1431.8s | 裁尾副本 |
| `10.24/_live/wx_pm_14-10-14-39_live.mp4` | 950,800,490 | 1332.2s | 裁尾副本 |
| `10.25/_live/wx_pm_15-17-15-55_live.mp4` | 999,703,529 | 1781.8s | 裁尾副本 |

- 6 个原件字节与本地 MyDisk 原件逐一相同（合计 14,235,937,800 B）；10.25 两段
  733s、10.24 四段 1573s（约 6~9MB/s）。manifest 齐全。
- `10-24奇瑞探秘局_视频号.json` / `10-25奇瑞探秘局_视频号.json` 的 `input.videos`
  已改指 OSS（文件头注释同步）；`livepipe check` **2/2 rc=0**。

✅ 结果也不落本地（用户 2026-11-19 要求「结果也不能在本地」）：

- 两份配置的 `output.dir` / `collect.out` 改到
  `~/oss-bucket/web/1/奇瑞汽车/直播看管/直播产生结果/2026-10-2{4,5}奇瑞探秘局/视频号`；
  `base_dir` 改到 OSS `~/oss-bucket/web/1/奇瑞汽车/直播看管/素材`，微信表情表
  （wechat-emoji.json + 图片等共 113 个文件 / 6,460,888 B）已上传到
  `<base_dir>/out/wechat_emoji/`，从 OSS 加载 109 个模板约 2.5s。
  配置里已无 `/run/media/...C0DE…` 与 `~/livepipe_assets` 引用。
- goofs **不支持 `open("a")`**：`rows.jsonl` 续跑会 ENOTSUP。已改
  `livepipe/parsing/cli/run.py`：先试 append，失败则 `open("w")` 重写已有行再继续。
- 20s 真实切片直写 OSS 探针验证：panels.json / rows.jsonl（60 行）/ frames（5 张）/
  comments.jsonl（11 条）/ summary.json 全部写成功，rc=0；探针目录已删。
- ⚠ 23:25 发现 goofs 掉线（ENOTCONN）；nautilus 占用导致普通卸载 busy，已用
  `fusermount3 -uz` 懒卸载 + `setsid nohup ~/goofys …` 重挂，1s 就绪；表情表
  109 模板、两份配置 check 2/2 均恢复。goofs 仍未 systemd 托管。

🚀 下一步

- 跑 10-24 / 10-25 探秘局：`$P -m livepipe run <cfg> --only panels,parse`
  （10-25 旧 seg1 半截 rows 在旧外置目录，新 OSS 输出目录为空，会从头抽），再
  `NLP_API_KEY=… --only filter,qualify,export,collect`；交付树直接落 `直播产生结果/`。
- 旧的本地/外置 out 里若有探秘局产物，按 P4 审计口径处理（本次未删）。

---

## 2026-11-19 · OSS 根更正为 `web/1/` + P1/P2 对象迁移 — 完成（复制已核对；旧前缀待删）

✅ 用户确认项目 OSS 根是 **`~/oss-bucket/web/1/奇瑞汽车/直播看管/`**（不是桶根
`~/oss-bucket/奇瑞汽车/直播看管/`）。后者是 P1/P2 早期写错的前缀：

| 错误前缀 | 对象 / 字节 | 正确落点 |
|---|---|---|
| `奇瑞汽车/直播看管/直播录屏/` | 38 对象 / 42,081,575,164 B | `web/1/奇瑞汽车/直播看管/直播录屏/` |
| `奇瑞汽车/直播看管/直播产生结果/` | 406,616 对象 / 85,078,098,111 B | `web/1/奇瑞汽车/直播看管/直播产生结果/` |

- 新增 `scripts/oss_relocate_prefix.py`：服务端 CopyObject 复制（含 3GB 大文件，实测
  0.1s/个）；⚠ 本桶 `upload_part_copy` 会 60s 读超时，**不要用 multipart copy**。
  带 `--verify-only`、断点续传（目标同大小跳过）、报告 JSON；**从不删源对象**。
- `直播录屏` 已完成：复制 38 个（17 个大对象补拷），`--verify-only` 38/38、
  42,081,575,164 B、0 缺失（目标多一个 25 B 的 `测试文件.txt`）。
- `直播产生结果` 已完成：406,616 个对象、85,078,098,111 B 全部到位
  （复制 395,995 / 断点续传跳过 10,621 / 失败 0，9,203.8s）；
  `--verify-only` 406,616 对 406,617（目标多一个空前缀占位对象）、
  两端字节同为 85,078,098,111、0 缺失（报告 `out/oss_relocate/results_prefix.json`）。
- 配置：21 份 `*.json` 的 **27 个** `_live` 路径改为 `web/1/...`；`livepipe check` 22/22 rc=0。
- 工具默认根同步到 `web/1/`：`p2_backfill.py`（DEST_ROOT）、`oss_download.py`
  （DEFAULT_ROOT）、`build_oss_inventory.py`（P1_SCREENSHOT_ROOT）、`p4_cleanup.py`
  （OSS_ROOTS 同时保留旧前缀作只读保留根）、`oss_ingest/common.py`
  （DEFAULT_OSS_ROOT/candidates）；README、oss_ingest README、oss-migration.html 同步。
- P2 state `~/.livepipe_p2_backfill/state.json` 的 148 个 dst 已改指新根
  （备份 `state.json.bak-20261119-prefix`）。
- 55 个整段仍读桶根 `直播data/录屏`（旧的存量根，不在本次更正范围）。

🚀 下一步

- 复核后删除桶根旧前缀 `奇瑞汽车/直播看管/{直播录屏,直播产生结果}`（**当前未删**）。
- P4 `audit` 重跑时把旧前缀当保留根，删除后再复核覆盖面。
- 百度 `直播录屏/奇瑞探秘局/` 已由另一条工作线直写 OSS 并切读（见顶部当天的
  「探秘局素材直写 OSS」条目），不需要再重复入库。

---

## 2026-11-19 · 百度「直播录屏/奇瑞探秘局」核对 — 无需新建配置

✅ 该目录（16:00 上传）是 10.24 / 10.25 探秘局**视频号**素材的再次归档：

| 归档子目录 | 文件 | 字节 |
|---|---|---|
| 10.24/上午 | `8： 43-9： 40.mp4` · `9： 41-10： 25.mp4` | 3,225,179,289 · 1,846,276,289 |
| 10.24/下午 | `12： 56-14： 10.mp4` · `14： 10-14： 39.mp4` | 3,225,564,914 · 1,425,814,445 |
| 10.25/下午 | `14： 01-15： 17.mp4` · `15： 17-15： 55.mp4` | 3,225,691,953 · 1,287,410,910 |

- 与本地 MyDisk 原件**逐个大小一致**（合计 14,235,937,800 B）；与 11-17 已归档的
  `10.24/视频号/奇瑞探秘局/{1,2}`、`10.25/1` 的百度 MD5 也相同 ⇒ 同一批文件。
- 仓库已有 `10-24奇瑞探秘局_视频号.json`（4 段，seg2/seg4 用裁尾副本）与
  `10-25奇瑞探秘局_视频号.json`（2 段，seg2 用裁尾副本）覆盖这批素材；
  `livepipe check` 两份都 rc=0（590×1280 / 行距 41.5 / x_text 64 / rect [0,660,590,1190]）。
- 现状：panels 已落盘；10-25 seg1 有半截 `rows.jsonl`（1811 行 ≈ 18.8min）；其余未 parse，
  无交付树。

🚀 下一步

- 跑法照配置头：`$P -m livepipe run 10-24奇瑞探秘局_视频号.json --only panels,parse`
  （10-25 同），再 `NLP_API_KEY=… --only filter,qualify,export,collect`。
- ✅ 已解决（见顶部 2026-11-19「探秘局素材直写 OSS」）：素材已灌入 OSS
  `web/1/…/直播录屏/奇瑞探秘局/`，两份配置 `input.videos` 已切读，check 2/2；
  结果与 `base_dir` 也已切到 OSS（`直播产生结果/` + `素材/`），本地不留产物。

---

## 2026-11-19 · 腾讯文档「视频统计」填全球车撞库/交付数据 — 完成

✅ 文档《直播评论整理_v1(1)》（`DYWRrdnBHSHZKWnpo`，页签 000001）第 **132–149 行**
（2026-09-30～10-06，按平台）填入 5 列（首次填在 135–152 行，随后表格被
上移 3 行，已按当前行重新对齐并清掉错位残留）：

| 列 | 口径 | 来源 |
|---|---|---|
| I 总用户名单数 | 直播账号数 | `matches.json` → `accounts_count` |
| J 撞库命中用户 | 命中名单人数 | `matches.json` → `matched_count` |
| K 撞库命中用户张数 | 命中账号标记图张数 | `撞库结果/<天>/标记图/<平台>/` 图片数 |
| M 可用名单数 | 交付树账号数 | `all_users_正面_3张以上/manifest.json` → `counts.users` |
| P 3张用户评论总数(截图总) | 交付树图片数 | 同上 → `counts.images` |

- 10-05 两行（无录屏，10-05 无直播产物）与 10-01 快手行（当天无快手产物）留空。
- 逐格写入后回读公式栏校验，5 列全部一致；文档「最近保存 19:23」。
- 注意：146–149 行的「直播名称」与「日期+平台」看起来错位（如 10-06 抖音行名称是
  「…视频号…」）；本次按「平台」列填，建议核对这几行的名称。

---

## 2026-11-19 · 全球车的中国路 撞库（09-30～10-06）— 完成

✅ 六天按天撞库，结果在 `out/2026-0930-1006全球车的中国路/撞库结果/<天>/`：
`matches.json` / `命中明细.csv` / `未命中明细.csv` / `撞库结果.xlsx` / `README.md` /
`标记图/` / `撞库清单/*.html`（含汇总）。合计名单 598 · 命中 468 · 未命中 130 ·
命中账号 757（仅直播 311）· 标记图 14,875 张 / 21 个 HTML。
远端批次的 `summary.json` 视频是 `/root/livepipe_batches/...`，用 `--video-map`
映射到 MyDisk 后「仅直播命中 / 未命中最近账号」补图全部成功。

🚀 下一步

- `p2_backfill.py` 的 `GLOBAL_TOP` 只遍历 `^\d{2}-\d{2}$` 天目录，`撞库结果/`
  不会被回填 OSS；需要时给它加一条 `撞库结果/ → 2026-0930-1006《全球车的中国路》/撞库结果/` 映射。

---

## 2026-11-19 · P2 回填 out 结果 → OSS「直播产生结果」— 进行中

🚧 运行中

- 新增 `scripts/p2_backfill.py`：外置 out + 本地 out 的事件树 → OSS
  `直播产生结果`；两阶段 `deliverables`（json/jsonl/md/txt/csv/xlsx/html +
  `all_users_*` + `撞库结果`）→ `all`（其余 seg 帧图）。排除 `media`（录屏副本）、
  `dataset_*`、`*_emoji`、`_backup*`；全球车的中国路统一到
  `2026-0930-1006《全球车的中国路》/<MM.DD>/…`（与 P2 验收 10.04 一致）。
- 计划：37 个任务；交付物 **174,270 文件 / 34.93GB**；全部 **505,983 文件 /
  108.83GB**。
- 实测：8 并发约 **22–30 文件/秒、5.46MB/s**；10.04 单任务 550 文件 12.5s
  （上传 378 / 跳过 172）。
- 当前：`--phase deliverables` 后台跑，日志
  `~/.livepipe_p2_backfill/run-deliverables.log`，state
  `~/.livepipe_p2_backfill/state.json`；失败 0（持续观察）。
- 完成后跑 `--phase all` 补帧图，再重跑 P4 `audit`（外置/本地 out 的覆盖面会提升，
  决定严格口径能否通过）。

🚀 下一步

- 等 deliverables 结束 → `p2_backfill.py run --phase all --yes` → 重跑
  `p4_cleanup.py audit --targets external_out,local_out --allow-metadata`。

---

## 2026-11-19 · OSS P4 影子期开始 + 清盘审计 — 进行中（未删除任何文件）

✅ 已完成的部分

- 新增 `scripts/p4_cleanup.py`：`shadow-start/status`（7 天影子期，状态在
  `~/.livepipe_p4_shadow.json`）、`audit`（覆盖审计）、`plan`、`apply`（影子期满 +
  0 不可删 + `--yes` 才执行）、`rollback-check`、`migrate-emoji`。
  审计规则：每个本地文件在保留根（OSS + 未选中本地根）找同路径同字节或同文件名
  同字节副本；视频额外允许同字节数；`--allow-metadata` 忽略批次元数据。
  `--policy inputs` 只要求 82 段引用的 OSS 录屏可读（结果靠重跑），默认
  `--policy results`。
- 影子期：**2026-11-19T02:06:02Z 开始，最早 2026-11-26T02:06:02Z 允许删除**。
- 审计（严格口径）：
  - **staging** `~/livepipe_batches`：41,856 文件 / 62.99GB；41,801 有副本，
    其余 55 个是批次元数据（2.88MB）⇒ `--allow-metadata` 下 **safe**。
  - **local_out** 仓库 `out/`：67,068 文件 / 13.29GB；**49,175 / 9.56GB 无副本**
    （10-01~03 remote 批次结果等）⇒ 严格口径未过。
  - **external_out** `/run/media/puyu/C0DE…/out`：474,223 文件 / 170.81GB；
    **448,095 / 136.09GB 无副本**（442k jpg、29 个 media mp4 等）⇒ 严格口径未过。
  - **inputs 口径**：82 段引用的 55 个唯一 OSS 对象 `ffprobe` 全部可读，
    `p15_repoint --verify` 22/22 ⇒ 允许「只保 OSS 录屏、结果靠重跑」。
- 为删外置 out 做的准备：
  - 表情表 4 套 / 18,200,771 B 从外置 out 复制到
    `/home/puyu/livepipe_assets/out/`；22 份配置 `base_dir` 改为该目录，备份
    `~/.livepipe_p4_config_backup/20261119T021318Z/`；`livepipe check` 22/22，
    douyin/wechat 表情表加载正常。
  - staging 里唯一没有别处副本的孤儿录屏
    `09-22…/videos/奇瑞集团/18：13-19：31.mp4`（2,012,027,943 B / 4,723.41s）已用
    P3 wrapper 写入 OSS `直播录屏/2026-9-22…/9.22/视频号/奇瑞集团/18-13-19-31.mp4`
    （334.8s，manifest 齐全）。
- 回滚检查：`rollback-check` 82 条本地视频路径中 **76 条不存在**（MyDisk 未挂载），
  当前无法把 `input.videos` 指回本地；配置应继续指向 OSS。删除本地 out 后，
  “回滚到本地”只能理解为用 OSS 录屏重跑。
- 审计报告：`out/p4/audit-{staging,local_out,external_out}.json`，摘要
  `livepipe/docs/p4-audit-summary.json`，副本 `~/.livepipe_p4_audit/`；日志
  `livepipe/docs/oss-p4-devlog.html`。
- 清单/备份持久化：inventory 22 manifests 复制到
  `livepipe/docs/oss_inventory/2026-11-17/`；P1.5 配置备份复制到
  `~/.livepipe_p4_audit/p1.5-backup-20261117T234234Z/`；删本地 out 后仍可做
  inputs 检查与回滚检查。
- **未删除任何本地文件**（影子期未满且 out 严格口径未过）；未提交 git。

🚀 下一步

- 影子期到 **2026-11-26T02:06:02Z** 后：
  - 先删 staging（审计已过）：
    `p4_cleanup.py apply --targets staging --allow-metadata --yes`。
  - 外置/本地 out 二选一：先把结果按 P2 同步到 OSS（严格口径），或显式选择
    `--policy inputs`（结果靠 OSS 录屏重跑）再删。
- MyDisk 挂回后重跑 `rollback-check`；确认原片是否还要保留。
- 本地剩余空间当前 115GB（已 ≥100GB 验收值）；删 out 后再复核。

决策

- 不直接删：P4 必须影子期满 + 覆盖审计通过 + `--yes`；严格口径默认保留结果。
- 表情表必须迁出外置 out，否则删盘后 livepipe 无表可用；已迁移并复验。
- 不在 MyDisk 缺失时删 out：本地回滚已不可用，先保留。

---

## 2026-11-19 · OSS P3 下载链路 — 完成（3.2GB 直写 · bypy 目录 · 失败零正式文件）

✅ 完成

- 新增 `scripts/oss_download.py`：
  - `live`：跑外部下载命令写 `<root>/<事件>/<日期>/<平台>/<账号>/<名>.mp4.part`，
    退出 0 后校验字节/时长、`os.replace` 改名并写 `.manifest.json`；命令失败不留
    正式文件（下载器违约写正式名也会删掉）；`--expect-bytes/--expect-duration`、
    `--clean-part`、`--resume`、`--clean-part-on-fail`。
  - `baidu`：`bypy download` 远端目录 → 本地暂存 → 逐视频复制成 OSS `.part` →
    同一套收尾；只处理视频扩展名，manifest 记 `source=baidu:<远端>` / `remote_rel`；
    `--dest-rel` 指定 OSS 落点，`--clean-staging` 成功后删暂存。
- 离线自检 `scripts/selftest_oss_download.py`：**22 项全过**（live 成功/幂等/失败/
  违约写正式名/字节闸门/dry-run；baidu 目录/非视频/来源/bypy 失败）。
- 真实验收（测试路径 `直播录屏/_p3_probe`，验收后已删除）：
  - **3.2GB 直写**：3,229,231,923 B 源文件经 `cp → {part}` 写入 OSS，收尾后
    3,229,231,923 B、ffprobe 12,507.435667s，manifest 齐全；墙钟 536s（约 6.0MB/s），
    中段/末段帧解码通过。goofys 期间 RSS 约 6.0GB（15GB 机器可用 4GB）。
  - **失败路径**：真实 OSS 上 `echo boom > {part}; exit 3` → EXIT 1、无正式文件；
    `--clean-part-on-fail` 连 `.part` 一起删。
  - **百度目录**：`/直播录屏/_p3_probe/2026-11-19`（1 个 2,948 B / 1.0s 测试视频）
    bypy 拉到暂存 → OSS `_p3_probe/11.19/抖音/test`，字节/时长匹配、manifest 记录
    baidu 来源；非视频跳过，暂存已清理。
- 文档：新增 `livepipe/docs/oss-p3-devlog.html`；`livepipe/README.md` 增 P3 小节；
  `livepipe/tools/oss_ingest/README.md` 指向 wrapper；`oss-migration.html` 横幅与
  计划 P3 行标完成；`.wolf` 同步。
- 本机没有 dypy：3.2GB 直写用 `cp` 作为下载器替身，验证「下载器写 OSS `.part` →
  收尾」这一段；dypy 参数只替换 `--cmd`。
- 未接生产下载队列、未提交 git；3.2GB/百度测试对象均已清理。

🚀 下一步

- P4 清盘与回滚：影子运行 7 天；确认 OSS 可读后删除外置 out 161GB / 暂存 59GB /
  本地 out 13GB；MyDisk 原片在 OSS 校验后清理。
- 生产启用 P3 前先在目标机器做 256MB 级直写压测（goofys 大文件内存占用高）；
  小内存机器走「本地暂存 → 后台上传」兜底。
- goofys systemd 托管仍未做。

决策

- 结果/录屏收尾统一复用 `finalize_part`（字节 + 时长闸门、manifest）；wrapper 只负责
  跑下载器、bypy 暂存和失败清理。
- `live` 失败默认保留 `.part` 以便续传，但绝不生成正式文件；需要干净现场加
  `--clean-part-on-fail`。

---

## 2026-11-18 · OSS P2 结果链路 — 完成（664/664 · 122.94MB · SHA256 全绿）

✅ 完成

- 新增 `scripts/oss_sync.py`：本地结果目录 → OSS 增量同步。size 相同跳过，
  `--sha256` 再比内容；跳过符号链接 / 隐藏文件 / `*.part|*.lock|*.failed`；
  直接写正式名（goofys rename 对含 `+` 的目录会 404）、失败重试 3 次、默认 4 并发；
  `--dry-run` / `--verify-only`（严格核对多出文件）/ `--report`，结尾
  `OSS_SYNC_RESULT {json}` 供批处理解析。
- 离线自检 `scripts/selftest_oss_sync.py`：**33 项全过**（增量/重跑跳过/哈希/软链/
  隐藏/dry-run/verify/extras/`+` 目录）。
- 接线 `~/脚本/ssh/batch_local.py` / `batch_remote.py`：「收 / 回传」核对通过后
  自动同步；配置 `oss_results_root` + `oss_sync`，任务级 `oss_result`（相对 root，
  不写则按 `local_out` 相对 `local_out_root` 镜像）；新增 `--oss-only` 只补同步；
  state 增 `oss_state / oss_dst / oss_files / oss_bytes / oss_uploaded /
  oss_skipped / oss_at`。OSS 失败不回退 `transfer_failed`。
  自检：batch_local **57 项**、batch_remote **45 项**全过。
- 真实任务验收：`10-04_全球车的中国路_视频号`（664 文件 / 122,937,029 B）→
  `直播产生结果/2026-0930-1006《全球车的中国路》/10.04/视频号`：
  - 续传上传 442 个 / 90.01MB，跳过 222，失败 0，83.7s（1.07 MB/s）。
  - `--verify-only` 664/664、0 不一致、0 多出（34.3s）；
    `--sha256` 664/664 全一致（111.2s，两端各读 122.94MB）。
  - 重跑同步 **上传 0 / 跳过 664**；`batch_local --apply --oss-only` rc=0，
    state 记 `oss_state=synced`、`oss_files=664`、`oss_uploaded=0`。
- 踩坑并修复（buglog）：goofys rename 在 `+1八重樱-3/` 这类含 `+` 目录上 404
  （S3 NoSuchKey）；结果文件改成直接写正式名，自检加 `+` 目录用例。
- 文档：新增 `livepipe/docs/oss-p2-devlog.html`；README 增 P2 小节；
  `oss-migration.html` 横幅与计划 P2 行标完成；`~/脚本/ssh/README.md` 增 P2 用法；
  示例配置（`batch_jobs*.example.json`）补 `oss_results_root` / `oss_sync` /
  `oss_result`。
- 测试配置 `~/脚本/ssh/batch_jobs.p2test.json` + `batch_jobs.p2test.state.json`
  （只含 10-04 视频号任务）留在本机；未提交 git。

🚀 下一步

- P3 下载链路：dypy 直播直写 OSS 的 `<名>.part`（close 后校验 + rename + manifest）；
  百度来源 bypy → 本地暂存 → goofys 复制。
- 存量其它任务的结果可按需 `--apply --oss-only` 补同步（先给任务配 `oss_result`；
  不写会按本地相对路径镜像）。
- 大任务（对象数 >1 万）再评估「交付树按账号打包」；goofys 建议 systemd 托管仍未做。

决策

- 结果文件直接写正式名、不用 `.part + rename`：文件小、失败可重跑；也绕开 goofys
  对含 `+` 目录的 rename 404。P1 的 3GB 大文件上传仍保留 `.part → rename`。
- OSS 同步失败不改变任务的 `transferred` 状态，用 `--oss-only` 单独补；避免重做回传。

---

## 2026-11-18 · OSS P1.5 切读 — 完成（22/22 check · 82/82 段 · 2 样片 rc=0）

✅ 完成

- goofys 挂载先恢复：`~/oss-bucket` 只剩 ENOTCONN 挂载点、无进程；`fusermount3 -u` 后
  `setsid nohup ~/goofys -f -o allow_other --endpoint http://oss-cn-beijing.aliyuncs.com
  --subdomain static-cantotimes-com ~/oss-bucket` 重挂（凭据 `~/.aws/credentials`）。
- **22 份配置 / 82 段 `input.videos` 全部指 OSS**：55 整段读旧根
  `~/oss-bucket/直播data/录屏/…`（175,302,204,124 B）+ 27 裁尾读新契约根
  `~/oss-bucket/奇瑞汽车/直播看管/直播录屏/<事件>/_live/…`（40,069,546,585 B），
  合计 215,371,750,709 B；`base_dir` 仍指本地，未动。
- 工具 `scripts/p15_repoint.py`：整段取 manifest `segments[].oss`、裁尾取
  `p1-upload-list.csv` 的 `src_abs→dst`，只替换配置文本里的 JSON 字符串；
  `--verify` 22/22、82/82 目标在位且字节相符；`--apply` 幂等（重跑显示“全部已是 OSS，
  无文件改动”）。切读前备份 `out/p1.5/backup/20261117T234234Z/`，报告
  `out/p1.5/repoint-report.json`。
- `livepipe check` **22/22 EXIT 0**。
- 样片实跑（临时配置：`base_dir` 指 `~/livepipe_batches/10-04_全球车的中国路_抖音` 的表情表，
  `output.dir` 指 `/tmp/p15_sample`；正式配置未动）：
  - A `10-02_全球车的中国路_抖音` seg3（裁尾 / 新契约根，97,153,646 B / 164.7s）：
    59 帧 / 639 rows / **48 条唯一评论** / 225s，rc=0。
  - B `09-30_全球车的中国路_抖音` seg1（整段 / 旧根，3,225,543,521 B / 6192.1s）
    中段 t=3000–3060s（验证 goofys 远端 seek）：29 帧 / 359 rows / **26 条唯一评论** / 121s，rc=0。
- 文档：新增 `livepipe/docs/oss-p15-devlog.html`；`livepipe/README.md` 增 P1.5 小节；
  `oss-migration.html` 横幅改 P1.5 完成、实施计划 P1.5 行标“已完成”。
- ⚠ 外置 out 盘 `/run/media/puyu/C0DE6A4BDE6A39B4` 本次**未挂载**：样片输出只在
  `/tmp/p15_sample/`；正式跑前需把盘挂回（配置仍指该根）。

🚀 下一步

- 挂回外置 out 盘后按需重跑下游；P2 结果链路（`oss_sync.py` + `batch_local`/`batch_remote`
  回传）是下一阶段。
- goofys 建议改 systemd 托管（`scripts/bootstrap_oss_env.sh --systemd`），避免终端断开掉挂载。

决策

- 切读只改 `input.videos` 路径字符串，几何/panels/裁切参数一律不动；整段留旧根、裁尾读新契约根。
- 样片用临时配置 + `/tmp` 输出，不污染正式产出目录；`base_dir` 保持本地（按 P1.5 计划）。

---

## 2026-11-18 · OSS P1 补传 — 完成（29/29 · 46,520,729,506 B）

✅ 完成

- 29 个对象全部在目标且字节相符：24 个本次上传（45,689,353,799 B）+ 5 个试传已在位
  （831,375,707 B）；失败 0、冲突 0、无残留 `.part`。
- 实测：墙钟 7,235s（2.01h），按本次写入聚合 6.31 MB/s（单文件峰值 4.13、最慢 0.60）；
  3 并发，单文件耗时之和 21,221s。goofys 并发不提速，瓶颈在挂载/带宽。
- 落点：新契约根 `奇瑞汽车/直播看管/直播录屏` 27 文件 / 40,069,546,585 B；旧存根补 2 个整段
  6,451,182,921 B（`直播data/录屏/2026-0930-1006…/9.30/抖音`）。
- 验收：`--verify-only` 29/29（1.2s）；`build_oss_inventory.py --verify-uploads` 自动切
  P1（22 配置 / 82 段 / 55 整段 / 27 裁尾 / 0 缺失，EXIT 0），27/27 副本字节一致；
  `audit` 22 配置 PASS；SHA256 4 个样本（2 整段 + 2 裁尾，7,452,754,609 B）两端全部 MATCH。
- 0930 事件根索引 50 → 52 个可用视频（补进 2 整段），总计 82。
- 文档：新增 `livepipe/docs/oss-p1-devlog.html`；`oss-migration.html` 横幅改 P1 完成、
  实施计划拆出 P1.5 切读；`livepipe/README.md` 增 P1 小节；`oss-inventory-summary.json` 已是 phase=p1。
- 收尾修两个脚本问题：`--verify-only` 不再覆盖上传报告（默认写
  `p1-verify-report.json`）；上传报告按 29 行 verify 快照 + 上传日志重建
  （`reconstructed=true`、`started_at=null`）。
- **只补传，不改配置**：22 份 `input.videos` 仍指本地；未删本地、未提交 git。

🚀 下一步

- P1.5 切读：22 份配置 `input.videos` 指到 OSS 绝对路径（`base_dir` 保持本地），
  `livepipe check` 22/22，再抽 2 个任务实跑；确认后再做 P2 结果链路。
- 切读前保持 OSS 与本地双份；MyDisk/外置盘删除要等 P4 影子期结束。

决策

- 上传与切读分开：P1 只写 OSS，切读失败可立即把配置指回本地，不动已传对象。
- 仍不臆造 `source_oss`；裁尾副本按 `_live` 文件名直传新契约根。

---

## 2026-11-17 · OSS P0 映射清单 — 完成（53 + 2 / 27）

✅ 完成

- 工具 `livepipe.tools.oss_ingest`：默认根候选自动探测（真实根 `~/oss-bucket/直播data/录屏`）、
  `manifest --out-dir`、裁尾 `trim_s` 时长回退、`ingest` 默认要求有效时长（`--allow-no-duration`
  逃生）、`sync` 默认只比大小、`missing`/`local_missing` 共用同一口径、索引惰性 ffprobe。
  310 项离线自检全绿；22 配置默认根 audit 5.1s（优化前约 4–5 分钟）。
- 新增 `scripts/build_oss_inventory.py`：两批事件根（0930 50 个可用视频 / 10.19 30 个）各建一次索引，
  输出 22 份 manifest + `index.json` / `audit.json` / `summary.json` / `p1-upload-list.csv`。
  `generated_at` 固定，两遍重跑 diff 为空（热缓存约 5s）。两个根相同/互相包含直接退出 2。
- 评审闭环（评审 + 使用逻辑各出独立报告）：0 字节临时名不再计入索引（80 个可用，不是 81）；
  `verify-media` 无时长改 UNKNOWN + EXIT 1；显式根不存在直接报错；兜底保留 `segment_names`；
  CSV 增 config/seg/note/src_abs 列；P0/P1 两套基线自动切换，补传后 55/0 仍 EXIT 0；
  `--verify-uploads` 按 dst 核 27 个副本；`audit` 的 PASS 文案明确“裁尾副本不在本次检查内”。
- 实测：22 配置 / 82 段 / **53 整段 + 27 裁尾 + 2 缺失** / 本地缺失 0；整段已映射
  168,851,021,203 B；裁尾 40,069,546,585 B（37.32 GiB）；缺失 6,451,182,921 B。
- 缺口仅 `09-30_全球车的中国路_抖音.json` seg1 `9：29-11：12.mp4`（3,225,543,521 B /
  6192.073s）与 seg3 `13：07-14：53.mp4`（3,225,639,400 B / 6397.963s）；OSS `9.30/抖音/`
  只有 1 个 3,225,559,956 B 文件，确认没传过。
- 文档数字修正：55 → 53+2；0930 批次实际 51 文件 / 131,558,840,703 B（122.52 GiB），
  不是 54 文件 / 133.4GB（51 个里含 1 个 0 字节百度云临时名，可用索引计 50）；27 个裁尾 40.07GB，不是 37.9GB。
- 交付物在 `out/oss_inventory/2026-11-17/`（gitignore）；docs 摘要
  `livepipe/docs/oss-inventory-summary.json`；开发日志 `livepipe/docs/oss-p0-devlog.html`。

🚀 下一步

- P1 补传 29 个对象、46,520,729,506 B（2 整段 + 27 裁尾）；整段用 `ingest` 的字节+时长双闸门；
  补传后同一命令自动切 P1 基线（55 / 27 / 0，EXIT 0），再加 `--verify-uploads` 核 27 个副本落盘。
- **上传需用户授权**；P0 只读盘点，未向 OSS 写任何文件。

决策

- `missing` 采用严格口径（非裁尾且 OSS 无对象，含本地不可用段），素材盘没挂载时不会误判 PASS；
  只读计算/审核节点用 `--report-only`。
- 裁尾不臆造 `source_oss`（按时间区间 18/27 可唯一回溯），P1 直接上传裁尾副本。
- `sync` 默认只比大小，`--sha256` 显式开启（goofys 冷读 6.6MB/s，默认算哈希不现实）。

---

## 2026-11-17 · 直播看管 OSS 化技术方案 — 完成

✅ 方案落在 `livepipe/docs/oss-migration.html`（独立 HTML，浏览器直接打开）。

- OSS 存量：0930 批次 54 文件/133.4GB、1019 批次 30 文件/86.8GB；`直播产生结果` 为空。
- 22 份配置 82 段引用：55 段按字节大小 1:1 命中 OSS；27 段裁尾引用待补传 37.9GB（21 个
  `_live` 29GB + 6 个 1019 media 8.9GB）。
- 本机实测：goofys 写 9.4MB/s、冷读 6.6MB/s、小文件 10–13 个/s；软链/硬链不可用、
  mtime 被忽略、statvfs 假 1PB；OSS SDK multipart list/abort 403。
- 推荐：dypy 直播直写 OSS（`.part` → rename；128MB rename 0.15s）；计算读 goofys，结果先落
  本地 NVMe、任务完成按 size+sha256 同步 OSS；不要直接把 `.撞库合并` 软链树搬上 OSS。
- 环境基线：任意机器需 bypy 1.8.9（百度 `/直播录屏`）+ goofys 0.24.0；新机器搬
  `~/.bypy/bypy.json` 与 `~/.aws/credentials`（0600），健康检查通过再接队列。

🚀 下一步：P0 生成 22 份配置的 OSS 映射清单（size+duration，55 整段已验证；27 裁尾先补传），
再按方案 P1–P4 切读、切写、清盘。

---

## 2026-11-17 · `out/` 迁到外置 NTFS 盘 — 完成

✅ `out/`（161 GiB；474,223 个文件 / 170,809,653,134 字节）→
`/run/media/puyu/C0DE6A4BDE6A39B4/out/`。`rsync -aH` 退出码 0；文件数、字节数与源一致，
40 个跨目录抽样 MD5 全同；1,113 个绝对符号链接已改指新根、无悬空。源目录已删除
（本地释放约 161G）。

- 仓库 JSON：82 份根配置 + 6 份 `livepipe/*.json` 里所有 `"out/…"` 与旧绝对路径
  `/home/puyu/github/LiveMonitorMockScale/out` 全部改为新根；`mock_scale` 的
  baseline / body_measure / expand 配置与 `mock_scale/out/*/report.json` 同步。
- 依赖默认表情表路径的配置统一加 `"base_dir": "/run/media/puyu/C0DE6A4BDE6A39B4"`
  （`emoji.tables.*.path` 缺省值仍是相对 `out/…`，靠 base_dir 落到新盘）。用
  `livepipe.config.load(check_videos=False)` 验过 77 份可加载配置：`output.dir` 与所有
  emoji 路径都在新根；另有 11 份加载失败，与改动前逐字一致（历史遗留的空 `input` / 示例配置）。
- `out/` 内部：527 份 json/jsonl、63 份 md/yaml（含 `dataset_*/data.yaml`）里的旧绝对路径
  已改写；1,113 个符号链接重定向；`.log` 作为历史记录未动。
- `livepipe/examples/*.json` 是模板，保留相对 `out/…` 未改。

🚀 下一步：继续 10-25 探秘局全量抽行（配置路径已指向外置盘；seg1 可 `--resume`）。

---

## 2026-11-16 · 奇瑞「探秘局」2026-10-25（视频号）— 新建配置 — 完成

✅ 新配置（仓库根）：`10-25奇瑞探秘局_视频号.json`；素材
`/run/media/puyu/MyDisk/10.25/下午/` 下**只有视频号下午场**（2 段，590×1280）

| 段 | 素材 | 实际直播 |
|---|---|---|
| seg1 | `14： 01-15： 17.mp4` | 76.5min，直播到文件末尾 |
| seg2 | `15： 17-15： 55.mp4` | 直播在 **1781.97s** 结束（15:46:42）⇒ 裁到 1781.8s |

- 裁后副本（stream copy，末帧仍有 `聊一聊`，输入栏模板 NCC=0.998）：
  `out/2026-10-25奇瑞探秘局/media/wx_pm_15-17-15-55_live.mp4`。
  判定只认底部输入栏（0.05s 采样：1781.95s=0.9999、1782.00s=0.438），「直播已结束」字样不可靠。
- 录屏 `creation_time` = 2026-10-25 15:17 / 15:55（+08，与文件名结束时间一致）；状态栏
  起帧 2:01、段界 3:17、结束页 3:55，确认本批是 10-25。
- 几何（2026-11-16，多帧 RapidOCR）：视频号 **41.5** / y_phase 33 / x_text **64** /
  badge_span 46 / wrap_indent + wrap_gap_ratio **0.75** / rect **[0,660,590,1190]** /
  x_ignore_from 350 / account_key=**username**（昵称基本不打码）。
- 评论区顶部反诈卡（`通知` + 6 行）与主题卡（`主题` + `CTCC中国汽车场地职业联赛`）沿用
  10-24 的 overlay_cards + skip_phrases；切片发现 `赞了直播` 的 OCR 变体 `资了直描`
  ⇒ overlay_cards 补 `直描`（同 10-19 视频号）。
- 校验：`livepipe check` ✔；2 份 panels.json + panels.resolved.json 已写出；`--dry-run` ✔。
  两个 60s 真实切片：seg1 **15 条** / seg2 **12 条**唯一评论，昵称/正文可读，
  `[强]×3` 表情识别正常；seg2 的 `资了直描` 已被过滤。
- seg1 已有部分 `rows.jsonl`（1811 行，覆盖前 1126.8s ≈ 18.8min；一次误触发的全量
  parse 留下的，几何与正式配置一致）⇒ 正式跑 `--resume` 可直接接着用。
- 已知残留（短切片）：seg1 有 2 条用户名未识别、`住妮`（佳妮的 OCR 误读）、`）沫沫`
  前缀；全量多帧见证后应改善。
- 撞库名单：`撞库名单/可用名单/10-25/10-25_探秘局_昵称.txt`（20 个昵称，需 `--platform 视频号`）。

🚀 下一步：全量抽行（seg1 剩约 58min、seg2 约 30min）
`$P -m livepipe run 10-25奇瑞探秘局_视频号.json --only panels,parse`；
再 `NLP_API_KEY=... run … --only filter,qualify,export,collect`；
交付后撞 `撞库名单/可用名单/10-25/10-25_探秘局_昵称.txt`。

---

## 2026-11-16 · 奇瑞全球创新大会 2026-10-19（6 账号 × 6 份配置）— 新建配置 — 完成

✅ 外置盘 `/run/media/puyu/MyDisk/10.19/` 下 **3 个抖音账号 + 3 个视频号账号**，
每个账号一整条 9:00–17:00 的直播；已按账号建 **6 份配置**（仓库根）：

| 配置 | 段数 | 版式 | 输出 |
|---|---|---|---|
| `10-19奇瑞创新大会_抖音QQ汽车.json` | 6 | 43.8 / x_text 86 / rect [0,600,590,1196] | `out/2026-10-19奇瑞全球创新大会/抖音/QQ汽车` |
| `10-19奇瑞创新大会_抖音奇瑞汽车.json` | 5 | 同上（12:05–12:26 缺 21min 素材） | `…/抖音/奇瑞汽车` |
| `10-19奇瑞创新大会_抖音风云新能源.json` | 6 | **大字号** 58.5 / x_text 98 / rect [0,635,590,1196] | `…/抖音/风云新能源` |
| `10-19奇瑞创新大会_视频号奇瑞QQ.json` | 5 | 41.5 / x_text 64 / rect [0,650,590,1190] | `…/视频号/奇瑞QQ` |
| `10-19奇瑞创新大会_视频号奇瑞汽车.json` | 3 | 41.5（**592×1280**）/ rect [0,650,592,1190] | `…/视频号/奇瑞汽车` |
| `10-19奇瑞创新大会_视频号风云新能源汽车.json` | 5 | 41.5 / x_text 64 / rect [0,650,590,1190] | `…/视频号/风云新能源汽车` |

- 六条流实测**都在 ≈17:00 结束直播**（之后是「直播已结束」推荐页，录制到 17:04–19:40 不等）。
  逐条用各自底部输入栏模板做 NCC 二分 + 顺序解码定位最后直播帧，`ffmpeg -c copy` 裁到
  `out/2026-10-19奇瑞全球创新大会/media/`（6 个副本，末帧仍有输入栏）。
- 认人口径：所有配置 `account_key=nickname_level_if_masked`（风云新能源几乎全打码，
  其余账号也偶有 `润**` / `大**` 这类）。
- 过滤：反诈卡 / 三方管理工具卡 / 主题卡 / 公众号卡 / 底部广告卡 / 系统通知都按
  实测短语写进 `overlay_cards` + `skip_phrases`；`抖音风云新能源` 补 `^康家车米了$`、
  `视频号奇瑞QQ` 补 `直描`（`赞了直播` 的 OCR 变体）。
- 校验：6 份 `check` 全 ✔；panels / panels.resolved 已写出；6 份 `--dry-run` 全 ✔。
  每账号 60s 真实切片：QQ汽车 5 条、奇瑞汽车(抖音) 13 条、风云新能源(抖音) 11 条、
  奇瑞QQ 3 条、奇瑞汽车(视频号) 11 条、风云新能源汽车 2 条，用户名/正文可读，
  表情能识别；已知残留：大字号版偶有匿名行、通知 OCR 变体（`货了直描` 等，已补词）。
- ⚠ 老配置 `1019-1024奇瑞创新大会_抖音.json` 的素材目录
  `/run/media/puyu/MyDisk/2026-1019-1024奇瑞创新大会/` **已不在盘上**；10-19 是同一天
  更完整的 8h 录制，归档到新场次目录，不复用旧 `out/2026-1019-1024奇瑞创新大会/` 树。

🚀 下一步：全量抽行（6 条流各约 8h，合计约 **48h 录屏**；单机 OCR 约 1.2–1.5 倍墙钟，
建议分 6 个队列并行）：
`$P -m livepipe run 10-19奇瑞创新大会_<平台><账号>.json --only panels,parse`；
再 `NLP_API_KEY=... --only filter,qualify,export,collect`。撞库名单暂无 10-19
（`撞库名单/可用名单/` 只有 10-20 / 10-24 / 10-25）。

---

## 2026-11-16 · 奇瑞「探秘局」2026-10-24（视频号）— 新建配置 — 完成

✅ 新配置（仓库根）：`10-24奇瑞探秘局_视频号.json`；素材
`/run/media/puyu/MyDisk/奇瑞探秘局/` 下**只有视频号**（4 段，590×1280，第 2/4 段是结束页）

| 段 | 素材 | 实际直播 |
|---|---|---|
| seg1 | `上午/8： 43-9： 40.mp4` | 57.7min，直播到文件末尾 |
| seg2 | `上午/9： 41-10： 25.mp4` | 直播在 **1432.10s** 结束 ⇒ 裁到 1431.9s |
| seg3 | `下午/12： 56-14： 10.mp4` | 74.9min，直播到文件末尾 |
| seg4 | `下午/14： 10-14： 39.mp4` | 直播在 **1332.44s** 结束 ⇒ 裁到 1332.2s |

- 裁后副本（stream copy，末帧仍有 `聊一聊`）：
  `out/2026-10-24奇瑞探秘局/media/wx_am_09-41-10-25_live.mp4` /
  `wx_pm_14-10-14-39_live.mp4`。判定只认底部输入栏（模板 NCC），「直播已结束」字样出现时机不稳定。
- 录屏 `creation_time` = 2026-10-24 09:41 / 10:26 / 14:10 / 14:40（+08），确认本批是 10-24。
- 几何（2026-11-16，四段各 3 帧 + 逐帧扫描）：视频号 **41.5** / y_phase 33 / x_text **64** /
  badge_span 46 / wrap_indent + wrap_gap_ratio **0.75**（续行 25~26.5px=0.60~0.64）/
  rect **[0,660,590,1190]**（视频下沿≈644、输入栏上沿 1203）/ x_ignore_from 350 /
  account_key=**username**（昵称基本不打码）。
- 评论区顶部两类常驻卡已处理：反诈卡（`通知` + 「欢迎来到直播间…」6 行）与主题卡
  （`主题` + `CTCC中国汽车场地职业联赛`）⇒ overlay_cards 逐行短语 + skip_phrases 整行丢。
- 校验：`livepipe check` ✔；`--only panels` 写出 4 份 panels.json + panels.resolved.json；
  `--dry-run` ✔。短切片：seg1 t=1500 60s → **14 条唯一评论 / 0 用户名未识别**（1 条折行并对）；
  seg3 t=240 60s（反诈卡+主题卡同屏）→ 只剩 1 条真评论，卡片全过滤。
- 已知残留：短切片里少数昵称带药丸前缀（`+6多才多艺` / `+5小软` 等）尚未归一，
  属 badge_alias 的「全量多帧才有干净见证」老现象。

🚀 下一步：全量抽行（约 2.3h 录屏）
`$P -m livepipe run 10-24奇瑞探秘局_视频号.json --only panels,parse`；
再 `NLP_API_KEY=... run … --only filter,qualify,export,collect`；
交付后撞 `撞库名单/可用名单/10-24/10-24_探秘局_昵称.txt`（19 个昵称，需 `--platform 视频号`）。

---

## 2026-11-16 · 2026-0930-1006《全球车的中国路》16 份配置 — 完成

✅ 按外置盘素材 `/run/media/puyu/MyDisk/2026-0930-1006《全球车的中国路》/` 生成
16 份配置（6 天 × 平台），全部 `livepipe check` 通过，panels.json 已落盘

| 日期 | 抖音 | 快手 | 视频号 | 段数 |
|---|---|---|---|---|
| 09-30 | ✅ | ✅ | ✅ | 4 / 2 / 4 |
| 10-01 | ✅ | ✅ | ✅ | 5 / 3 / 4 |
| 10-02 | ✅ | ✅ | ✅ | 5 / 2 / 6 |
| 10-03 | ✅ | ✅ | ✅ | 3 / 2 / 4 |
| 10-04 | ✅ | — | ✅ | 1 / 1 |
| 10-06 | ✅ | — | ✅ | 3 / 3 |

- 配置：`MM-DD_全球车的中国路_{抖音,快手,视频号}.json` → `out/2026-0930-1006全球车的中国路/<MM-DD>/<平台>/`
- 共 **52 段**；其中 **21 段**原片含「直播已结束」推荐流，已裁到直播区间（stream copy）
  存 `_live/`（外置盘，约 29GB），配置直接引用裁后副本。
- ⚠ **平台按画面内容认，不信目录名**：9.30「快手/」里是视频号录屏、「视频号/」里是快手录屏；
  10.02「快手/1」里是抖音录屏（「快手/2」才是快手）。配置已按实际平台重新归档。
- 几何（2026-11-16 实测）：抖音 44.0 / x_text 86；快手 42.0 / x_text 81 / wrap_indent；
  视频号 41.0 / x_text 66 / badge_span 44 / x_ignore_from 440；rect 逐段量（视频号 y1
  抬到场次横幅底 +3，避免「第N站（…）：…」横幅被当评论）。
- 120s 试跑：抖音 57 条、快手 37 条、视频号 15 条唯一评论，用户名/正文正常（视频号
  横幅与「若主播7天」订阅卡不再入评论）。

🚀 下一步：按天跑 `--only panels,parse`（OCR 约 50h 素材），再 `filter,qualify,export,collect`；
   之后可与 `撞库名单/可用名单/09-30…10-06` 的 `全球车的中国路` 名单撞库。

---
## 2026-11-16 · 腾讯文档《直播评论整理_v1(1)》填撞库结果 — 完成

✅ 在用户登录的独立 Chrome（`.chrome-claude`，调试端口 9333）里，把 `out/` 的撞库与交付结果
填进腾讯文档「视频统计」页签的 **5 列**（20 行 × 5 列 = 100 格）

| 列（Excel） | 口径 | 来源 |
|---|---|---|
| I 总用户名单数 | 总用户 / 直播账号数 | `matches.json` → `accounts_count` |
| J 撞库命中用户 | 命中人数 | `matches.json` → `matched_count` |
| K 撞库命中用户张数 | 撞库截图 | `撞库结果/标记图/<平台>/` 图片数 |
| M 可用名单数 | `交付清单.txt` 的「用户 X 人」 | `out/<场次>/<平台>/交付清单.txt` |
| P 3张用户评论总数(截图总) | `交付清单.txt` 的「真图 X 张」 | 同上 |

| 场次 | 行号 | 命中（抖音/视频号/快手） |
|---|---|---|
| 09-10 | 86–88 | 上午/下午：16·19 / 15·16 / 14·19 |
| 09-12 | 91–93 | 19 / 13 / 13 |
| 09-13 | 94–96 | 18 / 14 / 14 |
| 09-14 | 97–99 | 19 / 28 / 13 |
| 09-15 | 100–102 | 上午/下午：18·18 / 15·15 / 14·14 |
| 09-16 | 103–104 | 19 / 14 |
| 10-31 | 184–186 | 20 / 16 / 16 |

- 两场（09-10、09-15）的 J/K 沿用表内 `上午：X下午：Y` 写法；I 用整批账号数。
- 另补 **09-05~09-09**（71–85 行）的 **M 可用名单数 / P 截图总**（15 行 × 2 列 = 30 格），
  同样取各批 `<平台>/交付清单.txt` 的「用户 X 人」「真图 X 张」。
- **09-22 QQ3 多账号**（123–131 行，9 个账号）按渠道拆分填写：渠道1=刘通用、渠道2=刘真人、渠道3=张真人；
  J/K 直接写真实姓名（`刘通用：x，刘真人：y，张真人：z`），不用「渠道N」代称。命中人数按 `matches.json` 的 `segs`（`segN__账号`）归到账号；
  张数把每条命中的 `标记图` 图片数按各段评论数占比分摊到账号（视频盘 `/root/livepipe_batches` 已不可访问，
  无法逐账号重跑补图）。I 为该账号 `seg*/comments.jsonl` 去重账号数，M/P 取各账号目录的 `交付清单.txt`。
- 口径校验：09-08 行的 I 与 `accounts_count`（1747）、K 与 `标记图` 张数（528/240）逐格对得上；
  09-05 风云A9L 的 M（96/39）与 `交付清单.txt` 一致。
- 校验：20 行 × 5 列全部逐格回读一致；重开新标签页从服务端加载仍一致。
- 未填：09-05、09-06 视频号（源名单没有视频号列）；09-05 风云A9L 撞库列（名单人名在直播里被打码，0 命中）；
  09-22 QQ3 与 10.19–10.24 创新大会（本地撞库按「渠道/主名单」跑，与表里按账号拆的行对不上）。
- 注意：腾讯文档页面在网络波动时会切「只能查看」并卡住，填写需逐格校验、分小批跑。

🚀 下一步：如要给 09-22 / 创新大会按账号拆行填写，需要确认每行对应哪份名单。

---

## 2026-11-16 · 2026-09-05 起交付目录补材料 — 完成

✅ 用 `/home/puyu/脚本/write_delivery_materials.py` 给 `out/` 下 2026-09-05 及之后的交付树补 `交付清单.txt` + `summary.json`

| 指标 | 数值 |
|---|---|
| 场次 | 15（含 10.31、09-22 多账号、1019-1024） |
| 交付目录（`all_users_正面_3张以上`） | 47 |
| 用户 / 截图 | 3418 人 / 43813 张 |
| 真图 / 假图 | 43813 / 0 |
| 成功 / 失败 | 47 / 0 |

- 材料**两处都放**：`all_users_正面_3张以上/` 内一份，平台/账号目录根下再一份（用户 2026-11-16 指出
  `<场次>/<平台>/交付清单.txt` 缺失，材料应能从平台目录直接看到）。
- 09-22 多账号布局按账号目录单独编号：`<场次>_<平台>_<账号>`；快手沿用历史 `platform=""`。
- 完整性提示 2 处：09-22 抖音/QQ汽车 manifest 有 `_蜜桃气泡-9`、1019-1024 抖音 manifest 有
  `_秋水伊人_-4`，磁盘上都不存在。
- 排除项：`seg*/…all_users_正面_1张以上`（分片中间产物）、`.撞库合并/`（隐藏虚拟交付树）。
- 09-05风云/抖音 平台根下今天 10:28 误跑出的 0 用户材料已被正确版本覆盖。

🚀 下一步：如需要，也可给 `.撞库合并/` 合并树单独出材料。

---

## 2026-11-16 · 1019-1024 奇瑞创新大会（抖音）撞库 — 完成

✅ `match` 跑完，名单 `10-20_主名单`（抖音 144 人）× 本批直播全量 5799 账号

| 指标 | 数值 |
|---|---|
| 名单 / 命中 / 未命中 | 144 / 70 / 74 |
| 命中账号（仅直播） | 109（52） |
| 命中标记图 | 121 账号目录 · 1076 张 |
| 未命中最近图 | 81 账号目录 · 114 张 |
| 现补图 | 命中 64 个 + 未命中最近 78 个，0 失败 |
| HTML | 1 个，180 条 jpg 链接 0 坏链 |

- 名单选型依据：仓库里没有单独的「创新大会」名单；本批录屏的
  `ffprobe creation_time = 2026-10-20T01:35:23Z`（= 10-20 09:35 +08:00），
  与渠道表 `10.20` sheet 的「多渠道账号清单」对应 ⇒ 用 `10-20_主名单`。
- 视频前缀重映射：`/root/livepipe_batches/1019-1024奇瑞创新大会_抖音/videos`
  → `/run/media/puyu/MyDisk/2026-1019-1024奇瑞创新大会`，否则 52 个仅直播命中补不了图。
- 视频号 / 微博两列名单跳过（本批只有抖音素材）。
- 结果 → `out/2026-1019-1024奇瑞创新大会/撞库结果/10-20_主名单/`。

🚀 下一步：如还要撞 10-24/10-25「探秘局」名单（仅昵称一列），需 `--platform 抖音`。

---

## 2026-11-16 · 全量撞库（21 场 × 32 块名单中可跑的）— 完成

✅ 21 个场次块全部 `match` 跑完（rc=0），命中/未命中/标记图/未命中最近图/HTML/xlsx 齐全

| 指标 | 数值 |
|---|---|
| 名单人数合计 | 1546 |
| 命中人数 | 1139 |
| 未命中人数 | 407 |
| 命中账号（跨平台去重前） | 1632 |
| 命中标记图 | 1138 账号目录 · 22784 张 |
| 未命中最近账号图 | 359 账号目录 · 2923 张 |
| HTML 交付清单 | 74 个，1952 条 jpg 链接 **0 坏链** |

- 本轮修复：09-05风云A9L / 09-06 / 09-07 的补图全部失败，原因是重跑脚本的
  `--video-map` 只带了 `/root/livepipe_batches/...`，而这三批的 `summary.json` 记录的是
  历史前缀 `/home/puyu/work/data/...`。补上 `--video-map /home/puyu/work/data=/run/media/puyu/MyDisk`
  后重跑 5 个场次，补图缺口清零（09-05风云A9L 未命中最近图 12 → 20 个目录 / 130 张）。
- 09-22 多账号批次（`平台/子账号/...` 布局）先用 `.撞库合并/` 虚拟交付树（子账号目录加
  `子账号__` 前缀、段名 `segN__子账号`、合并 manifest 与 judgments）才能被 `discover_platforms`
  发现；三份名单命中 17 / 89 / 305 人，命中图 17/89/305 账号、785/836/3533 张。
  首轮 HTML 场次名取成了目录名「撞库合并」，用 `--title 2026-09-22… --date 2026-09-22`
  `--no-images` 重生成，旧 `20261116_撞库合并_*.html` 已删。
- **唯一剩余缺口**：09-05猎鹰上午还有 11 个「仅直播命中」+ 4 个「未命中最近账号」没有图，
  素材在未挂载的 `/run/media/puyu/A1B7-2982`，挂盘后重跑该场即可。
- 32 块名单里另有 11 块（09-30 ~ 10-25 全球车的中国路 / 主名单 / 探秘局等）没有对应直播产物。

🚀 下一步：09-05猎鹰素材盘挂上后补那 15 个账号的图；其余交付物可直接用。

---

## 2026-11-15 · 补跑 09-14 视频号 seg1/seg2 与 09-16 视频号 seg4 — 完成

✅ 两批均 parse → filter → qualify → export → collect 全链跑完，`verify` 0 错误 0 警告

| 配置 | 补的段 | 交付树（前 → 后） |
|---|---|---|
| `09-14艾瑞泽8CTCC_视频号.json` | seg1（61 评论）+ seg2（115 评论），0 字节残留重 OCR | 30 账号 / 437 图 → **40 账号 / 562 图** |
| `09-16老章拆车_视频号.json` | seg4（973 评论），0 字节残留重 OCR | 36 账号 / 407 图 → **48 账号 / 553 图** |

- 09-07 抖音 / 09-13 视频号 / 09-14 快手的 parse 也已跑完并补齐下游：
  分别 112 账号 / 1164 图、18 / 672、23 / 579，verify 均 0 错误。
- **仍未补**：09-05 猎鹰快手 seg1（素材在未挂载的 A1B7-2982）；
  `8pro_20260810_01/seg1`（老 8PRO 批次，素材 `/home/puyu/work/data` 已空）。
- ⚠ 09-07 视频号 seg1：parse 15:12 已重跑（198 条评论），但 filter 还是 11-10 的旧产物，
  `users.jsonl` 比 `comments.jsonl` 旧 ⇒ seg1 目前 0 个合格用户、交付树 40 账号里没有它的贡献。
  补 `--only filter,qualify,export,collect` 即可。

🚀 下一步：需要时补 09-07 视频号 seg1 的 filter 链；其余等素材盘。

---

## 2026-11-15 · 09-07 视频号 filter 链补齐 — 完成

✅ `--only filter,qualify,export,collect` 跑完，`verify` 0 错误 0 警告

- seg1 合格用户 0 → **20**（198 条评论），各段 export 16/17/17/24/23/20 人。
- 交付树 40 账号 / 896 图 → **40 账号 / 1070 图**（账号数不变，seg1 补了 174 张图）。
- 全仓只剩两处 0 字节残留：09-05 猎鹰快手 seg1（A1B7-2982 未挂载）、
  `8pro_20260810_01/seg1`（老批次素材目录已空）。

🚀 下一步：等素材盘挂载后补这两处；其余批次 verify 全绿。

---

## 2026-11-15 · 09-13/09-07 批次 verify 扫描结论

- **09-13 抖音**：三段全解析、78 账号 / 1017 张，之前只有 5 条时间戳警告；
  重跑 `--only qualify,export,collect` 后计数一字未变（78 / 1017），证明内容本就自洽。
- **09-13 快手**：三段全解析、24 账号 / 471 张，`verify` 完整（0 错误），
  仍有同款 5 条时间戳警告（qualify/export/collect 分批跑的 mtime 交错）；
  想全绿就重跑 `--only qualify,export,collect`。
- **09-13 视频号**：seg2 完整（227 评论 → 15 账号 / 167 张），但 **seg1 是 0 字节
  rows.jsonl 残留**（171 分钟素材整段没解析）⇒ 交付和撞库池都缺 12:29–15:19 这段；
  修法同 09-07：`--only panels,parse` 重 OCR seg1，再 filter→collect。
- **09-07 抖音**：真正缺 seg1/seg2（0 字节 rows/comments），其余四段完整；
  要补就 `--only panels,parse` 重 OCR（约 2 小时素材），再跑 filter→collect。
- 规律：verify 的「X 比 Y 旧」类警告 = 下游分批重跑的时间戳交错；
  只要硬核对（账号并集/交付树包含/计数一致）过了，内容就是自洽的。

---

## 2026-11-15 · 未命中名单的最近账号也导出标记图 — 完成

✅ `标记图/未命中/<平台>/<名单名>/<账号目录>/`（与命中图分开归档）

- 默认导图时，未命中名单的 `best`（平台内最近账号）也导一份：交付命中直接复制交付图，
  仅直播的最近账号同样从视频原帧现补（复用 `render_missing`，两拨分开计数）。
- `matches.json` 的 `unmatched[].best` 补全 `level/in_delivery/n_comments/n_positive/
  count/segs/first_image/dir_exists`，与 `matches[]` 同形，供导图/复核。
- HTML「未命中名单」新增「最近图」列，直接点开 `../标记图/未命中/…`。
- 终端摘要增加「未命中最近图：N 个账号目录 · M 张」；README / doc / 全流程说明同步。
- 顺带修复 bug：`summary.json` 旧视频路径落在无权限目录（`/root/…`）时
  `Path.is_file()` 抛 `PermissionError`，`--video-map` 根本没机会生效。
- 自检 118 项全过；09-16 抖音实跑：2 个未命中 → `标记图/未命中/抖音/<名字>/追风的猫-8/`
  各 8 张；另用真实视频验证「仅直播」命中/最近账号现补均落到预期目录。

🚀 下一步：下次撞库直接用；复核未命中先看 `标记图/未命中/`。

---

## 2026-11-15 · 素材盘迁移：8 份配置的视频路径改到 MyDisk — 完成

✅ `09-05风云A9L渠道_{抖音,视频号}.json`、`09-06猎鹰智驾_{抖音,视频号,快手}.json`、
`09-07猎鹰智驾_{抖音,视频号,快手}.json`：34 个 `input.videos` 路径 + 8 处文件头注释

- 旧前缀 `/home/puyu/work/data/`、`~/LiveMonitorMockScale/data/` → `/run/media/puyu/MyDisk/`
- 目标文件逐个确认存在；`C.load` 严格模式全部通过，`verify` 直接跑视频时间核对。
- 未动：`08-08CTCC*` / `10-31_*` / 风云A9L 年型车等（仍在未挂载的 `A1B7-2982`，
  没有同名新文件）。

---



✅ 新子命令 `python -m livepipe verify <cfg.json>`（`livepipe/tools/verify_run/`）

- 只读、按配置实际步骤核对：comments/summary/rows/frames、users、users_qualified/merged、
  export 各平台 `all_users_*`、collect 交付树；并检查上下游时间戳断链。
- 退出码 0/1/2；`--json` 机读、`--strict` 警告也失败、`--only/--skip` 与 run 同义。
- 素材盘没挂载也能用：`config.load(..., check_videos=False)` 宽松模式。
- **视频时间 × seg 时间**（2026-11-15 追加）：段文件名区间 × ffprobe 实际时长；短于区间
  超容差（默认 120s）⇒ 错误，长于 ⇒ 警告；`--time-tolerance` / `--no-media-time` /
  `--video-map 旧=新`。实测 09-07 快手 seg4 文件名 `14:30-17:23`（173min）实际 53min ⇒ fail。
- 自检 36 项全过；扫全仓配置：09-16 抖音 / 10-31 三平台判完整；
  09-07 抖音 seg1/2、09-16 视频号 seg4、09-05/06/14 快手 seg1 命中 0 字节 rows 的真实缺口；
  09-08 系列报下游未重跑的断链。

🚀 下一步：交付前用 `python -m livepipe verify <cfg> --strict` 过一遍。

---



✅ `撞库名单/split_account_lists.py` → `撞库名单/可用名单/`（32 份场次 × 平台名单）

- 源表 22 个 sheet 按「场次 × 平台」拆开：每场一份 `.csv`（多平台并排，`livepipe match`
  直接读）+ 每个平台一份 `.txt`（一行一个，配 `--platform`）+ `README.md` / `manifest.json`。
- 9.5 拆成猎鹰（上午）/ 风云A9L（下午）两场；9.6–9.15 按上午/下午拆；9.22 按渠道张 /
  渠道刘真人 / 渠道刘通用拆；10.20 拆主名单 / 央视财经（无需录屏，只留档）。
- 校验：32 份全部用 `read_name_list` 回读一致；`match --selftest` 106 项全过；
  09-16 CSV/TXT 实跑结果一致。
- ⚠ 09-13 抖音/快手、09-22 三平台还没有 `all_users_*` 交付树；9.30–10.6 与 10.20/24/25
  没有对应 out 批次（README 已标）。

🚀 下一步：需要撞哪天就 `python -m livepipe match 撞库名单/可用名单/<日期>/<slug>.csv "out/<批次>"`。

---



✅ 新配置（仓库根）：`09-16老章拆车_抖音.json` / `09-16老章拆车_视频号.json`

- 素材：`/run/media/puyu/MyDisk/2026-09-16“汽车人老章”A9拆车（渠道七七）/`（外置盘）
  - 抖音 5 段（590×1280，账号「老章说车」，19：40-22：17）：
    40.3 + 37.8 + 36.8 + 34.7 + 7.9min = 157.5min
  - 视频号 6 段（590×1280，账号「老章拆车」，19：40-22：18）：
    30.1 + 28.6 + 29.1 + 28.4 + 27.0 + 14.7min = 158.0min
  - 两平台同一台手机、同一版 App（590×1280），无旋转/翻转。
- 输出：`out/2026-09-16“汽车人老章”A9拆车（渠道七七）/{抖音,视频号}`；
  11 份面板已落盘（抖音 5 / 视频号 6）。
- 量到的版式（2026-11-11，每文件 3 帧 + 逐帧扫描）：
  - 抖音 590×1280：行距 **43.8** / y_phase 10 / x_text **84** / badge_span **52** /
    wrap_gap_ratio 0.85（续行 26~29.5px=0.60~0.67，独立行 42~46px）/
    rect **[0,920,590,1190]** / x2_from=content / x_ignore_from **null**（本批无右侧卡）/
    account_key=**nickname_level_if_masked**；
    overlay 补 `分享了直|加入粉丝团|等\d+人在说|人在说|官方提示`。
  - 视频号 590×1280：行距 **41.9** / y_phase 15 / x_text **64** / badge_span **46** /
    wrap_indent + wrap_gap_ratio **0.75**（续行 25~25.5px=0.60~0.61，独立行 35~42px）/
    rect **[0,740,590,1190]** / x2_from=window / x_ignore_from 350 /
    fan_badge_x_max **90** + fan_badge_with_level / account_key=**nickname_level_if_masked**；
    overlay 补 `加入粉丝团|送出粉丝牌|等\d+人在说|人在说`。
  - ⚠ 两个平台的「评论区顶部」完全不同（本批实测）：
    - 抖音评论是 **逐行渐隐**：880~918 的亮白 p99.5 只有 185~206，920 起才是 250~255
      ⇒ y1=920（取在渐隐带之下）；
    - 视频号 **没有渐隐**：最上面一条真实评论顶 ≈762（t=144 `送出粉丝牌`），
      再往上的 650~750 是**视频画面里的手机屏幕文字**（`责任免球款以官方`）⇒ y1=740。
- 尾部裁切（只认底部输入栏，0.5s 级二分）：
  - 抖音 seg5（`22：09-22：17`）直播在 **131.88s** 结束 ⇒ 流拷贝到 131.7s：
    `out/2026-09-16…/media/dy_22-09-22-17_live.mp4`（2.2min），末帧仍有 `说点什么`；
  - 视频号 seg6（`22：03-22：18`）直播在 **515.31s** 结束 ⇒ 流拷贝到 515.1s：
    `out/2026-09-16…/media/wx_22-03-22-18_live.mp4`（8.6min），末帧仍有 `聊一聊`；
  - 抖音 seg4 / 视频号 seg5 均直播到文件末尾，不裁。
- 校验：两份 `livepipe check` 全部 ✔；11 份 `panels.json` + `panels.resolved.json` 已写出；
  `--dry-run` 全过（seg5/seg6 指向裁好的副本，表情表命中 douyin/wechat）。
- 短切片试跑（seg1 t=600s，60s，`--only panels,parse` 等价的 build_parse_args 调用）：
  - 抖音 **69 条唯一评论**（114 帧 / 661 行，等级标 87.0%，用户名未识别 9 条，多行合并 8 条，
    表情 8 条 22 个）；把 overlay 补齐后零 OCR 复算 **63 条**，`粉丝团/分享了直播/人在说`
    类系统行清零（残留 1 条：分享通知被并进上一条评论）。
  - 视频号 **40 条唯一评论**（53 帧 / 368 行，多行合并 11 条 27.5%，用户名未识别 12 条 30%，
    表情 9 条 11 个）；`粉丝` 前缀正确剥掉（`粉丝泉水：` → `泉水`）。
  - ⚠ 试跑会把 60s 的 `rows.jsonl` 写进真实段目录（`--dump-rows` 用的是 seg out），
    两个 seg1 的 `rows.jsonl` 已删除；正式跑之前确认段目录只有 panels.json。

🚀 下一步：全量抽行（抖音 5 段约 2.2h、视频号 6 段约 2.3h 录屏）——
`$P -m livepipe run 09-16老章拆车_抖音.json --only panels,parse`，视频号同；
再 `NLP_API_KEY=... run … --only filter,qualify,export,collect`。
⚠ 外置盘路径，跑之前确认盘已挂载；本机还有 09-14 抖音全量任务在跑，OCR 串行避免 OOM。

---

## 2026-11-11 · 奇瑞2026全球创新大会（CGIC 2026·芜湖）2026-10-19~24 — 新建抖音配置 — 完成

✅ 新配置（仓库根）：`1019-1024奇瑞创新大会_抖音.json`

- 素材：`/run/media/puyu/MyDisk/2026-1019-1024奇瑞创新大会/抖音-奇瑞汽车/`（外置盘；
  本批**只有抖音**一个平台，没有快手/视频号目录）
  - 4 段 590×1280：`7：58-9：33`（96.7min）· `9：35-10：50`（75.9）·
    `10：51-12：08`（77.8）· `12：09-13：15`（66.9），合计 317.3min
  - 末段最后一帧仍是直播态（底部输入栏 `说点什么...` 在）⇒ **不需要裁尾**
- 量到的版式（2026-11-11，每文件 3~8 帧）：
  - 抖音 590×1280：行距 **43.8** / y_phase 22 / x_text **86** / badge_span **74** /
    wrap_gap_ratio **0.85** / rect **[0,672,590,1196]** / x2_from=content /
    x_ignore_from **380**（商品讲解卡 x1≥390）/ account_key=username /
    badge_alias + username_alias + badge_name_recover / colon 255+30 /
    emoji douyin（`build.enabled=true`）
  - 顶部两处坑：① 视频画面到 y≈591，y≈522 的「!CHANGE! 2026奇瑞全球创新大会」是
    **视频里的下三分之一横幅**（OCR 读成 `ICHANGE：…`，像置顶评论，其实在视频区）；
    ② 评论列表顶部有 ~70px 渐隐带（半透明行，OCR 仍读得出零碎字）到 ≈672 才全亮
    ⇒ rect y1=672
  - 折行续行 x1 26~35（< 86−58+8=36 ⇒ 贴左沿分支，不需要 wrap_indent）；续行间距
    27.5~32px=0.63~0.73、独立行 41~43=0.94~0.98 ⇒ 0.85
  - overlay 补开场三张卡（折行 8 块的逐块短语：`直播或礼物消费` / `严禁出现违法违` /
    `明示信息为准` / `工作人员高效回应` / `当科技切实改变生活` / `价值才能真正体现`）
    + 内置表没有的 `分享了直播` / `推荐了直播` + `^奇瑞汽车$`（通知被折行后剩下的整行）+
    `^讲解虫$`（`讲解中` 的 OCR 变体）
- 校验：`livepipe check` ✔；`--only panels` 写出 4 份 panels.json；`--dry-run` 全过。
- 试跑（seg1 t=600~780s、10 帧、2 个面板文件直接走 manual）：`--only panels,parse` 产出
  6 条评论（多行续行正确并成一条、`分享了直播`/`来了` 全部丢掉、无匿名碎片行）；
  本机约 30s/帧。

🚀 下一步：跑全量抽行（**串行**，四段合计 5.3h 录屏）
`$P -m livepipe run 1019-1024奇瑞创新大会_抖音.json --only panels,parse`
（`P=/home/puyu/anaconda3/envs/vlx/bin/python`；中断了就重跑同一条，rows.jsonl 在会续跑）
再设 `NLP_API_KEY` 跑 `--only filter,qualify,export,collect`。

## 2026-11-11 · 艾瑞泽8CTCC职业联赛（上海站）（渠道七七）2026-09-15 — 新建三份配置 — 完成

✅ 新配置（仓库根）：`09-15艾瑞泽8CTCC_抖音.json` / `09-15艾瑞泽8CTCC_快手.json` /
`09-15艾瑞泽8CTCC_视频号.json`

- 素材：`/run/media/puyu/MyDisk/2026-09-15艾瑞泽8CTCC职业联赛（上海站）（渠道七七）/`（外置盘）
  本批**上午 + 下午两场**，每个平台都要处理**两个场次末段**：
  - 抖音 5 段：上午 `8：58-10：10` + 裁尾 `dy_morn_10-11-11-09_live`；
    下午 `13：58-15：11` / `15：11-16：19` + 裁尾 `dy_eve_16-19-17-18_live`
  - 快手 4 段：上午 `8：59-10：40` + 裁尾 `ks_morn_10-40-11-09_live`；
    下午 `13：58-15：36` + 裁尾 `ks_eve_15-36-17-18_live`
  - 视频号 5 段：上午 `8：58-10：16` + 裁尾 `wx_morn_10-17-11-09_live`；
    下午 `13：58-15：14` / `15：14-16：23` + 裁尾 `wx_eve_16-24-17-04_live`
- 输出：`out/2026-09-15艾瑞泽8CTCC职业联赛（上海站）（渠道七七）/{抖音,快手,视频号}`；
  14 份面板已落盘（抖音 5 / 快手 4 / 视频号 5）。
- 量到的版式（2026-11-11，每文件 3 帧，全部 590×1280）：
  - 抖音：行距 **43.8** / y_phase 26 / x_text 84 / badge_span 50 /
    wrap_gap_ratio 0.85 / rect **[0,668,590,1190]** / x2_from=content /
    x_ignore_from **380**（右侧商品讲解卡 x1≥391）/ account_key=username /
    merge_emoji_continuation + emoji_gap_sweep。
  - 快手：行距 **45.0** / y_phase 15 / x_text 81 / badge_span 47 /
    wrap_gap_ratio 0.85 / rect **[0,655,590,1200]** / x2_from=window /
    无 x_ignore_from / account_key=username / emoji_workers 1；
    overlay 沿用 `送出.*人气` + `^票[爵等]?$` 兜底拆行的送礼提示。
  - 视频号：行距 **41.9** / y_phase 14 / x_text 64 / badge_span 46 /
    wrap_indent、wrap_gap_ratio **0.75** / rect **[0,686,590,1190]** /
    x2_from=window / x_ignore_from 350 / fan_badge_x_max **90** +
    fan_badge_with_level / account_key=username；底部左侧广告卡
    （`奇瑞汽车`/`HER`/`点击了解详情`）走 skip_phrases + overlay_cards。
- 尾部裁切（6 个场次末段，都是「直播已结束」页）：PyAV seek + 底部输入栏 0.5s 二分，
  实测最后直播点（相对各段起点）：
  - 抖音 上午 **2981.91s** → 2981.7 / 下午 **2479.38s** → 2479.2
  - 快手 上午 **1235.72s** → 1235.5 / 下午 **5027.37s** → 5027.2
  - 视频号 上午 **2639.22s** → 2639.0 / 下午 **2190.02s** → 2189.8
  - 六份裁后末帧 OCR 均仍有输入栏（live=True）。
- 校验：三份 `livepipe check` 全部 ✔；`--only panels` 写出 14 份 panels.json；
  三份 `--dry-run` 全部 exit=0。

🚀 下一步：按需跑抽行
`$P -m livepipe run 09-15艾瑞泽8CTCC_<平台>.json --only panels,parse`（串行，OCR 并行会 OOM）；
再设 `NLP_API_KEY` 跑 `--only filter,qualify,export,collect`。

## 2026-11-11 · 艾瑞泽8CTCC职业联赛（上海站）（渠道七七）2026-09-14 — 新建三份配置 — 完成

✅ 新配置（仓库根）：`09-14艾瑞泽8CTCC_抖音.json` / `09-14艾瑞泽8CTCC_快手.json` /
`09-14艾瑞泽8CTCC_视频号.json`

- 素材：`/run/media/puyu/MyDisk/2026-09-14艾瑞泽8CTCC职业联赛（上海站）（渠道七七）/`（外置盘）
  - 抖音 3 段（590×1280，14：30-15：47 + 15：47-18：14 + 18：14-19：00，共 270.1min）
  - 快手 3 段（590×1280，14：30-16：01 + 16：02-17：58 + 17：59-19：00，共 270.2min）
  - 视频号 4 段（590×1280，14：30-15：41 + 15：41-16：58 + 16：58-18：37 + 18：38-19：00，共 269.5min）
- 输出：`out/2026-09-14艾瑞泽8CTCC职业联赛（上海站）（渠道七七）/{抖音,快手,视频号}`；
  10 份面板已落盘（抖音 3 / 快手 3 / 视频号 4）。
- 量到的版式（2026-11-11，每文件 3 帧）：
  - 抖音 590×1280：行距 **43.8** / y_phase 26 / x_text 84 / badge_span 50 /
    wrap_gap_ratio 0.85 / rect **[0,668,590,1190]** / x2_from=content /
    x_ignore_from **380**（商品讲解卡 `·讲解中`/`天生热爱等你开场`/`艾瑞泽8征服版`/
    `立即预约`/`CHERY` 的 x1≥392）/ account_key=username /
    merge_emoji_continuation + emoji_gap_sweep。
  - 快手 590×1280：行距 **45.0** / y_phase 18 / x_text 81 / badge_span 47 /
    wrap_gap_ratio 0.85 / rect **[0,655,590,1200]** / x2_from=window /
    无 x_ignore_from / account_key=username / emoji_workers 1；
    overlay 补 `送出.*人气` + `^票[爵等]?$`（`送出11个人气` 常被拆行）。
  - 视频号 590×1280：行距 **41.9** / y_phase 14 / x_text 64 / badge_span 46 /
    wrap_indent、wrap_gap_ratio **0.75**（续行 22.5~26.5px=0.54~0.63，独立行
    34.5~42.5px=0.82~1.01，0.85 会误并）/ rect **[0,686,590,1190]** /
    x2_from=window / x_ignore_from 350 / fan_badge_x_max **90** +
    fan_badge_with_level / account_key=username；overlay 补糊掉的系统行 `天注了王播`。
- 尾部裁切（三份末段都有「直播已结束」页）：PyAV seek + 底部输入栏 0.5s 二分，
  实测最后直播点 **2805.37 / 3711.97 / 1335.30s** ⇒ 各减 0.2s 重裁到
  **2805.2 / 3711.8 / 1335.1s**，PyAV 流拷贝（`add_stream_from_template`）：
  - `out/2026-09-14…/media/dy_18-14-19-00_live.mp4`（46.8min）
  - `out/2026-09-14…/media/ks_17-59-19-00_live.mp4`（61.9min）
  - `out/2026-09-14…/media/wx_18-38-19-00_live.mp4`（22.3min）
  - 抖音裁后末帧 OCR 仍见 `说点什么`（live=True）；快手/视频号逐帧校验见 memory。
- 校验：三份 `livepipe check` 全部 ✔；`--only panels` 写出 10 份 panels.json；
  `--dry-run` 全部走通（filter 起因尚无 comments 而跳过，属预期）。

🚀 下一步：按需跑抽行
`$P -m livepipe run 09-14艾瑞泽8CTCC_<平台>.json --only panels,parse`（串行，OCR 并行会 OOM）；
再设 `NLP_API_KEY` 跑 `--only filter,qualify,export,collect`。

## 2026-11-11 · 09-10 及以后配置 — 远程建表开关统一打开 — 完成

✅ 抖音 4 份（09-10/09-12/09-13/10-31）与视频号 3 份（09-12/09-13/10-31）补上
`emoji.tables.<src>.build.enabled=true`；快手 4 份原本就开着。

- 服务器实测建表：抖音 214 张（官方表情接口，约 1 分钟）、快手 207 张、视频号 109 张，全部成功。
- 抖音脚本单独跑必须带 `--api`（否则落 DOM 分支报「没抽到任何表情 URL」）；
  pipeline 会自动补 API_URL，配置里只写 `{"enabled": true}` 即可。
- 09-10/09-12/09-13 全过 `livepipe check`；10-31 两份因外置盘未挂载只能做语法+开关校验。
- 注意：开启后每次 `run.sh` 都会重建对应表；不想刷新用 `LIVEPIPE_SKIP_EMOJI=1 ./run.sh …`。

🚀 下一步：上传 09-10 抖音/快手批次时用同一套 `upload_livepipe_ssh.py --apply`，
run.sh 会自动在远程建好表情表再进 parse。

## 2026-11-11 · 09-10 视频号远程批次 — 表情表改为远程自建 — 完成

✅ 远程 `~/livepipe_batches/09-10猎鹰智驾_视频号/out/wechat_emoji/` 原先只有表 JSON、没有 `img/`，
`./run.sh` 四个平台全部「跳过 emoji_tables」，parse 报「一条可用的模板都没有（图没下全？）」。

- 根因两层：① `emoji.tables.*.build.enabled` 默认 false（配置没开 ⇒ run.sh 预建表空转）；
  ② `upload_livepipe_ssh.py` 只传 `*-emoji.json`、不传同目录 `img/`（109 张 png / 888K）
  ⇒ 远程 109 条模板全缺图，gallery 抛错后被降级成「跳过表情识别」。
- 修法：`09-10猎鹰智驾_视频号.json` 增加 `emoji.tables.wechat.build.enabled=true` 并重传；
  远程 `./run.sh --only emoji_tables` 从 codeload.github.com 拉 4.7MB tarball、解出 109 张 png、
  重建表（落 `<base_dir>/out/wechat_emoji/`）。远程 `load_emoji_gallery` 验证 = 109 个模板。
- 注意：建表无图片缓存，每次 run.sh 都会重下；不想刷新用 `LIVEPIPE_SKIP_EMOJI=1 ./run.sh …`。

🚀 下一步：远程 `./run.sh --only panels,parse`（四段 ~3.9h OCR）。

⚠ 待办：`upload_livepipe_ssh.py` 传表情表时把 `img/` 一并纳入上传计划（否则所有跳过
`emoji_tables` 的批次都会踩同一个坑）。

## 2026-11-10 · 艾瑞泽8CTCC职业联赛（上海站）（渠道七七）2026-09-12 — 新建三份配置 — 完成

✅ 新配置（仓库根）：`09-12CTCC_抖音.json` / `09-12CTCC_快手.json` / `09-12CTCC_视频号.json`

- 素材：`/run/media/puyu/MyDisk/2026-09-12艾瑞泽8CTCC职业联赛（上海站）（渠道七七）/`（外置盘）
  - 抖音 2 段（590×1280，14：36-16：04 + 16：05-17：49，共 192.5min）
  - 快手 2 段（590×1280，14：36-16：02 + 16：02-17：49，共 193.0min）
  - 视频号 1 段（592×1280，14：35-17：47，193.6min）
- 输出：`out/2026-09-12艾瑞泽8CTCC职业联赛（上海站）（渠道七七）/{抖音,快手,视频号}`；
  5 份面板已落盘（抖音 2 / 快手 2 / 视频号 1）。
- 量到的版式（2026-11-10，每文件 6 帧；rect y1 都在顶部虚化带之下）：
  - 抖音 590×1280：行距 **35.0** / y_phase 20 / x_text 78 / badge_span 58 /
    wrap_indent、wrap_gap_ratio **0.95** / rect **[0,655,590,1210]** /
    x2_from=content / x_ignore_from **380**（商品卡 x1≥390）/
    account_key=**nickname_level_if_masked**（有 `穿越**` 这类打码名）。
  - 快手 590×1280：行距 **45.0** / y_phase 26 / x_text 81 / badge_span 58 /
    wrap_indent、wrap_gap_ratio 0.85 / rect **[0,680,590,1200]** /
    x2_from=window / 无 x_ignore_from / account_key=username / emoji_workers 1。
  - 视频号 592×1280：行距 **33.0** / y_phase 16 / x_text 66 / badge_span 44 /
    wrap_indent、wrap_gap_ratio 0.85 / rect **[0,660,592,1190]** /
    x_ignore_from 500 / fan_badge_x_max **66** + fan_badge_with_level /
    account_key=**nickname_level_if_masked**；底部广告卡
    （`奇瑞汽车` / `HER` / `点击了解详情`）走 skip_phrases + overlay_cards。
- 尾部裁切（三份都是「直播已结束」页 + 推荐/广告流）：
  - 第一版按 cv2 `CAP_PROP_POS_MSEC` 采样切到 1507.5 / 1742.4 / 6905.6s，
    **末帧仍是结束页**（cv2 seek 不可靠 ⇒ 采样点判晚）。
  - 改用 PyAV `container.seek` + **底部输入栏**（`说点什么` / `聊一聊`）0.5s 二分，
    实测最后直播点 1505.34 / 1738.26 / 6903.02s ⇒ 各减 0.2s 重裁：
    - `out/…/media/dy_eve_16-05-17-49_live.mp4`（1505.1s）
    - `out/…/media/ks_eve_16-02-17-49_live.mp4`（1738.0s）
    - `out/…/media/wx_14-35-17-47_live.mp4`（6902.7s）
  - 重裁后三份末帧 OCR 均有输入栏、无「直播已结束」（stream copy，无重编码）。
- 已知边界：快手 `N粉丝团` / `榜N` 药丸偶尔与昵称并框会污染用户名（同 08-08 / 09-13），
  交付时按名单人工核对；快手 `6 6 666` 这类纯数字续行会被徽章列判据丢掉（已核对只有个例）。
- 验证：三份 `livepipe check` 全过；5 份 `panels.json` 已写出；三份 `--dry-run` 全过
  （段路径、表情表、badge_reread 自动绑定均正确）。

🚀 下一步：全量抽行（5 段合计 ~5.4h 直播）——
`$P -m livepipe run 09-12CTCC_抖音.json --only panels,parse`，快手/视频号同；
再 `NLP_API_KEY=... run … --only filter,qualify,export,collect`。
⚠ 外置盘路径，跑之前确认盘已挂载。

---

## 2026-11-10 · 猎鹰智驾瑞虎9羌塘无人区（渠道）2026-09-10 — 新建三份配置 — 完成

✅ 新配置（仓库根）：`09-10猎鹰智驾_抖音.json` / `09-10猎鹰智驾_快手.json` / `09-10猎鹰智驾_视频号.json`

- 素材：`/run/media/puyu/MyDisk/2026-09-10猎鹰智驾瑞虎9羌塘无人区（渠道）/`（外置盘）
  - 抖音 4 段（720×1560，上午 2 + 下午 2，共 236.5min）
  - 快手 5 段（上午 3 段 720×1560 + 下午 2 段 1968×3150，共 237.7min）
  - 视频号 4 段（592×1280，上午 2 + 下午 2，共 232.8min）
    ⚠ 视频号两个文件夹名误写成「9.10快手丨奇瑞汽车…」，内容是视频号；配置按真实路径写。
- 输出：`out/2026-09-10猎鹰智驾瑞虎9羌塘无人区（渠道）/{抖音,快手,视频号}`；13 份面板已落盘。
- 量到的版式（2026-11-10，每档 4 帧；rect y1 均在顶部渐隐带之下）：
  - 抖音 720×1560：行距 **53.0** / y_phase 22 / x_text 96 / badge_span 74 /
    rect **[0,850,620,1380]** / x2_from=content / x_ignore_from 470（商品卡 x1≥476）。
  - 快手 720×1560：行距 **55.0** / y_phase 30 / x_text 105 / badge_span 74 /
    rect **[0,780,720,1380]** / x2_from=window / x_ignore_from 470。
  - 快手 1968×3150：行距 **79.9** / y_phase 60 / x_text 155 / badge_span 110 /
    rect **[0,1700,1968,3000]** / x2_from=content / x_ignore_from **700**
    （seg4 部分时段「粉丝农场」小游戏卡，卡面文字最小左沿 751、真评论被裁在 x2≤665）。
  - 视频号 592×1280：行距 **41.5** / y_phase 17 / x_text 34 / badge_span 44 /
    rect **[0,740,592,1128]** / x2_from=window / fan_badge_x_max 32 /
    **x_ignore_from null**（四个文件 60s 间隔扫描均未见右侧卡片）。
- `overlay_cards` 快手两档追加闪光语录短语 + 粉丝农场卡面词；短切片又补
  `官方扶持|^来白|丝团灯牌|团灯牌`（见 `.wolf/buglog.json` 2026-11-10 快手条目）。
- `parse.calibrate=false`（同 09-05~09-09）；`account_key="username"`；快手两档 `emoji_workers=1`。
- 验证：三份 `livepipe check` 全过；13 份 `panels.json` 已写出；三份 `--dry-run` 全过；
  每平台 120s 真实短切片（抖音 seg3 t=1800 / 快手 seg1 t=1800、seg4 t=2400 / 视频号 seg3
  t=1800）parse 全部 rc=0，真实评论可读（如 `魔女：同事奇瑞开了五六年，没啥毛病`）。
- ⚠ 已知边界（配置无开关，已在快手配置头注明）：`N粉丝团` 药丸偶尔与昵称并框
  （`4粉丝团清风徐来`），同一个人可能出现带前缀的读法；全量后按 users.jsonl 里
  `*粉丝团*` 账号人工核对。

🚀 下一步：全量抽行（13 段合计 ~11.8h 录屏）——
`$P -m livepipe run 09-10猎鹰智驾_抖音.json --only panels,parse`，快手/视频号同；
再 `NLP_API_KEY=... run … --only filter,qualify,export,collect`。
⚠ 外置盘路径，跑之前确认盘已挂载。

---

## 2026-11-10 · 艾瑞泽8CTCC职业联赛（上海站）（渠道七七）2026-09-13 — 新建三份配置 — 完成

✅ 新配置（仓库根）：`09-13艾瑞泽8CTCC_抖音.json` / `09-13艾瑞泽8CTCC_快手.json` / `09-13艾瑞泽8CTCC_视频号.json`

- 素材：`/run/media/puyu/MyDisk/2026-09-13艾瑞泽8CTCC职业联赛（上海站）（渠道七七）/`（外置盘）
  - 抖音 3 段（590×1280，12：31-16：31，共 ~4.0h）
  - 快手 3 段（590×1280，12：30-16：31，共 ~4.0h）
  - 视频号 2 段（592×1280，12：29-16：30，共 ~4.0h）
- 输出：`out/2026-09-13艾瑞泽8CTCC职业联赛（上海站）（渠道七七）/{抖音,快手,视频号}`；8 份面板已落盘。
- ⚠ 三平台都是 **590/592×1280**，与本仓常见的 720×1560 / 1968×3150 都不同 ⇒ 全部像素量重测：
  - 抖音 590×1280：行距 **43.8** / y_phase 37 / x_text 83 / badge_span 48 /
    rect **[0,672,590,1189]** / x2_from=content / x_ignore_from null（右侧无卡）
  - 快手 590×1280：行距 **45.0** / y_phase 7 / x_text 81 / badge_span 47 /
    rect **[0,668,590,1189]** / x2_from=window / x_ignore_from null（正文最长 558）
  - 视频号 592×1280：行距 **41.9** / y_phase 7 / x_text 34 / badge_span 44 /
    fan_badge_x_max 32 / rect **[0,658,592,1190]** / x_ignore_from 350。
    **底部左侧有广告卡**（广告/奇瑞汽车/HER/点击了解详情）压住最后两行 ⇒ overlay + skip 补上。
  - rect y1 都取在**顶部渐隐带（虚化）之下**（用户口径；本批渐隐带到 655~665）。
- 露出的另一批参照：`09-12CTCC_*.json` 是同赛事 09-12 的配置，但**缩放不同**
  （抖音行距 35、视频号 33）⇒ 数字不能照抄，必须按本批素材重测。
- ⚠ 抖音 seg3（16：00-16：31）与快手 seg3（15：15-16：31）尾部是「直播已结束」页
  （推荐商品/猜你喜欢）。实测**直播最后一帧**：
  - 抖音 1849.69s（1850.0s 起已是结束页）→ `-t 1849.6 -c copy` ⇒
    `out/…/media/dy_16-00-16-31_live.mp4`（30.8min）
  - 快手 4557.44s（4557.75s 起结束页）→ `-t 4557.4 -c copy` ⇒
    `out/…/media/ks_15-15-16-31_live.mp4`（76.0min）
  - 判定用**底部输入栏 `说点什么`** 是否在（结束页先黑屏/直接出猜你喜欢，
    靠「直播已结束」四个字会判晚 2~3s；第一版就切多了，已重切）。
- 全批方向扫描（逐文件采样 8~16 帧）无翻转；视频号两段结尾都仍在直播，无需裁尾。
- 验证：三份 `livepipe check` 全过；8 份 `panels.json` 已写出；三段 60s 真实切片
  `--only panels,parse` 全部 rc=0（抖音 14 条 / 快手 16 条 / 视频号 13 条唯一评论，
  用户名与正文可读，快手 `榜N` 药丸被正确剔除，抖音/视频号纯表情行由 emoji_text 补齐）。

🚀 下一步：全量抽行（8 段合计 ~12h 录屏）——
`$P -m livepipe run 09-13艾瑞泽8CTCC_抖音.json --only panels,parse`，快手/视频号同；
再 `NLP_API_KEY=... run … --only filter,qualify,export,collect`。
⚠ 外置盘路径，跑之前确认盘已挂载；视频号 seg1 单段 2h51m，耐心。

---

## 2026-11-10 · 猎鹰智驾瑞虎9羌塘无人区（渠道）2026-09-08 — 新建三份配置 — 完成

✅ 新配置（仓库根）：`09-08猎鹰智驾_抖音.json` / `09-08猎鹰智驾_快手.json` / `09-08猎鹰智驾_视频号.json`

- 素材：`/run/media/puyu/MyDisk/2026-09-08猎鹰智驾瑞虎9羌塘无人区（渠道）/`（外置盘）
  - 抖音 6 段（720×1560，上午 3 + 下午 3，共 ~347min）
    ⚠ 文件夹名误写成「9.8快手…」，内容实为抖音 App 录屏
  - 快手 7 段（上午 3 段 720×1560 + 晚场大档 4 段 1968×3150，共 ~344min）
    晚场第一段 `16：11-17：06.mp4` **中途上下翻转**，已拆成两个无损副本（见下）
  - 视频号 6 段（592×1280，上午 3 + 下午 3，共 ~351min；末段直播到最后一帧即结束，无需裁剪）
- 输出：`out/2026-09-08猎鹰智驾瑞虎9羌塘无人区（渠道）/{抖音,快手,视频号}`；19 份面板已落盘。
- 量到的版式（2026-11-10，每档 14 帧；rect y1 均在顶部渐隐带之下）：
  - 抖音 720×1560：行距 **53.5** / y_phase 42 / x_text 96 / badge_span 74 /
    rect **[0,770,620,1380]** / x2_from=content / x_ignore_from 470
  - 快手 720×1560：行距 **55.0** / y_phase 30 / x_text 105 / badge_span 74 /
    rect **[0,740,720,1380]** / x2_from=window / x_ignore_from 470
  - 快手 1968×3150：行距 **80.0** / y_phase 55 / x_text 155 / badge_span 110 /
    rect **[0,1600,1968,3000]** / x2_from=content / x_ignore_from **1400**
  - 视频号 592×1280：行距 **41.5** / y_phase 22 / x_text 34 / badge_span 44 /
    rect **[0,675,592,1128]** / x2_from=window / fan_badge_x_max 32 / x_ignore_from 350
- 🔧 快手晚场 `9.8快手丨奇瑞汽车丨16：11-19：00/16：11-17：06.mp4`（1968×3150，55.3min）
  **前 260s 正立，之后整段倒置**、无 rotation 元数据（翻转点在 260.0–260.5s）。已在关键帧
  260.6186s 处无损拆成两个副本（`-c copy`）：
  - `out/…/media/ks_eve_16-11-17-06_a_upright.mp4`（260.6s，原样）
  - `out/…/media/ks_eve_16-11-17-06_b_rot180.mp4`（3056.6s，`-display_rotation 180` 写入
    rotation=-180，OpenCV `CAP_PROP_ORIENTATION_AUTO` 读取时自动转正；ffprobe 显示 1968×3150）
  - 配置里 seg4/seg5 指向这两个副本，seg6/seg7 用原始 17：07-18：09 / 18：09-18：59。
- `parse.calibrate=false`（同 09-05~09-07）；`account_key="username"`（三平台昵称不打码）。
- 验证：三份 `livepipe check` 全过（19 段面板已落盘，快手正确展开 7 段、副本顺序正确）；
  截图 OCR 量 x 右边界（抖音 max 577 < 620、快手大档 max 1290 < 1400）；
  四段真实短切片 `--only panels,parse` 全部 rc=0：
  - 抖音 720（t=1200s，60s）→ 35 条唯一评论，多行合并 2、表情 15 个
  - 快手 720（t=1200s，60s）→ 12 条唯一评论，表情 6 个
  - 快手 1968 旋转副本 b（t=900s，120s）→ 24 条唯一评论，正文/用户名干净
    （如 `楼你作业：奇瑞每款车都挺能打`），含 [火]/[赞] 识别
  - 视频号 592（t=1200s，60s）→ 11 条唯一评论

🚀 下一步：全量抽行（19 段合计 ~17.4h 录屏）——
`$P -m livepipe run 09-08猎鹰智驾_抖音.json --only panels,parse`，快手/视频号同；
再 `NLP_API_KEY=... run … --only filter,qualify,export,collect`。
⚠ 外置盘路径，跑之前确认盘已挂载；大档 4K 段建议 `emoji_workers=1`。

---

## 2026-11-10 · 猎鹰智驾瑞虎9羌塘无人区（渠道）2026-09-09 — 新建三份配置 — 完成

✅ 新配置（仓库根）：`09-09猎鹰智驾_抖音.json` / `09-09猎鹰智驾_快手.json` / `09-09猎鹰智驾_视频号.json`

- 素材：`/run/media/puyu/MyDisk/2026-09-09猎鹰智驾瑞虎9羌塘无人区（渠道）/`（外置盘）
  - 抖音 4 段（720×1560，上午 2 + 下午 2，共 201.3min）
  - 快手 4 段（上午 2 段 720×1560 + 下午 2 段 1968×3150，共 195.4min）
  - 视频号 5 段（592×1280，上午 3 + 下午 2，共 225.7min）
- 输出：`out/2026-09-09猎鹰智驾瑞虎9羌塘无人区（渠道）/{抖音,快手,视频号}`；13 份面板已落盘。
- 用户要求「框要低一点，因为有的评论区顶部有虚化」⇒ 三平台 rect y1 全部下移：
  - 视频号 592×1280：rect **[0,760,592,1128]**（分界≈717、渐隐带到≈745）；
    行距 41.5 / x_text 34 / badge_span 44 / `fan_badge_x_max` 90 / x_ignore_from 350。
  - 抖音 720×1560：rect **[0,790,620,1390]**（分界≈717、渐隐带到≈770、输入栏上沿 1403）；
    行距 53.0 / y_phase 35 / x_text 96 / badge_span 74 / x_ignore_from 470（商品卡 x1≥476）。
  - 快手 720×1560：rect **[0,770,720,1390]**；行距 55.0 / y_phase 13 / x_text 105 /
    badge_span 74 / x_ignore_from 470。
  - 快手 1968×3150：rect **[0,1600,1968,3000]**；行距 79.9 / y_phase 54 / x_text 155 /
    badge_span 110 / `x2_from=content` / x_ignore_from 1400。
- 三处末段直播在录制文件结束前就已结束（之后是「已结束/推荐直播」页，广告卡会被 OCR
  读成匿名评论）。已用 PyAV **stream copy** 无损截到最后一帧直播画面：
  - `out/…/media/wx_morn_10-39-11-08_live.mp4`（21.25min；原 29min）
  - `out/…/media/wx_eve_15-41-16-02_live.mp4`（19.89min；原 21min）
  - `out/…/media/ks_eve_15-55-16-02_live.mp4`（6.79min；原 7.23min）
- `overlay_cards` 按本批补：
  - 视频号：广告卡 `点击了解详情` / `广告` / `HER`（+ skip_phrases）；
  - 抖音：顶部 `主播` 小标签（OCR 读成 `土描`）`^J?\s*主播$|^土描$`；
  - 快手：`送出人气票` / `送出小可爱` / `送出粉丝团灯牌`、空白礼物计数 `^\s*0+\s+0+\s*$`、
    `来自搜索|来自开播提醒`。
- `parse.calibrate=false`（同 09-05~09-07：系统行会让自标定行距翻倍）；
  快手 `emoji_workers=1`（1968×3150 省内存），抖音 `emoji_workers=8`。
- 验证：三份 `livepipe check` 全过；13 份 `panels.json` 已写出；三份 `--dry-run` 全过
  （parse 命令逐段正确展开，视频号/抖音/快手分别指向 5/4/4 段素材；表情表命中
  wechat/douyin/kuaishou 三张已建好的表）。**尚未跑真实短切片**——下一位接手先按
  STATUS 下一步抽行，或先跑一小段确认。
- 认人口径：三平台昵称基本不打码 ⇒ `account_key="username"`（与 09-05/06/07 一致）。

🚀 下一步：全量抽行（13 段合计 ~10.4h 录屏）——
`$P -m livepipe run 09-09猎鹰智驾_抖音.json --only panels,parse`，快手/视频号同；
再 `NLP_API_KEY=... run … --only filter,qualify,export,collect`。
⚠ 外置盘路径：`/run/media/puyu/MyDisk/…`，跑之前确认盘已挂载。

---

## 2026-11-09 · 猎鹰智驾瑞虎9羌塘无人区（渠道）2026-09-07 — 新建三份配置 — 完成

✅ 新配置（仓库根）：`09-07猎鹰智驾_抖音.json` / `09-07猎鹰智驾_快手.json` / `09-07猎鹰智驾_视频号.json`

- 素材：`/home/puyu/work/data/2026-09-07猎鹰智驾瑞虎9羌塘无人区（渠道）/`
  - 抖音 6 段（上午 3 段 720×1560 + 下午 3 段 480×1068，共 319.3min）
  - 快手 6 段（上午 3 段 480×1068 + 下午 3 段 1968×3150，共 314.7min）
  - 视频号 6 段（上午 3 + 下午 3，592×1280，共 325.5min）
- 输出：`out/2026-09-07猎鹰智驾瑞虎9羌塘无人区（渠道）/{抖音,快手,视频号}`；18 份面板已落盘。
- 量到的版式（2026-11-09，每文件 4 帧，四档分辨率全部重测）：
  - 抖音 720×1560：行距 53.0 / x_text 96 / rect [0,770,620,1380] / x_ignore_from 470
  - 抖音 480×1068：行距 **35.6** / x_text **66** / badge_span 36 / rect [0,550,480,960] / x2_from=content
  - 快手 480×1068：行距 **36.8** / x_text **95** / badge_span 72 / rect [0,490,480,960] /
    x_ignore_from 350（右侧礼物文字 `优秀优秀优秀` x≈355）
  - 快手 1968×3150：行距 **79.9** / x_text 155 / rect [0,1600,1968,3000] /
    x2_from=content / x_ignore_from 1400（右侧 `一起自驾游` x≈1418）
  - 视频号 592×1280：行距 41.5 / x_text 34 / badge_span 44 / rect [0,740,592,1128] /
    wrap_indent / fan_badge_x_max 32 / x_ignore_from 350（下午场广告卡 x1≥352）
- 视频号末段 `18：49-19：00.mp4` 原长 65.8min，直播在 **737s** 结束，之后 ~53min 是
  「直播已结束」推荐页（小米 SU7 广告卡会被 OCR 读成匿名评论）。已用
  `ffmpeg -i 原文件 -t 738 -c copy` 只保留直播段，副本
  `out/2026-09-07猎鹰智驾瑞虎9羌塘无人区（渠道）/media/wx_eve_18-49-19-00_live.mp4`。
- 快手下午场文件夹写 `16：30-19：00`，但第一段 `14：30-17：23.mp4` 实测从 14:30 录起
  （配置头注释已注明）。
- `overlay_cards` 在 09-06 基础上补：快手 `大咖粉` / `老铁加粉丝团` / `来自短视频`；
  视频号 `直播已结束|推荐直播|前往直播广场`；三平台继续带纯拉丁/数字行过滤（键盘行）。
- 验证：三份 `livepipe check` 全过；18 份 `panels.json` 已写出；短切片真实解析
  抖音 720 20 条 / 抖音 480 21 条 / 快手 480 18 条 / 快手大档 21 条 / 视频号 7 条 /
  视频号末段（裁后）14 条，用户名干净、系统行/键盘行被丢弃；解析自检 193 项全过。
- 认人口径：三平台昵称基本不打码 ⇒ 与 09-05/09-06 猎鹰三平台一致 `account_key="username"`。

🚀 下一步：全量抽行（六段合计 ~5.3h/平台）——
`$P -m livepipe run 09-07猎鹰智驾_抖音.json --only panels,parse`，快手/视频号同；
再 `NLP_API_KEY=... run … --only filter,qualify,export,collect`。

---

## 2026-11-09 · 猎鹰智驾瑞虎9羌塘无人区（渠道）2026-09-06 — 新建三份配置 — 完成

✅ 新配置（仓库根）：`09-06猎鹰智驾_抖音.json` / `09-06猎鹰智驾_快手.json` / `09-06猎鹰智驾_视频号.json`

- 素材：`/home/puyu/work/data/2026-09-06猎鹰智驾瑞虎9羌塘无人区（渠道）/`
  - 抖音 4 段（午场 2 + 晚场 2，720×1560，共 167.7min）
  - 快手 3 段（午场 2 段 720×1560 + 晚场 1 段 1968×3150，共 139.0min）
  - 视频号 4 段（午场 2 + 晚场 2，592×1280，共 166.5min）
- 输出：`out/2026-09-06猎鹰智驾瑞虎9羌塘无人区（渠道）/{抖音,快手,视频号}`；11 份面板已落盘。
- 快手晚场 `17：15-18：11.mp4` 带 **rotation=90 显示矩阵**，且开头 ~258s 画面侧躺；
  已用 `ffmpeg -ss 258 -display_rotation 0 -i … -c copy` 做无损修正副本
  `out/2026-09-06猎鹰智驾瑞虎9羌塘无人区（渠道）/media/ks_eve_17-15-18-11_rot0.mp4`
  （50.8min，两处都写进配置头注释）。
- 量到的版式（2026-11-09，各文件 8 帧）：
  - 抖音 720×1560：行距 53.0 / x_text 96 / rect [0,780,620,1380] / x_ignore_from 470（同 09-05）
  - 快手移动 720×1560：行距 55.0 / x_text 105 / rect [0,770,720,1380] / x_ignore_from 470
  - 快手大档 1968×3150：行距 79.5 / x_text 155 / rect [0,1560,1968,3000] /
    **`x2_from=content`**（全局 `frame_width` 只能是 720，window 会把画面裁在 720）
  - 视频号 592×1280：行距 41.5 / x_text 34 / badge_span 44 / rect [0,670,592,1128] /
    wrap_indent / fan_badge_x_max 32（09-05 是 1080×2400，数字全部重测）
- `overlay_cards` 按本批补：快手闪光语录面板（标签页 + 推荐话术）、键盘/UI 字母行、
  `页面加载中` / `看播满10分钟` / `送票助我上榜`；视频号卡片标签 `通知/主题/公众号`、
  分享图标 `口`、`++`；三平台加纯拉丁/数字行过滤（输入框打开时键盘落进 rect）。
- 修 `parsing/ocr/overrides.py`：显式 `x_ignore_from: null` 时 `float(None)` 崩溃
  （CONFIG.md 本来就允许 null；已补 buglog）。
- 验证：三份 `livepipe check` 全过；15 份 `panels.json` 已写出；短切片真实解析
  抖音 14 条 / 快手移动 10 条 / 快手大档 18+23 条 / 视频号 15 条，用户名干净、
  系统行/键盘行被丢弃；解析自检 193 项全过。
- 认人口径：三平台昵称基本不打码（0.5%~0.8%）⇒ 与 09-05 猎鹰三平台一致保持
  `account_key="username"`（没有开 badge_reread）。

🚀 下一步：全量抽行（三段合计 ~7.7h 录屏）——
`$P -m livepipe run 09-06猎鹰智驾_抖音.json --only panels,parse`，快手/视频号同；
再 `NLP_API_KEY=... run … --only filter,qualify,export,collect`。快手大档药丸读数率低
（5.6%），但本批按 `username` 认人，不影响交付；若以后改用等级认人需先开 `badge_reread`。

---

## 2026-11-09 · 撞库池子改为「全部评论账号」— 完成

✅ 用户纠正：**池子不按正面评论，按全部评论**。

- 原实现：`load_stream_accounts` 只读 `seg*/users.jsonl`（filter 产物，≥1 条正面），
  只发负面/中性评论的人撞不出来 —— 10.31 抖音池子 1633，实际评论账号 2204。
- 新实现：`sources.load_seg_accounts` 优先读 `seg*/comments.jsonl`（parse 产物），
  用 `user_filter.group_accounts` 按 (昵称, 等级标) 重建（排除词 / `identity_panels` /
  `identity_masked` 从段 `users.json` 抄，与 filter 同口径）；标签按平台
  `judgments.jsonl` 回填（缺的按中性）。无 comments.jsonl 的老产物退回 users.jsonl 并告警。
- `render` 同步：零正面的命中按「正面/中性/负面」全部标签现补图（只取正面会一张都挑不出）。
- 新增 `match --video-map 旧前缀=新前缀`：素材盘目录被整理过（10.31 视频现在在
  `A1_正式录屏及产出/10.31`，summary.json 还指着旧路径），补图前先重映射，实测出图正常。
- 自检 92 → **106 项**（池子重建、负面计数、缓存缺失、认人面板、视频路径重映射）。
- 10.31 全量重跑（686.9s）：池子 **抖音 2204 / 视频号 104 / 快手 166**（原 1633/67/95）；
  命中名单仍 **52/67**；命中账号 **84 → 87**（快手 +4 个零正面：3 个 `快手用户…` +
  `闪闪`；同时 `快手用户1879898007208` 被默认 `--max-matches 5` 挤出）；
  仅直播命中 35 → 38；标记图 107 个账号目录 / **1677 张** / 260MB（现补 58 个）；
  HTML 4 页（三平台 + 汇总）。
- 遗留：`标记图/` 里有 583 张 2026-11-06 的孤儿旧图（旧账号目录名，如 `63358-48`；
  当前 matches 不引用、HTML 不计数；工具文档本来就写明旧目录不自动删）。要干净的交付
  目录需删 `标记图/` 后重跑（本次未做）。

🚀 下一步：若某批素材换盘/挪目录，撞库补图记得带 `--video-map`；泛名前缀
（`快手用户`）命中多时可用 `--max-matches 0` 看全量。

---

## 2026-11-08 · 等级标二次读数按 `account_key` 自动绑定 — 完成

✅ 用户要求：`parse.badge_reread` 与认人口径绑定 —— `account_key` 用等级就开，只看昵称就关。

- 默认值改为 `parse.badge_reread: null`（`config/defaults.py`）：**不写这个键 = 自动绑定**；
  显式 `true/false` 仍覆盖（征服8 微博侧显式 `false`）。
- 判据：`config/panels.py:level_identity_declared(cfg, seg)` —— 该段面板文件
  （`panels.resolved.json`）里任一面板的 `account_key ∈ {nickname_level,
  nickname_level_if_masked}`，或平台档案里这么声明 ⇒ 开。读不到面板文件就只看平台档案。
- `config/args.py:build_parse_args` 解析 `None` 走该判据（逐段，`seg` 传入）；
  `pipeline._step_parse` 日志注明「未写时按 account_key 绑定」。
- 补救工具同步：`tools/remedy_badges/cli.py` 原先直接把 `None` 当关（改阈值时会静默
  不补读），现在逐段用同一判据印出实际开关；`doc.py` 补自动绑定说明。
- 自检：`config/panels.py` 新增 `selftest()`（7 项，纯配置、不读盘）；解析 193 /
  account_id 38 / `livepipe check` 全过。
- 验证：A9L 抖音（`nickname_level`）三段 dry-run 均带 `--badge-reread`；
  A9L 视频号 / 猎鹰抖音（`username`）均不带。

🚀 下一步：若以后有新平台用 `nickname_level`，**什么都不用写**，parse 会自动开二次读数；
只有「认人口径用等级、但视频/药丸语料确实不需要补读」这种例外才显式写 `false`。

---

## 2026-11-08 · 等级标二次读数：修 `咕___-7` 与 `咕___◆4-44` 假拆分 — 完成

✅ 用户确认 `咕***` 的 7 条评论是同一个人；导出图里能清楚看到 `◆4 咕***`。

- 根因：**等级药丸 OCR 漏读**（`badge_text=None`），不是认人判据错。交付按
  (昵称, 等级标) 认人 ⇒ 漏读的 7 条自成一档 `(咕***, "")`。
- 修：`09-05风云A9L渠道_抖音.json` 打开 `parse.badge_reread: true`（原先默认关）——
  对没读到药丸的行按正文框裁药丸带、只跑识别、跨帧 ≥2 票补回，**只补不改**。
- 复算：`run --only parse,filter,qualify,export,collect`（从 rows.jsonl，不走 OCR 抽行）。
  三段补回 82+24+19=125 条；自证一致率 98.0% / 100% / 98.8%。
- 结果：`咕___◆4-50`（44+6，另 1 条仍无读数但不够 3 条）；`咕___-7` 消失。
  无等级目录 20 → 8，其余 12 个假账号并入真等级账号；交付树 **96 个账号 / 793 张图**。
- 剩余 8 个无等级目录（`N***` / `魂***` / `小***` / `用***` / `c***` / `10双***` /
  `11万***` / `15&***`）经导出图核对：**屏幕上确实没有粉丝团药丸**（不是漏读），
  `(昵称, "")` 档就是正确结果。
- 自检：跨段 28 / 解析 193 / account_id 38 全过。

🚀 下一步：其它按 (昵称, 等级标) 认人的配置（抖音 App 那类）也应默认开
`parse.badge_reread` —— 药丸在屏幕上但 det 漏读是常态（本批 14% 唯一评论），漏一条就多一个
`(昵称, "")` 假账号。

---

## 2026-11-08 · 打码名认人修正：可见部分逐字相同 + 等级标一致 — 完成

✅ 用户报的两个交付树问题已修并重跑：

- `all_users_正面_3张以上/小___` 里混进别的匿名用户（`健***`/`爱***` 等 51 个名字被并进
  `小***`）；`木___-5` 与 `木___◆15-11` 是同一个人却没并。
- 根因：① 打码名走了「相似度 ≥0.7 + 共用正文 ≥2」的模糊通道 —— 打码名只差第一个字时
  相似度≈0.75，模板化直播任意两号共用文案是常态，等于把陌生人并成一个账号；
  ② `15木***` 被剥成 `木***` 时前缀 `15` 没补回 `level`，5 条落进 `(木***, "")`。
- 判据（用户 2026-11-08 定，逐字纠正两次）：打码名**可见部分（星号前第一个字）逐字
  100% 相同**才算同名；星号个数读错不算差异（`小***`/`小**` 同名）；名字对上后**两边
  等级标都读到且一致**才并。形近/大小写/共用正文对打码名一律关闭。
- 代码：`account_id.visible_name()`；`merge_segments/align.py` 打码分支 +
  `promote_prefix_level`；`parsing/text/badges.py` 段内同口径；`qualify` 按
  `users.json.identity_panels` 开关补等级。
- 自检：跨段 28 项、解析 193 项（新增 4 条打码回归）、account_id 38 项全过。
- 重跑 `--only qualify,export,collect`：**103 个账号**；`木___◆15-16`、`健___◆7-18` 出现，
  `木___-5` 消失；8 个 `小___◆*` 目录全部只含 `小***`；rename 仅 11 条数字药丸前缀
  + 1 条 `大**→大***`。
- 配置更正：A9L 抖音保持 `nickname_level`（99% 行打码；`大大` 是 `大***` 的 OCR 残渣，
  放开会并错），A9L 视频号/猎鹰三平台保持 `username`（0%~0.2% 打码）。
- 文档：README「匿名怎么配置」+ CONFIG.md `account_key` 小节补判据；`.wolf` 同步。

🚀 下一步：其余平台/批次若交付树里还有打码名，按同一条判据核一遍（跨段 rename 只应有
「数字药丸前缀」和「可见部分相同的星号变体」两类）。

---

## 2026-11-08 · 交付认人：匿名 / 非匿名分开，匿名按等级标一致 — 完成

✅ 新口径 `account_key="nickname_level_if_masked"`（平台档案开关）

- 用户要求：直播账号得分**匿名与非匿名**；**匿名的等级标得一致**，不能用现有逻辑。
- 判据：昵称**末尾星号**（`爱**` / `用***` / 全角 `＊`）= 打码/匿名 ⇒ 身份 =
  **(昵称, 等级标)**，等级标一致才算同一个人（读不到等级自成一档，不赌）；
  **不打码昵称**（`微风许甜`）= 实名 ⇒ 身份只按昵称（OCR 等级读错不会把人拆开）。
- 代码：`account_id.py` 新增 `KEY_NICKNAME_LEVEL_IF_MASKED` / `is_masked()`，
  `level_of(comment, split_panels, masked_panels)`；`config.panels` 新增
  `masked_identity_panels()`（`identity_panels()` 同时包含两口径）；`config.args`
  填 `identity_masked`；`user_filter` 新增 `--identity-masked` 并透传
  `group_accounts`；`remedy_badges`（check_cache / new_level_index）同步。
- 文档：CONFIG.md account_key 小节（三档 + 为什么需要）、user_filter doc、README。
- 配置：本批 5 份 09-05 配置（猎鹰智驾 抖音/快手/视频号、风云A9L渠道 抖音/视频号）
  已设 `account_key=nickname_level_if_masked`，`panels.resolved.json` 已重写。
- 验证：`account_id` 自检 32 项、跨段归一 15 项全过；合成用例（爱** ×3 档、
  微风许甜 ×2 档）三种口径账号数 2 / 5 / 4 —— if_masked 只拆打码名；
  真实 60s 切片（A9L 抖音 / 猎鹰视频号）跑通；老配置（10-31 等）口径不变（空表）。

🚀 下一步：全量抽行（5 份配置）→ filter/qualify 会自动按新口径拆账号；
老批次若要同口径交付，需要把对应配置改成 `nickname_level_if_masked` 后重跑
`--only parse,filter,qualify,export,collect`（会改变名单，先确认）。

---

## 2026-11-07 · 猎鹰智驾瑞虎9羌塘无人区（渠道）2026-09-05 — 新建三份配置 — 完成

✅ 新配置（仓库根）：`09-05猎鹰智驾_抖音.json` / `09-05猎鹰智驾_快手.json` / `09-05猎鹰智驾_视频号.json`

- 素材：`/run/media/puyu/A1B7-2982/A1_正式录屏及产出/2026-09-05猎鹰智驾瑞虎9羌塘无人区（渠道）/`
  · 抖音 3 段（720×1560，60.2+60.1+49.1min，账号「奇瑞汽车」）
  · 快手 3 段（720×1560，60.1+62.8+49.5min）
  · 视频号（内部录屏）2 段（1080×2400，60.4+114.3min；seg1 文件名写 55 分钟、实测 60.4）
- 输出：`out/2026-09-05猎鹰智驾瑞虎9羌塘无人区（渠道）/{抖音,快手,视频号}`；8 份面板已落盘。
- 实测（16+12+8 帧，证据写在配置头注释）：
  · 抖音 douyin_app：行距 53.0 / x_text 96 / x_ignore_from 470（卡左沿 475）/ 续行 0.66~0.76
    ⇒ wrap_gap_ratio 0.85；rect `[0,724,620,1358]`（分界 717、输入栏上沿 1362）；
    主播开输入框的「闪光语录」面板进 overlay_cards（seg3 t≈1030s 实测）。
  · 快手：行距 55.0 / x_text 105 / x_ignore_from 470（右侧浮层 `这款车设计充` 495）/ 续行 0.78
    ⇒ wrap_gap_ratio 0.85；rect `[0,726,720,1378]`（分界 718、输入栏上沿 1384）；
    `点点赞` 按钮与开播反诈提示进 overlay_cards。
  · 视频号 1080×2400：行距 81.7 / x_text 64 / badge_span 80 / wrap_indent / 续行 0.78~0.82
    ⇒ wrap_gap_ratio 0.88；粉丝药丸 x 53~58 ⇒ fan_badge_x_max 60 + with_level；
    rect `[0,1250,1080,2136]` —— **y1 抬到列表顶部 ~70px 渐隐带之下**（1178~1248 的行只渲染
    半截，OCR 读成 `天江丁工描` 类碎片；y1=1184 试跑 60s 混进 3 条，抬到 1250 后 0 条）。
- 验证：check 三份全过；60s 试跑 —— 抖音 16 条（药丸前缀 `11霸气娘们`→`霸气娘们`）、
  快手 7→9 条（`点点赞` 假行已消）、视频号 10→7 条（渐隐带碎片清零、`+1小芒果`→`小芒果`）。
  残留：无干净见证的药丸前缀（`17真爱无价` / `+2天**`）与快手药丸簇并进昵称
  （`8美丽团王姐（四川）`），与既有语料同现象。

🚀 下一步：全量抽行（三平台各约 2.8~2.9h 录屏）
`HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /home/puyu/anaconda3/envs/vlx/bin/python -m livepipe run <config> --only panels,parse`
之后 `NLP_API_KEY=… --only filter,qualify,export,collect`。

---

## 2026-11-07 · 风云A9L+OTA升级直播（渠道）2026-09-05 — 新建两份配置 — 完成

✅ 新配置（仓库根）：`09-05风云A9L渠道_抖音.json` / `09-05风云A9L渠道_视频号.json`

- 素材：`/home/puyu/work/data/2026-09-05风云A9L+OTA升级直播（渠道）/`
  · 抖音 3 段（720×1568 / 720×1560，55.5+20.1+23.5min，账号「风云新能源」）
  · 视频号（内部录屏）4 段（1206×2622，1.0+55.7+13.3+30.7min）
- 输出：`out/2026-09-05风云A9L+OTA升级直播（渠道）/{抖音,视频号}`；面板已按 manual 落盘。
- 实测（22+12 帧，报告写在每个配置头部）：
  · 抖音 douyin_app：行距 53.5 / x_text 96 / badge_span 74 / x_ignore_from 460（卡左沿 477）
    / 续行间距 0.76 ⇒ wrap_gap_ratio 0.85；rect seg1/2 `[0,728,620,1372]`、
    seg3 `[0,724,620,1394]`（视频区/分界 721/717，输入栏上沿 1374/1396）。
  · 视频号 wechat_app：行距 76.7 / x_text 72 / badge_span 97 / wrap_indent / 续行 0.78
    ⇒ wrap_gap_ratio 0.85；rect `[0,1372,1206,2372]`（分界 1365、输入栏上沿 2374）；
    主题卡/公众号卡进 skip_phrases + overlay_cards（OCR 常把 `风云新能源>` 的 `>` 吃掉）。
- 验证：`check` 两份全过（面板已生成）；抖音 seg2 前 60s 解析 32 条、视频号 seg2 前 60s
  6 条（开场卡片多）；续行测试（视频号 t=285~310 / 抖音 seg3 t=290~310）均正确合并，
  药丸前缀归一正常（`1无心人不动情`→`无心人不动情`、`11百***`→`百***`）。
  残留：少数药丸前缀无干净见证（`17用***` / `7冥***`），与既有语料同现象。

🚀 下一步：全量抽行
`HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /home/puyu/anaconda3/envs/vlx/bin/python -m livepipe run <config> --only panels,parse`
（两平台各约 1.7h 录屏；中断重跑走 rows.jsonl 复算）。之后 `NLP_API_KEY=… --only filter,qualify,export,collect`。

---

## 2026-11-07 · 跨段账号归一（+1八重樱 / 八重樱）— 完成

✅ `qualify` 合计前新增跨段归一：相似度 ≥0.7 入围 + 前缀形状/正文证据复核

- 用户指出 `+1八重樱-10` 与 `八重樱-6` 是同一个人，且「相似度在 0.7 以上得再比对一遍」。
  根因是跨段：同一个人在某一段只有「药丸并进昵称」的读法，段内别名归一（只取证相邻帧）
  没有干净版可对照 ⇒ 交付树两个目录。
- 新模块 `tools/merge_segments/align.py`，`qualify` 读 `users.jsonl` 后、按合计判定前跑：
  `strong` 药丸（`+1`/`★1`/`￥1`/`￥63358`）直接认；`digit`（`7用户…`/`11义节`/`163358`）
  要共用正文或等级标；`weak` 标点（`-`/`~`/`？`）**不算药丸**，须「只差一个字符 +
  共用正文≥2」；另有大小写、形近两类；等级标不一致硬闸。改名整批跨段应用。
- `selftest.py` 15 项断言通过；解析层原有自检 189 项仍通过。
- 10.31 三平台重建：视频号 **30 账号 / 573 图**（`+1八重樱-10`+`八重樱-6`→`八重樱-16`，
  `+1无量塔姬子`→`无量塔姬子-15`）；抖音 **114 / 1271**（`11义节`→`义节-40`、
  `8一路顺风`+`路顺风`→`一路顺风-38`、`7用户6758305`→`用户6758305-56`）；
  快手 **36 / 668**（`163358`/`￥63358`→`63358-57`、`）0176`→`0176-11`）。
- 首版把 `-`/`~`/`？` 也当药丸，`-风-`(23) 差点并进 `风-`(1)；已收紧并记 buglog。
  `￥` 单独算强药丸（快手金币徽章）。证据键方向修正（日志不再是「链式」）。

🚀 下一步（未做）：① 交付副本 `/home/puyu/交付/10.31/视频号/…` 仍是旧树（含
`+1八重樱-9`、`W-3`、`W-5`），要刷新需确认（副本里还有 `交付清单.html`/`summary.json`
不是管线生成，整棵覆盖会冲掉）；② `-风-`/`一风-` 相似度 0.667 低于 0.7 口径，
若要并需先定「一/- 互读」规则；③ 旧配置（08-08CTCC 等）下次跑会自动用上新归一，
其现有交付树未重建。

---

## 2026-11-07 · 撞库 HTML 交付清单 + 补图重跑去重 — 完成

✅ 每次 `match` 默认写 `撞库清单/<日期>_<场次>_<平台>.html`（多平台另有 `汇总.html`）

- 用户要求「方便人看的 html 格式」，参考
  `/home/puyu/交付/撞库格式/20260821_成都车展发布会_抖音-奇瑞汽车.html`。
- 新模块 `tools/list_match/html.py`：一平台一页，5 个 KPI（直播账号 / 命中账号 / 占比 /
  交付树截图 / 撞库截图）、人数与图片两条占比条、可搜索 + 点表头排序的命中表（含
  名称匹配分类、评论/正面、在交付树、账号目录、首图链接）、未命中复核表、口径页脚。
- 场次/日期自动：在 `cwd/*.json` 找 `output.dir` 指向本平台目录的配置，用其 `name`；
  10.31 得到 `20261031_奇瑞电感智行·大美中国_{抖音,视频号,快手}.html`。
  `--title` / `--date` 可强制；`--no-html` 关。
- 清单不嵌图（258MB），只给「首图」相对链接到 `../标记图/…`；`--no-images` 时无链接、
  截图数按 manifest 估算并在页内提示。
- 顺手修掉一个真 bug：补图用「文件已存在」决定加不加段名前缀，重跑会把同一段的图
  再复制成 `seg1_1.jpg`（赵丽丽 2→4 张、抖音总数 689→699）。改成按本轮 `owners`
  判断 + 本轮首次写前清旧图；重跑后 1526 张、689 张恢复正确（见 buglog）。
- 自检 92 项（新增文件名/转义/首图链接/汇总/未命中表断言）；HTML 内 86 个相对链接
  全部可解析；`google-chrome --headless` 截图核对版式与 JS 序号。
- 10.31 最终：抖音 32 账号 / 689 张 / 采集图 1256；视频号 26 / 443；快手 26 / 394；
  合计 84 账号 / 1526 张（交付树全部 2484 张 / 1795 个直播账号）。

🚀 下一步（未做）：HTML 里同一名单名命中多个账号时会连出多行（如 李将成 3 行），
可按名单名做视觉分组（需要处理排序后的分组边框）；清单目前不含缩略图，若要「一眼看图」
可加可选的首图缩略图列（会显著增大 HTML）。

---

## 2026-11-06 · 撞库改为「直播全量账号」— 已完成并在 10.31 全量验证

✅ 默认撞**直播里全部账号**（发过一条正面评论就算），`--delivered-only` 退回只撞交付树

- 用户纠正：「不是找出结果的，是找直播里全部的，有一个评论也出撞库结果」——交付树只收
  ≥3 张，名单上只评论过一两条的人会被误报成未命中。
- 账号宇宙 = 各段 `<平台>/seg*/users.jsonl` 并集 ∪ 交付树；同一人以交付树身份为准
  （保留 `dir`），仅在直播的记 `in_delivery=False`。`load_stream_accounts` /
  `merge_accounts`（`sources.py`）。没有 `seg*/users.jsonl` 的旧产物自动退回并告警。
- 仅直播命中没有现成图：新增 `render.py`，逐段把这几个人喂给 `export_user_images`
  按交付口径现补（整帧 + 绿框、顺序解码，bug-264），落同一个 `标记图/`；
  跨段同名文件加 `segN_` 前缀不覆盖。
- 全量账号放大了 `partial` 误撞（`天后`↔`明天后天`、`一路顺风`↔`顺风` 都 1.0）：
  前后缀多余那段是汉字就只按整体 ratio；中间片段只对 ≥3 字短名做窗口比。
- 10.31 实测（阈值 0.8）：抖音 20/23（32 账号 / 仅直播 8）、视频号 16/22（26 / 8）、
  快手 16/22（26 / 8）⇒ **52/67 人、84 个命中账号、24 条仅直播命中**；
  `标记图/` 87 个账号目录 / 1526 张 / 258 MB（交付命中 60 + 现补 27）。
  抽查 `赵丽丽`、`在干嘛呢`、`飞奔的鹌鹑`、`派大星星` 的现补图：590×1280 整帧、绿框在位。
- 全量命令 426s（补图是大头，单段 18~67s）；`--no-images` 仍只秒级出表。
- 自检 80 项全过（新增合并/仅直播命中/前后缀防误撞/临时目录复制）。
- 顺手删掉 `score_norm()` 里 `return` 之后的死代码（旧窗口循环，永远跑不到）。

🚀 下一步（未做）：`快手用户` 这类泛名前缀会撞到任意 `快手用户<长ID>`；如需要可加
coverage 惩罚（会改变现有阈值语义，先不默认开）。交付树外的命中现在要花时间从视频补图，
批量名单大了会慢 —— 可考虑补图并发或缓存。

---

## 2026-11-06 · 撞库（名单 × 交付账号）— 新功能完成

✅ `python -m livepipe match <名单> <直播路径> [--threshold 0.8]` + `livepipe/list_match.py`

- 用户需求：拿活动方名单（`撞库名单/10.31人名单.xlsx`）去撞已生成的交付树（`out/10.31`），
  给一个匹配度阈值（0.8），输出谁被拍到、分数、图在哪个目录。
- 新包 `livepipe/tools/list_match/`（`common` 归一化/相似度 · `sources` 名单与交付树读取 ·
  `matching` 平台内匹配 · `report` json/csv/xlsx/README/复制 · `cli` · `selftest`），
  薄壳 `livepipe/list_match.py`；`python -m livepipe` 新增 `match` 子命令。
- 名单：xlsx/csv 平台表头下面（`--layout column`，本批 xlsx 就是这种）或右边（`row`）是人名；
  `.txt` 一行一个 + `--platform`。按平台各撞各的。演示的 xlsx 表头行是
  `序号|抖音|序号|视频号|序号|快手`，名字在**同一列下方**——第一版误按「右边」读，已改成
  auto 判断（非纯数字多的一边）并留 `--layout` 强制。
- 匹配度：名字 NFKC + casefold + 去空白/零宽；`--metric partial`（默认）= 整名 ratio 与
  「短名逐窗口 ratio」取大（容忍 OCR 截断：`期待`↔`期待sj`、`章衡9116`↔`章衡`、
  `喜乐哥`↔`喜乐哥554`、`👑63358`↔`63358`）；`ratio` 只比整名。单字名单不做窗口匹配。
- 产出：`<直播路径>/撞库结果/{matches.json, 命中明细.csv, 未命中明细.csv, 撞库结果.xlsx,
  README.md}`；**标记图默认导出**到 `标记图/<平台>/<名单名>/<账号目录>/1.jpg …` ——
  交付图**原样**复制（整帧原图 + 评论绿框，不裁、不加字），只是按名单姓名归档；
  `--no-images` 只出表。10.31 实测 60 个账号目录 / 1495 张 / 258 MB，1.2s。
- 10.31 实测（阈值 0.8 / partial）：抖音 18/23 命中、视频号 15/22、快手 15/22，合计
  48/67 人、60 个账号；未命中 19 人逐条留在 `未命中明细.csv`（带最近账号与分数，如
  `搂你作业`↔`楼你作业` 0.75、`德莉莎`↔`德丽莎` 0.667）。
- 需要复核的边界：`超级喜欢可乐（搓甲版）`↔`（健身版）` 0.818 命中；`快手用户` 命中两个
  `快手用户<id>` 账号（partial 对长后缀给 1.0）——报告全部列出，阈值/`--max-matches` 可调。
- 自检 65 项（含一次临时目录的标记图复制；图片原样、非图片不复制、重复命中只导一次）；
  `export_user_images` 既有自检 52 项仍过；只出表时 0.2s。
- 文档：`livepipe/README.md`（工具 + 撞库小节）、`livepipe/全流程说明.md` §7、根 `README.md`。

🚀 下一步（未做）：标记图现在是「按名单名归档的命中账号图」，还没有「未命中也要交付」的形态；
若要让 `快手用户` 这类泛名前缀不撞到任意长 ID，可加 coverage 惩罚（默认保持标准 partial）。

---

## 2026-11-06 · 同一个人被拆成两个账号（W-3/W-5、期待si-3/期待sj-26）— 已修复并重建交付树

✅ 新 opt-in 平台键 `username_alias` + 三段零 OCR 复算 + filter/qualify/export/collect 重建

- 用户报：交付树里 `W-3`/`W-5`、`待si-3`/`期待sj-26` 是同一个人（`期待si` 那条是评论
  没完全滚上来时拍的）。相邻档还有 `李素`/`李素熵`、`一个诚实的老.`/`..`/`...`。
- 根因：OCR 检测框切掉昵称**末尾** 1~2px 或整个末字 —— `W.` 的句点、`李素熵` 的末字、
  `期待sj` 的 `j` 降部被框切 1px 读成 `i`（同一份像素换个框高就读不同：1343 vs 42）。
  交付层按昵称原文字符串认人（`account_id.account_key`）⇒ 一个人两个目录、各自算
  「≥3 条正面」而掉名单。按单帧选代表治不了，只能按账号级多数写法归一。
- 修复：`parsing/text/badges.py` 新增 `normalize_username_alias` / `_tail_variant`；
  证据 = 同 rid 组两种读法各 ≥2 次 + 只差一个尾巴（多/少 1 末字、≤3 纯标点、等长 ≥4
  字换末字）+ 等级标硬闸；主写法 = 面板内行数多数；整块面板统一改写，跑在
  `normalize_badge_alias` 之后、`dedup_comments` 之前。接线：overrides / config.panels
  白名单 / config.args（auto 模式）/ CLI `--username-alias` / comments.input /
  parse_live_comments 导出；`10-31_视频号.json` 开 `"username_alias": true`；
  `livepipe/CONFIG.md` 新增同名小节。selftest +12（roi_alias ⑥a~⑥j），全套通过
  （ROI/相邻守卫 189 项）。
- 三段复算：seg1 `W`→`W.`（68/192）、`一个诚实的老.`→`..`；seg2 `期待si`→`期待sj`
  （42/704）、`期待s`→`期待sj`（3/704）、`李素`→`李素熵`（19/200）、`+.12昙缇先生W`→
  `+.12昙缇先生`；seg3 `李素`→`李素熵`（34/301）、`陈女士再睡一..`→`...`。
- 交付树已重建：32 个账号 / 562 张图；`W-8`（原 W-3+W-5）、`期待sj-28`、`李素熵-13`。
- 残留（另一套机制）：行首药丸在**每条拍摄都并进正文**时没有干净见证 ⇒ `+1八重樱-9`
  与 `八重樱-6`、`+1无量塔姬子-6` 与 `无量塔姬子-9`、`+1吖完蛋了-3` 与 `吖完蛋了-14`
  仍分开（`badge_alias` 的 rid 组几何通道看不见；面板级几何实测差 32~43px，可另开
  通道，但跨段证据不足时仍会残留，待定）。
- 已知取不到的档：末尾是橙色装饰字形（`宁远1869∞`），OCR 在 `W`/`0`/漏读间摇摆
  （8 vs 15 选到 `0`）；账号已合并，只是交付名里那个装饰字不稳定。
- 2026-11-06 续：用户报「其他平台也有这个问题」⇒ 14 份配置的平台档案全部开
  `username_alias`（douyin_app / kuaishou / weibo / wechat_app），`livepipe check` 全过。
  10.31 抖音/快手零 OCR 复算：抖音 39 组、快手 9 组（`15云时`/`15云`、`@晓物`/`@晓`、
  `淇淇她哥叫老昊`/`老吴`、`63350`/`63358`、`毛球不是球?`/`毛球不是球`，同句同时刻证据
  已核对）；两棵交付树已重建（抖音 119 账号/1256 图，快手 39 账号/665 图）。
- 跨平台残留（另一套机制，未做）：① 行首药丸在整段没有干净见证时不并，跨段又各算各的
  ⇒ 抖音 `11义节`(seg1/3) 与 `义节`(seg2)、`8一路顺风` 与 `一路顺风`；快手
  `163358` 与 `63358`。② 字符级误读（`山药西施`/`山药四施`、`自己的`/`自已的`、
  `提交了`/`提父了`）按设计不救。

## 2026-11-05（续 2） · seg1 t=4637 标注图「第 5 条圈住两条评论 / 摸鱼的西红柿多处错」— 已修复并重抽验证

✅ seg1 代码+配置修复、全段重抽+零 OCR 复算均完成并核对；🚀 seg2/seg3 需同样重抽

- 用户报 `out/10.31/视频号/seg1/frames/04637.0_roi.jpg`：第 5 条把 `+7派大星：…很用心`
  与 `摸鱼的西红柿🍅：在泉州开车逛古城…合适` 圈成一个 ROI；摸鱼的西红柿同时有
  「用户名没切出来」「`合适` 被读成 `合活`」「`合适` 单独成匿名评论」多处错误。
- 根因（三处独立缺陷叠在同一帧）：
  1. `_indented_continuation` 拿 `open_rec["text_box"]`（**并集**）的左沿当缩进基准。
     并进贴左沿折行（`很用心` x1=21）后并集左沿被拉低，下一条真评论（x1=34）看起来
     右移 13px、间距 34px=0.83 行距（< `_INDENT_MAX_GAP_RATIO=0.85`）⇒ 被并进来。
  2. 续行 `合适` 左沿 33~37，与首行昵称 34~36 重叠，`wrap_indent` 形状带 [−20,30)
     接不住：落下一整数槽 ⇒ 匿名评论；与首行同槽 ⇒ `assemble_row` 按 x 拼成
     `摸鱼的西红柿合活在泉州…`。
  3. 昵称后夹表情 🍅、冒号被 OCR 吃掉：像素冒号检测因**下点 2×1（面积 2<3）**凑不成
     点对，t=4630.9 帧下点 V 仅 78~86（< v_min=100）；即使找到，`masked_only` 也挡住
     未打码昵称补字，而字宽等分会被表情宽度带偏一个字。
- 修复：
  - `layout/roi.py` + `layout/paragraphs.py`：`_indented_continuation` 增加 `base_box`
    （首行框），基准 = max(首行左沿, x_text)；续行合并不改它。
  - `10-31_视频号.json`：平台档案加 `wrap_at_text_left=true`（续行走间距软入口）。
  - `parsing/ocr/colon.py`：点面积闸 3→2；`insert_colon_by_pixels(blocks=…)` 在
    ≥24px 块缝里按**块边界**插「：」；配置加 `colon_v_min=80`。
  - selftest +5（roi_colon 3 / roi_wrap 2），全套通过（ROI 守卫 173→178）。
- 切片验证（真实视频重抽）：t=4632~4636 `+7派大星` ROI [0,1016,590,1065]、
  `摸鱼的西红柿` ROI [0,1076,590,1126] content=`在泉州开车逛古城，走走停停正 合适`，
  `一缕阳光` 独立 —— 第 5 条不再跨条；用户名由像素冒号补出。
- seg1 全段已重抽（6340s，rows 备份在 `seg1/.backup-20261105/`）：
  唯一评论 271→268，用户名未识别 46→23，ROI 高 >51px 的并块 3→2（重抽后那 2 条是
  真两行评论，不是并块）；t=4637 的标注图从 6 条变 7 条，第 5 条只圈 `+7派大星`。
- 重抽后又发现**去重层**的遗留：最早拍到的帧里折行 `合适` 还没滚进可视区 ⇒ 最终记录
  被截断。`dedup.py` 新增 `_content_consensus`（同一条评论的全部拍摄按「行数档 + 正文
  多数决」选，等长闸挡住吞进来的系统行），selftest 44→46。零 OCR 复算后 7 条正文改进
  （补全折行 / 去系统行 / 纠 OCR 抖动），同句重复 269→268。
- 最终核对：`comments.jsonl` 267 条、`摸鱼的西红柿` = `在泉州开车逛古城，走走停停正 合适`
  （lines=2，t=4631.06，ROI 与 t 同源）；`frames/04637.0_roi.jpg` 7 个独立框。
- 下一步：seg2/seg3 仍是旧 rows，需移走 `seg*/rows.jsonl` 后 `--only panels,parse`
  重抽；三段齐了再跑 `--only filter,qualify,export,collect`（要 NLP_API_KEY）。
- 已知残留（OCR 误读，非布局）：`派大星` 的 `品牌持色`（应为「特色」）在多帧都读错；
  若要做品牌词纠正，需要单独的文本纠正表，不在本轮范围。

## 2026-11-05（续） · 视频号交付图「框圈进下一条评论」— 自标定把行距抬到 62，已改配置并验证

✅ 配置修复 + 70s 切片验证通过；⚠ 全量需重抽（旧 rows.jsonl 的槽位已写死）

- 用户报 `out/10.31/视频号/all_users_正面_3张以上/符华-35/30.jpg` 的框圈进下一条评论，
  其他账号也有。统计：交付 827 图里 **288 张（34.8%）** ROI 高 >51px，涉及 28 个账号。
- 根因：视频号配置开着 `parse.calibrate=true`，标定按**每段开头 4 帧**估行距；seg2/seg3
  开头评论稀疏（首行间距 62/101.5/39/61.5/63/41.5，中位 62）⇒ 行距 41.0 → **58.6/60.4**。
  行距大于真实值 ⇒ 相邻评论（实际 ~41px）落进同一行槽，`assemble_row` 拼成一句、ROI 取并集。
  反解 rows.jsonl 拟合：seg1 41.02 / seg2 58.63 / seg3 60.41；并块率 1.1% / 18.8% / 23.1%。
- 修复：`10-31_视频号.json` 改 `calibrate=false`；因 x_text=30 的「贴左沿」门槛是 −20，
  续行只能走缩进带 ⇒ 显式 `wrap_indent=true`、`wrap_gap_ratio=0.75`。行距 41.0 写死。
- 验证：seg2 两个 70s 切片（t=4100 / t=0）重跑 `--only panels,parse`，无行槽并块，
  ROI 高度 22~24px；`符华：保养保险都便宜…` 与 `小燕子：奇瑞造车很多年了…` 各自独立。
- 抖音/快手配置本就是 calibrate=false；抖音当前产物并块 0.4%/0.7%（正常）。
- 重建步骤（旧 rows 必须移走，否则 parse 只复算不重抽）：
  `mv out/10.31/视频号/seg*/rows.jsonl` 到备份目录 → `--only panels,parse`（全量 OCR）→
  `--only filter,qualify,export,collect`（要 NLP_API_KEY）。seg1 污染只有 1.1%，但为了
  三段一致，建议三段一起重抽。
- 注：用户 2026-11-05 把视频号 `panels.manual` 的 rect y1 从 640 改到 730（避开顶部进房
  通知），配置里保留了这个改动；代价是评论区最上面 ~2 行会被切掉。

## 2026-11-05 · 新素材 10.31（奇瑞「电感智行·大美中国」三平台）— 配置已写、60s 试跑通过

✅ 完成（三份 JSON 配置 + 合法性校验 + 各平台 60s 切片试跑）

- 用户给了新素材目录 `/run/media/puyu/A1B7-2982/10.31`（抖音/视频号/快手 各三段，全部
  590×1280，同一台手机），要求「写对应的 json 配置」。
- 产出三份配置（仿 08-08CTCC 三件套，逐平台实测重写几何）：
  `10-31_抖音.json` · `10-31_视频号.json` · `10-31_快手.json`。
  输出根目录 `out/10.31/<平台>`，交付树 `out/10.31/<平台>/all_users_正面_3张以上`。
- 量到的几何（2026-11-05，seg1 各六帧 OCR + 像素统计）：
  · 抖音 douyin_app：row_pitch 44.0 / y_phase 33 / x_text 86 / badge_span 74 /
    rect [0,585,575,1200] / x_ignore_from 380（讲解卡）。
  · 视频号 wechat_app：row_pitch 41.0 / y_phase 21 / x_text 30 / rect [0,640,590,1185] /
    x2_from window；overlay/skip 短语补 `电感智行大美中国`、`奇瑞汽车>`。
  · 快手 kuaishou：row_pitch 60.0（单行气泡 60 / 两行 105 = 2×45+15）/ wrap_gap_ratio 0.80
    （续行 45px = 0.75 行距，默认 0.73 会整类丢）/ y_phase 10 / x_text 105 /
    rect [0,640,590,1185]；overlay_cards 补 `^榜\d+$|^粉丝团$|送出N个人气票|打卡|亲密值|
    倒计时|风云榜单|距离上日榜|加入[，,]?来|来自(关注|推广)`。
- 试跑（各平台 seg1 t=1200~1260，`--only panels,parse`，vlx）：
  · 抖音 28 条唯一评论（修好标定问题后；见 buglog 2026-11-05），`13沉着` 独立成条；
  · 视频号 11 条（用户名未识别 2 条；`colon_text_fix_masked_only=true` 保持 08-08 口径，
    未打码昵称冒号丢失时宁可不猜）；
  · 快手 7 条（该分钟被同一用户的长昵称折行评论主导：`榜1烟比女好只伤肺不伤` 这类
    药丸并进昵称的污染是 08-08CTCC 已记录的已知残留）。
- ⚠ 未做全量抽取（三段合计 抖音 4.3h / 视频号 4.1h / 快手 5.1h）；跑法写在每份配置头部。
- 遗留：快手药丸簇并进昵称的读法仍会污染用户名（同 08-08）；视频号未打码昵称丢冒号时
  用户名会丢（内容仍在）。

## 2026-11-04 · 快手 seg1 标注图 00307.2 / 00117.3 / 00385.1：浮层卡与系统按钮被当评论

✅ 完成（配置修复 + 三段复算验证）；⚠ 16:11 用户另起全量重跑，产物重建中

- 用户报 `seg1/frames` 的 `00307.2` / `00117.3` / `00385.1` 有问题。看图 + 对齐 rows：
  ① `00307.2` 整帧被**送礼弹窗**盖住，卡上的 `主播拍了拍我的头像：美呆了！` /
     `抓住机会，和主播互动吧～` / `CHERY` / `1快币208快币` 全被当评论；
  ② `00117.3` 第 6 条是系统行按钮 `送票助我上榜吧去助力` / `去助力`；
  ③ `00385.1` 第 6 条是 `10人在点赞主播 去点赞` 的按钮。同类还有赞助商横幅
     `CHERY。技术奇瑞...关注` 系列（t≈964–1019，8 条匿名评论）。
- 根因：浮层卡判据只认**无冒号**行（`主播拍了拍…` 带冒号被切成「昵称：正文」）；
  弹窗/卡片文案每帧不同且列表不滚动，跨帧「滚了它没动」判据凑不出帧对。
- 修复：`08-08CTCC_快手.json` 档案 `overlay_cards` 增补实测短语
  （点亮粉丝团 / 送票助我上榜吧 / 去助力$ / 去点赞$ / 抓住机会 / 快币 / 免费票次日 /
  ^人气票$ / ^[xX×]\d+$ / 成为在线 / ^CHERY）；`skip_phrases` 加整行
  `主播拍了拍我的头像：美呆了！`。
- 验证（`--only parse` 零 OCR 复算三段）：唯一评论 584/613/286 → **561/577/285**，
  逐帧评论 9695→9532，junk 短语在 comments 命中 **0**；`00307.2` 该帧 0 条有效评论、
  不再出图；`00117.3`/`00385.1` 6 框→5 框（人工看图确认）。
  `--only filter,qualify,export,collect` rc=0，collect **41 账号 / 751 图**。
- ⚠ 16:11:55 用户在 pts/2 另起了一次全量 `run`（配置已含新短语，正在重建三段与交付树）；
  当前 `seg1/rows.jsonl` 是重抽中的半截产物，等它跑完再看产物。
- 遗留：seg1 t≈950–1035s 录屏切到了安卓桌面 / 微信 / 广告页，仍有 **272 条匿名垃圾评论**
  （无用户名，不进交付名单，但会占标注图）。要么剪掉该时间段的素材，要么加「非直播画面」
  闸（另做）。

## 2026-11-04 · 快手 seg1 没表情/没过滤：自标定行距翻倍 + 重抽 + 过滤链

✅ 完成（三段重抽 + 过滤 + 交付）

- 用户报 `out/…/快手/seg1/frames` 没有表情、没有过滤信息。
- 现场：`seg1/rows.jsonl` / `comments.jsonl` 的 `emojis` 全为空（表情表建好之前抽的）；
  同时三段 `users*.jsonl` / `judgments.jsonl` / `all_users_正面_3张以上` 都不在了 ——
  filter/qualify/export/collect 需要重跑。
- **第二个根因（新发现）**：`parse.calibrate` 自标定把快手行距从 **45.0 改成 90.8**
  （置信度 0.67 过闸）。快手评论之间夹着大量系统行（恭喜/送灯牌/加入），标定按「含冒号的
  评论首行」取中心，相邻首行正好隔一条系统行 ⇒ 行距翻倍。后果：
  ① 相邻两条评论被当折行续行（用户名粘连/丢失）；
  ② 表情高度按 `0.667×90.8≈60px` 扫，30px 的官方表情一个都认不到。
- 修复：`08-08CTCC_快手.json` 的 `parse.calibrate=false`（行距/相位/续行/浮层卡都已
  实测写死）；三段旧/半截 `rows.jsonl` 都备份成 `rows.jsonl.bak_pre_emoji_20261104` 后
  移走，整批重跑 `--only panels,parse`（vlx，带表情表；seg1 45min + seg2 50min +
  seg3 28min 量级）。
- 60s 窗口验证（`--no-calibrate`）：5 条评论、**4 条带表情共 11 个**
  （`[赞]×4 [爱心]×3 [玫瑰]×2 [捂脸] [奸笑]`）、用户名未识别 0（改前 3 条丢失/粘连）。
- 三段全量重抽完成（rc=0）：seg1 **584** 条 / 94 条带表情 **353** 个；seg2 **613** 条 /
  121 条 **391** 个；seg3 **286** 条 / 70 条 **243** 个。标注图已画黄色表情框
  （`00000.0_roi.jpg` 可见 `[赞]×4` / `[捂脸][奸笑]` / `[玫瑰]×2`）。
- `--only filter,qualify,export,collect` 完成（rc=0）：filter 合格账号 seg1/2/3 = 58/58/35；
  qualify 跨段合计 ≥3 条 → **41** 个；交付树
  `all_users_正面_3张以上/` = **41 个账号 / 754 张图**（manifest mismatch 0）；
  `judgments.jsonl` 390 行、各段 `users.jsonl` 58/58/35、`users_qualified.jsonl` 31/37/22。
- 标注图已带黄色表情框（`00000.0_roi.jpg` 可见 `[赞]×4` / `[捂脸][奸笑]` / `[玫瑰]×2`）。
- 遗留：seg1 用户名未识别 232/584（40%，多为纯表情/系统行/OCR 丢昵称，内容仍在）；
  Unicode emoji（👍❤️🔥）仍不认；文字 token（`[感谢]`/`[吡牙]`）原样留在 content。
- 三段都重抽（seg2/seg3 的旧 rows 也是今天坏标定下的半截产物：3579/1254 行、seg3
  没有 comments），避免只修 seg1 后交付树不一致。

## 2026-11-03 · 快手表情获取（live.kuaishou.com emojiPanel → 207 个官方表情表）

✅ 完成（建表工具 + 表资产 + 配置启用 + 60s OCR 窗口验证）

- 用户要求「快手的表情获取」。快手直播 Web 首页内嵌
  `pcLive.webConfig.emojiPanel`，是 **207 个 token→图片 URL**（`//ali2.a.yximgs.com` /
  `//bd.a.yximgs.com` 的 `bs2/emotion/*.png`，100×100）——这就是官方表情源。
- 新增 `livepipe/tools/kuaishou_emoji/` + 薄壳 `livepipe/kuaishou_emoji_table.py`：
  - 建表：`python3 livepipe/kuaishou_emoji_table.py --out out/kuaishou_emoji`
    （`--src` 可给另存的 HTML / panel JSON 离线建）；
  - 产物：`kuaishou-emoji.json`（byId/byToken/byName + thresholds）· `img/NNN_名称.png`
    · `sheet_01..03.png` 验收表；已建到 `out/kuaishou_emoji/`（207/207 下载成功）。
- 阈值是**实测**（CTCC 8.8 快手 App 590×1280、行距 45）：
  - `height_ratio=0.667`：官方 100×100 图被 App 渲染成 **30px 高**（30/45）；
  - `looks_like=0.82` / `min_score=0.83` / `min_margin=0.10`：真表情 NCC
    `[赞]` 0.94~0.96、`[666]` 0.845~0.868，纯文字最高 0.80；
  - `coarse_min=0.55`、`min_saturation=20`。
- 接入：`SOURCE_TABLES["kuaishou"]`、默认 `emoji.tables.kuaishou`、`emoji_tables` 步、
  `config/args.py`、`gallery` 报错提示、README/CONFIG 文档；
  `08-08CTCC_快手.json`：平台档案加 `emoji: "kuaishou"`，`emoji.enabled=true`，
  `tables.kuaishou.build.enabled=true`（跑 `--only emoji_tables` 可自动建/补齐），
  `steps.skip` 去掉 `emoji_tables`。
- 验证：`--only emoji_tables` rc=0（207 个，图缓存命中）；seg2 前 60s 带 OCR 窗口
  识别到 **4 条带表情 / 12 个**（`[火]×7`、`[赞]×5`），其中 **4 条纯表情评论**由
  `emoji_text` 补上正文；自检 173 项全过。

📌 已知残留

- **现有 rows.jsonl 是在表情关着时抽的**（`emojis` 为空）——只跑 `--only parse`
  不会补表情。要让现有三段产出带表情，必须重抽行：移走各段 `rows.jsonl` 后跑
  `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /home/puyu/anaconda3/envs/vlx/bin/python \
  -m livepipe run 08-08CTCC_快手.json --only panels,parse`（配置头估约 2.5h）。
- Unicode emoji（👍❤️🔥 系统键盘）仍不认；本表只覆盖快手官方表情图片。
- 部分评论里官方表情以**文字 token**（`[感谢]` / `[互粉]`）出现（录屏时表情图未渲染），
  这类 OCR 文本原样保留，`emoji_text` 为空。

🚀 下一步

- 需要现有快手交付带表情时：重抽三段 → `NLP_API_KEY=… --only filter,qualify,export,collect`。

## 2026-11-03 · 视频号 00020.1 第 1 条公众号卡不识别（新增 skip_phrases 整行跳过词）

✅ 完成（代码 + 配置 + 自检 + seg1/2/3 零 OCR 复算）

- 用户报 `out/11-…（8.8）/视频号/seg1/frames/00020.1_roi.jpg` 第 1 条（红框
  `? : 奇瑞汽车>`）应该不能识别；要求「配置里配置跳过词」。
- 现场：第一条是**公众号卡**——OCR 只读到 `奇瑞汽车>`（`公众号` 标签被吃掉），
  旧 parser 把它收成用户名空的「评论」；同类还有主题卡第二行
  `CTCC中国汽车场地职业联赛`（t=142.8）。第一行 `王**来了` 是进房系统行，本来就没收。
- 为什么不用 `strip_phrases`：它按**行尾后缀**剥，剥品牌名会误伤真评论
  （`我喜欢奇瑞汽车 → 我喜欢`）。新增 opt-in 平台键 **`skip_phrases`**：
  **整行（忽略空白）等于**配置里任一词才丢这一行；`小王：奇瑞汽车真不错` 不受影响。
- 落点：`platforms.wechat_app.skip_phrases`（`08-08CTCC_视频号.json`）=
  `奇瑞汽车>` / `奇瑞汽车` / `公众号奇瑞汽车>` / `CTCC中国汽车场地职业联赛` /
  `2026CTCC中国汽车场地职业联赛`。裸词 `奇瑞汽车` 本场实测只出现在底部卡片带
  （y≈1181、无冒号、socket 6），收进来把漏 `>` 的同一张卡也清掉。
- 代码：`paragraphs._is_skip_line` + `parse_live_rows(skip_phrases=)`；经
  `overrides._skip_phrases_panels` → `comments/input.py` → `comments/build.py` 透传；
  `config/panels.py` 合法键 + `CONFIG.md`。不写这个键的面板逐字节不变。
- 验证：自检 ROI 173 项全过（+4：整行丢卡 / 忽略空白 / 正文提及保留 / 不配置旧行为）；
  `panels,parse` 零 OCR 复算 rc=0（三段全跑完）；新 `00020.1_roi.jpg` 只剩
  `1 小兔子乖乖: 大家好啊`，公众号卡不再画框；seg1/2/3 最终 comments 235/363/220 条，
  其中 `奇瑞汽车>` / 裸 `奇瑞汽车` / `CTCC…` 评论 **0/0/0**。

📌 已知残留

- 交付下游（filter/qualify/export/collect）还没重跑：comments 少了两条卡片，
  如需更新交付树要再跑 `--only filter,qualify,export,collect`（NLP 步骤要 key）。
- `skip_phrases` 是**整行精确匹配**（忽略空白），不做模糊/正则；OCR 变体读法
  （如 `奇瑞汽车 ＞`、`公众号：奇瑞汽车`）要补进列表或交给 overlay/frozen 判据。

🚀 下一步

- 需要新交付树时：`NLP_API_KEY=… python3 -m livepipe run 08-08CTCC_视频号.json
  --only filter,qualify,export,collect`。

## 2026-11-02 · mock_scale 加入快手 App 平台（kuaishou_app）

✅ 完成（代码 + 配置 + 750 张回归 + 三条线小批试跑）

- 用户要求「代码得加入快手app平台」。快手交付树
  `out/11-…（8.8）/快手/all_users_正面_3张以上`（56 账户 / 750 图）此前在 mock_scale 里
  会被路径里的活动名「抖音」抢判成 `douyin`，且三条线都定位不了。
- 新增 `core/platforms.py` 档案 `kuaishou_app` + 三个 opt-in 字段（默认 0/False/False，
  其它平台逐字节不变）：
  - `needs_geom`：快手交付框是**多行融合框**，目标行不一定是第一行（750 张里 91 张不是）
    ⇒ 必须用 `comments.jsonl` 的 `colon_box` 定行。
  - `geom_row_from_colon`：有 geom 时把取样窗口纵向收到冒号那一行（±min(12, 0.8×冒号高)），
    丢弃带外的 badge/表情。
  - `bg_quantize=32`：卷轴底色是深色半透明层，精确众数被白字抢走（背景估成白色、
    墨迹 0.90）⇒ 按 32 分桶取众数桶均值。
  - 别名 `快手/快手app/快手直播/kuaishou/kuaishou_app`，最近段先命中。
- 接入三条线：`batch_run` / `batch_produce` 自动从交付树重建 geom（manifest + comments）；
  `expand_produce` 的 plan 把 `base_geom` 存进 job、出片透传；`platform_probe` 也走
  geom/冒号行/量化背景。新增 `expand/expand_produce.快手.config.json`。
- 验证：定位 **750/750 成功、750/750 冒号与上游 colon_box 对位**；mock 线 6/6 出片；
  扩展线副本 plan 750 定位/658 可用、need 465/planned 435（缺口 30 = 6.45%）、
  sample 4/4、run --limit 2 14/14、原图 md5 0 变；探针 8/8 判定「可以跑」。

📌 已知残留

- 扩展线有丸素材少（750 张里徽章可用 34 张）⇒ 缺口 6.45%，正式写回前要定口径。
- `榜N` / 粉丝团药丸被 OCR 并进昵称（livepipe 侧已知残留），mock 侧按目录名原样传递。
- 正文线（`batch_body`）会沿用同一份 geom/平台档，但本次未跑它的八道闸验收。

🚀 下一步

- 扩展线正式写回（需用户点头）：先处理 6.45% 缺口，再
  `python3 expand/expand_produce.py --config expand/expand_produce.快手.config.json --mode run --yes`。
- mock 线批量出片：`make_spec.py --source <快手语料根>` 生成名单，再 `batch_produce`。

## 2026-11-02 · 抖音 CTCC 8.8 标注三处问题 + 每场专用词配置

✅ 完成（代码 + 自检 + 正式三段复算 + 切片抽行验证）

- 用户报 `00160.1_roi.jpg`：第 3 条应为第 2 条第二行；第 8 条第二行未识别；
  第 9 条带商品卡 `16人已预约`。要求配置里能写每场专用词。
- 新增三个 opt-in 平台键（`08-08CTCC_抖音.json` 已开）：
  - `strip_phrases`：商品卡/浮层词按**行尾**剥（`孙正16人已预约`→`孙正`；
    整行是 `艾瑞泽8PRO` 时当作不存在；正文中间真提及不动）。
  - `merge_emoji_continuation`：空文本表情续行并入上一条；`昵称：` 与
    「正文列起头的短昵称+表情」改判为纯表情评论。
  - `emoji_gap_sweep`：抽行时在相邻两行空档补扫表情（**x 从面板左沿扫**：
    折行表情常缩进到徽章列左边，x_text 起扫会整行漏掉）。
- 顺带修 `_strip_suffix_phrases`「没命中也会削冒号」的 bug（会把不同用户评论粘成一条）。
- 验证：正式三段复算后 `00160.1_roi.jpg` 只剩 7 条评论（表情续行并入第 2 条 lines=3、
  第 9 条 `孙正` 干净）；seg1/2/3 残留专用词 0/0/1（seg3 为正文中间真提及）。
  200s 切片重抽行：t=160.141 的 `囍财` 行表情 2→4 颗（第二行 x=49~112 已补）。
  自检 169 项全过。

🚀 下一步

- 正式三段（seg1/2/3）的 `rows.jsonl` 还是修复前产物，标注图里第二行表情要等**重抽行**：
  备份/移走 `<段目录>/rows.jsonl` → `python -m livepipe run 08-08CTCC_抖音.json --only panels,parse`
  → 再 `--only filter,qualify,export,collect`（评论内容变了，交付要重出）。注意 seg1 视频有
  坏帧，解码恢复（`parsing/ocr/decode.py`）已接进抽行/画图/导出。

## 2026-11-02 · 新建 CTCC 8.8（鄂尔多斯站）三平台配置：抖音 / 视频号 / 快手

✅ 完成（配置 + check + 每平台 60s 窗口试跑）

- 素材：「11-2026-08-08抖音CTCC职业联赛（8.8）」下的三个平台目录，各 2~3 段、时间不重叠：
  抖音 720×1600 三段（55.1/60.6/32.9min）· 视频号 720×1600 三段（55.1/55.6/33.3min）·
  快手 590×1280 三段（54.6/60.3/33.1min）。
- 新配置（仓库根，一份配置一个平台）：
  - `08-08CTCC_抖音.json` —— `douyin_app`（沿用风云A9L 口径）：行距 53.5、x_text 96、
    badge_span 74、rect `[0,780,620,1420]`、x_ignore_from 460、colon_*、badge_alias。
  - `08-08CTCC_视频号.json` —— `wechat_app`（沿用风云A9L 视频号口径）：行距 50.6、x_text 30、
    rect `[0,780,720,1320]`、x2_from=window、fan_badge_x_max 100 + with_level、
    overlay_cards、colon_*。
  - `08-08CTCC_快手.json` —— **新档案 `kuaishou`**：行距 45.0、相位 3、x_text 81、
    badge_span 58、rect `[0,545,590,1135]`、x2_from=window、wrap_indent=true；
    **表情关**（out/ 下没有快手表情表）。
  - 三份都是 output.dir = `out/11-…（8.8）/<平台>`，collect.out 显式写到
    `<output.dir>/all_users_正面_3张以上`（避免 …/抖音/抖音/… 双平台名）。
- 量测证据（2026-11-02，每平台 10 帧 OCR）：行距/相位/x_text 见各配置文件头与行内注释；
  rect 边界实测（抖音输入框 1441~1477、视频号 `聊一聊` 1338~1372、快手 `说点什么..`
  1159~1188）。
- 验证：三份 `livepipe check` 全过；各平台从 t=540s 起跑 60s（`--only panels,parse`，
  /tmp/ctcc88_check2/）：视频号 14 条评论（0 空用户名、5 条纯表情）、抖音 12 条
  （0 空用户名、1 条表情、等级标 9/12）、快手 15 条（0 空用户名、5 条纯表情）。

📌 已知残留

- 快手：药丸簇（✦N / 粉丝团 / 榜N）**内联在行首、没有固定徽章列**，被 OCR 并进昵称的
  读法会污染用户名（`10粉丝团榜3灰太狼70947` / `榜3`）；现有 badge_alias 只认数字等级串，
  覆盖不到。表情表也没有 ⇒ 纯表情评论的内容丢失（行还在，正文为空）。
- 抖音/视频号：与既有语料同一类残留（药丸前缀名、顶部渐隐带丢昵称、长评论横向进卡片区）。

🚀 下一步

- 抽行（三份各跑，串行，合计约 7h）：`HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
  /home/puyu/anaconda3/envs/vlx/bin/python -m livepipe run 08-08CTCC_<平台>.json --only panels,parse`
- 筛+交付：`NLP_API_KEY=… -m livepipe run 08-08CTCC_<平台>.json --only filter,qualify,export,collect`

## 2026-11-02 · 抖音 A9L 全量跑完（seg1 坏段修复 + 交付树重出）

✅ 完成

- 用户要求「全量跑完」。最终：seg1 rows 13892 / t=3510s（100%）· 3707 条唯一评论；
  seg2 rows 3869 / t=1448s（100%）· 1180 条。交付树
  `抖音/all_users_正面_2张以上/`：**160 个账号 / 1475 张图**（manifest mismatch 0，
  每账号 2~63 张，中位 6）。
- 之前卡在 seg1 t≈955s 有三个叠加原因：
  1. **视频文件本身有坏 h264 段**（帧 42912 / t≈957s 起 `Invalid NAL unit size`）：
     顺序解码到这里 `cap.read()` 返回 False，抽行/画图/导出都把它当成「视频结束」。
     **这是真正的主因**——首跑和两次续跑都停在同一帧。
  2. **U 盘掉线一次**（`/run/media/puyu/A1B7-2982` 在 11-02 约 10:16 消失）：第二次续跑
     的中断叠加。
  3. **管线不续跑 OCR**：`config/paths.py:100` 只要 `segN/rows.jsonl` 存在就把该段当
     「零 OCR 复算」（`input.resume` 在这条路径上不生效）⇒ 直接重跑命令不会补 OCR。
- 修复：新增 `livepipe/parsing/ocr/decode.py`（`skip_bad` / `read_recovering`）——
  读失败时按 1→4→16→64→256→1024→4096 帧递增跳过坏段再继续；接进
  `ocr/sampling.py`（adaptive / interval / backoff 三条抽帧路）、`parsing/render.py`
  （标注图取帧）、`tools/export_user_images/frames.py`（导出原帧）。健康文件上 no-op，
  真到片尾照旧结束。实测 seg1 在帧 42912 / 43654 / 43699→43955 / 43977 四处触发恢复。
  自检 161 项全过。
- 续跑用的是 `/tmp/resume_seg1.py`（`build_parse_args` 后 `a.rows=None; a.resume=True`），
  绕开管线的「rows 存在 ⇒ 只复算」分支；跑完后 `--only filter,qualify,export,collect`。
- 质量：seg1 用户名 70%、冒号框 80%；seg2 用户名 78%、冒号框 87%。filter 合格
  seg1 447 / seg2 165，跨段合计 ≥2 条正面 160 个账号。

📌 已知残留

- 管线仍没有「检查点未到片尾就续跑 OCR」的语义（`input.resume` 只在 `--video` 路径生效，
  而配置管线见 rows 就切复算）。以后中途断了，要么用 resume 脚本，要么移走 rows.jsonl。
- 坏段附近（t≈957~980s）会跳过几帧；那几帧本来就解不出来。

## 2026-11-01 · 抖音 A9L seg1 t=80.1 第 2/3/4 行没冒号框（已修 + 已就地重画）

✅ 完成

- 用户报 `out/.../抖音/seg1/frames/00080.1_roi.jpg` 第 2、3、4 条（`冰***` / `哭***` /
  `顺***`）没有冒号框，「特别是冒号」。
- 现场：这三行是「◆N 等级药丸 + 打码昵称 + 冒号」被 OCR 并进**徽章块**（`badge_text`
  = `4冰***` / `7哭***` / `8顺***`），正文块从冒号右边开始（x1≈188，正常行 100~105）；
  `rows.jsonl` 里 `colon_box=None`。
- 根因：`render.refresh_colon_box` 有一条守卫「已经有用户名 ⇒ 不补框」。而 `badge_name_recover`
  会先把徽章块里的掩码昵称拼成用户名 ⇒ 用户有值、冒号框被挡掉。抽行那趟（video_rows 的
  `nick_in_badge` 左探）在当前代码下其实找得到冒号（同一帧实测 `[163,1168,173,1180]`），
  但用户看的是旧 rows 零 OCR 复算重画的图。
- 修法：`refresh_colon_box` 的守卫改成「已有用户名且**不是**掩码徽章才不补」；掩码徽章
  （`badge_text` 含 `*`）照旧走左探补框。自检 +2 项（161 项全过）。
- 已就地重画抖音两段（`--rows --video --out <段目录>`，零 OCR）：
  seg1 809 条里 703 条有冒号框（87%）、seg2 1180 条里 1031 条（87%）；
  `00080.1` 第 2/3/4 行都出现青框（`/tmp/dy_fixed_r234.png` 为核对图）。
- ⚠ 抖音全量 parse 还没跑完：seg1 rows 只到 t≈955s（58.7min 里约 16min），seg2 rows 到
  t≈?；后续 resume 会继续 OCR，新代码会一并生效。

🚀 下一步

- 继续跑完抖音两段：`HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /home/puyu/anaconda3/envs/vlx/bin/python -m livepipe run 风云A9L.json --only panels,parse`（resume 续跑）。
- 跑完后 `--only filter,qualify,export,collect`；注意本次重画只改了 `colon_box`，用户名没变，
  已有 `users.json` / `users_qualified.jsonl` 仍然有效（但全量 parse 后要重算）。

## 2026-11-01 · 视频号冒号：像素法整段短路 + 打码昵称 `*` 丢冒号（已修，待重跑交付）

✅ 完成（代码 + 配置 + 自检 + 窗口验证）

- 用户报 `out/.../视频号/奇瑞汽车/seg1/frames/00106.4_roi.jpg` 第 4/5/6 行（`6**：` /
  `苗苗*：` / `铠子*：`）没有冒号框、用户名丢（`苗苗*同价位很能打` 成匿名）。
- 根因：视频号评论气泡半透明浮在直播画面上，气泡底亮度中位 V≈115~165；像素冒号检测
  默认 `bg_max_v=110` **整段短路** ⇒ 三个视频号配置的像素法一次都没跑过，冒号框全靠
  OCR 文本插值；而 RapidOCR 对打码昵称后的 2px 冒号经常整块不读。
- 修法（默认行为不变，既有语料逐字节相同）：
  - 三个配置（`风云A9L_奇瑞QQ/奇瑞汽车/风云新能原.json`）加
    `colon_bg_max_v: 255` / `colon_v_delta: 30`；
  - `colon.py`：`_MASKED_TAIL_LOOSE_RE`（单星号）+ `insert_colon_by_pixels(masked_only=)`；
  - `video_rows.py` / `render.refresh_colon_box`：昵称块整块丢（正文块距 x_text ≥40px、
    无徽章文本）⇒ 左探补冒号框（只补框不补字）；文本无冒号且无左探证据时关掉右探 48px；
  - `config/panels.py` 允许键 `colon_text_fix_masked_only`；`CONFIG.md` 补说明。
- 验证：0–115s 窗口重跑（`/tmp/a9l_colonfix4`）：3 处补字全对（`苗苗*：`/`铠子*：`/`6**：`），
  此前通用等分补出的 4 处错误切分（`梁内饰：`/`平安边：`/`梁，后：`/`“梁，。：`）全部消失；
  新增 25 个冒号框只有 1 个在文字框外（正是 `6**` 左探目标）；t=106.4 标注图 4/5/6 行都有青框。
  自检 159 项全过（+5）；抖音配置 20s 冒烟 rc=0。

🚀 下一步

- 交付要更新：三段都要**重跑 parse（OCR）**，旧 `rows.jsonl` 里是旧文本/旧框，复算救不回来：
  ```
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /home/puyu/anaconda3/envs/vlx/bin/python \
    -m livepipe run 风云A9L_奇瑞汽车.json --only panels,parse   # 三个配置各跑一遍
  ```
  然后 `NLP_API_KEY=... -m livepipe run <配置> --only filter,qualify,export,collect`。
- 已知残留：`妍跑` / `平安` 这类「昵称后标点被 OCR 一起吃掉」的行，某一帧文本里没有冒号时
  仍可能丢用户名（其它帧读到了，但 dedup 留最早那帧）。要彻底修可在 dedup 加
  「同 rid 同位移时优先有冒号的那次」——影响全部语料，需全量 diff 后再动。
- `8PRO.json` / `风云T7.json` / `08-09CTCC.json` 同样没写 `colon_*`；若它们的底也偏亮可照抄，
  但要先看气泡底亮度（暗底则不必）。

## 2026-11-01 · 抖音「用户名空」补回 + 过滤句子加在哪

✅ 完成（代码 + 自检 + rows 复算）

- 用户报抖音产出「用户名仍然是空」。根因：OCR 把「等级药丸 + 掩码昵称」并成一块
  （`12小***`/`6许***`）按徽章列丢掉，正文没有冒号 ⇒ 切不出用户名；昵称只剩在 badge_text。
- 新增 opt-in 平台键 `badge_name_recover`：形状明确（可选 1~2 位等级 + **无数字**昵称 +
  ≥2 星，允许尾部 `.`/`·` 残迹）时把昵称拼回 `昵称：正文`；`121***` 这种等级/昵称首字
  都是数字的**歧义不猜**。抖音配置已开，视频号/既有语料不写此键 ⇒ 行为不变。
- rows 复算（零 OCR）结果：seg1 918→809 条、空名 420→223（46%→28%）；seg2 1274→1180、
  426→262（33%→22%）；t≈191.2 的 `接缝和用料上，都能看` 恢复为 `许***`，`121***` 仍留空。
  自检 154 项全过。
- 过滤句子的入口（回答用户）：① 无冒号的 UI/公告句 → 平台档案 `overlay_cards` 正则
  （匹配即丢该行，并丢紧跟的一行卡续行）；② 顶部通知与评论粘连 → `notice_prefix_strip`；
  ③ 系统动作行（赞了直播/关注了主播）→ 内置 `_NOTICE_RE`（改内置表要按项目惯例做语料 diff）；
  ④  **正文内容过滤**目前没有配置键 —— 需要新增（例如 `parse.drop_content`），要先拿到句子清单。

🚀 下一步

- 抖音交付要按新解析重跑：`--only filter,qualify,export,collect`（用户名变了，账号归并会变；
  判定缓存可复用大部分）。
- 视频号三账号若也要补用户名：在 `wechat_app` 档案加 `badge_name_recover` 后 rows 复算；
  注意视频号打码常只有 1 颗星，需先量 badge_text 形状再决定是否放宽 `\*{2,}`。

## 2026-11-01 · 视频号「两条评论并成一条」修复 + 风云新能原 seg1 重抽/交付

✅ 完成（代码 + 自检 + 单帧验证 + seg1 重抽 + 交付重跑）

- 用户报 `out/10-…/视频号/风云新能原/seg1/frames/00057.0_roi.jpg` 第 2 条：`恋恋：…`
  与 `和睦睦：…` 被识别成一条评论。
- 根因：平滑滚动帧逐帧相位偏半行距 ⇒ 两行吸进同一槽；`split_fused_rows` 本应拆回，
  但目标槽 k+1 坐着下一条的折行续行 `来蹲守`（y 差 0.64 行距），旧闸把它当「另一条行」
  ⇒ 整槽不拆。
- 修复：`livepipe/parsing/ocr/fused.py` 新增 `_is_wrap_occupant`（间距落折行闸 +
  块无冒号 + 最左块形状是续行）⇒ 续行顺延到小数槽 k+i+0.5，照拆；其余被占情形不变。
  `selftest/roi_grouping.py` 新增 2 条断言（含冒号线后自检 159 项全过）。
- 验证：0 起点复跑 seg1 前 60s（`/tmp/a9l_fusedcheck3`），旧 rows 该窗口 6 条高块
  （含 3 条融合行）全部消失；t=57.0 rows 变成 socket 2=恋恋 / 3=和睦睦 / 3.5=来蹲守。
- **seg1 已重抽 + 交付已重跑**（`_logs/fusedfix2_parse_*.log`、`fusedfix2_deliver_*.log`）：
  用的是合并后的当前代码（同仓另一条工作线的冒号像素法修复也在内：`colon_box` 像素小框
  840 → 9967）。seg1 18,588 行 / 2,166 条唯一评论；高块 318 → 231，两昵称融合 91 → 54，
  `恋恋+和睦睦` 融合 3 → 0。交付：**109 个账号 / 1,339 张图**（修复前 110/1349）。
  唯一减少的账号是并块产生的噪声名 `是靠组装+1扬帆起航`（应删）；恋恋/和睦睦都在名单里。
- 备份：`视频号/风云新能原/_backup_pre_fusedfix/`（修复前）与 `_backup_fusedfix_only/`
  （第一遍仅并块修复的产物）。

📌 已知残留

- seg1 仍剩 231 条高块，其中 54 条文本含两个「昵称：」：绝大多数是**目标槽坐着别的行、
  且文本语义可不可分**（如 `yu_oriens：…一直在` + `款款深情：…` + 续行 `线`），按
  「宁可漏拆不可粘错」保留；余下是个别单块跨行/表情列。
- seg2 与奇瑞QQ / 奇瑞汽车 **尚未重抽**（seg2 仍是零 OCR 复算的旧文本/旧框）；要同时带上
  并块与冒号两处修复，需同样移走各段 `rows.jsonl` 后重跑 `--only panels,parse` +
  `filter,qualify,export,collect`。

## 2026-11-01 · 抖音标注图「昵称+冒号并进药丸块」导致冒号不圈 — 已修并重画

✅ 完成

- 用户报 `out/.../抖音/seg1/frames/00191.2_roi.jpg` 冒号没识别到。根因：OCR 把「✦N + 昵称」
  并成一个**不含冒号**的块、按徽章列丢掉（`badge_text=121***`），正文块从冒号右边起，
  而像素找冒号的搜索框只向右放宽 48px，冒号落在两块之间的空隙里。
- 修复：`_COLON_PROBE_LEFT=48`；抽行时「药丸块带掩码昵称 + 文本无冒号」⇒ 向左扩搜索框并
  用正文左沿当 hint；`refresh_colon_box` 增加**补框**路径（只对显式写了 `colon_*` 的面板，
  阈值经 `_draw → draw_frames` 传入）；配置 `x_text` 96→81（rect 左沿 15 的裁剪内坐标）。
- 重算（`--only panels,parse`，rows 复算 3m21s）：seg1 284 条「无名+掩码药丸」补到 258 条
  冒号框、seg2 242 补到 203 条；`00191.2_roi.jpg` 重画后这些行都有青色冒号小框。
  自检 147 项全过。
- 残留：这些行用户名仍空（等级数字与打码昵称切分有歧义，不猜）。要让**未来**抽行避开，
  当前 `x_text=81` 已生效；重抽行需把 `<段目录>/rows.jsonl` 移走（`resume=true` 会直接复算）。

## 2026-11-01 · 视频号三个账号配置 + 全量跑（进行中）

✅ 完成（配置与验证）

- 新配置三份（仓库根）：`风云A9L_奇瑞QQ.json` / `风云A9L_奇瑞汽车.json` /
  `风云A9L_风云新能原.json`。素材：
  「/run/media/puyu/A1B7-2982/A1_正式录屏及产出/10-2026-08-07风云A9L年型车上市直播维护/视频号/<账号>/」，
  每个账号两段（720×1600），合计 QQ 67.4min · 奇瑞汽车 65.3min · 风云新能源 88.6min。
- 三个账号版式一致（33 帧实测）：行距 50.5、x_text 30、粉丝药丸闸 100（两簇 33~37 / 85~90）、
  评论区 rect `[0, 980, 720, 1385]`（顶=直播画面下沿，底=输入栏图标行之上）、
  x2_from=window、无商品浮层卡 ⇒ 不设 x_ignore_from。
- ⚠ 段顺序：文件名 `10：11…` 字典序会排在 `8：42…` 前 ⇒ `input.videos` 用显式数组按时间列。
- 修复版式差异：新增 opt-in 平台键 `fan_badge_with_level`（允许「等级药丸 + 粉丝药丸」一起
  并进正文，`+6粉丝若云：…`），默认关、既有语料不变；三个配置 fan_badge_x_max=100 + 该键开。
- 验证：check 三份全过；panels 已落盘；draw 出图 rect 正确；QQ 60s 重解析用户名零「粉丝」
  前缀；风云新能原 60s 全跑通；NLP 接口（deepseek-v4.1-flash）冒烟测试通过；自检 140 项全过。

✅ 全量首跑 + 修复复算都已完成

- **全量首跑**（01:35–04:50，rc 全 0）：交付树 QQ 78 / 奇瑞汽车 110 / 风云新能源 115 个账号。
- 审计发现两类假账号（通知粘连、双药丸前缀）⇒ 加两个 opt-in 平台键
  `notice_prefix_strip`（`开播请注意驾驶安[全金]`）与 `fan_badge_with_level`（等级 token
  前缀 0~3 个，含 `+.13` / `18+6` 变体），修了 `_strip_fan_badge` 覆盖已清洗文本的顺序 bug。
- **复算**（05:00–05:13，`--only parse,filter,qualify,export,collect`，零 OCR、filter 走缓存，
  新增判定 0/0/1 条，rc 全 0）后的最终交付：
  | 账号 | 交付账号 | 图 | 评论（空用户名） |
  |---|---|---|---|
  | 奇瑞QQ | 73 | 679 | 1524（41%） |
  | 奇瑞汽车 | 101 | 1199 | 2625（41%） |
  | 风云新能原 | 110 | 1349 | 3359（37%） |
  交付树里通知类/「粉丝」类假账号 **0**；名单、manifest、README 齐全。
- 自检 144 项全过；日志 `out/.../视频号/_logs/`（`run*.log` 首跑、`rerun*.log` 复算）。

📌 已知残留

- 评论里有 37~41% 用户名空（续行残片/系统行/OCR 丢昵称，不影响交付名单的账号归并）。
- OCR 把「上一条正文尾 + 下一条昵称」并成单块的融合行（QQ seg1 约 8 条），几何拆分无解。
- 交付名单里仍有少量药丸前缀/OCR 噪声名：QQ 5 个（`+.13灿烂` 等）+1 个数字名，奇瑞汽车 2+3，
  风云 2+2；`badge_alias` 只归一有等级见证的那些，建议人工过一眼名单。
- 既有 `8PRO.json` 有约 260 行同类 `+N粉丝…`；给它档案加 `fan_badge_with_level` + 调
  `fan_badge_x_max` 可修，但要先复算对照。
- `08-09CTCC.json` / `风云T7.json` 的 `filter.nlp.api_key` 里仍有明文 key，建议清成 null。

- 顶部 UI 通知可能与最上一行粘连成用户名（风云 seg2 见到
  `非主驾开播请注意驾驶安全1粉丝和和睦睦`）；量少，先交付时留意。
- 既有 `8PRO.json` 有约 260 行 `+N粉丝…` 同类行（用户名带「粉丝」前缀）；给它的
  wechat_app 档案加 `fan_badge_with_level: true` + 调 fan_badge_x_max 可修，但要先复算对照。
- `08-09CTCC.json` / `风云T7.json` 的 `filter.nlp.api_key` 里仍有明文 key，建议清成 null。

## 2026-11-01 · 新建 风云A9L（抖音）配置 + 冒号像素检测支持浅灰底

✅ 完成

- 新配置 `风云A9L.json`（仓库根）：素材
  「/run/media/puyu/A1B7-2982/A1_正式录屏及产出/10-2026-08-07风云A9L年型车上市直播维护/抖音/*.mp4」
  两段（58.7min + 24.2min，均 720×1600）。抖音手机 App 档案 `douyin_app`（与风云T7同族）。
- 实测（17 帧）：行距 53.5 · 相位 10 · x_text 96 · 药丸 44~96 / 昵称 100+ / 正文 182~193 ·
  评论文字右沿 ≤583 · 商品讲解卡左沿 477 ⇒ rect `[0,1000,620,1420]`、`x_ignore_from=460`。
- 试跑暴露并修复：浅灰气泡底让像素冒号检测整类失效（默认 bg_max_v=110），OCR 又常丢掉
  冒号 ⇒ 用户名丢失 53~59%。新增 opt-in 平台键 `colon_bg_max_v` / `colon_v_min` /
  `colon_v_delta` / `colon_text_fix`（默认值与旧行为逐字节相同），本配置取值
  255 / — / 30 / true。60s 复跑用户名未识别降到 29~46%。
- 验证：`check` 通过；`--only panels` 落盘两段 panels.json；draw 出图 rect 正确；
  60s 试跑产物在 `/tmp/a9l_validate4`（改前对照 `/tmp/a9l_validate2`）；
  自检 138 项全过。

🚀 下一步

- 全量抽行（约 4~5h）：`HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /home/puyu/anaconda3/envs/vlx/bin/python -m livepipe run 风云A9L.json --only panels,parse`
  （中断可直接重跑，`<段目录>/rows.jsonl` 自动续跑）。
- 之后 `NLP_API_KEY=... -m livepipe run 风云A9L.json --only filter,qualify,export,collect`；
  交付树：`out/10-2026-08-07风云A9L年型车上市直播维护/抖音/all_users_正面_2张以上/`。
- `风云T7.json` 里 `filter.nlp.api_key` 写着明文 key（注释说应留 null），建议清掉改成 null。

## 2026-10-31 · manual 模式「配置是唯一来源」落到代码 — 改配置直接生效

✅ 完成

- 上一轮只是把面板文件手动同步了一次；用户再改 seg2 rect 后画图仍旧 ⇒ 说明「配置驱动」没进代码。
- 代码改动：
  - `livepipe/draw_boxes.py`：`panels.mode=manual` 时直接画 `panels.manual`（来源显示
    `panels.manual（配置）`），面板文件落后时打警告；其他 mode 的面板文件优先级不变。
  - `livepipe/pipeline.py`：`_step_panels` 在 manual 下比对配置与面板文件，不一致就按配置重写
    （`overwrite=true` 无条件）；locate 的行为不变。
  - `livepipe/config/panels.py`：新增并导出 `manifest_drift`（只比配置写了的键，文件多出的
    覆盖键不算差异）；`write_manifest` 的 `_note` 按 mode 给不同文案。
  - 文档：`CONFIG.md` 的 overwrite/manual 说明；`08-09CTCC.json` 与 8PRO/风云T7/征服8/csj
    的 overwrite 注释。
- 验证：seg2 配置 `[0,880,740,1220]` 直接 draw，实测红框 y=880/1220、x1=0（旧值 100 消失）；
  `--only panels` 只重写 seg2，seg1/seg3 报「一致，保持不动」；reuse 分支仍读面板文件；
  内置自检（37+133+12+17+44+13 项 + 示例）全过。
- 记录：`.wolf/buglog.json`、`.wolf/memory.md`。

🚀 下一步

- 其余 manual 配置（8PRO / 风云T7 / 征服8）：改 rect 后可先 `--only panels` 把文件同步，
  不跑也行 —— draw 已按配置画，但 parse 等下游仍读面板文件。
- `csj.json` 的 `check` 会报 `panels.example.json` 不存在（本工作区本来就没有该文件，与本次改动无关）。

## 2026-10-31 · CTCC 面板改为「配置是唯一来源」— 已完成并用图验证

✅ 完成

- 现象：改 `08-09CTCC.json` 的 `panels.manual` 后重跑 `draw_boxes.py --config`，图不变。
  原因：config 模式的框来自磁盘 `out/12-…/segN/panels.json`（`_panel_source` 优先级最高），
  `panels.manual` 只是文件缺失时的兜底；`panels.overwrite=false` 又让面板步不刷新旧文件。
- 处理：旧三份 `segN/panels.json` 备份到 `out/12-…/_panels_backup_pre_manual/` 后删除；
  `vlx python -m livepipe run 08-09CTCC.json --only panels` 按配置重新落盘；再
  `vlx python livepipe/draw_boxes.py --config ./08-09CTCC.json` 重画 `panels/*.jpg`。
- 验证：新 `panels.json` 与 `panels.manual` 逐项一致（seg1 `[0,900,720,1220]`、seg2
  `[100,900,720,1220]`、seg3 `[0,850,720,1320]`）；重画图上红框实测 seg1 y=900/1220、
  seg2 x1=100、seg3 y=850/1320。
- 记录：`.wolf/buglog.json` 与 `.wolf/memory.md`。

🚀 下一步

- 以后改 rect 的固定动作：挪走/删掉对应 `segN/panels.json` → `--only panels` → draw。
- 若要复算评论产物，注意 `parse` 等下游应按新 rect 重跑（当前 seg 目录里只有 panels.json，
  尚无 rows/comments 产物）。

## 2026-10-31 · seg7 标注图「分隔冒号」青框大量标错 — 已修复并重画验证

✅ 完成

- 修复在 `livepipe/parsing/ocr/colon.py`（插值按字宽加权 + 像素点对接受插值 hint）与
  `livepipe/parsing/render.py`（`refresh_colon_box` 重画时用当帧像素重算并写回）。
  详见 `.wolf/buglog.json`。
- `out/风云T7_20260826_11_02/seg7` 已用修复后的代码复算重画：4216 张标注图；5598 条唯一评论
  除 `colon_box` 外与旧产物**逐字段一致**（等级标仍是 4160 条）。4633 个冒号框里 4461 个左移，
  中位 −17px；抽样 6 张标注图核对，青框都压在「昵称 | 正文」的分隔冒号上。
- 文档：`livepipe/docs/colon-fix-report.html`（自包含单文件：两张实测图 + 内联计算示意与
  位移分布；根因、改法、验证数据、复算命令都在里面）。

复算命令（缺一不可）：

```
/home/puyu/anaconda3/envs/vlx/bin/python livepipe/parse_live_comments.py \
  --rows out/风云T7_20260826_11_02/seg7/rows.jsonl \
  --panels-file out/风云T7_20260826_11_02/seg7/panels.resolved.json \
  --badge-reread \
  --video "/home/puyu/work/data/20-2026-08-26风云T7上市/抖音/风云新能源/17：20-18：19.mp4" \
  --out out/风云T7_20260826_11_02/seg7
```

- `--panels-file`：不传则平台档案缺失，`overlay_cards` 不过滤 ⇒ 唯一评论从 5598 变成 6124。
- `--badge-reread`：不传则等级标读数少 189 条（4160 → 3978）。
- 必须用带 rapidocr 的 `vlx` 解释器，否则在 dedup 前的二次读数处报
  `ModuleNotFoundError: rapidocr_onnxruntime`。

🚀 下一步

- seg1–seg6 的 `frames/` 若产出时间早于 2026-10-31 12:06，同样带着旧冒号框；需要时对每段用上面
  同一条命令复算（各自替换 `--rows` / `--panels-file` / `--video` / `--out`）。

决策

- 不手工改 `comments.jsonl` 里的 `colon_box`：走 `--rows` 复算，保证重画与落盘的是同一个框。

## 2026-10-31 · 8pro seg2 冒号框（柠檬* / 苗苗*）— 已修复重画

✅ 完成

- 旧产物（10-29）整段 146 个冒号框都是插值全高框：视频号灰冒号 V≈130~145 被旧亮度闸 150
  滤掉。用当前代码复算重画后，142/146 变成像素框（高 12~13px），柠檬* / 苗苗* 由
  `[115,1047,138,1074]` 收到 `[105,1057,113,1069]`。
- 剩余 6 行暴露第二个边界：2×2 灰点缺角后面积=3，面积闸写的是 <4。`colon.py` 已改为 `< 3`，
  `roi_colon.py` 加断言（133 项通过），重画后仅剩 4 个滚动中近不可见的点（V≈110、面积 1~2）。
- 149 条唯一评论除 `colon_box` 外零字段差异；users 等下游文件不受影响。

🚀 下一步

- 8pro 其余段（seg1/seg3…）若也是 10-29 产出，同样带插值冒号框；需要时按同样命令逐段复算。
