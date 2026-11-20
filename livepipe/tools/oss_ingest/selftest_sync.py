"""``sync.py`` 的离线自检：全部在临时目录里造小文件，不碰 OSS / 网络 / FUSE。

导出两种入口：

- :func:`run` —— 聚合器（``selftest.py``）传入自带 ``Harness``，逐项调用
  ``h.eq(name, got, want)`` / ``h.raises(name, exc, fn)``。
- :func:`selftest` —— 单独运行时自带极简 Harness，返回进程退出码。

覆盖：嵌套复制、软链跳过、``.part/.lock/.failed/.tmp`` 跳过、同尺寸不同 mtime
仍跳过、``sha256`` 同尺寸异内容重拷、``verify_tree`` 的 missing/size/hash/extra、
``delete_local`` 删文件并清空目录但保留 ``src``、``dry_run`` 零写入、无 ``.part``
残留、重试两次后成功 = 3 次尝试、连续失败进 ``failures``、``workers=1`` 计数确定。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from . import sync as sync_mod
from .common import OssIngestError, rel_to
from .sync import sync_tree, verify_tree


# --------------------------------------------------------------------------- #
# 测试小工具
# --------------------------------------------------------------------------- #
def _write(path: str | Path, data: bytes) -> None:
    """写一个文件（自动建父目录）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _parts(root: str | Path) -> list[str]:
    """列出目标树里所有 ``*.part`` 的相对路径（用于断言无残留）。"""
    base = Path(root)
    if not base.is_dir():
        return []
    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            if name.endswith(".part"):
                found.append(rel_to(Path(dirpath) / name, base))
    return sorted(found)


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #
def _t_nested_copy(h) -> None:
    """嵌套目录逐层复制，字节数准确，成功后无 .part 残留。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "a.txt", b"aaa")
        _write(src / "sub" / "b.bin", b"bbbb")
        st = sync_tree(src, dst, workers=1)
        h.eq("嵌套复制 files", st["files"], 2)
        h.eq("嵌套复制 bytes", st["bytes"], 7)
        h.eq("嵌套复制 copied", st["copied"], 2)
        h.eq("嵌套复制 skipped", st["skipped"], 0)
        h.eq("嵌套复制 failed", st["failed"], 0)
        h.eq("嵌套复制 无软链", st["symlinks_skipped"], 0)
        h.eq("嵌套复制 a 内容", (dst / "a.txt").read_bytes(), b"aaa")
        h.eq("嵌套复制 b 内容", (dst / "sub" / "b.bin").read_bytes(), b"bbbb")
        h.eq("嵌套复制 无 .part", _parts(dst), [])


def _t_resync_skips(h) -> None:
    """第二次同步大小一致，全部跳过。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "a", b"1234")
        _write(src / "b", b"56789")
        sync_tree(src, dst, workers=1)
        st = sync_tree(src, dst, workers=1)
        h.eq("二次同步 copied", st["copied"], 0)
        h.eq("二次同步 skipped", st["skipped"], 2)
        h.eq("二次同步 files", st["files"], 2)
        h.eq("二次同步 bytes", st["bytes"], 9)


def _t_symlink_skipped(h) -> None:
    """源软链（文件与目录）都不复制，只计入 symlinks_skipped。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "real.txt", b"x")
        (src / "sub").mkdir()
        os.symlink(src / "real.txt", src / "link.txt")
        os.symlink(src / "sub", src / "dirlink")
        st = sync_tree(src, dst, workers=1)
        h.eq("软链跳过 计数", st["symlinks_skipped"], 2)
        h.eq("软链跳过 files", st["files"], 1)
        h.eq("软链跳过 copied", st["copied"], 1)
        h.eq("软链文件未复制", (dst / "link.txt").exists(), False)
        h.eq("软链目录未复制", (dst / "dirlink").exists(), False)


def _t_temp_suffixes_skipped(h) -> None:
    """``.part/.lock/.failed/.tmp`` 都不参与同步（含裸文件名 ``.part`` 等）。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        for name in ("x.part", "y.lock", "z.failed", "w.tmp",
                     ".part", ".lock", ".failed", ".tmp"):
            _write(src / name, b"junk")
        _write(src / "keep.txt", b"ok")
        st = sync_tree(src, dst, workers=1)
        h.eq("临时后缀 files", st["files"], 1)
        h.eq("临时后缀 copied", st["copied"], 1)
        h.eq("临时后缀 目标内容",
             sorted(p.name for p in dst.iterdir()), ["keep.txt"])
        h.eq("裸临时名 .part 跳过", sync_mod._skippable(".part"), True)
        h.eq("裸临时名 .lock 跳过", sync_mod._skippable(".lock"), True)
        h.eq("裸临时名 .tmp 跳过", sync_mod._skippable(".tmp"), True)
        h.eq("裸临时名 .failed 跳过", sync_mod._skippable(".failed"), True)


