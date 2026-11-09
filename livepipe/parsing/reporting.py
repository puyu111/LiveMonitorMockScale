"""livepipe.parsing.reporting — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

from .comments.input import has_geometry
from .comments.input import parse_rows_any
from .comments.overlays import overlay_card_re_for
from .common.log import _log
from .ocr.overrides import _overlay_cards_panels
from .ocr.overrides import _rank_pill_panels
from .ocr.overrides import _skip_phrases_panels
from .layout.paragraphs import _is_skip_line
from .text.classify import classify_line
from .text.cleaning import _clean



def _stats(comments: list[dict], label: str) -> None:
    n = len(comments)
    n_multi = sum(1 for c in comments if c.get("lines", 1) > 1)
    _log(f"{label}: {n} 条评论")
    if not n:
        return
    _log(f"  多行合并   {n_multi} 条（{n_multi / n:.1%}）")
    _log(f"  用户名未识别 {sum(1 for c in comments if not c['username'])} 条"
          f"（昵称被 OCR 丢了，内容仍在）")
    _log(f"  正文为空   {sum(1 for c in comments if not c['content'])} 条"
          f"（纯表情评论，正文靠 emoji_text 补）")
    n_roi = sum(1 for c in comments if c.get("roi"))
    if n_roi:
        c = next(c for c in comments if c.get("roi"))
        # ROI 相对纯文字框**两头都扩**：左边到等级徽章、右边到面板右沿（框外表情）。
        n_badge = sum(1 for c in comments if c.get("roi") and c.get("text_box")
                      and c["roi"][0] < c["text_box"][0])
        _log(f"  带 ROI     {n_roi} 条（左含等级徽章 {n_badge} 条 / 右扩到面板 "
              f"{c['panel_right']} 圈住框外表情）；示例 roi={c['roi']} text_box={c['text_box']}")
    _stats_emoji(comments)


def emoji_summary(comments: list[dict]) -> dict:
    """表情层的账（进 ``summary.json``，也喂给 ``_stats_emoji`` 打屏）。

    ``counts`` 把没敢认的按 ``?guess`` 单列 —— 它们和认出来的混在一起会让「认对了几个」
    看起来比实际好（未识别的也算进 token 计数里了）。

    ``by_panel`` 是**按面板**再算一遍：两个窗口用的可能是两套表情资产、两组阈值，
    合在一行账里看不出「左边认了 36 个、右边 0 个」这种偏瘫 —— 而「右边 0 个」正是
    「右面板的表接错了」最直接的信号。
    """
    def _tally(rows: list[dict]) -> dict:
        counts: dict[str, int] = {}
        for c in rows:
            for e in c.get("emojis") or []:
                k = e.get("token") or f"?{e.get('guess')}"
                counts[k] = counts.get(k, 0) + int(e.get("n", 1))
        return counts

    rows_with = [c for c in comments if c.get("emojis")]
    counts = _tally(rows_with)
    by_panel: dict[str, dict] = {}
    for c in comments:
        p = c.get("panel") or "?"
        d = by_panel.setdefault(p, {"comments_with_emoji": 0, "total": 0,
                                    "unrecognized": 0, "comments": 0})
        d["comments"] += 1
        if c.get("emojis"):
            d["comments_with_emoji"] += 1
            for e in c["emojis"]:
                n = int(e.get("n", 1))
                d["total"] += n
                if not e.get("token"):
                    d["unrecognized"] += n
    return {
        "comments_with_emoji": len(rows_with),
        "total": sum(counts.values()),
        "unrecognized": sum(v for k, v in counts.items() if k.startswith("?")),
        "counts": dict(sorted(counts.items(), key=lambda z: -z[1])),
        "by_panel": by_panel,
        # 纯表情评论（content 空）里被 emoji_text 补上内容的条数 —— 这一段是否生效的直接证据
        "filled_empty_content": sum(1 for c in comments
                                    if not c.get("content") and c.get("emoji_text")),
    }


def _stats_emoji(comments: list[dict]) -> None:
    """表情识别结果的一行账：几条带表情、几个、token 是什么、几个没敢认。

    这行必须出现（哪怕全是 0）—— 「表没接上」和「这一屏真的没表情」在产物里长得一模一样，
    不报出来就分不清是没跑还是没命中（空产物不能当成功，老毛病了）。**再按面板各报一行**：
    左右两个窗口是两套资产，只报总数会把「一边全丢」平摊掉。
    """
    s = emoji_summary(comments)
    if not s["comments_with_emoji"] and not s["total"]:
        _log("  表情识别   0 条带表情（这一屏确实没有，或表情表没接上 —— 看上面的 [emoji] 行）")
    else:
        top = "、".join(f"{k}×{v}" for k, v in list(s["counts"].items())[:6])
        _log(f"  表情识别   {s['comments_with_emoji']} 条带表情 / 共 {s['total']} 个（{top}）"
              + (f"；没敢认 {s['unrecognized']} 个（分数或区分度不够，token=null，看法见 guess）"
                 if s["unrecognized"] else ""))
        if s["filled_empty_content"]:
            ex = next(c["emoji_text"] for c in comments
                      if not c.get("content") and c.get("emoji_text"))
            _log(f"            其中补上了空正文 {s['filled_empty_content']} 条（示例 {ex}）")
    if len(s["by_panel"]) > 1:
        parts = []
        for p, d in sorted(s["by_panel"].items()):
            parts.append(f"{p} {d['comments_with_emoji']}/{d['comments']} 条 共 {d['total']} 个")
        _log("            按面板：" + "；".join(parts)
             + "（某一边是 0 ⇒ 先看 --emoji-table 给那张面板选的表对不对）")


def _report(rows: list[dict], args: argparse.Namespace) -> int:
    """逐行归类计数（不打印正文）—— 上真实语料前先用它核一遍续行判定。"""
    texts = [r.get("text", "") for r in rows]
    geo = has_geometry(rows)                    # 判据必须和解析层一致，否则统计对不上账
    # 面板级开关也要跟着来，否则「统计说 drop:浮层卡 N 行、解析却不认」两边对不上账。
    rank_pill = _rank_pill_panels(getattr(args, "panels_file", None),
                                  getattr(args, "rank_pill", None))
    overlay_cards = _overlay_cards_panels(getattr(args, "panels_file", None),
                                          getattr(args, "overlay_cards", None))
    skip_phrases = _skip_phrases_panels(getattr(args, "panels_file", None))
    counts: dict[str, int] = {}
    cont_lines: list[str] = []
    for r in rows:
        t = r.get("text", "")
        panel = r.get("panel")
        # 与 parse_live_rows 同一条判据：整行跳过词先丢，再归类。
        if skip_phrases and _is_skip_line(t, skip_phrases.get(panel) or []):
            counts["drop:跳过词"] = counts.get("drop:跳过词", 0) + 1
            continue
        kind, payload = classify_line(t, args.max_username_len, badge_column_excluded=geo,
                                      rank_pill=bool(rank_pill) and panel in rank_pill,
                                      overlay_card=overlay_card_re_for(overlay_cards, panel))
        if kind == "comment" and payload[0] is None:        # type: ignore[index]
            key = "comment/用户名未识别"
        elif kind == "drop":
            key = f"drop:{payload}"
        else:
            key = kind
        counts[key] = counts.get(key, 0) + 1
        if kind == "continuation":
            cont_lines.append(_clean(t))
    print(f"输入 {len(texts)} 行（几何：{'有' if has_geometry(rows) else '无'}）：")
    for k in sorted(counts):
        print(f"  {k:<18} {counts[k]:>4}")
    comments = parse_rows_any(rows, args)
    _stats(comments, "=> 解析")
    print(f"   （{counts.get('continuation', 0)} 行被判为续行）")
    if cont_lines:
        print("   被判为续行的行（**逐条人工核**，这里最可能误并）：")
        for t in cont_lines[:15]:
            print(f"     {t!r}")
    return 0


def _load_summary(out: str | None) -> dict:
    """读上一次的 ``<out>/summary.json`` 当底稿（没有/读不动就 ``{}``）。

    从 ``--rows`` 复算时用它：定位矩形、抽样间隔、抽了多少帧这些**只有跑 OCR 才知道**的
    元信息复算不出来，直接覆盖就永远丢了。
    """
    if not out:
        return {}
    try:
        d = json.loads((Path(out) / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _emit(comments: list[dict], args: argparse.Namespace, meta: dict | None = None) -> None:
    """输出：``--out`` 时写盘，否则打到 stdout。"""
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        with (out / "comments.jsonl").open("w", encoding="utf-8") as f:
            for c in comments:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        if meta is not None:
            # 表情的账进 summary：哪些 token、几个、几个没敢认、补了几条空正文 ——
            # 光看 comments.jsonl 只能看到「认出来了」，看不到「有没有漏认」，得对着账核。
            meta = dict(meta, unique_comments=len(comments),
                        emoji=emoji_summary(comments))
            (out / "summary.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2))
        _log(f"[done] -> {out}/comments.jsonl")
    if args.print or not args.out:
        json.dump(comments, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
