"""``.part`` 收尾与成品校验（**只用标准库**）。

下载 / 录屏工具常先写 ``xxx.mp4.part``，确认完整后再改成正式名。本模块把
「确认完整 → 原子改名 → 记 manifest」收成一步：

- :func:`finalize_part`：校验 ``.part``（存在、非软链、非空），探测时长，
  ``os.replace`` 改名，并原子写 ``<final>.manifest.json``。
- :func:`verify_media`：只读地报告成品文件的字节数与时长。

安全约定：

- ``final`` 已存在且大小不同 → 默认拒绝，``force=True`` 才覆盖；同大小视为
  幂等重跑，允许替换。
- 默认**不**为 manifest 计算 sha256（3GB 文件再扫一遍太贵）；需要时由
  :mod:`.sync` / ``verify_tree`` 补。
- 只写 ``final`` 同目录下的 manifest，不碰别的路径。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from .common import (
    MANIFEST_VERSION,
    OssIngestError,
    probe_duration,
    write_json_atomic,
)

_PART_SUFFIX = ".part"

#: ``--expect-duration`` 允许的误差（秒）。
_DURATION_TOLERANCE = 0.5


def _unlink_quiet(path: str | Path) -> None:
    """删除文件；不存在 / 删不掉都忽略。"""
    try:
        Path(path).unlink()
    except OSError:
        pass


def verify_media(path: str | Path) -> dict:
    """只读校验一个成品媒体文件，返回 ``{"bytes": int, "duration_s": float|None}``。

    文件不存在 / 是软链 / 不是普通文件 / 大小为 0 → :class:`OssIngestError`。
    时长依赖 ffprobe；ffprobe 缺失或读不出时 ``duration_s`` 为 ``None``，
    不算错误。
    """
    p = Path(path)
    if p.is_symlink():
        raise OssIngestError(f"拒绝软链：{p}")
    if not p.exists():
        raise OssIngestError(f"文件不存在：{p}")
    if not p.is_file():
        raise OssIngestError(f"不是普通文件：{p}")
    try:
        size = p.stat().st_size
    except OSError as e:
        raise OssIngestError(f"无法读取文件大小：{p}（{e}）") from e
    if size <= 0:
        raise OssIngestError(f"文件为空：{p}")
    return {"bytes": size, "duration_s": probe_duration(p)}


def finalize_part(part: str | Path, final: str | Path | None = None, *,
                  force: bool = False, write_manifest: bool = True,
                  expect_bytes: int | None = None,
                  expect_duration: float | None = None,
                  allow_no_duration: bool = False,
                  metadata: dict | None = None) -> dict:
    """把 ``.part`` 收尾成正式文件，返回写出的 manifest dict。

    规则
    ----
    - ``part`` 必须存在、是普通文件、不是软链、大小 > 0。
    - ``final`` 省略时 = 去掉名字末尾的 ``.part``；名字不以 ``.part`` 结尾且
      未给 ``final`` → 报错（避免把任意文件误当半成品）。
    - ``final`` 已存在且大小不同 → 拒绝，``force=True`` 才覆盖；大小相同允许
      幂等替换。
    - **完整性闸门**：``expect_bytes`` 会核对字节数；``expect_duration`` 会核对
      时长（容差 0.5s）。
    - **默认要求有效时长**：改名前必须探到 ``duration_s > 0``；ffprobe 缺失或
      读不出 ⇒ 拒绝并保留 ``.part``，避免把截断的半成品提成正式文件。确认
      文件本身完整、只是环境探不到时长时，显式传 ``allow_no_duration=True``
      （CLI ``--allow-no-duration``）跳过该闸门，manifest 记 ``duration_s=null``。
      若同时给了 ``expect_duration``，则**始终**要求有效时长（不然无从核对）。
    - 先 ``os.replace`` 改名，再原子写 ``<final>.manifest.json``（与迁移文档
      的“改名成正式名，写 manifest”一致）。这样即使中途断电也不会留下指向
      不存在 final 的 manifest；manifest 写失败时 final 已在正式名下。
    - manifest schema：
      ``{version, part, final, bytes, duration_s, sha256|null, finalized_at}``，
      另把 ``metadata``（event/day/platform/account/trim 等）里的非空值并入。
      ``sha256`` 默认不读盘，固定为 ``null``；``finalized_at`` 是 UTC ISO8601。
    """
    src = Path(part)
    if final is None:
        if not src.name.endswith(_PART_SUFFIX) or src.name == _PART_SUFFIX:
            raise OssIngestError(
                f"文件名不以 {_PART_SUFFIX} 结尾，请用 --final 指定目标名：{src}")
        dst = src.with_name(src.name[:-len(_PART_SUFFIX)])
    else:
        dst = Path(final)

    if src.is_symlink():
        raise OssIngestError(f"拒绝软链：{src}")
    if not src.exists():
        raise OssIngestError(f"待收尾文件不存在：{src}")
    if not src.is_file():
        raise OssIngestError(f"不是普通文件：{src}")
    try:
        size = src.stat().st_size
    except OSError as e:
        raise OssIngestError(f"无法读取文件大小：{src}（{e}）") from e
    if size <= 0:
        raise OssIngestError(f"待收尾文件为空：{src}")

    if expect_bytes is not None and size != int(expect_bytes):
        raise OssIngestError(
            f"字节数不符：{src} 实际 {size}，期望 {expect_bytes}（保留 .part）")

    if dst.is_symlink():
        raise OssIngestError(f"目标已存在且是软链，拒绝覆盖：{dst}")
    if dst.exists():
        if not dst.is_file():
            raise OssIngestError(f"目标已存在且不是普通文件：{dst}")
        try:
            dst_size = dst.stat().st_size
        except OSError as e:
            raise OssIngestError(f"无法读取目标大小：{dst}（{e}）") from e
        if dst_size != size and not force:
            raise OssIngestError(
                f"目标已存在且大小不同（{dst_size} != {size}）：{dst}\n"
                "  确认要用 .part 覆盖它，请加 --force")

    duration = probe_duration(src)
    # 只接受有限正数；0、负数、NaN 都按「探不到有效时长」处理。
    if duration is not None and not (float(duration) > 0):
        duration = None
    if expect_duration is not None:
        if duration is None:
            raise OssIngestError(
                f"给了 --expect-duration 但无法确认时长（ffprobe 不可用或读不出），"
                f"拒绝收尾：{src}\n  请安装 ffprobe 或去掉 --expect-duration；"
                ".part 已保留")
        if abs(float(duration) - float(expect_duration)) > _DURATION_TOLERANCE:
            raise OssIngestError(
                f"时长不符：{src} 实际 {float(duration):.3f}s，"
                f"期望 {float(expect_duration):.3f}s（保留 .part）")
    elif duration is None and not allow_no_duration:
        raise OssIngestError(
            f"无法确认有效时长（ffprobe 不可用或读不出），拒绝把 .part 提成"
            f"正式文件：{src}\n"
            "  请安装 ffprobe 后重试；确认文件完整、只是环境探不到时长时，"
            "显式加 --allow-no-duration（API: allow_no_duration=True）。\n"
            "  .part 已保留")

    manifest = {
        "version": MANIFEST_VERSION,
        "part": str(src),
        "final": str(dst),
        "bytes": size,
        "duration_s": duration,
        "sha256": None,
        "finalized_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z"),
    }
    for key, value in (metadata or {}).items():
        if value is not None:
            manifest[key] = value

    mpath = manifest_path(dst)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
    except OSError as e:
        raise OssIngestError(f"改名失败：{src} → {dst}（{e}）") from e

    # 改名成功后再写 manifest：断电窗口只会是「有 final、无 manifest」，
    # 不会留下指向不存在 final 的假 manifest。
    if write_manifest:
        try:
            write_json_atomic(mpath, manifest)
        except OssIngestError as e:
            raise OssIngestError(
                f"已改名 {src} → {dst}，但 manifest 写入失败：{e}\n"
                f"  文件已是正式名；请检查 {mpath} 后补写") from e

    return manifest


def manifest_path(final: str | Path) -> Path:
    """成品文件对应的 manifest 路径：``<final>.manifest.json``。"""
    return Path(str(final) + ".manifest.json")
