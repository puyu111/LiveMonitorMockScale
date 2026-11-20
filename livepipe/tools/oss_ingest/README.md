# `livepipe.tools.oss_ingest` —— OSS 入库工具

把「磁盘素材 / 本地结果」安全地接入 OSS（goofys 挂载）的一组小工具：
**配置→OSS 映射清单**、**只读审计**、**按内容同步**、**`.part` 收尾**、
**环境体检**。全部只用 Python 标准库，自检离线跑在临时目录里。

> **跑 livepipe 之前：素材必须先到 OSS。** `input.videos` 必须是
> `~/oss-bucket/...`（goofs 挂载）路径，不接受百度/bypy 路径。新素材先用
> `scripts/oss_download.py baidu`（或 `live`）直写 OSS，裁尾副本走
> `cp → .part → ingest`，再 `livepipe check`。`check` / `run` 不会自动下载，
> 缺文件直接报错。

```bash
python3 -m livepipe.tools.oss_ingest --help
python3 -m livepipe.tools.oss_ingest --selftest
python3 -m livepipe oss --help              # 同一套命令的 livepipe 入口
python3 livepipe/oss_ingest.py --help       # 薄壳脚本入口
```

## 子命令

| 命令 | 作用 |
|---|---|
| `manifest <config...> [--oss-root PATH] [--disk-root PATH] [--source-map JSON] [--out PATH] [--out-dir DIR]` | 为每份配置生成「磁盘素材 → OSS 对象」映射清单（OSS 索引整批只建一次） |
| `audit <config...> [--oss-root PATH] [--disk-root PATH] [--source-map JSON] [--report-only] [--out PATH]` | 只读审计引用数、全片命中、裁尾、缺失、本地缺失、失败配置 |
| `sync <src> <dst> [--sha256\|--no-sha256] [--delete-local] [--dry-run] [--workers N]` | 按大小同步目录树（`--sha256` 再比哈希，默认关闭），`.part` + `os.replace` 落盘 |
| `verify <src> <dst> [--sha256\|--no-sha256]` | 只读校验 `src` 是否完整同步到 `dst`（缺失 / 大小 / 哈希 / 多余对象） |
| `verify-media <path> [--allow-unknown-duration]` | 只读校验一个成品媒体的字节数与时长；探不到时长默认 `UNKNOWN` 且返回 1（确需只校验字节再加放行标志） |
| `ingest <part> [--src PATH] [--final PATH] [--force] [--expect-bytes N] [--expect-duration S] [--allow-no-duration]` | 校验完整性后把 `.part` 收尾成正式文件并写 `.manifest.json` |
| `check-env [--oss-root PATH] [--network] [--role all\|download\|compute\|review]` | 体检 python/bypy/goofys/fuse/挂载/凭据/ffprobe |
| `--selftest` | 离线自检（临时目录、无网络、无 FUSE） |

### 默认 OSS 根（自动选择）

未传 `--oss-root` 时，按下面的顺序选择**第一个「存在且至少含一个可用视频文件」**
的目录；空目录、只有结果目录、只有 0 字节文件或下载器临时名（`*.p.mp4` /
`*.baiduyun*`）的根都不算有效根。实际使用的根会打印在输出第一行，
`manifest` 还会写进 `generated_from.oss_root`。

1. `~/oss-bucket/直播data/录屏` —— **存量全片根**（覆盖 22 份配置的基线是
   82 引用 / 53 全片 / 27 裁尾 / 2 缺失）
2. `~/oss-bucket/web/1/奇瑞汽车/直播看管/直播录屏` —— 迁移文档的新契约（新录屏的
   裁尾副本统一进这里）
3. `~/oss-bucket/web/1/奇瑞汽车/直播看管` —— 旧兜底

⚠ 项目 OSS 根在 `web/1/` 下（2026-11-19 用户确认）。桶根 `~/oss-bucket/奇瑞汽车/`
是 P1/P2 早期写错的前缀，只保留到迁移复核完成。

工具级 `audit` 可以对着一个共同父根跑；`scripts/build_oss_inventory.py`
**故意按两个事件根分别建索引**（global 50 个可用视频 / 1019 30 个），避免两批
同字节视频落进同一候选池而互相误配。脚本会拒绝两个根相同或互相包含的调用。

显式 `--oss-root` 在默认 strict 模式下要求目录存在：路径写错会直接报
「显式 OSS 根不存在」，而不是把整批算成全量缺失；`check-env` 走非 strict，
仍会继续逐项诊断。