def _t_same_size_ignores_mtime(h) -> None:
    """大小相同、mtime 不同也跳过（绝不用 mtime 判断）。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "f.bin", b"1234")
        _write(dst / "f.bin", b"1234")
        os.utime(dst / "f.bin", (1, 1))
        st = sync_tree(src, dst, workers=1)
        h.eq("同尺寸异 mtime copied", st["copied"], 0)
        h.eq("同尺寸异 mtime skipped", st["skipped"], 1)


def _t_size_mismatch_recopy(h) -> None:
    """大小不同 → 重新复制并覆盖目标。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "f.bin", b"12345")
        _write(dst / "f.bin", b"1234")
        st = sync_tree(src, dst, workers=1)
        h.eq("尺寸不同 copied", st["copied"], 1)
        h.eq("尺寸不同 内容", (dst / "f.bin").read_bytes(), b"12345")


def _t_sha256_recopy(h) -> None:
    """同尺寸异内容：默认只比大小（跳过）；``sha256=True`` 才比哈希并重拷。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "f.bin", b"AAAA")
        _write(dst / "f.bin", b"BBBB")
        st1 = sync_tree(src, dst, workers=1)
        h.eq("默认 size-only skipped", st1["skipped"], 1)
        h.eq("默认 size-only 内容未变", (dst / "f.bin").read_bytes(), b"BBBB")
        st2 = sync_tree(src, dst, workers=1, sha256=True)
        h.eq("sha256 开启 copied", st2["copied"], 1)
        h.eq("sha256 开启 内容", (dst / "f.bin").read_bytes(), b"AAAA")


def _t_sha256_equal_skip(h) -> None:
    """``sha256=True`` 且内容一致时仍跳过。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "f.bin", b"same")
        _write(dst / "f.bin", b"same")
        st = sync_tree(src, dst, workers=1, sha256=True)
        h.eq("sha256 一致 copied", st["copied"], 0)
        h.eq("sha256 一致 skipped", st["skipped"], 1)


def _t_verify_tree(h) -> None:
    """``verify_tree`` 检出 missing / size_mismatch / sha256_mismatch / extra。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "a", b"12345")
        _write(src / "b", b"abcd")
        _write(src / "sub" / "c", b"zz")
        _write(dst / "a", b"123")          # 尺寸不符
        _write(dst / "b", b"abcd")         # 正确
        _write(dst / "extra", b"e")        # 多余
        v = verify_tree(src, dst)
        h.eq("verify files", v["files"], 3)
        h.eq("verify bytes", v["bytes"], 11)
        h.eq("verify missing", v["missing"], 1)
        h.eq("verify size_mismatch", v["size_mismatch"], 1)
        h.eq("verify sha256_mismatch", v["sha256_mismatch"], 0)
        h.eq("verify ok", v["ok"], 1)
        h.eq("verify extra", v["extra"], 1)
        _write(dst / "a", b"ABCDE")        # 同尺寸、异内容
        v2 = verify_tree(src, dst, sha256=True)
        h.eq("verify sha256 检出", v2["sha256_mismatch"], 1)
        h.eq("verify sha256 ok", v2["ok"], 1)


def _t_verify_missing_dst_readonly(h) -> None:
    """目标整体缺失：全部 missing，且校验过程不创建目标目录。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "a", b"1")
        _write(src / "b", b"22")
        v = verify_tree(src, dst)
        h.eq("verify 缺失目标 missing", v["missing"], 2)
        h.eq("verify 缺失目标 ok", v["ok"], 0)
        h.eq("verify 缺失目标 extra", v["extra"], 0)
        h.eq("verify 不创建目标", dst.exists(), False)


