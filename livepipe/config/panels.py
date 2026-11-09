"""livepipe.config.panels"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import sys

from .deps import account_id
from .errors import ConfigError
from .io import _parse_json
from .paths import segments



# --------------------------------------------------------------------------- #
# 面板 manifest：{面板名: {rect, platform, …}} —— **人工标注平台的地方**
# --------------------------------------------------------------------------- #

def _panel_entry(v: Any) -> dict:
    """把面板条目的两种写法归一成 dict：``[x1,y1,x2,y2]`` 或 ``{"rect": …}``。"""
    if isinstance(v, (list, tuple)):
        return {"rect": [int(round(float(x))) for x in v]}
    if isinstance(v, dict):
        e = dict(v)
        if "rect" in e:
            e["rect"] = [int(round(float(x))) for x in e["rect"]]
        return e
    raise ConfigError(
        '面板条目要写成 [x1,y1,x2,y2] 或 {"rect": …}，实际是 '
        f"{type(v).__name__}")


def read_manifest(path: str) -> tuple[dict[str, dict], dict]:
    """读面板文件（定位产物或手写），返回 ``({面板名: 条目}, 其余元信息)``。"""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"找不到面板文件：{p}")
    data = _parse_json(p.read_text(encoding="utf-8"), p)
    if not isinstance(data, dict):
        raise ConfigError(f"面板文件的根节点要是对象：{p}")
    raw = data.get("panels", data)
    if not isinstance(raw, dict) or not raw:
        raise ConfigError(f"面板文件里没有 panels（或为空）：{p}")
    meta = {k: v for k, v in data.items() if k != "panels"}
    return {k: _panel_entry(v) for k, v in raw.items()}, meta


def manifest_drift(entries: dict[str, dict], path: str) -> list[str]:
    """手写面板（配置里的）与**已落盘**面板文件的差异；为空 = 一致，文件不存在也返回空。

    只比 ``entries`` 里写了的键（rect / platform 等）—— 文件里多出来的键不算差异
    （人工在面板文件里补的覆盖项要保住）。用于 ``panels.mode=manual`` 下把配置当唯一
    来源：不一致时面板步按配置重写，描框时也要知道文件已经落后。
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        have, _meta = read_manifest(str(p))
    except ConfigError as e:
        return [f"{p} 读不出来：{e}"]
    drift: list[str] = []
    for name, want in entries.items():
        got = have.get(name)
        if got is None:
            drift.append(f"{name}: 配置里有，面板文件里没有")
            continue
        for k, v in want.items():
            if got.get(k) != v:
                drift.append(f"{name}.{k}: 配置={v!r} 文件={got.get(k)!r}")
    for name in have:
        if name not in entries:
            drift.append(f"{name}: 只在面板文件里（配置的 panels.manual 没有）")
    return drift