背景：存量录屏没有按迁移文档落到 `直播录屏`，而是留在 `直播data/录屏`；
旧的 `奇瑞汽车/直播看管` 只剩结果目录。三者都不满足时，`manifest` / `audit`
会直接报错并提示用 `--oss-root` 指定实际目录，而不是把空目录当成有效根。
`check-env` 为了能在「还没挂载好」的机器上继续诊断，会回退到第一个候选路径，
并在表里把 `OSS 挂载` 标成 FAIL。

`sync` / `verify` 的 `src` / `dst` 都支持 `~`。

退出码：`0` 成功；`1` 可预期错误、`sync`/`verify` 有失败项、`audit` 有缺失 /
本地缺失 / 坏配置（`--report-only` 可强制 0）、或 `check-env` 有 FAIL；
`2` 用法错误。错误统一打印 `[oss_ingest][error] ...` 到 stderr。

> `manifest` 只负责生成清单，**不设覆盖门禁**（有缺失仍返回 0）；要判定
> “是否全部映射”请用 `audit`（默认缺失即返回 1），P0 交付用
> `scripts/build_oss_inventory.py` 的硬校验。

## 常见流程

### 1. 新下载的录屏收尾

```bash
# 下载器留下 1001_wechat_12-43-14-00_0-2854.mp4.part
python3 -m livepipe.tools.oss_ingest ingest \
  ~/downloads/1001_wechat_12-43-14-00_0-2854.mp4.part

# 迁移文档里的 --src / 事件元数据写法也支持（等价于位置参数）：
python3 -m livepipe.tools.oss_ingest ingest \
  --src "$OSS/直播录屏/$EVENT/$DAY/$PLATFORM/$ACCOUNT/$NAME.mp4.part" \
  --event "$EVENT" --day "$DAY" --platform "$PLATFORM" --account "$ACCOUNT"
# → 改名成 .mp4，并写 .mp4.manifest.json
#   （bytes / duration_s / event / day / platform / account / finalized_at）

# 更严的完整性闸门：字节数 / 时长不符就拒绝，.part 原样保留
python3 -m livepipe.tools.oss_ingest ingest clip.mp4.part \
  --expect-bytes 3225832995 --expect-duration 4578.624
```

- 改名前**默认必须**探到有效时长（`duration_s > 0`）；ffprobe 缺失或读不出时
  拒绝并保留 `.part`，绝不把可能截断的半成品提成正式文件。确认文件本身完整、
  只是环境探不到时长时，显式加 `--allow-no-duration` 跳过该闸门
  （manifest 的 `duration_s` 记为 `null`）。
- 给了 `--expect-duration` 时**始终**要求有效时长：探不到就拒绝，因为无从核对
  期望值（`--allow-no-duration` 也不能绕过这种情况）。
- `final` 已存在且大小不同：默认拒绝，确认后加 `--force`。
- 完整下载链路的 wrapper 是 `scripts/oss_download.py`（`live` / `baidu`）：
  `live` 跑下载器；`baidu` 用 bypy 授权/列目录后按 PCS 单请求顺序流式直写
  OSS（不落本地暂存）。两条路都经由这里的 `finalize_part`，失败不留正式文件。
- manifest 默认不写 sha256（避免把 3GB 再读一遍）；需要时用 `sync --sha256` 校验。

### 2. 任务完成后把本地结果同步到 OSS

```bash
python3 -m livepipe.tools.oss_ingest sync \
  out/2026-10-01/直播产生结果 \
  ~/oss-bucket/web/1/奇瑞汽车/直播看管/直播产生结果 \
  --sha256 --dry-run          # 先看会复制什么
# 确认后去掉 --dry-run；要清空本地源再加 --delete-local
```

- 永不使用 mtime 判断；默认**只比大小**，加 `--sha256` 才在大小相同时再比哈希
  （`--no-sha256` 显式关闭，等于默认行为）。
- 跳过软链与 `*.part/*.lock/*.failed/*.tmp`（含裸文件名 `.part` 等）。
- 复制先写 `<目标名>.part`，校验后 `os.replace`；失败自动重试（默认 3 次）。
- 永久失败会在源文件旁留 `<名>.failed` 标记（含时间与原因），不阻塞其他文件。
- `--delete-local` 强制开启 SHA256，且只在目标校验通过后删源，并清理 `src`
  下空目录（不删 `src` 本身）。
- `--dry-run` 不写盘、不删源；`删除源` 列显示“会删除的源文件数”。
- `src` / `dst` 不能相同或相互嵌套（否则会把目标当源一路套娃复制）。

批量 / 任务集成的一行调用（迁移文档里的 `oss_sync` 名字）：

