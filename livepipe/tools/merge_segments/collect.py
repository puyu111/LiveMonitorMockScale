"""livepipe.tools.merge_segments.collect"""

from __future__ import annotations

from ... import account_id as aid
from pathlib import Path
import json
import shutil
import time

from .common import _label
from .common import _uname
from .common import log
from .common import read_jsonl
from .common import sanitize



# --------------------------------------------------------------------------- #
# ② collect：各段导出目录 → 最终交付树
# --------------------------------------------------------------------------- #

def _stage_dirs(seg: dict, stage: str, platform_dir: str | None) -> list[Path]:
    """该段的导出目录（含 manifest.json 的 ``all_users_正面_<stage>张以上``）。

    路径由 export 定成 ``<段目录>/<平台中文名>/all_users_正面_<N>张以上/``；
    平台名不写死（视频号/抖音/微博…），靠 glob 认；配了 ``collect.platform_dir`` 就只认它。
    """
    base = Path(seg["out"])
    pat = f"{platform_dir}/{stage}" if platform_dir else f"*/{stage}"
    return sorted(p for p in base.glob(pat) if (p / "manifest.json").exists())


#: 交付树里 `README.md` 的固定开头（讲**目录名怎么读**，不含本批数据）。
#: 为什么由 `collect` **生成**、而不是手写一份放进去：这棵树每次 collect 都被 rmtree
#: 重建（改阈值/口径就会重来）⇒ 手写的那份下一次 collect 就静默消失了，而它恰好是
#: 「目录名里那个 `◆` 是什么意思」的唯一说明。数据部分全部从 `doc` 现取，不会过期。
_README_HEAD = """\
# 正面评论交付图 · 目录怎么读

**认人口径 = `(昵称, 等级标)`。** 手机端直播的昵称是**打码**的（`用***`），一个屏幕名
底下能有好几十个真人；屏幕上**唯一**的身份证据是评论行左边那颗粉丝团药丸 `◆N`
（等级标）。所以「昵称 + 等级标**都对得上**」才算同一个人，实现见 `livepipe/account_id.py`。

目录名因此有两种读法：

| 目录名 | 怎么读 |
|---|---|
| `白___-12` | 昵称 `白***`，**这一次没读到等级标**，合计 12 张 |
| `白___◆19-5` | 昵称 `白***` + 等级标 `◆19`，合计 5 张 |

每个目录里是 `1.jpg … N.jpg`（**跨段连续重编号**，按时间先后），一张图 = 一条评论，
绿框圈住的就是这条评论。

⚠️ **不带 `◆` 的目录不等于「这个人没有粉丝团等级」**，而是「**这一次没读出来**」——
OCR 只能认出屏幕上一部分药丸里的数字。这类目录**有可能**混着同名、等级不同的若干人，
而数据上分辨不了（这批具体漏下多少见下表）。**别拿目录名去猜**：要更细只能改进读数来源
（重跑 OCR / 扩大读数覆盖），**不要**把「认人」退回只按昵称。
"""


def _readme(doc: dict, cfg: dict, segs: list[dict]) -> str:
    """拼交付树的 `README.md`：固定说明 + 本批的账（账全从 `doc` 现算，不另立口径）。"""
    users, counts = doc["users"], doc["counts"]
    blank = [r for r in users if not r.get("level")]
    seg_rows = "\n".join(
        f"| {s.get('name') or '(单段)'} | `{s['video']}` | `{s['out']}` |" for s in segs)
    return _README_HEAD + f"""
## 这一批

| | |
|---|---|
| 平台 | `{doc['platform_dir']}` |
| 规模 | **{counts['users']} 个账号 / {counts['images']} 张图** |
| 交付门槛 | 每人在**整场直播**里正面评论 ≥ **{int(doc['min_positive'])}** 条（`qualify.min_positive`） |
| 没读到等级标的 | {len(blank)} 个目录 / {sum(r['count'] for r in blank)} 张图（目录名里没有 `◆`） |
| 判定标签 | {', '.join(doc['labels'])} |
| 生成时间 | {time.strftime('%Y-%m-%d %H:%M')} |

## 素材分段

| 段 | 视频 | 该段的输出目录 |
|---|---|---|
{seg_rows}

## 这个目录里的文件

| 文件 | 是什么 |
|---|---|
| `README.md` | 这份说明（每次 `collect` 重新生成，别手改） |
| `manifest.json` | 机器可读清单。`users[]` 带 `username`/`level`/`dir`/`count`/`segs`（各段各几条），`images[]` 逐图带 `rel`（树内路径）/`seg`（来自哪段）/`t`（片内秒）/`roi`（评论框）/`panel`/`content`/`emoji_text`/`label` |
| `正面账号.txt` | 账号名单，一行一个：读到等级标的写成 `用***◆16`，没读到的就是昵称本身 |
| `目录名/N.jpg` | 每条正面评论一张截图，绿框圈住的就是这条评论 |

## 口径改了怎么重算

```bash
cd <Embodied 仓库根>     # output.dir 是相对路径 ⇒ 必须在项目根下跑
python3 -m livepipe.remedy_badges <配置.json>            # 干跑：只印单子，不动盘
python3 -m livepipe.remedy_badges <配置.json> --apply    # 按新口径重切交付树
```

⚠️ 改 `qualify.min_positive`（每人几条才进交付）或改解析层的等级标读数之后，**`export` 与
`collect` 必须一起重跑**：只重跑 `qualify` 的话，各段导出目录里的账号身份还是旧口径的，
`collect` 配不上就会**静默少账号**（orphan 那条警告只是个提醒，别指望它兜住）。
"""


