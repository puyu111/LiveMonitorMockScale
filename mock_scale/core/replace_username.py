"""主流程：标注框 → 定位用户名 → 本地确定性擦除 → 写新用户名。

本实验要回答的问题只有一个：**在「背景近乎纯色」这个最简情形下，本地 Telea
能不能替代 DashScope 擦除接口？** 所以每一步都留下**可量化的证据**，而不是只出图：

===================  ==========================================================
指标                 含义
===================  ==========================================================
``ink_before``       擦除框内「还不是背景色」的像素数（框内一共这么多墨）
``ink_after``        擦完之后同一个框内的非背景像素数 —— **越接近 0 越干净**
``outside_changed``  蒙版外被改动的像素数，**必须是 0**（本地路线的局部性保证）
``overflow_px``      新名字比原来宽出多少（>0 = 压到右侧正文了）
===================  ==========================================================

``ink_after`` 是这里的关键。对照 work/test 那 14 次真调：API 那侧有 **4 张擦完之后
墨迹比原来还多**（模型在蒙版区凭空造字）—— 这个指标当时就是用来抓住那个问题的，
现在用同一把尺子量本地路线，两者可以直接比。
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import random
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
from PIL import Image

import config
import platforms
import draw_text
import inkmask
import inpaint_local

LOGGER = logging.getLogger("replace_username")


@dataclass
class Outcome:
    """单张图的处理结果与度量。"""

    file: str
    ok: bool
    error: str = ""
    box: Optional[tuple[int, int, int, int]] = None
    span: Optional[tuple[int, int]] = None
    slot: Optional[tuple[int, int]] = None
    colon_span: Optional[tuple[int, int]] = None
    join_gap: int = 0
    slot_width: int = 0
    max_name_width: int = 0
    tail_diff_px: int = -1
    """尾块平移后，与原图同一块**逐像素差**的个数（-1 = 没测）。

    这是「平移而非重画」这条承诺的实测值：尾巴是原图像素整块搬过去的，所以只要
    搬对了，这块就该**逐像素相同**。>0 说明有东西压上去了（``fit=height`` 溢出时
    会压住冒号，那是有意为之）或被啃掉了。

    只比**墨迹包围盒**（``tail_span`` × ``tail_rows``），不含 ``TAIL_MOVE_PAD``
    那一圈 —— 那一圈本来就是背景，比它没有鉴别力。
    """
    tail_leftover_px: int = -1
    """尾巴**留在原地**、没被搬走的墨迹像素数（-1 = 没测）。

    真实尾巴行范围（``tail_rows``，在标注框外量的）里，落在搬家竖条之外的那些墨迹：
    正文里的 emoji 比汉字高、上下会探出标注框，搬家的竖带光夹在框里就搬不走它们
    （用户 2026-10-11「移动正文，没有完全移动」）。

    ⚠️ 与 :attr:`tail_diff_px` **不是**同一条：那一条比的是「目的地那块像不像原图」
    （留在原地的墨迹在目的地上是"缺了一块"，也会红），但它分不清「留了半截」与
    「新写的字压住了尾巴」；这一条**只**看见留在原地的墨迹。位移 0（尾巴本来就不用搬）记 0。
    """
    mask_pixels: int = 0
    ink_before: int = 0
    ink_after: int = 0
    outside_changed: int = 0
    tail_shift_px: int = 0
    gap_now: int = -1
    gap_ok: bool = False
    assembly_right: Optional[int] = None
    box_right: int = 0
    box_ok: bool = False
    glyph_height: int = 0
    text_color: Optional[tuple[int, int, int]] = None
    bg_color: Optional[tuple[int, int, int]] = None
    badge_found: bool = False
    capsule_head: Optional[int] = None
    """评论行底下那颗半透明深色**胶囊的左圆头**在哪一列；``None`` = 没量到/不适用。

    只有擦等级标时才会去量（``inkmask._capsule_head``）。它决定了新名字的**起写列**
    （``name_left = capsule_head + config.CAPSULE_HEAD_NAME_PAD``，用户 2026-10-11
    「计算胶囊头距离用户名的距离变成固定值」），也决定药丸那块地往左填到哪。
    """
    name_left: int = 0
    """新名字**从哪一列起写**（= ``inkmask.EraseRegion.name_left``）。

    擦等级标时它由胶囊头 + 固定间距定，不擦时是原名字自己的左沿。
    量「胶囊头 → 用户名」这条规格时**用它**，别拿成品图上的墨迹左沿反推
    （字形有左留白，能差 1~2px）。
    """
    font_size: int = 0
    drawn_name: str = ""
    text_width: int = 0
    truncated_chars: int = 0
    stripped_chars: int = 0
    """从新名字里剔掉的 emoji 字符数（规格：新文本不含表情）。"""
    filled_chars: int = 0
    """为补满原槽位随机追加的字符数（规格：总长不够就补满）。"""
    name_emoji: str = ""
    """原图用户名段里被判为 emoji 的那几段（``w28/墨556/高29/彩191``），空 = 纯文字。

    判据在 :data:`config.NAME_EMOJI_SAT_MAX`。**本函数不据此改变行为** ——
    单张模式照常出片（只是日志里说一声），要不要跳过整张图是调用方的事：
    批量流程会跳（``batch_run.Row.skip``，2026-09-30 规格）。
    """
    multi_line: bool = False
    """检测到的框里是否装了**不止一行**文字（定位按行带切，见 ``inkmask.EraseRegion``）。

    True 时本次出片**只动名字所在的那一行带**，第二行原样留着（bug-168）。
    """
    line_band: tuple[int, int] = (0, 0)
    """本次实际使用的行带（绝对行号，闭区间）。"""
    overflow_px: int = 0
    elapsed_s: float = 0.0
    output: str = ""

    result_image: Optional[np.ndarray] = field(default=None, repr=False, compare=False)
    """结果图（RGB ndarray），**只在调用方要求时**才填。

    批量脚本拿它做追加校验（例如「尾块平移后是否逐像素等于原图」）而**不落盘** ——
    650 张 × 3 个探针 ≈ 1950 张结果图，PNG 存下来是几个 GB 的纯浪费。
    ``repr=False`` 是因为 1080×1920 的数组一旦进日志就是灾难。
    """

    @property
    def reduction(self) -> float:
        """墨迹清除率 = 1 - after/before。1.0 = 擦得一个不剩。"""
        if self.ink_before <= 0:
            return 1.0 if self.ink_after <= 0 else 0.0
        return 1.0 - self.ink_after / self.ink_before

    def as_row(self) -> str:
        """格式化成报告表格的一行。"""
        if not self.ok:
            return f"  ✗ {self.file:<12} {self.error}"
        cut = f" 截{self.truncated_chars}字" if self.truncated_chars else ""
        fill = f" 补{self.filled_chars}字" if self.filled_chars else ""
        emo = f" 去emoji{self.stripped_chars}" if self.stripped_chars else ""
        right = "?" if self.assembly_right is None else str(self.assembly_right)
        room = "?" if self.assembly_right is None else str(self.box_right - 1 - self.assembly_right)
        return (
            f"  {'✓' if self.clean else '⚠'} {self.file:<12} "
            f"墨迹 {self.ink_before:>6} → {self.ink_after:<6} "
            f"(清 {self.reduction * 100:5.1f}%)  "
            f"框外改动 {self.outside_changed}  "
            f"字高 {self.glyph_height:>2}px 字号 {self.font_size:>2}  "
            f"名字「{self.drawn_name}」{self.text_width}/{self.max_name_width}px{cut}{fill}{emo}  "
            f"冒号距名字 {self.gap_now}px(原 {self.join_gap}){'✓' if self.gap_ok else '✗'} "
            f"尾巴移 {self.tail_shift_px:>+3}px  "
            f"尾块差 {self.tail_diff_px:>4}px{'✓' if self.tail_exact else '✗'} "
            f"尾残留 {self.tail_leftover_px:>3}px{'✓' if self.tail_clean else '✗'} "
            f"装配右缘 {right} 距框 {room}px{'✓' if self.box_ok else '✗'}  "
            f"{self.elapsed_s:.2f}s"
        )

    @property
    def has_emoji(self) -> bool:
        """原图用户名段里是否含 emoji —— 与 :attr:`inkmask.EraseRegion.has_emoji` 同义。

        批量流程据此跳过整张图（``batch_run.SKIP_EMOJI``）。**单张模式不看它**：
        判据是「图上像素的事」，出不出片是调用方的决定。
        """
        return bool(self.name_emoji)

    @property
    def clean(self) -> bool:
        """是否「擦干净了」：残余墨迹不超过原墨迹的 5%，且框外零改动。"""
        return (
            self.ok
            and self.outside_changed == 0
            and self.ink_after <= max(8, int(self.ink_before * 0.05))
        )

    @property
    def tail_exact(self) -> bool:
        """尾块是否是**原图像素**（平移而非重画）。没测（-1）时算 False。"""
        return self.tail_diff_px == 0

    @property
    def tail_clean(self) -> bool:
        """尾巴是否**没有留在原地**（一个墨迹像素都没落下）。没测（-1）时算 False。"""
        return self.tail_leftover_px == 0

    @property
    def spec_ok(self) -> bool:
        """是否满足排布规格：两段相对距离不变 + 尾块是原像素 + 整条装配不越框。"""
        return self.ok and self.gap_ok and self.box_ok and self.tail_exact


# --------------------------------------------------------------------------
# 单图处理
# --------------------------------------------------------------------------


def resolve_box(
    filename: str,
    img_shape: tuple[int, ...],
    override: Optional[tuple[int, int, int, int]],
) -> tuple[int, int, int, int]:
    """决定这张图用哪个标注框。

    优先级：命令行 ``--bbox`` > ``config.BOX_TABLE`` > 报错。
    **故意不做自动检测** —— 本实验要量的是 inpainting，不能让框检测的误差混进结论。

    Args:
        filename:  文件名（含后缀），用于查表。
        img_shape: 图像 shape，用于校验。
        override:  命令行给的框，或 None。

    Returns:
        ``(x0, y0, x1, y1)``。

    Raises:
        inkmask.RegionNotFoundError: 表里没有、也没给 ``--bbox``。
    """
    box = override or config.BOX_TABLE.get(filename)
    if box is None:
        raise inkmask.RegionNotFoundError(
            f"{filename} 不在 config.BOX_TABLE 里，请用 --bbox x0,y0,x1,y1 手动指定"
        )
    h, w = img_shape[:2]
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
        raise inkmask.RegionNotFoundError(
            f"{filename} 的框 {box} 超出图像范围 {w}×{h}"
        )
    return box


def _derive_seed(*parts: str) -> int:
    """由若干字符串派生一个**跨进程稳定**的整数种子。

    用 ``md5`` 而不是内建 ``hash``：``hash`` 受 ``PYTHONHASHSEED`` 影响，同一个图名
    在不同进程会得到不同种子，「同一张图永远同一结果」这条确定性就废了。
    """
    digest = hashlib.md5("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def process_image(
    path: Path,
    new_name: str,
    *,
    bbox: Optional[tuple[int, int, int, int]] = None,
    method: str = config.INPAINT_METHOD,
    radius: int = config.INPAINT_RADIUS,
    full_rect: bool = False,
    erase_badge: bool = False,
    inset: Optional[int] = None,
    vinset: Optional[int] = None,
    fit: str = config.FIT_MODE,
    fill_slot: Optional[bool] = None,
    fill_ascii_weight: Optional[float] = None,
    font_size: Optional[int] = None,
    fit_text: Optional[str] = None,
    seed: Optional[int] = None,
    output_dir: Optional[Path] = None,
    save_output: bool = True,
    save_compare: bool = config.SAVE_COMPARISON,
    save_mask: bool = False,
    keep_result: bool = False,
    profile: "Optional[platforms.PlatformProfile]" = None,
    text_color: Optional[tuple[int, int, int]] = None,
    geom: Optional[dict] = None,
) -> Outcome:
    """处理一张图：定位 → 擦除 → 写字 → 存盘。

    ⚠️ **定位那一步是有平台差异的**：``profile=None`` 时走抖音的「按核心色饱和度认名字」
    （现状，零回归）；其余平台传对应 profile 走「按冒号定位」。详见 :mod:`platforms`。

    Args:
        path:         输入图片路径。
        new_name:     新用户名。emoji 会被剔掉（规格：新文本不含表情）。
        bbox:         手动指定的标注框，None 则查 ``config.BOX_TABLE``。
        method:       inpaint 方法（``telea`` / ``ns``）。
        radius:       inpaint 半径。
        full_rect:    True = 整段列区间整块擦，False = 只擦笔画。
        erase_badge:  **把等级标也擦掉**（默认 False）。True 且框里有等级标时：等级标
                      （连同它探出内缩框的那几条边）被**就地取背景**填平，新名字从
                      **胶囊头 + 固定间距**起写（``config.CAPSULE_HEAD_NAME_PAD``，规格：
                      假用户身上不留真用户的任何痕迹，且离胶囊头的距离跟别的行一样）、
                      尾巴跟着挪。⚠️ 「补满槽位」的基准**不变**，还是原名字自己多宽 ——
                      擦掉等级标后那块地归新名字，但假名字该多长还是多长
                      （见 ``inkmask.EraseRegion.name_span_width`` 的 ⚠️）。
                      只有 mock 线开它；扩展线**故意留着等级标**（那是要贴上去的东西）。
        inset:        框的内缩量（像素）；``None`` 取 :data:`config.BOX_INSET`（现状，零回归）。
                      ``config.BOX_INSET`` 是给**画在图上、含彩色描边**的框用的；而
                      ``manifest.json`` 的 ``roi`` 是**紧贴那一行字**的框、不含描边
                      ⇒ 传那个框要用更小的值（`expand_produce.ROI_INSET`），否则会从字里切掉几 px。
        vinset:       框的**纵向**内缩量；``None`` = 与 ``inset`` 相同（零回归）。
                      紧贴文字的 roi（``manifest.roi``）要传 0 —— 纵向内缩会把字的最上/最下
                      几行夹在擦除蒙版之外（成品留残墨）并把字高量小（见 `inkmask.inset_box`）。
        fit:          新名字放不下时的取舍：``height`` 保字高 / ``width`` 保宽度。
        fill_slot:    名字比原槽位窄时是否随机补满；None 取 ``config.FILL_SLOT_ENABLED``。
        fill_ascii_weight: 补位字符里 ASCII 的权重；传 0 = 只补中文（None 取 config 默认 0.30）。
        font_size:    **钉死**字号（None = 按本图量出来的字高定，现状）。
                      给值时新名字一律用这个字号 ⇒ 同一个名字跨多张素材大小一致；
                      也是「字号别参考名字里 emoji 的高度」的解法（见 `draw_text.plan_name`）。
        fit_text:     按字高定字号时**量哪串字**（只在 ``font_size=None`` 时有意义）。
                      ``None`` 取上游现状（中文样例「爱国者」）；扩展线传**要写的那串名字** ——
                      中文/数字/英文三类的墨高不一样，用中文字样去定数字/英文名的字号会矮两成
                      （见 `draw_text.plan_name` 的 ``fit_text`` 与 `expand/扩展张数.md` §23）。
        seed:         补字随机种子；None 由「图名 + 新名字」派生（逐图确定）。
        output_dir:   输出目录，None 用 ``config.DEFAULT_OUTPUT_DIR``。
        save_output:  是否存结果图。批量跑设为 False —— 见 :attr:`Outcome.result_image`。
        save_compare: 是否存「原图 / 结果」拼图。
        save_mask:    是否存蒙版图（排查用）。
        keep_result:  是否把结果图放进 ``Outcome.result_image`` 带回来（不落盘）。
        profile:      平台 profile（定位策略 + 阈值）。None = 抖音现状（``saturation_run``），
                      传 ``platforms.PROFILES["weibo"]`` 等则走 ``colon_anchor``。
        geom:         上游几何（``text_box`` / ``colon_box`` / ``badge_box`` / ``emojis``）；
                      给了就只收窄取样与改动的窗口、并按 ``colon_box`` 定名字/正文分界，
                      对不上时自动退回旧逻辑（见 ``inkmask._geom_clean``）。
        text_color:   **钉死**写上去的名字用什么颜色；None = 按这张底图量（现状）。
                      扩展线传**该用户自己图上出现次数最多的那个名字色**（``self_color``）——
                      用户 2026-10-11 报「用户名颜色有问题，建议固定用户颜色」：名字段里混进
                      emoji 时逐张量的色会被 emoji 的彩色拽偏，同一个用户在不同底图上颜色不一。
                      与徽章同一条口径：**同一个用户的东西，用他自己图上的**（见 `expand/扩展张数.md`）。

    Returns:
        :class:`Outcome`。**函数不抛异常** —— 单张失败记进 ``ok=False``，
        批量跑时才不会被一张坏图打断。
    """
    import time

    t0 = time.perf_counter()
    out_dir = output_dir or config.DEFAULT_OUTPUT_DIR
    if save_output or save_compare or save_mask:
        out_dir.mkdir(parents=True, exist_ok=True)

    try:
        img = np.array(Image.open(path).convert("RGB"))
    except Exception as exc:  # noqa: BLE001 - 读图失败原因很杂，统一上报
        LOGGER.error("%s 读取失败：%s", path.name, exc)
        return Outcome(file=path.name, ok=False, error=f"读取失败: {exc}")

    try:
        box = resolve_box(path.name, img.shape, bbox)
        region = inkmask.find_erase_region(
            img, box, full_rect=full_rect, profile=profile, erase_badge=erase_badge,
            inset=config.BOX_INSET if inset is None else int(inset),
            vinset=None if vinset is None else int(vinset),
            geom=geom,
        )
    except (inkmask.RegionNotFoundError, ValueError) as exc:
        LOGGER.error("%s 定位失败：%s", path.name, exc)
        return Outcome(file=path.name, ok=False, error=f"定位失败: {exc}")

    # 名字用什么颜色：调用方钉死了就用它（扩展线传该用户自己图上的众数色），
    # 否则按这张底图量（``inkmask`` 已经把名字段里的 emoji 从取色窗口里剔掉了）。
    name_color = tuple(int(v) for v in text_color) if text_color is not None else region.text_color

    if region.has_emoji:
        # **不改行为**：单张模式照常出片，但必须说一声 —— 批量流程会据此跳过整张图，
        # 两条路径的差别得在日志里看得见，否则「这张图没出片」会像静默失败。
        LOGGER.warning(
            "%s 原图用户名段里有 emoji（%s）—— 批量流程会跳过这张图（不出片）",
            path.name, region.emoji_note,
        )

    if region.multi_line:
        # **不改行为**，只是要说一声：这个框是检测器圈大了（装了两行），
        # 本次出片只动名字所在的那一条行带，第二行原样留着（bug-168）。
        LOGGER.warning(
            "%s 检测框里装了不止一行文字 —— 只按行带 %s 出片，带外一概不动",
            path.name, region.line_band,
        )

    # ---- 擦除 ----
    erased = inpaint_local.erase(
        img, region.mask, method=method, radius=radius, bg_color=region.bg_color
    )

    # ---- 硬校验：蒙版外一个像素都不许动 ----
    outside = inpaint_local.assert_outside_untouched(img, erased, region.mask)

    # ---- 擦掉等级标之后，把它那块地**就地取背景**铺平（mock 线，见 erase_badge） ----
    #   为什么不能用主擦除那一步顺手干完：药丸比内缩框高 1~2 行，那两行**本来就在
    #   蒙版之外**（`inkmask._badge_rect` 是在整幅图上量的）⇒ 只擦蒙版会留下上下两条
    #   药丸边。也正因为「填的是蒙版外」，这一步必须在上面那条硬校验**之后**。
    #   为什么不是 ``inpaint`` 而是整块铺平：药丸是**实心**的一大块（实测 89×35px），
    #   inpaint 的取样源会被它上下的绿色描边污染（洗净那一步把描边刷成框级 mode 色，
    #   于是整块被填成亮区那个色 —— 就是用户报的「背景色不对」）。见
    #   :func:`inpaint_local.fill_local` 与 :data:`config.LOCAL_FILL_SPAN_PX`。
    if region.badge_rect is not None:
        filled = inpaint_local.fill_local(
            erased,
            region.badge_rect,
            # 取哪几边的背景：药丸坐在**胶囊**里 ⇒ 只取右边（左边紧挨着的是胶囊头，
            # 取它会拿页面色往胶囊上涂）。见 ``EraseRegion.badge_fill_sides``。
            sides=region.badge_fill_sides,
            clamp=(region.box_rect[0], region.box_rect[2]),
            bg_color=region.bg_color,
        )
        # 这一步**故意**动蒙版外的像素（药丸比内缩框高、投影也不在蒙版里），所以它
        # 自己带一条同口径的校验：**改动只许落在它声明的那个矩形里**。`outside_changed`
        # 因此仍然是「声明范围之外零改动」——只是声明范围多了一块地（bug-166 的口径：
        # 说清楚参照物是什么，别用一个数糊两件事）。
        r_x0, r_y0, r_x1, r_y1 = region.badge_rect
        rect_mask = np.zeros(region.mask.shape, np.uint8)
        rect_mask[r_y0:r_y1, r_x0:r_x1] = 255
        outside += inpaint_local.assert_outside_untouched(erased, filled, rect_mask)
        erased = filled

    # ⚠️ 这两个数必须在**擦完、还没写字**的时候量。
    #    一开始写成「在最终图上量」，结果把**新写的名字**也算成了残余墨迹 ——
    #    于是 3.jpg 出现 1326 → 1497「越擦越多」的假象。那个数看着像 bug-144
    #    在本地路线上复现，其实纯粹是量错了对象。
    ink_before = inpaint_local.region_ink(img, region.erase_box, region.bg_color)
    ink_after = inpaint_local.region_ink(erased, region.erase_box, region.bg_color)

    # ---- ① 先排布：量好字号、裁进预算、去 emoji、补满槽位、算尾巴该挪多少 ----
    #    预算：clip 时是「名字 + 尾巴」合起来不越框的宽度；其余模式是槽位本身
    #    （width 靠缩字号、height 靠溢出，都不该动尾巴）。
    #
    #    rng 的种子：显式 seed 优先；否则由「图名 + 新名字」派生 —— 同一张图永远
    #    给同一个补字结果（本实验看家的确定性不能丢），换图/换名字才变。
    max_width = region.max_name_width if fit == "clip" else region.slot_width
    rng = random.Random(seed if seed is not None else _derive_seed(path.name, new_name))
    report = draw_text.plan_name(
        new_name,
        slot_width=region.slot_width,
        # 尾巴的位移基准：擦掉等级标时是「等级标左沿 → 原名字右缘」那一整块，
        # 名字右缘因此仍落回原名字的右缘（见 EraseRegion.name_span_width）。
        shift_base_width=region.name_span_width,
        glyph_height=region.glyph_height,
        max_width=max_width,
        fit=fit,
        fill_slot=fill_slot,
        rng=rng,
        fill_ascii_weight=fill_ascii_weight,
        font_size=font_size,
        fit_text=fit_text,
    )

    # ---- ② 再搬尾巴：把**原图的**尾巴像素（冒号 + 正文）整块平移 ----
    #    位移 = 新名字宽度 - 原槽位宽度：名字窄了就左移、宽了就右移，
    #    于是「名字↔冒号」「冒号↔正文」两段距离同时回到原值。
    #
    #    ⚠️ 顺序不能反（bug-153）：搬尾巴要把它原来占的那条竖带填成背景色，
    #       而那条带子在新名字变宽时**正好压在新名字身上**。先写字后搬尾巴
    #       = 拿背景色把名字啃掉一块。
    result, tail_shift, tail_rect = draw_text.shift_tail(
        erased,
        img,                     # 尾巴像素取自**原图**，不是擦过的那张
        tail_span=region.tail_span,
        tail_rows=region.tail_rows,
        bg_color=region.bg_color,
        shift=report.shift_px,
        box_rect=region.box_rect,
        box_right=region.box_right,
        tail_win=region.tail_win,   # 墨迹探出标注框时按它放宽（见 inkmask._tail_real_rows）
    )

    # ---- ③ 最后写字：左对齐写进原名字的位置，压在搬好的底子之上 ----
    result = draw_text.render_name(
        result,
        report,
        slot_left=region.name_left,
        glyph_top=region.glyph_top,
        text_color=name_color,
    )
    name_right = region.name_left + report.text_width - 1

    # ---- 校验③：尾块「平移」而不是「重画」----
    #    尾巴是原图像素整块搬过去的（shift_tail），所以把原图那块按位移取出来，
    #    应当与结果图对应位置**逐像素相同**。这一条抓的是「某个环节偷偷重画了
    #    尾巴」——重画一定会改墨迹宽度（bug-150：「：」画出 6px、原文 4px）。
    #    只比墨迹包围盒，不含 TAIL_MOVE_PAD 那一圈（那圈本来就是背景）。
    tail_diff = _block_diff(
        img,
        result,
        rows=(region.tail_rows[0], region.tail_rows[1] + 1),
        cols=(region.tail_span[0], region.tail_span[1] + 1),
        shift=tail_shift,
    )

    # ---- 校验④：尾巴有没有**留在原地**的墨迹 ----
    #    2026-10-11 用户「移动正文，没有完全移动」：``tail_rows`` 是在框外量的**真实**行范围，
    #    而搬家的那条竖带过去只到标注框 —— 探出框的那几行（正文里的 emoji）搬不走、原地留半截。
    #    ``tail_exact`` 也变红（留在原地的墨迹在**目的地**列上是"缺了一块"），但它分不清
    #    「留了半截」与「画上去的东西压住了尾巴」；这一条**只量留在原地的那些墨迹**，直指病灶。
    #    位移 0 = 尾巴本来就不用搬 ⇒ 记 0（不是"没量"）。
    tail_leftover = 0 if tail_shift == 0 else _tail_leftover(
        img,
        rows=region.tail_rows,
        cols=(region.tail_span[0], region.tail_span[1] + 1),
        move_rows=(tail_rect[1], tail_rect[3]),
        bg_color=region.bg_color,
    )

    # ---- 校验①：两段相对距离都没变 ----
    #    这一条**不能靠推理**（推理说它按构造就是 join_gap），要在**结果图上重量一次**。
    #    搜索范围刻意开到「平移后的尾巴右缘之外」：从名字右缘往右，第一个墨迹列
    #    就该是冒号左缘 —— 若某个环节把尾巴挪错了，这里立刻看得出来。
    colon_left_now = _first_ink_col(
        result,
        (region.colon_rows[0], region.colon_rows[1] + 1),
        region.bg_color,
        from_x=name_right + 1,
        to_x=min(result.shape[1], region.tail_span[1] + tail_shift + 2),
    )
    gap_now = (
        colon_left_now - name_right - 1 if colon_left_now is not None else 10**6
    )

    # ---- 校验②：整条装配没有越过标注框 ----
    #    「整体是否超过框，是则截断」—— 同样要在结果图上量，不能只看 max_width 的算式。
    #    判据用**「框右边界之外的像素有没有被改动」**，而不是「最右墨迹列」：
    #    框外面本来就还有别的东西（绿描边、框右侧的其他 UI），拿「最右墨迹」去比
    #    会把它们当成越界（第一版就是这么误报的，8/8 全 ✗）。
    overrun = int(
        (result[:, region.box_right :] != img[:, region.box_right :]).any(axis=2).sum()
    )
    box_ok = overrun == 0
    # 最右墨迹列只在框内量 —— 它是给人看的余量，不作判据。
    assembly_right = _last_ink_col(
        result,
        (region.tail_rows[0], region.tail_rows[1] + 1),
        region.bg_color,
        from_x=name_right + 1,
        to_x=region.box_right,
    )

    # ---- 存盘 ----
    out_path = out_dir / f"{path.stem}_replaced.png"
    if save_output:
        out_dir.mkdir(parents=True, exist_ok=True)
        Image.fromarray(result).save(out_path)

    if save_mask:
        Image.fromarray(region.mask).save(out_dir / f"{path.stem}_mask.png")

    if save_compare:
        # 裁到**整个标注框**（带绿边）而不是擦除区 —— 规格要验的是「名字↔冒号↔正文」
        # 这一整条装配，只裁蒙版的话冒号和正文都在画面外，等于没法肉眼验收。
        _save_comparison(img, result, box, out_dir / f"{path.stem}_compare.png")

    elapsed = time.perf_counter() - t0
    outcome = Outcome(
        file=path.name,
        ok=True,
        box=box,
        span=region.span,
        slot=region.slot,
        colon_span=region.colon_span,
        join_gap=region.join_gap,
        slot_width=region.slot_width,
        max_name_width=region.max_name_width,
        tail_diff_px=tail_diff,
        tail_leftover_px=tail_leftover,
        mask_pixels=region.mask_pixels,
        ink_before=ink_before,
        ink_after=ink_after,
        outside_changed=outside,
        tail_shift_px=tail_shift,
        gap_now=gap_now,
        gap_ok=(gap_now == region.join_gap),
        assembly_right=assembly_right,
        box_right=region.box_right,
        box_ok=box_ok,
        glyph_height=region.glyph_height,
        text_color=name_color,
        bg_color=region.bg_color,
        badge_found=region.badge_found,
        capsule_head=region.capsule_head,
        name_left=region.name_left,
        font_size=report.font_size,
        drawn_name=report.drawn_name,
        text_width=report.text_width,
        truncated_chars=report.truncated_chars,
        stripped_chars=report.stripped_chars,
        filled_chars=report.filled_chars,
        name_emoji=region.emoji_note,
        multi_line=region.multi_line,
        line_band=region.line_band,
        overflow_px=report.overflow_px,
        elapsed_s=elapsed,
        output=str(out_path) if save_output else "",
        result_image=result if keep_result else None,
    )
    LOGGER.info("%s", outcome.as_row().strip())
    return outcome


def _first_ink_col(
    img: np.ndarray,
    rows: tuple[int, int],
    bg_color: tuple[int, int, int],
    *,
    from_x: int,
    to_x: int,
) -> Optional[int]:
    """在 ``[from_x, to_x)`` 列、``rows`` 行范围内，找**最左边**那列墨迹。

    用来独立验证「冒号停在原位」：写完之后从**结果图**上重新量一次冒号墨迹的左缘，
    与定位阶段的 ``colon_span[0]`` 比。不能只靠推理说「它就是按原坐标画的」——
    抗锯齿、阈值、蒙版残留都可能让它偏。

    ⚠️ 搜索范围必须**独立于擦除区**。擦除区现在只覆盖名字（冒号根本不擦），
    拿它当搜索范围会一个墨迹像素都找不到 —— 那正是这个函数第一版报 ``None`` 的原因。

    Args:
        img:      ``(H, W, 3)`` 结果图。
        rows:     搜索的行范围 ``(y0, y1)``。
        bg_color: 背景色。
        from_x:   起始列（含），一般传槽位右缘 +1，跳过名字本身。
        to_x:     结束列（不含），一般传正文左边界。

    Returns:
        最左墨迹列的**绝对列号**；范围内没有墨迹时返回 None。
    """
    y0, y1 = rows
    if from_x >= to_x:
        return None
    strip = img[y0:y1, from_x:to_x]
    ink = inkmask.compute_ink_mask(
        strip, np.asarray(bg_color, dtype=np.uint8), config.BG_DIST_THRESH
    )
    cols = np.nonzero(ink.any(axis=0))[0]
    return int(cols.min()) + from_x if len(cols) else None


def _block_diff(
    img: np.ndarray,
    result: np.ndarray,
    *,
    rows: tuple[int, int],
    cols: tuple[int, int],
    shift: int,
) -> int:
    """把原图的一块按 ``shift`` 平移后，与结果图比逐像素差，返回不同的像素数。

    用于验证「尾巴是被**搬**过去的，不是**画**出来的」—— 见 ``Outcome.tail_diff_px``。

    `cols` 是**原图**里的列范围；结果图里的对应范围是 ``cols + shift``。
    目标范围越出图片或完全在画布外时返回 ``-1``（没法判，不假装是 0）。

    Args:
        img:    原图。
        result: 结果图。
        rows:   行范围 ``(y0, y1)``，y1 不含。
        cols:   原图列范围 ``(x0, x1)``，x1 不含。
        shift:  尾巴的**实际**位移（``shift_tail`` 夹过之后的那个）。

    Returns:
        差异像素数；无法比较时 -1。
    """
    y0, y1 = rows
    x0, x1 = cols
    h, w = result.shape[:2]
    if not (0 <= y0 < y1 <= h and 0 <= x0 < x1 <= w):
        return -1
    dx0, dx1 = x0 + shift, x1 + shift
    if dx0 < 0 or dx1 > w:
        return -1
    src = img[y0:y1, x0:x1]
    dst = result[y0:y1, dx0:dx1]
    if src.shape != dst.shape:
        return -1
    return int((src != dst).any(axis=2).sum())


def _tail_leftover(
    img: np.ndarray,
    *,
    rows: tuple[int, int],
    cols: tuple[int, int],
    move_rows: tuple[int, int],
    bg_color: tuple[int, int, int],
) -> int:
    """量**没被搬走**的尾巴墨迹：真实尾巴行范围内、落在搬家竖条**之外**的墨迹像素数。

    用户 2026-10-11「移动正文，没有完全移动」就是这一项不为 0：正文里的 emoji 比汉字高，
    上下各探出标注框几个像素（实测 `天-3/1.jpg`：框 1223~1253、👍 墨迹 1222~1257），
    而搬家的竖带过去被夹在标注框里 ⇒ 探出的那几行原地不动，成品上就是「半个 emoji 留在原地、
    另外半个搬到了左边」。``tail_rows`` 现在是在框外量的真实范围（`inkmask._tail_real_rows`），
    所以这一条能看见它。

    Args:
        img:        **原图**（搬完之后的图上留没留东西，比的是原图那块墨迹有没有被覆盖到）。
        rows:       尾巴的**真实**行范围 ``(y0, y1)``，闭区间。
        cols:       尾巴的列范围 ``(x0, x1)``，x1 不含（与 ``tail_span`` 同源）。
        move_rows:  搬家竖条的**行**范围 ``(sy0, sy1)``，sy1 不含（``shift_tail`` 返回的那个矩形）。
        bg_color:   背景色。

    Returns:
        悬在竖条外、没被搬走的墨迹像素数。量不了（范围为空）时返回 0。
    """
    h, w = img.shape[:2]
    y0, y1 = max(0, rows[0]), min(h, rows[1] + 1)
    x0, x1 = max(0, cols[0]), min(w, cols[1])
    my0, my1 = move_rows
    if x0 >= x1 or y0 >= y1:
        return 0
    total = 0
    for a, b in ((y0, min(y1, my0)), (max(y0, my1), y1)):   # 竖条上方、竖条下方
        if a < b:
            total += int(
                inkmask.compute_ink_mask(
                    img[a:b, x0:x1], np.asarray(bg_color, dtype=np.uint8),
                    config.BG_DIST_THRESH,
                ).sum()
            )
    return total


def _last_ink_col(
    img: np.ndarray,
    rows: tuple[int, int],
    bg_color: tuple[int, int, int],
    *,
    from_x: int,
    to_x: int,
) -> Optional[int]:
    """在 ``[from_x, to_x)`` 列、``rows`` 行范围内，找**最右边**那列墨迹。

    与 :func:`_first_ink_col` 同源，用来验证「整条装配没有越过标注框」——
    量的是结果图上**实际**的最右墨迹列，而不是 ``max_name_width`` 那条算式。

    ⚠️ ``to_x`` 必须**故意越过框右边界**（调用方给了 ``box_right + 40``）。
    若把范围卡在框内，真越界也会被裁成「刚好齐平」，这条校验就成了摆设。

    Args:
        img:      ``(H, W, 3)`` 结果图。
        rows:     搜索的行范围 ``(y0, y1)``。
        bg_color: 背景色。
        from_x:   起始列（含），一般传新名字右缘 +1。
        to_x:     结束列（不含），要**越过框**。

    Returns:
        最右墨迹列的**绝对列号**；范围内没有墨迹时返回 None。
    """
    y0, y1 = rows
    if from_x >= to_x:
        return None
    strip = img[y0:y1, from_x:to_x]
    ink = inkmask.compute_ink_mask(
        strip, np.asarray(bg_color, dtype=np.uint8), config.BG_DIST_THRESH
    )
    cols = np.nonzero(ink.any(axis=0))[0]
    return int(cols.max()) + from_x if len(cols) else None


def _save_comparison(
    before: np.ndarray,
    after: np.ndarray,
    box: tuple[int, int, int, int],
    dest: Path,
    pad: int = config.COMPARISON_PAD,
) -> None:
    """存一张「原图 / 结果」上下拼接图，带上下文，方便肉眼验收。

    ⚠️ 裁剪范围取**标注框**，不取擦除区。擦除区现在只覆盖名字那块，
    用它裁的话冒号、正文、绿框全在画面外 —— 而规格要验的恰恰是
    「名字↔冒号↔正文」这一整条装配有没有整体平移、有没有越出绿框。

    Args:
        before: 原图。
        after:  结果图。
        box:    标注框（含描边），决定裁剪范围。
        dest:   输出路径。
        pad:    围绕标注框额外截取的像素。
    """
    h, w = before.shape[:2]
    x0, y0, x1, y1 = box
    cx0, cy0 = max(0, x0 - pad), max(0, y0 - pad)
    cx1, cy1 = min(w, x1 + pad), min(h, y1 + pad)

    a = Image.fromarray(before[cy0:cy1, cx0:cx1])
    b = Image.fromarray(after[cy0:cy1, cx0:cx1])
    gap = 10
    canvas = Image.new("RGB", (a.width, a.height * 2 + gap), (255, 0, 0))
    canvas.paste(a, (0, 0))
    canvas.paste(b, (0, a.height + gap))
    canvas.save(dest)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def parse_bbox(raw: str) -> tuple[int, int, int, int]:
    """解析 ``--bbox`` 的 ``x0,y0,x1,y1``。

    Args:
        raw: 形如 ``"561,1285,1092,1336"``。

    Returns:
        四元组。

    Raises:
        argparse.ArgumentTypeError: 格式不对。
    """
    parts = [p.strip() for p in raw.replace("，", ",").split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(f"--bbox 需要 4 个数字，收到 {len(parts)} 个：{raw!r}")
    try:
        x0, y0, x1, y1 = (int(p) for p in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--bbox 里有非整数：{raw!r}") from exc
    if x1 <= x0 or y1 <= y0:
        raise argparse.ArgumentTypeError(f"--bbox 需要 x1>x0 且 y1>y0：{raw!r}")
    return x0, y0, x1, y1


def collect_inputs(target: Path) -> list[Path]:
    """收集要处理的图片：给文件就用它，给目录就扫一遍。

    Args:
        target: 文件或目录路径。

    Returns:
        排序后的图片路径列表。

    Raises:
        FileNotFoundError: 路径不存在。
        ValueError: 目录里没有可识别的图片。
    """
    if not target.exists():
        raise FileNotFoundError(f"输入路径不存在：{target}")
    if target.is_file():
        return [target]
    files = sorted(
        p for p in target.iterdir()
        if p.is_file() and p.suffix.lower() in config.IMAGE_SUFFIXES
    )
    if not files:
        raise ValueError(f"目录 {target} 里没有图片（认这些后缀：{config.IMAGE_SUFFIXES}）")
    return files


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    p = argparse.ArgumentParser(
        prog="replace_username",
        description="mock_scale 独立实验：本地确定性 inpainting 擦除用户名 + Pillow 写新名",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python replace_username.py --new SuperUser\n"
            "  python replace_username.py --new 张三 --input data/2.jpg\n"
            "  python replace_username.py --new SuperUser --bbox 561,1285,1092,1336 --keep-mask\n"
        ),
    )
    p.add_argument("--new", required=True, help="新用户名（必填）")
    p.add_argument(
        "--old", default=None,
        help="旧用户名。**本路线不使用它** —— 擦除是按版面几何定位的，不读文字。"
             "仅记录进日志，便于人工核对",
    )
    p.add_argument(
        "--input", type=Path, default=config.DEFAULT_INPUT_DIR,
        help=f"输入图片或目录（默认 {config.DEFAULT_INPUT_DIR}）",
    )
    p.add_argument(
        "--output", type=Path, default=config.DEFAULT_OUTPUT_DIR,
        help=f"输出目录（默认 {config.DEFAULT_OUTPUT_DIR}）",
    )
    p.add_argument("--bbox", type=parse_bbox, default=None, help="手动指定标注框 x0,y0,x1,y1")
    p.add_argument(
        "--method", choices=sorted(inpaint_local.METHODS), default=config.INPAINT_METHOD,
        help=f"擦除策略（默认 {config.INPAINT_METHOD}）",
    )
    p.add_argument("--radius", type=int, default=config.INPAINT_RADIUS, help="inpaint 半径")
    p.add_argument(
        "--full-rect", action="store_true",
        help="整段列区间整块擦（默认只擦笔画像素）",
    )
    p.add_argument(
        "--erase-badge", action="store_true",
        help="**等级标（✦14 那种药丸）连名字一起擦掉**，新名字从药丸左沿起写"
             "（默认不擦：本文件是两条线共用的单图工具，擦不擦由调用方定 —— "
             "**mock 用户线默认擦**，见 core/batch_produce.py 的 --no-erase-badge）",
    )
    p.add_argument(
        "--fit", choices=("clip", "width", "height"), default=config.FIT_MODE,
        help="新名字比原名字宽时：clip=保字号砍字 / width=保名字缩字号 / "
             f"height=任由溢出（默认 {config.FIT_MODE}）",
    )
    p.add_argument(
        "--no-fill", dest="fill_slot", action="store_false", default=None,
        help="名字比原槽位**窄**时不随机补满（复现 2026-09-29 前的旧行为："
             "尾巴左移、右边留背景色空隙）。默认补满，见 config.FILL_SLOT_ENABLED",
    )
    p.add_argument(
        "--seed", type=int, default=None,
        help="补齐用的随机种子。不传则由「图名 + 新名字」派生 —— 同一张图永远"
             "同一结果；传了则整个批次用同一个种子",
    )
    p.add_argument(
        "--platform", default="auto",
        help="平台 profile（auto = 从输入路径判定；认不出走 generic）。"
             "抖音走「按颜色认名字」，视频号/微博走「按冒号定位」——见 platforms.py",
    )
    p.add_argument("--keep-mask", action="store_true", help="额外保存蒙版图")
    p.add_argument("--no-compare", action="store_true", help="不保存对比拼图")
    p.add_argument("-v", "--verbose", action="store_true", help="输出 DEBUG 日志")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    """命令行入口。

    Args:
        argv: 参数列表，None 时取 ``sys.argv[1:]``。

    Returns:
        退出码：0 = 全部成功，1 = 有失败，2 = 参数/环境错误。
    """
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else config.LOG_LEVEL,
        format=config.LOG_FORMAT,
        datefmt=config.LOG_DATEFMT,
    )

    if args.old:
        # 本路线不读文字，--old 只是留个核对用的锚点，明确说清楚别让人误以为它生效了。
        LOGGER.info("--old=%r 仅作记录，本路线的擦除范围来自版面几何、不依赖该值", args.old)

    try:
        files = collect_inputs(args.input)
    except (FileNotFoundError, ValueError) as exc:
        LOGGER.error("%s", exc)
        return 2

    profile = platforms.profile_for(
        platforms.detect_platform_from_path(args.input) if args.platform == "auto" else args.platform
    )
    fill_on = config.FILL_SLOT_ENABLED if args.fill_slot is None else args.fill_slot
    LOGGER.info(
        "待处理 %d 张 · 新名字「%s」· 平台[%s/%s] · 擦除[%s r=%d] · 排布[fit=%s]%s · 补满槽位[%s]%s",
        len(files), args.new, profile.key, profile.name_strategy, args.method, args.radius,
        args.fit,
        " · 整块擦" if args.full_rect else "",
        "开" if fill_on else "关",
        " · 连等级标一起擦" if args.erase_badge else "",
    )

    outcomes: list[Outcome] = []
    for f in files:
        outcomes.append(
            process_image(
                f,
                args.new,
                bbox=args.bbox,
                method=args.method,
                radius=args.radius,
                full_rect=args.full_rect,
                erase_badge=args.erase_badge,
                fit=args.fit,
                fill_slot=args.fill_slot,
                seed=args.seed,
                profile=profile,
                output_dir=args.output,
                save_compare=not args.no_compare,
                save_mask=args.keep_mask,
            )
        )

    # ---- 汇总 ----
    oks = [o for o in outcomes if o.ok]
    clean = [o for o in oks if o.clean]
    LOGGER.info("=" * 78)
    LOGGER.info("结果汇总")
    for o in outcomes:
        LOGGER.info("%s", o.as_row())
    LOGGER.info("-" * 78)
    if oks:
        total_before = sum(o.ink_before for o in oks)
        total_after = sum(o.ink_after for o in oks)
        LOGGER.info(
            "成功 %d/%d · 擦干净 %d/%d · 墨迹合计 %d → %d（清除 %.1f%%）",
            len(oks), len(outcomes), len(clean), len(oks),
            total_before, total_after,
            (1 - total_after / total_before) * 100 if total_before else 0.0,
        )
        LOGGER.info(
            "蒙版外改动合计 %d 像素（必须为 0）· 「冒号距名字」实测 %s（必须恒等于原图）"
            "· 「尾块是原像素」%s · 「装配不越框」%s · 尾巴位移 %s px · 平均耗时 %.2fs/张",
            sum(o.outside_changed for o in oks),
            "全部命中" if all(o.gap_ok for o in oks) else "❌ 有偏差",
            (
                f"合计差 {sum(o.tail_diff_px for o in oks)} 像素（必须为 0）"
                if all(o.tail_exact for o in oks)
                else "❌ 有重画/压盖"
            ),
            "全部命中" if all(o.box_ok for o in oks) else "❌ 有越界",
            sorted({o.tail_shift_px for o in oks}),
            sum(o.elapsed_s for o in oks) / len(oks),
        )
        if any(o.truncated_chars for o in oks):
            LOGGER.info(
                "被截断的图：%s",
                ", ".join(f"{o.file}→「{o.drawn_name}」(-{o.truncated_chars}字)"
                         for o in oks if o.truncated_chars),
            )
        if any(o.filled_chars for o in oks):
            LOGGER.info(
                "补满槽位的图 %d/%d（总长不变）：%s",
                sum(1 for o in oks if o.filled_chars), len(oks),
                ", ".join(
                    f"{o.file}→「{o.drawn_name}」(+{o.filled_chars}字，尾巴移{o.tail_shift_px:+d}px)"
                    for o in oks if o.filled_chars
                ),
            )
        if any(o.stripped_chars for o in oks):
            LOGGER.info(
                "剔除 emoji 的图：%s",
                ", ".join(f"{o.file}({o.stripped_chars}个)" for o in oks if o.stripped_chars),
            )
    LOGGER.info("=" * 78)

    return 0 if len(oks) == len(outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