```python
from livepipe.tools.oss_ingest.sync import oss_sync   # == sync_tree
stats = oss_sync(本地结果目录, OSS结果目录)            # 默认只比大小；加 sha256=True 才比哈希
```

同步完成后需要一份可判定的结论时用：

```bash
python3 -m livepipe.tools.oss_ingest verify 本地结果目录 OSS结果目录
# 结论：PASS / FAIL（缺失、大小、哈希、多余对象）
python3 -m livepipe.tools.oss_ingest verify-media 某成品.mp4
```

### 3. 新机器 bootstrap → check-env

```bash
bash scripts/bootstrap_oss_env.sh --check   # 只读体检
bash scripts/bootstrap_oss_env.sh           # 安装 fuse3 / goofys / bypy（需 sudo 时自动用）
bash scripts/bootstrap_oss_env.sh --systemd # 打印 systemd unit（生产建议托管，避免 pts 断开断挂）
# 按脚本最后打印的命令挂载（脚本不会后台自动挂载）
python3 -m livepipe.tools.oss_ingest check-env
python3 -m livepipe.tools.oss_ingest check-env --network   # 额外只看 bypy Quota/Used
```

## 清单 schema

`manifest` 输出（`version = 1`）：

```json
{
  "version": 1,
  "config": "/abs/path/config.json",
  "generated_from": {"config": "...", "oss_root": "...", "disk_root": null, "generated_at": "..."},
  "segments": [
    {"seg": "seg1", "disk": "/abs/disk/a.mp4", "oss": "/abs/oss/批次/a.mp4",
     "trimmed": false, "source_oss": null, "oss_missing": false,
     "bytes": 3225832995, "duration_s": 4578.624, "trim_s": null}
  ]
}
```

- `disk` 默认是**绝对路径**；只有调用 API 时显式传 `disk_root`
  （CLI 的 `--disk-root`）才改成相对该目录的展示形式。
- `oss` 是**绝对路径**（与实现实际返回一致），不是相对 `oss_root` 的路径。
- 全片映射按**字节大小精确匹配**；同尺寸多候选时优先 ffprobe 时长差 ≤ 0.5s 的候选。
- 裁尾识别：路径含 `/_live/`、文件名以 `_live.mp4` 结尾，或 `/out/` + `/media/` +
  `_live`。裁尾段 `trimmed=true`、`oss=null`；**不臆造** `source_oss`。
- 裁尾段的 `trim_s` 优先取配置的 `start` + `duration`；配置没写时解析
  `_live` 文件名末尾的 `_<start>-<end>`（如 `_0-2854`）；两者都没有时用已探到的
  `duration_s` 回退成 `[start or 0, start + duration_s]`（10-19 的
  `dy_qq_16-31-17-00_live.mp4` → `[0, 5455.716]`）。
- 给了 `--source-map map.json`（`{"磁盘路径或文件名": "OSS 源对象"}`）才会填
  `source_oss`，用于 P1 补传时定位源录屏。
- 配置本身已指向 OSS 时直接 `oss=raw`，不再按大小搜；但**只有对象真实存在**
  才填 `oss`，否则留给 `missing` / `local_missing` 计数。

`manifest a.json b.json ...` 会一次性建立 OSS 索引并复用于所有配置，
不会为每份配置重扫挂载树。索引跳过 0 字节文件与下载临时名
（`*.p.mp4`、`*.baiduyun*`），避免对未完成半成品跑 ffprobe。

`--out-dir DIR`：每份配置各写 `DIR/<配置文件名去扩展名>.json`（单份配置也写同名
文件）；目录不存在时自动创建。`--out PATH` 仍然保留，用于把所有清单合并成
一个 JSON（单份时就是该清单本身）。两种输出可以同时使用。多份配置改名后同名时，
`--out-dir` 会在写盘前报文件名冲突并返回 1，避免互相覆盖。

`audit` 输出：

```json
{"configs": 22, "refs": 82, "full_mapped": 53, "trimmed": 27,
 "missing": 2, "local_missing": 0, "failed_configs": 0,
 "details": [{"config": "...", "ok": true, "refs": 4, "full_mapped": 3,
              "trimmed": 1, "missing": 0, "local_missing": 0,
              "error": null, "segments": []}]}
```

- `missing`：**所有**非裁尾、`oss` 为空的段——包括本地文件不可用
  （`bytes < 0`）的段，以及配置指向 OSS 但对象不存在（此时段上
  `oss_missing=true`）的段。素材盘没挂载时它会等于本地缺失数，审计不会在
  「什么都没映射」的情况下报 PASS。
- `local_missing`：任意段 `bytes < 0`（素材盘不可用 / 文件缺失，含裁尾段），
  作为独立诊断保留。只挂 goofys 的计算 / 审核节点上这个数会大于 0，
  `missing` 也会同步变大；这些机器应显式使用 `--report-only`。
