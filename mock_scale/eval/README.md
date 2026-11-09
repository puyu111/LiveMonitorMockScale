# 评测台（`eval/`）

**一次评测 = 一份语料快照 × 同一 seed × 两臂各跑 N 遍**。这里只回答一个问题：某一步改动
**真的省了**吗，还是只在噪声里晃了一下。协议与判据在
[`docs/评测方案.html`](../docs/评测方案.html)，改动清单在
[`docs/评测落地计划.html`](../docs/评测落地计划.html)；本文件只讲**怎么用**。

## 三条红线

1. **永不给 `--yes`，永远带 `--out`** —— 语料是真实用户截图，就地写回会覆写它。
2. **只在快照上跑**（`snapshot` 复制出来的副本），原件只读；评测前后用 `verify` 对 md5 清单。
3. **`expand_pairs.jsonl` / `livepipe_join.json` / `expand_run_progress.jsonl` /
   `measure_cache.json` 含用户目录名或昵称正文** —— 只落本地快照 / `eval/` 下，不进交付包、
   不进 git（`.gitignore` 已挡）。`measure_cache.json` 的 key 是 sha256，但 value 里保留了
   `username`（量测结果要用），所以整份文件按敏感数据处理。

## 快速上手

```bash
cd ~/github/LiveMonitorMockScale/mock_scale

# 0) 快照（目标目录非空要加 --force；语料根必须有 manifest.json）
EVAL=~/eval/2026-10-28-baseline
python3 eval/bench.py snapshot --corpus "$SRC" --into "$EVAL/corpus"
#    → $EVAL/corpus · $EVAL/corpus.md5 · $EVAL/fingerprint.json

# 1) 跑臂（每臂 N 遍，默认取 arm 里的 repeat 或 3；run 自己会 --out 到 $EVAL/out/<tag>-<i>）
python3 eval/bench.py run --snapshot "$EVAL/corpus" --tag A --arm eval/arms/A-baseline.json \
                          --eval-root "$EVAL" --repeat 3

# 1b) 两臂**交错**跑（协议要求）：奇数轮 A→B、偶数轮 B→A。
#     同配置两臂 = placebo（量噪声底）；不同配置 = 一次 A/B。
python3 eval/bench.py pair --snapshot "$EVAL/corpus" --tag-a A --tag-b B \
                           --a-arm eval/arms/A-baseline.json \
                           --b-arm eval/arms/B-cache-warm.json \
                           --eval-root "$EVAL" --repeat 3

# 2) 基线（P2）：同一个臂多跑几遍 + placebo，合成中位数 / p95 / CV / 噪声带
#    --run 可给多次：A 与 A2（同配置不同批）一起并进来，量的是「同配置的离散度」
python3 eval/bench.py baseline --run "$EVAL/runs/A" --run "$EVAL/runs/A2" \
                               --eval-root "$EVAL" --label "A-baseline" --scope full-1478 \
                               --out "$EVAL/baseline.json"

# 3) 对比判定（退出码见下）；带 --baseline 时用它**预注册**的噪声带判定
python3 eval/bench.py compare --a "$EVAL/runs/A" --b "$EVAL/runs/B" \
                              --thresholds eval/thresholds.json \
                              --baseline "$EVAL/baseline.json" \
                              --out "$EVAL/compare-A-B.json"

# 4) 核对原件没被动过
python3 eval/bench.py verify --corpus "$SRC" --md5 "$EVAL/corpus.md5"
```

### A 档（对账表）

```bash
# B 臂换成 eval/arms/A-join.json（plan_flags: ["--livepipe-join"]），其余同 pair
python3 eval/bench.py pair --snapshot "$EVAL/corpus" --tag-a A --tag-b AJOIN \
        --a-arm eval/arms/A-baseline.json --b-arm eval/arms/A-join.json \
        --eval-root "$EVAL" --repeat 6
python3 eval/bench.py compare --a "$EVAL/runs/A" --b "$EVAL/runs/AJOIN" \
        --thresholds eval/thresholds.json --baseline "$EVAL/baseline.json" \
        --out "$EVAL/compare-A.json"
```

