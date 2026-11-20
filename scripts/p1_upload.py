#!/usr/bin/env python3
"""按 P0 交付的 ``p1-upload-list.csv`` 把 29 个对象补传到 OSS（P1）。

行为：

- 逐行核对源文件存在且字节数吻合；目标已存在且字节相符 → 跳过（可重复跑）。
- 上传先写同目录 ``<名>.part``，close 后核对字节，再 ``os.replace`` 成正式名；
  失败清理 ``.part`` 并重试。绝不覆盖字节不同的同名目标（会直接报错）。
- 默认 4 个并发；``--verify-only`` 只核对不写；``--dry-run`` 只打计划。
- 结束后写 ``p1-upload-report.json``（逐条状态/字节/耗时/速度）；``--verify-only``
  默认写 ``p1-verify-report.json``，不覆盖上传报告（可用 ``--report`` 显式指定）。

退出码：0 = 29 条全部在目标且字节相符；1 = 有失败；2 = 输入错误。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = (_REPO_ROOT / "out" / "oss_inventory" / "2026-11-17"
               / "p1-upload-list.csv")
DEFAULT_REPORT = (_REPO_ROOT / "out" / "oss_inventory" / "2026-11-17"
                  / "p1-upload-report.json")
_CHUNK = 8 * 1024 * 1024
_PRINT_LOCK = threading.Lock()


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1000.0
    return f"{n:.2f} TB"


def _log(msg: str) -> None:
    with _PRINT_LOCK:
        print(msg, flush=True)


def load_rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"CSV 没有数据行：{path}")
    for i, row in enumerate(rows, start=2):
        for key in ("config", "seg", "kind", "src_abs", "dst", "bytes"):
            if not row.get(key):
                raise SystemExit(f"CSV 第 {i} 行缺少 {key}")
        row["bytes"] = int(row["bytes"])
        row["src_abs"] = str(Path(row["src_abs"]).expanduser())
        row["dst"] = str(Path(row["dst"]).expanduser())
        row["row"] = i
    dsts = [r["dst"] for r in rows]
    dup = {d for d in dsts if dsts.count(d) > 1}
    if dup:
        raise SystemExit(f"CSV 里有重复目标：{sorted(dup)[:3]}")
    return rows


def preflight(rows: list[dict]) -> list[str]:
    errors: list[str] = []
    for row in rows:
        src = Path(row["src_abs"])
        if not src.is_file():
            errors.append(f"源文件不存在：{src}")
            continue
        size = src.stat().st_size
        if size != row["bytes"]:
            errors.append(
                f"源文件字节不符：{src}（{size} != {row['bytes']}）")
    return errors


def target_state(row: dict) -> tuple[str, int]:
    """返回 (``present`` / ``missing`` / ``conflict``, 目标字节数)。"""
    dst = Path(row["dst"])
    try:
        if dst.is_file():
            size = dst.stat().st_size
            return ("present" if size == row["bytes"] else "conflict"), size
        if dst.exists():
            return "conflict", -1
    except OSError:
        pass
    return "missing", -1


def upload_one(row: dict, *, retries: int = 3) -> dict:
    src = Path(row["src_abs"])
    dst = Path(row["dst"])
    result = {
        "row": row["row"], "config": row["config"], "seg": row["seg"],
        "kind": row["kind"], "src": str(src), "dst": str(dst),
        "bytes": row["bytes"], "status": "failed", "elapsed_s": None,
        "mbps": None, "error": None,
    }
    state, size = target_state(row)
    if state == "present":
        result["status"] = "present"
        return result
    if state == "conflict":
        result["error"] = f"目标已存在但字节不符（{size} != {row['bytes']}），拒绝覆盖"
        _log(f"[跳过·冲突] {dst}：{result['error']}")
        return result

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.parent / (dst.name + ".part")
    last_error: BaseException | None = None
    for attempt in range(1, retries + 1):
        started = time.monotonic()
        try:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError as e:
                raise OSError(f"无法清理残留 .part：{tmp}（{e}）") from e
            with open(src, "rb") as fin, open(tmp, "wb") as fout:
                shutil.copyfileobj(fin, fout, length=_CHUNK)
            written = tmp.stat().st_size
            if written != row["bytes"]:
                raise OSError(
                    f"写入字节不符：{tmp}（{written} != {row['bytes']}）")
            os.replace(tmp, dst)
            final = dst.stat().st_size
            if final != row["bytes"]:
                raise OSError(
                    f"落盘后字节不符：{dst}（{final} != {row['bytes']}）")
            elapsed = time.monotonic() - started
            result.update(status="uploaded", elapsed_s=round(elapsed, 3),
                          mbps=round(row["bytes"] / 1e6 / elapsed, 2)
                          if elapsed > 0 else None)
            _log(f"[上传完成] {row['kind']}/{row['seg']} "
                 f"{_human(row['bytes'])} → {dst} "
                 f"（{elapsed:.1f}s，{result['mbps']} MB/s）")
            return result
        except (OSError, shutil.Error) as e:
            last_error = e
            _log(f"[重试 {attempt}/{retries}] {dst}：{e}")
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            if attempt < retries:
                time.sleep(min(2 ** attempt, 10))
    result["error"] = str(last_error)
    return result


def verify_one(row: dict) -> dict:
    state, size = target_state(row)
    return {
        "row": row["row"], "config": row["config"], "seg": row["seg"],
        "kind": row["kind"], "src": row["src_abs"], "dst": row["dst"],
        "bytes": row["bytes"], "status": state, "actual": size,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="按 p1-upload-list.csv 补传 29 个对象到 OSS")
    p.add_argument("--csv", default=str(DEFAULT_CSV), metavar="PATH")
    p.add_argument("--report", default=None, metavar="PATH")
    p.add_argument("--workers", type=int, default=4, metavar="N")
    p.add_argument("--retries", type=int, default=3, metavar="N")
    p.add_argument("--limit", type=int, default=0, metavar="N",
                   help="只处理前 N 条（0 = 全部）")
    p.add_argument("--dry-run", action="store_true", help="只打印计划")
    p.add_argument("--verify-only", action="store_true", help="只核对目标，不写")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    csv_path = Path(args.csv).expanduser()
    if args.report:
        report_path = Path(args.report).expanduser()
    elif args.verify_only:
        # 只核在位的报告不能覆盖上传报告（后者含逐条耗时/速度）。
        report_path = csv_path.parent / "p1-verify-report.json"
    else:
        report_path = DEFAULT_REPORT
    if not csv_path.is_file():
        print(f"[error] CSV 不存在：{csv_path}", file=sys.stderr)
        return 2
    rows = load_rows(csv_path)
    if args.limit > 0:
        rows = rows[:args.limit]
    errors = preflight(rows)
    if errors:
        print("[error] 上传前核对失败：", file=sys.stderr)
        for err in errors:
            print(f"  ✗ {err}", file=sys.stderr)
        return 2

    total_bytes = sum(r["bytes"] for r in rows)
    print(f"P1 上传清单：{len(rows)} 个对象 · {total_bytes:,} B "
          f"({_human(total_bytes)})")
    if args.dry_run:
        for r in rows:
            state, size = target_state(r)
            print(f"  [{r['kind']:4}] {r['seg']:4} {r['bytes']:>13,} B "
                  f"{state:8} → {r['dst']}")
        print("dry-run：未写入任何文件")
        return 0

    started = time.monotonic()
    results: list[dict] = []
    if args.verify_only:
        for r in rows:
            results.append(verify_one(r))
    else:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(upload_one, r, retries=args.retries): r
                       for r in rows}
            for fut in as_completed(futures):
                results.append(fut.result())
    elapsed = time.monotonic() - started

    results.sort(key=lambda x: x["row"])
    uploaded = [r for r in results if r["status"] == "uploaded"]
    present = [r for r in results if r["status"] == "present"]
    failed = [r for r in results if r["status"] not in ("uploaded", "present")]
    moved = sum(r["bytes"] for r in uploaded)
    print(f"结果：上传 {len(uploaded)} · 已在位 {len(present)} · "
          f"失败 {len(failed)}；本次写入 {moved:,} B（{_human(moved)}），"
          f"总耗时 {elapsed:.1f}s"
          + (f"，{moved / 1e6 / elapsed:.2f} MB/s" if moved and elapsed else ""))
    for r in failed:
        print(f"  ✗ row {r['row']} {r['kind']} {r['seg']} → {r['dst']}"
              f"：{r.get('error') or r['status']}")

    report = {
        "csv": str(csv_path),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "elapsed_s": round(elapsed, 3),
        "objects": len(rows),
        "bytes": total_bytes,
        "uploaded": len(uploaded),
        "present": len(present),
        "failed": len(failed),
        "items": results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"报告 → {report_path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