def _t_verify_extra_ignores_temp(h) -> None:
    """目标里的临时后缀与软链不计入 extra。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "a", b"x")
        _write(dst / "a", b"x")
        _write(dst / "junk.part", b"junk")
        os.symlink(dst / "a", dst / "link")
        v = verify_tree(src, dst)
        h.eq("verify extra 忽略临时", v["extra"], 0)
        h.eq("verify extra 忽略软链", v["ok"], 1)


def _t_verify_symlink_count(h) -> None:
    """``verify_tree`` 同样跳过源软链并计数。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "real", b"r")
        os.symlink(src / "real", src / "link")
        _write(dst / "real", b"r")
        v = verify_tree(src, dst)
        h.eq("verify 软链计数", v["symlinks_skipped"], 1)
        h.eq("verify 软链 files", v["files"], 1)
        h.eq("verify 软链 ok", v["ok"], 1)


def _t_delete_local(h) -> None:
    """``delete_local`` 删源文件、自底向上清空目录，但保留 src 本身。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "a.txt", b"aaa")
        _write(src / "sub" / "b.txt", b"bb")
        _write(src / "sub" / "deep" / "c.txt", b"c")
        st = sync_tree(src, dst, workers=1, delete_local=True)
        h.eq("删源 copied", st["copied"], 3)
        h.eq("删源 deleted", st["deleted"], 3)
        h.eq("删源 源文件已删", (src / "a.txt").exists(), False)
        h.eq("删源 深层目录已清", (src / "sub" / "deep").exists(), False)
        h.eq("删源 子目录已清", (src / "sub").exists(), False)
        h.eq("删源 保留 src", src.is_dir(), True)
        h.eq("删源 目标内容", (dst / "sub" / "deep" / "c.txt").read_bytes(), b"c")


def _t_delete_local_on_skip(h) -> None:
    """目标已一致时也删源（verify 通过才删）。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "a", b"abc")
        sync_tree(src, dst, workers=1)
        st = sync_tree(src, dst, workers=1, delete_local=True)
        h.eq("跳过时删源 skipped", st["skipped"], 1)
        h.eq("跳过时删源 deleted", st["deleted"], 1)
        h.eq("跳过时删源 源已删", (src / "a").exists(), False)
        h.eq("跳过时删源 目标仍在", (dst / "a").read_bytes(), b"abc")


def _t_dry_run(h) -> None:
    """``dry_run`` 只统计、不写盘、不删源、无 .part。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "a", b"abc")
        st = sync_tree(src, dst, workers=1, dry_run=True)
        h.eq("dry_run copied", st["copied"], 1)
        h.eq("dry_run 未建目标", dst.exists(), False)
        h.eq("dry_run 源仍在", (src / "a").read_bytes(), b"abc")
        h.eq("dry_run 无 .part", _parts(dst), [])
        st2 = sync_tree(src, dst, workers=1, dry_run=True, delete_local=True)
        h.eq("dry_run 不删源", (src / "a").exists(), True)
        h.eq("dry_run 预演会删源", st2["deleted"], 1)
        h.eq("dry_run 预演不写目标", dst.exists(), False)


def _t_retry_then_success(h) -> None:
    """``_copy_one`` 失败两次后成功 = 共 3 次尝试，期间睡 2 次。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "a", b"hello")
        original = sync_mod._copy_one
        old_sleep = sync_mod.time.sleep
        attempts: list[int] = []
        sleeps: list[float] = []

        def flaky(s, d, want_sha):
            attempts.append(1)
            if len(attempts) <= 2:
                raise OSError("boom")
            return original(s, d, want_sha)

        sync_mod._copy_one = flaky
        sync_mod.time.sleep = lambda sec: sleeps.append(sec)
        try:
            st = sync_tree(src, dst, workers=1, retries=3)
        finally:
            sync_mod._copy_one = original
            sync_mod.time.sleep = old_sleep
        h.eq("重试成功 尝试次数", len(attempts), 3)
        h.eq("重试成功 睡眠", sleeps, [1, 1])
        h.eq("重试成功 copied", st["copied"], 1)
        h.eq("重试成功 failed", st["failed"], 0)
        h.eq("重试成功 内容", (dst / "a").read_bytes(), b"hello")
        h.eq("重试成功 无 .part", _parts(dst), [])


