#!/usr/bin/env python3
"""P1.5 切读：把 22 份配置的 ``input.videos`` 从本地路径改成 OSS 绝对路径。

只改配置文本里的视频路径字符串，其余字节原样保留（JSONC 注释、排版不动）。
默认 dry-run；``--apply`` 才写，写前备份到 ``out/p1.5/backup/<UTC>/``。
``--verify`` 只核对配置里的路径是否已全部是计划目标（可重复跑做验收）。
``--rollback`` 可从备份目录整体回滚。

映射来源：
- 整段：inventory manifest ``segments[].oss``（已按字节匹配）。
- 裁尾：``p1-upload-list.csv`` 的 ``src_abs -> dst``（P1 已补传的副本）。

退出码：0 = 82/82 段都有可读且字节相符的 OSS 目标；1 = 有缺口；
2 = 参数/输入错误。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_INV = REPO / "out/oss_inventory/2026-11-17"
DEFAULT_OUT = REPO / "out/p1.5"


def _load_plan(inv: Path) -> list[dict]:
    manifests = sorted((inv / "manifests").glob("*.json"))
    if not manifests:
        raise SystemExit(f"找不到 manifest：{inv / 'manifests'}")
    trim_by_src: dict[str, dict] = {}
    with open(inv / "p1-upload-list.csv", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row["kind"] == "trim":
                trim_by_src[row["src_abs"]] = row
    plan = []
    for mp in manifests:
        m = json.loads(mp.read_text("utf-8"))
        cfg = Path(m["config"])
        text = cfg.read_text("utf-8")
        segs = []
        for seg in m["segments"]:
            disk = seg["disk"]
            if seg["trimmed"]:
                row = trim_by_src.get(disk)
                target = str(Path(row["dst"]).expanduser()) if row else None
            else:
                target = seg["oss"]
            segs.append({
                "seg": seg["seg"], "disk": disk, "target": target,
                "bytes": seg["bytes"], "trimmed": bool(seg["trimmed"]),
            })
        plan.append({"config": cfg, "text": text, "segments": segs})
    return plan


def _seg_state(item: dict, seg: dict) -> str:
    """配置文本里这段的路径状态：``pending`` / ``oss`` / ``both`` / ``missing``。"""
    src_lit = json.dumps(seg["disk"], ensure_ascii=False)
    dst_lit = json.dumps(seg["target"], ensure_ascii=False)
    in_src = src_lit in item["text"]
    in_dst = dst_lit in item["text"]
    if in_src and in_dst:
        return "both"
    if in_dst:
        return "oss"
    if in_src:
        return "pending"
    return "missing"


def _check_plan(plan: list[dict]) -> list[str]:
    errors: list[str] = []
    for item in plan:
        for seg in item["segments"]:
            src, dst = seg["disk"], seg["target"]
            state = _seg_state(item, seg)
            if state == "missing":
                errors.append(f"{item['config'].name} {seg['seg']}："
                              f"配置文本里既没有源路径也没有 OSS 目标")
            elif state == "both":
                errors.append(f"{item['config'].name} {seg['seg']}："
                              f"源路径与 OSS 目标同时出现在配置里")
            if not dst:
                errors.append(f"{item['config'].name} {seg['seg']}：没有 OSS 目标")
                continue
            p = Path(dst)
            if not p.is_file():
                errors.append(f"{item['config'].name} {seg['seg']}：目标不存在 {dst}")
                continue
            size = p.stat().st_size
            if size != seg["bytes"]:
                errors.append(f"{item['config'].name} {seg['seg']}："
                              f"目标字节不符 {size} != {seg['bytes']}")
            if src == dst:
                errors.append(f"{item['config'].name} {seg['seg']}：源与目标相同")
    return errors


def _rewrite(item: dict) -> tuple[str, list[str]]:
    text = item["text"]
    done: list[str] = []
    for seg in item["segments"]:
        state = _seg_state(item, seg)
        if state == "oss":
            done.append(f"{seg['seg']}:已是 OSS")
            continue
        if state != "pending":
            raise SystemExit(
                f"{item['config'].name} {seg['seg']}：配置里的路径状态是 {state!r}，"
                f"既不是待改写也不是已是 OSS")
        src_lit = json.dumps(seg["disk"], ensure_ascii=False)
        dst_lit = json.dumps(seg["target"], ensure_ascii=False)
        text = text.replace(src_lit, dst_lit)
        done.append(f"{seg['seg']}:改写")
    return text, done


def _backup_and_write(plan: list[dict], out: Path) -> Path | None:
    rewrites = []
    for item in plan:
        new_text, _ = _rewrite(item)
        if new_text != item["text"]:
            rewrites.append((item, new_text))
    if not rewrites:
        return None
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup = out / "backup" / stamp
    backup.mkdir(parents=True, exist_ok=False)
    for item, _ in rewrites:
        cfg = item["config"]
        dst = backup / cfg.name
        shutil.copy2(cfg, dst)
    for item, new_text in rewrites:
        cfg = item["config"]
        tmp = cfg.with_name(cfg.name + ".p15.tmp")
        tmp.write_text(new_text, "utf-8")
        os.replace(tmp, cfg)
    return backup


def _rollback(backup: Path) -> int:
    if not backup.is_dir():
        print(f"[error] 备份目录不存在：{backup}", file=sys.stderr)
        return 2
    n = 0
    for f in sorted(backup.glob("*.json")):
        dst = REPO / f.name
        shutil.copy2(f, dst)
        n += 1
    print(f"已回滚 {n} 份配置 ← {backup}")
    return 0 if n else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P1.5 切读：配置的视频路径指到 OSS")
    ap.add_argument("--inventory", default=str(DEFAULT_INV), metavar="DIR")
    ap.add_argument("--out", default=str(DEFAULT_OUT), metavar="DIR")
    ap.add_argument("--apply", action="store_true", help="写入配置（默认只预览）")
    ap.add_argument("--verify", action="store_true",
                    help="只核对配置里的视频路径是否都已是计划目标（不改文件）")
    ap.add_argument("--rollback", default=None, metavar="BACKUP_DIR",
                    help="从备份目录恢复配置后退出")
    a = ap.parse_args(argv)

    if a.rollback:
        return _rollback(Path(a.rollback).expanduser())

    inv = Path(a.inventory).expanduser()
    if not inv.is_dir():
        print(f"[error] inventory 目录不存在：{inv}", file=sys.stderr)
        return 2

    plan = _load_plan(inv)
    errors = _check_plan(plan)
    total_segs = sum(len(p["segments"]) for p in plan)
    trims = sum(1 for p in plan for s in p["segments"] if s["trimmed"])
    print(f"P1.5 切读计划：{len(plan)} 份配置 · {total_segs} 段"
          f"（整段 {total_segs - trims} / 裁尾 {trims}）")
    for item in plan:
        counts = []
        for seg in item["segments"]:
            state = _seg_state(item, seg)
            counts.append(f"{seg['seg']}×{'OSS' if state == 'oss' else state}")
        print(f"  {item['config'].name:<38} {len(item['segments'])} 段  "
              f"{' '.join(counts)}")
    if errors:
        print(f"\n[error] {len(errors)} 处不可切换：", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        return 1

    if a.verify:
        if any(_seg_state(item, seg) != "oss"
               for item in plan for seg in item["segments"]):
            print("\n[error] 还有段没指到 OSS", file=sys.stderr)
            return 1
        print(f"\n[ok] {len(plan)} 份配置 / {total_segs} 段全部指向 OSS")
        return 0

    if not a.apply:
        print("\ndry-run：未改任何文件；确认无误后加 --apply")
        return 0

    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    backup = _backup_and_write(plan, out)
    if backup is None:
        print("\n全部已是 OSS，无文件改动（未写备份/报告）")
        return 0
    report = {
        "applied_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inventory": str(inv),
        "backup": str(backup),
        "configs": len(plan),
        "segments": total_segs,
        "trimmed": trims,
        "items": [
            {"config": str(p["config"]), "segments": p["segments"]}
            for p in plan
        ],
    }
    rp = out / "repoint-report.json"
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"\n已改写 {len(plan)} 份配置 / {total_segs} 段；备份 → {backup}")
    print(f"报告 → {rp}")
    print(f"回滚：python3 scripts/{Path(__file__).name} --rollback {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
