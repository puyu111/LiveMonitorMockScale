"""配置 → OSS 映射清单与只读审计（**只用标准库**）。

两件事：

- :func:`build_config_manifest` —— 把一份 livepipe 配置里的每个视频段映射到
  OSS 上的对应对象（按字节大小；同尺寸多候选时再比时长），产出可落盘的清单。
- :func:`audit_configs` —— 对一批配置做同样的事，只汇总「引用 / 已映射 / 裁尾 /
  缺失」，坏配置只记进 ``details``，不中断整次审计。

映射口径（与 ``docs/oss-migration.html`` 一致）：

- **整段**：本地文件字节数与 OSS 候选完全相等即命中；同尺寸多候选时优先选
  ffprobe 时长差 ``≤ 0.5s`` 且最接近的那个，否则取相对路径排序第一个。
- **裁尾**：路径含 ``/_live/``、文件名以 ``_live.mp4`` 结尾，或同时含 ``/out/``
  与 ``/media/`` 且文件名带 ``_live`` ⇒ ``trimmed=true``、``oss=null``。
  没有 source-map 参数时**不臆造** ``source_oss``；``trim_s`` 在配置没写
  ``duration`` 时回退解析 ``_live`` 文件名的 ``_<start>-<end>``，两者都没有时
  用已探到的时长回退成 ``[start or 0, start + duration_s]``。
- **配置已指向 OSS**：视频路径本身就在 ``oss_root`` 下 ⇒ 直接 stat，不再按大小搜；
  对象不存在时记 ``oss_missing=true``，由审计的 ``missing`` 计数发现。

OSS 索引只建一次（:func:`_build_oss_index`），同一批配置间复用
（:func:`build_config_manifests`），避免每份配置重扫整棵挂载树。索引只收常见
视频扩展名，跳过 0 字节文件与 ``*.p.mp4`` / ``*.baiduyun*`` 下载临时名，
**索引阶段不跑 ffprobe**（时长惰性探测，仅同尺寸多候选 tie-break 时现探），
桶内按相对路径排序以保证确定性。
"""

from __future__ import annotations

import glob as _glob
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ... import config as C
from .common import (
    MANIFEST_VERSION,
    OssIngestError,
    count_segments,
    file_size,
    is_temp_name,
    iter_files,
    oss_root as _oss_root,
    probe_duration,
    rel_to,
)

#: 索引 / 目录展开时认的视频扩展名（与 ``livepipe.config.constants._VIDEO_EXTS`` 一致）。
_VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".flv", ".ts", ".m4v")

#: 同尺寸多候选时允许的时长误差（秒）。
_DURATION_TOLERANCE = 0.5

#: ``_live`` 文件名末尾的裁尾区间：``_<start>-<end>``（如 ``_0-2854``）。
_TRIM_SUFFIX_RE = re.compile(r"_(-?\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$")


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _abs(path: str | Path) -> Path:
    """展开 ``~`` 并转绝对路径（不 resolve，保留调用方给的软链形态）。"""
    return Path(os.path.abspath(str(Path(str(path)).expanduser())))


