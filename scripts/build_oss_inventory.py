#!/usr/bin/env python3
"""生成 22 份配置的真实 P0 OSS 映射清单与审计交付物（**幂等**）。

只做三件事：

1. 按批次给两个**事件根**分别建索引（全球车 16 份 → 0930 事件根，50 个可用
   视频；10-19 6 份 → 10.19 事件根，30 个），调用
   :func:`livepipe.tools.oss_ingest.manifest.build_config_manifests`，每份配置
   的映射结果写成 ``manifests/<config stem>.json``。绝不把两批对象混进同一个
   候选池，避免跨事件同字节误配；两个根相同或互相包含时直接退出 2。
2. 汇总出 ``index.json`` / ``audit.json`` / ``summary.json`` / ``p1-upload-list.csv``。
3. 对 27 个裁尾段做**只读**的源对象分析（按 ``_live`` 文件名里的时间区间匹配 OSS
   文件名），结果写进 ``summary.json`` 的 ``trim_source_analysis``；不臆造
   ``source_oss``，也不上传任何文件。

幂等约定：所有输出写同一个固定 ``generated_at``（默认 ``2026-11-17T00:00:00Z``，
可用 ``--generated-at`` 覆盖），文件顺序、JSON 键顺序、CSV 行顺序都由输入排序
决定；同一批输入重复运行，输出字节级一致。

阶段判定：数字整组吻合 P0 基线（53 全片 / 2 缺失 / 29 行清单）或 P1 基线
（55 全片 / 0 缺失 / 27 行清单）都返回 0，``acceptance.phase`` 与 ``state``
自动切换；补传后再加 ``--verify-uploads`` 会按 CSV 的 ``dst`` 核对字节。

用法::

    # 默认就是那 22 份配置 + 两个事件根
    python3 scripts/build_oss_inventory.py

    # 指定配置（glob 或显式路径均可）与输出目录
    python3 scripts/build_oss_inventory.py \
        --configs '10-19奇瑞创新大会_*.json' \
        --out-dir /tmp/oss-inventory

    # 显式换事件根 / 固定生成时间
    python3 scripts/build_oss_inventory.py \
        --oss-root-global '~/oss-bucket/直播data/录屏/2026-0930-1006《全球车的中国路》' \
        --oss-root-1019   '~/oss-bucket/直播data/录屏/10.19' \
        --generated-at '2026-11-17T00:00:00Z'

退出码：0 = 成功且基线数字全部吻合；1 = 基线不一致或存在坏配置；2 = 参数 / 输入错误。
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

# 允许从任意工作目录运行：把仓库根放进 sys.path，再导入项目模块。
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from livepipe.tools.oss_ingest import manifest as M  # noqa: E402
from livepipe.tools.oss_ingest.common import (  # noqa: E402
    MANIFEST_VERSION,
    human_bytes,
    is_temp_name,
    iter_files,
    probe_duration,
    rel_to,
)

#: 默认输出目录（仓库根 ``out/`` 已 gitignore）。
DEFAULT_OUT_DIR = _REPO_ROOT / "out" / "oss_inventory" / "2026-11-17"

#: 默认的 22 份配置：按事件分组写 glob，避免误扫仓库根其它 JSON。
DEFAULT_CONFIG_GLOBS = (
    "09-30_全球车的中国路_*.json",
    "10-01_全球车的中国路_*.json",
    "10-02_全球车的中国路_*.json",
    "10-03_全球车的中国路_*.json",
    "10-04_全球车的中国路_*.json",
    "10-06_全球车的中国路_*.json",
    "10-19奇瑞创新大会_*.json",
)

#: 两批配置各自的 OSS 索引根（显式传给工具，不依赖模块默认值）。
#: 映射按字节大小在索引里找候选，必须限制在事件内，避免跨事件同字节误配。
DEFAULT_GLOBAL_OSS_ROOT = "~/oss-bucket/直播data/录屏/2026-0930-1006《全球车的中国路》"
DEFAULT_1019_OSS_ROOT = "~/oss-bucket/直播data/录屏/10.19"

#: 两个事件根的父目录，只用于拼 P1 目标路径（不参与索引）。
OSS_PARENT_DISPLAY = "~/oss-bucket/直播data/录屏"

#: 两批事件在公共 OSS 根下的目录名（用于裁尾分析时缩小搜索范围）。
GLOBAL_EVENT_DIR = "2026-0930-1006《全球车的中国路》"
EVENT_1019_DIR = "10.19"

#: 两批素材在本机的原始挂载根，只用于把缺失段还原成「OSS 相对路径」。
DEFAULT_GLOBAL_LOCAL_ROOT = "/run/media/puyu/MyDisk/2026-0930-1006《全球车的中国路》"
DEFAULT_1019_LOCAL_ROOT = "/run/media/puyu/MyDisk/10.19"

#: P1 契约里新录屏 / 裁尾副本的父目录（见 livepipe/docs/oss-migration.html）。
P1_SCREENSHOT_ROOT = "~/oss-bucket/web/1/奇瑞汽车/直播看管/直播录屏"

#: 输出里的固定生成时间；可用 ``--generated-at`` 覆盖（默认值保证幂等）。
DEFAULT_GENERATED_AT = "2026-11-17T00:00:00Z"

#: 与 manifest 模块一致的视频扩展名（用于统计索引文件数 / 分析 OSS 文件名）。
_VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".flv", ".ts", ".m4v")

#: P0 基线（补传前）。只有解析出的配置集恰好就是默认 22 份时才做严格校验。
EXPECTED = {
    "configs": 22,
    "refs": 82,
    "full_mapped": 53,
    "trimmed": 27,
    "missing": 2,
    "local_missing": 0,
    "failed_configs": 0,
}

#: P1 补传后的基线：2 段缺失整段落盘后，全片 55、缺失 0，裁尾仍是 27。
P1_EXPECTED = {**EXPECTED, "full_mapped": 55, "missing": 0}

#: P0 的 P1 清单 = 2 个缺失整段 + 27 个裁尾副本。
EXPECTED_P1_ROWS = 29

#: P0 时 29 个待补传对象的实测总字节（2 整段 + 27 裁尾）。
EXPECTED_P1_BYTES = 46_520_729_506

#: P1 完成后的清单只剩 27 个裁尾副本，合计 40,069,546,585 B。
EXPECTED_P1_DONE_ROWS = 27
EXPECTED_P1_DONE_BYTES = 40_069_546_585

#: P1 上传清单的列（顺序即 CSV 表头）。
CSV_HEADER = ["config", "seg", "kind", "src", "src_abs", "dst", "bytes",
              "duration_s", "trim_s", "note"]

#: OSS 文件名里的时间区间：``9： 01-10： 10`` / ``12：43-14：00`` / ``15：20~15：20``。
_OSS_TIME_RE = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{1,2})-(\d{1,2})$")

#: 全球车 ``_live`` 名：``930_kuaishou_9-29-12-35_0-6885``。
_TRIM_NAME_RE = re.compile(
    r"^(?P<date>\d{3,4})_(?P<plat>[a-z]+)_"
    r"(?P<time>\d{1,2}-\d{1,2}-\d{1,2}-\d{1,2})_(?P<start>\d+)-(?P<end>\d+)$")

#: 10-19 ``media`` 名：``dy_qq_16-31-17-00_live``。
_MEDIA_TRIM_RE = re.compile(
    r"^(?P<plat>dy|wx)_(?P<acct>[a-z]+)_"
    r"(?P<time>\d{1,2}-\d{1,2}-\d{1,2}-\d{1,2})_live$")

#: ``_live`` 前缀里的平台缩写 → OSS 目录名（仅用于展示 / 人工核对）。
_PLATFORM_DIRS = {
    "kuaishou": "快手",
    "douyin": "抖音",
    "wechat": "视频号",
    "dy": "抖音",
    "wx": "视频号",
}

#: 分析报告里优先展示的 3 个典型例子（按 ``_live`` 文件名前缀匹配）。
_PREFERRED_EXAMPLES = (
    "1001_wechat_12-43-14-00",   # 能唯一对上 OSS 源对象
    "930_kuaishou_9-29-12-35",   # OSS 拆分 / 仅部分覆盖
    "930_douyin_14-54-15-38",    # OSS 源缺失
)


class InventoryError(Exception):
    """输入 / 参数层面的可预期错误。"""


# --------------------------------------------------------------------------- #
# 通用小工具
# --------------------------------------------------------------------------- #
def _now() -> float:
    return time.perf_counter()


def _fmt_elapsed(seconds: float) -> str:
    return f"{seconds:.1f}s"


def _decimal_gb(num: int) -> float:
    """字节 → 十进制 GB（1 GB = 1e9 B），与文档里的「37.9GB」口径一致。"""
    return round(num / 1_000_000_000, 2)


def _gib(num: int) -> float:
    """字节 → GiB（1 GiB = 1024³ B），与 ``human_bytes`` 同口径。"""
    return round(num / (1024 ** 3), 2)


def _fmt_float(value: object) -> str:
    """CSV 里的浮点：整数去小数点，其余保留 3 位；``None`` → 空串。"""
    if value is None:
        return ""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return ""
    if num.is_integer():
        return str(int(num))
    return f"{num:.3f}"


def _fmt_range(value: object) -> str:
    """``[0.0, 6885.0]`` → ``0-6885``；缺区间 → 空串。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return ""
    return f"{_fmt_float(value[0])}-{_fmt_float(value[1])}"


