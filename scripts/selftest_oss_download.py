#!/usr/bin/env python3
"""``scripts/oss_download.py`` 的离线自检（不碰 OSS / 百度，全部在临时目录）。

用一个 1 秒的小 mp4 和假的 ``bypy`` 包覆盖：直播 .part 收尾、失败不留正式文件、
expect-bytes 闸门、baidu 直写 OSS（不落本地暂存）、非视频跳过、中途断流保留
.part、``--resume`` 续传、dry-run。
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
TOOL = REPO / "scripts" / "oss_download.py"
checks = 0
failed = 0

#: 假 ``bypy`` 包：只实现 oss_download 用到的授权 / 列目录 / PCS 取流三件事。
FAKE_BYPY = '''\
import os
from pathlib import Path


class const:
    UserAgent = "fake-bypy"
    DPcsUrl = "https://fake.pcs/rest/2.0/pcs/"
    ENoError = 0


def get_pcs_path(p):
    p = p.strip("/")
    return "/apps/bypy" + ("/" + p if p else "")


def _base():
    return Path(os.environ["FAKE_BYPY_DIR"])


class _Resp:
    def __init__(self, status_code, data=b"", text=""):
        self.status_code = status_code
        self._data = data
        self._text = text

    @property
    def text(self):
        return self._text

    def iter_content(self, chunk_size=8192):
        if os.environ.get("FAKE_BYPY_FAIL_MID") and self._data:
            half = max(1, len(self._data) // 2)
            yield self._data[:half]
            raise IOError("fake connection dropped")
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i:i + chunk_size]

    def close(self):
        pass


class _Requester:
    def request(self, method, url, params=None, cookies=None, headers=None,
                stream=False, timeout=None):
        if os.environ.get("FAKE_BYPY_FAIL"):
            return _Resp(500, text="fake server error")
        path = (params or {}).get("path", "")
        src = _base() / path[len("/apps/bypy/"):]
        data = src.read_bytes()
        rng = (headers or {}).get("Range", "")
        status = 200
        if rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a)
            end = int(b) if b else len(data) - 1
            data = data[start:end + 1]
            status = 206
        return _Resp(status, data=data)


class ByPy:
    def __init__(self, verbose=0):
        self._access_token = "fake-token"
        self._cookies = {}
        self._requester = _Requester()

    def _extraupdate(self):
        pass

    def _refresh_token(self):
        return 0

    def _walk_proceed_remote_dir(self, remotepath, proceed, args=None,
                                 recursive=True):
        base = _base()
        relroot = remotepath[len("/apps/bypy/"):].strip("/")
        d = base / relroot if relroot else base
        filejs = []
        for p in sorted(d.rglob("*")):
            if p.is_file():
                rel = p.relative_to(base).as_posix()
                filejs.append({"path": "/apps/bypy/" + rel,
                               "size": p.stat().st_size})
        proceed(0, remotepath, [], filejs, args)
        return 0
'''


def ok(cond: bool, label: str) -> None:
    global checks, failed
    checks += 1
    if cond:
        print(f"  ok  {label}")
    else:
        failed += 1
        print(f"  FAIL {label}")


def run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, env=e)


def make_video(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=160x120:d=1",
        "-c:v", "mpeg4", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True)
    return p.returncode == 0 and path.exists() and path.stat().st_size > 0


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="oss_download_selftest_"))
    try:
        src = tmp / "small.mp4"
        if not make_video(src):
            print("  FAIL 无法用 ffmpeg 生成测试视频")
            return 1
        size = src.stat().st_size
        root = tmp / "oss"

        print("[1] live：下载命令写 {part} → 收尾")
        p = run("live", "--root", str(root), "--event", "E", "--day", "11.19",
                "--platform", "抖音", "--account", "账号",
                "--name", "seg1", "--cmd", f"cp '{src}' {{part}}")
        final = root / "E/11.19/抖音/账号/seg1.mp4"
        ok(p.returncode == 0, "EXIT 0")
        ok(final.exists() and final.stat().st_size == size, "正式文件已生成")
        ok(Path(str(final) + ".manifest.json").exists(), "manifest 已写")
        man = json.loads(Path(str(final) + ".manifest.json").read_text("utf-8"))
        ok(man["bytes"] == size and man["duration_s"] and man["duration_s"] > 0,
           "manifest 字节/时长正确")
        ok(man.get("event") == "E" and man.get("source") == "live",
           "manifest 元数据正确")

        print("[2] live：已有正式文件（幂等）")
        p = run("live", "--root", str(root), "--event", "E", "--day", "11.19",
                "--platform", "抖音", "--account", "账号",
                "--name", "seg1", "--cmd", "false")
        ok(p.returncode == 0 and final.exists(), "直接返回 0，不重跑 cmd")

        print("[3] live：下载失败 → 不留正式文件")
        p = run("live", "--root", str(root), "--event", "E", "--day", "11.19",
                "--platform", "抖音", "--account", "账号",
                "--name", "fail", "--cmd", "exit 7")
        ok(p.returncode != 0, "非 0 退出")
        ok(not (root / "E/11.19/抖音/账号/fail.mp4").exists(),
           "没有正式文件")
        ok(not (root / "E/11.19/抖音/账号/fail.mp4.manifest.json").exists(),
           "没有 manifest")

        print("[4] live：下载器违约写正式名，失败时删掉")
        p = run("live", "--root", str(root), "--event", "E", "--day", "11.19",
                "--platform", "抖音", "--account", "账号",
                "--name", "bad", "--cmd", f"cp '{src}' {{final}}; exit 1")
        ok(p.returncode != 0, "非 0 退出")
        ok(not (root / "E/11.19/抖音/账号/bad.mp4").exists(),
           "违约生成的正式文件已删除")

        print("[5] live：expect-bytes 不符 → 拒绝收尾、保留 .part")
        p = run("live", "--root", str(root), "--event", "E", "--day", "11.19",
                "--platform", "抖音", "--account", "账号",
                "--name", "bytes", "--expect-bytes", str(size + 1),
                "--cmd", f"cp '{src}' {{part}}")
        ok(p.returncode != 0, "字节不符失败")
        ok(not (root / "E/11.19/抖音/账号/bytes.mp4").exists(), "没有正式文件")
        ok((root / "E/11.19/抖音/账号/bytes.mp4.part").exists(),
           ".part 保留")

        print("[6] live：dry-run 不建目录、不跑命令")
        dry_root = tmp / "dry"
        p = run("live", "--root", str(dry_root), "--event", "E", "--day", "11.19",
                "--platform", "抖音", "--account", "账号", "--name", "x",
                "--cmd", "false", "--dry-run")
        ok(p.returncode == 0 and not dry_root.exists(), "dry-run 无落盘")

        print("[7] baidu：假 bypy 列目录 → PCS 流式直写 OSS（无本地暂存）")
        fixture = tmp / "baidu_fixture"
        remote_rel = Path("fake/dir")
        make_video(fixture / remote_rel / "a.mp4")
        (fixture / remote_rel / "notes.txt").write_text("not video", encoding="utf-8")
        fake_site = tmp / "fake_site"
        pkg = fake_site / "bypy"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(
            "from .bypy import ByPy, const, get_pcs_path\n", encoding="utf-8")
        (pkg / "bypy.py").write_text(FAKE_BYPY, encoding="utf-8")
        env = {"FAKE_BYPY_DIR": str(fixture),
               "PYTHONPATH": str(fake_site) + os.pathsep
                             + os.environ.get("PYTHONPATH", "")}

        p = run("baidu", "--root", str(root), "--remote", "/fake/dir",
                "--dest-rel", "B/11.20/视频号/账号", env=env)
        bdest = root / "B/11.20/视频号/账号"
        bfinal = bdest / "a.mp4"
        ok(p.returncode == 0, "EXIT 0")
        ok(bfinal.exists() and bfinal.stat().st_size > 0, "视频已直写 OSS 目标")
        ok(not (bdest / "notes.txt").exists(), "非视频未复制")
        ok(not Path(str(bfinal) + ".part").exists(), ".part 已改名")
        ok(not (tmp / "staging").exists(), "没有本地暂存目录")
        ok(Path(str(bfinal) + ".manifest.json").exists(), "manifest 已写")
        man = json.loads(Path(str(bfinal) + ".manifest.json").read_text("utf-8"))
        ok(man.get("source", "").startswith("baidu:"), "manifest 记录百度来源")
        ok(man.get("remote_rel") == "a.mp4", "manifest 记录远端相对路径")

        print("[8] baidu：下载中途断流 → 不留正式文件、保留 .part")
        fail_root = tmp / "oss_fail"
        p = run("baidu", "--root", str(fail_root), "--remote", "/fake/dir",
                "--dest-rel", "B/11.20/视频号/账号",
                env={**env, "FAKE_BYPY_FAIL_MID": "1"})
        fdest = fail_root / "B/11.20/视频号/账号"
        ok(p.returncode != 0, "非 0 退出")
        ok(not (fdest / "a.mp4").exists(), "没有正式文件")
        part = fdest / "a.mp4.part"
        ok(part.exists() and 0 < part.stat().st_size < size,
           ".part 保留且是半截")

        print("[8b] baidu：.part 已存在但没给 --resume/--overwrite → 拒绝")
        p = run("baidu", "--root", str(fail_root), "--remote", "/fake/dir",
                "--dest-rel", "B/11.20/视频号/账号", env=env)
        ok(p.returncode != 0, "非 0 退出")
        ok(part.exists() and part.stat().st_size < size, ".part 原样保留")

        print("[9] baidu：--resume 从 .part 续传到收尾")
        p = run("baidu", "--root", str(fail_root), "--remote", "/fake/dir",
                "--dest-rel", "B/11.20/视频号/账号", "--resume", env=env)
        ok(p.returncode == 0, "EXIT 0")
        ok((fdest / "a.mp4").exists()
           and (fdest / "a.mp4").stat().st_size == size, "续传后字节完整")
        ok(not part.exists(), ".part 已清掉")

        print("[10] baidu：dry-run 不建目录、不访问网络")
        dry_root = tmp / "dry_baidu"
        p = run("baidu", "--root", str(dry_root), "--remote", "/fake/dir",
                "--dest-rel", "B/11.20/视频号/账号", "--dry-run",
                env={"FAKE_BYPY_DIR": "/nonexistent"})
        ok(p.returncode == 0 and not dry_root.exists(), "dry-run 无落盘")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n自检：{checks} 项，失败 {failed} 项")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
