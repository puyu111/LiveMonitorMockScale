#!/usr/bin/env python3
"""``scripts/oss_sync.py`` 的离线自检（不碰 OSS，全部在临时目录）。

覆盖：增量复制、重跑全跳过、同大小内容变化（--sha256）、符号链接/隐藏文件排除、
dry-run 不写、verify-only 的缺失/多出/--allow-extra、报告 JSON。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "scripts" / "oss_sync.py"
checks = 0
failed = 0


def ok(cond: bool, label: str) -> None:
    global checks, failed
    checks += 1
    if cond:
        print(f"  ok  {label}")
    else:
        failed += 1
        print(f"  FAIL {label}")


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True)


def marker(proc: subprocess.CompletedProcess) -> dict:
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith("OSS_SYNC_RESULT "):
            return json.loads(line[len("OSS_SYNC_RESULT "):])
    raise AssertionError(f"没有结果行：{proc.stdout}\n{proc.stderr}")


def write(root: Path, rel: str, data: bytes) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="oss_sync_selftest_"))
    try:
        src = tmp / "src"
        dst = tmp / "dst"
        write(src, "a.txt", b"hello")
        write(src, "seg1/comments.jsonl", b'{"a":1}\n')
        write(src, "seg1/frames/00001.jpg", b"jpg")
        write(src, "all_users_x/u1/1.jpg", b"img1")
        write(src, ".batch_runner.json", b"{}")
        write(src, "_logs/run.log", b"log")
        os.symlink(src / "a.txt", src / "link.txt")
        os.symlink(src / "seg1", src / "linkdir")

        print("[1] 首次同步")
        p = run("--src", str(src), "--dst", str(dst),
                "--report", str(tmp / "r1.json"))
        m = marker(p)
        ok(p.returncode == 0, "EXIT 0")
        ok(m["files"] == 5, "只算 5 个普通文件（含 _logs）")
        ok(m["uploaded"] == 5, "上传 5 个")
        ok(m["symlinks_skipped"] == 2, "跳过 2 个符号链接")
        ok((dst / "seg1/frames/00001.jpg").read_bytes() == b"jpg", "内容正确")
        ok(not (dst / "link.txt").exists(), "文件软链未复制")
        ok(not (dst / "linkdir").exists(), "目录软链未复制")
        ok(not (dst / ".batch_runner.json").exists(), "隐藏文件未复制")
        ok((dst / "_logs/run.log").exists(), "普通 _logs 目录照常同步")

        print("[2] 重跑：同大小全部跳过")
        p = run("--src", str(src), "--dst", str(dst),
                "--report", str(tmp / "r2.json"))
        m = marker(p)
        ok(p.returncode == 0 and m["uploaded"] == 0, "上传 0 个")
        ok(m["skipped"] == 5, "跳过 5 个")
        ok(m["failed"] == 0, "0 失败")

        print("[3] 同大小改内容：默认跳过，--sha256 会重传")
        write(src, "a.txt", b"hallo")  # 仍然 5 字节
        p = run("--src", str(src), "--dst", str(dst))
        m = marker(p)
        ok(m["uploaded"] == 0, "默认 size-only：跳过")
        ok((dst / "a.txt").read_bytes() == b"hello", "目标仍是旧内容")
        p = run("--src", str(src), "--dst", str(dst), "--sha256")
        m = marker(p)
        ok(m["uploaded"] == 1, "--sha256：只重传 1 个")
        ok((dst / "a.txt").read_bytes() == b"hallo", "目标已更新")

        print("[4] 大小变化：直接重传")
        write(src, "seg1/comments.jsonl", b'{"a":2}\n{"b":3}\n')
        p = run("--src", str(src), "--dst", str(dst))
        m = marker(p)
        ok(m["uploaded"] == 1, "重传 1 个")

        print("[5] dry-run 不写")
        old = (tmp / "dst_dry")
        p = run("--src", str(src), "--dst", str(old), "--dry-run")
        m = marker(p)
        ok(p.returncode == 0 and m["to_upload"] == 5 and m["mode"] == "dry-run",
           "dry-run 报告")
        ok(not old.exists(), "dry-run 没有创建目标")

        print("[6] verify-only")
        p = run("--src", str(src), "--dst", str(dst), "--verify-only")
        m = marker(p)
        ok(p.returncode == 0 and m["bad_count"] == 0, "一致时 EXIT 0")
        (dst / "all_users_x/u1/1.jpg").unlink()
        p = run("--src", str(src), "--dst", str(dst), "--verify-only")
        m = marker(p)
        ok(p.returncode == 1 and m["bad_count"] == 1, "缺 1 个 → EXIT 1")
        write(dst, "extra.txt", b"extra")
        p = run("--src", str(src), "--dst", str(dst), "--verify-only")
        m = marker(p)
        ok(p.returncode == 1 and m["extras_count"] == 1, "多出 1 个 → EXIT 1")
        p = run("--src", str(src), "--dst", str(dst), "--verify-only",
                "--allow-extra")
        m = marker(p)
        ok(p.returncode == 1, "缺文件仍失败（allow-extra 只放宽多出）")
        # 补回缺失文件后，多出允许
        p = run("--src", str(src), "--dst", str(dst))
        p = run("--src", str(src), "--dst", str(dst), "--verify-only",
                "--allow-extra")
        ok(p.returncode == 0, "补齐 + allow-extra → EXIT 0")

        print("[7] 排除模式")
        p = run("--src", str(src), "--dst", str(tmp / "dst2"),
                "--exclude", "_logs/*")
        m = marker(p)
        ok(m["files"] == 4, "--exclude 后只算 4 个")
        ok(not (tmp / "dst2/_logs/run.log").exists(), "被排除的文件未复制")

        print("[7b] include 过滤")
        p = run("--src", str(src), "--dst", str(tmp / "dst3"),
                "--include", "*.jsonl")
        m = marker(p)
        ok(m["files"] == 1, "--include 只算 1 个")
        ok((tmp / "dst3/seg1/comments.jsonl").exists(), "命中文件已复制")
        ok(not (tmp / "dst3/a.txt").exists(), "未命中文件未复制")

        print("[8] 报告 JSON")
        rep = json.loads((tmp / "r1.json").read_text("utf-8"))
        ok(rep["files"] == 5 and rep["uploaded"] == 5, "报告汇总正确")
        ok(rep.get("ok") is True, "报告 ok=true")

        print("[9] 含 '+' 的目录（goofys rename 会 404，工具改直写）")
        src2 = tmp / "src2"
        dst2 = tmp / "dst2_plus"
        write(src2, "+1八重樱-3/1.jpg", b"plus")
        p = run("--src", str(src2), "--dst", str(dst2))
        m = marker(p)
        ok(p.returncode == 0 and m["uploaded"] == 1, "EXIT 0 且上传 1 个")
        ok((dst2 / "+1八重樱-3/1.jpg").read_bytes() == b"plus", "内容正确")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n自检：{checks} 项，失败 {failed} 项")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