def collect(cfg: dict, segs: list[dict], *, dry_run: bool = False) -> int:
    c = cfg["collect"]
    n = int(cfg["qualify"]["min_positive"])
    stage = str(c["stage_min"] if c["stage_min"] is not None else cfg["qualify"]["stage_min"])
    stage_name = f"all_users_正面_{stage}张以上"

    merged_p = Path(cfg["output"]["dir"]) / "merged_users.json"
    if not merged_p.exists():
        log(f"[collect][error] 找不到 {merged_p} —— 先跑 qualify 步")
        return 1
    merged = json.loads(merged_p.read_text(encoding="utf-8"))
    merged_by_key = {aid.account_key(u["username"], u.get("level")): u
                     for u in merged["users"]}
    total_of = {k: u["n_positive"] for k, u in merged_by_key.items()}

    # 逐段读 manifest：账号身份 → [(段顺序, 该段导出目录, [(该段内的图名, 该图那一条 manifest 条目)…])]
    found: dict[tuple[str, str], list[tuple[int, Path, list[tuple[str, dict]]]]] = {}
    plat_seen: list[str] = []
    for s in segs:
        dirs = _stage_dirs(s, stage_name, c["platform_dir"])
        if not dirs:
            # 一段**本来就没有可交付用户**时，export 只印一句「没有可交付的用户」就返回，
            # 不建 `<段目录>/<平台>/all_users_…/manifest.json` ⇒ 这里找不到目录是**正常**的，
            # 不是「export 没跑」。判据：qualify 总会给每段写 `users_qualified.jsonl`
            # （哪怕 0 行），它存在且为空 ⇒ 该段确实 0 个可交付账号，跳过即可。
            # 从前一律当错误 return 1，于是七段里只要有一段全无合格用户，**整棵树都建不出来**
            # （现场：seg1 只有 3 条评论、0 个合格用户 ⇒ collect 在最后一步把整批交付掐掉）。
            qd = Path(s["out"]) / "users_qualified.jsonl"
            if qd.exists() and not read_jsonl(qd):
                log(f"[collect][note] {_label(s)} 0 个可交付用户（没有导出目录）⇒ 跳过该段")
                continue
            log(f"[collect][error] {_label(s)} 下找不到导出目录 "
                f"（期望 <段目录>/<平台>/{stage_name}/manifest.json）"
                f" —— 先把 export 步跑完")
            return 1
        for d in dirs:
            plat_seen.append(d.parent.name)
            doc = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            for u in doc.get("users") or []:
                imgs = [(im["rel"].split("/", 1)[1], im) for im in u.get("images") or []]
                if imgs:
                    found.setdefault(aid.account_key(u["username"], u.get("level")),
                                     []).append((s["i"], d / u["dir"], imgs))
    # 各段导出目录里**没有**的合格账号 = 账号身份对不上（最可能是改了口径后只重跑了
    # qualify、没重跑 export：`level` 是这一层新加的键，老 manifest 里没有它 ⇒
    # 键从 (昵称, 等级) 退回 (昵称, "")，两边配不上）。这些账号会**静默**从交付树里消失，
    # 所以宁可吵一声。
    orphan = [(k, v) for k, v in merged_by_key.items() if k not in found]
    if orphan:
        log(f"[collect][warn] {len(orphan)} 个合计合格的账号在各段导出目录里找不到图"
            f"（账号身份对不上？改了认人口径就得连 export 一起重跑）：")
        for k, v in sorted(orphan, key=lambda t: -t[1]["n_positive"])[:10]:
            log(f"          {_uname(v)}  合计 {v['n_positive']}")
    platform_dir = c["platform_dir"] or (plat_seen[0] if plat_seen else "未标平台")
    if len(set(plat_seen)) > 1:
        log(f"[collect][warn] 各段导出目录的平台名不一致（{sorted(set(plat_seen))}），"
            f"按下标第一段取 {platform_dir}")

    dst_root = (Path(c["out"]) if c["out"] else
                Path(cfg["output"]["dir"]) / platform_dir / f"all_users_正面_{n}张以上")
    log(f"[collect] {len(found)} 个账号（各段导出）→ {dst_root}")
    if dst_root.exists() and any(dst_root.iterdir()) and not c["force"]:
        log(f"[collect][error] {dst_root} 非空；要覆盖就把 collect.force 设成 true")
        return 2
    if dry_run:
        return 0
    if dst_root.exists():
        shutil.rmtree(dst_root)          # 整棵重来：否则改了阈值会留下上一轮的账号目录

    used: set[str] = set()
    rows, mismatch, n_img = [], [], 0
    for key in sorted(found, key=lambda k: (-total_of.get(k, 0), k)):
        name, level = key
        n_tot = total_of.get(key)
        if n_tot is None or n_tot < n:
            continue                                    # qualify 之后不该出现，防呆
        # 目录名里带上等级标（``用___◆16-3``）：同一个打码昵称底下可能有好几个账号，
        # 只写昵称+条数的话交付树里分不清谁是谁（连 ``正面账号.txt`` 也会出现两条一样的行）。
        dirname = f"{sanitize(aid.display(name, level))}-{n_tot}"
        if dirname in used:                             # 不同昵称清成同名 ⇒ 撞了就加尾号
            i = 2
            while f"{dirname}-{i}" in used:
                i += 1
            dirname = f"{dirname}-{i}"
        used.add(dirname)
        (dst_root / dirname).mkdir(parents=True, exist_ok=True)
        got = 0
        images: list[dict] = []
        for _i, src_dir, imgs in sorted(found[key], key=lambda t: t[0]):   # 段顺序 = 时间顺序
            for rel, im in sorted(imgs, key=lambda t: int(Path(t[0]).stem)):
                got += 1
                # 合并树是**重编号**的（1.jpg…N.jpg 跨段连续）⇒ 清单里的 rel 必须跟着改写，
                # 否则下游拿到的是段内路径、在自己的树上找不到文件（bug-356）。其余字段照抄。
                shutil.copy2(src_dir / rel, dst_root / dirname / f"{got}.jpg")
                images.append({**im, "rel": f"{dirname}/{got}.jpg",
                               "seg": _label(segs[_i]) if _i < len(segs) else str(_i)})
        if got != n_tot:
            mismatch.append({"username": name, "level": level, "dir": dirname,
                             "want": n_tot, "got": got})
        n_img += got
        rows.append({"username": name, "level": level, "dir": dirname, "count": got,
                     "segs": {_label(segs[i]) if i < len(segs) else str(i):
                              len(imgs) for i, _d, imgs in found[key]},
                     "images": images})

    (dst_root / "正面账号.txt").write_text(
        "\n".join(_uname(r) for r in rows) + ("\n" if rows else ""), encoding="utf-8")
    doc = {
        "kind": "all_users_positive_images_merged_segments",
        "platform_dir": platform_dir,
        "labels": cfg["export"]["labels"].split(","),
        "min_positive": n, "min_scope": cfg["export"]["min_scope"],
        "stage_min": c["stage_min"] if c["stage_min"] is not None else cfg["qualify"]["stage_min"],
        "segments": [{"name": s["name"], "video": s["video"],
                      "out": s["out"]} for s in segs],
        "counts": {"users": len(rows), "images": n_img, "mismatch": len(mismatch)},
        "users": rows, "mismatch": mismatch,
    }
    (dst_root / "manifest.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    (dst_root / "README.md").write_text(_readme(doc, cfg, segs), encoding="utf-8")
    log(f"[collect] {len(rows)} 个账号 / {n_img} 张图 → {dst_root}")
    log(f"[collect] 名单 → {dst_root / '正面账号.txt'}；说明 → {dst_root / 'README.md'}")
    if mismatch:
        log(f"[collect] ⚠️ 图数与合计不符 {len(mismatch)} 个：")
        for m in mismatch[:10]:
            log(f"          {aid.display(m['username'], m.get('level'))}"
                f"  合计 {m['want']} 实得 {m['got']}")
    return 0
