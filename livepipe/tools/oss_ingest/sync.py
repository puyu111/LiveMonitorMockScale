"""按内容把本地目录同步到 OSS 挂载点（或任意目标目录）。

设计要点（与 ``livepipe/docs/oss-migration.html`` 的 P2 一致）：

- **只比内容**：目标不存在 → 复制；大小相同且未开 ``sha256`` → 跳过；
  ``sha256=True`` 时大小相同再比哈希。**绝不使用 mtime** 做判断。
- **跳过非成品**：源软链（计入 ``symlinks_skipped``）与
  ``*.lock`` / ``*.part`` / ``*.failed`` / ``*.tmp`` 一律不参与同步。
- **先写临时名**：目标写成同目录的 ``<名>.part``，校验大小（及 sha256）后
  ``os.replace`` 到正式名；失败清理临时文件。单个文件最多尝试 ``retries`` 次，
  间隔 1 秒（``time.sleep`` 可被自检 monkeypatch）。
- **删源要谨慎**：``delete_local=True`` 时只在目标校验通过后删源文件，最后
  自底向上清理 ``src`` 下的空目录，**永不删除 ``src`` 本身**；``dry_run`` 不写不删。
- **并发可确定**：``workers>1`` 用线程池，但结果按相对路径排序后再汇总，
  自检固定 ``workers=1``。

对外只有 :func:`sync_tree` 与 :func:`verify_tree`；内部保留
:func:`_copy_one`、:func:`_skippable` 方便自检 monkeypatch。
"""

from __future__ import annotations

import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .common import OssIngestError, file_size, rel_to, sha256_file

#: 不参与同步的“未完成 / 锁 / 临时”后缀（小写比较）。
_SKIP_SUFFIXES = frozenset({".lock", ".part", ".failed", ".tmp"})

#: 永久失败时留在源文件旁的标记后缀。
_FAILED_SUFFIX = ".failed"

#: 复制时的读写块大小。
_COPY_CHUNK = 1 << 20


# --------------------------------------------------------------------------- #
# 内部小工具
# --------------------------------------------------------------------------- #
def _skippable(path: str | Path) -> bool:
    """文件名是否属于 ``.lock/.part/.failed/.tmp``（大小写不敏感）。

    除了普通后缀（``x.part``）还认“裸后缀”文件名（恰好叫 ``.part`` /
    ``.lock`` / ``.tmp`` / ``.failed``）—— ``Path(".part").suffix`` 为 ``''``，
    只看后缀会把它们同步过去。
    """
    name = Path(path).name.lower()
    if Path(path).suffix.lower() in _SKIP_SUFFIXES:
        return True
    return name.endswith(tuple(sorted(_SKIP_SUFFIXES)))


def _unlink_quiet(path: Path) -> None:
    """删除临时文件；不存在 / 删不掉都忽略。"""
    try:
        path.unlink()
    except OSError:
        pass


def _size(path: str | Path) -> int:
    """文件大小；读不到按 0 计（汇总字节数时避免负数）。"""
    n = file_size(path)
    return n if n >= 0 else 0


def _is_within(child: Path, parent: Path) -> bool:
    """``child`` 是否就是 ``parent`` 或位于其下（按 realpath 比较）。"""
    try:
        child_real = os.path.realpath(str(child))
        parent_real = os.path.realpath(str(parent))
        return os.path.commonpath([child_real, parent_real]) == parent_real
    except (TypeError, ValueError, OSError):
        return False


def _check_roots(src_root: Path, dst_root: Path) -> None:
    """拒绝相同 / 相互嵌套的源与目标。

    ``dst`` 在 ``src`` 里时：下一轮 ``_walk_source`` 会把上次写进 ``dst`` 的
    文件当成源，再复制成 ``dst/dst/...``，每跑一次多一层；``--delete-local``
    还会删掉目标里的文件。
    """
    if _is_within(dst_root, src_root):
        raise OssIngestError(
            f"目标不能与源相同或位于源目录内：src={src_root} dst={dst_root}")
    if _is_within(src_root, dst_root):
        raise OssIngestError(
            f"源不能位于目标目录内：src={src_root} dst={dst_root}")


