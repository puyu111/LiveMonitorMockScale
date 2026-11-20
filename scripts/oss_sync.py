#!/usr/bin/env python3
"""把一个本地结果目录同步到 OSS（goofys 挂载点），按大小 + 可选 SHA256 增量复制。

用途是 livepipe 任务结果的「收尾同步」：任务产物先写本地，跑完后整体同步到
``直播产生结果/...``；重跑时同大小的文件直接跳过，只补新增/变化的文件。

行为：

- 遍历 ``--src``：跳过符号链接（goofys 不支持软链）、隐藏文件/目录
  （``.batch_runner.json`` 这类元数据）以及 ``*.part`` / ``*.lock`` / ``*.failed``。
- 逐文件比较目标：存在且大小相同 → 跳过；不同 → 直接写正式名（结果文件小，
  失败可重跑；goofys 的 rename 在含 `+` 的目录上会 404），close 后核对字节。
  失败重试 3 次，并清理旧版本留下的 `<名>.part`。
- ``--sha256`` 对大小相同的文件再比内容哈希（关键批次用）；上传后再读回校验。
- ``--dry-run`` 只算要传多少；``--verify-only`` 只核对源与目标的对象数/字节
  （默认目标多出的文件也算失败，``--allow-extra`` 放宽）。
- 并发默认 4（goofys 小文件元数据是瓶颈，实测约 2–13 个/秒，取决于文件大小/缓存）。
- 结束时打印一行 ``OSS_SYNC_RESULT {json}``，供 batch_local / batch_remote
  解析；``--report`` 另写 JSON 报告。

退出码：0 = 成功/一致；1 = 有失败或核对不一致；2 = 参数错误。
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

MARK = "OSS_SYNC_RESULT "
CHUNK = 8 * 1024 * 1024
DEFAULT_WORKERS = 4
DEFAULT_RETRIES = 3
DEFAULT_EXCLUDES = ("*.part", "*.lock", "*.failed")
SKIP_DIR_NAMES = {"__pycache__"}

_print_lock = threading.Lock()


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1000.0
    return f"{n:.2f} TB"


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def _is_hidden_or_excluded(rel: str, name: str, excludes: tuple[str, ...],
                           include_hidden: bool) -> bool:
    if not include_hidden and name.startswith("."):
        return True
    return any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat)
               for pat in excludes)


def scan_source(src: str, excludes: tuple[str, ...],
                include_hidden: bool,
                includes: tuple[str, ...] = ()) -> tuple[list[dict], int]:
    """返回 (文件列表, 跳过的符号链接数)。文件列表按相对路径排序。"""
    entries: list[dict] = []
    symlinks = 0
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        keep_dirs = []
        for d in sorted(dirnames):
            p = os.path.join(dirpath, d)
            rel = os.path.relpath(p, src)
            if os.path.islink(p):
                symlinks += 1
                continue
            if d in SKIP_DIR_NAMES or _is_hidden_or_excluded(
                    rel, d, excludes, include_hidden):
                continue
            keep_dirs.append(d)
        dirnames[:] = keep_dirs
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, src)
            if os.path.islink(p):
                symlinks += 1
                continue
            if _is_hidden_or_excluded(rel, name, excludes, include_hidden):
                continue
            if includes and not any(
                    fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat)
                    for pat in includes):
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            if not os.path.isfile(p):
                continue
            entries.append({"rel": rel, "src": p, "size": st.st_size})
    entries.sort(key=lambda e: e["rel"])
    return entries, symlinks


def _stat_size(path: str) -> int | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_size if os.path.isfile(path) else None


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_one(entry: dict, dst_root: str, verify_sha: bool,
              retries: int) -> dict:
    rel = entry["rel"]
    src = entry["src"]
    size = entry["size"]
    dst = os.path.join(dst_root, rel)
    out = {"rel": rel, "size": size, "action": "failed", "error": None,
           "sha256": None}
    dst_size = _stat_size(dst)
    if dst_size == size:
        if verify_sha:
            try:
                if sha256_file(src) == sha256_file(dst):
                    out["action"] = "skipped"
                    return out
            except OSError as e:
                out["error"] = f"哈希读取失败：{e}"
                return out
        else:
            out["action"] = "skipped"
            return out

    parent = os.path.dirname(dst)
    legacy = dst + ".part"
    last_error = None
    for attempt in range(1, retries + 1):
        started = time.monotonic()
        try:
            os.makedirs(parent, exist_ok=True)
            # 旧版本/中断留下的 .part 清掉；结果文件直接写正式名。
            # （goofys 的 rename 在含 '+' 的目录上会 404，结果文件小，直接写更稳。）
            if os.path.exists(legacy):
                try:
                    os.unlink(legacy)
                except OSError:
                    pass
            h = hashlib.sha256() if verify_sha else None
            with open(src, "rb") as fin, open(dst, "wb") as fout:
                while True:
                    chunk = fin.read(CHUNK)
                    if not chunk:
                        break
                    fout.write(chunk)
                    if h is not None:
                        h.update(chunk)
                fout.flush()
                try:
                    os.fsync(fout.fileno())
                except OSError:
                    pass
            final = _stat_size(dst)
            if final != size:
                raise OSError(f"写入字节不符：{final} != {size}")
            if verify_sha:
                if h is None or sha256_file(dst) != h.hexdigest():
                    raise OSError("SHA256 校验不一致")
                out["sha256"] = h.hexdigest()
            out["action"] = "uploaded"
            out["seconds"] = round(time.monotonic() - started, 3)
            return out
        except OSError as e:
            last_error = e
            try:
                if os.path.exists(legacy):
                    os.unlink(legacy)
            except OSError:
                pass
            if attempt < retries:
                time.sleep(min(2 ** attempt, 8))
    out["error"] = str(last_error)
    return out


def target_extras(dst_root: str, expected: set[str]) -> list[str]:
    extras: list[str] = []
    if not os.path.isdir(dst_root):
        return extras
    for dirpath, _dirs, names in os.walk(dst_root):
        for name in names:
            p = os.path.join(dirpath, name)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            rel = os.path.relpath(p, dst_root)
            if rel.endswith(".part"):
                continue
            if rel not in expected:
                extras.append(rel)
    extras.sort()
    return extras


def compare(entries: list[dict], dst_root: str, verify_sha: bool,
            report: dict) -> int:
    bad: list[dict] = []
    for e in entries:
        dst = os.path.join(dst_root, e["rel"])
        size = _stat_size(dst)
        if size is None:
            bad.append({"rel": e["rel"], "reason": "missing"})
            continue
        if size != e["size"]:
            bad.append({"rel": e["rel"], "reason": f"size {size} != {e['size']}"})
            continue
        if verify_sha:
            try:
                if sha256_file(e["src"]) != sha256_file(dst):
                    bad.append({"rel": e["rel"], "reason": "sha256 mismatch"})
            except OSError as ex:
                bad.append({"rel": e["rel"], "reason": f"hash error: {ex}"})
    report["bad"] = bad[:200]
    report["bad_count"] = len(bad)
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="本地结果目录 → OSS 增量同步（size + 可选 SHA256）")
    ap.add_argument("--src", required=True, help="本地源目录")
    ap.add_argument("--dst", required=True, help="目标目录（OSS 挂载点）")
    ap.add_argument("--sha256", action="store_true",
                    help="同大小文件再比内容哈希，上传后读回校验（慢）")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    ap.add_argument("--exclude", action="append", default=None,
                    metavar="GLOB", help="额外排除（相对路径或文件名，可多次）")
    ap.add_argument("--include", action="append", default=None,
                    metavar="GLOB", help="只同步匹配这些 glob 的文件（可多次）")
    ap.add_argument("--include-hidden", action="store_true",
                    help="不要跳过 . 开头的文件/目录")
    ap.add_argument("--dry-run", action="store_true", help="只算要传什么")
    ap.add_argument("--verify-only", action="store_true",
                    help="只核对源与目标的对象数/字节/哈希")
    ap.add_argument("--allow-extra", action="store_true",
                    help="verify-only 时允许目标多出文件")
    ap.add_argument("--report", default=None, metavar="PATH",
                    help="写 JSON 报告")
    a = ap.parse_args(argv)

    src = os.path.abspath(os.path.expanduser(a.src)).rstrip("/")
    dst = os.path.abspath(os.path.expanduser(a.dst)).rstrip("/")
    if not os.path.isdir(src):
        print(f"[error] 源目录不存在：{src}", file=sys.stderr)
        return 2
    if os.path.realpath(src) == os.path.realpath(dst):
        print("[error] 源与目标是同一个目录", file=sys.stderr)
        return 2
    excludes = tuple(DEFAULT_EXCLUDES) + tuple(a.exclude or ())
    entries, symlinks = scan_source(src, excludes, a.include_hidden,
                                    tuple(a.include or ()))
    total_bytes = sum(e["size"] for e in entries)
    t0 = time.monotonic()

    report: dict = {
        "src": src, "dst": dst, "files": len(entries), "bytes": total_bytes,
        "symlinks_skipped": symlinks, "sha256": bool(a.sha256),
        "mode": ("verify" if a.verify_only else
                 "dry-run" if a.dry_run else "sync"),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    log(f"源 {src}")
    log(f"目标 {dst}")
    log(f"扫描：{len(entries)} 个文件 / {human_bytes(total_bytes)}"
        f"；跳过符号链接 {symlinks} 个"
        + ("（含 SHA256）" if a.sha256 else ""))

    if a.verify_only:
        rc = compare(entries, dst, a.sha256, report)
        extras = target_extras(dst, {e["rel"] for e in entries})
        report["extras"] = extras[:200]
        report["extras_count"] = len(extras)
        if extras and not a.allow_extra:
            rc = 1
        report["ok"] = rc == 0
        report["elapsed_s"] = round(time.monotonic() - t0, 1)
        log(f"核对：不一致 {report['bad_count']} 个；目标多出 {len(extras)} 个"
            + ("" if a.allow_extra or not extras else "（--allow-extra 可忽略）"))
        for item in report["bad"][:10]:
            log(f"  ✗ {item['rel']}：{item['reason']}")
        for rel in extras[:10]:
            log(f"  + {rel}")
    else:
        if a.dry_run:
            to_upload = []
            skipped0 = 0
            for e in entries:
                same = _stat_size(os.path.join(dst, e["rel"])) == e["size"]
                if same and not a.sha256:
                    skipped0 += 1
                else:
                    to_upload.append(e)
            report["to_upload"] = len(to_upload)
            report["to_upload_bytes"] = sum(e["size"] for e in to_upload)
            report["already_same_size"] = skipped0
            log(f"计划：需传 {len(to_upload)} 个 / "
                f"{human_bytes(report['to_upload_bytes'])}；同大小跳过 {skipped0} 个")
            for e in to_upload[:20]:
                log(f"  ↑ {e['rel']}  {human_bytes(e['size'])}")
            if len(to_upload) > 20:
                log(f"  … 另有 {len(to_upload) - 20} 个")
            report["ok"] = True
            report["elapsed_s"] = 0.0
        else:
            # 同步模式不在主线程预扫目标：每个 worker 自己 stat，同尺寸直接跳过。
            # 大结果树（6k+ 小文件）这样少一半 goofys stat，也避免串行等元数据。
            to_upload = entries
            report["to_upload"] = len(to_upload)
            report["to_upload_bytes"] = sum(e["size"] for e in to_upload)
            log(f"开始：{len(to_upload)} 个文件 / "
                f"{human_bytes(report['to_upload_bytes'])}（同尺寸由 worker 跳过）")
            started = time.monotonic()
            uploaded = skipped2 = 0
            uploaded_bytes = 0
            failures: list[dict] = []
            done = 0
            last_log = 0.0
            with ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
                futures = {
                    pool.submit(_copy_one, e, dst, a.sha256, max(1, a.retries)):
                        e for e in to_upload
                }
                for fut in as_completed(futures):
                    done += 1
                    res = fut.result()
                    if res["action"] == "uploaded":
                        uploaded += 1
                        uploaded_bytes += res["size"]
                    elif res["action"] == "skipped":
                        skipped2 += 1
                    else:
                        failures.append({"rel": res["rel"],
                                         "error": res["error"]})
                        if len(failures) <= 20:
                            log(f"  ✗ {res['rel']}：{res['error']}")
                    now = time.monotonic()
                    if done == len(futures) or now - last_log >= 5:
                        last_log = now
                        log(f"  进度 {done}/{len(futures)} · 上传 {uploaded} · "
                            f"跳过 {skipped2} · 失败 {len(failures)} · "
                            f"{human_bytes(uploaded_bytes)}")
            report.update({
                "uploaded": uploaded, "uploaded_bytes": uploaded_bytes,
                "skipped": skipped2,
                "failed": len(failures), "failures": failures[:200],
                "elapsed_s": round(time.monotonic() - started, 1),
                "mbps": (round(uploaded_bytes / 1e6 / max(
                    time.monotonic() - started, 0.001), 2) if uploaded_bytes
                    else 0.0),
            })
            report["ok"] = not failures
            log(f"完成：上传 {uploaded} 个 / {human_bytes(uploaded_bytes)}；"
                f"跳过 {report['skipped']} 个；失败 {len(failures)} 个；"
                f"耗时 {report['elapsed_s']}s")
            for item in failures[:10]:
                log(f"  ✗ {item['rel']}：{item['error']}")

    if a.report:
        rp = Path(a.report).expanduser()
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                      "utf-8")
    print(MARK + json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
