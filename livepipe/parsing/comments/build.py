"""livepipe.parsing.comments.build — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import re
from collections import Counter

from ..comments.overlays import _FROZEN_BOX_RATIO
from ..comments.overlays import frozen_overlay_texts
from ..comments.overlays import overlay_card_re_for
from ..common.log import _log
from ..layout.paragraphs import parse_live_rows
from ..ocr.overrides import resolve_panels
from ..text.badges import _badge_digits



def _row_pitch_panels(panels_file: str | None) -> dict[str, float]:
    """每个面板的行距（``row_pitch``：平台档案给初值、自标定按采样帧修正）。

    为什么要从面板档案取、而不是现场按这一帧的行 y 估一个：行距是**行网格**的尺子
    （槽位吸附、缩进续行的间距闸都用它），一帧里凑出来的中位数会被折行（0.75 行距）
    带偏 —— 折行比评论多的那一帧就量错了。面板档案里的值是自标定在整段采样帧上算的。
    """
    out: dict[str, float] = {}
    if panels_file:
        for name, p in resolve_panels(panels_file).items():
            if p.get("row_pitch"):
                out[name] = float(p["row_pitch"])
    return out


def comments_from_rows(rows: list[dict], *, merge_separator: str = " ",
                       max_username_len: int = 24, merge_continuation: bool = True,
                       right_pad: int | None = 0,
                       require_adjacent_socket: bool = True,
                       rank_pill: set[str] | None = None,
                       fan_badge: dict[str, int] | None = None,
                       fan_badge_level: set[str] | None = None,
                       badge_name_recover: set[str] | None = None,
                       strip_phrases: dict[str, list[str]] | None = None,
                       skip_phrases: dict[str, list[str]] | None = None,
                       merge_emoji_continuation: set[str] | None = None,
                       notice_prefix: dict[str, str] | None = None,
                       overlay_cards: dict[str, str | None] | None = None,
                       row_pitch: dict[str, float] | None = None) -> list[dict]:
    """按（帧, 面板）分组跑 ``parse_live_rows``，再拼成一条评论流。

    ``rank_pill``：**哪些面板**的等级标是「榜N」药丸（平台档案 ``rank_pill``，见
    ``_rank_pill_panels``）。按面板名给，不按行 —— 行里没有平台，面板名是它的身份。

    ``fan_badge``：哪些面板的行首有「粉丝」药丸徽章，值是该面板的 ``fan_badge_x_max``
    （见 ``_fan_badge_panels``）。同样按面板名给。

    ``overlay_cards``：哪些面板会被**浮层卡**压住，值是该面板本素材补的短语
    （``None`` = 只有内置表，见 ``overlay_card_re``）。同样按面板名给。
    这个开关同时打开浮层卡的**第二条判据** —— 跨帧「列表滚了它没动」
    （``frozen_overlay_texts``），它不依赖任何素材词表。

    ``skip_phrases``：哪些面板要丢掉**整行等于**这些词的行走掉（平台档案
    ``skip_phrases``）。与 ``strip_phrases`` 不同：只认整行（忽略空白），不按后缀剥 ——
    ``奇瑞汽车>`` 这类公众号/主题卡行用它，正文里提到品牌名的真评论不受影响。

    ``row_pitch``：每个面板的行距（见 ``_row_pitch_panels``）。缩进续行的间距闸用它。
    """
    # 浮层卡的第二条判据（跨帧「列表滚了它没动」，见 ``frozen_overlay_texts``）：
    # 在**分组之前**把这些行摘掉，它们就既不会自成一匿名评论、也不会被并进上一条。
    frozen = frozen_overlay_texts(rows, panels=set(overlay_cards or {}),
                                  row_pitch=row_pitch)
    if frozen:
        def _is_frozen(r) -> bool:
            if "：" in r["text"] or ":" in r["text"]:
                return False          # 有冒号的行按定义是评论 —— 投票时不收它，摘行时也不许摘
            if not (r.get("text") or "").strip():
                # 纯表情行（text 空、内容靠 emoji_text）：**没有文字可配** ⇒ 浮层判据对它
                # 不成立。不挡住的话它会掉进下面的「带键」分支 —— 只要它某帧的 y 落在
                # 一条浮层带上，这条评论就被摘掉了（带键是按 y 判的，不看时间）。
                return False
            f = frozen.get(r.get("panel"))
            if not f:
                return False
            if r.get("text") in f["texts"]:
                return True
            yc = (r["box"][1] + r["box"][3]) / 2
            return any(abs(yc - y) <= _FROZEN_BOX_RATIO * f["pitch"]
                       for y in f["bands"])

        keep_rows, n = [], 0
        for r in rows:
            if _is_frozen(r):
                n += 1
            else:
                keep_rows.append(r)
        rows = keep_rows
        desc = []
        for p, v in sorted(frozen.items()):
            names = " / ".join(sorted(v["texts"]))
            if v["bands"]:
                names = f"{names} ＋{len(v['bands'])} 条带".strip()
            desc.append(f"{p}: {names}")
        _log(f"  [card] 浮层（列表滚动时它不动）：丢 {n} 行 —— " + "；".join(desc))
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r.get("t"), r.get("panel")), []).append(r)
    out: list[dict] = []
    for key in sorted(groups, key=lambda k: (k[0] if k[0] is not None else -1, str(k[1]))):
        # 组内保证是屏幕上到下（socket 升序）
        rs = sorted(groups[key], key=lambda r: (r.get("socket") is None, r.get("socket")))
        notice_rx = (re.compile(notice_prefix[key[1]])
                     if notice_prefix and key[1] in notice_prefix else None)
        out += parse_live_rows(rs, merge_separator=merge_separator,
                               max_username_len=max_username_len,
                               merge_continuation=merge_continuation,
                               right_pad=right_pad,
                               require_adjacent_socket=require_adjacent_socket,
                               rank_pill=bool(rank_pill) and key[1] in rank_pill,
                               fan_badge_x_max=(fan_badge or {}).get(key[1]),
                               fan_badge_with_level=bool(fan_badge_level)
                               and key[1] in fan_badge_level,
                               badge_name_recover=bool(badge_name_recover)
                               and key[1] in badge_name_recover,
                               strip_phrases=(strip_phrases or {}).get(key[1]),
                               skip_phrases=(skip_phrases or {}).get(key[1]),
                               merge_emoji_continuation=bool(merge_emoji_continuation)
                               and key[1] in merge_emoji_continuation,
                               notice_prefix=notice_rx,
                               overlay_cards=overlay_card_re_for(overlay_cards, key[1]),
                               row_pitch=(row_pitch or {}).get(key[1]))
    return out


def _decisive(cnt: Counter | None) -> str:
    """一组读数里**唯一最高**的那个；平票（或没有）→ ``""``。

    等级标读数会错（``29`` 读成 ``129``、``5`` 读成 ``15``），多数决能压掉零星的错读；
    而**平票**说明这一组本身就没定论 —— 不猜，返回空串让它落在「读不到等级」那一档
    （宁可漏归一，不可错归并）。
    """
    mc = cnt.most_common() if cnt else []
    if not mc or (len(mc) > 1 and mc[0][1] == mc[1][1]):
        return ""
    return mc[0][0]


def _same_user(a: dict, b: dict) -> bool:
    """两条记录是不是**同一个昵称**（两边都要有昵称，读不到昵称就不算数）。"""
    ua = (a.get("username") or "").strip()
    ub = (b.get("username") or "").strip()
    return bool(ua) and ua == ub


def _log_badge_fill(stats: dict, comments: list[dict]) -> None:
    """登出「等级标有多少条有读数、其中多少是从同一条评论的其它帧补回来的」。

    交付层按 (昵称, 等级标) 认人（见 account_id），这个数直接决定交付名单，所以要看得见。
    """
    from ..ocr.pills import PILL_MIN_AGREE

    have = sum(1 for c in comments if _badge_digits(c.get("badge_text")))
    n = len(comments)
    if not have:
        return
    _log(f"  [badge] 等级标：{have}/{n} 条唯一评论有读数（{100 * have / max(n, 1):.1f}%）"
         f" —— 其中 {stats.get('badge_filled', 0)} 条是从**同一账号的其它帧**补回来的"
         f"（同一条评论、同昵称、多数决；平票不补 {stats.get('badge_fill_tied', 0)} 条）")
    # 二次读数的账：补了多少、以及**它自证得怎么样**（与面板读数两边独立，都对上才算数）
    rr_ok, rr_diff = stats.get("badge_reread_agree", 0), stats.get("badge_reread_differ", 0)
    rr_none = stats.get("badge_reread_novote", 0)
    if stats.get("badge_reread") or stats.get("badge_reread_tied") or rr_ok or rr_diff or rr_none \
            or stats.get("badge_reread_pooled"):
        pooled = stats.get("badge_reread_pooled", 0)
        _log(f"  [badge] 药丸带二次读数：又补了 {stats.get('badge_reread', 0)} 条"
             f"（跨帧 ≥{PILL_MIN_AGREE} 帧读出同一个值才认；平票/不够票 {stats.get('badge_reread_tied', 0)} 条）；"
             f"与面板读**两边都有**的 {rr_ok + rr_diff + rr_none} 条里对上 {rr_ok} 条"
             f"（{100 * rr_ok / max(rr_ok + rr_diff, 1):.1f}%，分母只算**投得出票**的 {rr_ok + rr_diff} 条 —— "
             f"投不出票 {rr_none} 条（票不够/平票）**不算不一致**）、不一致 {rr_diff} 条"
             + (f"；另有 {pooled} 条的票是从被 rid 链**断开**的兄弟记录并过来的（纯表情评论没有"
                f"文本键，链一断票就分家 —— 见 _pool_sibling_votes）" if pooled else ""))
