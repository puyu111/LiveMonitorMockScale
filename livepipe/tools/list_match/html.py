"""撞库结果的 **HTML 交付清单**（给人看，不是机读）。

样式对齐 ``/home/puyu/交付/撞库格式/20260821_成都车展发布会_抖音-奇瑞汽车.html``：
一页一个平台，顶部 5 个 KPI + 两条占比条，下面一张可搜索/可排序的「撞库用户名单」，
再下面是「未命中名单」和口径说明。

产出::

    <结果>/撞库清单/20261031_奇瑞电感智行·大美中国_抖音.html
    <结果>/撞库清单/汇总.html                     ← 多平台时才写

- **场次 / 日期**：默认自动找 ``cwd/*.json`` 里 ``output.dir`` 指向本平台目录的配置，
  用它的 ``name`` 当场次、里面的日期当场次日期；找不到就用直播目录名（``10.31``）。
  ``--title`` / ``--date`` 可强制。
- **图**：清单不嵌图（258MB 塞不进一页），只给「首图」链接，指向 ``../标记图/…``，
  浏览器直接打开。``--no-images`` 时没有链接，截图数退回 manifest 的口径。
- 纯字符串拼装，零第三方依赖；名字全部转义，含 ``<`` / ``&`` 也不会破页。
"""

from __future__ import annotations

import html as _html
import json
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from .common import (
    IMAGE_SUFFIXES,
    dir_username,
    platform_sort_key,
    safe_dirname,
)

_DATE_RE = re.compile(r"(20\d{2})\s*[-./年]\s*(\d{1,2})\s*[-./月]\s*(\d{1,2})")
_MD_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)")
_TRAIL_DATE = re.compile(r"\s*20\d{2}\s*[-./年]\s*\d{1,2}\s*[-./月]\s*\d{1,2}\s*日?\s*$")


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def _esc(value: object) -> str:
    return _html.escape("" if value is None else str(value), quote=True)


def _slug(text: object, maxlen: int = 48) -> str:
    """场次名 → 文件名片段（空白/非法字符变 ``-``）。"""
    s = re.sub(r'[\\/:*?"<>|\s]+', "-", str(text or ""))
    s = re.sub(r"-{2,}", "-", s).strip("-·. ")
    return s[:maxlen]


def _clean_event(name: object) -> str:
    """配置 ``name`` → 场次：取 `` · `` 之前、去掉末尾日期，如
    ``奇瑞电感智行·大美中国 2026-10-31 · 抖音手机 App · 竖屏`` → ``奇瑞电感智行·大美中国``。"""
    s = str(name or "").split(" · ")[0].strip()
    s = _TRAIL_DATE.sub("", s).strip()
    return s


def _gen_date(report: dict) -> date:
    raw = str(report.get("generated_at") or "")
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return date.today()


def _infer_date(texts, fallback: date) -> date:
    for text in texts:
        if not text:
            continue
        m = _DATE_RE.search(str(text))
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
        m = _MD_RE.search(str(text))
        if m:
            try:
                cand = date(fallback.year, int(m.group(1)), int(m.group(2)))
            except ValueError:
                continue
            if (cand - fallback).days > 183:      # 10.31 生成于 11 月 ⇒ 本年；生成于 3 月 ⇒ 去年
                try:
                    cand = date(fallback.year - 1, cand.month, cand.day)
                except ValueError:
                    continue
            return cand
    return fallback