def _write_json(path: Path, payload: object) -> None:
    """原子写 JSON（同目录 ``.tmp`` → fsync → replace），UTF-8 不转义中文。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _write_csv(path: Path, rows: list[list[object]], header: list[str]) -> None:
    """原子写 CSV；``utf-8-sig`` 便于 Excel 直接打开中文列。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _basename(path: str | Path) -> str:
    return Path(str(path)).name


def _abs_str(path: str | Path) -> str:
    return os.path.abspath(str(Path(str(path)).expanduser()))


# --------------------------------------------------------------------------- #
# 配置解析与分组
# --------------------------------------------------------------------------- #
def default_patterns() -> tuple[str, ...]:
    """默认 glob 锚定仓库根，保证从任意 cwd 运行都能找到那 22 份配置。"""
    return tuple(str(_REPO_ROOT / pattern) for pattern in DEFAULT_CONFIG_GLOBS)


def resolve_configs(patterns: tuple[str, ...] | list[str]) -> list[str]:
    """展开 glob / 显式路径，去重后按文件名排序。

    显式路径必须存在；glob 零命中直接报错（避免悄悄生成一份空清单）。
    """
    found: list[str] = []
    for raw in patterns:
        if not isinstance(raw, str) or not raw:
            raise InventoryError(f"空的配置参数：{raw!r}")
        item = os.path.expanduser(raw)
        if any(ch in item for ch in "*?["):
            hits = sorted(glob.glob(item))
            if not hits:
                raise InventoryError(f"glob 没有命中任何配置：{raw}")
            found.extend(hits)
        else:
            path = Path(item)
            if not path.is_file():
                raise InventoryError(f"配置文件不存在：{raw}")
            found.append(str(path))

    # 按绝对路径去重，再按文件名排序，保证输出顺序与调用顺序无关。
    unique: dict[str, str] = {}
    for path in found:
        unique[_abs_str(path)] = _abs_str(path)
    return sorted(unique.values(), key=_basename)


def group_of(config_path: str | Path) -> str:
    """配置 → 批次名：10-19 归 ``1019``，其余归 ``global``。"""
    return "1019" if Path(str(config_path)).name.startswith("10-19") else "global"


def verify_upload_rows(rows: list[list[object]],
                       header: list[str] = CSV_HEADER) -> dict:
    """按 CSV 行核对 dst 是否已落盘且字节一致（P1 后验收用）。"""
    result = {"checked": len(rows), "present": 0,
              "missing": [], "size_mismatch": []}
    for row in rows:
        rec = dict(zip(header, row))
        dst = Path(str(rec["dst"])).expanduser()
        try:
            size = dst.stat().st_size if dst.is_file() else -1
        except OSError:
            size = -1
        if size < 0:
            result["missing"].append(str(dst))
        elif size != int(rec["bytes"]):
            result["size_mismatch"].append(
                {"dst": str(dst), "expect": int(rec["bytes"]),
                 "actual": size})
        else:
            result["present"] += 1
    return result