对账表写在**快照根**（`livepipe_join.json`）。`_run_once` 每遍开始会先删掉它 —— 两臂共用
一份快照时，不删就会把上一臂的表读成这一臂的指标。判据是 `J_join`（覆盖率 = 100%）与
`J_conflict`（冲突 = 0），外加 `E_out` 必须仍为 0（A 是只读旁挂，产物一个字节都不该变）。

### E 档（真实崩溃 + 续跑）

```bash
python3 eval/bench.py crash --snapshot "$EVAL/corpus" --eval-root "$EVAL" \
        --arm eval/arms/A-baseline.json --kill-after 20 --out "$EVAL/eval-E-resume.json"
```

`crash` 自己做四件事：① 完整跑一遍当参照（逐图 md5）；② 起 `--mode run`，等
`expand_run_progress.jsonl` 攒到 `--kill-after` 张时 **SIGKILL 整个进程组**（worker 一起死，
和真断电一致）；③ 同目录 `--resume` 续跑，要求 `reused > 0` 且产物 md5 与参照**逐张相同**；
④ 拿同一份 journal 换 `--seed 12345` 再续跑，要求 `reused = 0`、`stale_fingerprint > 0`
（指纹闸真的会挡）。四条全过退出 0。

### B 档（量测缓存）

```bash
# 冷臂 = A-baseline（cache: "cold"）；热臂 = B-cache-warm（cache: "warm"）
python3 eval/bench.py pair --snapshot "$EVAL/corpus" --tag-a BCOLD --tag-b BWARM \
        --a-arm eval/arms/A-baseline.json --b-arm eval/arms/B-cache-warm.json \
        --eval-root "$EVAL" --repeat 6
python3 eval/bench.py compare --a "$EVAL/runs/BCOLD" --b "$EVAL/runs/BWARM" \
        --thresholds eval/thresholds.json --baseline eval/baseline-sample73-p3b.json \
        --out eval/compare-B.json
```

缓存两类**像素测量**（都不含等级/配对）：

1. **逐图量测** `measure_base`：key = 路径 + size/mtime_ns + roi + `roi_inset/roi_vinset` +
   profile + 用户名 + 代码指纹；
2. **字号拟合扫描** `fit_font_size_for(text, glyph_height)`：key = 文字 + 字高 + 字体文件
   指纹（size/mtime/face）。`allocate` 对**每个（目标名字 × 底图字高）**都做一次二分字号
   扫描（sample-73：1064 次调用 / 7039 次 `measure_ink`），是 plan 墙钟的主要部分。

两臂都从**空缓存**起步：`_run_once` 每遍先删 `expand/measure_cache.json`。热臂在同一轮内
先跑一遍完整的 `plan`（**预热，不计入 T**），再计时跑第二遍 —— 所以计时那一遍是 100% 命中。
命中率 `H_cache =（底图命中 + 拟合命中）/（底图 + 拟合的全部量测次数）`；冷臂没有拟合计数器，
分母只含底图量测。缓存关着时产品行为与旧版逐字节相同（`cache_enabled: false`）。

⚠️ 缓存文件含用户目录名/昵称，且**代码或字体指纹任一项变了就整份作废**；它不是交付物。

### C 档（等级配对）

```bash
# C 关 = A-baseline；C 开 = C-level-on（plan_flags: ["--level-pair"]）
python3 eval/bench.py pair --snapshot "$EVAL/corpus" --tag-a C-off --tag-b C-on \
        --a-arm eval/arms/A-baseline.json --b-arm eval/arms/C-level-on.json \
        --eval-root "$EVAL" --repeat 1        # C 的协议是「各跑一遍」，不是 3 遍取中位
python3 eval/bench.py compare --a "$EVAL/runs/C-off" --b "$EVAL/runs/C-on" \
        --thresholds eval/thresholds-C.json --out eval/compare-C.json
# 「只许改配对字段」用另一个工具验：同一配对量测字段一致 + 每张产物变化可归因到底图更换
python3 eval/plandiff.py --plan-a "$EVAL/runs/C-off/..." --plan-b ... --out-a ... --out-b ...
```

