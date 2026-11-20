#!/usr/bin/env python3
"""P2 回填：把本地 out 结果按事件树同步到 OSS ``直播产生结果``。

两个来源：

- 外置 out（``/run/media/puyu/C0DE6A4BDE6A39B4/out``）：按事件目录逐个同步；
  ``dataset_*`` / ``*_emoji`` / ``_backup*`` / ``media``（录屏副本）不参与。
- 本地 out（仓库 ``out/``）：``2026-0930-1006全球车的中国路`` 按天同步；另外
  4 个 ``MM-DD_全球车的中国路_视频号/视频号`` 旧批次目录归到同一个事件。

落点：``直播录屏/…`` 之外的 ``直播产生结果/<事件>/…``；全球车的中国路统一成
``2026-0930-1006《全球车的中国路》/<MM.DD>/…``（与 P2 验收的 10.04 一致）。

两阶段：

- ``deliverables``：先传所有 json/jsonl/md/txt/csv/xlsx/html + ``all_users_*``
  交付图 + ``撞库结果``；体积小，产品先可用。
- ``all``：再补其余文件（主要是 seg 帧图）；``oss_sync`` 按大小跳过已传的，
  所以第二阶段会快速掠过第一阶段产物。

每个阶段可断点续传：目标同尺寸文件跳过；state 记在
``~/.livepipe_p2_backfill/state.json``。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OSS_SYNC = REPO / "scripts/oss_sync.py"
DEST_ROOT = Path.home() / "oss-bucket/web/1/奇瑞汽车/直播看管/直播产生结果"
EXT_OUT = Path("/run/media/puyu/C0DE6A4BDE6A39B4/out")
LOCAL_OUT = REPO / "out"
STATE_DIR = Path.home() / ".livepipe_p2_backfill"
STATE = STATE_DIR / "state.json"

GLOBAL_TOP = "2026-0930-1006全球车的中国路"
GLOBAL_CANON = "2026-0930-1006《全球车的中国路》"
LEGACY_DAYS = {
    "09-30_全球车的中国路_视频号": "09.30",
    "10-01_全球车的中国路_视频号": "10.01",
    "10-03_全球车的中国路_视频号": "10.03",
    "10-06_全球车的中国路_视频号": "10.06",
}
SKIP_TOP = re.compile(r"^(dataset_|_backup)|(_emoji$)|^oss_inventory$|^p1\.5$|^p4$")
DAY_RE = re.compile(r"^\d{2}-\d{2}$")
EXCLUDES = ("*/media/*", "media/*")
DELIVERABLE_INCLUDES = (
    "*.json", "*.jsonl", "*.md", "*.txt", "*.csv", "*.xlsx", "*.html",
    "*.yaml", "*.yml", "*all_users_*/*", "all_users_*/*", "*撞库结果*/*",
)
MARK = "OSS_SYNC_RESULT "


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1000.0
    return f"{n:.2f} TB"


def log(msg: str) -> None:
    print(msg, flush=True)


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"version": 1, "tasks": {}}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n",
                   "utf-8")
    os.replace(tmp, STATE)


def build_tasks() -> list[dict]:
    tasks: list[dict] = []

    def add(src: Path, dest: Path) -> None:
        if src.is_dir() and any(src.iterdir()):
            tasks.append({"src": str(src), "dest": str(dest)})

    if EXT_OUT.is_dir():
        for top in sorted(EXT_OUT.iterdir()):
            if not top.is_dir() or SKIP_TOP.search(top.name):
                continue
            if top.name == GLOBAL_TOP:
                for day in sorted(top.iterdir()):
                    if day.is_dir() and DAY_RE.match(day.name):
                        add(day, DEST_ROOT / GLOBAL_CANON /
                            day.name.replace("-", "."))
            else:
                add(top, DEST_ROOT / top.name)

    if LOCAL_OUT.is_dir():
        for top in sorted(LOCAL_OUT.iterdir()):
            if not top.is_dir() or SKIP_TOP.search(top.name):
                continue
            if top.name == GLOBAL_TOP:
                for day in sorted(top.iterdir()):
                    if day.is_dir() and DAY_RE.match(day.name):
                        add(day, DEST_ROOT / GLOBAL_CANON /
                            day.name.replace("-", "."))
            elif top.name in LEGACY_DAYS:
                add(top, DEST_ROOT / GLOBAL_CANON / LEGACY_DAYS[top.name])
    return tasks


def task_key(t: dict) -> str:
    safe = re.sub(r"[^\w.-]+", "_", t["src"])[-160:]
    return safe


def run_sync(t: dict, phase: str, workers: int, retries: int) -> dict:
    STATE_DIR.joinpath("reports").mkdir(parents=True, exist_ok=True)
    report = STATE_DIR / "reports" / (task_key(t) + f".{phase}.json")
    cmd = [sys.executable, str(OSS_SYNC), "--src", t["src"],
           "--dst", t["dest"], "--workers", str(workers),
           "--retries", str(retries), "--report", str(report)]
    for ex in EXCLUDES:
        cmd += ["--exclude", ex]
    if phase == "deliverables":
        for inc in DELIVERABLE_INCLUDES:
            cmd += ["--include", inc]
    log(f"\n=== {phase} · {t['src']}\n    → {t['dest']}")
    started = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    result = None
    for line in proc.stdout:
        line = line.rstrip("\n")
        if line:
            log("  " + line)
        if line.startswith(MARK):
            try:
                result = json.loads(line[len(MARK):])
            except json.JSONDecodeError:
                pass
    rc = proc.wait()
    info = result or {"ok": rc == 0, "files": 0, "bytes": 0,
                      "uploaded": 0, "skipped": 0, "failed": rc != 0}
    info.update({"rc": rc, "elapsed_s": round(time.time() - started, 1),
                 "dest": t["dest"], "report": str(report),
                 "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                              time.gmtime())})
    return info


def cmd_plan(args) -> int:
    sys.path.insert(0, str(REPO / "scripts"))
    import fnmatch
    import oss_sync  # noqa: E402
    tasks = build_tasks()
    if args.task:
        tasks = [t for t in tasks if args.task in t["src"]]
    total_n = total_b = total_dn = total_db = 0
    for t in tasks:
        entries, _ = oss_sync.scan_source(
            t["src"], tuple(oss_sync.DEFAULT_EXCLUDES) + EXCLUDES, False)
        n2 = len(entries)
        b2 = sum(e["size"] for e in entries)
        dn = db = 0
        for e in entries:
            if any(fnmatch.fnmatch(e["rel"], p)
                   or fnmatch.fnmatch(os.path.basename(e["rel"]), p)
                   for p in DELIVERABLE_INCLUDES):
                dn += 1
                db += e["size"]
        total_n += n2
        total_b += b2
        total_dn += dn
        total_db += db
        log(f"{t['src']}\n  → {t['dest']}\n"
            f"  交付物 {dn} / {human_bytes(db)}；全部 {n2} / {human_bytes(b2)}")
    log(f"\n任务 {len(tasks)} 个；交付物 {total_dn} / {human_bytes(total_db)}；"
        f"全部 {total_n} 文件 / {human_bytes(total_b)}")
    return 0


def cmd_run(args) -> int:
    if not args.yes:
        print("[error] run 需要 --yes（先跑 plan）", file=sys.stderr)
        return 2
    tasks = build_tasks()
    if args.task:
        tasks = [t for t in tasks if args.task in t["src"]]
    if args.limit:
        tasks = tasks[:args.limit]
    state = load_state()
    ok = fail = skip = 0
    for i, t in enumerate(tasks, 1):
        key = task_key(t)
        st = state["tasks"].setdefault(key, {"src": t["src"]})
        prev = st.get(args.phase) or {}
        if prev.get("ok") and not args.force:
            log(f"[{i}/{len(tasks)}] 跳过 {key}：{args.phase} 已完成")
            skip += 1
            continue
        info = run_sync(t, args.phase, args.workers, args.retries)
        st[args.phase] = info
        st["dest"] = t["dest"]
        st["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save_state(state)
        if info.get("ok") and not info.get("failed"):
            ok += 1
            log(f"[{i}/{len(tasks)}] 完成 {key}：上传 {info.get('uploaded', 0)}，"
                f"跳过 {info.get('skipped', 0)}，{info.get('elapsed_s')}s")
        else:
            fail += 1
            log(f"[{i}/{len(tasks)}] 失败 {key}：rc={info.get('rc')} "
                f"failed={info.get('failed')}")
            if args.on_error == "stop":
                break
    log(f"\n阶段 {args.phase} 结束：成功 {ok}，跳过 {skip}，失败 {fail}")
    return 0 if fail == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P2 回填：out 结果 → OSS 直播产生结果")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="列出任务与两阶段体积")
    p.add_argument("--task", default=None, help="只匹配 src 路径包含该串的任务")
    p.set_defaults(func=cmd_plan)
    p = sub.add_parser("run", help="执行同步（可断点续传）")
    p.add_argument("--phase", choices=("deliverables", "all"), required=True)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--task", default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--force", action="store_true")
    p.add_argument("--on-error", choices=("continue", "stop"), default="continue")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_run)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
