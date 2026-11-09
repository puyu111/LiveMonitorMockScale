"""给「只在直播里、没进交付树」的命中补出标记图。

撞库的范围是**直播全量账号**：只要发过**任意一条**评论（正面/负面/中性）就算，
不管有没有达到交付阈值（≥3 张）。这些人交付树里没有目录，图也不存在 —— 这里临时把
他们的用户记录喂给 ``export``，从视频原帧里按交付口径（整帧 + 评论绿框、不裁、不加字）
现出几张，再放进 ``标记图/<平台>/<名单名>/<账号目录>/``。零正面的账号按全部标签取评论，
有正面的仍只取正面。

**只在真有这种命中时才跑**；取帧走 ``sample_mode=adaptive`` 顺序解码，和交付图
同口径（bug-264），代价是要从段首解码到评论时刻，几十秒到几分钟。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from .common import IMAGE_SUFFIXES, safe_dirname
from .sources import load_judgments, load_seg_accounts, stream_segments


def _resolve_video(seg: Path, video_map: list[tuple[str, str]] | None) -> str | None:
    """段视频的真实路径：summary.json 里的旧路径失效时，按 ``--video-map`` 前缀重映射。

    素材盘经常换挂载点 / 目录被整理，summary.json 里记的绝对路径就废了；
    没有重映射且原路径不在时原样返回，让 export 报出清楚的错。
    """
    sp = seg / "summary.json"
    if not sp.is_file():
        return None
    try:
        video = str(json.loads(sp.read_text(encoding="utf-8")).get("video") or "")
    except (OSError, json.JSONDecodeError):
        return None
    if not video:
        return None
    if Path(video).is_file():
        return video
    for old, new in video_map or []:
        if video.startswith(old):
            cand = new + video[len(old):]
            if Path(cand).is_file():
                return cand
    return video


def _render_group(seg: Path, seg_users: dict[tuple[str, str], dict],
                  items: dict[tuple[str, str], list[dict]], label: str,
                  out_dir: Path, owners: dict[Path, str],
                  labels: tuple[str, ...], video: str | None = None,
                  log=print) -> dict:
    """跑一次 export，只出 ``items`` 里的账号、只取 ``labels`` 的评论；图落到 ``标记图/``。

    ``owners`` 记录「每个账号目录本轮是哪个段先写的」：**第一个**写它的段用原名
    （``1.jpg``），之后的段加 ``<段名>_`` 前缀（``seg2_1.jpg``）。判断按「本轮」
    而不是「文件已存在」—— 以前用后者，重跑时会把同一段的图也复制成
    ``seg1_1.jpg``，同一个账号的图凭空翻倍。
    """
    # 延迟导入：export 那条链会拉 OpenCV / Pillow，只在真要补图时才付这个代价
    from ..export_user_images.parser import make_parser as export_parser
    from ..export_user_images.run import run as export_run

    with tempfile.TemporaryDirectory(prefix="zm_render_") as td:
        tmp = Path(td)
        users_file = tmp / "users.jsonl"
        with users_file.open("w", encoding="utf-8") as f:
            for key in items:
                f.write(json.dumps(seg_users[key], ensure_ascii=False) + "\n")
        out = tmp / "out"
        argv = ["--users", str(users_file), "--out", str(out),
                "--summary", str(seg / "summary.json"), "--base", "frame",
                "--labels", ",".join(labels), "--min-positive", "1",
                "--min-scope", "platform", "--force", "--quality", "90"]
        if video:
            argv += ["--video", str(video)]
        panels = seg / "panels.resolved.json"
        if not panels.exists():
            panels = seg / "panels.json"
        if panels.exists():
            argv += ["--panels", str(panels)]
        export_run(export_parser().parse_args(argv))

        tag = "_".join(sorted(labels))  # export 的目录名先按标签排序再拼（run.py 同口径）
        tree = next(out.glob(f"*/all_users_{tag}_1张以上"), None)
        if tree is None:
            return {"accounts": 0, "images": 0, "skipped": len(items)}
        manifest = json.loads((tree / "manifest.json").read_text(encoding="utf-8"))
        by_key = {(u["username"], str(u.get("level") or "")): u
                  for u in manifest.get("users", []) if isinstance(u, dict)}

        accounts = image_count = skipped = 0
        for key, ms in items.items():
            u = by_key.get(key)
            if not u or not u.get("dir"):
                skipped += len(ms)
                log(f"[撞库][warn] {label}/{key[0]}：export 没出图（取不到帧？），跳过")
                continue
            src = tree / u["dir"]
            for m in ms:
                dst = out_dir / "标记图" / label / safe_dirname(m["list_name"]) / u["dir"]
                dst.mkdir(parents=True, exist_ok=True)
                first = dst not in owners
                if first:
                    owners[dst] = seg.name
                    # 本轮第一次写这个账号目录：清掉上一轮留下的图，免得新旧混在一起
                    for old in dst.iterdir():
                        if old.is_file() and old.suffix.lower() in IMAGE_SUFFIXES:
                            old.unlink()
                for f in sorted(src.iterdir()):
                    if not f.is_file() or f.suffix.lower() not in IMAGE_SUFFIXES:
                        continue
                    name = f.name if first else f"{seg.name}_{f.name}"
                    shutil.copy2(f, dst / name)
                    image_count += 1
                accounts += 1
        return {"accounts": accounts, "images": image_count, "skipped": skipped}


def _render_seg(seg: Path, seg_users: dict[tuple[str, str], dict],
                items: dict[tuple[str, str], list[dict]], label: str,
                out_dir: Path, owners: dict[Path, str],
                video_map: list[tuple[str, str]] | None = None, log=print) -> dict:
    """把一段的命中账号按评论标签分两拨补图。

    有正面评论的仍按交付口径只取**正面**；**零正面**（只发过中性/负面评论也能进撞库
    池子）的取全部标签，否则 export 一张都挑不出来。
    """
    video = _resolve_video(seg, video_map)
    pos = {k: v for k, v in items.items()
           if int((seg_users.get(k) or {}).get("n_positive") or 0) > 0}
    rest = {k: v for k, v in items.items() if k not in pos}
    got = {"accounts": 0, "images": 0, "skipped": 0}
    for group, labels in ((pos, ("正面",)), (rest, ("正面", "中性", "负面"))):
        if not group:
            continue
        part = _render_group(seg, seg_users, group, label, out_dir, owners,
                             labels, video, log)
        for k in got:
            got[k] += part[k]
    return got


def render_missing(out_dir: Path, report: dict, log=print,
                   video_map: list[tuple[str, str]] | None = None) -> dict:
    """给 ``in_delivery=False`` 的命中补图；返回 ``{accounts, images, skipped, segments}``。

    ``skipped`` = 补不了的命中数（缺平台目录 / 段里查无此人 / 取不到帧）。
    ``video_map`` = ``(旧前缀, 新前缀)``：summary.json 里的视频路径失效时重映射。
    """
    needed = {label: [m for m in rep.get("matches", []) if not m.get("in_delivery")]
              for label, rep in report.get("platforms", {}).items()}
    needed = {k: v for k, v in needed.items() if v}
    totals = {"accounts": 0, "images": 0, "skipped": 0, "segments": 0}
    if not needed:
        return totals
    owners: dict[Path, str] = {}

    for label, matches in needed.items():
        rep = report["platforms"][label]
        pdir = rep.get("platform_dir")
        segs = stream_segments(Path(pdir)) if pdir else []
        if not segs:
            totals["skipped"] += len(matches)
            log(f"[撞库][warn] {label}：找不到 seg*/comments.jsonl 或 users.jsonl，"
                f"{len(matches)} 个「仅直播命中」补不了图（只有表格）")
            continue
        cache = load_judgments(Path(pdir))
        seg_users = {s: load_seg_accounts(s, cache) for s in segs}
        plan: dict[Path, dict[tuple[str, str], list[dict]]] = {}
        for m in matches:
            key = (m["account"], m.get("level") or "")
            hit = [s for s in segs if key in seg_users[s]]
            if not hit:
                totals["skipped"] += 1
                log(f"[撞库][warn] {label}/{m['display']}：段 comments.jsonl / users.jsonl"
                    f" 里都没有，补不了图")
                continue
            for s in hit:
                plan.setdefault(s, {}).setdefault(key, []).append(m)

        for seg, items in sorted(plan.items(), key=lambda kv: kv[0].name):
            totals["segments"] += 1
            log(f"[撞库] 补图：{label}/{seg.name} · {len(items)} 个账号"
                f"（顺序解码视频，可能要几十秒）")
            try:
                got = _render_seg(seg, seg_users[seg], items, label, out_dir,
                                  owners, video_map, log)
            except SystemExit as e:
                totals["skipped"] += len(items)
                log(f"[撞库][warn] {label}/{seg.name} 补图失败：{e}")
                continue
            except Exception as e:  # noqa: BLE001 - 一张补图失败不该毁整份报告
                totals["skipped"] += len(items)
                log(f"[撞库][warn] {label}/{seg.name} 补图出错：{type(e).__name__}: {e}")
                continue
            totals["accounts"] += got["accounts"]
            totals["images"] += got["images"]
            totals["skipped"] += got["skipped"]
    return totals