- `failed_configs`：读不了 / JSON 坏的配置数；坏配置只进 `details[].error`，
  不中断整次审计。
- 默认门禁：`missing > 0` 或任一 `details[].ok == false`（坏配置 / 缺失 /
  本地缺失）返回 1，并打印 `结论：PASS/FAIL`；`--report-only`
  （别名 `--allow-missing`）可强制返回 0。`details[].ok` 要求
  `missing == 0` **且** `local_missing == 0`。
- 有裁尾段时，PASS 文案会写成 `结论：PASS（清单完成：…… N 段裁尾副本不在
  本次检查内）`：`audit` 只校验本地裁尾的区间与字节，**不代表裁尾副本已上传**。
  P1 补传后的落盘验收用 `scripts/build_oss_inventory.py --verify-uploads`
  （按 CSV 的 `dst` 核字节）。

## `.part` manifest schema

```json
{"version": 1, "part": "...mp4.part", "final": "...mp4", "bytes": 2005390060,
 "duration_s": 2854.414, "sha256": null, "finalized_at": "2026-11-17T10:00:00Z",
 "event": "...", "day": "...", "platform": "...", "account": "...",
 "trim": [0, 2854]}
```

（`event/day/platform/account/trim` 只在 CLI 传了对应参数时出现。）

## `check-env`

固定表头 `检查 | 结果 | 说明`，逐项检查：

1. `python3 ≥ 3.8`
2. `bypy`（`PATH` 或 `~/anaconda3/bin/bypy`）
3. `~/.bypy/bypy.json` 存在且 `0600`（**只查权限位，不读内容**）
4. `goofys`（`PATH`、`/usr/local/bin/goofys`、`~/goofys`）
5. `/dev/fuse`
6. fuse3（`fusermount3` 或 `libfuse3`；缺失仅 WARN）
7. `/etc/fuse.conf` 的 `user_allow_other`（缺失仅 WARN，但 `goofys -o allow_other` 需要它）
8. OSS 挂载：从 `oss_root` 逐级向上找挂载点，并用 `findmnt` /
   `/proc/self/mountinfo` 校验其 **fstype/source 是 fuse/goofys**；OSS 根不存在
   也判 FAIL（不再因为“任意祖先挂载”而误报 PASS）
9. `ffprobe`（可选，缺失仅 WARN）
10. `~/.aws/credentials` 存在且 `0600`

`--role compute|review` 时把 bypy 可执行 / 凭据缺失降级为 WARN（这些机器只读
OSS，不需要百度下载）；默认 `--role all` 仍是 FAIL。

`--network` 额外跑 `bypy info`（30s 超时），只摘出含 `Quota` / `Used` 的行，
绝不回显整段输出或凭据。任何 FAIL ⇒ 退出 1；WARN 不影响。

## 自检

```bash
python3 -m livepipe.tools.oss_ingest --selftest
# 自检 N 项全过 ✅
```

自检覆盖：`human_bytes` / `rel_to` / JSON 原子写 / `iter_files` 软链、
OSS 根自动选择（空目录 / `.part` 不算视频、显式覆盖、全无效报错与非严格回退）、
`finalize_part`（正常、异尺寸拒绝、`--force`、`write_manifest=False`、
无时长默认拒绝 + `allow_no_duration` 逃生阀）、CLI 退出码与 `prog` 透传、
`manifest --out-dir`、裁尾 `trim_s` 的时长回退、OSS 索引跳过 0 字节/临时名、
`check_env` 的 PASS/FAIL 表；并防御式聚合
`selftest_manifest.run(h)` 与 `selftest_sync.run(h)`（缺模块只跳过）。

## 边界（来自真实环境实测）

- goofys 0.24.0：写 9.4–12.5 MB/s、冷读 6.6 MB/s、小文件 10–13 个/秒、
  128MB `rename` 约 0.15s、`os.replace` 可用。
- 不支持软链（ENOSYS）、硬链（EPERM）；`utime` 被忽略（所以**不用 mtime**）；
  `statvfs` 报假 1PB；multipart 的 list/abort 返回 403。
- 因此同步以「大小 + 可选 SHA256」为准，落盘用同目录 `.part` + `os.replace`。

## 安全

- 只读凭据的**存在性与权限位**，不读取、不打印内容。
- 不写生产配置、不跑批次、不碰 `out/` 与 OSS 挂载内容；测试全在临时目录。
- `sync --dry-run` 与 `bootstrap_oss_env.sh --check` 保证零副作用。