def _t_retry_exhausted(h) -> None:
    """连续失败进入 ``failures``，临时文件不残留。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "a", b"hello")
        original = sync_mod._copy_one
        old_sleep = sync_mod.time.sleep
        attempts: list[int] = []
        sleeps: list[float] = []

        def always(s, d, want_sha):
            attempts.append(1)
            raise OSError("boom")

        sync_mod._copy_one = always
        sync_mod.time.sleep = lambda sec: sleeps.append(sec)
        try:
            st = sync_tree(src, dst, workers=1, retries=3)
        finally:
            sync_mod._copy_one = original
            sync_mod.time.sleep = old_sleep
        h.eq("连续失败 尝试次数", len(attempts), 3)
        h.eq("连续失败 睡眠", sleeps, [1, 1])
        h.eq("连续失败 copied", st["copied"], 0)
        h.eq("连续失败 failed", st["failed"], 1)
        h.eq("连续失败 记录数", len(st["failures"]), 1)
        h.eq("连续失败 src", st["failures"][0]["src"], str(src / "a"))
        h.eq("连续失败 dst", st["failures"][0]["dst"], str(dst / "a"))
        h.eq("连续失败 error 含原因", "boom" in st["failures"][0]["error"], True)
        marker = src / "a.failed"
        h.eq("连续失败 留 .failed 标记", marker.exists(), True)
        h.eq("连续失败 标记路径", st["failures"][0].get("marker"),
             str(marker))
        h.eq("连续失败 无 .part", _parts(dst), [])


def _t_retries_one_attempt(h) -> None:
    """``retries=1`` 只尝试一次且不睡眠。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "a", b"x")
        original = sync_mod._copy_one
        old_sleep = sync_mod.time.sleep
        attempts: list[int] = []
        sleeps: list[float] = []

        def always(s, d, want_sha):
            attempts.append(1)
            raise OSError("nope")

        sync_mod._copy_one = always
        sync_mod.time.sleep = lambda sec: sleeps.append(sec)
        try:
            st = sync_tree(src, dst, workers=1, retries=1)
        finally:
            sync_mod._copy_one = original
            sync_mod.time.sleep = old_sleep
        h.eq("单次重试 尝试次数", len(attempts), 1)
        h.eq("单次重试 无睡眠", sleeps, [])
        h.eq("单次重试 failed", st["failed"], 1)


def _t_workers_one_deterministic(h) -> None:
    """``workers=1`` 两次同步的计数完全一致（含软链与临时后缀）。"""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src"
        dst1, dst2 = Path(td) / "dst1", Path(td) / "dst2"
        _write(src / "a.txt", b"aaa")
        _write(src / "b.txt", b"bbbb")
        _write(src / "sub" / "c.txt", b"cc")
        _write(src / "sub" / "deep" / "d.txt", b"d")
        _write(src / "x.part", b"junk")
        os.symlink(src / "a.txt", src / "link.txt")
        st1 = sync_tree(src, dst1, workers=1)
        st2 = sync_tree(src, dst2, workers=1)
        h.eq("确定性 两次结果一致", st1, st2)
        h.eq("确定性 files", st1["files"], 4)
        h.eq("确定性 bytes", st1["bytes"], 10)
        h.eq("确定性 copied", st1["copied"], 4)
        h.eq("确定性 软链", st1["symlinks_skipped"], 1)
        h.eq("确定性 无失败", st1["failures"], [])


def _t_bad_source_raises(h) -> None:
    """源不存在 / 不是目录时抛 :class:`OssIngestError`。"""
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "missing"
        dst = Path(td) / "dst"
        h.raises("源不存在抛错", OssIngestError,
                 lambda: sync_tree(missing, dst))
        h.raises("verify 源不存在抛错", OssIngestError,
                 lambda: verify_tree(missing, dst))
        _write(Path(td) / "file.txt", b"x")
        h.raises("源是文件抛错", OssIngestError,
                 lambda: sync_tree(Path(td) / "file.txt", dst))


