"""livepipe.parsing.comments.input — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

from ..comments.build import _row_pitch_panels
from ..comments.build import comments_from_rows
from ..common.log import _log
from ..layout.roi import refresh_badge_left
from ..ocr.overrides import _badge_alias_panels
from ..ocr.overrides import _badge_name_recover_panels
from ..ocr.overrides import _emoji_merge_panels
from ..ocr.overrides import _fan_badge_panels
from ..ocr.overrides import _fan_badge_level_panels
from ..ocr.overrides import _notice_prefix_panels
from ..ocr.overrides import _strip_phrases_panels
from ..ocr.overrides import _skip_phrases_panels
from ..ocr.overrides import _overlay_cards_panels
from ..ocr.overrides import _rank_pill_panels
from ..ocr.overrides import _username_alias_panels
from ..ocr.overrides import resolve_panels
from ..text.badges import normalize_badge_alias
from ..text.badges import normalize_username_alias
from ..text.classify import parse_live_comments



def load_rows(path: str) -> list[dict]:
    """读 JSONL 行（每行含 ``text``；带 ``socket``/``box`` 就走版式解析）。"""
    if path == "-":
        return [json.loads(l) for l in sys.stdin if l.strip()]
    return [json.loads(l) for l in Path(path).open(encoding="utf-8") if l.strip()]


def has_geometry(rows: list[dict]) -> bool:
    return bool(rows) and "socket" in rows[0] and "box" in rows[0]


def parse_rows_any(rows: list[dict], args: argparse.Namespace) -> list[dict]:
    """有几何走 ``parse_live_rows``（出 ROI，带相邻守卫），没有就走纯文本版。"""
    kw = dict(merge_separator=args.sep, max_username_len=args.max_username_len,
              merge_continuation=not args.no_merge)
    if has_geometry(rows):
        # 行序里的 badge_left 是**抽行那一刻**写的；改了平台档案的 badge_span 后，走
        # --rows 复算/续跑时它是旧值 ⇒ 按当前面板档案重算（零 OCR 也能修 ROI 左沿）。
        pf = getattr(args, "panels_file", None)
        if pf and not Path(pf).exists():
            # 面板档案是**派生文件**（panels 步按平台档案展开后写出）。``--only parse``
            # 本来由 build_parse_args 现场补写，可它也可能**跑到一半**被别人删掉 —— 实测
            # 2026-10-26：两个进程同时跑同一份输出目录，另一条的 panels 步重建了段目录，
            # 这条已经在 OCR 里跑了四分钟的段拿到行序、走到这里才发现
            # ``panels.resolved.json`` 没了 ⇒ FileNotFoundError 把整条管线（连同后面
            # 六段）带崩。它只影响「按当前档案重算 ROI 左沿」和几个解析闸，读不到就退回
            # 行序里的旧值、闸全关，记一笔让人知道该补跑 —— 绝不因为一个派生文件丢掉
            # 整条 OCR 的成果。
            _log(f"  [roi] ⚠ 面板文件不存在，跳过 badge_left 重算与面板闸：{pf}"
                 f"（先跑 panels 步；若两个进程在跑同一份输出目录，先停掉一个）")
            pf = args.panels_file = None
        if pf:
            panels_now = resolve_panels(pf)
            n_left = refresh_badge_left(rows, panels_now)
            if n_left:
                _log(f"  [roi] badge_left 按当前面板档案重算：{n_left} 行"
                     f"（旧行序里是抽行时的值）")
        rank_pill = _rank_pill_panels(pf, getattr(args, "rank_pill", None))
        fan_badge = _fan_badge_panels(pf, getattr(args, "fan_badge", None))
        fan_badge_level = _fan_badge_level_panels(pf)
        badge_name_recover = _badge_name_recover_panels(pf)
        strip_phrases = _strip_phrases_panels(pf)
        skip_phrases = _skip_phrases_panels(pf)
        emoji_merge = _emoji_merge_panels(pf)
        notice_prefix = _notice_prefix_panels(pf)
        badge_alias = _badge_alias_panels(pf, getattr(args, "badge_alias", None))
        username_alias = _username_alias_panels(
            pf, getattr(args, "username_alias", None))
        overlay_cards = _overlay_cards_panels(pf, getattr(args, "overlay_cards", None))
        comments = comments_from_rows(rows, right_pad=args.right_pad,
                                      require_adjacent_socket=not args.no_adjacency,
                                      rank_pill=rank_pill or None,
                                      fan_badge=fan_badge or None,
                                      fan_badge_level=fan_badge_level or None,
                                      badge_name_recover=badge_name_recover or None,
                                      strip_phrases=strip_phrases or None,
                                      skip_phrases=skip_phrases or None,
                                      merge_emoji_continuation=emoji_merge or None,
                                      notice_prefix=notice_prefix or None,
                                      overlay_cards=overlay_cards or None,
                                      row_pitch=_row_pitch_panels(
                                          getattr(args, "panels_file", None)) or None,
                                      **kw)
        if badge_alias:
            # 必须在 ``dedup_comments`` **之前**：dedup 的身份键含用户名，名字先统一了
            # 才能把同一条评论的多种读法并成一条（见 normalize_badge_alias 的说明）。
            st = normalize_badge_alias(comments, badge_alias)
            how = (f"，其中按等级标认下 {st['badge_ok']} 对、"
                   f"跨 rid 同句配对 {st['badge_pairs']} 对"
                   + (f"、顺手补回等级标 {st['badge_from_alias']} 条"
                      if st["badge_from_alias"] else ""))
            if st["rewritten"]:
                _log(f"  [badge] 行首药丸别名归一：{st['normalized']}/{st['groups']} 组、"
                     f"改写 {st['rewritten']} 条（{len(st['renames'])} 种脏名字）{how}")
                for k, v in sorted(st["renames"].items()):
                    _log(f"          {k} -> {v}")
            elif st["badge_ok"] or st["badge_pairs"] or st["vetoed"]:
                _log(f"  [badge] 行首药丸别名归一：没有可改写的名字{how}")
            # 等级标读出来却对不上 ⇒ 明确不并（这是「等级标一致才是一个用户」的硬闸），
            # 留证：万一等级标被 OCR 读错，这几行就是唯一能看出误否的地方。
            for u, canon, pd, cd in st["vetoed"][:3]:
                _log(f"          ⚠ 等级标不一致，不并：{u}（等级 {pd}）≠ "
                     f"{canon}（读到 {'/'.join(cd)}）")
        if username_alias:
            # 同理必须在 ``dedup_comments`` **之前**：dedup 的身份键含用户名。也必须在
            # ``normalize_badge_alias`` **之后**：药丸脏名先洗干净，尾巴比较才有意义。
            st = normalize_username_alias(comments, username_alias)
            if st["rewritten"]:
                _log(f"  [alias] 昵称尾巴别名归一：{st['groups']} 组、"
                     f"改写 {st['rewritten']} 条（{len(st['renames'])} 种脏名字）")
                for (panel, u), v in sorted(st["renames"].items()):
                    ru = st["rows"].get((panel, u), 0)
                    rv = st["rows"].get((panel, v), 0)
                    _log(f"          {u} -> {v}（行数 {ru} vs {rv}）")
            for u, v, du, dv in st["vetoed"][:3]:
                _log(f"          ⚠ 等级标不一致，不并：{u}（等级 {'/'.join(map(str, du))}）≠ "
                     f"{v}（读到 {'/'.join(map(str, dv))}）")
        return comments
    # 纯文本：没有版式，只能按顺序判续行（歧义消不掉，用 --report 核）
    return parse_live_comments([r.get("text", "") for r in rows], **kw)
