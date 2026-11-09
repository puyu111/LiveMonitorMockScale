"""撞库结果的落盘：``matches.json`` / CSV / xlsx / README + 命中账号的标记图。

目录形态::

    <out>/matches.json          机读全量（平台、名单、账号、分数、最近未命中）
    <out>/命中明细.csv          一行 = 一个「名单名 × 命中账号」
    <out>/未命中明细.csv        一行 = 一个没到阈值的名单名 + 最近账号
    <out>/撞库结果.xlsx         同上三张表（装了 openpyxl 才写）
    <out>/README.md             口径说明（每次重跑都覆盖）
    <out>/标记图/<平台>/<名单名>/<账号目录>/1.jpg …   命中账号的交付图（默认导出）
    <out>/撞库清单/<日期>_<场次>_<平台>.html           给人看的交付清单（默认写）

「标记图」就是交付树里**原样**的图（整帧原图 + 该条评论一个绿框），不裁、不加字；
只是按**名单姓名**重新归了一次档，拿到手就能直接发给活动方核对。``--no-images`` 关掉。

这份 README 与 ``collect`` 的那份同理：**由工具生成**，每次重跑现算；别手改。
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from .common import IMAGE_SUFFIXES, platform_sort_key, safe_dirname
from .html import write_html
from .render import render_missing

HIT_HEADER = ["平台", "名单序号", "名单姓名", "名单行", "账号", "等级", "匹配度",
              "评论数", "正面数", "在交付树", "账号目录", "图数", "分段", "首图", "目录存在"]
UNHIT_HEADER = ["平台", "名单序号", "名单姓名", "名单行", "最近账号", "最近匹配度"]
SUMMARY_HEADER = ["平台", "名单人数", "命中人数", "未命中人数", "命中账号数",
                  "直播账号数", "交付账号数", "仅直播命中", "未命中账号数", "交付树"]


def _segs_text(segs: object) -> str:
    if not isinstance(segs, dict) or not segs:
        return ""
    return "；".join(f"{k}={v}" for k, v in segs.items())


def _fmt_seq(value: object) -> str:
    return "" if value is None else str(value)


def _scope_text(scope: object) -> str:
    return {"stream": "直播全量账号（发过任意一条评论就算，正面/负面/中性都进池子，"
                      "不要求进交付树）",
            "delivered": "只撞交付树（≥交付阈值的账号）"}.get(
                str(scope or "stream"), str(scope))


def hit_rows(report: dict) -> list[list]:
    """报告 → 命中明细的行（CSV / xlsx 共用，保证两边逐格一致）。"""
    rows: list[list] = []
    for label in sorted(report["platforms"], key=platform_sort_key):
        rep = report["platforms"][label]
        for m in rep["matches"]:
            rows.append([
                label,
                _fmt_seq(m.get("list_seq")),
                m["list_name"],
                _fmt_seq(m.get("list_row")),
                m["account"],
                m.get("level") or "",
                f"{m['score']:.4f}",
                "" if m.get("n_comments") is None else m["n_comments"],
                "" if m.get("n_positive") is None else m["n_positive"],
                "是" if m.get("in_delivery") else "否",
                m.get("account_dir") or "",
                "" if m.get("count") is None else m["count"],
                _segs_text(m.get("segs")),
                m.get("first_image") or "",
                "是" if m.get("dir_exists") else "否",
            ])
    return rows


def unhit_rows(report: dict) -> list[list]:
    rows: list[list] = []
    for label in sorted(report["platforms"], key=platform_sort_key):
        rep = report["platforms"][label]
        for u in rep["unmatched"]:
            best = u.get("best") or {}
            rows.append([
                label,
                _fmt_seq(u.get("list_seq")),
                u["list_name"],
                _fmt_seq(u.get("list_row")),
                best.get("display") or best.get("account") or "",
                f"{best['score']:.4f}" if best else "",
            ])
    return rows


def summary_rows(report: dict) -> list[list]:
    rows: list[list] = []
    for label in sorted(report["platforms"], key=platform_sort_key):
        rep = report["platforms"][label]
        rows.append([
            label, rep["list_count"], rep["matched_count"], rep["unmatched_count"],
            rep["matched_accounts"], rep["accounts_count"], rep.get("delivered_count", 0),
            rep.get("matched_not_delivered", 0),
            rep["accounts_not_matched"], rep.get("tree") or "",
        ])
    return rows


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM ⇒ Excel 直接认
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _write_xlsx(path: Path, report: dict, log) -> bool:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter
    except ImportError:  # pragma: no cover - 环境问题
        log("[撞库][note] 没装 openpyxl，跳过 .xlsx（CSV 已写）")
        return False

    wb = Workbook()
    sheets = [
        ("汇总", SUMMARY_HEADER, summary_rows(report), (10, 12, 12, 12, 12, 12, 14, 70)),
        ("命中明细", HIT_HEADER, hit_rows(report),
         (10, 10, 22, 8, 22, 8, 10, 30, 8, 24, 40, 8)),
        ("未命中明细", UNHIT_HEADER, unhit_rows(report), (10, 10, 22, 8, 22, 12)),
    ]
    first = True
    for title, header, rows, widths in sheets:
        ws = wb.active if first else wb.create_sheet(title)
        if first:
            ws.title = title
            first = False
        ws.append(header)
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in rows:
            ws.append(r)
        ws.freeze_panes = "A2"
        for i, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = width
    wb.save(path)
    return True


def _readme(report: dict) -> str:
    inp = report["inputs"]
    s = report["summary"]
    lines = [
        "# 撞库结果",
        "",
        "> 本目录由 `python -m livepipe match` 生成，**每次重跑都会覆盖**；不要手改。",
        "",
        "## 本次口径",
        "",
        f"- 名单：`{inp.get('names')}`",
        f"- 直播产物：`{inp.get('live')}`",
        f"- 匹配度阈值：**{float(inp.get('threshold', 0)):.2f}**",
        f"- 撞库范围：**{_scope_text(inp.get('scope'))}**",
        f"- 匹配指标：`{inp.get('metric')}`"
        + ("（整名相似度与逐窗口相似度取大，容忍昵称被截断）"
           if inp.get("metric") == "partial" else "（整名 `difflib` 相似度）"),
        "- 名单归一化：NFKC + 忽略大小写 + 去空白/零宽字符；标点、数字、emoji 保留",
        "",
        "## 汇总",
        "",
        "| 平台 | 名单人数 | 命中人数 | 未命中 | 命中账号 | 直播账号 | 交付账号 | 仅直播命中 | 未在名单内账号 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for label in sorted(report["platforms"], key=platform_sort_key):
        r = report["platforms"][label]
        lines.append(f"| {label} | {r['list_count']} | {r['matched_count']} | "
                     f"{r['unmatched_count']} | {r['matched_accounts']} | "
                     f"{r['accounts_count']} | {r.get('delivered_count', 0)} | "
                     f"{r.get('matched_not_delivered', 0)} | "
                     f"{r['accounts_not_matched']} |")
    lines += [
        f"| **合计** | {s['list_names']} | {s['matched_names']} | "
        f"{s['unmatched_names']} | {s['matched_accounts']} | {s.get('accounts', '—')} | — | "
        f"{s.get('matched_not_delivered', 0)} | — |",
        "",
        "## 文件",
        "",
        "| 文件 | 内容 |",
        "|---|---|",
        "| `matches.json` | 机读全量：每个名单名命中的全部账号、分数、评论数、账号目录、分段条数 |",
        "| `命中明细.csv` | 一行 = 一个「名单名 × 命中账号」；分数 ≥ 阈值 |",
        "| `未命中明细.csv` | 没到阈值的名单名 + 平台内最近账号（人工复核用） |",
        "| `撞库结果.xlsx` | 上面三张表（装了 openpyxl 才有） |",
        "| `标记图/` | 命中账号的交付图，按「平台 / 名单姓名 / 账号目录」归档 |",
        "| `撞库清单/` | **给人看的 HTML 交付清单**：一平台一页，KPI + 占比条 + "
        "可搜索/排序的命中名单 + 未命中复核表；多平台另有 `汇总.html` |",
        "",
        "## 怎么用分数",
        "",
        "- 分数是 0~1；**阈值只决定「算不算命中」**，报告里仍然保留最近账号和它的分数。",
        "- **「在交付树」列是「否」的命中**：这个人在直播里确实发过评论（`评论数`/`正面数` 列），"
        "只是没达到交付阈值（交付要 ≥3 张正面图），所以交付树里没有他的账号目录 —— "
        "标记图会从视频里**现补**出来，形态与交付图一致。",
        "- 一个名单名可能命中多个账号：同名不同等级标（`用***◆16` / `◆21`）在交付树里"
        "本来就是两个目录；OCR 把等级数字并进昵称也会多出一个近似档。",
        "- 「账号目录」是**相对交付树**的路径（见汇总表最后一列的交付树绝对路径）；"
        "每个命中的账号，图都**原样**放在 `标记图/<平台>/<名单名>/<账号目录>/` 里"
        "（整帧原图 + 评论绿框，不裁、不加字）；一个名单名命中多个账号就有多个子目录。",
        "",
        "## 标记图",
        "",
        "```",
        "标记图/<平台>/<名单姓名>/<账号目录>/1.jpg  2.jpg …",
        "```",
        "",
        "- 图**原样复制**自交付树，文件名不变；只复制图片，不改一个像素。",
        "- 一个名单名命中多个账号时，各账号一个子目录，避免混在一起。",
        "- 同一结果目录重跑只覆盖同名文件；**旧名单名留下的 `标记图/` 子目录不会自动删**"
        "（要干净重来就先删掉 `标记图/`）。",
        "- 只要表格、不要图：`--no-images`。",
        "",
        f"生成时间：{report.get('generated_at')}",
        "",
    ]
    return "\n".join(lines)


def write_reports(
    out_dir: Path,
    report: dict,
    write_xlsx: bool = True,
    images: bool = True,
    html: bool = True,
    log=print,
    video_map: list[tuple[str, str]] | None = None,
) -> dict:
    """把报告写进 ``out_dir``；返回写出的文件路径。

    ``images=True`` 时同时导出标记图；``html=True`` 时再写 ``撞库清单/``
    （人看的交付清单，图导完才写 —— 清单要数图片张数）。
    ``video_map`` 透传给现补图：summary.json 里的视频路径失效时做前缀重映射。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": out_dir / "matches.json",
        "hits": out_dir / "命中明细.csv",
        "unhits": out_dir / "未命中明细.csv",
        "readme": out_dir / "README.md",
    }
    paths["json"].write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_csv(paths["hits"], HIT_HEADER, hit_rows(report))
    _write_csv(paths["unhits"], UNHIT_HEADER, unhit_rows(report))
    paths["readme"].write_text(_readme(report), encoding="utf-8")
    if images:
        info = export_images(out_dir, report, log=log)
        rendered = render_missing(out_dir, report, log=log, video_map=video_map)
        info["rendered"] = rendered
        info["accounts"] += rendered["accounts"]
        info["images"] += rendered["images"]
        paths["images"] = info
    if html:
        paths["html"] = write_html(out_dir, report, log=log)
    if write_xlsx:
        xlsx = out_dir / "撞库结果.xlsx"
        if _write_xlsx(xlsx, report, log):
            paths["xlsx"] = xlsx
    return paths


