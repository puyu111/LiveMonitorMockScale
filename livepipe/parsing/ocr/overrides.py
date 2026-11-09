"""livepipe.parsing.ocr.overrides — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from pathlib import Path
import json
import re

from ..ocr.grid import DEFAULT_PANELS



def _apply_panel_override(panels: dict, override: dict) -> dict:
    """把 ``{名: rect}`` 或 ``{名: {字段…}}`` 合并进内置面板（就地改 + 返回）。

    允许只给 rect：``row_pitch`` / ``y_phase`` / ``x_text`` 从同名内置面板补齐，所以
    自动定位的结果能和手写行网格混用（行距是面板自己的属性，不随裁切变）。
    """
    for name, cfg in override.items():
        if isinstance(cfg, list):          # 直接给矩形
            cfg = {"rect": cfg}
        base = panels.get(name, {})
        merged = dict(base)
        merged.update(cfg)
        # 坐标一律归整：自动定位（``locate_panels_auto`` 的 hybrid/ocr）给出的是**浮点**
        # （733.0 这种，来自 OCR 框的并集），不转 int 会直接崩在 numpy 切片上。
        if "rect" in merged:
            merged["rect"] = [int(round(float(v))) for v in merged["rect"]]
        for k in ("y_phase", "x_text", "x_ignore_from", "badge_span"):
            if merged.get(k) is not None:      # 显式 null = 关闭该开关，不要 float(None)
                merged[k] = int(round(float(merged[k])))
        # ``row_pitch`` 是**例外：必须留小数**。它不是坐标，是「槽位 = 相位 + 槽号 × 行距」里
        # 逐行**累加**的间距 —— 手机 App 版实测 53.5，归整成 54 之后每往下一行就多偏 0.5px，
        # 二十行就是 10px（0.19 个行距）。这点漂移叠上「整帧只有一个相位」的误差，网格线正好
        # 落到两行**中间**时，两行各自都偏不到半个行距 ⇒ 一起归进同一槽，``assemble_row``
        # 按 x 拼文字、框取并集 ⇒ 两条评论粘成一行（用户报的 t=107.2 第 7 条）。seg4 实测
        # 十八帧：53.5 → **0** 个并块；54 → 11/161 行（6.8%）。所以只保证一位小数即可，
        # 不再归整（``y_phase`` 也不能这么算：它由 ``estimate_phase`` 逐帧重估）。
        if "row_pitch" in merged:
            merged["row_pitch"] = round(float(merged["row_pitch"]), 1)
        if "wrap_gap_ratio" in merged:
            merged["wrap_gap_ratio"] = float(merged["wrap_gap_ratio"])
        # ``x_text``（徽章列右沿）是**裁切内坐标**，换矩形就会失效：徽章列在原图里的
        # 绝对位置是固定的，矩形左沿一动，同一个徽章在新裁切里的局部 x 就变了。
        # 实测 hybrid 矩形 x1 比手写小 23px，右面板徽章局部 x 从 54 变 74 > x_text=60，
        # 于是徽章块逃过 `_in_badge_gutter` 被拼进正文，用户名成了 `13打酱油`。
        # 所以覆盖了 rect 又没显式给 x_text 时，按**绝对锚点**重算局部值。
        if "rect" in cfg and "x_text" not in cfg and "rect" in base and "x_text" in base:
            anchor = base["rect"][0] + base["x_text"]      # 徽章列右沿（原图像素）
            merged["x_text"] = int(round(anchor - merged["rect"][0]))
        panels[name] = merged
    return panels


def resolve_panels(panels_file: str | None = None,
                   rects: dict | None = None) -> dict:
    """内置面板 + 覆盖。两个来源可以只给 rect：``rects``（``--auto-panels`` 现场定位的
    结果）和 ``panels_file``（JSON 文件，``--dump-panels`` 的产出或手写面板）。

    优先级：``panels_file`` > ``rects`` > 内置 ``DEFAULT_PANELS``。两条一起给是写法错误，
    由 CLI 拦（``--auto-panels`` 与 ``--panels-file`` 二选一）。
    """
    panels = {k: dict(v) for k, v in DEFAULT_PANELS.items()}
    if panels_file:
        override = json.loads(Path(panels_file).read_text())
        # 兼容 ``--dump-panels`` 的输出形态（{"panels": {name: [x1,y1,x2,y2]}}）。
        # 值也可能是带 rect 的字典（手工写的面板文件），那就原样用。
        if "panels" in override and isinstance(override["panels"], dict):
            override = {n: ({"rect": b} if isinstance(b, list) else b)
                        for n, b in override["panels"].items()}
        _apply_panel_override(panels, override)
        # 面板文件是**面板集合**的来源：文件里没提到的内置面板一律丢掉。否则单面板录屏
        # （竖屏单直播间）会带进内置 ``right``，其 rect 落在帧外 ⇒ OCR 裁切宽 0 ⇒ 除以 0 崩。
        # 每个面板缺的字段仍由 ``_apply_panel_override`` 从**同名**内置面板补齐。
        panels = {k: v for k, v in panels.items() if k in override}
    if rects:
        _apply_panel_override(panels, {n: {"rect": b} for n, b in rects.items()})
    return panels


def parse_x2_from(spec: str | None) -> dict[str, str]:
    """``--x2-from`` 的取值 → ``{面板: "content"|"window"}``。

    写法与 ``--emoji-table`` 同风格：``面板=content|window``，逗号或空格分隔。
    """
    out: dict[str, str] = {}
    for it in re.split(r"[,\s]+", (spec or "").strip()):
        if not it:
            continue
        if "=" not in it:
            raise SystemExit(f"--x2-from 要写成 面板=content|window，收到 {it!r}")
        k, v = it.split("=", 1)
        v = v.strip()
        if v not in ("content", "window"):
            raise SystemExit(f"--x2-from 的取值只能是 content 或 window，收到 {v!r}")
        out[k.strip()] = v
    return out


def parse_rank_pill(spec: str | None) -> set[str]:
    """``--rank-pill`` 的取值 → 带「榜N」药丸的**面板名集合**（逗号或空格分隔）。

    与 ``--x2-from`` 同风格，给**不吃面板文件**的用法兜底（内置面板 / 手写命令行）。
    正常走配置时不用它 —— 平台档案里的 ``rank_pill`` 会被展开进 panels 文件
    （见 config.py 的 ``normalize_panels``），随面板一起来。
    """
    return {s for s in re.split(r"[,\s]+", (spec or "").strip()) if s}


def _rank_pill_panels(panels_file: str | None, spec: str | None) -> set[str]:
    """哪些面板的等级标是「榜N」药丸（要按药丸切昵称/正文）？

    面板自己的 ``rank_pill`` 优先（panels 文件 / 平台档案），其次命令行 ``--rank-pill``。
    """
    out = parse_rank_pill(spec)
    if panels_file:
        for name, p in resolve_panels(panels_file).items():
            if p.get("rank_pill"):
                out.add(name)
    return out


def parse_fan_badge(spec: str | None) -> dict[str, int]:
    """``--fan-badge`` 的取值 → ``{面板名: 徽章块左沿上限}``（逗号或空格分隔）。

    写法 ``面板=x``（``--x2-from`` 同风格）。给不吃面板文件的用法兜底；正常走配置时
    用面板/平台档案里的 ``fan_badge_x_max``（见 ``_fan_badge_panels``）。
    """
    out: dict[str, int] = {}
    for it in re.split(r"[,\s]+", (spec or "").strip()):
        if not it:
            continue
        if "=" not in it:
            raise SystemExit(f"--fan-badge 要写成 面板=x，收到 {it!r}")
        k, v = it.split("=", 1)
        try:
            out[k.strip()] = int(float(v.strip()))
        except ValueError:
            raise SystemExit(f"--fan-badge 的 x 要是数字，收到 {v!r}")
    return out


def _fan_badge_panels(panels_file: str | None, spec: str | None) -> dict[str, int]:
    """哪些面板的行首有**被并进正文**的「粉丝」药丸徽章，以及它的几何闸（见 _FAN_BADGE_RE）。

    面板自己的 ``fan_badge_x_max`` 优先（panels 文件 / 平台档案），其次命令行 ``--fan-badge``。
    """
    out = parse_fan_badge(spec)
    if panels_file:
        for name, p in resolve_panels(panels_file).items():
            if p.get("fan_badge_x_max") is not None:
                out[name] = int(p["fan_badge_x_max"])
    return out


def _fan_badge_level_panels(panels_file: str | None) -> set[str]:
    """哪些面板允许「粉丝」药丸**前面**还并着一个等级药丸（``+6粉丝若云：…``）。

    面板/平台档案里的 ``fan_badge_with_level``。只有视频号手机 App 的某些版式
    （✦N 在左、粉丝在右）需要；不写这个键的面板与旧行为逐字节相同。
    """
    out: set[str] = set()
    if panels_file:
        for name, p in resolve_panels(panels_file).items():
            if p.get("fan_badge_with_level"):
                out.add(name)
    return out


def _strip_phrases_panels(panels_file: str | None) -> dict[str, list[str]]:
    """哪些面板要从行尾剥掉「本场专用词」，值是短语列表（平台档案 ``strip_phrases``）。"""
    out: dict[str, list[str]] = {}
    if panels_file:
        for name, p in resolve_panels(panels_file).items():
            v = p.get("strip_phrases")
            if v:
                out[name] = [str(x) for x in v if str(x)]
    return out


def _skip_phrases_panels(panels_file: str | None) -> dict[str, list[str]]:
    """哪些面板要把**整行等于**这些词的行走掉，值是短语列表（平台档案 ``skip_phrases``）。

    与 ``strip_phrases`` 的区别：后者按**行尾后缀**剥（剥完还剩正文就保留），这里只认
    **整行匹配**（忽略空白）—— 用于「奇瑞汽车>」这种公众号卡 / 主题卡行。按后缀剥品牌名
    会误伤正文（``我喜欢奇瑞汽车``），按整行匹配则不会。
    """
    out: dict[str, list[str]] = {}
    if panels_file:
        for name, p in resolve_panels(panels_file).items():
            v = p.get("skip_phrases")
            if v:
                out[name] = [str(x) for x in v if str(x)]
    return out


def _emoji_merge_panels(panels_file: str | None) -> set[str]:
    """哪些面板要把纯表情续行并进上一条评论（``merge_emoji_continuation``）。"""
    out: set[str] = set()
    if panels_file:
        for name, p in resolve_panels(panels_file).items():
            if p.get("merge_emoji_continuation"):
                out.add(name)
    return out


def _badge_name_recover_panels(panels_file: str | None) -> set[str]:
    """哪些面板要从并进药丸块的 ``badge_text`` 里恢复昵称（``badge_name_recover``）。

    只有形状明确（1~2 位等级 + 非数字昵称 + 星号）才恢复，见
    ``parsing/layout/paragraphs.py`` 的 ``_RECOVER_BADGE_NAME_RE``。不写这个键的面板不变。
    """
    out: set[str] = set()
    if panels_file:
        for name, p in resolve_panels(panels_file).items():
            if p.get("badge_name_recover"):
                out.add(name)
    return out


def _notice_prefix_panels(panels_file: str | None) -> dict[str, str]:
    """哪些面板要剥「行首粘连的顶部 UI 通知」，值是通知短语的正则片段。

    面板/平台档案里的 ``notice_prefix_strip``（视频号手机 App：``开播请注意驾驶安全``
    常与列表第一行评论并成一块）。不写这个键的面板与旧行为逐字节相同。
    """
    out: dict[str, str] = {}
    if panels_file:
        for name, p in resolve_panels(panels_file).items():
            v = p.get("notice_prefix_strip")
            if v:
                out[name] = str(v)
    return out


def parse_badge_alias(spec: str | None) -> set[str]:
    """``--badge-alias`` 的取值 → 要做行首药丸别名归一的**面板名集合**（逗号/空格分隔）。

    给不吃面板文件的用法兜底；正常走配置时用面板/平台档案里的 ``badge_alias``
    （见 ``_badge_alias_panels``）。
    """
    return {s for s in re.split(r"[,\s]+", (spec or "").strip()) if s}


def _badge_alias_panels(panels_file: str | None, spec: str | None) -> set[str]:
    """哪些面板要做行首药丸别名归一（见 ``normalize_badge_alias``）。

    面板自己的 ``badge_alias`` 优先（panels 文件 / 平台档案），其次命令行 ``--badge-alias``。
    """
    out = parse_badge_alias(spec)
    if panels_file:
        for name, p in resolve_panels(panels_file).items():
            if p.get("badge_alias"):
                out.add(name)
    return out


def parse_username_alias(spec: str | None) -> set[str]:
    """``--username-alias`` 的取值 → 要做**昵称尾巴别名归一**的面板名集合（逗号/空格分隔）。

    给不吃面板文件的用法兜底；正常走配置时用面板/平台档案里的 ``username_alias``
    （见 ``_username_alias_panels``）。
    """
    return {s for s in re.split(r"[,\s]+", (spec or "").strip()) if s}


def _username_alias_panels(panels_file: str | None, spec: str | None) -> set[str]:
    """哪些面板要做昵称尾巴别名归一（见 ``normalize_username_alias``）。

    面板自己的 ``username_alias`` 优先（panels 文件 / 平台档案），其次命令行
    ``--username-alias``。
    """
    out = parse_username_alias(spec)
    if panels_file:
        for name, p in resolve_panels(panels_file).items():
            if p.get("username_alias"):
                out.add(name)
    return out


def parse_overlay_cards(spec: str | None) -> dict[str, str | None]:
    """``--overlay-cards`` 的取值 → ``{面板名: 本素材补的正则片段 | None}``。

    取值是逗号分隔的 ``面板`` 或 ``面板=额外正则``（例：``comments=奇瑞汽车|^广?告``）。
    给不吃面板文件的用法兜底；正常走配置时用面板/平台档案里的 ``overlay_cards``
    （见 ``_overlay_cards_panels``）。内置短语表在代码里（``_OVERLAY_CARD_RE``），
    ``=`` 后面那截是**这个素材**才有的词（品牌名这类，见 ``overlay_card_re``）。
    """
    out: dict[str, str | None] = {}
    for item in (spec or "").replace(" ", ",").split(","):
        if not item:
            continue
        name, _, extra = item.partition("=")
        if name:
            out[name] = extra or None
    return out


def _overlay_cards_panels(panels_file: str | None, spec: str | None) -> dict[str, str | None]:
    """哪些面板会被**浮层卡**压住，各补什么短语（见 ``overlay_card_re``）。

    面板自己的 ``overlay_cards`` 优先（panels 文件 / 平台档案），其次命令行 ``--overlay-cards``。
    值可以是 ``True``（只用内置表）或一段正则（内置表 ＋ 它）。
    """
    out = parse_overlay_cards(spec)
    if panels_file:
        for name, p in resolve_panels(panels_file).items():
            v = p.get("overlay_cards")
            if v:
                out[name] = v if isinstance(v, str) else None
    return out


def _x2_policy(pname: str, panel: dict, policy: dict[str, str] | None) -> str:
    """这个面板的**裁切右沿**跟内容走（``content``）还是固定成超集（``window``）？

    面板自己的 ``x2_from``（来自 panels 文件 / 平台档案）优先，其次命令行 ``--x2-from``。
    为什么要有 ``window``：有的面板（视频号）**右边跟着内容伸缩**，右沿由内容定就必然
    在某一帧开始裁内容 —— 裁切边不是平移不变的，被裁的行会整行丢或误读。
    """
    return str(panel.get("x2_from") or (policy or {}).get(pname) or "content")
