"""撞库的输入：名单文件（xlsx / csv / txt）+ 交付树（``all_users_*``）。

名单格式（``10.31人名单.xlsx`` 就是这种）::

    序号 | 抖音     |        | 序号 | 视频号   |        | 序号 | 快手
    1    | 谯谯美食 |        | 1    | 威猛生活 |        | 1    | 派大星星
    2    | 我有三宝 |        | 2    | 桥豆妈   |        | 2    | 东方日出

平台表头下面（或右边）就是该平台的人名；``--layout auto`` 自己判断是「表头在上、名字在下」
还是「表头在左、名字在右」。纯文本名单（一行一个名字）用 ``--platform`` 指定平台。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .common import (
    dir_count,
    dir_username,
    is_number,
    normalize_name,
    platform_label,
    platform_sort_key,
    strip_level,
)

#: 表头行里除了平台名，常见还会有的列名（用来确认「这是表头，不是某个人的名字」）。
_HEADER_WORDS = {
    "序号", "编号", "姓名", "昵称", "名单", "账号", "平台", "备注",
    "name", "platform", "no", "no.", "id", "comment", "comments",
}


class SourceError(RuntimeError):
    """输入文件 / 目录不符合预期（报给人看，别吞掉）。"""


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _headerish_name(value: object) -> bool:
    """是不是「姓名 / 昵称 / name」这类表头词（无平台表头的普通表要跳过）。"""
    return _text(value).casefold().rstrip(":：") in _HEADER_WORDS


def _dedupe(entries: list[dict]) -> list[dict]:
    """同一平台里按归一化名字去重（名单里重复写同一个人只撞一次）。"""
    seen: set[str] = set()
    out: list[dict] = []
    for e in entries:
        key = normalize_name(e["name"])
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


# --------------------------------------------------------------------------- #
# 名单
# --------------------------------------------------------------------------- #

def read_rows(path: Path, sheet: str | None = None) -> list[list] | None:
    """读名单的二维表；``.txt`` 返回 ``None``（由调用方按行读）。"""
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError as e:  # pragma: no cover - 环境问题
            raise SourceError(
                "读 .xlsx 需要 openpyxl（pip install openpyxl）；"
                "也可以把名单另存为 .csv / .txt") from e
        try:
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        except Exception as e:  # noqa: BLE001 - 文件坏了要报清楚
            raise SourceError(f"打不开名单 {path}：{e}") from e
        if sheet:
            if sheet not in wb.sheetnames:
                raise SourceError(
                    f"名单里没有工作表 {sheet!r}（有：{'、'.join(wb.sheetnames)}）")
            ws = wb[sheet]
            return [list(r) for r in ws.iter_rows(values_only=True)]
        # 没指定工作表：挑第一个能找到平台表头的表；都找不到就用活动表
        for ws in wb.worksheets:
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            if _find_header(rows)[0] is not None:
                return rows
        ws = wb.worksheets[0] if wb.worksheets else None
        return [list(r) for r in ws.iter_rows(values_only=True)] if ws else []
    if suffix in (".csv", ".tsv"):
        delim = "\t" if suffix == ".tsv" else ","
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return [list(r) for r in csv.reader(f, delimiter=delim)]
    if suffix in (".txt", ".lst", ".names"):
        return None
    raise SourceError(
        f"不认识的名单后缀 {suffix!r}（支持 .xlsx / .csv / .tsv / .txt）")


def _find_header(rows: list[list]) -> tuple[int | None, list[tuple[int, str]]]:
    """找表头行：返回 ``(行号, [(列号, 平台名), …])``；找不到返回 ``(None, [])``。

    优先「有平台名 **且** 还有 序号/姓名/平台 之类表头词」的行 —— 免得把某个叫「抖音」
    的人名当成表头。退一步只要有平台名的第一行。
    """
    fallback: tuple[int, list[tuple[int, str]]] | None = None
    for i, row in enumerate(rows):
        labels = [(j, lab) for j, v in enumerate(row)
                  if (lab := platform_label(v)) is not None]
        if not labels:
            continue
        if fallback is None:
            fallback = (i, labels)
        has_word = any(_text(v).casefold() in _HEADER_WORDS for v in row)
        if has_word:
            return i, labels
    return fallback if fallback else (None, [])


def _pick_column(
    rows: list[list], header: int, col: int, layout: str,
) -> list[tuple[int, object]]:
    """选名字所在列：表头**下面**同一列（column），还是表头**右边**（row）。

    - ``down`` = 表头下面同一列（本批 xlsx 就是这种：``序号 | 抖音`` 下面一列是名字）。
    - ``right`` = 表头右边**同一行连续的非空单元格**（``抖音 | 小明 | 小红``），
      再加下面各行「表头右边那一列」（``序号 | 抖音`` / ``1 | 小明`` 这种两列表）。
    自动判断看哪边更像人名：非纯数字的单元格多的一边；都像数字（纯数字昵称）则看条数。
    """
    data = rows[header + 1:]
    right: list[tuple[int, object]] = []
    down: list[tuple[int, object]] = []
    # 表头同一行右边：连续非空到第一个空格为止（多平台并排时不会吃到隔壁平台）
    hrow = rows[header]
    for j in range(col + 1, len(hrow)):
        if not _text(hrow[j]):
            break
        right.append((header, hrow[j]))
    for k, row in enumerate(data):
        ri = header + 1 + k
        if col + 1 < len(row) and _text(row[col + 1]):
            right.append((ri, row[col + 1]))
        if col < len(row) and _text(row[col]):
            down.append((ri, row[col]))
    if layout == "row":
        return right
    if layout == "column":
        return down
    rn = sum(1 for _, v in right if not is_number(v))
    dn = sum(1 for _, v in down if not is_number(v))
    if rn > dn:
        return right
    if dn > rn:
        return down
    return down if len(down) > len(right) else (right or down)


def parse_name_table(rows: list[list], layout: str = "auto") -> dict[str, list[dict]]:
    """二维表 → ``{平台: [{"platform","name","row","seq"}]}``（行号 1 起，Excel 口径）。"""
    header, labels = _find_header(rows)
    if header is None:
        return {}
    out: dict[str, list[dict]] = {}
    for col, label in labels:
        bucket = out.setdefault(label, [])
        chosen = _pick_column(rows, header, col, layout)
        for ri, value in chosen:
            name = _text(value)
            if not name or _headerish_name(name):
                continue
            seq = None
            row = rows[ri]
            if col - 1 >= 0 and col - 1 < len(row) and is_number(row[col - 1]):
                seq = row[col - 1]
            bucket.append(
                {"platform": label, "name": name, "row": ri + 1, "seq": seq})
    return {k: _dedupe(v) for k, v in out.items()}


def _read_plain(rows: list[list]) -> list[dict]:
    """没有平台表头的普通表：挑非空最多的「非数字」列当名单。"""
    if not rows:
        return []
    ncols = max(len(r) for r in rows)
    best_col, best_vals = 0, []
    for c in range(ncols):
        vals = [(i, r[c]) for i, r in enumerate(rows)
                if c < len(r) and _text(r[c])]
        if sum(1 for _, v in vals if not is_number(v)) > sum(
                1 for _, v in best_vals if not is_number(v)):
            best_col, best_vals = c, vals
    return [{"platform": None, "name": _text(v), "row": i + 1, "seq": None}
            for i, v in best_vals if _text(v) and not _headerish_name(v)]


def read_name_list(
    path: Path,
    sheet: str | None = None,
    layout: str = "auto",
    platform: str | None = None,
) -> dict[str | None, list[dict]]:
    """名单文件 → ``{平台中文名 | None: [条目]}``。

    - 有平台表头：按平台分组；
    - 没有表头（或 ``.txt``）：全部放进 ``None`` 这一档，调用方再按 ``--platform`` /
      只有一个平台时补上；``platform`` 给了就直接落到该平台。
    """
    rows = read_rows(path, sheet=sheet)
    if rows is None:                              # .txt：一行一个
        entries = []
        with path.open("r", encoding="utf-8-sig") as f:
            for i, line in enumerate(f, 1):
                name = strip_level(line.strip())
                if name and not name.startswith("#") and not _headerish_name(name):
                    entries.append(
                        {"platform": platform, "name": name, "row": i, "seq": None})
        if not entries:
            raise SourceError(f"名单 {path} 是空的")
        return {platform: _dedupe(entries)}

    grouped = parse_name_table(rows, layout=layout)
    if grouped and not any(grouped.values()):
        got = "、".join(grouped)
        hint = ("--layout column（名字在表头下面）/ --layout row（名字在表头右边）"
                if layout == "auto"
                else ("--layout column（名字在表头下面）" if layout == "row"
                      else "--layout row（名字在表头右边）"))
        raise SourceError(
            f"名单里找到了平台表头（{got}），但按 --layout {layout} 一个人名都没读到；"
            f"换成 {hint} 再试")
    if not grouped:                               # 没有平台表头
        entries = _read_plain(rows)
        if not entries:
            raise SourceError(f"名单 {path} 里没有读到名字")
        if platform:
            for e in entries:
                e["platform"] = platform
            return {platform: _dedupe(entries)}
        return {None: _dedupe(entries)}

    if platform:
        if platform not in grouped:
            raise SourceError(
                f"名单里没有平台列 {platform!r}（读到：{'、'.join(grouped)}）")
        return {platform: grouped[platform]}
    return grouped


# --------------------------------------------------------------------------- #
# 交付树
# --------------------------------------------------------------------------- #

def load_manifest(tree: Path) -> dict | None:
    p = tree / "manifest.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SourceError(f"读不了 {p}：{e}") from e
    return data if isinstance(data, dict) else None


def manifest_platform(manifest: dict | None) -> str | None:
    if not manifest:
        return None
    for key in ("platform_dir", "platform"):
        lab = platform_label(manifest.get(key))
        if lab:
            return lab
    return None


def _trees_in(platform_dir: Path) -> list[Path]:
    """平台目录下的交付树（``all_users_*``）；平台目录自己就是树也算。"""
    trees = [p for p in sorted(platform_dir.glob("all_users_*")) if p.is_dir()]
    if not trees and (platform_dir / "manifest.json").is_file():
        trees = [platform_dir]
    return trees


def discover_platforms(
    live_root: Path,
    tree: Path | None = None,
    platform: str | None = None,
    only: set[str] | None = None,
) -> list[dict]:
    """直播产物根目录 → ``[{"label","dir","tree","manifest"}]``。

    ``live_root`` 是 ``out/10.31`` 这种「下面按平台分目录」的；也接受直接给某棵交付树
    （``--tree`` 或路径本身就是 ``all_users_*``）。每个平台必须恰好一棵树：找到多棵
    （不同阈值各一棵）就报错，请用 ``--tree`` 指定 —— 静默挑一棵会撞错名单。
    """
    if tree is not None:
        t = Path(tree).resolve()
        if not t.is_dir():
            raise SourceError(f"找不到交付树目录 {t}")
        manifest = load_manifest(t)
        label = (platform or manifest_platform(manifest)
                 or platform_label(t.parent.name) or t.parent.name)
        if only and label not in only:
            raise SourceError(f"--platform 只允许 {('、'.join(sorted(only)))}，"
                              f"但 --tree 是 {label}")
        return [{"label": label, "dir": t.parent, "tree": t, "manifest": manifest}]

    live_root = Path(live_root).resolve()
    if not live_root.is_dir():
        raise SourceError(f"找不到直播产物目录 {live_root}")
    if live_root.name.startswith("all_users_") and (
            live_root / "manifest.json").is_file():
        manifest = load_manifest(live_root)
        label = (platform or manifest_platform(manifest)
                 or platform_label(live_root.parent.name) or live_root.parent.name)
        return [{"label": label, "dir": live_root.parent, "tree": live_root,
                 "manifest": manifest}]

    found: list[dict] = []
    for child in sorted(live_root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        label = platform_label(child.name)
        if label is None:
            continue
        if only and label not in only:
            continue
        trees = _trees_in(child)
        if not trees:
            continue
        if len(trees) > 1:
            with_manifest = [t for t in trees if (t / "manifest.json").is_file()]
            if len(with_manifest) == 1:
                trees = with_manifest
            else:
                listing = "\n".join(f"  - {t}" for t in trees)
                raise SourceError(
                    f"{label} 下找到多棵交付树，不知道撞哪棵（用 --tree 指定）：\n{listing}")
        t = trees[0]
        found.append({"label": label, "dir": child, "tree": t,
                      "manifest": load_manifest(t)})

    if only:
        missing = sorted(only - {x["label"] for x in found})
        if missing:
            raise SourceError(
                f"直播产物里没有这些平台的交付树：{'、'.join(missing)}"
                f"（在 {live_root} 下找 <平台>/all_users_*）")
    if not found:
        raise SourceError(
            f"{live_root} 下没找到任何 <平台>/all_users_* 交付树"
            "（先跑 export / collect）")
    found.sort(key=lambda x: platform_sort_key(x["label"]))
    return found


def load_accounts(tree: Path, manifest: dict | None = None) -> list[dict]:
    """交付树 → 账号列表（优先 ``manifest.json``，没有就扫目录）。

    每条：``username``（原始昵称）/ ``level``（等级标，可能 ``""``）/ ``dir`` /
    ``count`` / ``segs`` / ``first_image`` / ``path``。等级标不参与撞库（名单不会写 ◆N），
    但保留在报告里，好区分「同名不同人」的档。
    """
    if manifest is None:
        manifest = load_manifest(tree)
    accts: list[dict] = []
    if manifest and isinstance(manifest.get("users"), list):
        for u in manifest["users"]:
            if not isinstance(u, dict):
                continue
            username = str(u.get("username") or "").strip()
            if not username:
                continue
            d = str(u.get("dir") or "").strip()
            images = u.get("images") if isinstance(u.get("images"), list) else []
            first = ""
            if images and isinstance(images[0], dict):
                first = str(images[0].get("rel") or "")
            count = u.get("count")
            if not isinstance(count, int):
                count = len(images) or None
            accts.append({
                "username": username,
                "level": str(u.get("level") or "").strip(),
                "dir": d,
                "count": count,
                "segs": u.get("segs") if isinstance(u.get("segs"), dict) else {},
                "first_image": first,
                "path": (tree / d) if d else None,
                "in_delivery": True,
                "n_comments": None,
                "n_positive": None,
            })
        if accts:
            return accts

    # 没有 manifest（或 users 为空）：扫交付目录，目录名 = <账号>-<条数>
    for p in sorted(tree.iterdir(), key=lambda x: x.name):
        if not p.is_dir() or p.name.startswith(".") or p.name == "frames":
            continue
        username = dir_username(p.name)
        if not username:
            continue
        accts.append({
            "username": username,
            "level": "",
            "dir": p.name,
            "count": dir_count(p.name),
            "segs": {},
            "first_image": "",
            "path": p,
            "in_delivery": True,
            "n_comments": None,
            "n_positive": None,
        })
    if accts:
        return accts

    # 最后兜底：正面账号.txt（一行一个；带 ◆N 的剥掉等级标再撞）
    txt = tree / "正面账号.txt"
    if txt.is_file():
        for line in txt.read_text(encoding="utf-8").splitlines():
            name = strip_level(line.strip())
            if name:
                accts.append({
                    "username": name, "level": "", "dir": "", "count": None,
                    "segs": {}, "first_image": "", "path": None,
                    "in_delivery": True, "n_comments": None, "n_positive": None,
                })
    return accts


# --------------------------------------------------------------------------- #
# 直播全量账号（不要求进交付树）
# --------------------------------------------------------------------------- #

def stream_segments(platform_dir: Path) -> list[Path]:
    """平台目录下能给出全量账号的段目录（``seg1`` …，按名字排序）。

    ``comments.jsonl``（parse 产物，**全部评论**）优先；老产物只有 ``users.jsonl``
    （filter 产物，≥``min_positive`` 条正面的账号）也算一段。
    """
    return sorted(
        p for p in Path(platform_dir).glob("seg*")
        if p.is_dir()
        and ((p / "comments.jsonl").is_file() or (p / "users.jsonl").is_file()))


def _account_key(username: str, level: str = "") -> tuple[str, str]:
    return (username, str(level or ""))


def load_judgments(platform_dir: Path) -> dict[str, str]:
    """平台共用的判定缓存（``<platform_dir>/judgments.jsonl``）→ ``{sha1: 标签}``。

    与 ``filter`` 共用同一个文件（配置 ``filter.cache`` 默认 null ⇒
    ``<output.dir>/judgments.jsonl``）。读不到就是空表 —— 账号照撞，
    只是正面数按 filter 的口径算「中性」。
    """
    path = Path(platform_dir) / "judgments.jsonl"
    cache: dict[str, str] = {}
    if not path.is_file():
        return cache
    for ln, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError as e:
            raise SourceError(f"{path}:{ln} 不是合法 JSON：{e}") from e
        if isinstance(d, dict) and d.get("k"):
            cache[str(d["k"])] = str(d.get("label") or "中性")
    return cache


def _seg_identity(seg: Path) -> tuple[list[str], set[str], set[str]]:
    """段目录 ``users.json`` → (排除词, 按等级认人的面板, 打码才按等级认人的面板)。

    撞库重建账号身份必须与 filter 用**同一个口径**（面板/排除词都记在这份汇总里），
    否则同一个人会被拆成两个键、或者把官方号撞进名单。
    """
    p = Path(seg) / "users.json"
    if not p.is_file():
        return [], set(), set()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], set(), set()
    if not isinstance(d, dict):
        return [], set(), set()
    keywords = [str(k) for k in (d.get("exclude_keywords") or []) if str(k)]
    panels = {str(s) for s in (d.get("identity_panels") or []) if str(s)}
    masked = {str(s) for s in (d.get("identity_masked") or []) if str(s)}
    return keywords, panels, masked


def load_seg_accounts(seg: Path, cache: dict[str, str] | None = None,
                      ) -> dict[tuple[str, str], dict]:
    """一段的**全部评论账号** → ``{(昵称, 等级标): 用户记录}``。

    优先 ``comments.jsonl``（parse 产物）：**发过任意一条评论就算**（正面/负面/中性
    不设门槛），每条评论的标签按判定缓存 ``cache`` 回填（缓存里没有的按 filter 口径
    当「中性」）。没有 ``comments.jsonl`` 的旧产物退回 ``users.jsonl``
    （只有 ≥``min_positive`` 条正面的账号）。
    """
    seg = Path(seg)
    cache = cache or {}
    comments_path = seg / "comments.jsonl"
    if comments_path.is_file():
        from ..user_filter.cache import _key as judge_key
        from ..user_filter.comments import load_comments
        from ..user_filter.group import group_accounts

        keywords, panels, masked = _seg_identity(seg)
        by_user, _anon, _excluded = group_accounts(
            load_comments(str(comments_path)), keywords, panels, masked)
        out: dict[tuple[str, str], dict] = {}
        for key, cs in by_user.items():
            rows = []
            for c in cs:
                lab = cache.get(judge_key(c.get("content") or "",
                                          c.get("emoji_text") or ""), "中性")
                rows.append({"t": c.get("t"), "panel": c.get("panel"),
                             "label": lab, "content": c.get("content"),
                             "emoji_text": c.get("emoji_text"),
                             "roi": c.get("roi")})
            out[key] = {
                "username": key[0],
                "level": key[1],
                "n_comments": len(rows),
                "n_positive": sum(1 for r in rows if r["label"] == "正面"),
                "n_negative": sum(1 for r in rows if r["label"] == "负面"),
                "n_neutral": sum(1 for r in rows if r["label"] == "中性"),
                "comments": rows,
            }
        return out

    users_path = seg / "users.jsonl"
    out = {}
    if not users_path.is_file():
        return out
    for ln, line in enumerate(users_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            u = json.loads(line)
        except json.JSONDecodeError as e:
            raise SourceError(f"{users_path}:{ln} 不是合法 JSON：{e}") from e
        if not isinstance(u, dict):
            continue
        username = str(u.get("username") or "").strip()
        if not username:
            continue
        out[_account_key(username, u.get("level"))] = u
    return out


def load_stream_accounts(platform_dir: Path) -> list[dict]:
    """直播里**全部**有评论的账号（跨段合并，不看交付阈值，也不看正负面）。

    数据源是每段 ``comments.jsonl``（parse 产物）：**发过任意一条评论**（正面/负面/
    中性）就有资格被撞出来。老产物没有 ``comments.jsonl`` 时退回 ``seg*/users.jsonl``
    （只有 ≥1 条正面的账号）。账号身份 = ``(昵称, 等级标)``，与 filter 同口径；
    ``n_positive`` 按平台共用的 ``judgments.jsonl`` 判定缓存回填。

    返回条目与 :func:`load_accounts` 同形，外加 ``n_comments`` / ``n_positive``；
    ``dir`` 为空、``in_delivery=False``（还没和交付树合并）。
    """
    merged: dict[tuple[str, str], dict] = {}
    cache = load_judgments(platform_dir)
    for seg in stream_segments(platform_dir):
        for key, u in load_seg_accounts(seg, cache).items():
            acc = merged.get(key)
            if acc is None:
                acc = merged[key] = {
                    "username": key[0],
                    "level": key[1],
                    "dir": "",
                    "count": None,
                    "segs": {},
                    "first_image": "",
                    "path": None,
                    "in_delivery": False,
                    "n_comments": 0,
                    "n_positive": 0,
                }
            npos = int(u.get("n_positive") or 0)
            acc["n_comments"] += int(u.get("n_comments") or 0)
            acc["n_positive"] += npos
            acc["segs"][seg.name] = acc["segs"].get(seg.name, 0) + npos
    return sorted(merged.values(),
                  key=lambda a: (-int(a["n_positive"] or 0), -int(a["n_comments"] or 0),
                                 a["username"], a["level"]))


def merge_accounts(delivered: list[dict], stream: list[dict]) -> list[dict]:
    """交付账号 ∪ 直播账号：交付条目优先（保留 ``dir``），直播条目补 ``n_comments``。

    交付树里的每个账号都应在直播全量池里（同一批数据、同一身份键），
    这里按 ``(昵称, 等级标)`` 对上就补评论数；对不上的交付账号原样保留。
    """
    out: dict[tuple[str, str], dict] = {}
    for a in delivered:
        item = dict(a)
        item["in_delivery"] = True
        out[_account_key(item["username"], item.get("level"))] = item
    for s in stream:
        key = _account_key(s["username"], s.get("level"))
        if key in out:
            out[key]["n_comments"] = s.get("n_comments")
            out[key]["n_positive"] = s.get("n_positive")
            if s.get("segs"):
                out[key]["segs"] = s["segs"]
        else:
            out[key] = dict(s)
    return sorted(out.values(),
                  key=lambda a: (-int(a.get("n_positive") or 0), a["username"],
                                 str(a.get("level") or ""), a.get("dir") or ""))