def detect_phase(totals: dict, row_count: int, p1_bytes: int) -> str | None:
    """按数字判定当前处于 P0（待补传）还是 P1（补传完成）。

    两套基线都必须整组吻合才算命中：只看 full_mapped 会把「补了 2 段、
    但裁尾数字也变了」的异常状态误判成 P1。
    """
    if (totals == EXPECTED and row_count == EXPECTED_P1_ROWS
            and p1_bytes == EXPECTED_P1_BYTES):
        return "p0"
    if (totals == P1_EXPECTED and row_count == EXPECTED_P1_DONE_ROWS
            and p1_bytes == EXPECTED_P1_DONE_BYTES):
        return "p1"
    return None


def build_groups(args: argparse.Namespace) -> dict[str, dict]:
    """两个批次的 OSS 索引根 / 本地根 / 事件目录 / P1 目标父目录。

    每个事件根单独建索引（global 51 个视频、1019 30 个），绝不把两批对象
    混进同一个候选池；P1 目标路径仍用父目录 + 事件目录拼。
    """
    return {
        "global": {
            "oss_root": Path(args.oss_root_global).expanduser(),
            "display": (DEFAULT_GLOBAL_OSS_ROOT
                        if args.oss_root_global == DEFAULT_GLOBAL_OSS_ROOT
                        else str(args.oss_root_global)),
            "parent_display": OSS_PARENT_DISPLAY,
            "event_dir": GLOBAL_EVENT_DIR,
            "local_root": Path(DEFAULT_GLOBAL_LOCAL_ROOT).expanduser(),
            "event": GLOBAL_EVENT_DIR,
        },
        "1019": {
            "oss_root": Path(args.oss_root_1019).expanduser(),
            "display": (DEFAULT_1019_OSS_ROOT
                        if args.oss_root_1019 == DEFAULT_1019_OSS_ROOT
                        else str(args.oss_root_1019)),
            "parent_display": OSS_PARENT_DISPLAY,
            "event_dir": EVENT_1019_DIR,
            "local_root": Path(DEFAULT_1019_LOCAL_ROOT).expanduser(),
            "event": "2026-10-19奇瑞全球创新大会",
        },
    }


# --------------------------------------------------------------------------- #
# 裁尾源分析（只读，不写 source_oss）
# --------------------------------------------------------------------------- #
def _date_dir(mmdd: str) -> str:
    """``930`` → ``9.30``；``1001`` → ``10.01``；``1019`` → ``10.19``。"""
    if mmdd == "930":
        return "9.30"
    if len(mmdd) == 4:
        return f"{mmdd[:2]}.{mmdd[2:]}"
    raise InventoryError(f"无法识别的日期前缀：{mmdd}")


def _normalize_time_name(stem: str) -> tuple[int, int, int, int] | None:
    """把 OSS 文件名主干里的时间区间归一成 4 元组；不是时间名 → ``None``。

    忽略空格、全角冒号、全角波浪号：``9： 01-10： 10`` → ``(9,1,10,10)``。
    """
    text = (stem.replace(" ", "")
            .replace("：", "-")
            .replace(":", "-")
            .replace("~", "-"))
    match = _OSS_TIME_RE.match(text)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _parse_trim_name(stem: str) -> dict | None:
    """解析裁尾文件名里的时间区间。

    返回 ``{date_dir, platform, time, kind}``；``date_dir`` 对 10-19 media 固定为
    ``10.19``（该批没有日期层，分析时会改用整棵树）。
    """
    match = _TRIM_NAME_RE.match(stem)
    if match:
        return {
            "date_dir": _date_dir(match.group("date")),
            "platform": _PLATFORM_DIRS.get(match.group("plat"), match.group("plat")),
            "time": _normalize_time_name(match.group("time")),
            "kind": "live",
        }
    match = _MEDIA_TRIM_RE.match(stem)
    if match:
        return {
            "date_dir": "10.19",
            "platform": _PLATFORM_DIRS.get(match.group("plat"), match.group("plat")),
            "time": _normalize_time_name(match.group("time")),
            "kind": "media",
        }
    return None


def scan_oss_entries(root: Path) -> list[dict]:
    """扫一遍 OSS 根，收集**可用**视频对象的相对路径与归一化时间区间。

    与 ``manifest._build_oss_index`` 同一口径：跳过 0 字节文件与下载器临时名
    （``*.p.mp4`` / ``*.baiduyun*``），否则 ``indexed_videos`` 会比映射引擎
    多算半成品。
    """
    entries: list[dict] = []
    for path in iter_files(root):
        if path.suffix.lower() not in _VIDEO_EXTS:
            continue
        if is_temp_name(path.name):
            continue
        rel = rel_to(path, root)
        if rel == "..":
            continue
        try:
            size = path.stat().st_size if path.is_file() else -1
        except OSError:
            size = -1
        if size <= 0:
            continue
        entries.append({
            "rel": rel,
            "stem": path.stem,
            "time": _normalize_time_name(path.stem),
            "bytes": size,
        })
    return entries


def _range_contains(outer: tuple[int, int, int, int],
                    inner: tuple[int, int, int, int]) -> bool:
    """时间区间是否重叠（分钟精度足够，用于区分「拆分」与「源缺失」）。"""
    os_, om, oe, omi = outer
    is_, im, ie, imi = inner

    def minutes(h: int, m: int) -> int:
        return h * 60 + m

    return (minutes(os_, om) < minutes(ie, imi)
            and minutes(is_, im) < minutes(oe, omi))