def _t_same_src_dst_raises(h) -> None:
    """源与目标相同直接拒绝，避免删源时误删。"""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src"
        _write(src / "a", b"x")
        h.raises("源目标相同抛错", OssIngestError,
                 lambda: sync_tree(src, src, workers=1))


def _t_nested_roots_rejected(h) -> None:
    """目标是源的子目录 / 源是目标的子目录都必须拒绝，避免套娃复制。"""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src"
        _write(src / "a", b"x")
        h.raises("目标在源内抛错", OssIngestError,
                 lambda: sync_tree(src, src / "oss_dst", workers=1))
        h.raises("源在目标内抛错", OssIngestError,
                 lambda: sync_tree(src, Path(td), workers=1))
        h.raises("verify 目标在源内抛错", OssIngestError,
                 lambda: verify_tree(src, src / "oss_dst"))


def _t_delete_local_forces_sha256(h) -> None:
    """``delete_local`` 即使没显式传 sha256 也要强制哈希校验。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "a", b"same")
        _write(dst / "a", b"diff")          # 同尺寸、不同内容
        st = sync_tree(src, dst, workers=1, delete_local=True)
        h.eq("删源强制哈希 copied", st["copied"], 1)
        h.eq("删源强制哈希 deleted", st["deleted"], 1)
        h.eq("删源强制哈希 目标内容", (dst / "a").read_bytes(), b"same")
        h.eq("删源强制哈希 源已删", (src / "a").exists(), False)


def _t_failed_marker_not_synced(h) -> None:
    """``*.failed`` 标记本身不会被当成源文件同步。"""
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "src", Path(td) / "dst"
        _write(src / "a", b"x")
        _write(src / "a.failed", b"marker")
        st = sync_tree(src, dst, workers=1)
        h.eq("失败标记 files", st["files"], 1)
        h.eq("失败标记 未复制", (dst / "a.failed").exists(), False)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
_TESTS = (
    _t_nested_copy,
    _t_resync_skips,
    _t_symlink_skipped,
    _t_temp_suffixes_skipped,
    _t_same_size_ignores_mtime,
    _t_size_mismatch_recopy,
    _t_sha256_recopy,
    _t_sha256_equal_skip,
    _t_verify_tree,
    _t_verify_missing_dst_readonly,
    _t_verify_extra_ignores_temp,
    _t_verify_symlink_count,
    _t_delete_local,
    _t_delete_local_on_skip,
    _t_dry_run,
    _t_retry_then_success,
    _t_retry_exhausted,
    _t_retries_one_attempt,
    _t_workers_one_deterministic,
    _t_bad_source_raises,
    _t_same_src_dst_raises,
    _t_nested_roots_rejected,
    _t_delete_local_forces_sha256,
    _t_failed_marker_not_synced,
)


def run(h) -> None:
    """供聚合器调用：逐个执行用例；单个用例异常也继续跑后面的。"""
    for test in _TESTS:
        try:
            test(h)
        except Exception as e:  # noqa: BLE001 - 自检要报告而不是中断
            h.eq(f"{test.__name__} 未抛异常",
                 f"{type(e).__name__}: {e}", "ok")


class _Harness:
    """单独运行时的极简 Harness（与聚合器接口一致）。"""

    def __init__(self) -> None:
        self.passed = 0
        self.failures: list[tuple[str, object, object]] = []

    def eq(self, name: str, got, want) -> None:
        if got == want:
            self.passed += 1
        else:
            self.failures.append((name, got, want))

    def raises(self, name: str, exc, fn) -> None:
        try:
            fn()
        except exc:
            self.passed += 1
        except BaseException as e:  # noqa: BLE001
            self.failures.append((name, f"抛了 {type(e).__name__}", exc.__name__))
        else:
            self.failures.append((name, "未抛异常", exc.__name__))

    def report(self) -> int:
        for name, got, want in self.failures:
            print(f"[FAIL] {name}: got={got!r} want={want!r}")
        if self.failures:
            print(f"selftest_sync: {len(self.failures)} 项失败")
            return 1
        print(f"selftest_sync: {self.passed} 项全过 ✅")
        return 0


def selftest() -> int:
    """独立入口：跑全部用例并返回退出码。"""
    h = _Harness()
    run(h)
    return h.report()


if __name__ == "__main__":
    raise SystemExit(selftest())
