"""livepipe.parsing.ocr.lines — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from ..constants import BADGE_INSET
from ..constants import BADGE_SPAN
from ..constants import WRAP_GAP_RATIO
from ..constants import WRAP_MIN_GAP_RATIO
from ..text.cleaning import _is_junk
from ..text.patterns import _COLON_RE



# 「药丸碎片」判据的三个数（见 ``_is_pill_fragment``）——都是**这块素材量出来的**，
# 不是拍的：seg1+seg6 的 2693 行里药丸画在 x∈[22,95]（裁切内），而徽章列按
# ``x_text − BADGE_SPAN`` 算出来是 [38,96)（seg1–6）/ [28,86)（seg7，0.82× 缩放）。
PILL_FRAG_SLACK_L = 16      # 药丸墨迹能左伸到徽章列左沿之外多少（95−38=57 是右沿，22 是左沿）
PILL_FRAG_MAX_DIGITS = 3    # 药丸里最多几位数字（◆ 被读成数字时 13→013；等级标本身 ≤2 位）
PILL_FRAG_MAX_W = 56        # 碎片最宽（实测 ◆+两位数 ≈ 49px，再宽的就不是药丸了）


def _is_pill_fragment(x1: int, x2: int, text: str, panel: dict) -> bool:
    """这块墨是不是**等级药丸的一截**（宽药丸只剩数字那一半被 det 框住）？

    为什么不能只看几何：这块素材量出来的三簇坐标**在同一段里是重叠的**（seg1+seg6，
    2693 行）——

      * 药丸墨迹   ``x∈[22,95]``（宽度 = BADGE_SPAN − BADGE_INSET = 50 的由来）；
      * 折行续行   ``x∈[37,45]``（手机 App 的缩进续行，**正面落在药丸带里**）；
      * 无药丸昵称 ``x∈[10,15]``（非粉丝团观众，昵称顶到面板左内边距）。

    ``_is_wrapped_line`` 那条「贴左沿一律算整行正文」的口子为了保住 37–45 的续行，把闸口
    设在 ``x_text − 50``（seg1–6 = 46、seg7 = 36）—— 于是**起步在闸口左边的宽药丸被一起
    吃掉**：只有一位数、det 又框到 x≥46 的窄药丸才幸免。现场就是用户报的这张
    ``好***：好用``：det 在交付帧上把 ``13`` 读到了 1.00 分、框在 x∈[31,67]，却因为
    31 < 36 被判成整行、再被间距闸当横幅丢掉 ⇒ 这条评论落进「无标」档（bug-386 的现场，
    同一帧里 ``18``/``28`` 同样被吃）。

    分开这两簇的唯一线索是**内容**：续行是正文（汉字，``待！``/``真香``/``6666``），药丸
    只有数字（◆ 常被读成 ``0``/``o``/``+``）。所以这里要求「纯数字 + 窄 + 整块都在徽章列
    里」，几何只用来圈范围（左边给 ``PILL_FRAG_SLACK_L`` 的余量，右边不许越过 ``x_text``）。

    三条边界都是**宁可漏**：数字位数超了、块太宽、右沿越过 ``x_text`` ⇒ 一律不算碎片，
    照旧走老路（继续当整行、由间距闸定去留）。
    """
    if _COLON_RE.search(text) or not _is_junk(text):
        return False        # 有冒号 / 有汉字字母 ⇒ 正文（续行、无标昵称、通知行）
    digits = [ch for ch in text if ch.isdigit()]
    if not digits or len(digits) > PILL_FRAG_MAX_DIGITS:
        return False        # 没数字是装饰符号；4 位以上不是等级标（桌面版的 `6666` 续行）
    if x2 - x1 > PILL_FRAG_MAX_W:
        return False
    xt = panel["x_text"]
    return (x1 >= max(BADGE_INSET, xt - BADGE_SPAN - PILL_FRAG_SLACK_L)
            and x2 < xt)


def _in_badge_gutter(x1: int, x2: int, text: str, panel: dict) -> bool:
    """这块是不是「左侧等级徽章列」的碎片（该丢）？

    判据必须带**内容**，不能只看 x1 —— 否则会连整行正文一起丢，实测两种翻车现场：
      * ``'2。hubvg:'``  x1=34：OCR 把徽章和昵称并成了同一个块，块左沿被徽章拖到
        ``x_text`` 左边，丢它整行就没了；
      * ``'梁红：'``    x1=9 ：该用户没有等级徽章，昵称直接从面板最左边开始。
    这两种块都带冒号，是正文；只有「没冒号的左侧碎块」（纯数字徽章、装饰符号）才是
    要丢的徽章列。

    光有「没冒号 + ``x1 < x_text``」还不够 —— **折行续行**也满足这两条（它从面板左沿
    起、没有冒号），于是整条续行被当徽章丢掉，正文就少了半句（bug-229 的现场：
    ``念口：一集一集解锁跟闯关一柱`` 的续行 ``有成就感，我好喜欢，兄弟们下个广告见！``
    整行不见）。所以要开一个口子：**顶在面板裁切左沿上**的块是整行正文
    （见 ``BADGE_INSET`` 那段量的两个区间），留下。

    口子开得很窄，是故意的：只有「从面板左沿起」的块才放行，其余一律照旧丢。徽章碎片
    的 x1 实测 ≥12，一个也进不来；``'哈**'``（x∈[51,120]，贴边的通知碎片）同样在门外。

    这里只判**形状**（``_is_wrapped_line``）。形状像的里面还混着左对齐的**系统横幅**，
    它们该丢不该拼 —— 那一步要看「跟上一行的间距」，得等普通行都归好槽才判得了，
    所以放在 ``group_rows`` 里（见那边的 ``WRAP_GAP_RATIO`` 闸门）。

    先过一道**内容**闸（``_is_pill_fragment``）：形状闸会漏掉**宽药丸**（它的数字那一半
    起步在闸口左边），而那一半是纯数字、跟续行和昵称都分得开 —— 这是 bug-386。
    """
    if _COLON_RE.search(text):
        return False
    if _is_pill_fragment(x1, x2, text, panel):
        return True
    if _is_wrapped_line(x1, x2, text, panel):
        return False
    return x1 < panel["x_text"]


def _is_wrapped_line(x1: int, x2: int, text: str, panel: dict) -> bool:
    """这块的**形状**像不像一条整行（折行续行 / 左对齐的系统横幅）？

    分两段判，因为「续行从哪起」**随版式变**：

      * **贴面板裁切左沿**（``x1 < x_text - BADGE_SPAN + BADGE_INSET``）：桌面网页版抖音
        的折行续行顶在面板左沿（实测 x1 ∈ [0..3]）。这一段一律算整行正文 —— 数字续行
        （``6666``）也在这条线上，不能因为「纯数字」把它当徽章。

        这一段**吞掉的宽药丸**由 ``_in_badge_gutter`` 里的 ``_is_pill_fragment``（内容闸）
        先捞走：它判「纯数字 + 窄 + 整块在徽章列里」，正是这一段与缩进带里唯一分得出的
        那一类（bug-386）。这里不再重复那道判据 —— 形状层只回答「像不像整行」。
      * **缩进带**（``x_text - BADGE_SPAN + BADGE_INSET ≤ x1 < x_text``）：**手机 App 版**
        的折行续行是**缩进**的，对齐到气泡的正文内边距（实测 x1 ≈ 37–45），正好落进药丸带。
        但网页版这一段里坐着的是**左对齐通知的碎片**（实测 ``哈**`` x∈[51,119]，是
        ``哈** 赞了直播`` 被 OCR 切开的一半）—— 必须照旧当徽章碎片丢（bug-204/229）。
        两种版式在这一段里**长得一样**，分不开，所以这一段只对**开了 ``wrap_indent`` 的面板**
        （自标定测到缩进续行才开，见 ``calibrate_panels``）才按续行认；默认关，行为与从前一致。

    为什么不能只看左沿：网页版靠「续行贴左沿、徽章在药丸带」这条版式事实分得开，手机版
    续行**缩进**进药丸带，两者左沿重叠 —— 只按 x1 判会把续行整块当徽章丢掉（多行评论就
    散成幽灵匿名评论）。这是「同平台不同缩放」的又一个落点，所以缩进带按面板开关。

    这是**纯形状**判据，左对齐的**系统横幅**（``加入了直播间`` / ``宋辉来了``）也命中 ——
    它们同样是从面板左沿起的整行。所以别拿它当「就是续行」用：真正确认要加一道**间距**闸
    （续行紧贴首行、横幅占满一个行距），那一步在 ``group_rows`` 里（``wrap_gap_ratio``）。
    这里只负责「排除徽章碎片」。
    """
    if _COLON_RE.search(text):
        return False
    return _is_wrapped_shape(x1, x2, text, panel)


def _is_wrapped_shape(x1: int, x2: int, text: str, panel: dict) -> bool:
    """``_is_wrapped_line`` 的**形状那一半**（不看冒号）。

    单独拆出来是给「带冒号的折行」用的（见 ``_colon_wrap_candidates``）：形状判据本身
    跟冒号无关，冒号只用来把「昵称：正文」的整行挡在**硬入口**之外 —— 那一类没过间距闸
    就**丢**，而带冒号、左对齐的块里坐着真评论（昵称没有等级徽章时顶到面板左沿）。
    """
    if x1 < panel["x_text"] - BADGE_SPAN + BADGE_INSET:
        return True                       # 贴左沿：一律算整行正文（含数字续行）
    if not panel.get("wrap_indent"):
        return False                      # 网页版：缩进带里的块照旧当徽章碎片
    if _is_junk(text):
        return False                      # 缩进带里的纯徽章药丸 ⇒ 不是正文
    return x1 < panel["x_text"]



def _is_true_wrapped_line(yc: float, row_centers: list, pitch: int,
                          gap_ratio: float = WRAP_GAP_RATIO,
                          min_gap_ratio: float = WRAP_MIN_GAP_RATIO) -> bool:
    """「形状像整行」的块到底是不是**折行续行**？看它跟上一行的**间距**。

    ``centers`` 是这一帧同面板里**已经落定的行**的 y 中心 —— 普通行，加上调用方
    **按 y 顺序逐条认下来的续行**（一条评论可以折成 3 行，见 ``group_rows`` 里的说明）。
    两个方向都要卡，缺一个都会漏（两个常量各自的注释里有全片实测分布）：

      * **上沿** ``gap_ratio``：够不着上一行的是**左对齐系统横幅**（占满一个行距）；
      * **下沿** ``min_gap_ratio``：「上一行」其实就是**同一行被 OCR 切开的另一半**
        （`言之有李` + `来了`，间距 ≈0）时，这块是横幅的**头部**，不是续行。

    两个比值都是**版式属性**（网页版续行 ≈0.63 行距、横幅 ≥1.21），所以现在**按面板给**
    （``panel["wrap_gap_ratio"]``）—— 手机 App 版续行间距实测 ≈0.74–0.76 行距，比网页版
    松，用网页版那组常数会把续行全挡在门外。自标定见 ``calibrate_panels``。

    没有上一行的（面板顶端、上一行已滚出屏幕）一律不算 —— 没有首行可拼，
    留着只会多一条「用户名丢了」的假评论。
    """
    above = [y for y in row_centers if y < yc]
    if not above:
        return False
    gap = yc - max(above)
    return pitch * min_gap_ratio <= gap <= pitch * gap_ratio