def _config_name(cwd: Path, platform_dir: Path | None) -> str:
    """在 ``cwd/*.json`` 里找 ``output.dir`` 指向本平台目录的配置，返回它的 ``name``。

    配置是 JSONC（带 ``//`` 注释）；这里只做**尽力而为**的嗅探，读不到就返回空。
    """
    if platform_dir is None:
        return ""
    try:
        from ...config.io import _strip_jsonc
    except Exception:  # pragma: no cover - 配置模块不可用也不该拖垮撞库
        return ""
    target = platform_dir.resolve()
    for f in sorted(cwd.glob("*.json")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if "output" not in text or '"name"' not in text:
            continue
        try:
            cfg = json.loads(_strip_jsonc(text))
        except Exception:
            continue
        d = str(((cfg.get("output") or {}).get("dir")) or "").strip()
        if not d:
            continue
        p = Path(d)
        if not p.is_absolute():
            p = cwd / p
        try:
            if p.resolve() != target:
                continue
        except OSError:
            continue
        return str(cfg.get("name") or "").strip()
    return ""


def _count_images(root: Path | None) -> int:
    if root is None or not root.is_dir():
        return 0
    n = 0
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES:
            n += 1
    return n


def _mark_dir(out_dir: Path, label: str, m: dict) -> Path | None:
    """命中账号在 ``标记图/`` 里的目录。

    交付命中用 ``account_dir``；仅直播命中在 ``matches.json`` 里没有目录
    （``account_dir`` 为空），按 ``<账号>-<条数>`` 的命名在名单名目录下找。
    """
    base = out_dir / "标记图" / label / safe_dirname(m["list_name"])
    d = m.get("account_dir") or ""
    if d and (base / d).is_dir():
        return base / d
    if not base.is_dir():
        return None
    names = {m.get("account") or "", m.get("display") or ""}
    cands = [p for p in base.iterdir()
             if p.is_dir() and dir_username(p.name) in names]
    if not cands:
        return None
    return max(cands, key=lambda p: _count_images(p))


def _first_image(directory: Path | None) -> str:
    if directory is None or not directory.is_dir():
        return ""
    for p in sorted(directory.iterdir()):
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES:
            return p.name
    return ""


def _rel_url(*parts: object) -> str:
    """相对 HTML 的链接：逐段 URL 编码，中文/空格都能点。"""
    return "/".join(quote(str(p), safe="") for p in parts)


def _pct(part: int, whole: int) -> float:
    return (part / whole * 100.0) if whole else 0.0


def _chip(kind: str, text: str) -> str:
    return f'<span class="chip {kind}">{_esc(text)}</span>'


def _match_kind(list_name: str, m: dict) -> tuple[str, str]:
    """名称匹配的分类：精确同名 / 截断同名（0.8 以上的包含）/ 疑似 N%。"""
    if str(list_name) == str(m.get("account") or ""):
        return "exact", "精确同名"
    if float(m.get("score") or 0) >= 0.999:
        return "norm", "截断同名"
    return "fuzzy", f"疑似 {float(m.get('score') or 0) * 100:.0f}%"


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #

_CSS = """
:root{
  --bg:#f4f6fa; --card:#ffffff; --ink:#1a2233; --ink2:#5a6579; --ink3:#8b95a7;
  --line:#e6eaf2; --brand:#2b5cff; --brand-soft:#eef2ff; --hit:#e0483c; --hit-soft:#fdeeec;
  --ok:#12855f; --ok-soft:#e6f6f0; --warn:#b7791f; --warn-soft:#fdf5e6;
  --shadow:0 1px 2px rgba(20,30,60,.05), 0 8px 24px rgba(20,30,60,.06);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:"Microsoft YaHei","PingFang SC","Hiragino Sans GB","Source Han Sans SC",-apple-system,"Segoe UI",sans-serif;
  font-size:14px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:1280px;margin:0 auto;padding:32px 24px 64px}
.head{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;flex-wrap:wrap;margin-bottom:22px}
.head h1{margin:0 0 6px;font-size:24px;letter-spacing:.3px;font-weight:700}
.head .sub{color:var(--ink2);font-size:13px}
.tags{display:flex;gap:8px;flex-wrap:wrap}
.tag{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:5px 13px;font-size:12.5px;color:var(--ink2);box-shadow:var(--shadow)}
.tag b{color:var(--ink);font-weight:600}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;box-shadow:var(--shadow);position:relative;overflow:hidden}
.kpi .lab{font-size:12.5px;color:var(--ink3);letter-spacing:.2px;margin-bottom:7px}
.kpi .val{font-size:27px;font-weight:700;letter-spacing:.2px;line-height:1.15;font-variant-numeric:tabular-nums}
.kpi .note{font-size:12px;color:var(--ink3);margin-top:5px}
.kpi.hl{background:linear-gradient(135deg,#fff6f5,#fdeeec);border-color:#f6d6d2}
.kpi.hl .val{color:var(--hit)}
.kpi.brand{background:linear-gradient(135deg,#f3f6ff,#eef2ff);border-color:#d9e2ff}
.kpi.brand .val{color:var(--brand)}
.bars{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:22px}
.barcard{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;box-shadow:var(--shadow)}
.barcard .t{display:flex;justify-content:space-between;font-size:12.5px;color:var(--ink2);margin-bottom:9px;gap:12px;flex-wrap:wrap}
.barcard .t b{color:var(--ink);font-variant-numeric:tabular-nums}
.track{height:9px;background:#eef1f7;border-radius:999px;overflow:hidden}
.fill{height:100%;border-radius:999px}
.fill.u{background:linear-gradient(90deg,#4d79ff,#2b5cff)}
.fill.i{background:linear-gradient(90deg,#f0715f,#e0483c)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);overflow:hidden;margin-bottom:18px}
.panel .ph{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:15px 18px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.panel .ph h2{margin:0;font-size:15.5px;font-weight:700}
.panel .ph .hint{font-size:12.5px;color:var(--ink3)}
.tools{display:flex;gap:8px;align-items:center}
.tools input{border:1px solid var(--line);background:#fbfcfe;border-radius:9px;padding:7px 12px;font-size:13px;width:200px;outline:none;color:var(--ink);font-family:inherit}
.tools input:focus{border-color:#b9c8ff;background:#fff}
.tblwrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13.5px;min-width:1060px}
thead th{background:#fafbfe;color:var(--ink2);font-weight:600;font-size:12.5px;text-align:left;padding:11px 14px;border-bottom:1px solid var(--line);white-space:nowrap;cursor:pointer;user-select:none}
thead th .ar{color:#c2cad9;font-size:10px;margin-left:4px}
thead th.act{color:var(--brand)}
thead th.act .ar{color:var(--brand)}
thead th.num,tbody td.num{text-align:right;font-variant-numeric:tabular-nums}
tbody td{padding:11px 14px;border-bottom:1px solid #f1f4f9;vertical-align:middle}
tbody tr:hover td{background:#fafbff}
tbody tr:last-child td{border-bottom:none}
.rank{color:var(--ink3);font-variant-numeric:tabular-nums;font-size:12.5px;width:44px}
.uname{font-weight:600;color:var(--ink);word-break:break-all}
.acct{color:var(--ink2);word-break:break-all}
.pct{display:flex;align-items:center;gap:8px;justify-content:flex-end}
.pct .mini{width:52px;height:6px;background:#eef1f7;border-radius:999px;overflow:hidden;flex:none}
.pct .mini i{display:block;height:100%;min-width:2px;background:linear-gradient(90deg,#f0715f,#e0483c);border-radius:999px}
.pct .mini.b i{background:linear-gradient(90deg,#8fa8ff,#2b5cff)}
.pct span{font-variant-numeric:tabular-nums;min-width:52px;text-align:right}
.chip{display:inline-block;border-radius:6px;padding:2.5px 9px;font-size:12px;white-space:nowrap;font-weight:600}
.chip.exact{background:var(--ok-soft);color:var(--ok)}
.chip.norm{background:var(--brand-soft);color:var(--brand)}
.chip.fuzzy{background:var(--warn-soft);color:var(--warn)}
.chip.yes{background:var(--ok-soft);color:var(--ok)}
.chip.no{background:var(--warn-soft);color:var(--warn)}
.chip.none{background:#f1f3f7;color:#8b95a7}
.mono{font-family:ui-monospace,Consolas,"Courier New",monospace;font-size:12.5px;color:var(--ink2);word-break:break-all}
a.link{color:var(--brand);text-decoration:none;font-weight:600;white-space:nowrap}
a.link:hover{text-decoration:underline}
.muted{color:var(--ink3)}
.legend{display:flex;gap:18px;flex-wrap:wrap;padding:13px 18px;border-top:1px solid var(--line);background:#fbfcfe;font-size:12.5px;color:var(--ink2)}
.legend i{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:6px;vertical-align:middle}
.notice{display:flex;gap:9px;align-items:flex-start;background:#fdf9ef;border-bottom:1px solid #f2e6c9;padding:11px 18px;font-size:12.8px;color:#8a6212;line-height:1.7}
.notice b{color:#6d4c07}
.notice .ic{flex:none;font-weight:700;color:#d29b21}
.foot{margin-top:16px;font-size:12.5px;color:var(--ink3);line-height:1.9}
.foot b{color:var(--ink2)}
@media (max-width:980px){.kpis{grid-template-columns:repeat(2,1fr)}.bars{grid-template-columns:1fr}}
@media print{body{background:#fff}.panel,.kpi,.barcard{box-shadow:none}}
"""

_JS = """
(function(){
  var tb=document.getElementById('tbody');
  if(!tb) return;
  var rows=Array.prototype.slice.call(tb.querySelectorAll('tr'));
  function renumber(){var k=0;rows.forEach(function(r){if(r.style.display!=='none'){k++;var e=r.querySelector('.rank');if(e)e.textContent=k;}});}
  var q=document.getElementById('q');
  if(q){q.addEventListener('input',function(){
    var v=q.value.trim().toLowerCase();
    rows.forEach(function(r){r.style.display=(!v||(r.dataset.search||'').toLowerCase().indexOf(v)>-1)?'':'none';});
    renumber();
  });}
  var ths=Array.prototype.slice.call(document.querySelectorAll('thead th[data-sort]'));
  var asc={};
  ths.forEach(function(th){
    th.addEventListener('click',function(){
      var key=th.dataset.sort,num=th.dataset.num==='1';
      asc[key]=!asc[key];
      ths.forEach(function(o){o.classList.remove('act');});
      th.classList.add('act');
      rows.slice().sort(function(a,b){
        var x=a.dataset[key]||'',y=b.dataset[key]||'';
        var r=num?(parseFloat(x)-parseFloat(y)):x.localeCompare(y,'zh');
        return asc[key]?r:-r;
      }).forEach(function(r){tb.appendChild(r);});
      renumber();
    });
  });
  renumber();
})();
"""


def _page(title: str, subtitle: str, tags: list[tuple[str, str]], body: str,
          extra: str = "") -> str:
    tag_html = "".join(
        f'<div class="tag">{_esc(k)} <b>{_esc(v)}</b></div>' for k, v in tags)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <div>
      <h1>{_esc(title)}</h1>
      <div class="sub">{_esc(subtitle)}</div>
    </div>
    <div class="tags">{tag_html}</div>
  </div>
{body}
  <div class="foot">{extra}</div>
</div>
<script>{_JS}</script>
</body>
</html>
"""


def _kpi(label: str, value: str, note: str, cls: str = "") -> str:
    return (f'<div class="kpi {cls}"><div class="lab">{_esc(label)}</div>'
            f'<div class="val">{_esc(value)}</div>'
            f'<div class="note">{_esc(note)}</div></div>')


def _bar(title: str, part: int, whole: int, cls: str) -> str:
    pct = _pct(part, whole)
    return (f'<div class="barcard"><div class="t"><span>{_esc(title)}</span>'
            f'<b>{part} / {whole} = {pct:.2f}%</b></div>'
            f'<div class="track"><div class="fill {cls}" style="width:min({pct:.4f}%,100%)"></div></div></div>')


# --------------------------------------------------------------------------- #
# 单个平台
# --------------------------------------------------------------------------- #

def _platform_stats(out_dir: Path, label: str, rep: dict,
                    has_images: bool) -> tuple[list[dict], int, int]:
    """平台报告 → ``(每行指标, 撞库截图总数, 交付树截图总数)``。

    截图数优先数 ``标记图/`` 里的实际文件；没导图（``--no-images``）才退回
    manifest 的 ``count`` / 仅直播账号的 ``n_positive``。
    """
    rows: list[dict] = []
    for m in rep.get("matches", []):
        mark = _mark_dir(out_dir, label, m) if has_images else None
        n = _count_images(mark) if mark is not None else 0
        if not n:
            n = int(m.get("count") or m.get("n_positive") or 0)
        rows.append({
            "m": m,
            "mark": mark,
            "imgs": n,
            "first": _first_image(mark) if mark is not None else "",
            "kind": _match_kind(m["list_name"], m),
        })
    # 同一目录只算一次（名单名 → 账号 是一对多，但目录本就不同；去重更稳）
    seen: set[Path] = set()
    hit_imgs = 0
    for r in rows:
        key = r["mark"] if r["mark"] is not None else Path(f"#row{id(r)}")
        if key in seen:
            continue
        seen.add(key)
        hit_imgs += r["imgs"]
    tree = Path(rep["tree"]) if rep.get("tree") else None
    return rows, hit_imgs, _count_images(tree)


def build_platform_html(
    label: str,
    rep: dict,
    out_dir: Path,
    *,
    event: str,
    when: date,
    threshold: float,
    scope: str,
    has_images: bool,
) -> str:
    """一个平台 → 一页 HTML 字符串。"""
    rows, hit_imgs, total_imgs = _platform_stats(out_dir, label, rep, has_images)

    accounts = int(rep.get("accounts_count") or 0)
    hit_accounts = int(rep.get("matched_accounts") or 0)
    hit_names = int(rep.get("matched_count") or 0)
    list_count = int(rep.get("list_count") or 0)
    delivered = int(rep.get("delivered_count") or 0)
    live_only = int(rep.get("matched_not_delivered") or 0)

    kpis = "".join([
        _kpi("检测出的总用户", str(accounts), "直播账号（comments.jsonl 去重）"),
        _kpi("撞库用户数量", str(hit_accounts),
             f"命中账号 · 名单 {hit_names} 人", "hl"),
        _kpi("撞库占比", f"{_pct(hit_accounts, accounts):.2f}%",
             "命中账号 / 直播账号", "hl"),
        _kpi("总截图数量", str(total_imgs), "交付树 all_users 下全部图片"),
        _kpi("撞库用户截图数量", str(hit_imgs),
             "标记图下全部图片", "brand"),
    ])

    bars = _bar("撞库用户占比（人数口径）", hit_accounts, accounts, "u") + \
        _bar("撞库截图占比（图片口径）", hit_imgs, total_imgs, "i")

    # ---- 命中表 ----
    trs: list[str] = []
    for r in rows:
        m = r["m"]
        kind, kind_text = r["kind"]
        n = r["imgs"]
        pt = _pct(n, hit_imgs)
        pa = _pct(n, total_imgs)
        d = m.get("account_dir") or (r["mark"].name if r["mark"] is not None else "")
        if r["first"]:
            name_dir = safe_dirname(m["list_name"])
            url = _rel_url("..", "标记图", label, name_dir,
                           r["mark"].name, r["first"])
            first_cell = f'<a class="link" href="{_esc(url)}" target="_blank">查看</a>'
        else:
            first_cell = '<span class="muted">—</span>'
        comm = "—"
        if m.get("n_comments") is not None or m.get("n_positive") is not None:
            c = "—" if m.get("n_comments") is None else m["n_comments"]
            p = "—" if m.get("n_positive") is None else m["n_positive"]
            comm = f"{c} / {p}"
        search = " ".join(str(x) for x in (
            m["list_name"], m.get("display") or "", m.get("account") or "", d))
        delivery = ('<span class="chip yes">是</span>' if m.get("in_delivery")
                    else '<span class="chip no">否（仅直播）</span>')
        trs.append(
            f'<tr data-search="{_esc(search)}" data-name="{_esc(m["list_name"])}" '
            f'data-account="{_esc(m.get("display") or m.get("account"))}" '
            f'data-score="{float(m.get("score") or 0):.4f}" data-imgs="{n}" '
            f'data-pt="{pt:.6f}" data-pa="{pa:.6f}" data-comments="{m.get("n_comments") or 0}">'
            f'<td class="rank"></td>'
            f'<td class="uname">{_esc(m["list_name"])}</td>'
            f'<td class="acct">{_esc(m.get("display") or m.get("account"))}</td>'
            f'<td>{_chip(kind, kind_text)}</td>'
            f'<td class="num">{n}</td>'
            f'<td class="num"><div class="pct"><div class="mini"><i style="width:{min(pt, 100):.2f}%"></i></div><span>{pt:.2f}%</span></div></td>'
            f'<td class="num"><div class="pct"><div class="mini b"><i style="width:{min(pa, 100):.2f}%"></i></div><span>{pa:.2f}%</span></div></td>'
            f'<td class="num">{_esc(comm)}</td>'
            f'<td>{delivery}</td>'
            f'<td><span class="mono">{_esc(d or "—")}</span></td>'
            f'<td>{first_cell}</td>'
            f'</tr>')

    counts = {"exact": 0, "norm": 0, "fuzzy": 0}
    for r in rows:
        counts[r["kind"][0]] += 1

    hit_table = f"""
  <div class="panel">
    <div class="ph">
      <div><h2>撞库用户名单 · 共 {hit_accounts} 个账号（{hit_names} 人）</h2></div>
      <div class="tools"><input id="q" type="search" placeholder="搜索姓名 / 账号 / 目录…"></div>
    </div>
    {"" if has_images else '<div class="notice"><span class="ic">!</span><div><b>本次未导出标记图</b>（<code>--no-images</code>）：截图数按 manifest 口径估算，「首图」不可点。</div></div>'}
    <div class="tblwrap">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th data-sort="name">撞库用户名<span class="ar">&#8597;</span></th>
          <th data-sort="account">命中账号<span class="ar">&#8597;</span></th>
          <th>名称匹配</th>
          <th class="num" data-sort="imgs" data-num="1">截图数<span class="ar">&#8597;</span></th>
          <th class="num" data-sort="pt" data-num="1">占撞库截图<span class="ar">&#8597;</span></th>
          <th class="num" data-sort="pa" data-num="1">占全部截图<span class="ar">&#8597;</span></th>
          <th class="num" data-sort="comments" data-num="1">评论 / 正面<span class="ar">&#8597;</span></th>
          <th>在交付树</th>
          <th>账号目录</th>
          <th>首图</th>
        </tr>
      </thead>
      <tbody id="tbody">
{chr(10).join(trs) if trs else '<tr><td colspan="11" class="muted">这个平台没有命中。</td></tr>'}
      </tbody>
    </table>
    </div>
    <div class="legend">
      <span><i style="background:#12855f"></i>精确同名 {counts['exact']}</span>
      <span><i style="background:#2b5cff"></i>截断同名 {counts['norm']}</span>
      <span><i style="background:#b7791f"></i>疑似匹配 {counts['fuzzy']}</span>
      <span><i style="background:#f1f3f7"></i>仅直播（未进交付树） {live_only}</span>
      <span style="color:#8b95a7">点击表头可排序</span>
    </div>
  </div>
"""

    # ---- 未命中表 ----
    un_rows = rep.get("unmatched") or []
    utrs: list[str] = []
    for i, u in enumerate(un_rows, 1):
        best = u.get("best") or {}
        score = float(best.get("score") or 0)
        utrs.append(
            f'<tr><td class="rank">{i}</td>'
            f'<td class="uname">{_esc(u["list_name"])}</td>'
            f'<td class="num muted">{_esc(u.get("list_seq") if u.get("list_seq") is not None else "—")}</td>'
            f'<td class="acct">{_esc(best.get("display") or best.get("account") or "—")}</td>'
            f'<td>{_chip("fuzzy" if score >= 0.8 else "none", f"{score * 100:.0f}%")}</td>'
            f'</tr>')
    un_table = f"""
  <div class="panel">
    <div class="ph"><h2>未命中名单 · 共 {len(un_rows)} 人</h2>
      <div class="hint">阈值 {threshold:.2f} 卡掉或直播里确实没出现，附平台内最近账号供复核</div></div>
    <div class="tblwrap">
    <table style="min-width:760px">
      <thead><tr><th>#</th><th>名单姓名</th><th class="num">名单序号</th><th>最近账号</th><th>最近匹配度</th></tr></thead>
      <tbody>
{chr(10).join(utrs) if utrs else '<tr><td colspan="5" class="muted">名单上的人都命中了。</td></tr>'}
      </tbody>
    </table>
    </div>
  </div>
"""

    scope_text = ("直播全量账号（发过任意一条评论就算，正面/负面/中性都进池子，"
                  "不要求进交付树）"
                  if scope != "delivered" else "只撞交付树（≥交付阈值的账号）")
    extra = (
        f"<b>统计口径</b>：每个账号 = 直播 <code>seg*/comments.jsonl</code> 按"
        f" (昵称, 等级标) 去重后的账号；"
        f"截图数 = 该账号在 <code>标记图/</code> 里的图片数（.jpg/.jpeg/.png/.bmp/.webp）。<br>"
        f"<b>占比说明</b>：「占撞库截图」= 该账号截图数 / 本次撞库截图总数 {hit_imgs}；"
        f"「占全部截图」= 该账号截图数 / 交付树全部截图数 {total_imgs}。<br>"
        f"<b>名称匹配</b>：精确同名 = 与名单一字不差；截断同名 = 归一化后相同或被包含"
        f"（如 <code>期待</code> ↔ <code>期待sj</code>）；疑似 = 相似度达到阈值但没到 1.0，建议人工复核。<br>"
        f"<b>在交付树 = 否</b>：该账号在直播里确实发过评论（评论/正面列），只是没达到交付阈值"
        f"（交付要 ≥3 条正面），标记图从视频按同一口径现补，形态与交付图一致；"
        f"零正面的账号按中性/负面评论补图。<br>"
        f"<b>撞库范围</b>：{_esc(scope_text)}；阈值 <b>{threshold:.2f}</b>；"
        f"名单 {list_count} 人 · 命中 {hit_names} 人 · 未命中 {len(un_rows)} 人 · "
        f"交付账号 {delivered} 个。<br>"
        f"日期：{when:%Y-%m-%d}　"
        f"结果目录：<code>{_esc(out_dir)}</code>"
    )

    body = f"""
  <div class="kpis">{kpis}</div>
  <div class="bars">{bars}</div>
{hit_table}{un_table}"""

    return _page(
        "撞库交付清单",
        f"{event} · {label}",
        [("日期", f"{when:%Y-%m-%d}"), ("场次", event), ("平台", label),
         ("阈值", f"{threshold:.2f}")],
        body,
        extra,
    )


# --------------------------------------------------------------------------- #
# 汇总页 + 落盘
# --------------------------------------------------------------------------- #

def build_index_html(entries: list[dict], *, event: str, when: date,
                     threshold: float, out_dir: Path) -> str:
    """多平台 → 一张汇总表 + 每个平台清单的链接。"""
    total_accounts = sum(e["accounts"] for e in entries)
    total_list = sum(e["list_count"] for e in entries)
    total_names = sum(e["matched_count"] for e in entries)
    total_hit = sum(e["matched_accounts"] for e in entries)
    total_imgs = sum(e["hit_imgs"] for e in entries)
    total_delivery_imgs = sum(e["total_imgs"] for e in entries)

    kpis = "".join([
        _kpi("检测出的总用户", str(total_accounts), "直播账号（三个平台合计）"),
        _kpi("名单人数", str(total_list), "活动方名单"),
        _kpi("撞库命中人数", str(total_names),
             f"命中账号 {total_hit} 个", "hl"),
        _kpi("撞库占比", f"{_pct(total_hit, total_accounts):.2f}%",
             "命中账号 / 直播账号", "hl"),
        _kpi("撞库用户截图数量", str(total_imgs), "标记图合计", "brand"),
    ])
    bars = (_bar("撞库用户占比（人数口径）", total_hit, total_accounts, "u") +
            _bar("撞库截图占比（图片口径）", total_imgs, total_delivery_imgs, "i"))

    trs = []
    for e in entries:
        url = _rel_url(e["file"].name)
        trs.append(
            f'<tr><td class="uname">{_esc(e["label"])}</td>'
            f'<td class="num">{e["list_count"]}</td>'
            f'<td class="num">{e["matched_count"]}</td>'
            f'<td class="num">{e["matched_accounts"]}</td>'
            f'<td class="num">{e["accounts"]}</td>'
            f'<td class="num">{_pct(e["matched_accounts"], e["accounts"]):.2f}%</td>'
            f'<td class="num">{e["hit_imgs"]}</td>'
            f'<td><a class="link" href="{_esc(url)}">打开清单 →</a></td></tr>')

    body = f"""
  <div class="kpis">{kpis}</div>
  <div class="bars">{bars}</div>
  <div class="panel">
    <div class="ph"><h2>各平台清单 · 共 {len(entries)} 个</h2></div>
    <div class="tblwrap">
    <table style="min-width:900px">
      <thead><tr><th>平台</th><th class="num">名单人数</th><th class="num">命中人数</th>
        <th class="num">命中账号</th><th class="num">直播账号</th><th class="num">命中占比</th>
        <th class="num">撞库截图</th><th>交付清单</th></tr></thead>
      <tbody>{chr(10).join(trs)}</tbody>
    </table>
    </div>
  </div>"""
    extra = (
        f"<b>统计口径</b>：各平台分开撞、分开统计；「撞库用户截图数量」是"
        f"<code>标记图/</code> 下全部图片张数（交付命中 + 仅直播现补）。<br>"
        f"<b>占比说明</b>：「撞库截图占比」= 撞库截图 {total_imgs} / 交付树全部截图 "
        f"{total_delivery_imgs}。<br>"
        f"阈值 <b>{threshold:.2f}</b>；日期：{when:%Y-%m-%d}　"
        f"清单目录：<code>{_esc(out_dir)}</code>"
    )
    return _page(
        "撞库交付清单 · 汇总",
        event,
        [("日期", f"{when:%Y-%m-%d}"), ("场次", event),
         ("平台", "、".join(e["label"] for e in entries)),
         ("阈值", f"{threshold:.2f}")],
        body,
        extra,
    )


def write_html(out_dir: Path, report: dict, log=print) -> dict:
    """写 ``<out_dir>/撞库清单/``：一平台一页；多平台再写 ``汇总.html``。

    返回 ``{"dir", "files": [Path…], "rows": […] }``（rows 供汇总页/打印用）。
    """
    inputs = report.get("inputs") or {}
    fallback = _gen_date(report)
    threshold = float(inputs.get("threshold") or 0.0)
    scope = str(inputs.get("scope") or "stream")
    has_images = (out_dir / "标记图").is_dir()

    forced_title = str(inputs.get("title") or "").strip()
    forced_date = str(inputs.get("date") or "").strip()
    forced_when = _infer_date([forced_date], fallback) if forced_date else None

    live_name = Path(str(inputs.get("live") or "")).name
    out = out_dir / "撞库清单"
    out.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    entries: list[dict] = []
    for label in sorted(report["platforms"], key=platform_sort_key):
        rep = report["platforms"][label]
        pdir = Path(rep["platform_dir"]) if rep.get("platform_dir") else None
        name = forced_title or _config_name(Path.cwd(), pdir)
        event = _clean_event(name) or live_name or "撞库"
        when = forced_when or _infer_date([name, live_name], fallback)

        _rows, hit_imgs, total_imgs = _platform_stats(
            out_dir, label, rep, has_images)

        fname = (f"{when:%Y%m%d}_{_slug(event)}_{label}.html"
                 if _slug(event) else f"{when:%Y%m%d}_{label}.html")
        path = out / fname
        path.write_text(
            build_platform_html(label, rep, out_dir, event=event, when=when,
                                threshold=threshold, scope=scope,
                                has_images=has_images),
            encoding="utf-8")
        files.append(path)
        entries.append({
            "label": label, "file": path, "event": event, "when": when,
            "accounts": int(rep.get("accounts_count") or 0),
            "list_count": int(rep.get("list_count") or 0),
            "matched_count": int(rep.get("matched_count") or 0),
            "matched_accounts": int(rep.get("matched_accounts") or 0),
            "hit_imgs": hit_imgs, "total_imgs": total_imgs,
        })
        log(f"[撞库] 交付清单：{label} → {path}")

    if len(entries) > 1:
        event = _clean_event(forced_title) or entries[0]["event"]
        when = forced_when or entries[0]["when"]
        index = out / "汇总.html"
        index.write_text(
            build_index_html(entries, event=event, when=when,
                             threshold=threshold, out_dir=out_dir),
            encoding="utf-8")
        files.append(index)
        log(f"[撞库] 交付清单：汇总 → {index}")

    return {"dir": out, "files": files, "rows": entries}