C 的判据**只有语义栏**（`R_blank` / `R_lvl` / `N_out` + plan 差异面）：等级对成品**没有像素影响**
（贴的是目标自己的丸，覆盖写），所以 C 不记时间收益，`E_out` 也不判 —— 换了底图，成品 md5 本来
就该变，验收换成 `plandiff.py` 的「逐张可归因」。`expand_pairs.jsonl` 里第 3 层排除的行
（`tier: excluded`）是**审计行、不是配对**，`R_lvl` 的分母必须跳过它们（bench 已按此过滤）。

C′（事后变体，`eval/arms/C-level-soft.json`，`--level-pair-soft`）：明确不同只排后、不排除。
它和 C 用同一套阈值，**但未预注册**，只用来把「硬排除逼掉多少产量」量化出来给决策，不是上线口径。

### D 档（两遍量测）

```bash
# D 关 = A-baseline；D 开 = D-two-pass（plan_flags: ["--two-pass"]）
python3 eval/bench.py pair --snapshot "$EVAL/corpus" --tag-a D-off --tag-b D-on \
        --a-arm eval/arms/A-baseline.json --b-arm eval/arms/D-two-pass.json \
        --eval-root "$EVAL" --repeat 1
python3 eval/bench.py compare --a "$EVAL/runs/D-off" --b "$EVAL/runs/D-on" \
        --thresholds eval/thresholds-D.json --out eval/compare-D.json
```

D 的判据是**工作量只降不升 + 结论逐字段不变**：`N_badge`（`meta.counters.badge_calls`）、
`N_scan_px`（`meta.counters.scan_px`，`badge_rect_of` 里 ink mask 处理的像素数）、
`E_out = 0`，外加 `plandiff.py` 的 0 字段差异。徽章**只许延后、不许跳过**（bug-284）：
目标自己的图每张都量，能延后的只有「结构闸不过 **且** 不是目标用户」的图 —— 所以
`badge_deferred = 0` 也是一份合法结果（这份语料里每张图都属于目标用户）。

**交错跑**：`A / B / A / B / A / B`，不要 `A×3` 再 `B×3`（热漂移会让后跑的占便宜）——
`pair` 就是干这个的，`run` 只跑单臂。

## 臂（`eval/arms/*.json`）

臂是**文件**不是代码常数 —— 换一臂就加一个 JSON，进 diff 才看得见改了什么。

```json
{ "name": "A-baseline", "workers": 8, "seed": 20261010,
  "cache": "cold", "plan_flags": [], "run_flags": [], "env": {} }
```

| 键 | 含义 |
| --- | --- |
| `workers` / `seed` | 原样传给 `--workers` / `--seed`；两臂必须相同（`compare` 会记下来） |
| `cache` | `cold` ⇒ 每次 plan 前删 `expand/measure_cache.json`；`warm` ⇒ 同一轮内先跑一遍填缓存（预热），再计时一次全命中的 plan |
| `plan_flags` / `run_flags` | 逐项追加到命令行；**一臂只开一个开关** |
| `env` | 只写进报告做备注（如 `{"note": "..."}`），不参与运行 |
| `repeat` | 缺省 3，可被 `--repeat` 覆盖 |

## 阈值（`eval/thresholds.json`）

