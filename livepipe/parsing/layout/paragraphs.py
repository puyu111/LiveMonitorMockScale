"""livepipe.parsing.layout.paragraphs — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import re

from ..emoji.scan import emoji_text_of
from ..layout.roi import _has_own_badge
from ..layout.roi import _indented_continuation
from ..layout.roi import _record
from ..layout.roi import _roi_of
from ..layout.roi import _union
from ..layout.roi import _wrapped_socket
from ..text.badges import _strip_fan_badge
from ..text.classify import classify_line
from ..text.cleaning import _is_tail_fragment
from ..text.notices import _NOCOLON_NOTICE_RE
from ..text.patterns import _COLON_RE


# 顶部 UI 通知（`非主驾开播 请注意驾驶安全`）有时会被 OCR 与**下面第一行评论**并成一块：
# 文本变成 `非主驾开播请注意驾驶安全微微：家用车靠谱最重要`，用户名字段就带着整条通知，
# 交付名单里会多出一类假账号。只在平台档案显式给 `notice_prefix_strip`（正则片段）时才剥。
# 允许短语前有 ≤12 个字符的 OCR 变体（实测 `非全驾…` / `翡主驾…` / `菲主驾…`）。
_NOTICE_PREFIX_HEAD = 12

# 「等级药丸 + 掩码昵称」被 OCR 并成一个块时的**可恢复**形状：
#   `6许***` / `17石***` / `12小***.` —— 可选 1~2 位等级数字 + **无数字**昵称 + ≥2 颗星
#   （尾部可能粘着被读成 `.` / `·` 的冒号残迹）。等级也可能整块没读出来（`V***`），
#   只要有掩码昵称就能恢复昵称、等级留空。
# 星号是平台打码的标志，纯药丸块不会出现；而「等级数字」与「昵称首字符」**都是数字**的
# 情形（`121***`：是 `12`+`1***` 还是 `1`+`21***`？）**不猜** —— 正则直接不匹配。
_RECOVER_BADGE_NAME_RE = re.compile(
    r"^\s*(?:\d{1,2}\s*)?([^\d\s][^\d\s]*\*{2,})\s*[.。:：,，·•]?\s*$")


def _strip_notice_prefix(text: str, rx: re.Pattern) -> str:
    """剥掉行首与通知粘连的前缀（opt-in，见平台档案 ``notice_prefix_strip``）。

    只在两个条件同时成立时动手：短语出现在行首 12 字符以内；剥完剩下的部分里**还有**
    分隔冒号 —— 后者保证剥的是通知、留下的是评论（没有冒号的行本来就交给
    ``overlay_cards`` 丢掉，不需要在这里拼）。
    """
    m = rx.search(text)
    if m is None or m.start() > _NOTICE_PREFIX_HEAD:
        return text
    rest = text[m.end():].lstrip(" 　:：,，.。·;；")
    if not rest or ("：" not in rest and ":" not in rest):
        return text
    return rest


def _is_skip_line(text: str, phrases: list[str]) -> bool:
    """整行（忽略空白）是否等于配置里的某个跳过词（opt-in，平台档案 ``skip_phrases``）。

    为什么只认**整行**、不按后缀剥：``奇瑞汽车>`` / ``CTCC中国汽车场地职业联赛`` 这类
    公众号卡、主题卡是**整行**的；而 ``奇瑞汽车`` 也是用户正文里会提到的词
    （``我喜欢奇瑞汽车``），按后缀剥会误伤。忽略空白是为了让 ``公众号 奇瑞汽车>``
    与配置里的 ``公众号奇瑞汽车>`` 也算同一行（OCR 的空格不稳定）。
    """
    t = re.sub(r"\s+", "", text or "")
    if not t:
        return False
    return any(t == re.sub(r"\s+", "", p) for p in phrases if p)


def _strip_suffix_phrases(text: str, phrases: list[str]) -> str:
    """把素材配置里的**专用词从行尾**剥掉（可连续多个，opt-in，见 ``strip_phrases``）。

    为什么是「行尾」：这类词来自压在评论列右边的商品卡/浮层，OCR 会把它们**接在同一行
    评论的末尾**（实测 `白日梦游家：孙正16人已预约`、`几点开始艾瑞泽8PRO`、
    `16人已预约艾瑞泽8PRO`）。只剥后缀 ⇒ 正文中间真的提到车型的评论不被误伤。
    整行都是这些词时返回空串，调用方当「这一行不存在」处理。
    """
    cur = text
    changed = False
    while True:
        s = cur.rstrip(" 　")
        hit = next((p for p in phrases if p and s.endswith(p)), None)
        if hit is None:
            break
        cur = s[: -len(hit)]
        changed = True
    # **只有真的剥掉过才修整尾部标点**：否则 `彭奶奶：` 这种「昵称 + 空正文」的行会被
    # 顺手削掉冒号、判成续行，进而把不同人的评论粘成一条（实测 t=155.1）。
    return cur.rstrip(" 　:：,，.。·") if changed else text



def parse_live_rows(
    rows: list[dict],
    *,
    merge_separator: str = " ",
    max_username_len: int = 24,
    merge_continuation: bool = True,
    right_pad: int | None = 0,
    require_adjacent_socket: bool = True,
    rank_pill: bool = False,
    fan_badge_x_max: int | None = None,
    fan_badge_with_level: bool = False,
    badge_name_recover: bool = False,
    strip_phrases: list[str] | None = None,
    skip_phrases: list[str] | None = None,
    merge_emoji_continuation: bool = False,
    notice_prefix: re.Pattern | None = None,
    overlay_cards: re.Pattern | None = None,
    row_pitch: float | None = None,
) -> list[dict]:
    """在 ``parse_live_comments`` 之上加 ROI：输入是**带几何的行**。

    Args:
        rows: 同一帧里**按屏幕从上到下排序**的行，每行
            ``{"text","box":[x1,y1,x2,y2],"socket","panel","t","x_text","x_right"}``
            （``rows_from_video`` 或 ``--dump-rows`` 的产出）。可选 ``badge_box``：
            该行等级徽章的框 —— ROI 的左边界取它，好把等级标一起圈进去。
        right_pad: ROI 右边界距面板右边（``x_right``）的余量；``None`` ⇒ 不扩，只留纯文字框。
        require_adjacent_socket: 只允许**行槽位紧邻**（``socket == 上一行 + 1``、同帧同面板）
            的行并入上一条评论。这是「纯文本分不开续行与整条评论」的替代判据 ——
            续行必然紧挨着上一行，中间隔着别的行槽就不可能是续行。
        rank_pill: 这个面板的等级标是**跟在昵称右边**的「榜N」药丸（微博）⇒ 行按药丸切
            昵称/正文、药丸本身丢掉（见 ``_split_rank_pill``）。
        fan_badge_x_max: 这个面板的行首有「粉丝」药丸徽章、且它被 OCR 并进了正文
            （见 ``_strip_fan_badge``）；值是徽章块左沿的上限，``None`` ⇒ 不认这个徽章。
        fan_badge_with_level: 允许「粉丝」药丸前还并着一个等级药丸（``+6粉丝若云：…``，
            视频号手机 App 的另一种版式）。只有平台档案显式写 ``fan_badge_with_level`` 才开。
        badge_name_recover: 「等级药丸 + 掩码昵称」被并成一个块、按徽章列丢掉时，若形状明确
            （1~2 位等级 + 非数字昵称 + 星号）就把昵称拼回文本（``badge_text``，平台档案
            ``badge_name_recover``）。歧义形状（``121***``）不猜。
        strip_phrases: 本场直播的**专用词**（商品卡/浮层文字，平台档案 ``strip_phrases``）。
            OCR 把它们接在评论行尾，这里按**后缀**剥掉；整行都是这些词时当作这一行不存在。
        skip_phrases: 本场直播要**整行跳过**的词（平台档案 ``skip_phrases``）。整行（忽略
            空白）等于其中任一词就丢掉这一行，用于公众号卡（``奇瑞汽车>``）/ 主题卡
            （``CTCC中国汽车场地职业联赛``）；与 ``strip_phrases`` 不同，不按后缀剥，
            所以正文里提到品牌名的真评论不受影响。
        merge_emoji_continuation: 纯表情续行（OCR 无文字、只有表情的行）并入上一条评论
            （平台档案 ``merge_emoji_continuation``）。抖音长表情评论会折成多行，默认它们会
            各自成匿名「评论」；开了就按相邻槽位并进首行。
        notice_prefix: 行首粘连的顶部 UI 通知的正则（视频号 App 的 ``开播请注意驾驶安全``），
            ``None`` ⇒ 不剥（平台档案 ``notice_prefix_strip``，见 ``_strip_notice_prefix``）。
        overlay_cards: 这个面板会被**浮层卡**压住（视频号手机 App）时的判据 = 内置短语表
            ＋本素材补的短语（``overlay_card_re``）；``None`` ⇒ 不认浮层卡。
        row_pitch: 这个面板的**行距**（面板档案里的 ``row_pitch``，自标定/平台档案给的）。
            缩进续行的**间距闸**拿它当尺子（见 ``_indented_continuation``）；``None`` ⇒
            没有尺子，那道闸不生效（行为与从前一致）。

    另外还有一条**几何**判据（``_has_own_badge``）：行自己的文字若从徽章列右边开始
    （``box[0] >= x_text``），说明它带着自己的等级徽章 ⇒ 是新评论的首行，**一律不并**。
    没有它的话，OCR 吃掉昵称末尾的冒号（``緣起：`` → ``緣起``）就会让一条新评论被
    并进上一条的正文里 —— 全片实测这类误并有 588 条（bug-228）。
    **但它要求面板有左侧徽章列**：微博没有（``x_text`` 就是正文列左沿），于是每一行都
    满足它、折行一条都并不上 —— 所以折行还要有第三条门 ``_wrapped_socket``。

    **折行续行**（``group_rows`` 认出来、给了小数槽位的那些行）走的就是上面这条续行分支：
    小数槽位本身就是「过了间距闸」的凭据，``_wrapped_socket`` 认它；它们通常也没有徽章、
    ``box[0]`` 在面板左沿，``_has_own_badge`` 也不会拦。于是折行第二行会被接进首行的
    正文、``lines`` 记 2、ROI 取两行的并集 —— 以前它整行被当徽章碎片丢掉（bug-229）。

    **系统行的残句尾巴**：紧跟在一行 ``drop "系统行"`` 之后、形状是「≤4 字 + 句末标点」
    的行（实测微博开播公告折行后剩下的「失。」）**也丢**，见 ``_is_tail_fragment``。

    **浮层卡的续行**（``overlay_cards``）：卡自己会折行，而第二行往往没有任何特征词
    （主题卡的 ``2026CTCC中国汽车场地职业联赛``、广告气泡的品牌行 ``奇瑞汽车``）——
    按内容赌会误伤真评论。所以：命中卡片短语的行被丢之后，**紧跟其后、同面板、槽位相邻的
    一行也丢**。只吃**一行** —— 浮层卡是叠着排的（实测 seg1 t=0：通知卡 / 提示卡 / 主题卡 /
    公众号卡四张卡的行槽位首尾相接），无限续链会把最下面那张卡底下紧挨着的真评论一起吃掉。
    第二行自己带特征词时走 ``overlay_card_re`` 那条路，不占这个名额。

    Returns:
        每条评论比 ``parse_live_comments`` 多这些键：
        ``roi`` 感兴趣区域（**含左侧等级徽章 + 右侧框外表情**的整行）·
        ``text_box`` 纯 OCR 文字框（不含徽章、不含表情）· ``colon_box`` 分隔冒号的小框 ·
        ``badge_box`` 等级徽章框 ·
        ``badge_text`` 等级徽章块读出来的**等级标**（身份证据，见 ``_badge_renames``）·
        ``panel_right`` 面板右边界 · ``lines`` 合并了几行 · ``sockets`` 占用的行槽位 ·
        ``panel`` / ``t`` · ``x_text`` 正文起始 x。
    """
    out: list[dict] = []
    open_idx: int | None = None      # 当前可以被续接的评论下标
    prev: tuple | None = None        # 上一「保留」行的 (panel, socket)，做相邻性检查
    dropped: tuple | None = None     # 上一条被丢的**系统行**的 (panel, socket)，认残句尾巴用
    card: tuple | None = None        # 上一条被丢的**浮层卡**行的 (panel, socket)，认卡的续行用
    last_box: list | None = None     # 当前这条评论**最后一行**的文字框（判缩进续行的间距闸用）
    open_base: list | None = None    # 当前这条评论**首行**的文字框（判缩进续行的缩进基准）
    # 为什么要单独记首行：``rec["text_box"]`` 是并集，并进贴左沿的折行（x1≈21）后左沿
    # 就被拉到 21，下一条真评论（x1≈34）会被当成「右移 13px」的缩进续行并进来
    # （见 ``_indented_continuation``）。
    for r in rows:
        panel, sock = r.get("panel"), r.get("socket")
        box = list(r["box"]) if r.get("box") else None
        x_right = r.get("x_right")
        # 行首「粉丝」药丸：它**没被 group_rows 当成徽章丢掉**（并进正文块了），
        # 只能在这里按几何闸剥掉。剥成空串就是这个药丸自己成了一块 ⇒ 走 drop 空行。
        text = r.get("text", "")
        if notice_prefix is not None:
            text = _strip_notice_prefix(text, notice_prefix)
        if fan_badge_x_max is not None:
            text = _strip_fan_badge(r, fan_badge_x_max, with_level=fan_badge_with_level,
                                    text=text)
        # 「等级药丸 + 掩码昵称」被并成一个块、按徽章列丢掉（抖音手机 App 实测 t=191.2：
        # 块 `12小***`、正文 `家用代步适合吗`）⇒ 昵称只剩在 badge_text 里，文本没有冒号、
        # 用户名就空了。形状**明确**（1~2 位等级 + 非数字昵称 + 星号）时把它拼回文本；
        # `121***` 这种等级与昵称首字符都是数字的**不猜**（见 _RECOVER_BADGE_NAME_RE）。
        # opt-in：平台档案写 `badge_name_recover` 才开。
        if badge_name_recover and text and not _COLON_RE.search(text) and r.get("badge_box"):
            m = _RECOVER_BADGE_NAME_RE.match(r.get("badge_text") or "")
            if m is not None:
                text = f"{m.group(1)}：{text}"
        # 本场直播的专用词（商品卡/浮层文字被 OCR 接在评论行尾）：只剥后缀。
        # 整行都是这些词、又没有表情 ⇒ 当作这一行不存在（不打断上面的续接链）。
        if strip_phrases and text:
            text = _strip_suffix_phrases(text, strip_phrases)
            if not text and not (r.get("emojis") or []):
                continue
        # 本场要**整行跳过**的词（公众号卡 / 主题卡）：只认整行，正文里提到品牌名不动。
        if skip_phrases and text and _is_skip_line(text, skip_phrases):
            continue
        # 带几何：徽章列已被 group_rows 丢掉 ⇒ 前串里的数字只能是昵称（见 _split_comment）
        #
        # **折行是几何事实，不能交给文本判据**：小数槽位只有一个来源 —— ``group_rows``
        # 里那个块**过了间距闸**（``_is_true_wrapped_line``，跟上一行 0.15~0.73 个行距）
        # 才挂上的（``_wrapped_socket``）。主播那种两行评论的第二行以时间开头、带冒号
        # （``18：45盛大启幕！敬请期待！``），``classify_line`` 会把它读成「昵称 18 ：
        # 正文 45盛大启幕…」—— 一条凭空的评论。几何说是续行，就按续行走。
        if _wrapped_socket(sock) and text.strip():
            kind, payload = "continuation", text
        else:
            kind, payload = classify_line(text, max_username_len,
                                          badge_column_excluded=True, rank_pill=rank_pill,
                                          overlay_card=overlay_cards)
        # **昵称 + 纯表情**的行：`彭奶奶：`（正文就是后面的表情）在 classify_line 里
        # 因为「冒号后没字」被判成续行，于是会被并进上一条评论的正文 —— 不同用户的
        # 评论就这样粘成 `彭奶奶 铿锵玫瑰 毒舌猫猫头`。opt-in（merge_emoji_continuation）：
        #   ① 文本形如 `昵称：`（≤24 字、只有冒号没正文）⇒ 这是一条以表情为正文的评论；
        #   ② 文本是短昵称、没有冒号、且从正文列起头（`_has_own_badge`）⇒
        #      OCR 把冒号吃掉的纯表情评论（`囍财` + 💛×4）。
        # 两种都改判成 comment（正文留空，内容由 emoji_text 补）。
        if (merge_emoji_continuation and kind == "continuation"
                and (r.get("emojis") or [])):
            t = (text or "").strip()
            m = re.match(r"^(\S{1,%d})\s*[：:]\s*$" % max_username_len, t)
            if m is not None:
                kind, payload = "comment", (m.group(1), "")
            elif (t and not _COLON_RE.search(t) and _has_own_badge(r)
                  and len(t) <= max_username_len):
                kind, payload = "comment", (t, "")

        # 相邻性：正常行是「上一行槽位 + 1」（整数）。**折行续行**用的是小数槽位
        # （见 ``group_rows``：它卡在两槽之间，撞槽时落在 k±0.5），所以判据放宽成
        # 「上一行槽位 < 本行槽位 ≤ 上一行槽位 + 1.5」—— 整数槽位只有 prev+1 能进来，
        # 与原来完全等价；小数槽位（k±0.5）也认，但它只可能是折行，不会误收别的行。
        adjacent = (not require_adjacent_socket) or (
            prev is not None and prev[0] == panel
            and sock is not None and prev[1] is not None
            and prev[1] < sock <= prev[1] + 1.5)

        if kind == "comment":
            username, content = payload                       # type: ignore[misc]
            out.append(_record(r, username, content, box, x_right, panel, sock))
            open_idx = len(out) - 1
            last_box = box
            open_base = box
            prev = (panel, sock)
            dropped = None
            card = None
        # 微博：进房/点赞等系统通知有时**没有自己的整数行**（``group_rows`` 只把它当折行
        # 挂成小数槽位、紧跟在评论下面）⇒ 走不到 ``classify_line`` 的系统行判据，会被当
        # 「折行续行」并进上一条评论的正文（实测 t=44.1：``这车这么稳`` 后面粘了两条
        # 进房通知）。无冒号 + 短语表是量过的：微博全量语料里无冒号且含「播间」的行
        # 445 条，逐条看过全是进房通知，真评论 0 条（见 ``_NOCOLON_NOTICE_RE``）。
        elif (kind == "continuation" and rank_pill and _wrapped_socket(sock)
              and _NOCOLON_NOTICE_RE.search(text)):
            # 与「丢弃行」同样的收尾：断开续接链，并让下一块折行也跟丢。
            open_idx = None
            last_box = None
            open_base = None
            prev = None
            dropped = (panel, sock)
            card = None
        # 浮层卡的**续行**（第二行没有特征词的那些，见文档字符串）：紧跟卡片行、同面板槽位
        # 相邻 ⇒ 也丢。只吃一行（吃完就清掉，不许顺着往下续链）。
        elif (kind == "continuation" and card is not None and card[0] == panel
              and sock is not None and card[1] is not None
              and card[1] < sock <= card[1] + 1.5):
            card = None
            dropped = None
        elif (kind == "continuation" and merge_continuation
              and open_idx is not None and adjacent
              and (not _has_own_badge(r)
                   or _wrapped_socket(sock)        # 折行（group_rows 过了间距闸才给的小数槽位）
                   or _indented_continuation(out[open_idx], r, pitch=row_pitch,
                                             prev_box=last_box, base_box=open_base))):
            rec = out[open_idx]
            text = payload                                    # type: ignore[assignment]
            rec["content"] = (f"{rec['content']}{merge_separator}{text}"
                              if rec["content"] else text)
            if box:
                rec["text_box"] = _union(rec["text_box"], box) if rec["text_box"] else box
            bb = r.get("badge_box")
            merged = _roi_of(r)
            if merged:
                rec["roi"] = _union(rec["roi"], merged) if rec["roi"] else merged
            if bb:
                rec["badge_box"] = (_union(rec["badge_box"], bb) if rec["badge_box"] else bb)
            if x_right:
                rec["panel_right"] = (max(rec["panel_right"], x_right)
                                      if rec["panel_right"] else x_right)
            # 续行的表情接到后面：一条评论的表情**按阅读顺序**排在正文之后。
            # 唯一的跨行合并规则：首行末尾与续行开头是**同一款** ⇒ 并成一条（刷屏的
            # ``👍×6`` 会被折行成 ``4 + 2``，不并就数成两条，语义上「强调」被拆散）。
            # 只认「末尾 vs 开头」这一对，不做全量重排 —— 跨行的框 y 不同，按 x 全局排序
            # 会把两行的表情交错在一起。
            em = list(r.get("emojis") or [])
            if em:
                cur = list(rec.get("emojis") or [])
                head = em[0]
                if cur and (cur[-1].get("token") or "unknown") == (head.get("token") or "unknown"):
                    cur[-1]["n"] = int(cur[-1].get("n", 1)) + int(head.get("n", 1))
                    cur[-1]["score"] = min(cur[-1]["score"], head["score"])
                    em = em[1:]
                rec["emojis"] = cur + em
                rec["emoji_text"] = emoji_text_of(rec["emojis"])
            rec["lines"] += 1
            rec["sockets"].append(sock)
            last_box = box
            prev = (panel, sock)
            dropped = None
            card = None
        # 系统行/通知行的**折行续块**：槽位是小数（``group_rows`` 过了间距闸、几何上
        # 证明它就是紧上一行折下来的），而紧上一行刚被当系统行丢掉 ⇒ 这一块也是那行的一部分。
        # 公告横幅会被折成 4~5 块（``欢迎使用微博直播服务…`` / ``微博站方将采取…`` / …），
        # 第一块命中短语丢掉后，后面的块没有特征词、又接不上任何评论，会自成一匿名评论
        # （实测 t=39.4 的整段公告被收成一条 4 行「评论」）。**只认小数槽位**：整数槽位
        # 的孤行可能是「昵称被 OCR 丢了」的真评论，照旧留（见下面那个分支）。
        elif (kind == "continuation" and dropped is not None and dropped[0] == panel
              and sock is not None and dropped[1] is not None
              and dropped[1] < sock <= dropped[1] + 1.5
              and _wrapped_socket(sock)):
            dropped = (panel, sock)      # 续着丢：下一块折行也接在这一行后面
            card = None
        # 系统行的**残句尾巴**（「失。」这类）
        elif (kind == "continuation" and _is_tail_fragment(payload)   # type: ignore[arg-type]
              and dropped is not None and dropped[0] == panel and sock is not None
              and dropped[1] is not None and dropped[1] < sock <= dropped[1] + 1.5):
            # 整段开播公告被折行切下来的最后一块（它不含任何系统短语，只能按形状认）。
            # **丢它比留着强** —— 留着它会自成一匿名评论，而它下面那行「昵称被 OCR 吃掉
            # 冒号」的真评论会并进来，变成 '失。 用户6861943086榜二这车这么稳'
            # （实测 t=5~30 每一帧都这样）。
            dropped = None
            card = None
        elif kind == "continuation":
            # 合并不了：槽位不相邻 / 没有上一条可接 / 关了合并 / **这行有自己的徽章**。
            # **别丢** —— 真实语料里这类行（'緣起'、'大家研究一下'）多是「用户名被 OCR
            # 丢了」的整条评论，丢了就是真的丢数据。留成 username=None 的匿名评论，
            # 它自己也能被下一行续接。
            out.append(_record(r, None, payload, box, x_right, panel, sock))  # type: ignore[arg-type]
            open_idx = len(out) - 1
            last_box = box
            open_base = box
            prev = (panel, sock)
            dropped = None
            card = None
        # 纯表情**续行**：OCR 没有文字、只有表情的行（抖音长表情评论折成多行时常见）。
        # 槽位与上一条相邻（socket 差 ≤1.5）才并；opt-in —— 它与「另一位用户只发表情的
        # 独立评论」形状相同，默认维持「各成一条」，配置到具体素材再开。
        elif (merge_emoji_continuation and payload in ("碎片行", "空行")
              and open_idx is not None and adjacent
              and any(e.get("token") for e in (r.get("emojis") or []))):
            rec = out[open_idx]
            em = list(r.get("emojis") or [])
            cur = list(rec.get("emojis") or [])
            head = em[0]
            if cur and (cur[-1].get("token") or "unknown") == (head.get("token") or "unknown"):
                cur[-1]["n"] = int(cur[-1].get("n", 1)) + int(head.get("n", 1))
                cur[-1]["score"] = min(cur[-1]["score"], head["score"])
                em = em[1:]
            rec["emojis"] = cur + em
            rec["emoji_text"] = emoji_text_of(rec["emojis"])
            merged = _roi_of(r)
            if merged:
                rec["roi"] = _union(rec["roi"], merged) if rec["roi"] else merged
            rec["lines"] += 1
            rec["sockets"].append(sock)
            prev = (panel, sock)
            last_box = box
            dropped = None
            card = None
        elif (payload in ("碎片行", "空行")
              and any(e.get("token") for e in (r.get("emojis") or []))):
            # **纯表情评论**：正文是图片表情，OCR 只认文字 ⇒ 这一行常常只剩打码昵称的
            # 残渣（``***``）甚至一个空串，被判成「碎片行」丢掉。可它是一条**真评论**，
            # 而且表情已经认出来了（``r["emojis"]`` 带 token，见 rows_from_video）——
            # 丢了就等于「心和打call、比心没识别出来，这条评论也没圈」（用户报的现场：
            # ``◆4 S***：❤️❤️❤️`` / ``🐱🐱🐱`` 三行在标注图上一个框都没有）。
            # 只放宽这两类空壳：通知/系统行/浮层卡照旧丢 —— 它们也常带表情
            # （``欢迎 xx 来到直播间🌹``），放宽了就是往交付里灌垃圾。
            # 正文留空串，内容由 ``emoji_text`` 补（见 _record / user_filter 的判定键）。
            out.append(_record(r, None, "", box, x_right, panel, sock))
            open_idx = len(out) - 1
            last_box = box
            open_base = box
            prev = (panel, sock)
            dropped = None
            card = None
        else:
            # 丢弃的行（通知/提示/系统行/浮层卡/碎片）：**断开续接链** —— 中间隔着别的行槽，
            # 后面那行就不可能是这一条的续行。
            open_idx = None
            last_box = None
            open_base = None
            prev = None
            # 只有**系统行**才让下一行有机会被判成它的残句尾巴（见上面那条分支）。
            dropped = (panel, sock) if payload == "系统行" else None
            # 只有**浮层卡**才让下一行有机会被判成它的续行（见上面那条分支）。
            card = (panel, sock) if payload == "浮层卡" else None

    if right_pad is not None:
        for rec in out:
            if rec["roi"] and rec["panel_right"]:
                rec["roi"] = [rec["roi"][0], rec["roi"][1],
                              max(rec["roi"][2], int(rec["panel_right"]) - right_pad),
                              rec["roi"][3]]
    return out