def _failed_marker(src_file: Path, error: BaseException) -> Path | None:
    """在源文件旁写 ``<名>.failed`` 标记；写不了就静默跳过。"""
    marker = src_file.with_name(src_file.name + _FAILED_SUFFIX)
    stamp = datetime.now(timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")
    try:
        marker.write_text(
            f"{stamp} {type(error).__name__}: {error}\n", encoding="utf-8")
        return marker
    except OSError:
        return None


def _walk_source(src: Path) -> tuple[list[Path], int, list[dict]]:
    """遍历源目录，返回 ``(参与同步的文件, 跳过的软链数, 遍历错误)``。

    - 不跟随软链；软链文件与软链目录都只计数，不进入结果。
    - 跳过 :func:`_skippable` 的文件。
    - 结果按相对路径（POSIX）排序，保证确定性。
    - 读不了的目录记进 ``walk_errors``（``{path, error}``），让部分遍历可见，
      而不是静默少同步文件。
    """
    entries: list[Path] = []
    symlinks = 0
    walk_errors: list[dict] = []

    def _onerror(error: OSError) -> None:
        walk_errors.append({
            "path": getattr(error, "filename", None),
            "error": f"{type(error).__name__}: {error}",
        })

    if not src.is_dir():
        return entries, symlinks, walk_errors
    for dirpath, dirnames, filenames in os.walk(
            src, followlinks=False, onerror=_onerror):
        kept_dirs: list[str] = []
        for name in dirnames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                symlinks += 1
            else:
                kept_dirs.append(name)
        dirnames[:] = sorted(kept_dirs)
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if full.is_symlink():
                symlinks += 1
                continue
            if not full.is_file():
                continue
            if _skippable(full):
                continue
            entries.append(full)
    entries.sort(key=lambda p: rel_to(p, src))
    return entries, symlinks, walk_errors


def _target_path(dst_root: Path, rel: str) -> Path:
    """把 POSIX 相对路径拼到目标根下（跨平台安全）。"""
    return Path(dst_root, *rel.split("/"))


def _target_status(src_file: Path, dst_file: Path, sha256: bool) -> str:
    """目标相对源的状态：``"copy"`` 或 ``"skip"``（只读，不写盘）。"""
    if dst_file.is_symlink() or not dst_file.is_file():
        return "copy"
    if _size(dst_file) != _size(src_file):
        return "copy"
    if sha256:
        try:
            if sha256_file(dst_file) != sha256_file(src_file):
                return "copy"
        except OSError:
            return "copy"
    return "skip"


def _target_ok(src_file: Path, dst_file: Path, sha256: bool) -> bool:
    """目标是否已与源一致（大小，必要时 sha256）。删源前的最终校验。"""
    try:
        if dst_file.is_symlink() or not dst_file.is_file():
            return False
        if _size(dst_file) != _size(src_file):
            return False
        if sha256:
            return sha256_file(dst_file) == sha256_file(src_file)
        return True
    except OSError:
        return False


def _copy_one(src_file: Path, dst_file: Path, sha256: bool) -> None:
    """复制单个文件：先写 ``<名>.part``，校验后 ``os.replace``；失败清理临时文件。

    校验项：目标临时文件大小必须等于源；``sha256=True`` 时再比 SHA256。
    任何异常都会删掉临时文件并原样抛出，交给上层重试 / 记入 ``failures``。
    """
    src_file = Path(src_file)
    dst_file = Path(dst_file)
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst_file.parent / (dst_file.name + ".part")
    try:
        with open(src_file, "rb") as fsrc, open(tmp, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst, _COPY_CHUNK)
        if _size(tmp) != _size(src_file):
            raise OssIngestError(f"大小校验失败：{tmp}")
        if sha256 and sha256_file(tmp) != sha256_file(src_file):
            raise OssIngestError(f"sha256 校验失败：{tmp}")
        os.replace(tmp, dst_file)
    except BaseException:
        _unlink_quiet(tmp)
        raise


def _process_one(src_file: Path, rel: str, dst_root: Path,
                 sha256: bool, delete_local: bool, dry_run: bool,
                 retries: int) -> dict:
    """处理单个文件，返回 ``{rel, action, copied, deleted, failure}``。"""
    dst_file = _target_path(dst_root, rel)
    status = _target_status(src_file, dst_file, sha256)

    if dry_run:
        # 只报告“会做什么”，不写、不删。delete_local 时把“会删的源”也预演出来。
        would_delete = bool(delete_local)
        return {"rel": rel, "action": status, "copied": status == "copy",
                "deleted": would_delete, "failure": None}

    if status == "skip":
        if not delete_local:
            return {"rel": rel, "action": "skip", "copied": False,
                    "deleted": False, "failure": None}
        if _target_ok(src_file, dst_file, sha256):
            try:
                src_file.unlink()
            except OSError as e:
                return {"rel": rel, "action": "skip", "copied": False,
                        "deleted": False,
                        "failure": _failure(src_file, dst_file, e)}
            return {"rel": rel, "action": "skip", "copied": False,
                    "deleted": True, "failure": None}
        # 目标在这期间被改坏了：退回复制。

    attempts = max(1, int(retries))
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            _copy_one(src_file, dst_file, sha256)
            last_error = None
            break
        except Exception as e:  # noqa: BLE001 - 单文件失败不应中断整棵树
            last_error = e
            if attempt < attempts - 1:
                time.sleep(1)

    if last_error is not None:
        marker = _failed_marker(src_file, last_error)
        return {"rel": rel, "action": "copy", "copied": False,
                "deleted": False,
                "failure": {**_failure(src_file, dst_file, last_error),
                            "marker": str(marker) if marker else None}}

    if delete_local:
        if not _target_ok(src_file, dst_file, sha256):
            return {"rel": rel, "action": "copy", "copied": True,
                    "deleted": False,
                    "failure": {"src": str(src_file), "dst": str(dst_file),
                                "error": "目标校验失败，未删除源文件"}}
        try:
            src_file.unlink()
        except OSError as e:
            return {"rel": rel, "action": "copy", "copied": True,
                    "deleted": False,
                    "failure": _failure(src_file, dst_file, e)}
        return {"rel": rel, "action": "copy", "copied": True,
                "deleted": True, "failure": None}

    return {"rel": rel, "action": "copy", "copied": True,
            "deleted": False, "failure": None}


def _failure(src_file: Path, dst_file: Path, error: BaseException) -> dict:
    """构造 ``failures`` 里的一条记录。"""
    return {"src": str(src_file), "dst": str(dst_file),
            "error": f"{type(error).__name__}: {error}"}


def _prune_empty_dirs(root: Path) -> None:
    """自底向上删除 ``root`` 下的空目录；``root`` 本身永不删除。"""
    root = Path(root)
    if not root.is_dir():
        return
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False,
                                                  followlinks=False):
        current = Path(dirpath)
        if current == root:
            continue
        try:
            os.rmdir(current)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
