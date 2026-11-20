"""``oss_ingest`` 的公共基础函数（**只用标准库**）。

这里是唯一允许所有子模块导入的底座：

- 路径与常量：:data:`DEFAULT_OSS_ROOT`、:data:`OSS_ROOT_CANDIDATES`、
  :func:`oss_root`（纯路径）、:func:`resolve_oss_root`（自动选真实可用的根）、
  :func:`rel_to`
- 文件读取：:func:`file_size`、:func:`sha256_file`、:func:`iter_files`
- 段统计：:func:`seg_bytes`、:func:`count_segments`（``manifest`` / ``doc``
  共用同一口径）
- JSON：:func:`read_json`（出错统一抛 :class:`OssIngestError`）、
  :func:`write_json_atomic`（同目录 ``.tmp`` + ``fsync`` + ``os.replace``）
- 展示与探测：:func:`human_bytes`、:func:`probe_duration`（调 ffprobe）

约定：本模块**不**判断 ``.part/.lock/.failed/.tmp`` 之类业务后缀，那是
:mod:`.sync` 的职责；本模块也不读任何凭据内容。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterator

#: 生产 OSS 根的最后一个候选，也是 ``oss_root()`` 的纯路径默认值。
#: 注意：CLI 未显式传 ``--oss-root`` 时走 :func:`resolve_oss_root` 的候选顺序，
#: 不会直接使用这个值。
DEFAULT_OSS_ROOT = "~/oss-bucket/web/1/奇瑞汽车/直播看管"

#: 未指定 ``--oss-root`` 时按顺序自动选择「存在且至少含一个视频文件」的根：
#: 1. 当前真实存量（存量录屏实际在 ``直播data/录屏``，已验证覆盖 22 份配置）；
#: 2. 迁移文档的新契约（``web/1/奇瑞汽车/直播看管/直播录屏``）；
#: 3. 旧兜底（``web/1/奇瑞汽车/直播看管``）。
#: ⚠ 项目 OSS 根在 ``web/1/`` 下（2026-11-19 用户确认）；桶根 ``奇瑞汽车/`` 是
#: P1/P2 早期写错的前缀，只作迁移前的历史保留，不要再往那里写。
OSS_ROOT_CANDIDATES = (
    "~/oss-bucket/直播data/录屏",
    "~/oss-bucket/web/1/奇瑞汽车/直播看管/直播录屏",
    "~/oss-bucket/web/1/奇瑞汽车/直播看管",
)

#: 判断目录里是否有视频时认的扩展名（与 ``manifest._VIDEO_EXTS`` 一致）。
VIDEO_EXTENSIONS = frozenset(
    {".mp4", ".mov", ".mkv", ".avi", ".flv", ".ts", ".m4v"})


def is_temp_name(name: str) -> bool:
    """下载器临时 / 半成品名：``*.p.mp4`` / 含 ``.baiduyun``；不算可用视频。

    这类对象通常还在下载或只有 0 字节，选中它们会把「空根」误判成有效根，
    也会让统计的索引视频数比映射引擎多。
    """
    lower = name.lower()
    return lower.endswith(".p.mp4") or ".baiduyun" in lower


#: 映射清单 / 收尾 manifest 的 schema 版本。
MANIFEST_VERSION = 1

#: :func:`probe_duration` 调 ffprobe 的超时（秒）。
_PROBE_TIMEOUT = 20

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


class OssIngestError(Exception):
    """``oss_ingest`` 的可预期错误：消息直接给人看。"""


# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #
def oss_root(value: str | Path | None = None) -> Path:
    """OSS 根路径：展开 ``~`` 并转成绝对路径，**不** resolve（不跟软链）。

    省略 ``value`` 时用 :data:`DEFAULT_OSS_ROOT`。本函数是纯路径换算，不判断
    目录是否存在（自检依赖这一点）；需要「自动选一个真实可用的根」时用
    :func:`resolve_oss_root`。
    """
    raw = DEFAULT_OSS_ROOT if value is None else value
    p = Path(str(raw)).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    return Path(os.path.abspath(str(p)))


def has_video_file(root: str | Path, *,
                   extensions: frozenset[str] = VIDEO_EXTENSIONS) -> bool:
    """``root`` 下（递归）是否至少有一个**可用**视频文件。

    用于判断一个候选 OSS 根是否「真的有录屏」：空目录、只有结果目录、只有
    0 字节文件或下载器临时名（``*.p.mp4`` / ``*.baiduyun*``）的根都不算有效
    根。默认跳过软链，读不了的目录按「没有」处理。
    """
    base = Path(root)
    if not base.is_dir():
        return False

    def _onerror(_error: OSError) -> None:
        # 自动选根只是探测，读不了的子目录直接跳过即可。
        return None

    for dirpath, dirnames, filenames in os.walk(
            base, followlinks=False, onerror=_onerror):
        dirnames[:] = sorted(
            d for d in dirnames
            if not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            if Path(name).suffix.lower() not in extensions:
                continue
            if is_temp_name(name):
                continue
            full = Path(dirpath) / name
            if full.is_symlink():
                continue
            try:
                if full.stat().st_size <= 0:
                    continue
            except OSError:
                continue
            return True
    return False


def resolve_oss_root(value: str | Path | None = None, *,
                     candidates: tuple[str, ...] | list[str] | None = None,
                     strict: bool = True) -> tuple[Path, str]:
    """解析实际使用的 OSS 根，返回 ``(路径, 来源)``。

    - ``value`` 非空：显式覆盖，来源 ``"explicit"``；``strict=True`` 时要求
      路径存在（写错路径会得到「根不存在」的可行动错误，而不是全量缺失）。
    - 否则按 ``candidates``（默认 :data:`OSS_ROOT_CANDIDATES`）顺序，返回第一
      个「是目录且至少含一个视频文件」的根，来源 ``"auto"``。
    - 全部不满足时：``strict=True`` 抛 :class:`OssIngestError`（给可行动的
      提示）；``strict=False`` 返回第一个候选路径、来源 ``"unresolved"``，
      供 ``check-env`` 在这台机器还没挂载 OSS 时继续给出逐项诊断。
    """
    if value is not None:
        p = oss_root(value)
        if strict and not p.is_dir():
            raise OssIngestError(
                f"显式 OSS 根不存在：{p}\n"
                "  请检查 OSS 是否已挂载，或改用 --oss-root 指向实际目录"
                "（候选顺序见 tools/oss_ingest/README.md）。")
        return p, "explicit"

    roots = tuple(candidates) if candidates is not None else OSS_ROOT_CANDIDATES
    checked: list[Path] = []
    for raw in roots:
        p = oss_root(raw)
        checked.append(p)
        if has_video_file(p):
            return p, "auto"

    if strict:
        detail = "、".join(str(p) for p in checked) or "（候选列表为空）"
        raise OssIngestError(
            f"找不到可用的 OSS 根：按顺序检查了 {detail}，"
            "都不存在或目录里没有视频文件。\n"
            "  请先用 --oss-root 指定实际挂载目录，或确认 goofys 已挂载"
            "（默认根候选顺序见 tools/oss_ingest/README.md）。")
    fallback = checked[0] if checked else oss_root()
    return fallback, "unresolved"


def rel_to(path: str | Path, root: str | Path) -> str:
    """``path`` 相对 ``root`` 的 POSIX 形式路径；越界（含 ``..``）返回 ``".."``。

    例：``rel_to("/a/b/c.mp4", "/a")`` → ``"b/c.mp4"``。
    """
    try:
        rel = os.path.relpath(
            os.path.abspath(str(path)), os.path.abspath(str(root)))
    except (TypeError, ValueError, OSError):
        return ".."
    rel = rel.replace(os.sep, "/")
    if rel == ".." or rel.startswith("../"):
        return ".."
    return rel


# --------------------------------------------------------------------------- #
# 展示 / 探测
# --------------------------------------------------------------------------- #
def human_bytes(n: int) -> str:
    """字节数 → 人读的 1024 进制字符串：``0 B`` / ``1.0 KB`` / ``133.4 GB``。"""
    try:
        size = float(n)
    except (TypeError, ValueError):
        return "-"
    sign = "-" if size < 0 else ""
    size = abs(size)
    if size < 1024:
        return f"{sign}{size:.0f} B"
    value = size
    for unit in _UNITS[1:]:
        value /= 1024
        if value < 1024:
            return f"{sign}{value:.1f} {unit}"
    return f"{sign}{value:.1f} {_UNITS[-1]}"


def file_size(path: str | Path) -> int:
    """文件字节数；缺失 / 不是普通文件 / 读不到都返回 ``-1``。"""
    try:
        p = Path(path)
        if not p.is_file():
            return -1
        return p.stat().st_size
    except OSError:
        return -1


def seg_bytes(seg: dict) -> int:
    """清单段的 ``bytes``；缺失 / 非法按 ``-1``（本地不可用）计。"""
    try:
        return int(seg.get("bytes", -1))
    except (TypeError, ValueError):
        return -1


def count_segments(segments) -> dict:
    """按统一口径统计映射清单的段，供 ``manifest`` / ``audit`` / ``doc`` 共用。

    口径（与 README 一致）：

    - ``refs``：段总数；
    - ``full_mapped``：非裁尾且已有 ``oss``；
    - ``trimmed``：裁尾段；
    - ``missing``：非裁尾且 ``oss`` 为空——**包括**本地文件不可用
      （``bytes < 0``）的段，以及配置指向 OSS 但对象不存在（``oss_missing``）
      的段；素材盘没挂载时不会误报 PASS；
    - ``local_missing``：任意段 ``bytes < 0``（含裁尾段），作为独立诊断。
    """
    segs = list(segments or [])
    return {
        "refs": len(segs),
        "full_mapped": sum(
            1 for s in segs if not s.get("trimmed") and s.get("oss")),
        "trimmed": sum(1 for s in segs if s.get("trimmed")),
        "missing": sum(
            1 for s in segs if not s.get("trimmed") and not s.get("oss")),
        "local_missing": sum(1 for s in segs if seg_bytes(s) < 0),
    }


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    """流式算 SHA256，默认 1 MiB 一块。文件读不到时抛 ``OSError``。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def probe_duration(path: str | Path) -> float | None:
    """用 ffprobe 读容器时长（秒）；ffprobe 缺失、超时、失败或结果非数字 → ``None``。"""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    cmd = [
        exe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    lines = (proc.stdout or "").strip().splitlines()
    if not lines:
        return None
    try:
        return float(lines[0].strip())
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #
def read_json(path: str | Path) -> dict:
    """读 JSON 对象；文件缺失 / 坏 JSON / 顶层不是对象 → :class:`OssIngestError`。"""
    p = Path(path)
    try:
        with open(p, encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError as e:
        raise OssIngestError(f"文件不存在：{p}") from e
    except (OSError, json.JSONDecodeError) as e:
        raise OssIngestError(f"无法读取 JSON：{p}（{e}）") from e
    if not isinstance(data, dict):
        raise OssIngestError(f"JSON 顶层不是对象：{p}")
    return data


def write_json_atomic(path: str | Path, data: dict) -> None:
    """原子写 JSON：同目录 ``<名>.tmp`` → ``fsync`` → ``os.replace``。

    写入中途出错会清掉临时文件；父目录不存在时自动创建。
    """
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise OssIngestError(f"无法创建目录：{p.parent}（{e}）") from e
    tmp = p.parent / (p.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except OSError as e:
        _unlink_quiet(tmp)
        raise OssIngestError(f"无法写入 JSON：{p}（{e}）") from e
    except BaseException:
        _unlink_quiet(tmp)
        raise


def _unlink_quiet(path: Path) -> None:
    """删除临时文件；不存在 / 删不掉都忽略。"""
    try:
        path.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# 遍历
# --------------------------------------------------------------------------- #
def iter_files(root: str | Path, *,
               follow_symlinks: bool = False) -> Iterator[Path]:
    """遍历 ``root`` 下所有普通文件，按相对路径（POSIX）排序。

    - 默认**不跟随**软链：软链文件和软链目录都跳过。
    - ``follow_symlinks=True`` 时把软链也当普通条目收进来。
    - 读不了的目录抛 :class:`OssIngestError`，不静默跳过（清单/审计需要看到
      部分遍历）。
    - ``root`` 不存在或不是目录时产出空。
    """
    base = Path(root)
    if not base.is_dir():
        return

    def _onerror(error: OSError) -> None:
        # 读不了的目录必须显式失败，避免 manifest/audit 静默漏文件。
        raise OssIngestError(
            f"遍历目录失败：{getattr(error, 'filename', base)}（{error}）") from error

    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(
            base, followlinks=follow_symlinks, onerror=_onerror):
        if not follow_symlinks:
            dirnames[:] = [
                d for d in dirnames
                if not os.path.islink(os.path.join(dirpath, d))
            ]
            dirnames.sort()
        for name in filenames:
            full = Path(dirpath) / name
            if not follow_symlinks and full.is_symlink():
                continue
            found.append(full)
    found.sort(key=lambda p: rel_to(p, base))
    for p in found:
        yield p