def analyze_trim(seg: dict, group: dict, entries: list[dict],
                 config_path: str) -> dict:
    """一个裁尾段 → ``trim_source_analysis`` 条目。

    匹配规则：只看 OSS **文件名**里的 ``<开始>-<结束>`` 区间，与 ``_live`` 名里的
    区间逐字段相等才算命中；先找同平台目录，同平台没有精确命中时才用「其它
    平台目录的唯一精确命中」兜底（OSS 目录名可能与画面平台错位）。同区间多个
    对象 → ``ambiguous``。绝不用字节数 / 时长去「猜」源对象。
    """
    disk_name = _basename(seg["disk"])
    stem = Path(disk_name).stem
    parsed = _parse_trim_name(stem)

    item: dict = {
        "config": config_path,
        "seg": seg["seg"],
        "disk": seg["disk"],
        "disk_name": disk_name,
        "bytes": seg.get("bytes"),
        "duration_s": seg.get("duration_s"),
        "trim_s": seg.get("trim_s"),
        "platform": None,
        "time_range": None,
        "match": "unknown",
        "matched_oss": None,
        "source_match": None,
        "candidates": [],
        "reason": "",
        "platform_dir": None,
        "cross_platform": False,
    }

    if not parsed or parsed["time"] is None:
        item["match"] = "not_matched"
        item["reason"] = "not_time_named"
        item["reason_text"] = "文件名里没有可解析的 <开始>-<结束> 时间区间"
        return item

    time_range = parsed["time"]
    item["platform"] = parsed["platform"]
    item["platform_dir"] = parsed["platform"]
    item["time_range"] = (f"{time_range[0]}:{time_range[1]:02d}-"
                          f"{time_range[2]}:{time_range[3]:02d}")

    if parsed["kind"] == "media":
        # 10-19 的 6 个 media 裁尾：源是原始 SVID 对象，OSS 上存在但不按时间命名。
        item["match"] = "not_matched"
        item["reason"] = "media_raw_source"
        item["reason_text"] = (
            "10-19 media 裁尾；OSS 侧是未按时间命名的原始 SVID 对象，"
            "按时间区间无法自动匹配")
        return item

    # entries 已经按事件根扫描；0930 再按 MM.DD 分日期缩小范围，
    # 10.19 直接是 平台/账号。避免跨天同名区间误配。
    if parsed["kind"] == "live":
        prefix = f"{parsed['date_dir']}/"
    else:
        prefix = ""
    pool = [e for e in entries if e["rel"].startswith(prefix)]

    # 两级匹配：先看同平台目录（OSS 目录名是下载时的目录，可能和画面平台错位），
    # 同平台没有精确命中时，才允许「其它平台目录里的唯一精确命中」兜底。
    # 重叠 / 起止边界这类弱证据只在同平台目录里用，避免 9.30 抖音裁尾被
    # 9.30 快手的重叠对象误导。
    def _platform_of(entry: dict) -> str | None:
        parts = entry["rel"][len(prefix):].split("/")
        return parts[0] if len(parts) > 1 else None

    same_platform = [e for e in pool
                     if _platform_of(e) == parsed["platform"]]
    item["platform_dir"] = parsed["platform"]
    item["cross_platform"] = False

    exact_same = [e for e in same_platform if e["time"] == time_range]
    exact_any = [e for e in pool if e["time"] == time_range]

    if len(exact_same) == 1:
        item["match"] = "unique_time_match"
        item["matched_oss"] = exact_same[0]["rel"]
        item["source_match"] = exact_same[0]["rel"]
        item["reason"] = "unique_time_match"
        item["reason_text"] = "文件名时间区间在同平台目录唯一命中"
        return item
    if len(exact_same) > 1:
        item["match"] = "ambiguous"
        item["candidates"] = [e["rel"] for e in exact_same]
        item["reason"] = "ambiguous"
        item["reason_text"] = (
            f"同平台目录里同区间命中 {len(exact_same)} 个 OSS 对象，无法唯一确定")
        return item
    if len(exact_any) == 1:
        item["match"] = "unique_time_match"
        item["matched_oss"] = exact_any[0]["rel"]
        item["source_match"] = exact_any[0]["rel"]
        item["cross_platform"] = True
        item["reason"] = "unique_time_match"
        item["reason_text"] = (
            "同平台目录无命中，但在同日期其它平台目录唯一命中"
            "（OSS 目录名与画面平台错位）")
        return item
    if len(exact_any) > 1:
        item["match"] = "ambiguous"
        item["candidates"] = [e["rel"] for e in exact_any]
        item["reason"] = "ambiguous"
        item["reason_text"] = (
            f"同日期目录里同区间命中 {len(exact_any)} 个 OSS 对象，无法唯一确定")
        return item

    # 没有精确命中：只在同平台目录里看重叠 / 起止边界。
    start = time_range[:2]
    end = time_range[2:]
    start_hits = [e for e in same_platform
                  if e["time"] and e["time"][:2] == start]
    end_hits = [e for e in same_platform
                if e["time"] and e["time"][2:] == end]
    overlap = [e for e in same_platform
               if e["time"] and _range_contains(e["time"], time_range)]

    if start_hits:
        item["match"] = "not_matched"
        item["reason"] = "split_or_partial"
        item["candidates"] = [e["rel"] for e in start_hits]
        item["reason_text"] = (
            "起点能在 OSS 找到对象，但区间被拆分 / 仅部分覆盖，没有单一源对象")
    elif overlap:
        item["match"] = "not_matched"
        item["reason"] = "overlap_not_exact"
        item["candidates"] = [e["rel"] for e in overlap]
        item["reason_text"] = (
            "OSS 有重叠对象，但起止时间与 _live 名不一致，不能唯一确定")
    elif end_hits:
        item["match"] = "not_matched"
        item["reason"] = "split_or_partial"
        item["candidates"] = [e["rel"] for e in end_hits]
        item["reason_text"] = (
            "终点能接上 OSS 对象，但起点没有对应对象，源不完整")
    else:
        item["match"] = "not_matched"
        item["reason"] = "source_missing"
        item["reason_text"] = "OSS 同日期目录里没有同名区间、也没有重叠对象"
    return item


# --------------------------------------------------------------------------- #
# 审计 / 汇总
# --------------------------------------------------------------------------- #
def audit_segments(segments: list[dict]) -> dict:
    """按 manifest 模块 ``audit_configs`` 的同一口径计数。"""
    def _bytes(seg: dict) -> int:
        try:
            return int(seg.get("bytes", -1))
        except (TypeError, ValueError):
            return -1

    return {
        "refs": len(segments),
        "full_mapped": sum(
            1 for s in segments if not s["trimmed"] and s["oss"]),
        "trimmed": sum(1 for s in segments if s["trimmed"]),
        "missing": sum(
            1 for s in segments if not s["trimmed"] and not s["oss"]),
        "local_missing": sum(1 for s in segments if _bytes(s) < 0),
    }


