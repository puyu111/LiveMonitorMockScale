#!/usr/bin/env python3
"""P4 清盘与回滚：影子时钟 + OSS 覆盖审计 + 带闸门的删除。

阶段：

- ``shadow-start`` / ``shadow-status``：记录 7 天影子期的起点；到期后 ``apply``
  才允许删本地。
- ``audit``：扫描本地目标，逐个文件在「保留根」（OSS 录屏/结果 + 未选中的本地根）
  里找同相对路径同字节，或同文件名同字节的副本；找不到 = 不可删。
- ``plan``：只打印各目标可删/不可删数量和闸门状态，不删任何东西。
- ``apply --targets … --yes``：影子到期 + 选中目标 0 个不可删文件才执行。
  ``external_out`` 额外检查表情表（`base_dir` 依赖）；默认拒绝删。
- ``rollback-check``：检查 P1.5 本地路径备份是否还在、本地路径是否存在。

目标名：``staging``（~/livepipe_batches）、``local_out``（仓库 out/）、
``external_out``（外置盘 out/）、``mydisk``（MyDisk 原片，未挂载自动跳过）。

退出码：0 = 正常；1 = 闸门未通过 / 有不可删文件；2 = 参数错误。
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
STATE = Path.home() / ".livepipe_p4_shadow.json"
SHADOW_DAYS = 7
OSS_ROOTS = [
    Path.home() / "oss-bucket/直播data/录屏",
    Path.home() / "oss-bucket/web/1/奇瑞汽车/直播看管/直播录屏",
    Path.home() / "oss-bucket/web/1/奇瑞汽车/直播看管/直播产生结果",
    # 2026-11-19 之前的错误前缀（P1/P2 曾写到桶根）；保留为只读保留根，
    # 迁移完成并复核后再从这里删除。
    Path.home() / "oss-bucket/奇瑞汽车/直播看管/直播录屏",
    Path.home() / "oss-bucket/奇瑞汽车/直播看管/直播产生结果",
]
DEFAULTS = {
    "staging": Path.home() / "livepipe_batches",
    "local_out": REPO / "out",
    "external_out": Path("/run/media/puyu/C0DE6A4BDE6A39B4/out"),
    "mydisk": Path("/run/media/puyu/MyDisk"),
}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".ts", ".m4v"}
TARGET_HELP = ", ".join(DEFAULTS)


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1000.0
    return f"{n:.2f} TB"


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {}


def parse_targets(value: str | None) -> list[str]:
    if not value or value == "all":
        return [k for k, p in DEFAULTS.items() if p.exists()]
    out = []
    for name in value.split(","):
        name = name.strip()
        if name not in DEFAULTS:
            raise SystemExit(f"[error] 未知目标 {name!r}；可选：{TARGET_HELP}")
        out.append(name)
    return out


def root_of(name: str) -> Path:
    return Path(os.path.expanduser(DEFAULTS[name]))


def scan_files(root: Path) -> tuple[int, int, list[tuple[str, int]]]:
    """返回 (文件数, 字节数, [(相对路径, 字节)])；跳过软链。"""
    n = total = 0
    files: list[tuple[str, int]] = []
    if not root.exists():
        return 0, 0, files
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            p = Path(dirpath) / name
            if p.is_symlink() or not p.is_file():
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            files.append((str(p.relative_to(root)), size))
            n += 1
            total += size
    return n, total, files


def build_index(keep_roots: list[Path]) -> tuple[set, set, set]:
    """(相对路径,字节) + (文件名,字节) + 视频字节数索引。"""
    rel: set[tuple[str, int]] = set()
    base: set[tuple[str, int]] = set()
    video_sizes: set[int] = set()
    for root in keep_roots:
        if not root.exists():
            continue
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                p = Path(dirpath) / name
                if p.is_symlink() or not p.is_file():
                    continue
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                rel.add((str(p.relative_to(root)), size))
                base.add((name, size))
                if p.suffix.lower() in VIDEO_EXTS:
                    video_sizes.add(size)
    return rel, base, video_sizes


def is_metadata(rel: str, target: str) -> bool:
    """批次运行时元数据/生成物：删了不会丢结果，apply 时可用 --allow-metadata 忽略。"""
    base = os.path.basename(rel)
    if base in {".batch_runner.py", ".batch_runner.json", ".batch_runner.out",
                "batch.log", "run.sh"}:
        return True
    if target == "staging" and rel.count("/") == 1 and rel.endswith(".json"):
        return True
    if any(part.endswith("_emoji") for part in Path(rel).parts):
        return True
    return False


def audit_targets(selected: list[str], *, verbose: bool = True,
                  allow_metadata: bool = False) -> dict:
    keep = [r for r in OSS_ROOTS if r.exists()]
    for name in DEFAULTS:
        if name not in selected and root_of(name).exists():
            keep.append(root_of(name))
    if verbose:
        print("[audit] 保留根：")
        for r in keep:
            print(f"  keep {r}")
    rel_idx, base_idx, video_sizes = build_index(keep)
    if verbose:
        print(f"[audit] 保留索引：相对路径 {len(rel_idx)} / 文件名 {len(base_idx)}"
              f" / 视频字节 {len(video_sizes)}")
    result = {"generated_at": iso_now(), "selected": selected,
              "keep_roots": [str(r) for r in keep], "targets": {}}
    for name in selected:
        root = root_of(name)
        if not root.exists():
            if verbose:
                print(f"[audit] {name}: 不存在，跳过（{root}）")
            result["targets"][name] = {"root": str(root), "exists": False}
            continue
        n, total, files = scan_files(root)
        safe = unsafe = 0
        safe_bytes = unsafe_bytes = 0
        unsafe_real = unsafe_real_bytes = 0
        meta_ignored = [0, 0]
        examples: list[dict] = []
        unsafe_all: list[tuple[int, str]] = []
        unsafe_by_ext: dict[str, list[int]] = {}
        for rel, size in files:
            name_only = os.path.basename(rel)
            ext = os.path.splitext(rel)[1].lower()
            covered = ((rel, size) in rel_idx
                       or (name_only, size) in base_idx
                       or (ext in VIDEO_EXTS and size in video_sizes))
            if covered:
                safe += 1
                safe_bytes += size
            else:
                unsafe += 1
                unsafe_bytes += size
                if allow_metadata and is_metadata(rel, name):
                    meta_ignored[0] += 1
                    meta_ignored[1] += size
                    continue
                unsafe_real += 1
                unsafe_real_bytes += size
                unsafe_all.append((size, rel))
                slot = unsafe_by_ext.setdefault(ext or "<none>", [0, 0])
                slot[0] += 1
                slot[1] += size
                if len(examples) < 20:
                    examples.append({"rel": rel, "bytes": size})
        result["targets"][name] = {
            "root": str(root), "exists": True, "files": n, "bytes": total,
            "safe_files": safe, "safe_bytes": safe_bytes,
            "unsafe_files": unsafe, "unsafe_bytes": unsafe_bytes,
            "unsafe_real_files": unsafe_real,
            "unsafe_real_bytes": unsafe_real_bytes,
            "metadata_ignored_files": meta_ignored[0],
            "metadata_ignored_bytes": meta_ignored[1],
            "unsafe_examples": examples,
            "unsafe_by_ext": {k: {"files": v[0], "bytes": v[1]}
                              for k, v in sorted(unsafe_by_ext.items())},
            "unsafe_largest": [{"rel": r, "bytes": s}
                               for s, r in sorted(unsafe_all,
                                                  reverse=True)[:50]],
            "safe": unsafe_real == 0 and n > 0,
        }
        if verbose:
            t = result["targets"][name]
            print(f"[audit] {name}: {n} 文件 / {human_bytes(total)}；"
                  f"可删 {safe} / {human_bytes(safe_bytes)}；"
                  f"不可删 {unsafe} / {human_bytes(unsafe_bytes)}"
                  + (f"（其中元数据 {meta_ignored[0]} / "
                     f"{human_bytes(meta_ignored[1])} 已忽略）"
                     if allow_metadata and meta_ignored[0] else ""))
            for ex in examples[:5]:
                print(f"    ✗ {ex['rel']}  {human_bytes(ex['bytes'])}")
    return result


def inventory_dir() -> Path | None:
    for p in (REPO / "out/oss_inventory/2026-11-17",
              REPO / "livepipe/docs/oss_inventory/2026-11-17"):
        if (p / "manifests").is_dir():
            return p
    return None


def configs_from_inventory() -> list[Path]:
    inv = inventory_dir()
    cfgs = []
    if inv:
        for mp in sorted((inv / "manifests").glob("*.json")):
            try:
                data = json.loads(mp.read_text("utf-8"))
                cfgs.append(Path(data["config"]))
            except Exception:  # noqa: BLE001
                continue
    return cfgs


def cmd_migrate_emoji(args) -> int:
    """把外置 out 的表情表复制到不参与 P4 删除的本地 assets，并改 base_dir。"""
    ext = root_of("external_out")
    assets = Path(os.path.expanduser(args.assets))
    dirs = sorted(ext.glob("*_emoji"))
    if not dirs:
        print(f"[error] 外置 out 里没有 *_emoji：{ext}")
        return 1
    print(f"表情表 {len(dirs)} 套 → {assets}/out")
    total = 0
    for d in dirs:
        for p in d.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    print(f"共 {human_bytes(total)}")
    cfgs = configs_from_inventory()
    print(f"配置 {len(cfgs)} 份（来自 P1 inventory manifests）")
    if not args.apply:
        for d in dirs:
            print(f"  copy {d} → {assets / 'out' / d.name}")
        for c in cfgs:
            text = c.read_text("utf-8")
            if f'"base_dir": "{ext.parent}"' in text:
                print(f"  base_dir {c.name}: {ext.parent} → {assets}")
        print("dry-run：加 --apply 执行")
        return 0

    (assets / "out").mkdir(parents=True, exist_ok=True)
    for d in dirs:
        dst = assets / "out" / d.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(d, dst)
        print(f"  copied {d.name}")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup = Path.home() / ".livepipe_p4_config_backup" / stamp
    backup.mkdir(parents=True, exist_ok=False)
    changed = 0
    old_lit = f'"base_dir": "{ext.parent}"'
    new_lit = f'"base_dir": "{assets}"'
    for c in cfgs:
        text = c.read_text("utf-8")
        if old_lit not in text:
            if new_lit in text:
                print(f"  {c.name}: 已是 {assets}")
            else:
                print(f"  [warn] {c.name}: 找不到 base_dir={ext.parent}")
            continue
        shutil.copy2(c, backup / c.name)
        c.write_text(text.replace(old_lit, new_lit), "utf-8")
        changed += 1
        print(f"  {c.name}: base_dir → {assets}")
    print(f"改 {changed} 份配置；备份 → {backup}")
    return 0


def emoji_blockers() -> list[str]:
    ext = root_of("external_out")
    if not ext.exists():
        return []
    hits = list(ext.glob("*_emoji/*-emoji.json"))
    if not hits:
        return []
    old_lit = f'"base_dir": "{ext.parent}"'
    cfgs = [c for c in configs_from_inventory()
            if old_lit in c.read_text("utf-8")]
    if not cfgs:
        return []
    return [f"{len(hits)} 张表情表在外置 out，且 {len(cfgs)} 份配置 base_dir "
            f"仍指 {ext.parent}；先跑 migrate-emoji"]


def shadow_info(state: dict) -> dict:
    start = state.get("shadow_started_at")
    if not start:
        return {"started": False}
    t0 = calendar.timegm(time.strptime(start, "%Y-%m-%dT%H:%M:%SZ"))
    elapsed = (time.time() - t0) / 86400.0
    return {"started": True, "shadow_started_at": start,
            "elapsed_days": round(elapsed, 2),
            "remaining_days": round(max(0.0, SHADOW_DAYS - elapsed), 2),
            "earliest_apply_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0 + SHADOW_DAYS * 86400)),
            "ready": elapsed >= SHADOW_DAYS}


def cmd_shadow_start(args) -> int:
    state = load_state()
    if state.get("shadow_started_at") and not args.reset:
        print("影子期已开始：" + state["shadow_started_at"]
              + "（--reset 可重开）")
    else:
        state = {"version": 1, "shadow_started_at": iso_now(),
                 "note": "P4 影子期：7 天内只审计，不删本地"}
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n",
                         "utf-8")
        print("影子期开始：" + state["shadow_started_at"] + f"（{SHADOW_DAYS} 天）")
    print(json.dumps(shadow_info(state), ensure_ascii=False, indent=1))
    return 0


def cmd_shadow_status(_args) -> int:
    info = shadow_info(load_state())
    print(json.dumps(info, ensure_ascii=False, indent=1))
    return 0


def cmd_audit(args) -> int:
    selected = parse_targets(args.targets)
    result = audit_targets(selected, allow_metadata=args.allow_metadata)
    rep = Path(args.report).expanduser() if args.report else (
        REPO / "out/p4" / f"audit-{time.strftime('%Y%m%d-%H%M%S')}.json")
    rep.parent.mkdir(parents=True, exist_ok=True)
    rep.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                   "utf-8")
    print(f"[audit] 报告 → {rep}")
    bad = sum(1 for t in result["targets"].values()
              if t.get("exists") and not t.get("safe"))
    return 0 if bad == 0 else 1


def check_inputs() -> list[str]:
    """P4 的 inputs 策略：82 段 OSS 输入都在且能读出时长。"""
    blockers: list[str] = []
    p15 = REPO / "scripts/p15_repoint.py"
    r = subprocess.run([sys.executable, str(p15), "--verify"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        blockers.append("p15_repoint --verify 未通过（82 段 OSS 路径/字节不符）")
        print((r.stderr or r.stdout or "").strip()[-800:])
    inv = inventory_dir()
    if not inv:
        blockers.append("找不到 inventory manifests（out 与 livepipe/docs 都没有）")
        return blockers
    targets: set[str] = set()
    for mp in sorted((inv / "manifests").glob("*.json")):
        try:
            data = json.loads(mp.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for seg in data.get("segments") or []:
            if seg.get("oss"):
                targets.add(seg["oss"])
    if not targets:
        blockers.append("找不到 inventory manifest 的 OSS 目标")
        return blockers
    from livepipe.tools.oss_ingest.common import probe_duration
    bad = []
    for i, t in enumerate(sorted(targets), 1):
        d = probe_duration(t) if Path(t).exists() else None
        if not d or d <= 0:
            bad.append(t)
        if i % 10 == 0 or i == len(targets):
            print(f"  [inputs] ffprobe {i}/{len(targets)}")
    if bad:
        blockers.append(f"{len(bad)} 段 OSS 输入读不出有效时长（示例 {bad[0]}）")
    print(f"[inputs] OSS 段 {len(targets)} 个；不可读 {len(bad)} 个")
    return blockers


def cmd_plan(args) -> int:
    selected = parse_targets(args.targets)
    state = load_state()
    info = shadow_info(state)
    print("影子期：" + json.dumps(info, ensure_ascii=False))
    if args.policy == "inputs":
        blockers = check_inputs()
        if "external_out" in selected:
            blockers += emoji_blockers()
        for name in selected:
            n, total, _files = scan_files(root_of(name))
            print(f"[plan] {name}: {n} 文件 / {human_bytes(total)}（inputs 策略："
                  "不保留本地结果，删后靠 OSS 录屏重跑）")
    else:
        result = audit_targets(selected, allow_metadata=args.allow_metadata)
        blockers = emoji_blockers() if "external_out" in selected else []
        bad = [n for n, t in result["targets"].items()
               if t.get("exists") and not t.get("safe")]
        if bad:
            blockers.append("目标仍有不可删文件：" + ", ".join(bad))
    if not info.get("ready"):
        blockers.append(f"影子期未满（还需 {info.get('remaining_days', SHADOW_DAYS)} 天）")
    print("\n闸门：" + ("PASS" if not blockers else "BLOCK"))
    for b in blockers:
        print("  ✗ " + b)
    if not blockers:
        print("可以执行：python3 scripts/p4_cleanup.py apply --targets "
              + ",".join(selected) + f" --policy {args.policy} --yes")
    return 0 if not blockers else 1


def cmd_apply(args) -> int:
    if not args.yes:
        print("[error] apply 需要 --yes（先跑 plan 看闸门）", file=sys.stderr)
        return 2
    selected = parse_targets(args.targets)
    info = shadow_info(load_state())
    if not info.get("ready") and not args.ignore_shadow:
        print(f"[error] 影子期未满（{info.get('remaining_days')} 天）；"
              "确认要跳过请加 --ignore-shadow", file=sys.stderr)
        return 1
    if args.policy == "inputs":
        blockers = check_inputs()
        if "external_out" in selected:
            blockers += emoji_blockers()
        sizes = {}
        for name in selected:
            n, total, _files = scan_files(root_of(name))
            sizes[name] = (n, total)
        result = {"targets": {n: {"exists": root_of(n).exists(),
                                  "files": sizes[n][0],
                                  "bytes": sizes[n][1]}
                              for n in selected}}
    else:
        result = audit_targets(selected, verbose=False,
                               allow_metadata=args.allow_metadata)
        blockers = []
        for name in selected:
            t = result["targets"].get(name) or {}
            if not t.get("exists"):
                continue
            if t.get("unsafe_real_files"):
                blockers.append(f"{name}: {t['unsafe_real_files']} 个文件不在保留根里")
        if "external_out" in selected:
            blockers += emoji_blockers()
    if blockers:
        for b in blockers:
            print("[error] " + b, file=sys.stderr)
        print("[error] 拒绝删除；先补同步/迁移或从 --targets 里去掉", file=sys.stderr)
        return 1

    total_files = sum((result["targets"].get(n) or {}).get("files", 0)
                      for n in selected
                      if (result["targets"].get(n) or {}).get("exists"))
    total_bytes = sum((result["targets"].get(n) or {}).get("bytes", 0)
                      for n in selected
                      if (result["targets"].get(n) or {}).get("exists"))
    print(f"将删除：{', '.join(selected)}，{total_files} 文件 / "
          f"{human_bytes(total_bytes)}")
    started = time.monotonic()
    freed = 0
    for name in selected:
        root = root_of(name)
        if not root.exists():
            print(f"跳过 {name}：不存在")
            continue
        t = result["targets"].get(name) or {}
        rp = root.resolve()
        if str(rp) in ("/", str(Path.home())) or len(rp.parts) < 3:
            print(f"[error] 拒绝删除可疑路径：{rp}", file=sys.stderr)
            return 1
        print(f"删除 {name}：{root}（{t.get('files', 0)} 文件 / "
              f"{human_bytes(t.get('bytes', 0))}）")
        try:
            shutil.rmtree(root)
        except OSError as e:
            print(f"[error] 删除 {name} 失败：{e}", file=sys.stderr)
            return 1
        freed += t.get("bytes", 0)
        print(f"完成 {name}")
    print(f"清盘完成：释放 {human_bytes(freed)}，耗时 "
          f"{time.monotonic() - started:.1f}s")
    return 0


def cmd_rollback_check(_args) -> int:
    backups: list[Path] = []
    for base in (REPO / "out/p1.5/backup",
                 Path.home() / ".livepipe_p4_audit"):
        if base.is_dir():
            backups += [p for p in base.iterdir()
                        if p.is_dir() and len(list(p.glob("*.json"))) >= 20]
    if not backups:
        print("[error] 找不到 P1.5 备份（out/p1.5/backup 或 "
              "~/.livepipe_p4_audit）；无法回滚到本地路径")
        return 1
    latest = sorted(backups)[-1]
    cfgs = sorted(latest.glob("*.json"))
    print(f"P1.5 备份：{latest}（{len(cfgs)} 份配置）")
    local_paths = set()
    for cfg in cfgs:
        try:
            raw = json.loads(cfg.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            text = cfg.read_text("utf-8")
            for line in text.splitlines():
                s = line.strip().strip(",").strip('"')
                if s.startswith("/") and s.endswith(".mp4"):
                    local_paths.add(s)
            continue
        for v in (raw.get("input", {}).get("videos") or []):
            if isinstance(v, str) and v.startswith("/"):
                local_paths.add(v)
    missing = [p for p in sorted(local_paths) if not Path(p).exists()]
    print(f"本地视频路径：{len(local_paths)} 条；不存在 {len(missing)} 条")
    for p in missing[:10]:
        print("  ✗ " + p)
    if missing:
        print("[warn] 本地路径已被删/未挂载；回滚到本地不可用，配置应保持指向 OSS")
        return 1
    print("回滚命令：python3 scripts/p15_repoint.py --rollback " + str(latest))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P4 清盘与回滚（影子期 + 覆盖审计 + 闸门）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("shadow-start", help="开始 7 天影子期")
    p.add_argument("--reset", action="store_true", help="重置影子起点")
    p.set_defaults(func=cmd_shadow_start)
    p = sub.add_parser("shadow-status", help="查看影子期")
    p.set_defaults(func=cmd_shadow_status)

    for name, func, help_ in (
            ("audit", cmd_audit, "只读审计覆盖"),
            ("plan", cmd_plan, "打印删除计划与闸门")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--targets", default=None,
                       help=f"逗号分隔：{TARGET_HELP}；all=全部存在的目标")
        p.add_argument("--allow-metadata", action="store_true",
                       help="忽略批次元数据（run.sh/batch.log/.batch_runner.*/批次 JSON）")
        p.add_argument("--policy", choices=("results", "inputs"), default="results",
                       help="results=要求每个本地文件在保留根有副本；"
                            "inputs=只要求 82 段 OSS 录屏可读（结果可重跑）")
        if name == "audit":
            p.add_argument("--report", default=None)
        p.set_defaults(func=func)

    p = sub.add_parser("apply", help="影子到期且 0 不可删文件后删除")
    p.add_argument("--targets", required=True)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--ignore-shadow", action="store_true",
                   help="跳过 7 天影子期（仍要求 0 不可删文件）")
    p.add_argument("--allow-metadata", action="store_true",
                   help="忽略批次元数据（run.sh/batch.log/.batch_runner.*/批次 JSON）")
    p.add_argument("--policy", choices=("results", "inputs"), default="results",
                   help="results=保留每个本地结果；inputs=只保证 OSS 录屏可读")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("rollback-check", help="检查 P1.5 本地备份是否可回滚")
    p.set_defaults(func=cmd_rollback_check)

    p = sub.add_parser("migrate-emoji",
                       help="把外置 out 表情表复制到本地 assets 并改 base_dir")
    p.add_argument("--assets", default="~/livepipe_assets")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_migrate_emoji)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