```json
{ "T_total": { "kind": "time",  "min_delta_over_noise": 2 },
  "R_blank": { "kind": "blank", "max_over_baseline_pp": 1, "abs_max": 2 },
  "R_lvl":   { "kind": "ratio", "min": 0.70 },
  "N_out":   { "kind": "count", "min": "baseline" },
  "E_out":   { "kind": "count", "max": 0 },
  "J_join":  { "kind": "ratio", "min": 1.0 },
  "J_conflict": { "kind": "count", "max": 0 },
  "H_cache": { "kind": "ratio", "min": 1.0 } }
```

改阈值必须改文件、进 diff —— **跑完不许改**。

按步拆开的两份（同一套写法，只是各自只放该步的判据）：

* `thresholds-C.json` —— C 只看语义栏（`R_blank` / `R_lvl` / `N_out`），**不放** `E_out`：
  C 换了底图，成品 md5 本来就该变，产物验收改用 `plandiff.py` 的「逐张可归因」；
* `thresholds-D.json` —— `N_badge`（只许不升）/ `N_scan_px`（降幅 ≥10%）/ `N_out` / `E_out = 0`。

## 判定规则

* **噪声底** = 同一臂内 N 次墙钟的 `CV = (max − min) / median`（按评测方案 §4 预注册；
  **不是** stdev/mean，后者对 3 次重复只有前者约 0.4 倍，会把抖动判成收益）。
* **门槛取值顺序**：给了 `--baseline` 就用它的 `noise.band_pct`（= 2×CV，冻结的预注册值，
  且要求语料指纹一致）；没给才退回本臂 CV。报告里写 `noise_source` 说明用的是哪个。
* `T_total`：`Δ% > gate` 记收益；`< −gate` 判回退；带内写 **`no_difference`**。
  `gate = CV × thresholds.T_total.min_delta_over_noise`（默认 2）。
* `E_out`：两臂输出树里全部 `扩展*.jpg` 的 `(相对路径, md5)` 差异数，**必须 0**（硬线）。
* `R_lvl`：等级还没接进配对时两臂都是 `null` ⇒ 判 `inconclusive`，**不算 0% 通过**。
* `J_join` / `J_conflict`：只在 **B 臂真落了 `livepipe_join.json`** 时判（否则跳过，不把
  placebo 判成 inconclusive）。覆盖率 = comments 命中 ÷ 导出图数，冲突 = 两路拼出的
  `昵称：内容` 不一致的条数。`rows` 侧会先按 y 拼接多行、再剥掉首行的**等级数字前缀**
  （`3够***` → `够***`，昵称本身以数字开头时不动），否则正常命中会被误报成冲突。
* `H_cache`：只在 **B 臂的 arm 是 `cache: warm`** 时判（冷臂 hit=0 是定义，不是失败）。
  `h =（底图命中 + 字号拟合命中）/ 全部量测次数`；阈值 `min: 1.0` ⇒ 热臂必须一次不漏。
* E 档不走 `compare`：`crash` 自己判 `E_kill` / `E_reuse` / `E_identical` / `E_seed_guard`。

## 退出码
| 码 | 含义 |
| --- | --- |
| 0 | 全部通过（含「无差异」） |
| 2 | 用法/环境错误（快照目标非空、不是语料根…） |
| 3 | 有指标回退，报告照写 |
| 4 | 两臂语料指纹不同 ⇒ 不可比 |
| 5 | 原件被改动（`verify`）⇒ 立即停 |
| 6 | 有指标未测（`null`）⇒ `inconclusive`，不算通过 |

⚠️ **产品的 `3` 不是评测失败**：那是「缺口 >5%」的口径判定，report/reconcile 已经写完。
bench 照跑照记（`summary.json` 的 `rc_values`），只有其他非 0 退出码才当崩溃。

## 每臂的产物