def byte_totals(segments: list[dict]) -> dict:
    """整段已映射 / 裁尾 / 缺失三类的字节合计（``bytes<0`` 不计入）。"""
    full = trimmed = missing = 0
    for seg in segments:
        try:
            size = int(seg.get("bytes", -1))
        except (TypeError, ValueError):
            size = -1
        if size < 0:
            continue
        if seg["trimmed"]:
            trimmed += size
        elif seg["oss"]:
            full += size
        else:
            missing += size
    return {"full_mapped": full, "trimmed": trimmed, "missing": missing,
            "total": full + trimmed + missing}


def _suggested_full_target(seg: dict, group: dict) -> str:
    """缺失整段 → 补到存量批次事件目录下的原相对路径。"""
    disk = _abs_str(seg["disk"])
    rel = rel_to(disk, group["local_root"])
    if rel == "..":
        rel = _basename(disk)
    return (f"{group['parent_display'].rstrip('/')}/"
            f"{group['event_dir'].rstrip('/')}/{rel}")


def _suggested_trim_target(seg: dict, group: dict) -> str:
    """裁尾副本 → 迁移文档的 ``直播录屏/<事件>/_live/<文件名>`` 契约。"""
    return f"{P1_SCREENSHOT_ROOT}/{group['event']}/_live/{_basename(seg['disk'])}"


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="生成 22 份配置的真实 P0 OSS 映射清单与审计交付物")
    parser.add_argument(
        "--configs", nargs="+", default=None, metavar="GLOB_OR_PATH",
        help="配置 glob 或显式路径；省略时用默认 22 份")
    parser.add_argument(
        "--out-dir", default=str(DEFAULT_OUT_DIR), metavar="DIR",
        help=f"输出目录（默认 {DEFAULT_OUT_DIR}）")
    parser.add_argument(
        "--oss-root-global", default=DEFAULT_GLOBAL_OSS_ROOT, metavar="PATH",
        help="全球车批次的 OSS 索引根（默认 0930 事件根）")
    parser.add_argument(
        "--oss-root-1019", default=DEFAULT_1019_OSS_ROOT, metavar="PATH",
        help="10-19 批次的 OSS 索引根（默认 10.19 事件根）")
    parser.add_argument(
        "--generated-at", default=DEFAULT_GENERATED_AT, metavar="ISO8601",
        help=f"写进所有输出的固定生成时间（默认 {DEFAULT_GENERATED_AT}）")
    parser.add_argument(
        "--verify-uploads", action="store_true",
        help="P1 后核对清单里的 dst 是否已落盘且字节一致（默认关闭）")
    parser.add_argument(
        "--no-docs-summary", action="store_true",
        help="不写 livepipe/docs/oss-inventory-summary.json")
    return parser.parse_args(argv)


def _print_config_table(rows: list[dict]) -> None:
    print(f"{'config':<44}{'group':<8}{'refs':>5}{'mapped':>8}"
          f"{'trim':>6}{'miss':>6}{'bytes':>12}")
    for row in rows:
        print(f"{row['name']:<44}{row['group']:<8}{row['refs']:>5}"
              f"{row['full_mapped']:>8}{row['trimmed']:>6}"
              f"{row['missing']:>6}{human_bytes(row['bytes']['total']):>12}")


