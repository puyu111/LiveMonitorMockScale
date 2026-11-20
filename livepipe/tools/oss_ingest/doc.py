"""终端表格与摘要渲染（**只用标准库**）。

``cli.py`` 只负责取数与退出码，具体怎么排版集中在这里，方便单独调整：

- :func:`table`：等宽文本表，按东亚全角字符宽度对齐（中文不再歪）。
- :func:`manifest_summary` / :func:`manifest_table`：单份映射清单的摘要与逐段表。
- :func:`audit_summary` / :func:`audit_table`：多份配置的审计摘要。
- :func:`sync_summary`：同步 / 校验的计数行。
- :func:`ingest_summary`：``.part`` 收尾结果。
- :func:`check_env_table`：``check-env`` 的 ``检查 | 结果 | 说明`` 表。
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Iterable, Sequence

from .common import count_segments, human_bytes, seg_bytes

_ENV_HEADERS = ("检查", "结果", "说明")


# --------------------------------------------------------------------------- #
# 等宽表
# --------------------------------------------------------------------------- #
def _width(text: str) -> int:
    """终端显示宽度：东亚全角/宽字符按 2 列算，其余按 1 列。"""
    total = 0
    for ch in text:
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    """把表头和数据行排成对齐的纯文本表（`` | `` 分隔）。"""
    headers = [str(h) for h in headers]
    body = [[str(c) for c in row] for row in rows]
    widths = [_width(h) for h in headers]
    for row in body:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], _width(cell))

    def render(row: Sequence[str]) -> str:
        cells = []
        for i, cell in enumerate(row):
            pad = widths[i] - _width(cell) if i < len(widths) else 0
            cells.append(cell + " " * max(0, pad))
        return " | ".join(cells).rstrip()

    lines = [render(headers), "-+-".join("-" * w for w in widths)]
    lines.extend(render(row) for row in body)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 映射清单 / 审计
# --------------------------------------------------------------------------- #
def manifest_counts(manifest: dict) -> dict:
    """从单份清单的 ``segments`` 推出 refs/full_mapped/trimmed/missing/local_missing。

    口径集中在 :func:`common.count_segments`，与 ``audit`` 共用，避免两套。
    """
    return count_segments(manifest.get("segments") or [])


def _fmt_duration(value: object) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.1f}s"
    except (TypeError, ValueError):
        return "-"


def _fmt_trim(value: object) -> str:
    if not value or not isinstance(value, (list, tuple)) or len(value) != 2:
        return "-"
    try:
        return f"[{float(value[0]):g}, {float(value[1]):g}]"
    except (TypeError, ValueError):
        return "-"


def manifest_summary(manifest: dict) -> str:
    """单份清单的一行摘要：配置 / 段 / 全片 / 裁尾 / 缺失 / 本地缺失。"""
    c = manifest_counts(manifest)
    cfg = manifest.get("config") or "-"
    return (f"配置：{cfg} | 段 {c['refs']} | 全片命中 {c['full_mapped']} | "
            f"裁尾 {c['trimmed']} | 缺失 {c['missing']} | "
            f"本地缺失 {c['local_missing']}")


def manifest_table(manifest: dict) -> str:
    """逐段表：段 / 裁尾 / 大小 / 时长 / 裁尾区间 / 磁盘 / OSS。"""
    rows = []
    for seg in manifest.get("segments") or []:
        size = seg_bytes(seg)
        rows.append([
            seg.get("seg") or "-",
            "是" if seg.get("trimmed") else "否",
            human_bytes(size) if size >= 0 else "-",
            _fmt_duration(seg.get("duration_s")),
            _fmt_trim(seg.get("trim_s")),
            seg.get("disk") or "-",
            seg.get("oss") or "-",
        ])
    return table(("段", "裁尾", "大小", "时长", "裁尾区间", "磁盘", "OSS"), rows)


def audit_summary(audit: dict) -> str:
    """审计总览：配置 / 引用 / 全片 / 裁尾 / 缺失 / 本地缺失 / 失败配置。"""
    return (f"配置 {audit.get('configs', 0)} 份 | 引用 {audit.get('refs', 0)} 个视频 | "
            f"全片命中 {audit.get('full_mapped', 0)} | 裁尾 {audit.get('trimmed', 0)} | "
            f"缺失 {audit.get('missing', 0)} | "
            f"本地缺失 {audit.get('local_missing', 0)} | "
            f"失败配置 {audit.get('failed_configs', 0)}")


def audit_table(audit: dict) -> str:
    """逐配置表：配置 / 结果 / 引用 / 全片 / 裁尾 / 缺失 / 本地缺失 / 备注。"""
    rows = []
    for d in audit.get("details") or []:
        note = d.get("error") or ""
        if not note and d.get("missing"):
            note = f"缺失 {d['missing']} 段"
        elif not note and d.get("local_missing"):
            note = f"本地缺失 {d['local_missing']} 段"
        if d.get("ok"):
            result = "✔"
        elif not d.get("missing") and d.get("local_missing"):
            # 本地素材不可用 ⇒ 映射未验证，不能显示成通过。
            result = "未验证"
        else:
            result = "✗"
        rows.append([
            Path(str(d.get("config") or "-")).name,
            result,
            d.get("refs", 0),
            d.get("full_mapped", 0),
            d.get("trimmed", 0),
            d.get("missing", 0),
            d.get("local_missing", 0),
            note or "-",
        ])
    return table(("配置", "结果", "引用", "全片", "裁尾", "缺失",
                  "本地缺失", "备注"), rows)


def audit_missing_lines(audit: dict) -> list[str]:
    """列出未映射的全片与本地不可用的段，方便补传 / 换素材盘。"""
    lines: list[str] = []
    for d in audit.get("details") or []:
        for seg in d.get("segments") or []:
            if seg.get("oss_missing"):
                lines.append(
                    f"  缺失（OSS 对象不存在）：{d.get('config')} "
                    f"{seg.get('seg') or '-'} {seg.get('disk') or '-'}")
            elif seg_bytes(seg) < 0:
                lines.append(
                    f"  本地缺失：{d.get('config')} {seg.get('seg') or '-'} "
                    f"{seg.get('disk') or '-'}")
            elif not seg.get("trimmed") and not seg.get("oss"):
                lines.append(
                    f"  缺失：{d.get('config')} {seg.get('seg') or '-'} "
                    f"{seg.get('disk') or '-'}")
    return lines


# --------------------------------------------------------------------------- #
# 同步 / 收尾
# --------------------------------------------------------------------------- #
def sync_summary(result: dict) -> str:
    """同步（或校验）结果的计数行。"""
    lines = [
        f"文件 {result.get('files', 0)} 个 | "
        f"字节 {human_bytes(int(result.get('bytes') or 0))}"
        f"（{result.get('bytes', 0)} B）",
        f"复制 {result.get('copied', 0)} | 跳过 {result.get('skipped', 0)} | "
        f"软链跳过 {result.get('symlinks_skipped', 0)} | "
        f"删除源 {result.get('deleted', 0)} | 失败 {result.get('failed', 0)}",
    ]
    if result.get("walk_errors"):
        lines.append(f"⚠ 遍历错误 {len(result['walk_errors'])} 个目录"
                     "（部分文件可能未统计）")
    return "\n".join(lines)


def verify_summary(result: dict) -> str:
    """``verify_tree`` 结果的计数行。"""
    lines = [
        f"文件 {result.get('files', 0)} 个 | "
        f"字节 {human_bytes(int(result.get('bytes') or 0))}"
        f"（{result.get('bytes', 0)} B）",
        f"一致 {result.get('ok', 0)} | 缺失 {result.get('missing', 0)} | "
        f"大小不符 {result.get('size_mismatch', 0)} | "
        f"哈希不符 {result.get('sha256_mismatch', 0)} | "
        f"软链跳过 {result.get('symlinks_skipped', 0)} | "
        f"多余 {result.get('extra', 0)}",
    ]
    if result.get("walk_errors"):
        lines.append(f"⚠ 遍历错误 {len(result['walk_errors'])} 个目录"
                     "（校验结果可能不完整）")
    return "\n".join(lines)


def ingest_summary(manifest: dict, path: str | Path | None = None) -> str:
    """``.part`` 收尾结果：final / 字节 / 时长 / manifest 落点。"""
    size = int(manifest.get("bytes") or 0)
    lines = [
        f"final：{manifest.get('final')}",
        f"字节：{size} B（{human_bytes(size)}）",
        f"时长：{_fmt_duration(manifest.get('duration_s'))}",
        f"manifest：{path if path is not None else '-'}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 环境体检
# --------------------------------------------------------------------------- #
def check_env_table(rows: Sequence[dict]) -> str:
    """``check-env`` 固定三列表：``检查 | 结果 | 说明``。"""
    body = [[r.get("check", "-"), r.get("status", "-"), r.get("detail", "")]
            for r in rows]
    return table(_ENV_HEADERS, body)