```
$EVAL/
  corpus/                 # 快照（产品在这里写 expand_plan.json 等）
  corpus.md5              # 原件/快照的逐文件 md5 清单
  fingerprint.json        # corpus_sha256 + 文件数 + 来源路径
  runs/<tag>/rep<i>.json  # 单次：wall_s / plan / run / quality / outputs(md5 表) / join
  runs/<tag>/summary.json # 中位数、CV、rc_values、counters_median、outputs、join
  out/<tag>-<i>/          # 产品实际产物（report / reconcile / 扩展*.jpg / 进度 journal）
  compare-A-B.json        # 两臂判定（含 J_join / J_conflict / join 摘要）
  baseline.json           # P2 基线：中位数 / p95 / CV / 噪声带 / 分项 / 计数
  baseline-sample73-p3.json  # P3（A/E 代码版本）重冻的噪声带
  baseline-sample73-p3b.json # P3（B 量测缓存代码版本）重冻的噪声带
  compare-B.json          # B 档冷/热判定（T_total / H_cache / E_out）
  compare-C.json          # C 档严（--level-pair）：N_out 未过（38 < 53）
  compare-Csoft.json      # C′ 软（--level-pair-soft，事后）：N_out 过、R_lvl 60.5% 未过
  compare-D.json          # D 档（--two-pass）：N_badge 不升 · N_scan_px −89.8% · E_out 0
  plandiff-C.json         # C 的 plan 差异面（0 量测字段差异 · 产物变化可归因；无用户目录名）
  plandiff-D.json         # D 的 plan 差异面（0 字段差异 · 0 张 md5 变化）
  eval-E-resume.json      # E 档崩溃续跑验收（kill / resume / seed 闸）
```

`baseline.json` 是**冻结**的判据底：之后每一步的 `compare` 都拿它的 `noise.band_pct` 当门槛
（`thresholds.json` 说「怎么判」，`baseline.json` 说「这台机器上多少算抖动」）。`--run` 给多次
＝同一个臂的不同批（A + placebo A2）并在一起算 CV；**不同配置之间别并**，那是 `compare` 的事。

`_collect()` 只读**产品自己落的账**：plan 的 `meta.counters` / `users[].short_reason` / `short[]`、
`expand_pairs.jsonl`、report 的 `timings` / `counters`、以及输出图的 md5 —— **不 import 产品代码**，
产品怎么改都不用动这里。

## 评测台自身的验收（改 bench 之后必跑）

| 自检 | 怎么测 | 通过线 |
| --- | --- | --- |
| 快照可复现 | `snapshot` 跑两次比 `corpus_sha256` | 相同 |
| 产品可复现 | 同臂跑两次比全部 `扩展*.jpg` md5 | 逐张相同 |
| 不碰原件 | `verify` | 逐条相同 |
| 自身无偏 | `compare A vs A` | `|Δ| ≤ 噪声`，`E_out` 0；有 null 指标时退出 6 |
| 崩溃可续 | `crash --kill-after 20` | 复用 > 0 · md5 差 0 · 换 seed 复用 0 |

## 当前基线（P2，2026-10-27）

本仓库的 `eval/baseline.json` 是**样本语料**上冻结的第一份基线（`scope: sample-73`）：

| 指标 | 值 |
| --- | --- |
| 语料 | `out/风云T7_20260826_11/抖音/all_users_正面_2张以上`（73 张 / 27 户 / 74 文件） |
| 遍数 | 12（A + placebo A2，6 轮交错） |
| `T_plan` / `T_run` / `T_total` 中位数 | **4.532s / 4.066s / 8.599s** |
| `CV`（`(max−min)/median`） | **3.73%** ⇒ 噪声带 **±7.47%** |
| 量测 / 徽章调用 / job / 出片 | 73 / 57 / 53 / 53（12 遍逐次相同） |
| 分项（plan，逐图中位） | decode 18.9ms · locate 11.8ms · badge 0.76ms |
| 分项（run，全部 job 之和） | render 14.7s · check 9.5s · save 4.3s · sprite 1.3s（CPU 秒） |
| `R_blank` / `R_lvl` | 0.0% / `null`（等级还没接） |