def run(args: argparse.Namespace) -> int:
    t_all = _now()
    out_dir = Path(args.out_dir).expanduser()
    out_dir = (_REPO_ROOT / out_dir if not out_dir.is_absolute() else out_dir)
    groups = build_groups(args)

    # 两个事件根必须互不包含：同一候选池会让两批同字节视频互相误配。
    root_g = groups["global"]["oss_root"].resolve()
    root_1019 = groups["1019"]["oss_root"].resolve()
    if (root_g == root_1019 or root_g in root_1019.parents
            or root_1019 in root_g.parents):
        print("[error] --oss-root-global 与 --oss-root-1019 不能相同或互相包含："
              f"{root_g} / {root_1019}；请分别传两个事件根，"
              "避免跨事件同字节误配。", file=sys.stderr)
        return 2

    # 1) 解析配置集
    started = _now()
    patterns = (tuple(args.configs) if args.configs
                else default_patterns())
    try:
        configs = resolve_configs(patterns)
        default_configs = resolve_configs(default_patterns())
    except InventoryError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2
    is_default_set = configs == default_configs

    by_group: dict[str, list[str]] = {"global": [], "1019": []}
    for config in configs:
        by_group[group_of(config)].append(config)
    print(f"[1/6] 解析配置：{len(configs)} 份"
          f"（global {len(by_group['global'])} / 1019 {len(by_group['1019'])}）"
          f" · {_fmt_elapsed(_now() - started)}")

    # 2) 两个事件根各自建索引 + 生成 manifest（绝不把两批对象混进同一候选池）
    manifests: list[dict] = []
    manifest_group: dict[str, str] = {}
    errors: list[dict] = []
    oss_entries: dict[str, list[dict]] = {}
    coverage: dict[str, dict] = {}

    for step, name in enumerate(("global", "1019"), start=2):
        configs_in_group = by_group[name]
        group = groups[name]
        if not configs_in_group:
            coverage[name] = {"oss_root": str(group["oss_root"]),
                              "indexed_videos": 0, "configs": 0}
            oss_entries[name] = []
            continue
        started = _now()
        built, batch_errors = M.build_config_manifests(
            configs_in_group, group["oss_root"])
        elapsed = _now() - started
        errors.extend(batch_errors)
        for manifest in built:
            # 写入固定生成时间，保证重复运行字节一致。
            generated_from = manifest.setdefault("generated_from", {})
            generated_from["generated_at"] = args.generated_at
            manifests.append(manifest)
            manifest_group[_abs_str(manifest["config"])] = name
        entries = scan_oss_entries(group["oss_root"])
        oss_entries[name] = entries
        indexed = sum(1 for e in entries if e["bytes"] >= 0)
        coverage[name] = {
            "oss_root": str(group["oss_root"]),
            "indexed_videos": indexed,
            "configs": len(configs_in_group),
        }
        print(f"[{step}/6] {name} 事件根：{group['oss_root']} · "
              f"配置 {len(configs_in_group)} 份 · 索引视频 {indexed} 个 · "
              f"{_fmt_elapsed(elapsed)}")
        for err in batch_errors:
            print(f"[error] {err['config']}：{err['error']}", file=sys.stderr)

    coverage["total_indexed_videos"] = sum(
        item["indexed_videos"] for item in coverage.values())

    manifests.sort(key=lambda m: _basename(m["config"]))

    # 3) 写每份配置的 manifest
    started = _now()
    manifest_dir = out_dir / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    for manifest in manifests:
        _write_json(manifest_dir / f"{Path(manifest['config']).stem}.json",
                    manifest)
    print(f"[4/6] 写 {len(manifests)} 份 manifest → {manifest_dir}"
          f" · {_fmt_elapsed(_now() - started)}")

    # 4) 逐段汇总 / 裁尾分析 / CSV
    started = _now()
    details: list[dict] = []
    per_config: list[dict] = []
    missing_segments: list[dict] = []
    trim_items: list[dict] = []
    totals = {"configs": len(configs), "refs": 0, "full_mapped": 0,
              "trimmed": 0, "missing": 0, "local_missing": 0,
              "failed_configs": 0}
    bytes_total = {"full_mapped": 0, "trimmed": 0, "missing": 0, "total": 0}

    # 先按配置收集裁尾段，再做一次性的 OSS 文件名分析。
    config_segments: list[tuple[dict, list[dict]]] = []

    for manifest in manifests:
        config_abs = _abs_str(manifest["config"])
        group_name = manifest_group.get(config_abs, "global")
        group = groups[group_name]
        segments = manifest.get("segments") or []
        counted = audit_segments(segments)
        counts = byte_totals(segments)

        for key in ("refs", "full_mapped", "trimmed", "missing",
                    "local_missing"):
            totals[key] += counted[key]
        for key in bytes_total:
            bytes_total[key] += counts[key]

        detail = {
            "config": config_abs,
            "group": group_name,
            "oss_root": str(group["oss_root"]),
            "ok": counted["missing"] == 0 and counted["local_missing"] == 0,
            "error": None,
            **counted,
            "bytes": counts,
            "segments": segments,
        }
        details.append(detail)
        per_config.append({
            "name": _basename(config_abs),
            "config": config_abs,
            "group": group_name,
            **counted,
            "bytes": counts,
        })
        config_segments.append((detail, segments))

        for seg in segments:
            if seg["trimmed"]:
                continue
            if not seg["oss"]:
                # 工具对「有字节但 OSS 无候选」的段提前返回、不探时长；
                # 这里补一次本地探测，让上传清单的 duration_s 可用。
                duration = seg.get("duration_s")
                if duration is None:
                    duration = probe_duration(seg["disk"])
                missing_segments.append({
                    "config": config_abs,
                    "name": _basename(config_abs),
                    "group": group_name,
                    "seg": seg["seg"],
                    "disk": seg["disk"],
                    "bytes": seg["bytes"],
                    "duration_s": duration,
                    "suggested_oss_target": _suggested_full_target(seg, group),
                })

    for err in errors:
        totals["failed_configs"] += 1
        details.append({
            "config": _abs_str(err["config"]),
            "group": group_of(err["config"]),
            "ok": False,
            "error": err["error"],
            "refs": 0, "full_mapped": 0, "trimmed": 0,
            "missing": 0, "local_missing": 0,
            "bytes": {"full_mapped": 0, "trimmed": 0, "missing": 0,
                      "total": 0},
            "segments": [],
        })

    # 裁尾源分析：按批次复用同一份 OSS 文件名清单。
    for detail, segments in config_segments:
        group = groups[detail["group"]]
        pool = oss_entries.get(detail["group"], [])
        for seg in segments:
            if seg["trimmed"]:
                item = analyze_trim(seg, group, pool, detail["config"])
                item["group"] = detail["group"]
                trim_items.append(item)

    trim_items.sort(key=lambda it: (_basename(it["config"]), it["seg"]))
    by_reason: dict[str, int] = {}
    for item in trim_items:
        by_reason[item["reason"]] = by_reason.get(item["reason"], 0) + 1
    unique_matches = sum(1 for it in trim_items
                         if it["match"] == "unique_time_match")
    ambiguous = sum(1 for it in trim_items if it["match"] == "ambiguous")
    not_matched = len(trim_items) - unique_matches - ambiguous

    typical = []
    for preferred in _PREFERRED_EXAMPLES:
        for item in trim_items:
            if item["disk_name"].startswith(preferred):
                typical.append(item)
                break
    if len(typical) < 3:
        for item in trim_items:
            if item not in typical:
                typical.append(item)
            if len(typical) == 3:
                break

    trim_analysis = {
        "rule": (
            "按 _live 文件名里的 <HH-MM>-<HH-MM> 区间，在同日期目录下匹配 OSS "
            "文件名区间（忽略空格、全角冒号与 ~）；先找同平台目录，同平台无"
            "精确命中时才用其它平台目录的唯一精确命中兜底（OSS 目录名可能与"
            "画面平台错位）；同区间多个对象记 ambiguous；绝不用字节数/时长猜源对象。"),
        "total_trimmed": len(trim_items),
        "unique_time_match": unique_matches,
        "ambiguous": ambiguous,
        "not_matched": not_matched,
        "matched_rate": (
            round(unique_matches / len(trim_items), 4) if trim_items else 0.0),
        "by_reason": by_reason,
        "trimmed_bytes_split": {
            "live": sum(
                int(it["bytes"]) for it in trim_items
                if it["reason"] != "media_raw_source"
                and isinstance(it["bytes"], int) and it["bytes"] >= 0),
            "media": sum(
                int(it["bytes"]) for it in trim_items
                if it["reason"] == "media_raw_source"
                and isinstance(it["bytes"], int) and it["bytes"] >= 0),
        },
        "media_note": (
            "6 个 10-19 media 裁尾（dy_*/wx_*_live.mp4）的源是原始 SVID 对象，"
            "OSS 上存在但文件名不含时间区间，因此按本规则计为不可自动匹配；"
            "对应关系见各配置注释。"),
        "typical_examples": [
            {
                "disk_name": it["disk_name"],
                "config": _basename(it["config"]),
                "time_range": it["time_range"],
                "match": it["match"],
                "matched_oss": it["matched_oss"],
                "source_match": it["source_match"],
                "candidates": it["candidates"],
                "reason": it["reason"],
                "reason_text": it["reason_text"],
            }
            for it in typical
        ],
        "recommendation": (
            "P0 不填 source_oss（27 段 source_oss 全为 null）；"
            f"P1 直接补传 27 个裁尾副本（合计 "
            f"{_decimal_gb(bytes_total['trimmed'])} GB 十进制 / "
            f"{human_bytes(bytes_total['trimmed'])}，"
            f"{bytes_total['trimmed']} 字节），不必等按区间回溯源对象。"),
        "items": trim_items,
    }

    # P1 上传清单：29 行 = 2 个缺失整段（kind=full）+ 27 个裁尾副本（kind=trim）。
    # 列按“上传者先看哪条、传去哪、为什么”排：config/seg/kind → src/src_abs →
    # dst → bytes/duration/trim → note。
    csv_rows: list[list[object]] = []
    for miss in missing_segments:
        csv_rows.append([
            miss["name"],
            miss["seg"],
            "full",
            miss["disk"],                    # src：本地源文件（可能带 ~）
            _abs_str(miss["disk"]),          # src_abs：展开后的绝对路径
            miss["suggested_oss_target"],    # dst：存量批次的源相对路径
            miss["bytes"],
            _fmt_float(miss.get("duration_s")),
            "",                              # 整段没有 trim 区间
            "整段缺失：补到旧存根对应事件目录，需重扫",
        ])
    for item in trim_items:
        csv_rows.append([
            _basename(item["config"]),
            item["seg"],
            "trim",
            item["disk"],                    # src：本地裁尾副本
            _abs_str(item["disk"]),
            _suggested_trim_target(
                {"disk": item["disk"]}, groups[item["group"]]),
            item["bytes"],
            _fmt_float(item.get("duration_s")),
            _fmt_range(item.get("trim_s")),
            "裁尾副本：直传新契约根，线上直接可播",
        ])

    # P1 落盘核对（默认关闭）：CSV 的 dst 是否已存在且字节一致。
    uploads = verify_upload_rows(csv_rows) if args.verify_uploads else None

    # 5) 写汇总文件
    totals["failed_configs"] = len(errors)
    # 失败配置的 refs 等不参与上面累加；总数保持工具口径。
    index_payload = {
        "version": MANIFEST_VERSION,
        "generated_at": args.generated_at,
        "generated_by": "scripts/build_oss_inventory.py",
        "oss_roots": {
            name: {
                "path": str(groups[name]["oss_root"]),
                "display": groups[name]["display"],
                "indexed_videos": coverage[name]["indexed_videos"],
                "configs": coverage[name]["configs"],
            }
            for name in ("global", "1019")
        },
        "configs": [_abs_str(c) for c in configs],
        "manifests": manifests,
    }
    audit_payload = {
        "version": MANIFEST_VERSION,
        "generated_at": args.generated_at,
        "totals": {k: totals[k] for k in EXPECTED},
        "details": details,
    }
    doc_discrepancy = {
        "document": "livepipe/docs/oss-migration.html",
        "claimed": {
            "full_mapped": 55,
            "trimmed_bytes": 37_900_000_000,
            "trimmed_bytes_text": "37.9 GB",
            "trimmed_bytes_gb_decimal": 37.9,
            "note": "文档写「55 段整段 1:1 命中 / 27 段裁尾待补 37.9GB」",
        },
        "measured": {
            "full_mapped": totals["full_mapped"],
            "missing_full": totals["missing"],
            "trimmed": totals["trimmed"],
            "trimmed_bytes": bytes_total["trimmed"],
            "trimmed_bytes_gb_decimal": _decimal_gb(bytes_total["trimmed"]),
            "trimmed_bytes_gib": _gib(bytes_total["trimmed"]),
            "trimmed_bytes_human": human_bytes(bytes_total["trimmed"]),
        },
        "explanation": (
            "82 段里 27 段是裁尾；剩下 55 段中有 2 段在 OSS 上没有同字节对象"
            "（09-30 抖音 seg1/seg3），所以整段命中是 53 而不是 55。"
            "27 个裁尾副本实测合计 40.07GB，不是文档里的 37.9GB。"
            "本清单以实测为准。"),
    }
    # 验收三口径：文档预期 / 本次实测 / P1 补传后应达到的状态。
    p1_bytes = bytes_total["trimmed"] + bytes_total["missing"]
    phase = detect_phase(totals, len(csv_rows), p1_bytes)
    state_text = {
        "p0": "P0 清单完成，待 P1 补传 2 段后 strict 复跑",
        "p1": "P1 补传完成：55 段整段全部落盘，27 段裁尾副本已在上传清单",
    }.get(phase, "自定义配置集或数字未匹配固定基线，不做阶段判定")
    acceptance = {
        "doc_expected": {"full_mapped": 55, "missing": 0, "trimmed": 27},
        "actual": {k: totals[k] for k in (
            "configs", "refs", "full_mapped", "trimmed", "missing",
            "local_missing", "failed_configs")},
        "after_backfill": {"full_mapped": 55, "missing": 0, "trimmed": 27},
        "phase": phase,
        "p1_objects": totals["trimmed"] + totals["missing"],
        "p1_bytes": p1_bytes,
        "p1_bytes_gib": _gib(p1_bytes),
        "state": state_text,
    }
    summary_payload = {
        "version": MANIFEST_VERSION,
        "generated_at": args.generated_at,
        "generated_by": "scripts/build_oss_inventory.py",
        "totals": {k: totals[k] for k in EXPECTED},
        "bytes": bytes_total,
        "bytes_human": {k: human_bytes(v) for k, v in bytes_total.items()},
        "bytes_gb_decimal": {
            k: _decimal_gb(v) for k, v in bytes_total.items()},
        "bytes_gib": {k: _gib(v) for k, v in bytes_total.items()},
        "acceptance": acceptance,
        "coverage": coverage,
        "per_config": per_config,
        "missing_segments": missing_segments,
        "trim_source_analysis": trim_analysis,
        "doc_discrepancy": doc_discrepancy,
        **({"uploads": uploads} if uploads is not None else {}),
    }

    _write_json(out_dir / "index.json", index_payload)
    _write_json(out_dir / "audit.json", audit_payload)
    _write_json(out_dir / "summary.json", summary_payload)
    _write_csv(
        out_dir / "p1-upload-list.csv", csv_rows,
        ["config", "seg", "kind", "src", "src_abs", "dst", "bytes",
         "duration_s", "trim_s", "note"])

    docs_summary = None
    if not args.no_docs_summary:
        docs_summary = _REPO_ROOT / "livepipe" / "docs" / "oss-inventory-summary.json"
        _write_json(docs_summary, {
            "version": MANIFEST_VERSION,
            "generated_at": args.generated_at,
            "generated_by": "scripts/build_oss_inventory.py",
            "totals": {k: totals[k] for k in EXPECTED},
            "bytes": bytes_total,
            "bytes_human": {k: human_bytes(v) for k, v in bytes_total.items()},
            "bytes_gb_decimal": {
                k: _decimal_gb(v) for k, v in bytes_total.items()},
            "bytes_gib": {k: _gib(v) for k, v in bytes_total.items()},
            "acceptance": acceptance,
            "per_config": per_config,
            "missing_segments": missing_segments,
            "trim_source_analysis": {
                key: value for key, value in trim_analysis.items()
                if key != "items"
            },
            "doc_discrepancy": doc_discrepancy,
            "full_report": "out/oss_inventory/2026-11-17/summary.json",
        })

    print(f"[5/6] 写 index/audit/summary/CSV"
          f"（CSV {len(csv_rows)} 行）"
          + (f" + {docs_summary}" if docs_summary else "")
          + f" · {_fmt_elapsed(_now() - started)}")

    # 6) 校验
    failures: list[str] = []
    expected_totals = P1_EXPECTED if phase == "p1" else EXPECTED
    want_rows = (EXPECTED_P1_DONE_ROWS if phase == "p1" else EXPECTED_P1_ROWS)
    want_bytes = (EXPECTED_P1_DONE_BYTES if phase == "p1"
                  else EXPECTED_P1_BYTES)
    if is_default_set:
        if phase is None:
            failures.append(
                "默认 22 配置的数字既不匹配 P0 基线"
                f"（{EXPECTED['full_mapped']}/{EXPECTED['missing']}，"
                f"{EXPECTED_P1_ROWS} 行，{EXPECTED_P1_BYTES} B）"
                "也不匹配 P1 基线"
                f"（{P1_EXPECTED['full_mapped']}/{P1_EXPECTED['missing']}，"
                f"{EXPECTED_P1_DONE_ROWS} 行，{EXPECTED_P1_DONE_BYTES} B）")
        for key, expected in expected_totals.items():
            actual = totals[key]
            if actual != expected:
                failures.append(f"{key}={actual}（期望 {expected}）")
    else:
        print("[6/6] 自定义配置集：跳过固定基线校验，只做内部一致性检查")
        if totals["refs"] != (totals["full_mapped"] + totals["trimmed"]
                              + totals["missing"]):
            failures.append("refs != full_mapped + trimmed + missing")
    if totals["failed_configs"]:
        failures.append(
            f"failed_configs={totals['failed_configs']}（坏配置不算通过）")
    if len(manifests) != totals["configs"] - totals["failed_configs"]:
        failures.append(
            f"manifest 数 {len(manifests)} != 成功配置数 "
            f"{totals['configs'] - totals['failed_configs']}")
    if len(csv_rows) != totals["missing"] + totals["trimmed"]:
        failures.append(
            f"CSV 行数 {len(csv_rows)} != missing + trimmed "
            f"{totals['missing'] + totals['trimmed']}")
    if is_default_set and len(csv_rows) != want_rows:
        failures.append(
            f"CSV 行数 {len(csv_rows)}（期望 {want_rows}）")
    if is_default_set and p1_bytes != want_bytes:
        failures.append(
            f"P1 字节 {p1_bytes}（期望 {want_bytes}）")
    if is_default_set:
        no_trim_range = [it for it in trim_items if it.get("trim_s") is None]
        if no_trim_range:
            failures.append(
                f"{len(no_trim_range)} 个裁尾段没有 trim_s"
                "（manifest._trim_range 的 duration 回退未生效？）")
    if uploads is not None and (uploads["missing"]
                               or uploads["size_mismatch"]):
        failures.append(
            f"P1 落盘校验：缺失 {len(uploads['missing'])} 个，"
            f"字节不符 {len(uploads['size_mismatch'])} 个")

    _print_config_table(per_config)
    print(f"字节：整段已映射 {human_bytes(bytes_total['full_mapped'])} · "
          f"裁尾 {human_bytes(bytes_total['trimmed'])} "
          f"({_decimal_gb(bytes_total['trimmed'])} GB 十进制 / "
          f"{bytes_total['trimmed']} B) · "
          f"缺失 {human_bytes(bytes_total['missing'])}")
    print("缺失整段：")
    for miss in missing_segments:
        print(f"  - {miss['name']} {miss['seg']} "
              f"{miss['bytes']} B → {miss['suggested_oss_target']}")
    print("裁尾源分析："
          f"唯一命中 {unique_matches} / 不可自动匹配 {not_matched} "
          f"/ ambiguous {ambiguous}；按原因 {by_reason}")
    print(f"P1 清单：{len(csv_rows)} 行 · {p1_bytes} B "
          f"({_decimal_gb(p1_bytes)} GB 十进制 / {_gib(p1_bytes)} GiB)")
    if uploads is not None:
        print(f"P1 落盘校验：{uploads['checked']} 个目标 · "
              f"已落盘 {uploads['present']} · "
              f"缺失 {len(uploads['missing'])} · "
              f"字节不符 {len(uploads['size_mismatch'])}")

    elapsed_all = _now() - t_all
    if failures:
        print("[6/6] 校验失败：", file=sys.stderr)
        for failure in failures:
            print(f"  ✗ {failure}", file=sys.stderr)
        print(f"总耗时 {_fmt_elapsed(elapsed_all)}", file=sys.stderr)
        return 1

    print(f"[6/6] 校验通过：configs={totals['configs']} refs={totals['refs']} "
          f"full_mapped={totals['full_mapped']} trimmed={totals['trimmed']} "
          f"missing={totals['missing']} "
          f"local_missing={totals['local_missing']} "
          f"failed_configs={totals['failed_configs']} ✓")
    print(f"总耗时 {_fmt_elapsed(elapsed_all)}；输出目录 {out_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(_parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
