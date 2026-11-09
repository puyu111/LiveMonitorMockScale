"""livepipe.parsing.text.classify — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import re

from ..text.badges import _BADGE_RE
from ..text.cleaning import _clean
from ..text.cleaning import _is_junk
from ..text.notices import _ENTER_RE
from ..text.notices import _FOOTER_RE
from ..text.notices import _MASKED_NAME_LINE_RE
from ..text.notices import _NOCOLON_NOTICE_RE
from ..text.notices import _NOTICE_RE
from ..text.patterns import _COLON_RE


# 微博的「榜N」药丸：昵称**右边**紧跟的助力榜名次标（`昵称 [榜五]：正文`，版式见
# ``livepipe/weibo.json`` 文件头）。它既不是昵称也不是正文 —— 拆出来丢掉（用户要的
# 「榜一、榜二…不记录在内容中」），拆开之后昵称与正文也各归各位。
#
# 名次那截**最多一个字**（`榜一`…`榜九`），且**允许整截没有**：OCR 常把药丸里的小字
# 连图标一起吃掉，只剩一个「榜」（实测 825 行 ``用户6861943086榜``、96 行
# ``用户6861943086榜来了来了``），所以是 ``?`` 而不是 ``+``。
# 量过的证据（``out/weibo_run/rows.jsonl`` 全量 16694 行 / 1418 帧，2026-10-11）：
#   · 含「榜」的行 12182（73%）—— 这个平台的评论行几乎都带药丸；
#   · 「榜」后紧跟的串，按出现次数排下来全是**单个汉字**名次：二 1276 · 七 1123 · 四 990 ·
#     五 907 · 六 895 · 三 674 · 八 658 · 九 649 · 一 120，外加空串 4345；
#     **一个「榜十 / 榜百」都没有**（0 行）⇒ rank 只到 9，一个字足够;
#   · **名次那截绝不能吃阿拉伯数字**：看起来像「榜+数字」的 405 行，逐条看过全是
#     「名次被吃掉、正文以数字开头」—— ``用户6861943086榜一011``（正文 ``011``）、
#     ``幸运女神的小新榜618是不是还有活动啊``（正文 ``618是不是还有活动啊``，
#     618 是购物节）、``雅-当赏榜七6666``（正文 ``6666``）。
#     早期版本写成 ``榜[…\d]*``（想兜住「榜一被读成榜1」），结果把 ``榜四666666666666``
#     的 12 个 6 整串吞掉 ⇒ 那条真评论的正文变成空。**阿拉伯数字名次从未出现过**，删掉;
#   · 名次串长两个字的也都是「一个字名次 + 正文首字」：``画危鸭榜二一小时后…``（名次二、
#     正文 ``一小时后…``）、``昵称不能为空513榜八十八段弯道…``（名次八、正文 ``十八段弯道…``）、
#     ``昵称不能为空513榜七七冠荣光…``（名次七、正文 ``七冠荣光…``）⇒ 再贪一位就会啃正文;
#   · **「榜」从不出现在冒号之后**（0 / 16694）⇒ 它只可能长在「昵称 | 榜 | 正文」这一处，
#     不会误伤正文里的「榜」（'小明：榜一大哥' 仍按冒号正常切）;
#   · 一行最多一个「榜」（0 行有两个）⇒ 取第一个就行。
# 判据只对**带药丸的面板**生效（平台档案 ``rank_pill``）—— 抖音没有这个药丸，
# 不能拿形状去赌它的昵称与正文。
_RANK_PILL_RE = re.compile(r"榜[一二三四五六七八九十]?")


def _split_rank_pill(text: str, max_username_len: int) -> tuple[str | None, str] | None:
    """微博：把「昵称 榜N 正文」按**药丸**切成 ``(昵称, 正文)``；切不出就返回 None。

    药丸不是昵称、也不是正文，切完就丢 —— 剩下的两段才各归各位。这条判据解决的是
    「冒号被 OCR 吃掉」时的老问题：没有它，``幸运女神的小新帅飞了`` 这种行既拆不开昵称、
    又要被当续行并进上一条（一屏互不相干的评论并成一条）。药丸是**版式自带的分隔符**，
    比冒号可靠（它在屏幕上是个像素块，不吃）。

    只在药丸出现在**冒号之前**（或整行没有冒号）时才切：实测「榜」从不出现在冒号之后
    （0/16694，见 ``_RANK_PILL_RE``），所以冒号在前 ⇒ 这行的冒号才是分隔符，走老路。

    昵称空（药丸顶在行首：昵称连同冒号一起被 OCR 吃掉，实测 243 行）⇒ ``username=None``、
    正文照收（``榜三奇瑞是真的在听用户意见啊`` → 正文 ``奇瑞是真的在听用户意见啊``）；
    昵称超过 ``max_username_len`` ⇒ 不认这次切分（「榜」多半是正文里的一部分），返回 None。
    """
    m = _RANK_PILL_RE.search(text)
    if m is None:
        return None
    cm = _COLON_RE.search(text)
    if cm is not None and cm.start() < m.start():
        return None
    name = text[: m.start()].strip()
    if name and len(name) > max_username_len:
        return None
    # 药丸后面紧跟的冒号/空白是版式的一部分，不是正文的第一个字。
    return (name or None), text[m.end():].lstrip("：: \t　").strip()


def _split_comment(text: str, max_username_len: int,
                   badge_column_excluded: bool = False,
                   rank_pill: bool = False) -> tuple[str | None, str] | None:
    """把一行拆成 ``(username, content)``；不像「用户名: 内容」就返回 None。

    ``badge_column_excluded``：行来自**带几何**的路径（``group_rows`` 已经把左侧徽章列
    整块丢掉了）。这时「前串是纯数字」只能解释成昵称本身（实测抖音昵称 ``1221``，
    行文本 '1221：好帅哦大哥哥'，x1=656 已在正文列），不能按「徽章 + 用户名丢了」处理；
    徽章那个菱形图标有时还会被 OCR 读成一个点（'。hubvg:'），也一并剥掉。
    纯文本路径没有这个信息，只能保守：纯数字前串仍当徽章碎片。

    ``rank_pill``：这个面板的等级标是**跟在昵称右边**的「榜N」药丸（微博），先按药丸切一遍
    （见 ``_split_rank_pill``）。
    """
    if rank_pill:
        split = _split_rank_pill(text, max_username_len)
        if split is not None:
            return split
    m = _COLON_RE.search(text)
    if m is None:
        return None
    # 剥等级徽章：只有「剥掉后仍然非空、且确实剥掉了东西」才认这次剥离，避免啃昵称。
    prefix = text[: m.start()]
    stripped = _BADGE_RE.sub("", prefix, count=1).strip()
    badge_stripped = bool(stripped) and stripped != prefix.strip()
    candidate = stripped if badge_stripped else prefix.strip()
    if badge_column_excluded:
        candidate = candidate.lstrip("。.、·•")     # 徽章菱形被读成的点

    if not candidate or len(candidate) > max_username_len:
        return None            # 冒号前是长句 ⇒ 冒号在正文里，不是分隔符
    # 纯数字的用户名是真实存在的（实测抖音昵称 `1221`）：**剥掉徽章之后**剩下的数字算名字，
    # 或者几何路径已经保证前串里不可能有徽章。没这层保证时纯数字前串仍按「徽章在、
    # 用户名丢了」处理（'111：这是要漂移吗？' → 匿名评论），否则徽章会被当成用户名。
    if _is_junk(candidate) and not ((badge_stripped or badge_column_excluded)
                                    and candidate.isdigit()):
        return None            # 冒号前是纯数字/符号 ⇒ 更像正文里的冒号
    return candidate, text[m.end():].strip()


def classify_line(line: str, max_username_len: int = 24,
                  badge_column_excluded: bool = False,
                  rank_pill: bool = False,
                  overlay_card: re.Pattern | None = None) -> tuple[str, object]:
    """判一行的归属，供解析与统计共用（统计必须是同一套判据，否则对不上账）。

    ``badge_column_excluded`` 见 ``_split_comment``：带几何的路径传 True（徽章列已单独丢弃）。
    ``rank_pill`` 见 ``_split_comment``：这个面板的等级标是「榜N」药丸（微博），判据顺序
    与抖音**不同**（三条都是量出来的，见下）。
    ``overlay_card``：这个面板会被**浮层卡**压住（视频号手机 App）时给的判据 = 内置短语表
    ＋本素材补的短语（``overlay_card_re``）；不配这个面板就传 ``None``。
    排在药丸/通知之前判 —— 卡片是压在列表上的 UI，它的行跟评论比的是「谁在上面」，
    与药丸无关；且它跟 ``_NOCOLON_NOTICE_RE`` 一样**只认没有冒号的行**。

    Returns:
        ``("comment", (username, content))`` —— 新评论
        ``("continuation", 文本)``            —— 续行候选（还得看上一条在不在、槽位相邻否）
        ``("drop", 原因)``                    —— 丢弃（``空行``/``提示或通知``/``系统行``/
                                                   ``浮层卡``/``碎片行``）
    """
    line = _clean(line)
    if not line:
        return "drop", "空行"
    colonless = _COLON_RE.search(line) is None
    if _FOOTER_RE.search(line):
        return "drop", "提示或通知"
    # 浮层卡（视频号）：压在评论列表上的 UI 卡片 / 广告气泡，不是评论。只在这个面板配了
    # ``overlay_cards`` 时判（见 ``overlay_card_re``）。
    if overlay_card is not None and colonless and overlay_card.search(line):
        return "drop", "浮层卡"
    if rank_pill:
        # 微博（带「榜N」药丸的面板）判据顺序，两条都是量出来的：
        #   ① **系统行先判**：微博的系统行也含「榜」（「我也上榜」「恭喜X在助力榜飙升到
        #      第N名」），它们**没有冒号**，正是 _NOCOLON_NOTICE_RE 的判据；药丸先切的话
        #      「我也上榜」会被切成昵称「我也上」+空正文（1162 行的假评论）。
        #   ② **药丸切在抖音通知短语之前**：药丸在 ⇒ 这行一定是「昵称+榜N」开头的评论，
        #      而 _ENTER_RE（「…来了」）认的是形状、_NOTICE_RE 里也有几条会撞上正文
        #      （实测 199 行真评论以「来了」收尾，全被误丢）。
        if colonless and _NOCOLON_NOTICE_RE.search(line):
            return "drop", "系统行"
        parsed = _split_comment(line, max_username_len, badge_column_excluded,
                                rank_pill=True)
        if parsed is not None:
            return "comment", parsed
        # ``_ENTER_RE``（「…来了」）**不适用**：微博上以「来了」收尾的是真评论
        # （实测 199 行 ——「秋名山女车神来了」），而微博的进房通知含「播间」
        # （_NOCOLON_NOTICE_RE 认，上面第一条已经把它们收走）。上面药丸那一步已经把
        # 199 行里的绝大多数收成评论，这条只是保证**将来**昵称也被吃掉时（药丸跟着没了）
        # 不会把它们又丢回去。
        if _NOTICE_RE.search(line):
            return "drop", "提示或通知"
    else:
        # 通知与提示优先判：它们不该被当成续行粘到上一条评论尾巴上。
        if _NOTICE_RE.search(line) or _ENTER_RE.search(line):
            return "drop", "提示或通知"
        # 微博 App 的系统行（横幅/通知/开播公告）——**只认没有冒号的行**，见 _NOCOLON_NOTICE_RE。
        if colonless and _NOCOLON_NOTICE_RE.search(line):
            return "drop", "系统行"
        parsed = _split_comment(line, max_username_len, badge_column_excluded)
        if parsed is not None:
            return "comment", parsed
    # 冒号在、但冒号前不像用户名（空 / 纯数字徽章 / 超长）。只要冒号**后面**还有内容，
    # 它就是一条「用户名没被识别出来」的评论，**不是**续行 —— 续行会把正文并到
    # 上一条评论的尾巴上，等于把两个用户的评论搅在一起。
    # 实测反例：'111：这是要漂移吗？'（等级徽章在、用户名被 OCR 丢了）、'：帅'（只剩冒号）。
    m = _COLON_RE.search(line)
    if m is not None:
        prefix = line[: m.start()].strip()
        body = line[m.end():].strip()
        # 冒号前**过长**是另一种情况：冒号在正文里，整行仍按续行处理（别只留冒号后面那截）。
        if len(prefix) <= max_username_len and body and not _is_junk(body):
            return "comment", (None, body)
    # **掩码昵称 + 分隔符**（``**.线下有品鉴会就好了``：昵称被打码成 ``**``、冒号被 OCR 读成
    # ``.``）⇒ 这行是**另一个人**的新评论，昵称认不出来所以 username=None，正文取分隔符之后。
    # 不这么判它就是个「没有冒号的行」⇒ 续行候选，紧挨着上一行就并进去 —— 实测 seg2 t=30.1
    # 把 ``萌萌：五站八冠，这统治力可以`` 和这一条搅成了一条（同一句话在别的帧里是独立一条，
    # t=52.8 读到 ``线下有品鉴会就好了``、t=45.5 读到 ``一**：线下有品鉴会就好了``）。
    # 只认**带分隔符**的形态：``**预约了下一场直播`` 这种没有分隔符的，星号后面直接是正文，
    # 分不出「掩码昵称」和「真以星号开头的续行」（抖音语料里那一族全是通知，
    # 由 ``_indented_continuation`` 的 ``_MASKED_NAME_RE`` 挡在合并之外，不当评论收）。
    mm = _MASKED_NAME_LINE_RE.match(line)
    if mm is not None and not _is_junk(line[mm.end():]):
        return "comment", (None, line[mm.end():].strip())
    if _is_junk(line):
        return "drop", "碎片行"
    return "continuation", line


def parse_live_comments(
    lines: list[str],
    *,
    merge_separator: str = " ",
    max_username_len: int = 24,
    merge_continuation: bool = True,
) -> list[dict]:
    """解析直播间评论区文本行 → ``[{"username", "content"}, ...]``。

    Args:
        lines: 屏幕上一行一条的文本，**必须是同一帧里从上到下的顺序**（见 ``classify_line``
            的说明与本模块开头的「续行判定的固有歧义」）。拿不到几何时只能这样用，
            歧义也消不掉；有几何就用 ``parse_live_rows``。
        merge_separator: 续行拼接用的分隔符（默认空格；想要紧贴就传 ``""``）。
        max_username_len: 冒号前超过这个长度就不认作用户名（防止把正文里的冒号当分隔符）。
        merge_continuation: 是否把「没有用户名」的行并进上一条评论。关掉的话这类行直接丢弃。

    Returns:
        ``[{"username": str, "content": str}, ...]``，按出现顺序。
        正文为空的评论（纯表情评论、或表情被 OCR 丢掉）**保留**，content 为空串 ——
        别静默丢，否则「表情评论占比」这类统计会偏。
    """
    out: list[dict] = []
    for raw in lines:
        kind, payload = classify_line(raw, max_username_len)
        if kind == "comment":
            username, content = payload                    # type: ignore[misc]
            out.append({"username": username, "content": content})
        elif kind == "continuation" and merge_continuation and out:
            prev = out[-1]
            text = payload                                 # type: ignore[assignment]
            prev["content"] = (f"{prev['content']}{merge_separator}{text}"
                               if prev["content"] else text)
        # drop，或没有上一条可接的孤立续行 ⇒ 丢弃
    return out