def _utc_now() -> str:
    """UTC ISO8601，例如 ``2026-11-17T09:30:00Z``。"""
    return (datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"))


def _strip_jsonc(text: str) -> str:
    """去掉 ``//`` / ``/* */`` 注释与末尾多余逗号（字符串里的内容原样保留）。

    只在 :func:`_fallback_segments` 里用；配置正常时走 ``livepipe.config.io.load``，
    它自带更完整的 JSONC 解析。
    """
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1

    # 第二遍：删掉「后面只剩 } 或 ]」的逗号。同样避开字符串。
    s, out2, i, n, in_str = "".join(out), [], 0, len(out), False
    while i < n:
        c = s[i]
        if in_str:
            out2.append(c)
            if c == "\\" and i + 1 < n:
                out2.append(s[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
        elif c == ",":
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] in "}]":
                i += 1
                continue
        out2.append(c)
        i += 1
    return "".join(out2)


# --------------------------------------------------------------------------- #
# 配置读取：优先 livepipe.config，失败再走本地 JSONC 兜底
# --------------------------------------------------------------------------- #
def _fallback_segments(config_path: str | Path) -> list[dict]:
    """读不了 livepipe 配置时的兜底：只认 ``input.video/videos/start/duration``。

    - 目录 / 通配符能展开就展开，展开结果为空 ⇒ :class:`OssIngestError`
      （整份配置记 error，而不是假装 0 引用）。
    - 相对路径按配置的 ``base_dir`` 解析；没写 ``base_dir`` 时按配置文件所在目录。
    """
    path = Path(config_path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise OssIngestError(f"无法读取配置：{path}（{e}）") from e
    try:
        doc = json.loads(_strip_jsonc(text))
    except json.JSONDecodeError as e:
        raise OssIngestError(f"配置不是合法 JSON/JSONC：{path}（{e}）") from e
    if not isinstance(doc, dict):
        raise OssIngestError(f"配置根节点不是对象：{path}")

    inp = doc.get("input")
    if not isinstance(inp, dict):
        raise OssIngestError(f"配置缺少 input 节：{path}")

    base = doc.get("base_dir")
    if isinstance(base, str) and base:
        base_dir = Path(base).expanduser()
        if not base_dir.is_absolute():
            base_dir = path.parent / base_dir
    else:
        base_dir = path.parent

    if inp.get("videos") is not None:
        raw = inp["videos"]
        specs = list(raw) if isinstance(raw, (list, tuple)) else [raw]
    elif inp.get("video"):
        specs = [inp["video"]]
    else:
        raise OssIngestError(f"配置 input 里没有 video/videos：{path}")

    videos: list[str] = []
    for item in specs:
        if not isinstance(item, str):
            raise OssIngestError(
                f"input.video/videos 的项必须是字符串：{item!r}（{path}）")
        p = Path(item).expanduser()
        if not p.is_absolute():
            p = base_dir / p
        s = str(p)
        if any(ch in s for ch in "*?["):
            hits = sorted(_glob.glob(s))
            if not hits:
                raise OssIngestError(
                    f"input.videos 的匹配式没有命中任何文件：{s}（{path}）")
            videos += hits
        elif p.is_dir():
            hits = sorted(
                str(q) for q in p.iterdir()
                if q.is_file() and q.suffix.lower() in _VIDEO_EXTS)
            if not hits:
                raise OssIngestError(f"input.videos 指向的目录里没有视频：{p}")
            videos += hits
        else:
            if not p.exists() and p.suffix.lower() not in _VIDEO_EXTS:
                # 没有视频扩展名、又不存在：大概率是未挂载素材盘上的目录，
                # 绝不能当成单个视频文件（否则 refs 少算、bytes=-1 被误判）。
                raise OssIngestError(
                    f"input.videos 的引用无法展开（目录不存在或不是视频文件）："
                    f"{p}（{path}）")
            videos.append(str(p))

    if not videos:
        raise OssIngestError(f"input.videos 没有展开出任何视频：{path}")

    try:
        start = float(inp.get("start") or 0.0)
        duration = (float(inp["duration"])
                    if inp.get("duration") is not None else None)
    except (TypeError, ValueError) as e:
        raise OssIngestError(f"input.start/duration 不是数字：{path}") from e

    # 段名与 livepipe.config 保持一致：显式 segment_names 原样保留，长度不符
    # 时（配置本来就有问题）退回 segN，而不是让兜底解析再失败一次。
    names = inp.get("segment_names")
    if not isinstance(names, (list, tuple)) or len(names) != len(videos):
        names = None
    return [
        {"name": str(names[i]) if names is not None else None,
         "video": v, "start": start, "duration": duration}
        for i, v in enumerate(videos)
    ]


def _needs_expansion(raw: object) -> bool:
    """该引用是否仍需展开（目录 / 通配符 / 缺失的目录式路径）。

    生产配置允许 ``input.videos`` 写目录或通配符；``livepipe.config`` 在
    ``check_videos=False`` 时可能把它原样留成单条。这里识别出来，交给
    :func:`_fallback_segments` 展开（或报错），绝不当成单个视频文件。
    """
    if not isinstance(raw, str) or not raw:
        return False
    if any(ch in raw for ch in "*?["):
        return True
    if raw.endswith(("/", os.sep)):
        return True
    p = _abs(raw)
    if p.is_dir():
        return True
    # 不存在且没有视频扩展名：很可能是指向目录的引用（素材盘未挂载），
    # 交给兜底读取器给出「无法展开」的可行动错误。
    return not p.exists() and p.suffix.lower() not in _VIDEO_EXTS


def _load_segments(config_path: str | Path) -> list[dict]:
    """优先用 ``livepipe.config.io.load`` + ``segments``；失败再走本地兜底。

    返回的段保留 ``name/video/start/duration`` 四个键（其它键不用）。
    若 ``C.segments`` 留下未展开的目录 / 通配符引用，也改走兜底展开，避免把
    一个目录当成单个视频（会导致 bytes=-1、refs 少算）。
    """
    try:
        cfg = C.load(config_path, check_videos=False)
        segs = C.segments(cfg)
        if any(_needs_expansion(seg.get("video")) for seg in segs):
            return _fallback_segments(config_path)
        return segs
    except (Exception, SystemExit):  # ConfigError 继承 SystemExit，必须显式接住
        try:
            return _fallback_segments(config_path)
        except Exception as second:
            # 兜底错误里已经带了配置路径；不要再套一层前缀，否则
            # 审计 / 清单里会出现「无法读取配置 /x：无法读取配置：/x」。
            raise OssIngestError(str(second)) from second


# --------------------------------------------------------------------------- #
# OSS 索引
# --------------------------------------------------------------------------- #
def _is_temp_name(name: str) -> bool:
    """兼容旧名；规则统一在 :func:`common.is_temp_name`。"""
    return is_temp_name(name)


def _build_oss_index(root: str | Path) -> dict[int, list[dict]]:
    """一次性建立 ``{bytes: [{path, rel, duration}, ...]}``。

    **惰性时长**：索引阶段只 stat 大小，``duration`` 先置 ``None``；只有同尺寸
    多候选需要 tie-break 时才由 :func:`_candidate_duration` 现探并写回缓存。
    这样默认根（整棵 ``直播data/录屏``）下 22 份配置的审计不再对每个对象跑
    ffprobe。

    - 只索引常见视频扩展名；``.part/.lock/.failed/.tmp`` 天然不在扩展名表里。
    - 跳过 0 字节文件与下载临时名（``*.p.mp4`` / ``*.baiduyun*``）：它们可能是
      未完成的半成品，且对 ffprobe 很慢。
    - 每个桶按 ``rel`` 排序，保证「同尺寸取第一个」是确定的。
    """
    index: dict[int, list[dict]] = {}
    for p in iter_files(root):
        if p.suffix.lower() not in _VIDEO_EXTS or _is_temp_name(p.name):
            continue
        size = file_size(p)
        if size <= 0:
            continue
        entry = {
            "path": str(p),
            "rel": rel_to(p, root),
            "duration": None,
        }
        index.setdefault(size, []).append(entry)
    for entries in index.values():
        entries.sort(key=lambda e: e["rel"])
    return index


def _candidate_duration(cand: dict) -> float | None:
    """候选时长（惰性）：``duration`` 为 ``None`` 时才 ffprobe，并写回缓存。"""
    duration = cand.get("duration")
    if duration is None:
        duration = probe_duration(cand.get("path"))
        cand["duration"] = duration
    if duration is None:
        return None
    try:
        return float(duration)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# 单段映射
# --------------------------------------------------------------------------- #
def _is_trimmed(raw: str) -> bool:
    """裁尾引用识别（大小写不敏感），三种写法任一命中即可。"""
    s = str(raw).replace("\\", "/").lower()
    name = s.rsplit("/", 1)[-1]
    return (
        "/_live/" in s
        or name.endswith("_live.mp4")
        or ("/out/" in s and "/media/" in s and "_live" in name)
    )


def _lookup_source(source_map: dict | None, raw_path: Path) -> str | None:
    """在可选 ``source_map``（磁盘路径/文件名 → OSS 源对象）里查裁尾段的源。

    支持三种键：绝对路径、文件名、POSIX 形式的原字符串。查不到返回 ``None``，
    **绝不臆造** ``source_oss``。
    """
    if not source_map:
        return None
    candidates = [str(raw_path), raw_path.name,
                  str(raw_path).replace("\\", "/")]
    for key in candidates:
        value = source_map.get(key)
        if value:
            return str(value)
    for key, value in source_map.items():
        if value and Path(str(key)).name == raw_path.name:
            return str(value)
    return None


def _trim_range(start: Any, duration: Any, raw: str | Path | None = None,
                duration_s: Any = None) -> list[float] | None:
    """裁尾区间 ``[start, end]``。

    优先用配置的 ``start`` + ``duration``；生产配置的 ``duration`` 多为 null，
    此时回退解析 ``_live`` 文件名末尾的 ``_<start>-<end>``（如 ``_0-2854``）；
    两者都没有时，用已探到的 ``duration_s`` 回退成 ``[start or 0, start + duration_s]``
    （10-19 的 ``dy_qq_16-31-17-00_live.mp4`` → ``[0, 5455.716]``）。
    若探到的时长与文件名 end 的差 ≤ 1s，用更精确的时长作为 end。
    """
    if duration is not None:
        try:
            s = float(start or 0.0)
            return [s, s + float(duration)]
        except (TypeError, ValueError):
            return None
    if raw is None:
        return None
    match = _TRIM_SUFFIX_RE.search(Path(str(raw)).stem)
    if not match:
        # 配置没写 start+duration，文件名也没有 `_<start>-<end>`（10-19 的
        # `dy_qq_16-31-17-00_live.mp4` 就是这种）：用已探到的时长回退。
        if duration_s is not None:
            try:
                begin = float(start or 0.0)
                probed = float(duration_s)
            except (TypeError, ValueError):
                return None
            if probed > 0:
                return [begin, begin + probed]
        return None
    try:
        begin = float(match.group(1))
        end = float(match.group(2))
    except (TypeError, ValueError):
        return None
    if duration_s is not None:
        try:
            probed = float(duration_s)
            if abs(probed - end) <= 1.0:
                end = probed
        except (TypeError, ValueError):
            pass
    return [begin, end]


def _choose_candidate(local_path: Path, candidates: list[dict]
                      ) -> tuple[dict, float | None]:
    """同尺寸多候选：选时长最接近且差 ``≤ 0.5s`` 的；否则取排序第一个。

    返回 ``(候选, 本地时长)`` —— 本地时长顺带回传，避免再探一次。
    """
    local_duration = probe_duration(local_path)
    if local_duration is not None:
        best: dict | None = None
        best_delta: float | None = None
        for cand in candidates:
            dur = _candidate_duration(cand)
            if dur is None:
                continue
            try:
                delta = abs(dur - float(local_duration))
            except (TypeError, ValueError):
                continue
            if delta <= _DURATION_TOLERANCE and (
                    best_delta is None or delta < best_delta):
                best, best_delta = cand, delta
        if best is not None:
            return best, local_duration
    return candidates[0], local_duration


def _segment_entry(seg: dict, i: int, root: Path, disk_root: Path | None,
                   index: dict[int, list[dict]],
                   source_map: dict | None = None) -> dict | None:
    """一个段 → 清单条目；``video`` 为空（如 rows/text 配置）返回 ``None``。"""
    raw = seg.get("video")
    if not raw:
        return None
    raw_path = _abs(raw)
    raw_str = str(raw_path)

    # 目录 / 通配符必须先展开；绝不当成单个视频（否则会少算 refs）。
    if raw_path.is_dir() or any(ch in str(raw) for ch in "*?["):
        raise OssIngestError(f"视频引用未展开（目录 / 通配符）：{raw_str}")

    entry: dict[str, Any] = {
        "seg": str(seg.get("name") or f"seg{i + 1}"),
        "disk": (rel_to(raw_path, disk_root) if disk_root is not None
                 else raw_str),
        "oss": None,
        "trimmed": False,
        "source_oss": None,
        "oss_missing": False,
        "bytes": -1,
        "duration_s": None,
        "trim_s": None,
    }

    # 1) 配置本身已指向 OSS：直接 stat，不再按大小搜。
    #    但只有对象真的存在才认作已映射；否则留 oss=None 并用 oss_missing
    #    标记，让 missing 计数发现它（迁移后配置最常见的坏法）。
    if rel_to(raw_path, root) != "..":
        size = file_size(raw_path)
        entry["bytes"] = size
        if size >= 0:
            entry["oss"] = raw_str
            entry["duration_s"] = probe_duration(raw_path)
        else:
            entry["oss_missing"] = True
        return entry

    # 2) 裁尾引用：不臆造 source_oss（除非给了 source-map），trim 区间优先
    #    取配置，其次解析 _live 文件名末尾的 _<start>-<end>。
    if _is_trimmed(raw_str):
        entry["trimmed"] = True
        entry["bytes"] = file_size(raw_path)
        if entry["bytes"] >= 0:
            entry["duration_s"] = probe_duration(raw_path)
        entry["source_oss"] = _lookup_source(source_map, raw_path)
        entry["trim_s"] = _trim_range(seg.get("start"), seg.get("duration"),
                                      raw_str, entry["duration_s"])
        return entry

    # 3) 整段：按字节大小映射。
    size = file_size(raw_path)
    entry["bytes"] = size
    if size < 0:                      # 本地文件缺失 / 读不到 ⇒ missing
        return entry
    candidates = index.get(size) or []
    if not candidates:                # 有尺寸但 OSS 上没有同尺寸对象 ⇒ missing
        return entry

    if len(candidates) == 1:
        # 唯一候选不探 OSS 时长：本地盘探测快，用现有兜底，结果与原实现一致。
        chosen = candidates[0]
        local_duration = None
        chosen_duration = None
    else:
        chosen, local_duration = _choose_candidate(raw_path, candidates)
        # tie-break 已经探过（并缓存）候选时长；本地时长探不到时，
        # 这里补探选中的候选，保持与原「索引阶段全量探测」相同的取值。
        chosen_duration = _candidate_duration(chosen)
    entry["oss"] = chosen["path"]
    entry["duration_s"] = chosen_duration
    if entry["duration_s"] is None and local_duration is not None:
        entry["duration_s"] = local_duration
    elif entry["duration_s"] is None:
        entry["duration_s"] = probe_duration(raw_path)
    return entry


# --------------------------------------------------------------------------- #
# 公开接口
# --------------------------------------------------------------------------- #
def build_config_manifest(config_path: str | Path, oss_root: str | Path,
                          disk_root: str | Path | None = None,
                          source_map: dict | None = None) -> dict:
    """一份配置 → 映射清单（schema 见模块 docstring / 迁移文档）。

    ``disk_root`` 只影响 ``disk`` 字段的**展示**（相对该目录），文件读取仍用
    解析后的绝对路径。``oss_root`` 可以是 ``~`` 路径，会展开。
    ``source_map`` 可选（磁盘路径/文件名 → OSS 源对象），只用于给裁尾段补
    ``source_oss``。
    """
    root = _oss_root(oss_root)
    index = _build_oss_index(root)
    return _manifest(config_path, root, disk_root, index, source_map)


def build_config_manifests(config_paths: Sequence[str | Path],
                           oss_root: str | Path,
                           disk_root: str | Path | None = None,
                           source_map: dict | None = None
                           ) -> tuple[list[dict], list[dict]]:
    """批量生成清单：**OSS 索引只建一次**，所有配置共用。

    返回 ``(manifests, errors)``；``errors`` 里每项 ``{"config", "error"}``，
    坏配置不中断整批（与 :func:`audit_configs` 的口径一致）。这是
    ``manifest a.json b.json ...`` 的唯一推荐入口，避免每份配置重扫整棵挂载树。
    """
    if isinstance(config_paths, (str, Path)):
        config_paths = [config_paths]
    root = _oss_root(oss_root)
    index = _build_oss_index(root)
    manifests: list[dict] = []
    errors: list[dict] = []
    for cp in config_paths:
        try:
            manifests.append(
                _manifest(cp, root, disk_root, index, source_map))
        except Exception as e:              # 坏配置只记错误
            errors.append({"config": str(_abs(cp)), "error": str(e)})
    return manifests, errors


def _manifest(config_path: str | Path, root: Path,
              disk_root: str | Path | None,
              index: dict[int, list[dict]],
              source_map: dict | None = None) -> dict:
    """内部实现：用已建好的索引生成清单。"""
    config_abs = _abs(config_path)
    segments: list[dict] = []
    for i, seg in enumerate(_load_segments(config_abs)):
        entry = _segment_entry(
            seg, i, root,
            _abs(disk_root) if disk_root is not None else None,
            index, source_map)
        if entry is not None:
            segments.append(entry)
    return {
        "version": MANIFEST_VERSION,
        "config": str(config_abs),
        "generated_from": {
            "config": str(config_abs),
            "oss_root": str(root),
            "disk_root": (str(_abs(disk_root))
                          if disk_root is not None else None),
            "generated_at": _utc_now(),
        },
        "segments": segments,
    }


def audit_configs(config_paths: Sequence[str | Path],
                  oss_root: str | Path,
                  disk_root: str | Path | None = None,
                  source_map: dict | None = None) -> dict:
    """一批配置的只读审计。

    返回 ``{configs, refs, full_mapped, trimmed, missing, local_missing,
    failed_configs, details}``；``details[]`` 每项 ``{config, ok, refs,
    full_mapped, trimmed, missing, local_missing, error, segments}``。
    坏配置只记 ``error``、计入 ``failed_configs``，不中断其它配置。

    计数口径（严格按 SPEC）：

    - ``missing``：**所有**非裁尾且 ``oss`` 为空的段——包括本地文件不可用
      （``bytes < 0``）的段。素材盘没挂载时它会等于本地缺失数，审计不会在
      「什么都没映射」的情况下报 PASS。
    - ``local_missing``：任意段 ``bytes < 0``（本地素材盘不可用 / 文件缺失，
      含裁尾段），作为独立诊断保留。
    - ``details[].ok`` 仅在配置可读、``missing == 0`` **且**
      ``local_missing == 0`` 时为真（严格门禁）。只读的计算 / 审核节点应显式
      使用 ``--report-only``，而不是让 ``ok`` 静默为真。
    """
    if isinstance(config_paths, (str, Path)):
        config_paths = [config_paths]
    root = _oss_root(oss_root)
    index = _build_oss_index(root)

    totals = {"configs": 0, "refs": 0, "full_mapped": 0, "trimmed": 0,
              "missing": 0, "local_missing": 0, "failed_configs": 0}
    details: list[dict] = []
    for cp in config_paths:
        totals["configs"] += 1
        detail: dict[str, Any] = {
            "config": str(_abs(cp)),
            "ok": False,
            "refs": 0,
            "full_mapped": 0,
            "trimmed": 0,
            "missing": 0,
            "local_missing": 0,
            "error": None,
            "segments": [],
        }
        try:
            manifest = _manifest(cp, root, disk_root, index, source_map)
        except Exception as e:                      # 坏配置不拖垮整批
            detail["error"] = str(e)
            totals["failed_configs"] += 1
            details.append(detail)
            continue

        segs = manifest["segments"]
        detail["segments"] = segs
        counts = count_segments(segs)
        detail.update(counts)
        detail["ok"] = (counts["missing"] == 0
                        and counts["local_missing"] == 0)
        for key in ("refs", "full_mapped", "trimmed", "missing",
                    "local_missing"):
            totals[key] += detail[key]
        details.append(detail)

    return {
        "configs": totals["configs"],
        "refs": totals["refs"],
        "full_mapped": totals["full_mapped"],
        "trimmed": totals["trimmed"],
        "missing": totals["missing"],
        "local_missing": totals["local_missing"],
        "failed_configs": totals["failed_configs"],
        "details": details,
    }
