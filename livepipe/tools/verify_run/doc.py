"""``python -m livepipe verify`` 的完整说明。"""

DOC = """从盘上核对一次 `livepipe run` 的产物是否完整（只读，不重跑）。

    python -m livepipe verify <config.json> [--only a,b] [--skip c] [--json] [--strict]

看什么
------
按配置**实际要跑的步骤**（`steps.order` − `steps.skip`，再叠加 `--only/--skip`）逐步骤核对：

- `emoji_tables`：用到的表情表 / `build.enabled=true` 的表是否存在且非空。
- `panels`：面板文件存在、JSON 合法、每个面板有 `rect` 与 `platform`
  （`mode=locate` 还没人工填 platform 时只提示；parse 也在步骤里则算错误）。
- `parse`：`comments.jsonl`（JSONL 每行合法、条数 > 0）、`summary.json`
  （`unique_comments` 与盘上行数一致、`rows`/`frames_sampled` 在）、`rows.jsonl`
  （`output.rows=true` 时必须存在且**不是 0 字节**）、`frames/` 标注图数量、
  `panels.resolved.json`；并比较 `comments` / `rows` 的时间戳。
- **视频时间**（`parse` 的一部分，默认开，`--no-media-time` 关）：读段视频**文件名里的
  时间区间**（`19：40-20：20`、`09.09-11.57`、`12：29-15-19` 等写法），用 `ffprobe`
  读**实际时长**与 `creation_time`，核对「seg 时间 × 视频时间」：
  - 实际**短于**区间超过容差（`--time-tolerance`，默认 120s）⇒ **错误**：
    可能少录了一段，或这个文件不是这一段（例：文件名写 `14:30-17:23`，实际只有 53min）。
  - 实际**长于**区间超过容差 ⇒ **警告**：常见于录尾带推荐页/花絮，需人工核对直播结束时间。
  - `creation_time` 只作为参考信息（可能是复制/转码时间），不参与判定。
  - 素材不可达、文件名没有区间（裁剪副本 `..._live.mp4`）⇒ 跳过并提示。
    素材盘换过位置时用 `--video-map 旧前缀=新前缀`（可重复）把路径映射回来。
- `filter`：`users.jsonl` 存在、JSONL 合法；0 个用户 / 判定缓存缺失 / 正面数低于本步
  阈值（旧阈值产物）/ `users.jsonl` 比 `comments.jsonl` 旧 → 警告。
- `qualify`：`merged_users.json`（`n_qualified == len(users)`、阈值与配置一致）、
  每段 `users_qualified.jsonl` 存在，且各段的账号并集与 `merged_users` 完全对得上。
- `export`：按当前 `users_qualified.jsonl` + 标签 + 阈值复算应出的平台，
  逐平台核对 `all_users_正面_<N>张以上/manifest.json`、账号目录、图片数、缺图、
  `正面账号.txt`，以及 manifest 是否比用户表旧。
- `collect`：交付树存在、manifest 计数与盘上一致、每个账号目录的图片齐全、
  `正面账号.txt` / `README.md` 在；并核对**每个合计合格账号都进了树**
  （只在 merge 里有、树里没有 ⇒ 错误）。
- `draw`：`draw.out` 存在。

配置里 `filter/qualify/export/collect/draw` 的 `enabled=false` 默认**不要求**对应产物；
`--only` 点名会像 `run` 一样强制打开它们。

结论级别
--------
- **错误**（✘）：缺文件、JSON 坏、计数/身份对不上、0 字节 `rows.jsonl`、合格账号没进树。
- **警告**（⚠）：0 条评论/0 个用户（可能确实如此）、标注图/缓存缺失、文件偏旧、
  素材路径不可达、交付图数与合计不符（`manifest.mismatch`）。
- **提示**（·）：跳过/关闭的步骤、纯文本输入没有 summary。

退出码：`0` = 没有错误（警告不影响）；`1` = 有错误；`2` = 配置读不了。
`--strict` 把警告也当失败（退出码 1），适合放进交付前的自动检查。

例子
----
    # 跑完一整条链后
    python -m livepipe verify 10-31_抖音.json

    # 只核对本次只跑了的 parse（filter/qualify/export 还没跑）
    python -m livepipe verify 10-31_抖音.json --only parse

    # 交付前严格检查；机读结果交给别的脚本
    python -m livepipe verify 10-31_抖音.json --strict --json

    # 只核对 parse（含视频时间），容差 300s
    python -m livepipe verify 09-16老章拆车_抖音.json --only parse --time-tolerance 300

    # 素材盘挪了位置：把配置里的旧前缀映射到现挂载点
    python -m livepipe verify 09-07猎鹰智驾_快手.json \
        --video-map /home/puyu/work/data=/run/media/puyu/MyDisk

素材盘没挂载时也能核对：读配置失败会退回「不校验 input.videos 是否存在」的宽松模式。
"""
