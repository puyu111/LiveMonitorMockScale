"""livepipe.tools.export_user_images.run"""

from __future__ import annotations

from ... import account_id as aid
from pathlib import Path
import argparse
import json
import shutil
import time

from .common import _log
from .common import parse_color
from .common import pick_comments
from .common import platform_dir
from .common import sanitize
from .common import unique_dir
from .images import write_images
from .io import SCHEMA_VERSION
from .io import load_contract
from .io import load_panel_platform
from .io import read_jsonl
from .select import select
from .selftest import selftest



def run(args: argparse.Namespace) -> int:
    if args.selftest:
        return selftest()

    if args.base == "frame":
        import cv2  # noqa: F401  提前报「没装 OpenCV」，别跑到一半才炸
    else:
        from PIL import Image, ImageDraw  # noqa: F401

    users_path = Path(args.users)
    if not users_path.exists():
        raise SystemExit(f"找不到 {users_path}（先跑 `python -m livepipe run <cfg>` 的 filter 步）")
    run_dir = users_path.parent

    contract = load_contract(run_dir, Path(args.summary) if args.summary else None)
    sample_mode = args.sample_mode or contract.get("sample_mode") or "adaptive"
    start = float(contract.get("start") or 0.0)
    video = args.video or contract.get("video")
    panel_platform = load_panel_platform(run_dir, Path(args.panels) if args.panels else None)
    frames = Path(args.frames) if args.frames else run_dir / "frames"

    if args.base == "annotated" and not frames.is_dir():
        raise SystemExit(f"找不到标注图目录 {frames}（parse 步要 output.frames=true）")
    if args.base == "frame" and not (video and Path(video).exists()):
        raise SystemExit(f"取原帧要视频：summary.json 里没有 video，用 --video 指定（当前 {video!r}）")

    labels = {s.strip() for s in args.labels.split(",") if s.strip()}
    label_tag = "_".join(sorted(labels))
    color = parse_color(args.color)
    box = not args.no_box
    if args.pad < 0 or args.pad_ratio < 0:
        raise SystemExit("--pad / --pad-ratio 不能为负"
                         "（要回到参考语料那种紧贴形态：--pad-ratio 0）")
    scope = "global" if args.flat else args.min_scope

    users = read_jsonl(users_path)
    picked = select(users, panel_platform, labels, args.min_positive, scope)
    if args.flat:                                   # 不分平台：全并到一个目录
        merged: list[tuple[dict, list[dict]]] = []
        for lst in picked.values():
            merged += lst
        picked = {"": merged} if merged else {}

    _log(f"[export] {users_path.name} → {args.out or run_dir}/(<平台>/)all_users_{label_tag}_{args.min_positive}张以上/")
    _log(f"[export] 底图 = " + (
        f"视频原帧（{sample_mode} 口径，锚点 t={start:g}s，**顺序解码不 seek**，bug-264）"
        if args.base == "frame" else
        f"{frames} 里画好的标注图（**带编号和全部评论的框，不是交付形态**）"))
    _log(f"[export] 面板→平台：" + ("、".join(f"{k}={platform_dir(v)}" for k, v in panel_platform.items())
                                    or "（没有面板表，全归「未标平台」）"))
    if box:
        if args.pad == 0 and args.pad_ratio == 0:
            _log(f"[export] 绿框 RGB{color} 线宽 {args.width} 紧贴评论行、不留白"
                 f"（--pad 与 --pad-ratio 都是 0，参考语料形态）")
        else:
            _log(f"[export] 绿框 RGB{color} 线宽 {args.width} 外扩 max({args.pad}px, "
                 f"{args.pad_ratio:g}×行高)（留白，只圈这一条，图上不加任何文字）")
    else:
        _log("[export] --no-box：出纯原帧，不画框")

    total = sum(len(cs) for lst in picked.values() for _, cs in lst)
    if scope == "platform":
        miss = [r.get("username") for r in users
                if len(pick_comments(r, labels)) >= args.min_positive and not any(
                    r is rr for lst in picked.values() for rr, _ in lst)]
        if miss:
            _log(f"[note] {len(miss)} 个用户整段视频够 {args.min_positive} 条正面，但"
                 f"**没有任何单个平台够** ⇒ 按平台口径不交付：{'、'.join(miss[:5])}"
                 f"{'…' if len(miss) > 5 else ''}（要保留用 --min-scope global）")

    plan = []
    for plat in sorted(picked, key=lambda p: (-sum(len(c) for _, c in picked[p]), p)):
        lst = picked[plat]
        if args.limit:
            lst = lst[:args.limit]
        if not lst:
            continue
        n_img = sum(len(cs) for _, cs in lst)
        _log(f"  [{platform_dir(plat) or '（不分平台）'}] {len(lst)} 个用户 / {n_img} 张图")
        plan.append((plat, lst))
    if not plan:
        _log("[export] 没有可交付的用户")
        return 0
    if args.dry_run:
        for plat, lst in plan:
            for rec, cs in lst[:10]:
                _log(f"    {platform_dir(plat) or '-'}/"
                     f"{aid.display(rec.get('username'), rec.get('level'))}-{len(cs)}")
        _log("[export] dry-run，没写任何文件")
        return 0

    root = Path(args.out) if args.out else run_dir
    used_frames: set[int] = set()
    box_doc = {"on": box, "color": list(color) if box else None,
               "width": args.width, "pad": args.pad, "pad_ratio": args.pad_ratio}
    grand = {"users": 0, "images": 0, "missing": 0}
    t0 = time.time()

    # ① 先排好每个平台的目录与每张图的「第几号」——编号按 t 升序，先定死再取帧
    staged, all_jobs = [], []
    for plat, lst in plan:
        out_dir = root / platform_dir(plat) if plat else root
        target = out_dir / f"all_users_{label_tag}_{args.min_positive}张以上"
        if target.exists() and any(target.iterdir()) and not args.force:
            raise SystemExit(f"{target} 非空；要覆盖加 --force（同名图会被覆盖）")
        target.mkdir(parents=True, exist_ok=True)
        jobs, manifest, used_dirs = [], [], set()
        for rec, cs in lst:
            # 昵称带上等级标（``用***◆16-3``）：抖音 App 的昵称是打码的，同一个显示名
            # 底下是好几个账号，只写昵称+条数交付树里分不清（见 account_id）。
            base_name = f"{sanitize(aid.display(rec.get('username'), rec.get('level')))}-{len(cs)}"
            dirname = unique_dir(used_dirs, base_name)
            if dirname != base_name:
                _log(f"    [note] 目录重名 ⇒ {dirname}")
            rows = []
            for i, c in enumerate(cs, 1):
                rel = f"{dirname}/{i}.jpg"
                rows.append({"t": float(c.get("t") or 0.0), "dst": target / rel, "rel": rel,
                             "roi": c.get("roi") or c.get("box"), "panel": c.get("panel"),
                             "platform": plat, "label": c.get("label"),
                             "content": c.get("content") or "",
                             "emoji_text": c.get("emoji_text") or "", "ok": False})
            jobs += rows
            manifest.append({"username": rec.get("username"),
                             "level": str(rec.get("level") or ""), "dir": dirname,
                             "count": 0, "images": rows})
        staged.append({"plat": plat, "target": target, "manifest": manifest, "jobs": jobs})
        all_jobs += jobs

    # ② 一次顺序解码走完**全部平台**（各平台的图汇在一起按帧排序 ⇒ 视频只解一遍；
    #    同一帧的几张共用一次解码）
    missing = write_images(all_jobs, base=args.base, frames=frames, video=video,
                           sample_mode=sample_mode, start=start, box=box, color=color,
                           width=args.width, pad=args.pad, pad_ratio=args.pad_ratio,
                           quality=args.quality, used_frames=used_frames)

    # ③ 逐平台收尾：只登记真写出来的（缺帧的不虚报），空的用户目录不留
    for st in staged:
        target, keep = st["target"], []
        for m in st["manifest"]:
            m["images"] = [{k: v for k, v in r.items() if k not in ("dst", "ok")}
                           for r in m["images"] if r["ok"]]
            m["count"] = len(m["images"])
            if not m["count"]:
                shutil.rmtree(target / m["dir"], ignore_errors=True)   # 全缺帧 ⇒ 不留空目录
                continue
            keep.append(m)
        n_img = sum(m["count"] for m in keep)
        doc = {
            "schema_version": SCHEMA_VERSION,
            "kind": "all_users_positive_images",
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "platform": st["plat"], "platform_dir": platform_dir(st["plat"]) if st["plat"] else None,
            "base": args.base,
            "source": {"users": str(users_path), "video": video,
                       "summary": contract.get("path"),
                       "frames": str(frames) if args.base == "annotated" else None},
            "frame_contract": {"sample_mode": sample_mode, "start": start},
            "labels": sorted(labels), "min_positive": args.min_positive, "min_scope": scope,
            "box": box_doc,
            "counts": {"users": len(keep), "images": n_img, "missing_frames": missing},
            "users": keep,
        }
        (target / "manifest.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
        (target / "正面账号.txt").write_text(
            "\n".join(aid.display(m["username"], m.get("level")) for m in keep) + "\n",
            encoding="utf-8")
        grand["users"] += len(keep)
        grand["images"] += n_img
        grand["missing"] += missing
        _log(f"[done] {platform_dir(st['plat']) or '（不分平台）'}：{len(keep)} 个用户 / {n_img} 张图 → {target}")

    _log(f"[done] 合计 {grand['users']} 个用户 / {grand['images']} 张图（用到 {len(used_frames)} 张不同底帧）"
         f"，用时 {time.time() - t0:.0f}s")
    if grand["missing"]:
        _log(f"[warn] 有 {grand['missing']} 条评论没取到帧，已跳过（manifest 里也少这几张）")
    _log("[done] 每个平台目录里各有 manifest.json + 正面账号.txt")
    return 0