def sync_tree(src: str | Path, dst: str | Path, *, sha256: bool = False,
              delete_local: bool = False, dry_run: bool = False,
              workers: int = 1, retries: int = 3) -> dict:
    """把 ``src`` 目录树同步到 ``dst``，返回计数与失败清单。

    参数
    ----
    sha256
        大小相同时是否再比哈希；默认 ``False``（只比大小，常规重跑最便宜）。
        需要更强的完整性时显式传 ``sha256=True`` 或加 ``--sha256``。
    delete_local
        目标校验通过后删除源文件，并清理 ``src`` 下的空目录（保留 ``src``）。
        删除源时**强制**开启 sha256 校验，避免同尺寸的坏对象永久丢掉本地副本。
    dry_run
        只统计“会复制 / 会跳过 / 会删除源”，不写盘、不删源。
    workers
        ``>1`` 时用线程池并发复制；结果仍按相对路径排序汇总。
    retries
        单个文件最多尝试次数（``3`` = 最多 3 次尝试，失败间隔 1 秒）。永久
        失败会在源文件旁留 ``<名>.failed`` 标记，方便事后定位。

    返回
    ----
    ``{"files", "bytes", "copied", "skipped", "symlinks_skipped",
    "deleted", "failed", "failures", "walk_errors"}``。``files`` 是参与比较的
    源文件数（不含软链与临时后缀），``bytes`` 是这些文件的字节和；``dry_run``
    时 ``deleted`` 表示“会删除的源文件数”；``walk_errors`` 记录读不了的目录。
    """
    src_root = Path(str(src)).expanduser()
    dst_root = Path(str(dst)).expanduser()
    if not src_root.is_dir():
        raise OssIngestError(f"源不是目录：{src_root}")
    _check_roots(src_root, dst_root)
    if delete_local:
        sha256 = True

    entries, symlinks_skipped, walk_errors = _walk_source(src_root)
    total_bytes = sum(_size(p) for p in entries)
    retries = max(1, int(retries))

    def job(entry: Path) -> dict:
        return _process_one(entry, rel_to(entry, src_root), dst_root,
                            sha256, delete_local, dry_run, retries)

    if workers and int(workers) > 1:
        with ThreadPoolExecutor(max_workers=int(workers)) as pool:
            records = list(pool.map(job, entries))
    else:
        records = [job(entry) for entry in entries]
    records.sort(key=lambda r: r["rel"])

    copied = sum(1 for r in records if r["copied"])
    skipped = sum(1 for r in records if r["action"] == "skip")
    deleted = sum(1 for r in records if r["deleted"])
    failures = [r["failure"] for r in records if r["failure"]]

    if delete_local and not dry_run:
        _prune_empty_dirs(src_root)

    return {
        "files": len(entries),
        "bytes": total_bytes,
        "copied": copied,
        "skipped": skipped,
        "symlinks_skipped": symlinks_skipped,
        "deleted": deleted,
        "failed": len(failures),
        "failures": failures,
        "walk_errors": walk_errors,
    }