⚠️ **这不是生产噪声带**：73 张样本上固定开销占比大。全量 1,478 张语料到位后按同法重跑，
用 `scope: full-1478` 覆盖；在那之前，只用它做**机制验证**与 bench 自检（`CV ≤ 5%` 已满足）。

### P3 重冻（2026-10-27，A/E 代码版本）

产品加入 A（对账表）与 E（续跑 journal）后代码指纹变了，**在跑 A/B 之前**用同一份语料重冻了
噪声带（`eval/baseline-sample73-p3.json`，6 遍，`scope: sample-73`）：

| 指标 | 值 |
| --- | --- |
| `T_plan` / `T_run` / `T_total` 中位数 | 4.413s / 4.072s / 8.490s |
| `CV` | **3.30%** ⇒ 噪声带 **±6.60%** |
| 语料指纹 | `a941ea3f…`（与 P2 相同）· 代码指纹 `file:…`（P2 是 `file:36e6b08d…`） |

**A（只读对账）结果**：`BL2` vs `JOIN` 交错 6 轮 ⇒ `Δ +0.1%`（带内，`no_difference`）、
`E_out = 0`（53 张成品逐图 md5 相同）、`J_join = 100%`（73/73，rows 侧 73/73，多行 8 张）、
`J_conflict = 0`。`R_lvl` 仍是 `inconclusive` ⇒ 整体退出 6（与 P2 placebo 同口径）。
见 `eval/compare-A.json`。

**E（崩溃续跑）结果**：第 22 张完成后 SIGKILL ⇒ `--resume` 复用 22、重出 31、md5 差 0；
同 journal 换 seed 续跑 ⇒ 复用 0、`stale_fingerprint = 53`。四条全过。
见 `eval/eval-E-resume.json`。

### B 重冻与实测（2026-10-27，B 代码版本）

B 只改缓存开关下的路径（默认关），但仍按协议**先重冻**同一份语料的噪声带
（`eval/baseline-sample73-p3b.json`，6 遍）：`T_plan / T_run / T_total = 4.424 / 4.059 / 8.482s`，
`CV 3.36%` ⇒ 带 ±6.72%，语料指纹 `a941ea3f…`（与 P2/AE 相同）。

冷臂 `BCOLD` vs 热臂 `BWARM` 交错 6 轮：

| 指标 | 冷（无缓存） | 热（100% 命中） |
| --- | --- | --- |
| `T_total` 中位数 | 8.54s | **4.30s**（`Δ +49.6%`，带 ±6.72%） |
| `T_plan`（逐遍） | ~4.4s | **~0.25s** |
| 预热（填缓存，不计时） | — | ~1.04s |
| `H_cache` | 0 | **1.0**（底图 73/73 · 拟合 1060/1060） |
| `E_out` | 0（53 张逐图 md5 相同） | 0 |
| `N_out` / `R_blank` | 53 / 0% | 53 / 0% |

`run` 段不受缓存影响（出片本来就要重画）⇒ 收益全在 plan。**B 通过**：机制（H_cache=1.0）、
时间（远超噪声带）、产物（E_out=0）三项都过；`compare-B.json` 整体退出 6 只因 `R_lvl`
仍 `null`（C 未接），与 A 同口径。

### C 实测（2026-10-28，C/D 代码版本，sample-73）

关/开各跑一遍（C 的协议就是「各跑一遍」，不看时间）。等级来源 = `comments.jsonl` 的
`badge_text` + manifest 的账号级 `level`，三道脏值闸后：**目标已知 18/27，图已知 52/73**
（21 张无读数），`rid` 冲突 0、与账号级冲突 0。

