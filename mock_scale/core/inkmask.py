"""在标注框内**定位用户名**并生成擦除蒙版。

这一步对应 work/test 里 ``text_geometry.py`` 的角色，但**刻意做得简单得多** ——
本实验要回答的是「Telea 够不够干净」，所以定位环节必须**足够可靠，不能把误差带进结论**。

判据全部建立在**可测量的版面事实**上，而不是「猜哪个是名字」：

1. 背景是近乎纯色（实测 mode 色 ``(37,37,49)``）⇒ 与背景差得远的像素就是「墨迹」；
2. 把框内墨迹按**列**投影，得到若干**列段**：徽章 / 名字的几个字 / 冒号 / 正文；
3. 徽章 = **最左**那一段，且它是个**实心药丸** —— 用「填充率 ≥ 0.60 且宽 ≥ 40px」
   与一切文字区分（实测徽章 0.68~0.95，汉字 ≤ 0.66，冒号 ≤ 0.28）；
4. 用户名 = 徽章之后**连续**若干段「核心色够饱和」的（实测饱和度 70~80）；
5. 冒号 = 紧跟用户名之后的那**一段**（实测宽 4~5px、饱和度 31~42）。

第 4 条用**颜色**而不是位置来切，是踩过坑的结论。曾经用「最右段是正文，其余是名字」
这种纯位置规则，8 张里错了 6 张 —— 因为**正文里字与字的空隙会把正文切成多段**
（7.jpg 的正文就是 ``[198-253]/[255-280]/[310-421]`` 三段），按位置取「最后一段」
会把正文的前两段**当成名字一起擦掉**。而颜色的分离度是干净的：用户名 70~80、
冒号 31~42、正文（白）4~6，中间是空档。

第 5 条之后不再往下切 —— 名字和冒号本来就要一起擦掉、一起重写，多切一刀只会多一个
出错的地方。于是「名字里有几个字」「名字里夹不夹 emoji」都不影响**定位**的结果。

⚠️ 但 emoji **不影响定位 ≠ 不影响出片**：`inkmask` 把「名字段里有没有 emoji」
一并报出来（`EraseRegion.name_emoji`），由调用方决定怎么办 —— 批量流程据此**跳过
整张图**（2026-09-30 的规格：昵称含表情的不出片，且跳过的图不计入任何数量、只留绝对路径）。

⚠️ **本模块依赖「用户名是框内唯一的彩色文字」这个前提**（见 ``config.USERNAME_SAT_MIN``）。
视频号那类「昵称不饱和、正文更白」的截图不满足它 —— work/test 在那种语料上定位成功率
只有 26.7%。本实验的语料（8 张抖音截图，昵称一律青色）全部满足，但**别把这个前提
带进别的语料**。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

import config
import platforms

LOGGER = logging.getLogger(__name__)


class RegionNotFoundError(RuntimeError):
    """在框内找不出「用户名 + 冒号」这个结构时抛出。

    宁可硬失败也不要猜 —— 猜错的代价是**擦掉不该擦的东西**（正文、徽章），
    而那种错误在批量跑的时候很难被肉眼发现。
    """


@dataclass
class EraseRegion:
    """一次擦除操作的全部几何与外观信息。坐标一律是**原图绝对坐标**。

    Attributes:
        mask:        uint8 单通道蒙版，与原图同尺寸，255 = 要擦的像素。
        erase_box:   擦除范围的包围盒 ``(x0, y0, x1, y1)``（半开区间，右/下不含）。
        box_rect:    **内缩后**的标注框 ``(x0, y0, x1, y1)``（半开）—— 也就是描边
                     之内、可以安全动笔的那块地。平移尾巴的矩形要夹在它里面：
                     超出哪怕 1px 就会碰到绿色描边的抗锯齿边，把它**拖着走**
                     （bug-154：4/5/6/7.jpg 各有几百个像素的描边被搬了家）。
        span:        用户名 + 冒号所占的**列区间** ``(x_start, x_end)``（含端点）。
        slot:        原**用户名墨迹**占的列区间 ``(left, right)``（含端点）。
                     **新名字左对齐写在这里** —— 名字本身可用的宽度是 ``slot_width``，
                     但整条装配能用的宽度是 ``max_name_width``（多出来的部分靠尾巴
                     右移腾地方）。
        colon_span:  原**冒号墨迹**占的列区间 ``(left, right)``（含端点）。
        colon_rows:  冒号列区间里**落在名字行带内**的有墨行区间 ``(top, bottom)``（含端点）。
                     量「名字↔冒号」间距的窗口，原图与结果图共用它才可比。
        tail_span:   冒号及其右侧**一切墨迹**的列区间 ``(left, right)``，即
                     「冒号 + 正文」这一整条尾巴。规格要求正文的相对距离也不变
                     ⇒ 尾巴要**整体平移**，所以量的是它的包围盒，不是正文的左缘。
        tail_rows:   尾巴墨迹的**真实**行区间 —— 在框上下各放开
                     :data:`config.TAIL_WIN_VEXT` 行的窗口里量的，所以**可以探出标注框**
                     （正文里的 emoji 比汉字高，实测 `天-3/1.jpg` 框 1223~1253、墨迹 1222~1257）。
        tail_win:    允许尾巴搬家的**行窗口** ``(y0, y1)``：放开的窗口再削掉绿色描边
                     （及其抗锯齿边），平移矩形只许落在这里面。
        box_right:   标注框**内缩后**的右边界。尾巴平移后不许越过它 ——
                     「整体是否超过框，是则截断」的那条界线。
        glyph_top:   原用户名墨迹的**起始行**（绝对），用于对新文字做基线对齐。
        glyph_bottom:原用户名墨迹的**结束行**（绝对，含）。
        text_color:  原用户名的主体颜色，写新名字时沿用。
        bg_color:    框内背景的 mode 色。
        badge_found: 框内是否检测到徽章（决定擦除区左边界的解释）。
        comment_left:右侧正文的**原**左边界（绝对列号）。仅供诊断与搜索范围用 ——
                     尾巴整体平移之后，正文实际在哪一列由 ``shift_tail`` 的返回值决定，
                     这个字段**不再是结果图里的位置**。
        segments:    诊断用 —— 列段 ``(start, end, 填充率×100, 平均亮度)``。
        name_emoji:  名字段里**不像名字文字**的那几段，``(宽, 墨量, 段高, 彩度)``。
                     空列表 = 名字是纯文字。判据见 :data:`config.NAME_EMOJI_SAT_MAX`
                     那一段 —— 这里**只报告证据、不做决定**：擦不擦、跳不跳是调用方
                     的事（批量流程据此跳过整张图，见 ``batch_run.Row.skip``）。
    """

    mask: np.ndarray
    erase_box: tuple[int, int, int, int]
    box_rect: tuple[int, int, int, int]
    span: tuple[int, int]
    slot: tuple[int, int]
    colon_span: tuple[int, int]
    colon_rows: tuple[int, int]
    tail_span: tuple[int, int]
    tail_rows: tuple[int, int]
    box_right: int
    glyph_top: int
    glyph_bottom: int
    text_color: tuple[int, int, int]
    bg_color: tuple[int, int, int]
    badge_found: bool
    comment_left: int
    segments: list[tuple[int, int, int, int]] = field(default_factory=list)
    name_emoji: list[tuple[int, int, int, int]] = field(default_factory=list)
    join_gap: int = 0
    """原图里「用户名墨迹右缘 → 冒号墨迹左缘」之间**空了几列**（**按墨迹量出来的**）。

    这是规格里的「『：』距离名字的距离」，新名字写完后它必须**还是它** ——
    做法是把整条尾巴（冒号 + 正文）一起平移 ``新名字宽 - 槽位宽``，于是名字与冒号之间、
    冒号与正文之间的距离都按构造不变（见 ``draw_text.shift_tail``）。

    ⚠️ **不能用「列段左缘 − 槽位右缘」推**（那正是 bug-151 的反面）：列段的左缘由**任何一行**
    的墨迹决定，两行框里同一列在第二行也有字 ⇒ 推出来的值比真实间距小几个像素，
    3 张图当场报 ``GAP_BAD``。这里与 ``replace_username.gap_now`` **同一把尺子**：
    同一个行窗口里、名字右缘之后的第一条墨迹列。
    """
    multi_line: bool = False
    """框内是否**装了不止一行文字**（按行带判，见 :data:`config.ROW_BAND_MIN_ROWS`）。

    True 时本次定位的所有**上下**几何（字带、擦除蒙版的上下、冒号/尾巴的行范围）
    都已被**夹进名字所在的那一条行带** —— 否则会把第二行评论一起擦掉（bug-168）。
    """
    geom_used: str = ""
    """上游几何参与到了哪一步：``""`` 没用 / ``band`` 只用来收窄取样与改动窗口 /
    ``band+colon`` 还用 ``colon_box`` 定了名字与冒号的分界（2026-10-28）。"""
    line_band: tuple[int, int] = (0, 0)
    """本次实际使用的**行带**（绝对行号，闭区间）。单行框下 = 名字字带的上下界。"""
    tail_win: tuple[int, int] = (0, 0)
    """尾巴搬家的**行窗口**（绝对行号，闭区间；见 :func:`_tail_real_rows`）。

    ``(0, 0)`` = 没量到（调用方退回老口径：搬家的上下夹在标注框里）。
    窗口 = 标注框上下各放开 :data:`config.TAIL_WIN_VEXT` 行、再削掉绿色描边与其
    抗锯齿边之后的那一段；``tail_rows`` 一定落在它里面。
    """
    badge_span: Optional[tuple[int, int]] = None
    """**等级标**墨迹的列区间（绝对列号，含端点）；``None`` = 这次不擦等级标。

    只有 ``find_erase_region(erase_badge=True)`` 且真检测到等级标（``badge_found``）
    时才有值。两条定位策略的等级标都在名字**左边**（``segs[first - 1]``），
    擦掉它之后新名字写哪一列见 :attr:`name_left`。
    """
    capsule_head: Optional[int] = None
    """评论行底下那颗半透明深色**胶囊的左圆头**在哪一列（绝对列号）；``None`` = 没量到。

    只有擦等级标时才会去量（:func:`_capsule_head`）。它管两件事：

    * 新名字起写列 = 它 + :data:`config.CAPSULE_HEAD_NAME_PAD`（固定值，见 :attr:`name_left`）；
    * :attr:`badge_rect` 往左不许越过它（那颗胶囊的圆角得原样留着），
      并且那块地**从右边取背景**（见 :attr:`badge_fill_sides`）。
    """
    badge_rect: Optional[tuple[int, int, int, int]] = None
    """等级标要**填平**的那块地 ``(x0, y0, x1, y1)``（绝对、**半开**）；``None`` = 不填。

    比 :attr:`badge_span` 多出两样东西：蒙版膨胀那一圈（列方向）、以及药丸**探出内缩框**
    的行（实测 `于亚洲3-8/5.jpg` 的药丸 1195~1229、内缩框只有 1196~1227）——
    它在整幅图上量，见 :func:`_badge_rect`。调用方拿它去
    :func:`inpaint_local.fill_local` 整块铺平（**不是**按蒙版修补：
    探出框那两行本来就在蒙版之外）。
    """

    @property
    def name_left(self) -> int:
        """新名字**从哪一列起写** —— 「离胶囊头的距离**固定**」就固定在这里。

        只有三种可能（用户 2026-10-11「还得计算胶囊头距离用户名的距离变成固定值」）：

        ==========================================  ==================================
        情形                                         起写列
        ==========================================  ==================================
        没擦等级标（``badge_span is None``）            原名字的左沿 ``slot[0]``
        擦了、量到了胶囊头                             **``capsule_head + CAPSULE_HEAD_NAME_PAD``**
        擦了、没量到胶囊头（框贴着名字起的抖音图）      等级标墨迹左沿（现状）
        ==========================================  ==================================

        第一行是**原图说了算**：本来就没有等级标的行，名字离胶囊头 24±1px（实测），
        我们不该去挪它（挪了就是拿检测误差当版式）。第三行是零回归的退路。

        ⚠️ 固定间距若**顶到或越过原名字左沿**（``fixed > slot[0]``，病态几何：量出来的头
        太靠右）也退回第三行 —— 宁可不改，也不把整条名字+尾巴往右推。
        ⚠️ 用 :attr:`badge_span` / :attr:`capsule_head` 而不是各自的 rect：rect 带膨胀圈，
        拿它当写入原点会让名字白左移 3px。
        """
        if self.badge_span is None:
            return self.slot[0]
        if self.capsule_head is not None:
            fixed = self.capsule_head + config.CAPSULE_HEAD_NAME_PAD
            if fixed <= self.slot[0]:
                return fixed
        return self.badge_span[0]

    @property
    def badge_fill_sides(self) -> tuple[str, ...]:
        """铺平 :attr:`badge_rect` 那块地时**从哪几边取背景**（交给 ``fill_local``）。

        量到胶囊头 ⇒ **只取右边**：药丸左边紧挨着的是**胶囊头**，取左边等于拿页面色往胶囊
        上涂（实测:胶囊头那几列从 30 级被刷成 42 级）。药丸是坐在胶囊里的，它那块地本来就
        该是胶囊色，而右边（名字擦掉之后）就是胶囊色。
        没量到头（不是胶囊底、或抖音那种贴名字的框）⇒ 老办法左右过渡（现状，零回归）。
        """
        return ("right",) if self.capsule_head is not None else ("left", "right")

    @property
    def name_span_width(self) -> int:
        """新名字这条**装配**的基准宽度 = :attr:`name_left` → 原名字右缘。

        不擦等级标时它就是 ``slot_width``。擦掉之后它更宽（多出等级标那一块）——
        尾巴的位移按**它**算（``plan_name(shift_base_width=…)``）：名字右缘因此永远
        落回原名字的右缘，「名字↔冒号」的间距按构造成立（这条不变量与等级标无关）。
        ⚠️ 它与 ``slot_width`` 不能合并成一个数：「补满槽位」和选材都按
        ``slot_width``（原名字自己多宽）—— 拿这块地当补满目标，一个 5 字假名会被
        糊上一串随机字把等级标让出来的地方又填满，尾巴一动不动。
        """
        return self.slot[1] - self.name_left + 1

    @property
    def has_emoji(self) -> bool:
        """名字里是否含 emoji（判据见 :data:`config.NAME_EMOJI_SAT_MAX`）。"""
        return bool(self.name_emoji)

    @property
    def emoji_note(self) -> str:
        """一行说清 emoji 的证据（进日志/报告用），如 ``w28/墨556/高29/彩191``。"""
        return " ".join(
            f"w{w}/墨{ink}/高{h}/彩{sat}" for w, ink, h, sat in self.name_emoji
        )

    @property
    def glyph_height(self) -> int:
        """原用户名的墨迹高度（像素）。"""
        return self.glyph_bottom - self.glyph_top + 1

    @property
    def slot_width(self) -> int:
        """原用户名墨迹的宽度（像素）= 新名字可以**不惊动尾巴**就占满的宽度。"""
        return self.slot[1] - self.slot[0] + 1

    @property
    def tail_margin_right(self) -> int:
        """尾巴平移矩形的**右缘（不含）** = ``min(墨迹右缘 + 1 + pad, 框右边界)``。

        ⚠️ 这个算式必须与 ``draw_text.shift_tail`` 里 ``sx1`` 的算法**一模一样**。
        两处差 1px，预算就多给 1px：名字用满预算时尾巴会被夹紧，间距从 5 变成 6，
        校验立刻报 ✗。所以两边都读 ``config.TAIL_MOVE_PAD``，并把「夹到框」写进同一个式子。
        """
        return min(self.tail_span[1] + 1 + config.TAIL_MOVE_PAD, self.box_right)

    @property
    def tail_slack(self) -> int:
        """尾巴还能往右挪多少像素 —— 一直挪到（内缩后的）框右边界为止。

        这是用户那句「整体是否超过框，是则截断」里的**可用余量**：4/5/6.jpg 的
        正文本来就已经顶到绿框上（余量 0px），3/9.jpg 有 8px，2/7/8.jpg 才松得开
        （92 / 78 / 91px）。
        """
        return max(0, self.box_right - self.tail_margin_right)

    @property
    def max_name_width(self) -> int:
        """新名字允许的**最大墨迹宽度** = 它要顶替的那块地 + 尾巴让得出的余量。

        「那块地」= :attr:`name_span_width`（不擦等级标时就是 ``slot_width``）。

        ⚠️ 只有 ``fit="clip"`` 用它当截断预算；``fit="width"`` 缩字号时用的是
        ``slot_width``（不许动尾巴），``fit="height"`` 压根不裁。
        """
        return self.name_span_width + self.tail_slack

    @property
    def mask_pixels(self) -> int:
        """蒙版覆盖的像素数 —— 花钱/花时间的是它，不是框的面积。"""
        return int((self.mask > 0).sum())


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------


def inset_box(
    box: tuple[int, int, int, int],
    inset: int,
    img_w: int,
    img_h: int,
    vinset: Optional[int] = None,
) -> tuple[int, int, int, int]:
    """把标注框向内收缩，排除彩色描边本身，并夹紧到图像范围内。

    Args:
        box:   原始框 ``(x0, y0, x1, y1)``，含描边。
        inset: 横向（左右）向内收缩的像素数。
        img_w: 图像宽度。
        img_h: 图像高度。
        vinset:纵向（上下）向内收缩的像素数；``None`` = 与 ``inset`` 相同（零回归）。

    ⚠️ 横纵要分开（2026-10-11，用户「擦除不干净」）：`manifest.roi` 是**紧贴那一行字**的框
    （实测高 30px 装 27~28px 的字，上下只富余 1~2px），此时纵向内缩 5px = **从字里切掉 5px**：
    ① 擦除蒙版被夹在「内缩后的地」里 ⇒ 名字最上/最下那几行**永远擦不到**（残墨）；
    ② 字带也被同一块地夹着 ⇒ 字高量成 20 而真值 27~28 ⇒ 新名字画小 26~29%。
    横向不一样：roi 横跨整行（徽章 + 名字 + 冒号 + 正文），名字离左右缘很远，
    内缩只削到首尾两端的背景 —— 所以**只放纵向**，横向照旧（`ROI_INSET`，见那边 docstring）。

    Returns:
        收缩并夹紧后的框。

    Raises:
        RegionNotFoundError: 收缩后宽或高 <= 0。
    """
    v = inset if vinset is None else int(vinset)
    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0 + inset), max(0, y0 + v)
    x1, y1 = min(img_w, x1 - inset), min(img_h, y1 - v)
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        raise RegionNotFoundError(
            f"标注框 {box} 内缩 {inset}px（纵向 {v}px）后没有剩余区域（图 {img_w}×{img_h}）"
        )
    return x0, y0, x1, y1


def _line_is_frame(
    line: np.ndarray,
    margin: int = config.FRAME_GREEN_MARGIN,
    ratio: float = config.FRAME_LINE_RATIO,
) -> bool:
    """这一行/列是不是**描边**（而不是文字、背景）。

    Args:
        line:   ``(N, 3)`` 一行或一列的像素。
        margin: 绿通道要比红蓝高多少才算「绿占优」。
        ratio:  绿占优像素的占比门槛。

    Returns:
        True = 整行/列都是描边。
    """
    if line.size == 0:
        return False
    p = line.astype(np.int32)
    green = p[:, 1] - np.maximum(p[:, 0], p[:, 2]) > margin
    return float(green.mean()) >= ratio


def shave_frame(
    img_rgb: np.ndarray,
    box: tuple[int, int, int, int],
    *,
    margin: int = config.FRAME_GREEN_MARGIN,
    ratio: float = config.FRAME_LINE_RATIO,
) -> tuple[int, int, int, int]:
    """把框的四条边**再往里削**，直到削掉所有还残留在框内的描边。

    ``inset_box`` 按固定像素数内缩，但描边的抗锯齿往框内糊多少**逐图不同** ——
    5.jpg 糊到框右缘 -7px，落在 ``BOX_INSET=6`` 之内，那一列会被当成正文墨迹，
    于是跟着尾巴被搬走、或被背景色涂掉，绿框内缘缺一块（bug-155）。

    这里改用**颜色**判据逐边削：整行/整列都「绿占优」才算描边。
    比「把 inset 调大」好，因为调大是全局的，会连字形上下缘一起切掉
    （3.jpg 的字下缘本来就贴着框）。

    Args:
        img_rgb: ``(H, W, 3)`` 原图。
        box:     已内缩的框 ``(x0, y0, x1, y1)``。
        margin:  绿占优的通道差门槛。
        ratio:   整行/列绿占优的占比门槛。

    Returns:
        削过之后的框；至少保留 1×1。
    """
    x0, y0, x1, y1 = box
    while x1 - 1 > x0 and _line_is_frame(img_rgb[y0:y1, x1 - 1], margin, ratio):
        x1 -= 1
    while x0 < x1 - 1 and _line_is_frame(img_rgb[y0:y1, x0], margin, ratio):
        x0 += 1
    while y1 - 1 > y0 and _line_is_frame(img_rgb[y1 - 1, x0:x1], margin, ratio):
        y1 -= 1
    while y0 < y1 - 1 and _line_is_frame(img_rgb[y0, x0:x1], margin, ratio):
        y0 += 1
    return x0, y0, x1, y1


def _tail_real_rows(
    img_rgb: np.ndarray,
    box_rect: tuple[int, int, int, int],
    bg_color: "np.ndarray | tuple[int, int, int]",
    tail_span: tuple[int, int],
    box_right: int,
    *,
    pad: int = config.TAIL_MOVE_PAD,
    vext: int = config.TAIL_WIN_VEXT,
    row_limit: Optional[tuple[int, int]] = None,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """量尾巴墨迹的**真实**行范围，以及允许它搬家的**行窗口**。

    过去 ``tail_rows`` 是在**框内那几行**上量的（``tail_ink = ink & tail_cols & rows_2d``），
    而框（``manifest.roi``）是**紧贴那一行字**的 —— 正文里的 emoji 比汉字高，
    就有一截落在框外，量不到也搬不走：

    * 量的那一步：搬到新位置的 emoji 顶/底被削平（看着像被裁了一刀）；
    * 搬的那一步（``draw_text.shift_tail``）：原位置**留半截 emoji**
      —— 用户 2026-10-11 报的「苦瓜-5/扩展1，移动正文，没有完全移动」。
      实测 `天-3/1.jpg`：框 1223~1253，尾巴墨迹 **1222~1257**（上探 1、下探 4），
      而绿色描边在 1214~1218 / 1258~1262 —— 探出的那 4 行就贴在描边内侧。
      全语料账（486 张：位移非 0 的 379 张里 222 张探出框，旧搬法共留 51323px 残墨，新搬法 0）
      见 `expand/扩展张数.md` §25。

    量法（三道闸，缺一不可）：

    1. **放开窗口**：标注框上下各放 ``vext`` 行（相邻评论行间距 ~130px，够不着）；
    2. ``shave_frame`` 削掉窗口里的绿色描边；
    3. **按覆盖率砍行**：整行铺满非背景像素的是描边的**抗锯齿边**（颜色判据放过它，
       见 :data:`config.FRAME_ROW_COVER`），不是这一行的文字 —— 不砍的话
       `霖霖日记-3/1.jpg` 会一路量到描边上（虚报下探 6px）。

    只按**尾巴的列**（``tail_span`` ∓ ``pad``，与 ``shift_tail`` 同一个式子）量：
    框里可能还有别的 UI（实测 `霖霖日记-3/1.jpg` 右缘有一条竖着的点赞栏），
    那不是尾巴的一部分。

    Args:
        img_rgb:   ``(H, W, 3)`` 原图。
        box_rect:  内缩+削过描边的标注框。
        bg_color:  框内背景色（框外同一行背景相同 ⇒ 平底假设，见 ``shift_tail``）。
        tail_span: 尾巴墨迹的列区间。
        box_right: 标注框右边界（尾巴不许越过）。
        pad:       与 :data:`config.TAIL_MOVE_PAD` 同源。
        vext:      上下放开多少行。
        row_limit: 允许量到的**绝对行区间**（闭区间），``None`` = 不夹。
                   多行框里由 :func:`find_erase_region` 传「本条行带 + 空隙中线」，
                   免得把隔壁那条评论的正文一起量进来、跟着搬走（bug-xxx）。

    Returns:
        ``(tail_rows, tail_win)``。量不到任何墨迹时退回 ``(框内那几行, 框)``。
    """
    ix0, iy0, ix1, iy1 = box_rect
    fallback = ((iy0, iy1 - 1), (iy0, iy1 - 1))
    if row_limit is not None:
        fy0, fy1 = max(iy0, row_limit[0]), min(iy1 - 1, row_limit[1])
        if fy1 < fy0:
            return fallback
        fallback = ((fy0, fy1), (fy0, fy1))
    sx0 = max(ix0, tail_span[0] - pad)
    sx1 = min(img_rgb.shape[1], ix1, box_right, tail_span[1] + 1 + pad)
    if sx0 >= sx1:
        return fallback
    wy0 = max(0, iy0 - vext)
    wy1 = min(img_rgb.shape[0], iy1 + vext)
    if row_limit is not None:
        wy0, wy1 = max(wy0, row_limit[0]), min(wy1, row_limit[1] + 1)
        if wy1 <= wy0:
            return fallback
    cx0, cy0, cx1, cy1 = shave_frame(
        img_rgb, (ix0, wy0, ix1, wy1),
        ratio=config.FRAME_EDGE_RATIO,
    )
    if cx1 <= cx0 or cy1 <= cy0:
        return fallback
    win = img_rgb[cy0:cy1, cx0:cx1]
    ink = compute_ink_mask(win, np.asarray(bg_color, dtype=np.uint8), config.BG_DIST_THRESH)
    # 描边的抗锯齿边：整行都是非背景像素 ⇒ 不是这一行的文字
    ink[ink.mean(axis=1) >= config.FRAME_ROW_COVER, :] = False
    cols = np.zeros(win.shape[1], dtype=bool)
    cols[max(0, sx0 - cx0) : max(0, sx1 - cx0)] = True
    ink &= cols[None, :]
    rows_hit = np.nonzero(ink.any(axis=1))[0]
    if len(rows_hit) == 0:
        return fallback
    ty0 = max(int(rows_hit.min()) + cy0, iy0 - vext)
    ty1 = min(int(rows_hit.max()) + cy0, iy1 - 1 + vext)
    if row_limit is not None:
        ty0, ty1 = max(ty0, row_limit[0]), min(ty1, row_limit[1])
        if ty1 < ty0:
            return fallback
    return (ty0, ty1), (cy0, cy1 - 1)


def _capsule_head(
    img_rgb: np.ndarray,
    box_rect: tuple[int, int, int, int],
    rows: tuple[int, int],
    x_from: int,
    *,
    min_contrast: int = config.CAPSULE_HEAD_MIN_CONTRAST,
    max_pad: int = config.CAPSULE_HEAD_MAX_PAD,
    ref_px: int = 8,
    page_px: int = 14,
) -> Optional[int]:
    """量「这条评论底下那颗**半透明深色胶囊**」的**左圆头**在第几列（2026-10-11）。

    为什么要它：等级标坐在胶囊里、离胶囊头 ~9px，而名字（没有等级标时）离胶囊头 ~24px
    ⇒ 擦掉等级标后名字若从药丸左沿起写，就比版式该有的内边距少 15px（用户：
    「计算胶囊头距离用户名的距离变成固定值」）；同时 :func:`_badge_rect` 往左多吃投影那
    一步会**越过胶囊头**、把它的圆角刷平成页面色（用户：「得保留背景的胶囊头」）。
    两件事都要先知道头在哪。

    量法（只用**扫描起点左边**那一段，那里一定没有字）：

    1. 「胶囊里面」= ``x_from`` 左边 ``ref_px`` 列（跳过紧邻的 1 列，避字形/药丸的抗锯齿）的
       **逐列中位数**；「页面底色」= 内缩框左沿起 ``page_px`` 列的中位数。
       页面必须比胶囊亮 ``min_contrast`` 级，否则**这不是胶囊** ⇒ ``None``（零回归那条路）。
    2. 从 ``x_from - 1`` 往左扫，第一列亮过两者中点的，就是**胶囊外面** ⇒ ``head = 它+1``。

    ⚠️ 中位数而不是均值：这几列里混着 JPEG 噪声、药丸/字形的抗锯齿边，均值会被拽走。

    ⚠️ 三道闸都会让它返回 ``None``（**宁可不改**）：扫描起点左边不足 ``ref_px + 2`` 列；
    一路扫到内缩框左沿都没跨过中点（整条都是胶囊）；量出来的头离扫描起点超过 ``max_pad``
    （真头 9~24px，几十上百 px 说明扫到的是别的东西）；以及**头与起点之间不足 2 列胶囊**
    （第一列就已经是页面色 ⇒ 左边那点暗色不是胶囊）。

    Args:
        img_rgb: ``(H, W, 3)`` 原图。
        box_rect: 内缩+削过描边的标注框 ``(x0, y0, x1, y1)``，半开。
        rows:     行带 ``(top, bottom)`` **闭区间** —— 传名字的墨迹行（``glyph_band``），
                  它整个落在胶囊里面，量出来的头才是圆头最宽处。
        x_from:   扫描起点（绝对列号，**不含**）：药丸左沿 − 1 / 名字左沿 − 1。

    Returns:
        胶囊头那一列的绝对列号；量不到返回 ``None``。
    """
    ix0, iy0, ix1, iy1 = box_rect
    y0, y1 = max(iy0, int(rows[0])), min(iy1, int(rows[1]) + 1)
    x_from = min(int(x_from), ix1 - 1)
    if y1 - y0 < 6 or x_from - ix0 < ref_px + 2:
        return None

    col = np.median(img_rgb[y0:y1].astype(np.float32).mean(axis=2), axis=0)
    inside = float(np.median(col[x_from - ref_px : x_from - 1]))       # 胶囊内部
    page = float(np.median(col[ix0 + 1 : min(ix1, ix0 + page_px)]))    # 胶囊头左边的页面底
    if page - inside < min_contrast:
        return None
    mid = (inside + page) / 2
    head = None
    for x in range(x_from - 2, ix0, -1):
        if col[x] >= mid:
            head = x + 1
            break
    if head is None or head <= ix0 or x_from - head > max_pad:
        return None
    return int(head)


def _badge_rect(
    img_rgb: np.ndarray,
    box_rect: tuple[int, int, int, int],
    badge_span: tuple[int, int],
    bg: "np.ndarray | tuple[int, int, int]",
    *,
    pad: int = config.MASK_DILATE_PX,
    vext: int = config.TAIL_WIN_VEXT,
    shadow: int = config.BADGE_SHADOW_PX,
    head: Optional[int] = None,
    row_limit: Optional[tuple[int, int]] = None,
) -> Optional[tuple[int, int, int, int]]:
    """量等级标要**填平**的那块矩形（2026-10-11，mock 线「把等级标一起替换成用户名」）。

    与 :func:`_tail_real_rows` 同一套三道闸，理由也一样：

    1. **放开窗口**：等级标比内缩框高 —— 实测 `于亚洲3-8/5.jpg` 是 1195~1229，
       而内缩框只有 1196~1227 ⇒ 不放开就会**留下上下两条药丸边**；
    2. ``shave_frame`` 削掉窗口里的绿色描边；
    3. **按覆盖率砍行**：整行铺满非背景像素的是描边的抗锯齿边，不是这一行的内容。

    ⚠️ 这里量的是**整幅图**上的行范围（不是在 ``patch`` 里量）：药丸探出内缩框的那
    1~2 行在 ``patch`` 里根本不存在，蒙版也就永远够不着它们。谁用这个矩形：
    ``EraseRegion.badge_rect`` → ``replace_username.process_image`` 把它交给
    :func:`inpaint_local.fill_local` **整块铺平**（不是按蒙版修补）。

    Args:
        img_rgb:    ``(H, W, 3)`` 原图。
        box_rect:   内缩+削过描边的标注框。
        badge_span: 等级标墨迹的列区间（绝对列号，含端点）。
        bg:         框内背景色。
        pad:        列方向额外放开的像素（取蒙版膨胀半径，把蒙版膨胀那一圈也盖住）。
        vext:       上下放开多少行，与尾巴同源。
        shadow:     再往两侧各多吃多少列（连药丸的投影一起填平），见
                    :data:`config.BADGE_SHADOW_PX`。
        head:       **胶囊头**的列号（:func:`_capsule_head`），``None`` = 没量到。
                    给值时**往左**的扩展停在它右边一格 —— 左边那片是胶囊本身，
                    吃掉它就是把胶囊头刷平（用户 2026-10-11「得保留背景的胶囊头」）。
                    往右那一侧与「没量到头」的情形一律照旧。
        row_limit:  允许量到的**绝对行区间**（闭区间），``None`` = 不夹。多行框里由
                    :func:`find_erase_region` 传「本条行带 + 空隙中线」——药丸是按列在
                    整幅图上量的，不夹就会把上下两条评论的药丸串成一块铺平（bug-xxx）。

    Returns:
        ``(x0, y0, x1, y1)`` **半开**矩形；量不到任何墨迹时返回 ``None``。
    """
    ix0, iy0, ix1, iy1 = box_rect
    bx0 = max(ix0, badge_span[0] - pad)
    if head is not None:
        bx0 = max(bx0, int(head) + 1)
    bx1 = min(ix1, badge_span[1] + 1 + pad)
    if bx0 >= bx1:
        return None
    wy0 = max(0, iy0 - vext)
    wy1 = min(img_rgb.shape[0], iy1 + vext)
    if row_limit is not None:
        wy0, wy1 = max(wy0, row_limit[0]), min(wy1, row_limit[1] + 1)
        if wy1 <= wy0:
            return None
    cx0, cy0, cx1, cy1 = shave_frame(
        img_rgb, (ix0, wy0, ix1, wy1),
        ratio=config.FRAME_EDGE_RATIO,
    )
    if cx1 <= cx0 or cy1 <= cy0:
        return None
    win = img_rgb[cy0:cy1, cx0:cx1]
    ink = compute_ink_mask(win, np.asarray(bg, dtype=np.uint8), config.BG_DIST_THRESH)
    ink[ink.mean(axis=1) >= config.FRAME_ROW_COVER, :] = False
    cols = np.zeros(win.shape[1], dtype=bool)
    cols[max(0, bx0 - cx0) : max(0, bx1 - cx0)] = True
    hit = ink & cols[None, :]
    rows_hit = np.nonzero(hit.any(axis=1))[0]
    if len(rows_hit) == 0:
        return None
    k0, k1 = int(rows_hit.min()), int(rows_hit.max())
    ry0 = max(k0 + cy0, iy0 - vext)
    ry1 = min(k1 + cy0, iy1 - 1 + vext)
    if row_limit is not None:
        ry0, ry1 = max(ry0, row_limit[0]), min(ry1, row_limit[1])
        if ry1 < ry0:
            return None

    # ---- 行范围定了，再往两侧吃投影 ----
    # 逐列看**这几行**里有没有真墨迹：没有就继续吃。于是碰到等级标左边的图标、
    # 或右边的名字，立刻停 —— 宁可少填，也绝不把别人的东西盖掉。
    #
    # ⚠️ 这几行只在**内缩框的行范围**里取：绿描边的上下两条横杠整行都是墨迹，
    # 而且就压在内缩框外沿（这正是内缩的意义）；窗口被 ``vext`` 上下放开之后它们
    # 全进来了，不排掉的话**每一列**都「有墨迹」，投影一格都吃不动。
    b0 = max(k0, iy0 - cy0)
    b1 = min(k1, iy1 - 1 - cy0)
    if b1 < b0:
        b0, b1 = k0, k1
    band_ink = ink[b0 : b1 + 1].any(axis=0)
    if head is not None:
        # ⚠️ 量到胶囊头 ⇒ **往左一格都不吃**（`ex0 = bx0` ⇒ 下面那个 while 不跑）。
        #    药丸左边那几列是**胶囊自己的暗色**（实测 79 张：与药丸右边同色，中位差 +0.8 级），
        #    不是药丸的投影 —— 吃它只有一个后果：把胶囊的圆角刷平（用户 2026-10-11
        #    「得保留背景的胶囊头」）。于是这条路的承诺变成
        #    **「药丸左沿 − 蒙版膨胀那一圈」以西一个像素都不动**。
        ex0 = bx0
    else:
        ex0 = max(cx0, bx0 - shadow)
    ex1 = min(cx1, bx1 + shadow)
    while bx0 > ex0 and not band_ink[bx0 - 1 - cx0]:
        bx0 -= 1
    while bx1 < ex1 and not band_ink[bx1 - cx0]:
        bx1 += 1
    return (bx0, ry0, bx1, ry1 + 1)


def estimate_background(patch: np.ndarray, quantize: int = 0) -> np.ndarray:
    """估计一块区域的背景色 —— 取**出现次数最多**的颜色（mode）。

    直播 UI 的底色是大片纯色，mode 比均值稳健得多：均值会被文字和徽章拽偏，
    而 mode 只要背景占比最大就不动。

    Args:
        patch: ``(H, W, 3)`` 的 RGB 数组。
        quantize: 量化步长（0 = 精确众数，现状）。>0 时先把每个通道按
            ``floor(c / quantize)`` 分桶，取像素最多的桶，返回**桶内均值**。
            为什么需要它：视频压缩噪声会把同一块底色打散到许多相邻色值上，精确
            众数可能被**文字**抢走（快手 App 实测：白字 100 个同色 vs 暗底每种
            ~90 个 ⇒ 背景估成白色、墨迹占比 0.90）。默认 0 = 逐字节等于旧行为。

    Returns:
        长度 3 的 ``uint8`` 背景色。
    """
    flat = patch.reshape(-1, 3).astype(np.int32)
    if quantize and quantize > 1:
        buckets = flat // int(quantize)
        packed_b = (buckets[:, 0] << 16) | (buckets[:, 1] << 8) | buckets[:, 2]
        values, counts = np.unique(packed_b, return_counts=True)
        winner = int(values[int(np.argmax(counts))])
        return flat[packed_b == winner].mean(axis=0).round().astype(np.uint8)
    packed = (flat[:, 0] << 16) | (flat[:, 1] << 8) | flat[:, 2]
    values, counts = np.unique(packed, return_counts=True)
    best = int(values[int(np.argmax(counts))])
    return np.array([(best >> 16) & 0xFF, (best >> 8) & 0xFF, best & 0xFF], dtype=np.uint8)


def compute_ink_mask(patch: np.ndarray, bg: np.ndarray, thresh: int) -> np.ndarray:
    """挑出与背景差得够远的像素。

    Args:
        patch:  ``(H, W, 3)`` RGB。
        bg:     背景色，长度 3。
        thresh: 最大通道差的阈值。

    Returns:
        ``(H, W)`` 的 bool 数组，True = 墨迹。
    """
    diff = np.abs(patch.astype(np.int32) - bg.astype(np.int32)).max(axis=2)
    return diff > thresh


def column_segments(columns_hit: np.ndarray) -> list[tuple[int, int]]:
    """把「哪些列有墨迹」的布尔数组切成连续段。

    Args:
        columns_hit: ``(W,)`` bool，True = 该列有墨迹。

    Returns:
        ``(start, end)`` 列表，闭区间，按列升序。
    """
    segments: list[tuple[int, int]] = []
    start: Optional[int] = None
    for i, hit in enumerate(columns_hit):
        if hit and start is None:
            start = i
        elif not hit and start is not None:
            segments.append((start, i - 1))
            start = None
    if start is not None:
        segments.append((start, len(columns_hit) - 1))
    return segments


def row_bands(
    row_counts: np.ndarray, gap_px: int, min_rows: int = 1,
) -> list[tuple[int, int, int]]:
    """把「哪些行有墨迹」聚成**行带**（上下的那一维，与 :func:`column_segments` 对称）。

    相邻两条命中行的缝 ≤ ``gap_px`` 就算同一条带 —— 字形**内部**的行间是有缝的
    （「一」与它下面的笔画之间、笔画与抗锯齿之间），不并起来会被切碎。

    Args:
        row_counts: ``(H,)`` int，每行的墨迹像素数（不是 bool —— 带要按**墨量**排序）。
        gap_px: 允许的行间缝。
        min_rows: 只保留行数 ≥ 它的带（挡碎屑，见 :data:`config.ROW_BAND_MIN_ROWS`）。

    Returns:
        ``[(top, bottom, mass), ...]``，闭区间、按 top 升序；``mass`` = 带内墨迹像素合计。
    """
    hit = np.nonzero(row_counts)[0]
    bands: list[tuple[int, int, int]] = []
    if len(hit):
        start = prev = int(hit[0])
        for raw in hit[1:]:
            r = int(raw)
            if r - prev - 1 <= gap_px:
                prev = r
                continue
            bands.append((start, prev, int(row_counts[start : prev + 1].sum())))
            start = prev = r
        bands.append((start, prev, int(row_counts[start : prev + 1].sum())))
    return [b for b in bands if b[1] - b[0] + 1 >= min_rows]


def stroke_peak(ink: np.ndarray, span: tuple[int, int]) -> float:
    """一个列段内墨迹的**笔画粗细**：``cv2.distanceTransform`` 的峰值（px）。

    文字是细笔画，徽章 / emoji 这类**实心图形**是粗块 —— 这是把「图形」与「文字」分开的
    第二条判据（第一条是 :func:`_segment_stats` 的填充率，只认「实心药丸」）。
    实测（``宝宝不乖-3/1.jpg`` 的空心等级徽章 vs 名字）：徽章 **16.0**，
    名字段在 8 张 ``BOX_TABLE`` 上一律 **3.0~3.2**，冒号 2.0、正文 3.0。

    Args:
        ink:  ``(H, W)`` bool 墨迹。
        span: ``(start, end)`` 闭区间列范围。

    Returns:
        峰值距离（px）；段内无墨迹时 ``0.0``。
    """
    cols = np.zeros_like(ink)
    cols[:, span[0] : span[1] + 1] = ink[:, span[0] : span[1] + 1]
    if not cols.any():
        return 0.0
    return float(cv2.distanceTransform(cols.astype(np.uint8), cv2.DIST_L2, 5).max())


def _segment_stats(
    ink: np.ndarray,
    patch: np.ndarray,
    span: tuple[int, int],
) -> tuple[float, float]:
    """算一个列段的**填充率**与**平均亮度**。

    Args:
        ink:   ``(H, W)`` bool 墨迹。
        patch: ``(H, W, 3)`` RGB。
        span:  ``(start, end)`` 闭区间列范围。

    Returns:
        ``(填充率, 平均亮度)``；段内无墨迹时返回 ``(0.0, 0.0)``。
    """
    a, b = span
    sub = ink[:, a : b + 1]
    area = sub.shape[0] * sub.shape[1]
    n = int(sub.sum())
    if n == 0 or area == 0:
        return 0.0, 0.0
    bright = patch[:, a : b + 1].max(axis=2)[sub]
    return n / area, float(bright.mean())


def _core_color_of(
    patch: np.ndarray,
    ink: np.ndarray,
    cols: np.ndarray,
    rows: Optional[np.ndarray] = None,
) -> tuple[int, int, int]:
    """按**给定的列掩码**取主体色（与 :func:`_core_color` 同一把尺子）。

    为什么需要它：``colon_anchor`` 平台（微博/视频号）的药丸夹在**名字与冒号之间**，
    而「名字+药丸+冒号」这一整段的取色会被**药丸**带跑 —— 实测微博 ``yoyo_light-11/5.jpg``：
    原名字中性灰 ``RGB(188,188,188)``、药丸淡金 ``RGB(241,222,185)``，
    整段取色却得到 ``RGB(211,189,153)``（R+23 / B−35，肉眼看就是**发黄**）。
    所以那种版式必须按**名字自己的列**取色，把中间的药丸排除在外。

    Args:
        patch: ``(H, W, 3)`` RGB。
        ink:   ``(H, W)`` bool 墨迹。
        cols:  ``(W,)`` bool，参与取色的列。
        rows:  ``(H,)`` bool，参与取色的行；None = 不限。

    Returns:
        ``(R, G, B)``；掩码内无墨迹时返回黑色。
    """
    mask = ink & cols[None, :]
    if rows is not None:
        mask = mask & rows[:, None]
    pixels = patch[mask]
    if len(pixels) == 0:
        return (0, 0, 0)
    bright = pixels.max(axis=1).astype(np.int32)
    cutoff = np.percentile(bright, 75)
    core = pixels[bright >= cutoff]
    if len(core) == 0:
        core = pixels
    return tuple(int(v) for v in core.mean(axis=0).round())


def _core_color(patch: np.ndarray, ink: np.ndarray, span: tuple[int, int]) -> tuple[int, int, int]:
    """取一段文字**主体**的颜色。

    直接对墨迹取均值会被**抗锯齿边缘**拽暗（那些像素是文字色与背景色的混合）。
    所以先按亮度取前 25%，再求均值 —— 抗锯齿像素天然更暗，会被这一步筛掉。

    Args:
        patch: ``(H, W, 3)`` RGB。
        ink:   ``(H, W)`` bool 墨迹。
        span:  ``(start, end)`` 闭区间列范围。

    Returns:
        ``(R, G, B)`` 三元组；段内无墨迹时返回黑色。
    """
    cols = np.zeros(ink.shape[1], dtype=bool)
    cols[span[0] : span[1] + 1] = True
    return _core_color_of(patch, ink, cols)


def _is_emoji_like(span: tuple[int, int], sat: int, sat_min: int) -> bool:
    """这一段「像不像一枚 emoji」—— 只做**宽度**两道闸,不做颜色识别。

    宽度是唯一能把 emoji 与「真冒号」「正文段」同时分开的信号（实测）:

    * 真冒号 ``4~5px`` ⇒ 宽于它就不可能是冒号
    * emoji / 汉字 ``26~28px`` ⇒ 宽度**分不开**这两者,只能靠饱和度区分
    * 正文段 ``168~308px`` ⇒ 必须挡住,否则会把正文桥接进用户名（bug-141 翻版）

    所以这里的两道闸是「宽于冒号上限」**且**「不宽于单字上限」,饱和度只用来
    判「它够不够格当用户名」（不够格才需要桥接,够格的话规则 4 早就收下它了）。

    Args:
        span:    段的 ``(start, end)`` 闭区间列范围。
        sat:     该段的核心色饱和度。
        sat_min: 用户名饱和度阈值 ``USERNAME_SAT_MIN``。

    Returns:
        True 表示「宽度上像单枚 emoji（不是冒号、也不是段落），且饱和度不足」。
    """
    width = span[1] - span[0] + 1
    return (
        width > config.COLON_MAX_WIDTH
        and width <= config.USERNAME_BRIDGE_MAX_W
        and sat < sat_min
    )


def _bridge_reaches_text(
    segs: list[tuple[int, int]],
    sats: list[int],
    i: int,
    sat_min: int,
) -> bool:
    """桥接**有去有回**：从段 ``i`` 起跳过连续的「emoji 样」段，能否接上一个饱和段？

    这是前一版桥接的**必要补丁**。只按宽度判「像 emoji」会误伤：实测
    ``Czy-12/7.jpg`` 在昵称后有一段宽 14、饱和 5 的碎屑（真身是正文「36米」的
    「3」），它满足「宽于冒号上限 8 且窄于 40」，于是被桥接进用户名 ⇒ 擦除区
    右移、把正文的「3」擦掉、字号被撑大（**回归**）。

    加上这条前瞻之后就分开了：

    * ``A携酒话清风D疗-5/2.jpg``：桥过 🍼 之后接上「携酒话清风」(sat 76) ⇒ **成立**
    * ``Czy-12/7.jpg``：桥过碎屑之后接上的是正文段 (sat 4) ⇒ **不成立**，不桥接

    直觉：**emoji 从不单独出现在昵称之后，它总是嵌在文字中间** —— 所以「桥过去
    只能看到正文」就证明桥错了。

    Args:
        segs:    全部列段。
        sats:    各段核心色饱和度。
        i:       待桥接段的起始下标。
        sat_min: 用户名饱和度阈值。

    Returns:
        True 表示「跳过这一段之后能接上用户名文字」，值得桥接。
    """
    j = i
    while j < len(segs) and _is_emoji_like(segs[j], sats[j], sat_min):
        j += 1
    return j > i and j < len(segs) and sats[j] >= sat_min


def _emoji_in_name(
    ink: np.ndarray,
    segs: list[tuple[int, int]],
    sats: list[int],
    name_idx: list[int],
    *,
    sat_min: int = config.NAME_EMOJI_SAT_MIN,
    sat_max: int = config.NAME_EMOJI_SAT_MAX,
    min_ink: int = config.NAME_EMOJI_MIN_INK,
    min_width: int = config.NAME_EMOJI_MIN_WIDTH,
    max_height: int = config.NAME_EMOJI_MAX_SEG_H,
) -> list[tuple[int, int, int, int]]:
    """从**已经定位到的用户名段**里挑出「不像名字文字」的那几段。

    名字文字是**单一颜色**、**笔画**；emoji 是**多色或浅色**的**实心团块**。所以判据是
    「段的核心色彩度落在名字文字那簇之外」—— 高于 :data:`config.NAME_EMOJI_SAT_MAX`
    （🌈😊✨ 这类高彩度的），或低于 :data:`config.NAME_EMOJI_SAT_MIN`（🍼 这类浅色的，
    也正是桥接那条规则收进来的）。

    ⚠️ **彩度是 ``max(core) - min(core)``**（与 :data:`config.USERNAME_SAT_MIN` 同尺），
    **不是** HSV 的 S。两把尺子的刻度差得很远（同一枚 🌈：这里 190、HSV 那里 204；
    `文字`：这里 82、那里 88），定阈值不能混用。

    另外三道闸是**挡噪声**的，都实测出来的：

    * ``min_ink`` / ``min_width``：笔画碎片（一个汉字被切断的一小截）的颜色是背景与
      笔画的混色，彩度天然偏低，会撞下闸；
    * ``max_height``：emoji 占**一行字**。框里含两行评论的图上会切出高 51~103 的段，
      它们的彩度也在名字带之外 —— 不挡的话，「框检测跨行」会伪装成「昵称含 emoji」。

    实测（650 张语料、542 张定位成功、1191 个名字段）：过前三道闸的 1060 段里，
    彩度 ``60~82`` 有 1018 段（名字文字），``101`` 以上 21 段（emoji），
    ``90~100`` 只有 3 段且全是跨行的段。

    Args:
        ink:      ``(H, W)`` bool 墨迹（**框内局部坐标**）。
        segs:     全部列段（局部坐标）。
        sats:     各段的彩度 ``max(core) - min(core)``。
        sat_min:  「不够彩 ⇒ 不是名字文字」的下闸。
        sat_max:  「太彩 ⇒ 不是名字文字」的上闸。
        min_ink:  候选段的最小墨迹像素数。
        min_width:候选段的最小宽度。
        max_height:候选段的最大高度（一行字的上限）。

    Returns:
        ``[(宽, 墨量, 段高, 彩度), ...]``，按列序；空列表 = 名字是纯文字。
    """
    return [
        (segs[i][1] - segs[i][0] + 1, n, h, sats[i])
        for i, n, h in _emoji_seg_idx(
            ink, segs, sats, name_idx, sat_min=sat_min, sat_max=sat_max,
            min_ink=min_ink, min_width=min_width, max_height=max_height,
        )
    ]


def _emoji_seg_idx(
    ink: np.ndarray,
    segs: list[tuple[int, int]],
    sats: list[int],
    name_idx: list[int],
    *,
    sat_min: int = config.NAME_EMOJI_SAT_MIN,
    sat_max: int = config.NAME_EMOJI_SAT_MAX,
    min_ink: int = config.NAME_EMOJI_MIN_INK,
    min_width: int = config.NAME_EMOJI_MIN_WIDTH,
    max_height: int = config.NAME_EMOJI_MAX_SEG_H,
) -> list[tuple[int, int, int]]:
    """同 :func:`_emoji_in_name`，但回的是**段的下标**与它的墨量/段高。

    取主体色（:func:`_core_color_of`）要用它把 emoji 那几段**排除在取色窗口之外**：
    ``神秘人`` 的 🎇 就在名字段里（列 662~690、核心色 ``(246,235,37)``），
    参与取色就把青色名字染成 ``(175,223,196)`` —— 用户 2026-10-11 报的
    「东极勇哥-3/扩展2.jpg 用户名颜色有问题」正是这一条（见 `expand/扩展张数.md` §25）。

    Returns:
        ``[(段下标, 墨量, 段高), ...]``，按列序。
    """
    found: list[tuple[int, int, int]] = []
    for i in name_idx:
        a, b = segs[i]
        width = b - a + 1
        if width < min_width:
            continue
        sub = ink[:, a : b + 1]
        n_ink = int(sub.sum())
        if n_ink < min_ink:
            continue
        rows_hit = np.nonzero(sub.any(axis=1))[0]
        height = int(rows_hit.max() - rows_hit.min() + 1) if len(rows_hit) else 0
        if height > max_height:
            continue
        if sats[i] < sat_min or sats[i] >= sat_max:
            found.append((i, n_ink, height))
    return found


def _bridge_gap(
    ink: np.ndarray,
    segs: list[tuple[int, int]],
    left_idx: int,
    right_idx: int,
) -> float:
    """量「两个段之间的缝」被内容填满的比例 —— **仅诊断用,不参与决策**。

    ⚠️ 曾打算用它当桥接判据（bug-137 的候选修法：「缝里有内容」，替代放宽
    ``INK_CLUSTER_GAP_PX``）。**实测否决**：``A携酒话清风D疗-5/2.jpg`` 上它返回
    ``0.0`` —— 因为 ``compute_ink_mask`` 本就是「任何非背景像素」,emoji 的浅色
    边缘**已经**被算进它自己的列段,缝里确实是空的。
    **缝是空的 ≠ 那一段不是 emoji。** 保留此函数是为了排查时能打印它。

    Args:
        ink:       ``(H, W)`` bool 墨迹,已是**任何非背景像素**(含多色 emoji)。
        segs:      全部列段。
        left_idx:  左侧段的下标。
        right_idx: 右侧段的下标。

    Returns:
        缝的列覆盖率 ``0.0 ~ 1.0``;两段紧邻或无缝时返回 ``1.0``。
    """
    gap_start = segs[left_idx][1] + 1
    gap_end = segs[right_idx][0]  # 开区间
    if gap_start >= gap_end:
        return 1.0
    gap = ink[:, gap_start:gap_end]
    if gap.size == 0:
        return 1.0
    filled_cols = int(gap.any(axis=0).sum())
    return filled_cols / (gap_end - gap_start)


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


def _locate_by_saturation(
    ink: np.ndarray,
    segs: list[tuple[int, int]],
    stats: list[tuple[float, float]],
    sats: list[int],
    box: tuple[int, int, int, int],
    diag: list[tuple[int, int, int, int]],
    *,
    badge_min_width: int,
    badge_fill_ratio: float,
    badge_stroke_max: float,
    username_sat_min: int,
) -> tuple[int, list[int], int, bool]:
    """规则 3/4/5 —— **按核心色饱和度认名字**（抖音路径，原文照搬，一行未改）。

    只在 ``profile.name_strategy == "saturation_run"``（含 ``profile=None`` 的旧调用）时走。

    Returns:
        ``(first, name_idx, colon_idx, badge_found)``。
    """
    # ---- 规则 3：最左段若是**实心药丸**或**粗笔画图形** ⇒ 徽章，排除 ----
    #   两条判据，缺一不可：填充率认「实心药丸」（徽章 0.74 vs 文字 ≤0.49），
    #   笔画粗细认「空心图标 + 数字」那种徽章（实测 `宝宝不乖-3/1.jpg` 填充率只有 0.48，
    #   正落在汉字的 0.37~0.49 里 ⇒ 填充率分不开；但它的笔画峰值是 16.0，文字只有 3.0~3.2）。
    #   徽章被漏掉的后果不是「多擦一点」：它会被当成名字的第一个段 ⇒ 槽位把徽章圈进去
    #   （194px，真名字只有 112px）、字高取到徽章的 32px（真名字 28px）、取色从蓝紫色的
    #   徽章上取 ⇒ 画出来「名字又大又白」。
    #   ⚠️ 两条都要**同时**满足 `badge_min_width`：emoji 也是图形，但只有 26~28px 宽，
    #   它是**名字的一部分**，不能当徽章剔掉。
    first = 0
    badge_found = False
    if len(segs) >= 3:
        a, b = segs[0]
        fill, _ = stats[0]
        wide = (b - a + 1) >= badge_min_width
        stroke = stroke_peak(ink, segs[0]) if wide else 0.0
        # ⚠️ 粗笔画那条**只对「上下连成一条带」的段生效**：框跨两行时，徽章会和两行文字
        #    在**列**上连成一整段（实测 `魂殿抖音分殿殿主-5/2.jpg` 宽 236、行 3~99），
        #    笔画峰值一样很高 ⇒ 不加这道闸会把整段（**含名字**）当徽章剔掉、定位直接失败
        #    （风过无恨-10/3、魂殿抖音分殿殿主-5/2 与 4 三张当场回归）。
        one_band = False
        if wide and stroke >= badge_stroke_max:
            seg_ink = np.zeros_like(ink)
            seg_ink[:, a : b + 1] = ink[:, a : b + 1]
            one_band = len(row_bands(seg_ink.sum(axis=1).astype(int), config.GLYPH_ROW_GAP_PX)) == 1
        if wide and (fill >= badge_fill_ratio or one_band):
            badge_found = True
            first = 1
            LOGGER.debug(
                "排除徽章段 [%d-%d] 宽 %d 填充率 %.2f 笔画峰值 %.1f（%s）",
                a, b, b - a + 1, fill, stroke,
                "实心药丸" if fill >= badge_fill_ratio else "粗笔画图形",
            )

    # ---- 规则 3 的兜底：徽章判据在**两行框**上会失效，用「宽度 + 笔画粗细」再认一次 ----
    #   两条失效原因（实测 `一麻袋开心(沧海一舟)-6/1.jpg`，bug-177）：
    #   ① 填充率的分母是**框高**，两行框的框高翻倍、徽章只占其中一行 ⇒ 0.74 → **0.58**，跌破 0.60；
    #   ② 「粗笔画图形」那条闸要求段内**只连成一条行带**，而两行框里同一列上下各一枚徽章
    #      就是**两条带** ⇒ 也不成立。
    #   兜底只用两个**与框高无关**的量：宽度（文字 26~32px / 牌 66~80px）与
    #   笔画粗细（文字峰值 3~4 / 实心牌 16~26，差一个量级）。
    #   ⚠️ 宽度**上限**（`BADGE_MAX_WIDTH=100`）是关键：它挡住「徽章与文字在**列**上连成一整段」
    #      那种病理段（实测 112~236px）—— 那种段里含名字，剔掉就等于把名字也丢了（bug-168 的另一面）。
    if first < len(segs):
        a, b = segs[first]
        wide = badge_min_width <= (b - a + 1) <= config.BADGE_MAX_WIDTH
        stroke = stroke_peak(ink, segs[first]) if wide else 0.0
        if wide and stroke >= badge_stroke_max:
            LOGGER.debug(
                "规则 3 兜底：首段 [%d-%d] 宽 %d 笔画峰值 %.1f ⇒ 判为徽章（两行框里原判据失效）",
                a, b, b - a + 1, stroke,
            )
            badge_found = True
            first += 1

    # ---- 规则 4：从徽章之后开始，**连续**若干段「核心色够饱和」的就是用户名 ----
    # 用颜色而不是位置来切，是因为正文里字与字之间的空隙会把正文切成多段
    # （实测 7.jpg 的正文是 [198-253]/[255-280]/[310-421] 三段），
    # 「取最右段当正文」会把正文前半截吞进擦除区。
    #
    # ⚠️ 但**表情会让这条链断开**：emoji 是多色/低饱和的，它的段饱和度够不到阈值。
    #    实测 A携酒话清风D疗-5/2.jpg：`🍼携酒话清风` 切出来是
    #      seg0「A」sat=70 → seg1「🍼」sat=36 → seg2「携酒话清风」sat=76
    #    循环在 seg1 停下 ⇒ 名字被从中间切断，seg1 又被规则 5 当成「冒号」，
    #    槽位缩到 20px、真冒号留在原地 —— **静默出错**，比报错更坏。
    #    对策见下面的桥接循环：宽度闸（:func:`_is_emoji_like`）+ 前瞻
    #    （:func:`_bridge_reaches_text`）。⚠️ 别用「缝里有没有内容」判（:func:`_bridge_gap`
    #    实测失效，bug-165）。
    name_idx: list[int] = []
    i = first
    while i < len(segs) and sats[i] >= username_sat_min:
        name_idx.append(i)
        i += 1

    while (
        name_idx
        and i < len(segs)
        and _is_emoji_like(segs[i], sats[i], username_sat_min)
        and _bridge_reaches_text(segs, sats, i, username_sat_min)
    ):
        LOGGER.debug(
            "桥接疑似 emoji 段 [%d-%d]（宽 %d 饱和 %d）并入用户名",
            segs[i][0], segs[i][1], segs[i][1] - segs[i][0] + 1, sats[i],
        )
        name_idx.append(i)
        i += 1
        # 桥接后可能又能接上若干高饱和段（emoji 后面紧跟的就是剩下的汉字）
        while i < len(segs) and sats[i] >= username_sat_min:
            name_idx.append(i)
            i += 1

    if not name_idx:
        raise RegionNotFoundError(
            f"框 {box} 内找不到用户名的连续段（饱和度阈值 {username_sat_min}）"
            f"；各段饱和度={sats}，列段={diag}"
        )

    # ---- 规则 5：紧跟用户名之后的**那一段**就是冒号（实测宽 4~5px、饱和度 31~42） ----
    colon_idx = i
    if colon_idx >= len(segs):
        raise RegionNotFoundError(
            f"框 {box} 内用户名之后没有冒号段（用户名段={name_idx}，列段={diag}）"
        )
    return first, name_idx, colon_idx, badge_found


def _locate_by_colon(
    ink: np.ndarray,
    segs: list[tuple[int, int]],
    stats: list[tuple[float, float]],
    profile: "platforms.PlatformProfile",
    box: tuple[int, int, int, int],
    diag: list[tuple[int, int, int, int]],
) -> tuple[int, list[int], int, bool]:
    """按**冒号**定位（跨平台策略）。

    **为什么是冒号**：三份语料的实测名字段彩度是 74 / 2 / 0（抖音 / 视频号 / 微博）——
    「按饱和度认名字」在后两个平台上 0 命中；而冒号在三份语料里都是同一个签名
    （宽 4~6px、填充率 15~37%、**高度约占行高的六成**）。判据在
    :func:`platforms.find_colon_candidates`，这里只负责把它的结果接到 ``EraseRegion``
    需要的那四个量上。候选**不止一个**、谁是真的是由 :func:`platforms.pick_colon` 定的：
    默认取分数最高的那个；开了缝闸的平台先挑「缝」够大的那个，因为分数会偏向
    **名字内部的窄碎段** —— 它右侧还跟着半个名字，``body`` 比真冒号那边的还长
    （App 端开的是**前缝** 5，bug-358；视频号网页版开的是**后缝** 5，bug-367）。

    名字 = **冒号左边、与之相邻的那一串段**（撞上实心宽块＝徽章就停）。

    ⚠️ 只有**非**「背景不够纯」的失败会走到这里报错：本策略依赖列段的切分，
    而背景不是近纯色时整框会连成一段（视频号实测那 2 张）。那种情况应当在
    **定位之前**就由背景检查拦掉，别指望这里看出来。

    Returns:
        ``(first, name_idx, colon_idx, badge_found)``。

    Raises:
        RegionNotFoundError: 没有像冒号的段，或冒号左边拼不出名字。
    """
    if len(segs) < 3:
        raise RegionNotFoundError(
            f"框 {box} 内只切出 {len(segs)} 段，凑不出「名字 + 冒号 + 正文」"
            f"（冒号锚点需要至少 3 段）；列段={diag}"
        )

    fills = [f for f, _ in stats]
    rows: list[tuple[int, int]] = []
    for s, e in segs:
        col = ink[:, s : e + 1].any(axis=1)
        idx = np.nonzero(col)[0]
        rows.append((int(idx.min()), int(idx.max())) if len(idx) else (0, 0))

    # 行带：墨量最大的一条（与下面的字带取法一致）
    bands = row_bands(ink.sum(axis=1).astype(int), config.GLYPH_ROW_GAP_PX)
    band = tuple(max(bands, key=lambda b: b[2])[:2]) if bands else (0, ink.shape[0] - 1)

    cands = platforms.find_colon_candidates(segs, fills, rows, band, profile)
    if not cands:
        raise RegionNotFoundError(
            f"框 {box} 内没有「像冒号」的列段（宽 {profile.colon_w_range}、"
            f"填充率 ≤{profile.colon_fill_max}、高 {profile.colon_h_ratio}×行带、"
            f"上缘 ≥{profile.colon_lower_ratio}）；列段={diag}"
        )
    colon = platforms.pick_colon(cands, segs, profile)
    assert colon is not None  # cands 非空（上面刚判过）
    try:
        first, last = platforms.name_run_left_of(segs, fills, colon.index, profile)
    except ValueError as exc:
        raise RegionNotFoundError(f"框 {box}：{exc}；列段={diag}") from exc

    name_idx = list(range(first, last + 1))
    LOGGER.debug(
        "冒号锚点：%s（共 %d 个候选，取前缝 %dpx 的那个）；名字段 %s = 列 [%d-%d]",
        colon.as_note(), len(cands), platforms.preceding_gap(segs, colon),
        name_idx, segs[first][0], segs[last][1],
    )
    # ⚠️ **别写 `first > 0`**（bug-325，2026-10-11 用户「胶囊头没有了」）：那只说明
    #    「名字左边还有段」，而那段多半是**框左沿糊进来的 1px 描边残渣**（走描时缝太大
    #    走不过去就停下了）。20:51 那批 1466 张里 **663 张（45%）** 是这种，宽度中位 1px。
    #    把它当药丸的后果有两处：新名字整条**从残渣那列起写**（整段左移 ~50px）、
    #    以及把框左沿一块 16px 宽的地填平（那张图丢的就是药丸左边的胶囊头）。
    #    判据见 :func:`platforms.badge_left_of`：**像药丸** 且 **在名字这一串里**。
    badge = platforms.badge_left_of(segs, fills, first, profile)
    if badge is None and first > 0:
        LOGGER.debug(
            "名字左边那段 [%d-%d]（框内局部列）宽 %d 不算等级标"
            "（缝 %d，填充率 %.2f）⇒ 按「没有等级标」处理",
            segs[first - 1][0], segs[first - 1][1],
            segs[first - 1][1] - segs[first - 1][0] + 1,
            segs[first][0] - segs[first - 1][1] - 1, fills[first - 1],
        )
    return first, name_idx, colon.index, badge is not None


def _resplit_on_band(
    ink: np.ndarray,
    row_keep: np.ndarray,
    slot: tuple[int, int],
    band: tuple[int, int],
    *,
    colon_max_w: int = 8,
    colon_max_h_ratio: float = 0.8,
    colon_min_h_ratio: float = 0.4,
) -> Optional[tuple[tuple[int, int], tuple[int, int]]]:
    """在**名字这一行上重切列段**，把「被并进来的冒号」分出来。

    返回 ``(name_span, colon_span)``；切不出来（或冒号本来就没被并进来）返回 ``None``。

    **为什么需要它**：整框列投影用的是 ``ink.any(axis=0)`` —— **任意一行**在这列有墨就算命中。
    两行框里，第二行的文字会把名字与冒号**在列上粘成一段**。实测 ``军陌-4/3``：
    段 656-737 = 名字 656-711 + **冒号 717-721** + 第二行的墨 712-737。
    后果：① 冒号落在槽位里 ⇒ **被擦掉**（用户报「冒号没有了」）；② 槽位宽度虚高 ⇒ 补满槽位
    会把新名字撑到冒号原来的位子上。

    只在字带那一行重切，冒号自然分开。冒号的判据是「紧邻名字的下一段**又窄又矮**」
    （实测真冒号宽 5px、高 ~17px；汉字 26~28px 宽、占满整行）。

    ⚠️ **别用「只看下半行有没有墨」判冒号** —— 那是错的口径：本图冒号的两个点在行 897-900 与
    909-913，**上点在中线之上**（数据/ 那 8 张语料的冒号恰好在下方，所以当年记成「只有下半截」）。

    Args:
        ink:      ``(H, W)`` bool 墨迹（框内局部坐标）。
        row_keep: ``(H,)`` bool，名字所在那一行。
        slot:     当前的名字槽位列区间（含端点，局部坐标）。
        band:     字带 ``(top, bottom)``（含端点，局部坐标）。
        colon_max_w:     冒号的最大宽度。
        colon_max_h_ratio: 冒号高度 / 字带高 的上限。

    Returns:
        ``((name_l, name_r), (colon_l, colon_r))``，局部坐标、含端点。
    """
    band_ink = ink & row_keep[:, None]
    cols_hit = band_ink.any(axis=0)
    segs = column_segments(cols_hit)
    if len(segs) < 3:
        return None
    # 名字从**含槽位左缘的那一段**起算
    start = next((i for i, (a, b) in enumerate(segs) if a <= slot[0] <= b), None)
    if start is None:
        return None
    name_l = segs[start][0]
    band_h = band[1] - band[0] + 1
    # 往右走：撞到第一个「**又窄又矮**」的段就是冒号。
    #   · 窄（≤ ``colon_max_w``）：真冒号宽 4~6px；汉字 26~28px、半角数字 ~10px 都被挡掉。
    #   · **不高也不矮**（0.4~0.8 × 字带高）：真冒号的两个点合起来约占 0.65 行高；
    #     句点/逗号只有 ~0.2（太矮）⇒ 挡掉；字母 l / 汉字占满整行（太高）⇒ 也挡掉。
    for i in range(start + 1, len(segs)):
        a, b = segs[i]
        w = b - a + 1
        if w > colon_max_w:
            continue
        rh = np.nonzero(band_ink[:, a : b + 1].any(axis=1))[0]
        if len(rh) == 0:
            continue
        h = int(rh.max() - rh.min() + 1)
        if colon_min_h_ratio * band_h <= h <= colon_max_h_ratio * band_h:
            if i + 1 >= len(segs):   # 右边得有正文，否则只是名字末尾的一个小段
                return None
            # ⚠️ **新冒号右侧、原槽位之内，本行不许再有墨**（bug-326，2026-10-11 用户报
            #    `包子爱睡觉-15/2.jpg`「顺意」没擦掉）。重切被写出来（bug-183）是因为
            #    **两行框**里第二行的墨把名字与冒号在列投影上并成一段 —— 那种情况下，
            #    冒号右边多出来的那些列是**别的行**的墨，本行是空的。
            #    反过来，冒号右边本行**还有墨**，就说明那不是冒号，是本行名字的下一截，
            #    而这一刀切下去，那截名字既不在蒙版里、也不在尾巴的平移范围里 ⇒ 原样留在
            #    新名字右边。实测两类假阳性都栽在这里：
            #      · 视频号「顺」的**川字竖笔**（宽 4、高 25/33 = 0.76，正好落进下面的闸门）
            #        ⇒ 槽位 1243-1426 被切到 1243-1370，「顺意」跟着尾巴走、一个像素没擦；
            #      · 抖音拉丁昵称（`jlrfc…`）的**每个字母**都又窄又「不高不矮」
            #        ⇒ 在字母中间下刀。
            rest = band_ink[:, b + 1 : slot[1] + 1]
            if rest.any():
                LOGGER.debug(
                    "重切放弃：候选冒号 [%d-%d] 右侧、原槽位右缘 %d 之内，本行还有 %d px 墨"
                    "（那是名字的下一截，不是别的行的墨）",
                    a, b, slot[1], int(rest.sum()),
                )
                return None
            return (name_l, segs[i - 1][1]), (a, b)
    return None


# --------------------------------------------------------------------------
# 上游几何（livepipe 已经量好的框）—— 2026-10-28 「行带改用上游框」
# --------------------------------------------------------------------------
# 背景：``text_box`` 是行、``colon_box`` 是名字/正文的分界、``badge_box`` 是药丸、
# ``emojis[].box`` 是表情。它们比「按像素猜行」稳得多：语料里 5/39 张整行变白，
# 根因就是标注框横跨到了白色浮层卡片上、``estimate_background`` 取到白色。
# 这里的三个函数只做一件事：把上游给出的框换成「取样/改动的窗口」，
# 一律**只许收窄**，框外一个像素都不碰；任何一项对不上就返回 None，
# 调用方原样走旧逻辑（零回归）。


def _geom_box(v) -> Optional[tuple[int, int, int, int]]:
    """上游的一个框 → 4 个整数；不是合法框就 None。"""
    if not v or len(v) != 4:
        return None
    try:
        b = tuple(int(x) for x in v)
    except (TypeError, ValueError):
        return None
    return b if b[2] > b[0] and b[3] > b[1] else None


def _geom_clean(geom: Optional[dict], box_rect: tuple[int, int, int, int],
                *, row_from_colon: bool = False) -> Optional[dict]:
    """校验并规整上游几何；任何一项不像话就整条丢弃（宁可不帮，不可帮倒忙）。

    Args:
        geom: 上游几何（``text_box`` / ``colon_box`` / ``badge_box`` / ``emojis``）。
        box_rect: 允许取样的框 ``(x0, y0, x1, y1)``（内缩、削描边之后）。
        row_from_colon: True 时把**纵向**窗口收到 ``colon_box`` 那一行
            （冒号高度 ± 0.8 倍，上下各留行内升降部），并把带外的 ``badge_box`` /
            表情丢弃。快手 App 的交付框是**多行融合框**（ROI 常装 2~6 行），不按冒号行
            收窄时列投影会把相邻行并成一段 ⇒ 冒号认不出来。默认 False = 旧行为
            （纵向按 ``text_box``，并把药丸并进来）。
    """
    if not geom:
        return None
    tb = _geom_box(geom.get("text_box"))
    if tb is None:
        return None
    cb = _geom_box(geom.get("colon_box"))
    bb = _geom_box(geom.get("badge_box"))
    emos = []
    for e in geom.get("emojis") or []:
        b = _geom_box(e.get("box") if isinstance(e, dict) else e)
        if b:
            emos.append(b)
    x0, y0, x1, y1 = box_rect
    if row_from_colon and cb is not None:
        # 冒号实测高 12~13px、行带 22~25px ⇒ 上下各留 ~10px（0.8×冒号高）就能
        # 包住整行文字，又够不到相邻行（行距 45）。
        # ⚠️ **上限 12**：融合块里偶尔会读到高 30px 的「冒号」（两点跨进了相邻行），
        # 按 0.8×高算会给出 24px 余量、把窗口重新撑成两行（实测 `句句如你（单身）-30/4`）。
        pad = min(12, max(8, int(round((cb[3] - cb[1] + 1) * 0.8))))
        ty0, ty1 = cb[1] - pad, cb[3] + pad
        tb = (tb[0], ty0, tb[2], ty1)
        # 融合块里 ``badge_box`` / emoji 常来自**别的行**（快手实测 badge_box y
        # 比目标行低 30~40px）—— 带外的一律丢弃，压着边界就裁进来。
        def _clip(b):
            if not b or b[3] <= ty0 or b[1] >= ty1:
                return None
            return (b[0], max(b[1], ty0), b[2], min(b[3], ty1))
        bb = _clip(bb)
        emos = [e for e in (_clip(b) for b in emos) if e is not None]
    else:
        # 行：以 text_box 为准，**把药丸并进来**（多行框里药丸在首行，单靠 text_box 会把它切在带外，
        # 于是「擦等级标」擦不到 —— 实测 远___◆19-2/2）。只许收窄，且至少留 6 行。
        ty0 = min(tb[1], bb[1]) if bb else tb[1]
        ty1 = max(tb[3], bb[3]) if bb else tb[3]
    ty0, ty1 = max(y0, ty0), min(y1, ty1)
    if ty1 - ty0 < 6:
        return None
    # 列：名字起 → 正文终（含表情）；药丸在左就一并带上
    right = max([tb[2]] + [b[2] for b in emos] + ([cb[2]] if cb else []))
    # ⚠️ 左边**不收窄**：药丸的圆角底盘比 badge_box（只框住「12」那两个数字）宽得多，
    #    从 badge_box 起切会把药丸左边一截留在窗口外 ⇒ 擦不到，成品上留半块蓝底。
    #    右边才收窄（白卡/视频画面都在右边）。
    tx0, tx1 = x0, min(x1, right + 2)
    if tx1 - tx0 < 16:
        return None
    # 冒号必须落在「文字左沿 → 正文右沿」之间，否则这条 colon_box 不可信
    if cb and not (tx0 - 16 <= cb[0] and cb[2] <= tx1 + 16):
        cb = None
    return {"text_box": tb, "colon_box": cb, "badge_box": bb, "emojis": emos,
            "x_text": geom.get("x_text"), "window": (tx0, ty0, tx1, ty1)}


def _geom_bg_sample(patch: np.ndarray, g: Optional[dict], ox: int) -> np.ndarray:
    """取背景的样本：**只取文字那几列**，并把药丸/表情的列剔掉。

    白卡在文字右侧 ⇒ 不进样本；药丸是彩色实心块 ⇒ 剔掉，免得把药丸色当背景。
    """
    if not g:
        return patch
    tb = g["text_box"]
    h, w = patch.shape[:2]
    c0, c1 = max(0, tb[0] - ox), min(w, tb[2] - ox + 1)
    if c1 - c0 < 8:
        return patch
    keep = np.ones(c1 - c0, dtype=bool)
    for b in g["emojis"] + ([g["badge_box"]] if g["badge_box"] else []):
        a, z = max(0, b[0] - ox - c0), min(c1 - c0, b[2] - ox - c0 + 1)
        if z > a:
            keep[a:z] = False
    if keep.sum() < 8:
        return patch[:, c0:c1]
    idx = np.arange(c0, c1)[keep]
    return patch[:, idx]


def _colon_like_segments(ink: np.ndarray, segs: list[tuple[int, int]]) -> list[int]:
    """挑出「长得像冒号」的列段：窄（≤6px）且**墨迹分成上下两截**（两个点，中间有缝）。

    实测语料里真冒号一律是 4px 宽、两截各 4 行、中间空 4 行（`四___◆3-5/5`、
    `梦___◆8-2/2`、`远___◆19-2/2`、`小___-3/2` 全一致）。旁证：
    星号 8~10px、墨在上半区、单截；逗号（`远` 正文里的「价，」）4px 但单截；
    拉丁字母 `i` 也是两截，但它更靠左，由「离 colon_box 右缘最近」的评分排除。
    """
    out: list[int] = []
    for i, (s, e) in enumerate(segs):
        if e - s + 1 > 6:
            continue
        rows = np.nonzero(ink[:, s : e + 1].any(axis=1))[0]
        if len(rows) < 2:
            continue
        if int(np.count_nonzero(np.diff(rows) > 1)) >= 1:   # 至少一处断档 ⇒ 两个点
            out.append(i)
    return out


def _x_text_local(g: dict, ox: int) -> Optional[int]:
    """上游 ``x_text`` 换成本地列号；缺失或不是数就 None（别拿 0 当有效值）。

    ⚠️ 只在「药丸与名字首字粘成一段」这一种情形用它（见 :func:`_geom_locate`）：
    它的语义逐行不同 —— 有时是名字起点（`远___◆19-2/2.jpg`：106 = 「远」），
    有时是冒号**之后**正文的起点（`小___-3/2.jpg`：106 是「内」）。
    """
    x = g.get("x_text")
    if x is None:
        return None
    try:
        return int(x) - ox
    except (TypeError, ValueError):
        return None


def _geom_locate(
    ink: np.ndarray,
    segs: list[tuple[int, int]],
    stats: list[tuple[float, float]],
    g: Optional[dict],
    ox: int,
    profile: "Optional[platforms.PlatformProfile]" = None,
    *,
    badge_min_width: int = config.BADGE_MIN_WIDTH,
    badge_fill_ratio: float = config.BADGE_FILL_RATIO,
    badge_stroke_max: float = config.BADGE_STROKE_MAX,
) -> "Optional[tuple[int, list[int], int, bool, tuple[int, int], tuple[int, int]]]":
    """用上游的 ``colon_box`` 定冒号，再把「冒号左边那一串文字段」认作名字。

    ⚠️ **不用 ``x_text`` 定名字左沿**（2026-10-28）：它并不可靠 —— 实测
    `小___-3/2.jpg` 的名字从 x=40 起，``x_text`` 却写 106（冒号之后正文的起点），照它
    切片会把名字左半截留在擦除区外（成品上留 ``\\***`` 残迹）。名字的左右沿一律取
    **列段的真实边界**；唯一的例外见下面「药丸把名字首字吞进同一段」那一段。

    ⚠️ **冒号段先按「像冒号」认（窄 ≤6px + 上下两截），再按离 box 右缘最近挑**；
    只在认不出时才退回「与 box 有交叠」。后者会被上游的错 box 骗到：`colon_box` 有时是
    **字格**（装着前一个字符和真冒号），甚至**整段压在正文首字上**（实测 `四___◆3-5/5.jpg`
    box [152,170] = 「空」、`梦___◆8-2/2.jpg` box [153,171] = 「外」）—— 按重叠选会把
    正文首字当冒号、真冒号（4px 宽那两个点）被并进名字一起擦掉，**成品上冒号消失**。
    见 :func:`_colon_like_segments`。

    ⚠️ 往左走的名字串**遇到药丸就收手**（收了文字之后）；药丸在名字**右边**（微博的
    「铁粉牌」夹在名字与冒号之间）时才跨过去继续。药丸判据与启发式同源：
    宽 + 填充率，或宽 + 笔画粗细（两行框里填充率会被框高稀释，必须留这条兜底）。
    不拦的话，两行框里的药丸会被并进名字（实测 `梦___◆8-2/1.jpg` 槽位 40~162）。

    ⚠️ **药丸把名字首字吞进同一段**时（实测 `远___◆19-2/2.jpg`：药丸与「远」在列投影上
    连成一段 40-138），只靠列段边界会把名字量成 22px（只剩冒号左边那两段）。这种
    情形改用上游 ``x_text`` 当名字左沿 —— 但它只在「落在这一段内部、且在药丸右缘
    之后」时才可信（见 :func:`_x_text_local`），别处它可能是正文起点。

    Returns:
        ``(first, name_idx, colon_idx, badge_found, slot_local, colon_local)``；
        对不上（冒号列段不存在、名字里没墨）就 None，调用方退回旧逻辑。
    """
    if not g or not g["colon_box"]:
        return None
    cb = g["colon_box"]
    c0, c1 = cb[0] - ox, cb[2] - ox   # (x0, y0, x1, y1)：取 x0 / x1，别拿 y0

    if not (0 <= c0 < c1 <= ink.shape[1]):
        return None
    # 冒号段：**先认「长得像冒号」的那一段**（窄 + 上下两截，见 :func:`_colon_like_segments`），
    #   在 box 附近取「右缘离盒右缘最近」的。
    #   ⚠️ 为什么不能只按「与 colon_box 有交叠」选（2026-10-28 修）：上游的 box 有时
    #   **整段压在正文首字上** —— 实测 `四___◆3-5/5.jpg` 的 box [152,170] 正是「空」、
    #   `梦___◆8-2/2.jpg` 的 box [153,171] 正是「外」；按重叠选就把正文首字当了冒号，
    #   真冒号（135-138）被并进名字槽位一起擦掉 ⇒ **成品上冒号没了**。
    #   旁证：同两行的星号是 8-10px、墨在上半区、单截，真冒号 4px、两截。
    bi, bscore = None, None
    for i in _colon_like_segments(ink, segs):
        sg = segs[i]
        if not (c0 - 40 <= sg[1] <= c1 + 12):
            continue
        score = (abs(sg[1] - c1), sg[1] - sg[0] + 1)
        if bscore is None or score < bscore:
            bi, bscore = i, score
    if bi is None:
        # 没认出窄冒号（别的字体全角冒号可能更宽）：退回「与 box 有交叠、右缘最近」。
        #   ⚠️ 这条老逻辑留着兜底，但它会被上面那种「box 压在正文首字上」骗到。
        for i, sg in enumerate(segs):
            if min(sg[1], c1) - max(sg[0], c0) + 1 <= 0:
                continue
            score = (abs(sg[1] - c1), sg[1] - sg[0] + 1)
            if bscore is None or score < bscore:
                bi, bscore = i, score
    if bi is None:
        return None
    prof = profile or platforms.PROFILES.get("douyin")
    if prof is None:
        return None
    fills = [f for f, _ in stats]

    def _badge_like(i: int) -> bool:
        a, b = segs[i]
        w = b - a + 1
        if w < badge_min_width:
            return False
        if fills[i] >= badge_fill_ratio:
            return True
        # 两行框里填充率的分母是框高 ⇒ 药丸只占一半，判不出来；用笔画粗细兜底
        return w <= config.BADGE_MAX_WIDTH and stroke_peak(ink, segs[i]) >= badge_stroke_max

    # 名字 = 冒号左边那一串文字段；收了文字之后撞上药丸就停（抖音/视频号版式），
    # 还没收到文字时撞上药丸则跨过去（微博版式：牌子夹在名字与冒号之间）。
    picked: list[int] = []
    left_hint = None
    j = bi - 1
    prev_left = segs[bi][0]
    while j >= 0:
        s, e = segs[j]
        if prev_left - e - 1 > platforms.NAME_RUN_GAP_MAX:
            break
        if _badge_like(j):
            if picked:
                # 偶发：药丸与名字**首字粘成一段**（实测 `远___◆19-2/2.jpg`，局部
                # 21-119 = 药丸 21-79 + 「远」87-119；此处 87 来自 x_text）。
                # 这一段被判成药丸、右缘又压进名字里 ⇒ 只把冒号左边剩下的两段当名字，
                # 槽位只剩 22px（真名字 57px）：① 补满槽位按错的宽度算；
                # ② 选材把 46px 的名字当成「正好放得下」。上游 x_text 若落在这
                # 一段**内部**，就用它当名字左沿 —— 这是 x_text 唯一的用法：
                # 它在别的行并不可靠（`小___-3/2.jpg` 的 106 其实是冒号**之后**
                # 正文的起点，拿它当名字左沿会把「小***」整串留在成品上）。
                lx = _x_text_local(g, ox)
                if lx is not None and s < lx <= e and lx - s >= badge_min_width:
                    bb = g.get("badge_box")
                    if bb is None or lx > bb[2] - ox:
                        left_hint = lx
                break
        else:
            picked.append(j)
        prev_left = s
        j -= 1
    if not picked:
        return None
    picked.sort()
    nf, nl = picked[0], picked[-1]
    name_idx = picked
    slot_left = segs[nf][0] if left_hint is None else min(left_hint, segs[nf][0])
    slot_local = (slot_left, segs[nl][1])
    colon_local = (segs[bi][0], segs[bi][1])
    badge_found = nf > 0 and _badge_like(nf - 1)
    return name_idx[0], name_idx, bi, badge_found, slot_local, colon_local


def find_erase_region(
    img_rgb: np.ndarray,
    box: tuple[int, int, int, int],
    *,
    inset: int = config.BOX_INSET,
    vinset: Optional[int] = None,
    bg_thresh: int = config.BG_DIST_THRESH,
    badge_min_width: int = config.BADGE_MIN_WIDTH,
    badge_fill_ratio: float = config.BADGE_FILL_RATIO,
    badge_stroke_max: float = config.BADGE_STROKE_MAX,
    username_sat_min: int = config.USERNAME_SAT_MIN,
    dilate_px: int = config.MASK_DILATE_PX,
    full_rect: bool = False,
    erase_badge: bool = False,
    profile: "Optional[platforms.PlatformProfile]" = None,
    geom: Optional[dict] = None,
) -> EraseRegion:
    """在标注框内定位「用户名 + 冒号」，生成原图尺寸的擦除蒙版。

    Args:
        img_rgb:         ``(H, W, 3)`` RGB 原图。
        box:             标注框 ``(x0, y0, x1, y1)``，**含彩色描边**。
        inset:           横向内缩量，用于排除描边。
        vinset:          纵向内缩量；``None`` = 与 ``inset`` 相同。**紧贴文字的 roi 必须纵向给 0**，
                         否则字带与擦除蒙版一起被切（见 :func:`inset_box`）。
        bg_thresh:       墨迹判定阈值。
        badge_min_width: 徽章最小宽度。
        badge_fill_ratio:徽章最小填充率。
        username_sat_min:用户名段的核心色饱和度下限。
        dilate_px:       蒙版膨胀半径。
        full_rect:       True 时把「用户名 + 冒号」整段列区间**整块**擦掉（含字间空隙），
                         而不是只擦笔画。背景纯色时两者都行，默认只擦笔画 —— 少擦一点，
                         就少一点「算法自己编出来」的像素。
        erase_badge:     **把等级标也划进擦除范围**（默认 False；mock 线 2026-10-11
                         用户「mock 的所有用户都要把等级标一起替换成用户名」）。
                         True 且真检测到等级标时：蒙版左侧多出等级标那几列、
                         :attr:`EraseRegion.badge_span` / :attr:`EraseRegion.badge_rect`
                         有值，新名字于是从**等级标左沿**起写、尾巴的位移基准也换成
                         「等级标左沿 → 原名字右缘」（规格：「假用户身上不留真用户的
                         任何痕迹」—— 名字与等级都没了）。
                         ⚠️ **不动** ``slot`` / ``slot_width``：「补满槽位」与选材
                         （``batch_produce.fits_exactly``）都按「原名字自己多宽」判，
                         见 :attr:`EraseRegion.name_span_width`。


    Returns:
        :class:`EraseRegion`。

    Raises:
        RegionNotFoundError: 结构对不上（找不到正文段、或除掉徽章后不足两段）。
    """
    img_h, img_w = img_rgb.shape[:2]
    ix0, iy0, ix1, iy1 = inset_box(box, inset, img_w, img_h, vinset)
    # 内缩是**固定像素**，抗锯齿糊进来多少却是逐图不同 —— 再按颜色削一道，
    # 保证「框内」这块地里一个描边像素都没有（bug-155）。
    ix0, iy0, ix1, iy1 = shave_frame(img_rgb, (ix0, iy0, ix1, iy1))
    # **允许动笔的范围**（描边之内）：尾巴平移、药丸填平都以它为准 ——
    # 行带收窄之后仍要留出往右挪的余地，所以两者必须分开（2026-10-28）。
    ax0, ay0, ax1, ay1 = ix0, iy0, ix1, iy1
    # 上游几何只**收窄**「取样 + 改动」的窗口；对不上就 None，下面原样走旧逻辑。
    g = _geom_clean(
        geom, (ax0, ay0, ax1, ay1),
        row_from_colon=bool(profile is not None and profile.geom_row_from_colon),
    )
    geom_used = ""
    if g is not None and g["colon_box"] is not None:
        # 只有**能整步替掉定位**时才收窄窗口：收窄会改变列段与饱和度，
        # 没有 colon_box 的行还得靠启发式认名字，硬收窄会把它们认丢（实测 3 张）。
        ix0, iy0, ix1, iy1 = g["window"]
        geom_used = "band"
        LOGGER.info("用上游行带取样/改动：%s（允许动笔的范围仍是 %s）",
                    (ix0, iy0, ix1, iy1), (ax0, ay0, ax1, ay1))
    patch = img_rgb[iy0:iy1, ix0:ix1]
    LOGGER.debug("框内区域 (%d,%d)-(%d,%d) 尺寸 %dx%d", ix0, iy0, ix1, iy1, ix1 - ix0, iy1 - iy0)

    bg = estimate_background(
        _geom_bg_sample(patch, g, ix0),
        quantize=int(profile.bg_quantize) if profile is not None else 0,
    )
    ink = compute_ink_mask(patch, bg, bg_thresh)
    LOGGER.debug("背景 mode 色 %s，墨迹占比 %.1f%%", tuple(int(v) for v in bg), 100 * ink.mean())

    segs = column_segments(ink.any(axis=0))
    if not segs:
        raise RegionNotFoundError(f"框 {box} 内没有任何墨迹（阈值 {bg_thresh}）")

    stats = [_segment_stats(ink, patch, s) for s in segs]
    diag = [
        (s[0], s[1], int(round(f * 100)), int(round(b)))
        for s, (f, b) in zip(segs, stats)
    ]
    LOGGER.debug("列段 (start, end, 填充率%%, 平均亮度): %s", diag)

    cores = [_core_color(patch, ink, s) for s in segs]
    sats = [max(c) - min(c) for c in cores]

    # ---- 定位：两条策略，由 platform profile 选 ----
    #   ``saturation_run``（抖音，现状）：规则 3/4/5，按**核心色饱和度**认名字。
    #   ``colon_anchor``（视频号 / 微博 / 未知平台）：按**冒号**认分界，颜色不作必要条件。
    #   两条只产出同样四个量，下游（蒙版 / 间距 / 尾巴 / 字带）完全共用、一行不改。
    # 先看上游给了没有：``colon_box`` 能把「名字段 / 冒号」一次定死，
    # 定得下来就**根本不跑**下面两套启发式（收窄过的窗口对它们是不友好的）。
    _ov = _geom_locate(ink, segs, stats, g, ix0, profile)
    if _ov is not None:
        first, name_idx, colon_idx, badge_found, _slot, _colon = _ov
        geom_used = "band+colon"
        LOGGER.info(
            "按上游 colon_box 定分界：名字 [%d-%d] · 冒号 [%d-%d]（绝对 %d-%d / %d-%d）",
            _slot[0], _slot[1], _colon[0], _colon[1],
            _slot[0] + ix0, _slot[1] + ix0, _colon[0] + ix0, _colon[1] + ix0,
        )
    elif profile is not None and profile.name_strategy == "colon_anchor":
        first, name_idx, colon_idx, badge_found = _locate_by_colon(
            ink, segs, stats, profile, box, diag
        )
    else:
        first, name_idx, colon_idx, badge_found = _locate_by_saturation(
            ink, segs, stats, sats, box, diag,
            badge_min_width=badge_min_width,
            badge_fill_ratio=badge_fill_ratio,
            badge_stroke_max=badge_stroke_max,
            username_sat_min=username_sat_min,
        )

    span = (segs[name_idx[0]][0], segs[colon_idx][1])
    # 槽位 = 名字墨迹本身（首段左缘 → 末段右缘）；冒号、尾巴各自单独记 —— 三者要分开用：
    # 名字从槽位左缘起写；能写多宽由 ``max_name_width`` 定；尾巴按名字宽度差整体平移。
    if _ov is not None:
        # 名字右沿按**墨迹**量（槽位要的是「原名字自己多宽」，不能拿冒号左沿减），
        # 冒号用上游给的精确列范围。
        slot_local = (int(_slot[0]), int(_slot[1]))
        colon_local = (int(_colon[0]), int(_colon[1]))
    else:
        slot_local = (segs[name_idx[0]][0], segs[name_idx[-1]][1])
        colon_local = segs[colon_idx]

    # ---- 上下：名字的**字带** + 框里的**行带**（2026-09-30 规格：定位时要把上下量准） ----
    #   ① 字高过去取墨迹蒙版的**极值行**，会被描边内侧的抗锯齿残渣钉到整个内缩框高
    #      （实测 84px vs 本体 ~33px）⇒ 字号算成 88px ⇒ 新名字上下撑出框（bug-166）。
    #      改成取**墨量最大**的那条行带。
    #   ② 框检测只要求「框内前景占比 ≤0.35」，两行评论之间的空隙让它过关 ⇒ 框高 96 装两行。
    #      这类框四道判据全绿却把两行一起擦掉（bug-168）。改成：框里有多条行带时，
    #      **只保留与名字重叠最多的那一条**，带外的一切（擦除蒙版、冒号行、尾巴行）都不动。
    # ⚠️ 名字的**列**：两条策略的差别正在这里。
    #   ``saturation_run``（抖音）：名字与冒号紧挨着，``span`` = 名字 + 冒号，没有夹层。
    #   ``colon_anchor``（微博/视频号）：**药丸夹在名字与冒号之间**（实测微博
    #   ``[名字][铁粉牌][：][正文]``），``span`` 会把药丸圈进来 ⇒ ① 取色被药丸带跑
    #   （灰名字被染成淡金，bug-180）；② 字带被药丸的行范围撑大。
    #   ⇒ 所以这种版式只按**名字自己的列**（name_idx 的并集）取色与量字带。
    #   ⚠️ **两条策略都改成「只取名字自己的段」**（`name_idx` 的并集）。过去两边都用
    #   ``span``（名字首段 → 冒号末段），实测**两边都会被污染**：
    #   · ``colon_anchor``（微博/视频号）：药丸夹在名字与冒号之间 ⇒ 被药丸染黄（bug-180）；
    #   · ``saturation_run``（抖音）：紧跟名字的那一段可能是**与正文连成一段的「冒号」**
    #     （实测 `军陌-4/3`：112px 宽、含正文的**白字**）⇒ 名字被染白（bug-182）。
    name_cols_1d = np.zeros(ink.shape[1], dtype=bool)
    for k in name_idx:
        name_cols_1d[segs[k][0] : segs[k][1] + 1] = True
    name_cols = np.broadcast_to(name_cols_1d[None, :], ink.shape)
    name_ink = ink & name_cols
    name_bands = row_bands(name_ink.sum(axis=1).astype(int), config.GLYPH_ROW_GAP_PX)
    if not name_bands:
        raise RegionNotFoundError(f"框 {box} 内定位到的擦除区间 [%d-%d] 里没有墨迹" % span)
    # ⚠️ 名字所在的行 = **冒号所在的行**（冒号紧跟名字，定义如此）。
    #   不能只取「墨量最大的那条带」：两行框里 span 的列**同时含两行文字**，而第二行
    #   常常墨更多 ⇒ 会把字高量到**另一行**上去。实测 `一麻袋开心(沧海一舟)-6/5.jpg`：
    #   两行的质量是 1577 / **2295** ⇒ 取到第二行（38px），而名字在第 1~31 行 ——
    #   于是字号按另一行算，名字画得又大又偏。冒号的行范围是确定的，用它来锚。
    #
    # ⚠️ **上游给了 colon_box 就直接用它的 y**（2026-10-28）：两行框里两个名字等宽时，
    #    「名字那几列」在两行上都有墨、列完全重合，按**墨迹列**找行号会挑到墨更多的那行
    #    —— 实测 `梦___◆8-2/1.jpg` 挑中第二行（好***），把别人的名字换掉、还动了上一行
    #    的药丸。colon_box 的 y 是上游量出来的，属于哪一行没有歧义。
    if _ov is not None:
        c_lo, c_hi = g["colon_box"][1] - iy0, g["colon_box"][3] - iy0
        glyph_band = max(
            name_bands,
            key=lambda b: (max(0, min(b[1], c_hi) - max(b[0], c_lo) + 1), b[2]),
        )
        LOGGER.debug(
            "字带按「与上游 colon_box 行范围重叠」选：冒号在 %d~%d，选中 %d~%d",
            c_lo, c_hi, glyph_band[0], glyph_band[1],
        )
    else:
        colon_probe = np.nonzero(ink[:, colon_local[0] : colon_local[1] + 1].any(axis=1))[0]
        if len(colon_probe):
            c_lo, c_hi = int(colon_probe.min()), int(colon_probe.max())
            glyph_band = max(
                name_bands,
                key=lambda b: (max(0, min(b[1], c_hi) - max(b[0], c_lo) + 1), b[2]),
            )
            LOGGER.debug(
                "字带按「与冒号行范围重叠」选：冒号在 %d~%d，选中 %d~%d",
                c_lo, c_hi, glyph_band[0], glyph_band[1],
            )
        else:
            glyph_band = max(name_bands, key=lambda b: b[2])

    # 兜底：全墨主带**高得不像一行字**时，改用「本色墨迹」的那条带。
    #   一行字的上限直接复用 ``config.NAME_EMOJI_MAX_SEG_H``（45px，本就是为「一行」
    #   量出来的：本语料单行 27~32，两行/跨行的段 51~103）。超过它说明这一条带里
    #   混进了别的东西（头像、礼物动画、或者两行被连成了一条），而名字的笔画是
    #   **核心色够饱和**的、混进来的图形通常不是。
    #   ⚠️ 只在超限时才走这条路：金标准 8 张的带是 26~28px，压根触发不到（零回归）。
    one_line_max = config.NAME_EMOJI_MAX_SEG_H
    if glyph_band[1] - glyph_band[0] + 1 > one_line_max:
        chroma = patch.max(axis=2).astype(int) - patch.min(axis=2).astype(int)
        sat_bands = row_bands(
            (name_ink & (chroma >= username_sat_min)).sum(axis=1).astype(int),
            config.GLYPH_ROW_GAP_PX,
        )
        pick = max(sat_bands, key=lambda b: b[2]) if sat_bands else None
        if pick is not None and pick[1] - pick[0] + 1 <= one_line_max:
            LOGGER.warning(
                "字带 %dpx 高得不像一行字（上限 %d）⇒ 改用本色墨迹带 [%d-%d]（%dpx）",
                glyph_band[1] - glyph_band[0] + 1, one_line_max,
                pick[0] + iy0, pick[1] + iy0, pick[1] - pick[0] + 1,
            )
            glyph_band = pick

    # ---- 兜底：名字与冒号**被第二行的墨在列上并成一段**时，在名字这一行上重切（bug-183）----
    #   两行框里列投影会把第二行的墨连进来，名字与冒号粘成一段 ⇒ 冒号被当名字擦掉
    #   （用户报「冒号没有了」），而且槽位宽度虚高。切出来之后：冒号归尾巴（跟着平移、
    #   一个像素都不动，与「冒号从不重画」的规格一致），槽位回到真名字的宽度。
    box_bands = row_bands(
        ink.sum(axis=1).astype(int), config.ROW_BAND_GAP_PX, config.ROW_BAND_MIN_ROWS,
    )
    multi_line = len(box_bands) >= 2
    if multi_line:
        band = max(
            box_bands,
            key=lambda b: max(0, min(b[1], glyph_band[1]) - max(b[0], glyph_band[0]) + 1),
        )
        LOGGER.warning(
            "框 %s 里装了不止一行文字（行带 %s）—— 按名字所在的那条带 [%d-%d] 处理，"
            "带外一概不动（bug-168）",
            box, [b[:2] for b in box_bands], band[0] + iy0, band[1] + iy0,
        )
    else:
        band = (0, ink.shape[0] - 1)

    # ---- 多行框：药丸填平 / 尾巴搬家的**行窗口**也不许越过本条评论 ----
    #   擦除蒙版本来就夹在 ``row_keep`` 里，但 ``_badge_rect``（整块铺平药丸）和
    #   ``_tail_real_rows``（尾巴搬家的行范围）是**在整幅图上、按列量**的：框里有两条
    #   评论时，同一列在两条上都墨 ⇒ 行范围会把两行串起来 ⇒ 上一行的药丸被铺平、
    #   上一行的正文跟着搬走。实测 `梦___◆8-2/1.jpg`（bug-xxx）。
    #   夹到「本条行带 → 相邻行带的空隙中线」，既保住本条探出框的 emoji，又碰不到邻居。
    row_limit = None
    if multi_line:
        _above = [b[1] for b in box_bands if b[1] < band[0]]
        _below = [b[0] for b in box_bands if b[0] > band[1]]
        _vext = config.TAIL_WIN_VEXT
        _lo = band[0] - (_vext if not _above else min(_vext, max(1, (band[0] - max(_above)) // 2)))
        _hi = band[1] + (_vext if not _below else min(_vext, max(1, (min(_below) - band[1]) // 2)))
        row_limit = (_lo + iy0, _hi + iy0)
        LOGGER.debug("多行框：药丸填平 / 尾巴搬家的行窗口夹在 [%d-%d]", *row_limit)
    # ⚠️ 再用**字带**夹一次（bug-178）。行带可能是「两条文字粘成一条」——
    #   实测 ``军陌-4/3``：行带 79px、而字带只有 28px（字带那条 45px 兜底已经算准了），
    #   但过去只有**字号**用了字带，``row_keep`` 没用 ⇒ 擦除蒙版纵向漫到第二行、
    #   把名字列下方压着的第二行墨迹啃掉 251px；尾块的行范围也跟着跨两行。
    #   ``glyph_band`` 是**名字自己的墨迹行范围**，蒙版本来就在它 ± 膨胀之内 ——
    #   所以对单行框和已正确切分的两行框，这一步是**恒等**的，只在行带粘连时生效。
    margin = config.MASK_DILATE_PX + 2
    lo = max(band[0], glyph_band[0] - margin)
    hi = min(band[1], glyph_band[1] + margin)
    row_keep = np.zeros(ink.shape[0], dtype=bool)
    row_keep[lo : hi + 1] = True
    rows_2d = row_keep[:, None]

    # ⚠️ 上游给了 colon_box 时**不重切**（2026-10-28）：重切是给「两行框里名字与冒号
    #    在列投影上粘成一段」用的，而上游已经把那一段的边界量出来了 —— 再切一次只会
    #    在名字内部的窄段（星号、拉丁字母）中间下刀。实测 `2凌___-2/2.jpg`：
    #    正确的冒号是 [135-138]，重切却认了最后一个 `*` [115-122] 当冒号，`**` 留在成品上。
    split = None if _ov is not None else _resplit_on_band(
        ink, row_keep, slot_local, (glyph_band[0], glyph_band[1])
    )
    if split is not None:
        (nl, nr), (cl, cr) = split
        if (cl, cr) != colon_local:
            LOGGER.info(
                "名字与冒号被第二行的墨并成一段 ⇒ 在这一行上重切：槽位 %d-%d → %d-%d，冒号 → %d-%d",
                slot_local[0] + ix0, slot_local[1] + ix0, nl + ix0, nr + ix0, cl + ix0, cr + ix0,
            )
            slot_local = (nl, nr)
            colon_local = (cl, cr)


    # 冒号自己占的行范围 —— 平移那整块像素时要用（不能拿名字的行范围：冒号只占一行的一部分）
    #
    # 这里跟 ``tail_ink`` 一样**切进名字的行带**：两行框里，冒号那几列在第二行上也有墨迹，
    # 不切就会把第二行的行范围也算进来（尾巴块会横跨两行）。窗口必须落在名字所在的那一行。
    colon_cols = np.zeros_like(ink)
    colon_cols[:, colon_local[0] : colon_local[1] + 1] = True
    colon_rows_local = np.nonzero((ink & colon_cols & rows_2d).any(axis=1))[0]
    if len(colon_rows_local) == 0:
        raise RegionNotFoundError(f"框 {box} 的冒号列区间 {colon_local} 里没有墨迹")

    # ---- 尾巴组 = 名字与冒号之间的**一切** ＋ 冒号 ＋ 右侧（正文等）----
    #   ⚠️ 夹在名字与冒号之间的东西（实测微博的「铁粉」牌就在这儿）必须**跟着尾巴一起走**：
    #   ① 它占的正是「名字变宽时新名字要占的那块地」⇒ 不搬就会被压住；
    #   ② 判据「名字右缘之后的第一条墨迹列」会把它当成冒号 ⇒ 间距当场报 GAP_BAD
    #      （实测微博 12 张里 4 张中招）。抖音/视频号的牌子在名字**左边**，这条对它们不生效。
    tail_left_local = colon_local[0]
    for k in range(name_idx[-1] + 1, colon_idx):
        tail_left_local = min(tail_left_local, segs[k][0])
    group_cols = np.zeros_like(ink)
    group_cols[:, tail_left_local:] = True

    # ---- 「名字↔冒号」的原始间距：**按墨迹量**，不按列段边界 ----
    #   列段左缘由**任何一行**的墨迹决定（两行框里同一列在第二行也有字）⇒ 直接拿
    #   ``colon_span[0] - slot[1] - 1`` 会比真实间距小几个像素，与结果图上量出来的
    #   ``gap_now`` 差 3~5px ⇒ 3 张图报 GAP_BAD。这里改成与 ``gap_now`` **同一把尺子**
    #   （同一个行窗口、名字右缘之后的第一条墨迹列）。
    gap_cols = np.nonzero((ink & group_cols & rows_2d).any(axis=0))[0]
    gap_cols = gap_cols[gap_cols > slot_local[1]]
    if len(gap_cols) == 0:
        raise RegionNotFoundError(f"框 {box} 的名字右缘之后找不到尾巴墨迹（列段 {colon_local}）")
    join_gap_measured = int(gap_cols.min()) - int(slot_local[1]) - 1

    # ---- 尾巴 = 冒号 + 它右侧的一切（正文等）----
    # 规格说「正文也是相对距离不变」⇒ 尾巴得跟着名字**整体平移**，
    # 所以要的是这一整条的**包围盒**，而不是正文段自己的范围。
    tail_cols = group_cols                          # 到内缩框右缘为止
    tail_ink = ink & tail_cols & rows_2d
    tail_row_idx = np.nonzero(tail_ink.any(axis=1))[0]
    tail_col_idx = np.nonzero(tail_ink.any(axis=0))[0]
    if len(tail_row_idx) == 0 or len(tail_col_idx) == 0:
        raise RegionNotFoundError(f"框 {box} 的冒号右侧没有任何墨迹")
    # ⚠️ 行范围要**在框外再量一次**（见 :func:`_tail_real_rows`）：框只框住那一行汉字，
    #    正文里的 emoji 上下会探出去 —— 只搬框内那一条，原位置就留半截（2026-10-11 用户报的
    #    「移动正文，没有完全移动」）。列范围照旧按框内量（尾巴不许越过框右界，横向没得放）。
    tail_rows_real, tail_win = _tail_real_rows(
        img_rgb, (ax0, ay0, ax1, ay1), bg,
        (int(tail_col_idx.min()) + ix0, int(tail_col_idx.max()) + ix0), ax1,
        row_limit=row_limit,
    )
    # 正文左边界 = 冒号之后的第一个段；若冒号已是最后一段，就拿框的右边界兜底
    comment_left_local = segs[colon_idx + 1][0] if colon_idx + 1 < len(segs) else (ix1 - ix0)
    LOGGER.debug(
        "用户名段 idx=%s 饱和度=%s → 冒号段 [%d-%d]；擦除列区间 [%d-%d]（宽 %d），正文左边界 %d",
        name_idx, [sats[k] for k in name_idx],
        segs[colon_idx][0], segs[colon_idx][1],
        span[0], span[1], span[1] - span[0] + 1, comment_left_local,
    )

    # ---- 生成蒙版：**只擦用户名那一块，冒号一个像素都不碰** ----
    #   规格是「冒号前是要修改的，冒号及其后不修改」。既然冒号不改，就**别擦它** ——
    #   擦了再用 Pillow 画回去，字形宽度会差 1~2px（实测 8 张全部如此：原冒号墨迹
    #   745-748，重画后 745-750），多出来的那 2px 就漏到冒号**右边**去了，
    #   「冒号及其后逐像素不变」当场失效。保留原文则这条是**构造保证**。
    #   代价是冒号沿用原字体——而 ``fit=clip`` 下字号本就等于原字高，两者一致。
    core = np.zeros_like(ink)
    if full_rect:
        core[:, slot_local[0] : slot_local[1] + 1] = True
    else:
        core[:, slot_local[0] : slot_local[1] + 1] = ink[:, slot_local[0] : slot_local[1] + 1]

    # ---- 可选：等级标那几列也划进蒙版 ----
    #   ⚠️ 三层口径一处都不能动：
    #   ① 只往 **core**（= 蒙版）里加，**不动** ``slot_local`` —— 擦完等级标之后
    #      「补满槽位」与选材仍按 ``slot_width``（原名字自己多宽）判，
    #      见 :attr:`EraseRegion.name_span_width`；
    #   ② 只在**名字这一行的行带**里加（与名字同样受 ``row_keep`` 约束），
    #      免得碰到框里第二行评论（bug-168）；
    #   ③ 药丸**探出内缩框**的那 1~2 行在这里够不着（``patch`` 里根本没有它们）——
    #      那些列由 ``EraseRegion.badge_rect`` → ``fill_local`` 在整幅图上铺平。
    #   等级标是**实心**的，所以 ``ink`` 在它那几列上基本铺满 ⇒ 与 full_rect 等价；
    #   照旧走 ``ink`` 而不是直接置 True，只为保住「不编像素」这条口径。
    badge_span = badge_rect = capsule_head = None
    # 上游给了 ``badge_box`` 就算**有药丸**，哪怕列段因为抗锯齿把药丸和名字并成了一段
    # （实测 远___◆19-2/2：bg 修正后药丸与名字之间 8px 的空隙被填平 ⇒ 并成一段，
    #  ``name_idx[0] == 0`` ⇒ 旧判据认为「没有药丸」，成品上留了半块蓝底）。
    _has_badge = badge_found or bool(g and g["badge_box"])
    if erase_badge and _has_badge:
        if badge_found and first > 0:
            bl = max(0, segs[first - 1][0])
        else:
            # 药丸左沿 = 名字左沿以左、这一行里最靠左的那一列墨
            _cols = np.nonzero(ink[:, : slot_local[0]].any(axis=0))[0]
            bl = int(_cols.min()) if len(_cols) else 0
        core[:, bl : slot_local[0]] |= ink[:, bl : slot_local[0]]
        # ⚠️ 右沿止于**名字左沿**：药丸段一旦与名字并成一段，拿段尾会把名字/冒号一起算进去。
        badge_span = (int(bl) + ix0, int(slot_local[0]) - 1 + ix0)
        # 先量**胶囊头**（在整幅图上量）：它同时管两件事 —— 填平地往左不许越过它、
        # 以及新名字从「它 + 固定间距」起写（见 EraseRegion.name_left）。
        # 量不到（抖音那种框贴着名字起的图）就一路照旧。
        capsule_head = _capsule_head(
            img_rgb, (ax0, ay0, ax1, ay1),
            (glyph_band[0] + iy0, glyph_band[1] + iy0),
            badge_span[0] - 1,
        )
        badge_rect = _badge_rect(img_rgb, (ax0, ay0, ax1, ay1), badge_span, bg,
                                 head=capsule_head, row_limit=row_limit)
        if badge_rect is None:
            # 量不到就**如实退化**：蒙版里那几列照擦，但探出框的那几条药丸边没人管 ——
            # 说一声，别让「等级标还剩一道边」变成静默的怪现象。
            LOGGER.warning("框 %s 擦等级标：量不到药丸的行范围 ⇒ 探出内缩框的边会留下", box)

    mask_patch = core.astype(np.uint8) * 255
    if dilate_px > 0:
        k = 2 * dilate_px + 1
        mask_patch = cv2.dilate(mask_patch, np.ones((k, k), np.uint8), iterations=1)

    # ⚠️ 膨胀会往右漫 —— 名字右缘到冒号左缘只有 ``join_gap`` 个像素（实测 5），
    #    膨胀 3px 之后只剩 2px 余量，再多一点就把冒号啃掉一角。
    #    所以这里**硬切**：冒号列起一律清零，不靠「半径够小」。
    mask_patch[:, colon_local[0] :] = 0

    # ⚠️ 同理，**上下也要硬切**：框里装了两行时，名字槽位那几列在第二行上也有墨迹，
    #    不切就会把第二行一起擦掉（bug-168）。单行框下 ``row_keep`` 全 True，等于没切。
    mask_patch[~row_keep, :] = 0

    full_mask = np.zeros((img_h, img_w), dtype=np.uint8)
    full_mask[iy0:iy1, ix0:ix1] = mask_patch

    # ⚠️ 膨胀会让蒙版漫出框内区域，必须先夹回来 —— 否则会把**绿色描边本身**擦掉。
    #    这是本模块唯一一处会「静默出错」的地方：出错时结果图看着挺正常，只是框没了，
    #    批量跑的时候极难被肉眼发现。所以这里用「框外一律清零」而不是靠膨胀半径够小。
    #    ⚠️ 反过来说：**这块地的纵向范围就是「名字能擦到哪」**。紧贴文字的 roi 若纵向内缩
    #    （`vinset > 0`），字的最上/最下几行落在 `keep` 之外 ⇒ 永远擦不到、成品留残墨
    #    （2026-10-11 用户「擦除不干净」的根因，见 :func:`inset_box`）。
    keep = np.zeros((img_h, img_w), dtype=bool)
    keep[iy0:iy1, ix0:ix1] = True
    full_mask[~keep] = 0

    # ---- 字形行范围与主体色（都取自**名字**部分，不含冒号） ----
    #    行范围用上面算好的 ``glyph_band``（墨量最大的那条行带），**不是墨迹的极值行** ——
    #    极值行会被描边内侧的抗锯齿残渣钉住，实测把字高从 ~33px 顶到 84px（bug-166）。
    glyph_top, glyph_bottom = glyph_band[0] + iy0, glyph_band[1] + iy0
    # ---- 主体色 = **名字自己的列 ∩ 名字所在的行** ----
    #   ⚠️ 行也必须夹：两行框里「名字那几列」在**第二行**上也有字，而第二行常常更亮 ⇒
    #   把名字染成别的颜色。实测 `一麻袋开心(沧海一舟)-6/1`：名字本色青蓝 RGB(166,218,238)，
    #   被染成 (192,214,186)；(…)-6/5 被染成 (220,210,133) **发黄**。
    #   单行框下 rows_2d 就是名字的墨迹行，这一步是**恒等**的。
    #   ⚠️ **列也要再夹一道：把 emoji 那几段剔出去**（bug-170/180/182 的第三处同型）。
    #   名字里的 emoji 是一条**又亮又彩**的实心团块，混进前 25% 亮像素里就把整段带跑：
    #   实测 `神秘人-3/3.jpg`（🎇 在名字段内，列 662~690，核心色 (246,235,37) 金）
    #   ⇒ 青色名字 (156,220,240) 被取成 (175,223,196) **发绿**，用户 2026-10-11 报的
    #   `东极勇哥-3/扩展2.jpg` 就是这一张底图、这一段取色（见 `expand/扩展张数.md` §25）。
    #   色带：抖音用标定好的 (60, 92)；``colon_anchor`` 平台按**名字段自己的彩度**现算。
    #   ⚠️ 不这么做的话，白/灰名字（彩度 0~5）全落在带外 ⇒ 名字被整片报成 emoji
    #     ⇒ 批量流程的 SKIP_EMOJI 会把视频号/微博**每一张**都跳过（实测踩到过）。
    if profile is not None and profile.name_strategy == "colon_anchor":
        _ncore = int(np.median([sats[i] for i in name_idx]))
        _nspread = platforms.NAME_CHROMA_SPREAD
        emoji_segs = _emoji_seg_idx(
            ink, segs, sats, name_idx,
            sat_min=max(0, _ncore - _nspread), sat_max=_ncore + _nspread,
        )
    else:
        emoji_segs = _emoji_seg_idx(ink, segs, sats, name_idx)
    color_cols_1d = name_cols_1d.copy()
    for _i, _n, _h in emoji_segs:
        color_cols_1d[segs[_i][0] : segs[_i][1] + 1] = False
    if not color_cols_1d.any():
        # 整段名字都被判成 emoji（理论上不该发生）⇒ 宁可退回原窗口，也不取「无墨 ⇒ 黑」
        color_cols_1d = name_cols_1d
    text_color = _core_color_of(patch, ink, color_cols_1d, row_keep)
    # emoji 只**报告**，不改这里任何几何 —— 擦不擦、跳不跳是调用方的事
    # （批量流程据此跳过整张图，理由见 config.NAME_EMOJI_SAT_MAX）。
    name_emoji = [
        (segs[i][1] - segs[i][0] + 1, n, h, sats[i]) for i, n, h in emoji_segs
    ]
    if name_emoji:
        LOGGER.info(
            "用户名段里有 emoji：%s（判据载在这几段里；几何照常，取色窗口已把它们剔出去）",
            " ".join(f"w{w}/墨{n}/高{h}/彩{c}" for w, n, h, c in name_emoji),
        )

    ys, xs = np.nonzero(full_mask)
    erase_box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

    LOGGER.info(
        "定位到用户名+冒号：列 [%d-%d] · 槽位 [%d-%d] 宽 %d · 冒号 [%d-%d] · 拼接距离 %dpx"
        " · 字高 %dpx（行带 [%d-%d]）· 色 RGB%s · 蒙版 %d px%s",
        span[0] + ix0, span[1] + ix0,
        slot_local[0] + ix0, slot_local[1] + ix0, slot_local[1] - slot_local[0] + 1,
        colon_local[0] + ix0, colon_local[1] + ix0,
        colon_local[0] - slot_local[1] - 1,
        glyph_bottom - glyph_top + 1, glyph_top, glyph_bottom,
        text_color, int((full_mask > 0).sum()), "（含徽章）" if badge_found else "",
    )
    region = EraseRegion(
        mask=full_mask,
        erase_box=erase_box,
        span=(span[0] + ix0, span[1] + ix0),
        slot=(slot_local[0] + ix0, slot_local[1] + ix0),
        colon_span=(colon_local[0] + ix0, colon_local[1] + ix0),
        colon_rows=(
            int(colon_rows_local.min()) + iy0,
            int(colon_rows_local.max()) + iy0,
        ),
        tail_span=(
            int(tail_col_idx.min()) + ix0,
            int(tail_col_idx.max()) + ix0,
        ),
        tail_rows=tail_rows_real,
        box_right=ax1,
        box_rect=(ax0, ay0, ax1, ay1),
        glyph_top=glyph_top,
        glyph_bottom=glyph_bottom,
        text_color=text_color,
        bg_color=tuple(int(v) for v in bg),
        badge_found=badge_found,
        comment_left=comment_left_local + ix0,
        segments=diag,
        name_emoji=name_emoji,
        join_gap=join_gap_measured,
        multi_line=multi_line,
        line_band=(band[0] + iy0, band[1] + iy0),
        geom_used=geom_used,
        tail_win=tail_win,
        badge_span=badge_span,
        badge_rect=badge_rect,
        capsule_head=capsule_head,
    )

    if badge_span is not None:
        # ⚠️ 「起写列」与「装配基准」都读**同一个** region（`name_left` / `name_span_width`），
        #    这里不再自己算一遍 —— 两处算式一分开，日志就会跟实际写入的位置不一样。
        LOGGER.info(
            "等级标一并擦除：列 [%d-%d] · 填平地 %s · 胶囊头 %s · 新名字从 %d 起写"
            "（装配基准 %dpx = 原槽位 %d + 让出来的那一块；补满目标仍按原槽位 %d）",
            badge_span[0], badge_span[1], badge_rect,
            f"{capsule_head}（离它 {region.name_left - capsule_head}px）" if capsule_head is not None
            else "没量到（往左照旧多吃一点、名字从药丸左沿起写）",
            region.name_left,
            region.name_span_width,
            region.slot_width,
            region.slot_width,
        )
    return region