def verify_tree(src: str | Path, dst: str | Path, *,
                sha256: bool = False) -> dict:
    """只读校验 ``dst`` 是否完整复制了 ``src``，返回差异计数。

    返回 ``{"files", "bytes", "ok", "missing", "size_mismatch",
    "sha256_mismatch", "symlinks_skipped", "extra", "walk_errors"}``；
    ``extra`` 是目标里多出来的普通文件数（忽略软链与临时后缀）。
    **不写盘、不删文件。** ``sha256`` 默认 ``False``（只比大小）；
    需要哈希完整性时显式传 ``sha256=True``。
    """
    src_root = Path(str(src)).expanduser()
    dst_root = Path(str(dst)).expanduser()
    if not src_root.is_dir():
        raise OssIngestError(f"源不是目录：{src_root}")
    _check_roots(src_root, dst_root)

    entries, symlinks_skipped, walk_errors = _walk_source(src_root)
    total_bytes = sum(_size(p) for p in entries)

    missing = size_mismatch = sha256_mismatch = 0
    source_rels: set[str] = set()
    for entry in entries:
        rel = rel_to(entry, src_root)
        source_rels.add(rel)
        target = _target_path(dst_root, rel)
        if target.is_symlink() or not target.is_file():
            missing += 1
            continue
        if _size(target) != _size(entry):
            size_mismatch += 1
            continue
        if sha256:
            try:
                if sha256_file(entry) != sha256_file(target):
                    sha256_mismatch += 1
            except OSError:
                sha256_mismatch += 1

    extra = 0
    if dst_root.is_dir():
        for dirpath, dirnames, filenames in os.walk(dst_root,
                                                     followlinks=False):
            dirnames[:] = sorted(
                d for d in dirnames
                if not os.path.islink(os.path.join(dirpath, d)))
            for name in filenames:
                full = Path(dirpath) / name
                if full.is_symlink() or _skippable(full):
                    continue
                if rel_to(full, dst_root) not in source_rels:
                    extra += 1

    return {
        "files": len(entries),
        "bytes": total_bytes,
        "ok": len(entries) - missing - size_mismatch - sha256_mismatch,
        "missing": missing,
        "size_mismatch": size_mismatch,
        "sha256_mismatch": sha256_mismatch,
        "symlinks_skipped": symlinks_skipped,
        "extra": extra,
        "walk_errors": walk_errors,
    }


#: 迁移文档里的 ``oss_sync`` 名字：一行接入批量脚本。
#: ``from livepipe.tools.oss_ingest.sync import oss_sync``
oss_sync = sync_tree


__all__ = ["sync_tree", "verify_tree", "oss_sync"]