def export_images(out_dir: Path, report: dict, log=print) -> dict:
    """把命中账号的交付图**原样**复制到 ``<out>/标记图/<平台>/<名单名>/<账号目录>/``。

    只复制图片文件（``.jpg/.jpeg/.png/.webp/.bmp``），文件名不变、不裁剪、不加字 ——
    交付图本身就是「整帧原图 + 该条评论一个绿框」。同一名单名命中多个账号时各占一个
    子目录；旧名单名留下的目录不会自动删。

    ``in_delivery=False`` 的命中（只在直播里、没进交付树）这里跳过，由
    :func:`render_missing` 从视频现补。

    返回 ``{"dir", "accounts", "images", "missing", "not_delivered", "bytes"}``：
    ``missing`` 是交付树里找不到目录的命中数（``--tree`` 换了、目录被清过时会出现）。
    """
    root = out_dir / "标记图"
    seen: set[Path] = set()
    accounts = image_count = missing = not_delivered = total_bytes = 0
    for label in sorted(report["platforms"], key=platform_sort_key):
        rep = report["platforms"][label]
        tree = Path(rep["tree"]) if rep.get("tree") else None
        for m in rep["matches"]:
            d = m.get("account_dir") or ""
            if not m.get("in_delivery"):
                not_delivered += 1
                continue
            if tree is None or not d:
                missing += 1
                continue
            src = tree / d
            if not src.is_dir():
                missing += 1
                continue
            dst = root / label / safe_dirname(m["list_name"]) / d
            if dst in seen:                    # 同一名字重复命中同一账号：只导一次
                continue
            seen.add(dst)
            dst.mkdir(parents=True, exist_ok=True)
            for f in sorted(src.iterdir()):
                if not f.is_file() or f.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                shutil.copy2(f, dst / f.name)
                image_count += 1
                total_bytes += f.stat().st_size
            accounts += 1
    if missing:
        log(f"[撞库][warn] {missing} 个命中账号的目录不存在，没导出图")
    return {"dir": root, "accounts": accounts, "images": image_count,
            "missing": missing, "not_delivered": not_delivered,
            "bytes": total_bytes}