def write_manifest(cfg: dict, path: str, panels: dict[str, dict],
                   extra: dict | None = None) -> Path:
    """写面板文件（人工编辑的那个）。每个面板留一个 ``platform`` 待填。"""
    dst = Path(path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    known = "、".join(cfg["platforms"])
    tail = ("填完直接重跑管线即可：本文件不会被覆盖（panels.overwrite=false）。"
            if cfg["panels"]["mode"] != "manual" else
            "本文件是配置 panels.manual 的落盘副本：改配置后重跑管线会按配置重写它。")
    payload: dict = {
        "_note": [
            "★ 人工填写每个面板的 platform ★",
            f"可用平台：{known}（各自的处理方式见配置里的 platforms 段）。",
            "平台决定行距与表情表，填错整片认不出，所以不自动猜。",
            tail,
        ],
        "panels": panels,
    }
    if extra:
        payload.update(extra)
    dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst


def _window_right(cfg: dict, name: str, meta: dict | None) -> int | None:
    """面板所在窗口的右沿（原图像素）—— 定位产物里有现成的，手写时看 frame_width。"""
    win = ((meta or {}).get("_locate_meta") or {}).get(name, {}).get("window")
    if isinstance(win, (list, tuple)) and len(win) == 4:
        return int(win[2])
    fw = cfg["panels"]["auto"].get("frame_width")
    return int(fw) if fw else None


def normalize_panels(cfg: dict, entries: dict[str, dict], *, where: str,
                     meta: dict | None = None) -> dict[str, dict]:
    """按平台档案补全行网格（row_pitch / y_phase / x_text）与裁切策略，并校验 platform 已填。"""
    out: dict[str, dict] = {}
    known = "、".join(cfg["platforms"])
    for name, e in entries.items():
        if "rect" not in e:
            raise ConfigError(f"{where} 里的面板 {name!r} 缺 rect（写成 [x1,y1,x2,y2]）")
        plat = e.get("platform")
        if not plat:
            raise ConfigError(
                f"{where} 里的面板 {name!r} 还没填 platform —— 这正是要人工补的那一步。\n"
                f'  打开该文件，给它加 "platform": "<平台名>"；可用：{known}\n'
                "  （平台决定行距与表情表，填错整片认不出，所以必须人工确认，不自动猜。）")
        prof = cfg["platforms"].get(plat)
        if prof is None:
            raise ConfigError(
                f"面板 {name!r} 的 platform={plat!r} 不在 platforms 里；可用：{known}")
        merged = dict(e)
        for k in ("row_pitch", "x_text", "y_phase"):
            merged.setdefault(k, prof.get(k))
        # 版式相关的可选覆盖：一般**不用写** —— 解析阶段会从采样帧自标定（行距、续行间距比、
        # 浮层右界）；写了就以档案/面板里的值为准（自标定只在证据足够时覆盖）。
        # rank_pill 不走自标定：药丸是不是「跟在昵称右边」是**版式事实**，量不出来就照抖音那套
        # 解析（会把它并进用户名），所以只有平台档案/面板条目显式写了才开。
        # wrap_at_text_left 同理：**有没有左侧徽章列**是版式事实（微博没有 ⇒ 折行跟首行同
        # 左沿，形状判据失效，得走间距入口，见 parse_live_comments 的 ``group_rows``）。
        # badge_alias 也走这条：行首等级药丸会不会被 OCR 并进正文是**版式事实**
        # （见 parse_live_comments 的 normalize_badge_alias / CONFIG.md 的同名小节）。
        # username_alias 同理：这条版式的昵称末尾会不会被检测框切掉一截/一个字，是
        # **版式事实**（见 normalize_username_alias；同一个人被拆成两个账号）。
        # overlay_cards 同理：评论列表上会不会压**浮层卡**（公告卡/主题卡/公众号卡/广告气泡）
        # 是版式事实（见 parse_live_comments 的 _OVERLAY_CARD_RE）。
        # account_key 是**交付事实**：这个版式的昵称唯不唯一（昵称打码 + 粉丝团药丸的
        # 抖音 App ⇒ 按 (昵称, 等级标) 认人，见 account_id）。也走档案，因为它同样是
        # 「这个平台长什么样」决定的，不该让交付那几步各自写死一个平台名。
        # colon_* 是**像素找冒号的阈值**（见 parsing/ocr/colon.py 的 colon_box_by_pixels）：
        # 默认值是给暗底白字量的；浅灰气泡底（直播画面亮、气泡半透明）要让像素法能工作，
        # 得放开 bg_max_v、把 v_delta 调低。不写这三个键的平台与旧行为逐字节相同。
        # colon_text_fix_masked_only：冒号被 OCR 吃掉时只按**打码星号锚点**把「：」补回
        # 文本（视频号昵称后紧跟的标点常被一起吃掉，按字宽等分会偏一格）；默认关。
        for k in ("wrap_gap_ratio", "wrap_indent", "wrap_at_text_left",
                  "x_ignore_from", "rank_pill", "fan_badge_x_max", "badge_alias",
                  "username_alias", "overlay_cards", "account_key", "badge_span",
                  "fan_badge_with_level", "notice_prefix_strip", "badge_name_recover",
                  "strip_phrases", "skip_phrases", "merge_emoji_continuation", "emoji_gap_sweep",
                  "colon_bg_max_v", "colon_v_min", "colon_v_delta", "colon_text_fix",
                  "colon_text_fix_masked_only"):
            if k in prof:
                merged.setdefault(k, prof[k])
        miss = [k for k in ("row_pitch", "x_text", "y_phase") if merged.get(k) is None]
        if miss:
            raise ConfigError(
                f"面板 {name!r}（平台 {plat}）的行网格缺 {miss}："
                f"在 platforms.{plat} 里补，或在该面板条目里单独写")
        merged["platform"] = plat
        # 裁切右沿策略：面板自己写的优先，其次平台档案。
        policy = str(e.get("x2_from") or prof.get("x2_from") or "content")
        if policy not in ("content", "window"):
            raise ConfigError(
                f"面板 {name!r} 的 x2_from={policy!r} 不合法；只能是 content 或 window")
        merged["x2_from"] = policy
        if policy == "window":
            wr = _window_right(cfg, name, meta)
            if wr is None:
                print(f"[config][warn] 面板 {name!r} 的 x2_from=window，但推不出窗口右沿"
                      f"（定位产物没有 _locate_meta，也没设 panels.auto.frame_width）"
                      f"⇒ 裁切右沿保持 {merged['rect'][2]}", file=sys.stderr)
            else:
                merged["rect"] = [merged["rect"][0], merged["rect"][1],
                                  int(wr), merged["rect"][3]]
        out[name] = merged
    return out


def panel_emoji_spec(cfg: dict, panels: dict[str, dict]) -> str:
    """``{面板: {platform}}`` → ``left=<path>,right=<path>``（平台没配 emoji 的跳过）。"""
    if not cfg["emoji"]["enabled"]:
        return ""
    tables = cfg["emoji"]["tables"]
    parts = []
    for name, p in panels.items():
        src = (cfg["platforms"].get(p["platform"]) or {}).get("emoji")
        if not src:
            continue
        t = tables.get(src)
        if t is None:
            raise ConfigError(
                f"平台 {p['platform']!r} 的 emoji={src!r}，但 emoji.tables 里没有这个源")
        parts.append(f"{name}={t['path']}")
    return ",".join(parts)


def manual_entries(cfg: dict, seg: dict | None = None) -> dict:
    """``panels.manual`` → 该段的面板表。

    单视频写成字典（``{面板名: {...}}``）；多段写成**数组**（一段一项，顺序 = 段顺序）。
    """
    man = cfg["panels"]["manual"]
    if isinstance(man, (list, tuple)):
        i = (seg or {}).get("i", 0)
        if i >= len(man):
            raise ConfigError(f"panels.manual 只有 {len(man)} 项，"
                              f"但段 {seg.get('name') if seg else i} 需要第 {i + 1} 项")
        return dict(man[i] or {})
    return dict(man or {})


def segment_files(cfg: dict, seg: dict | None = None) -> tuple[str, str, str]:
    """该段的 ``(面板文件, 评论文件, 合格用户文件)``（多段下自动落到段目录里）。"""
    s = seg or segments(cfg)[0]
    d = Path(s["out"])
    src = cfg["filter"]["source"] or str(d / "comments.jsonl")
    usr = cfg["filter"]["out"] or str(d / "users.jsonl")
    return s["panels_file"], src, usr


def _identity_panels(cfg: dict, seg: dict | None, modes: tuple[str, ...]) -> list[str]:
    """该段里 ``account_key`` 属于 ``modes`` 的面板名（面板 → 平台读 panels.resolved.json）。"""
    s = seg or segments(cfg)[0]
    for p in (Path(s["out"]) / "panels.resolved.json", Path(s["panels_file"])):
        if not p.exists():
            continue
        try:
            entries, _meta = read_manifest(str(p))
        except ConfigError:
            continue
        out = []
        for name, e in entries.items():
            plat = str(e.get("platform") or "")
            prof = cfg["platforms"].get(plat) or {}
            mode = e.get("account_key") if e.get("account_key") is not None \
                else prof.get("account_key")
            if str(mode or account_id.KEY_USERNAME) in modes:
                out.append(str(name))
        if out:
            return sorted(out)
    return []


def identity_panels(cfg: dict, seg: dict | None = None) -> list[str]:
    """该段里**要按 (昵称, 等级标) 认账号**的面板名（见 :mod:`livepipe.account_id`）。

    包含两种口径的面板：``nickname_level``（一律按等级拆）与
    ``nickname_level_if_masked``（只有打码昵称按等级拆，集合见
    :func:`masked_identity_panels`）。判据是平台档案的 ``account_key``；
    面板 → 平台 这份对应关系读 ``panels.resolved.json``（面板步的产物，档案键已经并进去了）。
    读不到就退回 manifest（``panels.json``）—— 它只有人工标注的 ``platform``，
    但这一步要的**就是**平台名，够用。
    两处都没有（比如只跑 filter、面板文件被删了）⇒ 空表 = 只看昵称，与旧行为一致。
    """
    modes = (account_id.KEY_NICKNAME_LEVEL, account_id.KEY_NICKNAME_LEVEL_IF_MASKED)
    out = _identity_panels(cfg, seg, modes)
    if out:
        return out
    # 面板文件读不到（老语料只补跑 filter、或 panels 步还没跑）⇒ 这里静默退回「只看昵称」，
    # 等于把档案里声明的交付口径**悄悄换掉**（名单会变，日志里一个字都没有）。所以吵一声。
    declared = sorted(p for p, prof in (cfg["platforms"] or {}).items()
                      if isinstance(prof, dict)
                      and str(prof.get("account_key") or "") in modes)
    if declared:
        s = seg or segments(cfg)[0]
        print(f"[config][warn] {'/'.join(declared)} 声明了 account_key="
              f"nickname_level(/if_masked)，但面板文件读不到"
              f"（{Path(s['out']) / 'panels.resolved.json'} 不存在？）⇒ 本步退回**只看昵称**，"
              f"交付名单与配置声明的口径不一致；先跑 panels 步再来。", file=sys.stderr)
    return []


def masked_identity_panels(cfg: dict, seg: dict | None = None) -> list[str]:
    """该段里 ``account_key="nickname_level_if_masked"`` 的面板（只有打码昵称按等级拆）。"""
    return _identity_panels(cfg, seg, (account_id.KEY_NICKNAME_LEVEL_IF_MASKED,))


def level_identity_declared(cfg: dict, seg: dict | None = None) -> bool:
    """这份配置里有没有面板按 **(昵称, 等级标)** 认人 —— 决定等级标二次读数要不要开。

    先看该段面板文件（面板级 ``account_key`` 也能声明），读不到/没有再看平台档案。
    只回答「认人用不用等级」，不读交付产物、不打印告警。见 :mod:`livepipe.account_id`
    与 ``parse.badge_reread``：按昵称认人的配置不需要回原帧读药丸（读了也不参与身份）。
    """
    modes = (account_id.KEY_NICKNAME_LEVEL, account_id.KEY_NICKNAME_LEVEL_IF_MASKED)
    if _identity_panels(cfg, seg, modes):
        return True
    return any(isinstance(prof, dict)
               and str(prof.get("account_key") or "") in modes
               for prof in (cfg.get("platforms") or {}).values())


def resolve_panels(cfg: dict, *, write: bool = True,
                   seg: dict | None = None) -> tuple[str | None, str]:
    """面板配置 → ``(--panels-file 用的文件, --emoji-table 的取值)``。

    吃 manifest 的模式（reuse / locate / manual）把人工标注的平台**展开成完整行网格**，
    写出 ``<段目录>/panels.resolved.json`` 供 ``--panels-file`` 用；
    auto / builtin / batch 不吃 manifest，平台按 ``panels.auto.platforms`` 归属。
    """
    pn = cfg["panels"]
    s = seg or segments(cfg)[0]
    f = s["panels_file"]
    if pn["mode"] in ("reuse", "locate", "manual"):
        entries, meta = read_manifest(f)
        panels = normalize_panels(cfg, entries, where=str(f), meta=meta)
        spec = panel_emoji_spec(cfg, panels)
        dst = Path(s["out"]) / "panels.resolved.json"
        if write:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(json.dumps({"panels": panels}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        return str(dst), spec
    mapped = {n: {"platform": p} for n, p in pn["auto"]["platforms"].items()
              if p in cfg["platforms"]}
    return None, (panel_emoji_spec(cfg, mapped) if mapped else "")


# --------------------------------------------------------------------------- #
# 自检（不读盘：段目录指向不存在的路径，只看平台档案那一路）
# --------------------------------------------------------------------------- #

def selftest() -> int:
    n = 0

    def eq(got, want, what: str) -> None:
        nonlocal n
        n += 1
        assert got == want, f"{what}: 得到 {got!r}，期望 {want!r}"

    seg = {"out": "/nonexistent/livepipe-selftest", "panels_file": "/nonexistent/p.json"}

    def cfg(*modes: str) -> dict:
        return {"platforms": {f"p{i}": {"account_key": m} for i, m in enumerate(modes)}}

    # 认人口径 ⇒ 等级标二次读数：只看昵称一律不开
    eq(level_identity_declared(cfg("username"), seg), False, "只看昵称 ⇒ 不开")
    eq(level_identity_declared({"platforms": {}}, seg), False, "没有平台 ⇒ 不开（旧行为）")
    # 只要有一个面板按 (昵称, 等级) 认人就得开
    eq(level_identity_declared(cfg("nickname_level"), seg), True, "nickname_level ⇒ 开")
    eq(level_identity_declared(cfg("nickname_level_if_masked"), seg), True,
       "nickname_level_if_masked ⇒ 开（打码名仍要等级）")
    eq(level_identity_declared(cfg("username", "nickname_level"), seg), True,
       "混写：任一面板用等级 ⇒ 开")
    # 平台档案里没写 account_key 等于 username
    eq(level_identity_declared(cfg(""), seg), False, "没写 account_key ⇒ 按 username")
    eq(level_identity_declared({"platforms": {"p": None}}, seg), False,
       "平台值不是 dict ⇒ 当没声明")

    print(f"[selftest] 等级标二次读数绑定（account_key）{n} 项断言通过 ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