| 指标 | C 关 | C 严（`--level-pair`） | C′ 软（`--level-pair-soft`，事后） |
| --- | --- | --- | --- |
| `N_out` | 53 | **38**（−28%） | 53 |
| `R_blank` | 0% | 0% | 0% |
| `R_lvl` | `null` | **1.0**（23/23） | **0.605**（23/38） |
| 层级 | — | same 23 · unknown 9 · degraded 6 · excluded 594 | same 23 · unknown 9 · degraded 6 · cross 15 |
| plan 差异面 | — | 仅配对字段；量测字段 0 差异；38 张 md5 变化**逐张可归因**到底图更换 | 同左 |

**C 严不通过**：`N_out 38 < 53`（预注册线「≥ 基线」）。原因不是实现，是这份语料的结构 ——
**有徽章 ⇔ 等级可读**（9 户无等级 = 无徽章），所以对「有徽章的目标」第 2 层（底图等级未知）
**天然为空**（无徽章底图本来就过不了 C1 宽度闸）；而等级 1/11/12/14/16/17/19 各只有 **1 户**，
同等级底图只能来自别人 ⇒ 6 户直接配空、2 户被 C5 上限卡住。**C′ 软**把产量救回来，但
`R_lvl 60.5% < 70%` ⇒ 也不过。

结论：三条预注册线**没有任何口径能同时过**。按 `docs/量测优化方案.html` §3.2「超标就停下来
重新拍板（加第 4 层或回滚），不静默放宽」，C 保持**默认关**，决策点留给用户；C′ 只是量化
「硬排除逼掉多少产量」的对照臂，不是上线口径。

### D 实测（2026-10-28，C/D 代码版本，sample-73）

| 指标 | D 关 | D 开（`--two-pass`） |
| --- | --- | --- |
| `N_badge` | 57 | 57（`badge_deferred = 0`） |
| `N_scan_px` | 1,230,338 | **125,538（−89.8%）** |
| `N_out` / `E_out` | 53 / — | 53 / **0** |
| plan 差异面 | — | **0 字段差异**（19 组配对完全一致，0 张 md5 变化） |

**D 通过**：工作量只降不升、结论逐字段不变。`N_badge` 没降是**语料决定的**，不是没实现：
这份语料 73 张图**全部属于目标用户**，而 bug-284 的红线是「目标自己的图每张都量」（贴图源 +
`badge_families` 都靠它），能延后的只有「结构闸不过 **且** 非目标用户」的图 —— 这里一张都没有。
`compare-D.json` 退出 6 只因 `T_total` 无噪声底（D 的判据不是时间）。

## 已知边界（2026-10-27）

* `R_lvl` 现在**有值了**，但只在 C 臂（`--level-pair` / `--level-pair-soft`）里：
  `t_level / b_level` 来自 `comments.jsonl` 的 `badge_text` + 账号级 `level`（脏值只降级）。
  基线臂没有这两个键 ⇒ `null`（不是 0）。
* `meta.counters.scan_px` 已接（D 的 `N_scan_px`，`badge_rect_of` 里 ink mask 处理的像素数）；
  `report.counters.scan_px` 仍是 `0` —— **run 阶段本来就不量徽章**，不是"扫了 0 列"。
* `N_badge` 在 sample-73 上不会降：73 张图全属于目标用户，而目标自己的图必须每张都量
  （bug-284）；D 的可延后集合（结构闸不过 **且** 非目标用户）在这里为空。
* 全量语料 `out/livepipe_example3/<平台>/all_users_正面_3张以上`（1,478 张）本机暂缺；
  本仓库基线是 `sample-73` 范围，见上节。
* A 档对账按 `manifest.segments[].out` 找 `comments.jsonl` / `rows.jsonl`。合并桶里那是
  **绝对路径**，指向原始导出目录（不在快照里）—— 只读，不改任何东西；但这也意味着
  A 的输入不在 `corpus_sha256` 覆盖范围内。语料重建（重新导出）后对账结果要重跑。
* E 的 journal 落在 `--out` 目录里（就地写回时落在语料根）。`--out` 每换一个目录就是一份
  新进度；续跑必须指回**同一个 out 目录**。
