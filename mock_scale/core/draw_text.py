"""用 Pillow 把新用户名写回擦干净的背景上。

为什么写字这一步**必须**留给本地而不是模型：

* **冒号**。原图是「昵称 + 全角冒号 + 正文」。指令式模型实测会把「昵称+冒号」当成
  一个整体替换掉，冒号**一定**消失 —— work/test 里 9 次调用、三种措辞都没保住，
  换一张图又一次独立复现（bug-146）。本地写字则「冒号丢不掉」，因为是我们自己画的。
* **字形**。让模型写中文，等于每次都在赌它的字形生成；本地用 Noto Sans CJK **SC**
  字脸，字形是确定的。
* **尺寸**。字号由「原用户名实测字高」反推，逐图自适应，不靠模型理解「保持原有大小」。

对齐策略：**按墨迹包围盒对齐**，不是按 Pillow 的排版原点。做法是把文字先渲染到临时
画布上量出真实墨迹框，再平移过去 —— 这样「让新字顶部与原字顶部齐平」是**可验证**的，
而不是「大概齐」。
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import config
import inpaint_local

LOGGER = logging.getLogger(__name__)


class FontNotFoundError(RuntimeError):
    """找不到任何可用的中文字体。"""


@dataclass
class DrawReport:
    """**名字的排布方案** —— 量好了、裁好了，但还没画上去。

    这个对象是 :func:`plan_name` 与 :func:`render_name` 之间的契约，也是
    调用方的决策依据：它给出 ``shift_px``，而 :func:`shift_tail` 需要的正是这个数。

    ⚠️ 顺序是**先搬尾巴、后写名字**，不能反。见 :func:`shift_tail` 的说明。

    Attributes:
        font:            最终字体对象（渲染时用）。
        font_size:       最终使用的字号。
        drawn_name:      实际要写的文字（``fit="clip"`` 时可能是被截短的）。
        text_width:      新用户名的墨迹宽度。
        max_width:       这次允许的最大墨迹宽度（= 截断预算）。
        overflow_px:     超出预算的像素数，0 = 没有挤压。
        truncated_chars: 因截断而丢掉的字符数，0 = 没截。
        shift_px:        尾巴应当平移的像素数 = ``text_width - 位移基准``，
                         即「新名字比它要顶替的那块地宽/窄多少」（基准默认 = ``slot_width``，
                         擦掉等级标时是「等级标左沿 → 原名字右缘」那一整块，见
                         :func:`plan_name` 的 ``shift_base_width``）。尾巴照这个数平移，
                         名字↔冒号、冒号↔正文的距离就都回到原值。
        off_x / off_y:   墨迹包围盒相对绘制原点的偏移，渲染时用来对齐。
        font_path:       实际用到的字体文件。
    """

    font: ImageFont.FreeTypeFont
    font_size: int
    drawn_name: str
    text_width: int
    max_width: int
    overflow_px: int
    truncated_chars: int
    shift_px: int
    off_x: int
    off_y: int
    font_path: str
    stripped_chars: int = 0
    """从新名字里剔掉的 emoji 字符数（``strip_emoji``）。0 = 没剔。"""
    filled_chars: int = 0
    """为补满槽位随机追加的字符数（``fill_to_slot``）。0 = 没补。"""

    @property
    def ok(self) -> bool:
        """是否既没溢出也没截断 —— 名字原样落进预算。"""
        return self.overflow_px <= 0 and self.truncated_chars == 0


@lru_cache(maxsize=8)
def _load_font_cached(path: str, size: int, face_index: int) -> ImageFont.FreeTypeFont:
    """按缓存加载字体（Pillow 重复 truetype() 会很慢，字号固定后缓存住）。"""
    return ImageFont.truetype(path, size, index=face_index)


def resolve_font_path() -> str:
    """在候选列表里挑第一个真实存在的字体文件。

    Returns:
        字体文件路径。

    Raises:
        FontNotFoundError: 一个候选都不存在。
    """
    for p in config.FONT_PATHS:
        if Path(p).exists():
            return p
    raise FontNotFoundError(
        f"候选字体一个都不存在：{list(config.FONT_PATHS)}\n"
        "Linux 上装：sudo apt install fonts-noto-cjk"
    )


def load_font(size: int) -> ImageFont.FreeTypeFont:
    """加载指定字号的字体。

    Args:
        size: Pillow 的 em 字号（不是墨迹高度）。

    Returns:
        字体对象。

    Raises:
        FontNotFoundError: 找不到字体。
    """
    return _load_font_cached(resolve_font_path(), size, config.FONT_FACE_INDEX)


def measure_ink(text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int, int, int]:
    """量出文字**真实墨迹**的包围盒 ``(w, h, offset_x, offset_y)``。

    ``font.getbbox()`` 给的是排版框（含大量行距留白），不能直接用来对齐 ——
    必须真的把字画出来、再量哪些像素被点亮。

    Args:
        text: 要量的文字（可以是多字）。
        font: 字体对象。

    Returns:
        ``(墨迹宽, 墨迹高, 墨迹左边缘相对绘制原点的偏移, 墨迹上边缘相对绘制原点的偏移)``。
        文字全为空白时返回 ``(0, 0, 0, 0)``。
    """
    pad = 8
    canvas = Image.new("L", (font.size * (len(text) + 2) + 2 * pad, font.size * 3 + 2 * pad), 0)
    ImageDraw.Draw(canvas).text((pad, pad), text, font=font, fill=255)
    arr = np.array(canvas)
    ys, xs = np.nonzero(arr > 0)
    if len(xs) == 0:
        return 0, 0, 0, 0
    return (
        int(xs.max() - xs.min() + 1),
        int(ys.max() - ys.min() + 1),
        int(xs.min() - pad),
        int(ys.min() - pad),
    )


def fit_font_size(target_height: int, sample: str = "爱国者") -> ImageFont.FreeTypeFont:
    """二分搜索出「墨迹高度最接近 target_height」的字号。

    换字体会改变「字号 → 墨迹高度」的映射，所以这里**不查表、不乘系数**，
    直接在真实字体上量 —— ``config.FONT_SIZE_RATIO`` 只用来定搜索起点。

    Args:
        target_height: 目标墨迹高度（像素），取自原用户名的实测字高。
        sample:        用于测量的样例文字。默认用「爱国者」，与语料中的原用户名一致。

    Returns:
        字号最接近的字体对象。
    """
    lo, hi = config.FONT_SIZE_MIN, config.FONT_SIZE_MAX
    best = load_font(lo)
    best_err = 10**9
    while lo <= hi:
        mid = (lo + hi) // 2
        font = load_font(mid)
        h = measure_ink(sample, font)[1]
        err = abs(h - target_height)
        if err < best_err:
            best_err, best = err, font
        if h < target_height:
            lo = mid + 1
        elif h > target_height:
            hi = mid - 1
        else:
            break
    LOGGER.debug(
        "字高 %dpx → 字号 %d（实测墨迹高 %dpx）",
        target_height, best.size, measure_ink(sample, best)[1],
    )
    return best


# --------------------------------------------------------------------------
# 新名字的文字策略：去 emoji +「总长不够就随机补齐槽位」
# --------------------------------------------------------------------------

# emoji 的码位很散（表情、象形、国旗、键帽、变体选择符……），这里按 Unicode 的
# 几个主块 + 少量散字收成一个字符类。**只扫 Unicode 表，不查库、不引包** ——
# 本实验刻意不引 OCR/字体以外的依赖，规则自己写清、可复核。
_EMOJI_RE = re.compile(
    "["
    "\U0001f000-\U0001faff"  # 麻将/扑克/各类表情与象形符号（含国旗 1F1E6–1F1FF）
    "\U00002600-\U000027bf"  # 杂项符号、装饰符号（★☆♡☀☎…）
    "\U00002b00-\U00002bff"  # 杂项符号与箭头（⬛⭐⭕…）
    "\U0000fe00-\U0000fe0f"  # 变体选择符（emoji presentation）
    "\U0000200d"             # 零宽连接符（👨‍👩‍👧 的粘合剂）
    "\U000020e3"             # 组合用围框键帽（1⃣ 的方框）
    "\U0000231a-\U0000231b"  # ⌚⌛
    "\U000023e9-\U000023f3"  # ⏩…⏳
    "\U000023f8-\U000023fa"  # ⏸⏹⏺
    "\U000025aa-\U000025ab"  # ▪▫
    "\U000025b6\U000025c0\U000025fb-\U000025fe"  # ▶◀◻…◾
    "\U00002122\U00002139"   # ™ℹ
    "\U00002194-\U00002199\U000021a9-\U000021aa"  # ↔…⤴⤵
    "\U00003030\U0000303d\U00003297\U00003299"    # 〰〽㊗㊙
    "\U000000a9\U000000ae"   # ©®
    "]"
)


def strip_emoji(text: str) -> tuple[str, str]:
    """剔掉 emoji，返回 ``(清理后的文本, 被剔掉的字符)``。

    两个理由同向：规格要求「新写的文本里不要表情」，而 emoji 在 Pillow + Noto Sans
    CJK 下**本来就画不出来**（没有彩色字形，只会落成豆腐块）。所以直接剔。

    ⚠️ **如实回报剔了什么** —— 调用方要把它记进日志与 ``Outcome``，不能静默把用户
    给的名字改掉。原图里的 emoji 不受影响（那是语料，不是新写的文本）。

    Args:
        text: 原始文本。

    Returns:
        ``(不含 emoji 的文本, 被剔除的字符拼成的串)``。
    """
    return _EMOJI_RE.sub("", text), "".join(_EMOJI_RE.findall(text))


def _random_fill_char(rng: random.Random, ascii_weight: float | None = None) -> str:
    """随机取一个补位字符：按 ASCII 权重决定中文还是 ASCII。

    Args:
        rng:          随机源。
        ascii_weight: 取 ASCII 的概率；None = 用 :data:`config.FILL_SLOT_ASCII_WEIGHT`。
                      传 0 = **只补中文**（生产场景要「不够就用随机中文补满」时用）。
    """
    if rng.random() < (config.FILL_SLOT_ASCII_WEIGHT if ascii_weight is None else ascii_weight):
        return rng.choice(config.FILL_SLOT_ASCII_POOL)
    return rng.choice(config.FILL_SLOT_CJK_POOL)


def _best_fill_step(
    name: str,
    font: ImageFont.FreeTypeFont,
    *,
    target_width: int,
    max_width: int,
    rng: random.Random,
    ascii_weight: float | None = None,
) -> tuple[str, int]:
    """往前看一步：挑一个最接近 target 的后缀接到 ``name`` 后面。

    候选后缀来自两处，合起来既随机又不近视：

    * ``config.FILL_SLOT_ALPHABET`` 的**1~2 字穷举** —— 一个按宽度铺开的小字母表，
      保证「两个 ASCII 落在槽位里」这类组合不会被随机抽签错过（详见该常量的说明）；
      ``ascii_weight`` ≤ 0（只补中文）时先剔掉其中的 ASCII，只剩汉字；
    * ``FILL_SLOT_TRIES`` 组**随机**单字/双字，保持「随机中文/英文数字」的口径。

    基线是「这一步什么都不做」，所以没有候选更近时返回原样，调用方据此收工。
    候选一律不许越过 ``max_width``（越过就夹不回框内）。**并列最优的候选里随机取一个**
    —— 精度靠「谁最接近」定，名字的随机性靠并列里抽签定（否则小字母表每次都赢，
    ``--seed`` 就形同虚设）。
    """
    cur_w = measure_ink(name, font)[0]
    best_score = abs(cur_w - target_width)
    winners: list[tuple[str, int]] = []

    suffixes: list[str] = []
    alpha = config.FILL_SLOT_ALPHABET
    if ascii_weight is not None and ascii_weight <= 0:
        # 调用方要求「只补中文」（batch_produce 写死 ``fill_ascii_weight=0.0``）时，
        # 确定性小字母表里那两个 ASCII（i / 8 / W）也必须剔掉 —— 否则成品上会冒出
        # 「大猫i」「阿鹿8」「小风W」这种尾巴（2026-10-28 实测，见 bug-372）。
        alpha = "".join(ch for ch in alpha if not ch.isascii())
    for a in alpha:
        suffixes.append(a)
        suffixes.extend(a + b for b in alpha)
    for _ in range(config.FILL_SLOT_TRIES):
        suffixes.append(_random_fill_char(rng, ascii_weight))
        suffixes.append(_random_fill_char(rng, ascii_weight) + _random_fill_char(rng, ascii_weight))

    for suffix in suffixes:
        cand = name + suffix
        cand_w = measure_ink(cand, font)[0]
        if cand_w > max_width:
            continue
        score = abs(cand_w - target_width)
        if score < best_score:
            best_score, winners = score, [(cand, cand_w)]
        elif score == best_score:
            winners.append((cand, cand_w))

    if not winners:
        return name, cur_w
    return rng.choice(winners)


def fill_to_slot(
    name: str,
    font: ImageFont.FreeTypeFont,
    *,
    target_width: int,
    max_width: int,
    rng: random.Random,
    ascii_weight: float | None = None,
) -> tuple[str, int]:
    """在 ``name`` 后面随机补字，把墨迹宽度补到**最接近** ``target_width``。

    补的字从「常用中文字池 / ASCII 字母数字池」里随机取，**天然不含 emoji**。
    宽度只增不减，所以逐字爬山：每一步往前看一步（:func:`_best_fill_step`），
    直到「再补一个字也只会离 target 更远」为止。

    字宽是离散的（中文≈整字宽、ASCII≈半宽），凑不到像素级相等；靠每步的候选挑选，
    「保持总体长度不变」通常能逼近到 **1~2px 的尾巴位移**。``max_width`` 是硬上限
    （超过它尾巴就夹不回框内），于是 ``slot_width == max_width`` 的图（正文已顶到
    框）只会补到 ≤ 槽位、绝不会把尾巴顶出框。

    Args:
        name:          起始名字（已去 emoji、已按 ``fit`` 裁过）。
        font:          最终字体（补出来的字与它同字号）。
        target_width:  目标墨迹宽度，取原槽位 ``slot_width``。
        max_width:     允许的最大墨迹宽度。
        rng:           随机源（由调用方决定种子，便于复现）。

    Returns:
        ``(补齐后的名字, 它的墨迹宽度)``。
    """
    cur, cur_w = name, measure_ink(name, font)[0]
    while len(cur) - len(name) < config.FILL_SLOT_MAX_ADD and cur_w < target_width:
        nxt, nxt_w = _best_fill_step(
            cur, font, target_width=target_width, max_width=max_width, rng=rng,
            ascii_weight=ascii_weight,
        )
        if nxt is cur:  # 没有候选更近 ⇒ 收工
            break
        cur, cur_w = nxt, nxt_w
    return cur, cur_w


def fit_name(
    new_name: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    fit: str,
) -> tuple[str, ImageFont.FreeTypeFont, int]:
    """把新用户名塞进宽度为 ``max_width`` 的预算里。

    三条规则（``fit``）只在「名字比预算宽」时才有区别：

    ``clip``
        **字号不动，从右往左砍字**。字高与原来完全一致，代价是名字被截短
        （``SuperUser`` 在 2.jpg 上会变成 ``SuperUser`` 或 ``Super``，
        取决于正文给不给得出余量）—— **信息是真的丢了**，
        所以 ``DrawReport.truncated_chars`` 会如实报出来。
    ``width``
        **缩字号直到塞得下**。名字完整，代价是新名字比原来小一号。
    ``height``
        **什么都不做**，让它溢出（会压到冒号 / 正文），由调用方按 ``overflow_px`` 处置。

    Args:
        new_name:  新用户名。
        font:      起始字体（字号已按原字高定好）。
        max_width: 可用宽度（像素）—— ``clip`` 传 ``EraseRegion.max_name_width``，
                   ``width`` 传 ``slot_width``。
        fit:       ``clip`` / ``width`` / ``height``。

    Returns:
        ``(实际要写的文字, 最终字体, 被砍掉的字符数)``。
    """
    number = new_name
    truncated = 0

    if fit == "clip":
        # 至少留一个字 —— 一个字都塞不下时不再砍，交给 overflow_px 上报。
        while len(number) > 1 and measure_ink(number, font)[0] > max_width:
            number = number[:-1]
            truncated += 1
    elif fit == "width":
        while (
            measure_ink(number, font)[0] > max_width
            and font.size > config.FONT_SIZE_MIN
        ):
            font = load_font(font.size - 1)

    return number, font, truncated


def plan_name(
    new_name: str,
    *,
    slot_width: int,
    glyph_height: int,
    max_width: int,
    fit: str = config.FIT_MODE,
    fill_slot: bool | None = None,
    rng: random.Random | None = None,
    fill_ascii_weight: float | None = None,
    font_size: int | None = None,
    fit_text: str | None = None,
    shift_base_width: int | None = None,
) -> DrawReport:
    """**只算不画**：定字号、裁到预算内、补满槽位、算出要写的字和尾巴该挪多少。

    拆出这一步是为了**定顺序**。尾巴的平移量 ``shift_px`` 依赖名字的最终宽度，
    而名字的渲染又必须在尾巴搬完之后（见 :func:`shift_tail` 的 ⚠️）。
    于是管线是 ``plan_name → shift_tail → render_name``，
    而不是「先画名字、再想办法把尾巴挪到它右边」。

    文本的两道加工（用户 2026-09-29 规格）都在这里做，顺序固定：
      1. **去 emoji** —— 新写的文本里不许有表情（``config.STRIP_EMOJI``）；
      2. **补满槽位** —— 名字比槽位窄时随机补字（``config.FILL_SLOT_ENABLED``），
         使 ``shift_px ≈ 0``、尾巴不动、总长不变。

    Args:
        new_name:     新用户名。
        slot_width:   原用户名墨迹的宽度，也是「补满」的目标宽度。
        glyph_height: 原用户名墨迹高度，作为字号目标。
        max_width:    允许的最大墨迹宽度（截断预算、也是补字的硬上限）。
        fit:          ``clip`` 保字号截断 / ``width`` 保名字缩字号 / ``height`` 任由溢出。
        fill_slot:    是否补满槽位；None 取 ``config.FILL_SLOT_ENABLED``。
        rng:          补字用的随机源；None 时新建一个（不确定）。
        fill_ascii_weight: 补位字符里 ASCII 的权重；0 = 只补中文。
        font_size:    **钉死**字号（None = 按本图量出来的字高 ``glyph_height`` 定）。
                      生产场景下同一个名字要跨多张素材大小一致，而单张图量出来的字高
                      可能被名字里的 **emoji** 撑大（它们比汉字高）⇒ 由调用方给一个
                      **语料的文字高度**（如 28）把它钉住。
        fit_text:     按墨高定字号时**量哪串字**（None = 默认样例「爱国者」）。
                      ⚠️ **中文/数字/英文三类的墨高不一样**（`expand/扩展张数.md` §23）：同一个
                      字号下，我们字体的**中文墨高 = em**，而**数字只有 0.75em、英文 x 高 ~0.6em**
                      （平台反过来 —— 平台把数字画得几乎和中文同高）。默认样例是中文 ⇒ 名字若是
                      纯数字/纯英文，按它定出来的字号会让那串字**比平台矮 18%~25%**；
                      传**要写的那串字本身**（`plan_name` 用去 emoji 之后的 `clean`），
                      三类就各自对到目标高度（实测：纯中文字号不变，纯数字 20→27、纯英文 21→25）。
        shift_base_width: 尾巴位移的**基准宽度**（``shift_px = 最终宽度 − 它``）。None = 用
                      ``slot_width``（现状）。擦掉等级标时两者**必须分开**（2026-10-11
                      「把等级标一起替换成用户名」）：
                      · 名字从**等级标左沿**起写 ⇒ 它的右缘要落回**原名字的右缘**，
                        基准因此是「等级标左沿 → 原名字右缘」那一整块；
                      · 而「补满槽位」还是按 ``slot_width``（原名字自己多宽）——
                        基准一并改成那块地，一个 5 字假名就会被当成「比槽位窄」而糊上
                        一串随机字，把等级标让出来的地方又填满、尾巴一动不动。

    Returns:
        :class:`DrawReport`。

    Raises:
        ValueError: new_name 为空、去 emoji 后为空，或 fit 取值不认识。
    """
    if not new_name.strip():
        raise ValueError("新用户名不能为空")
    if fit not in ("clip", "width", "height"):
        raise ValueError(f"fit 只能是 'clip' / 'width' / 'height'，收到 {fit!r}")

    fill_slot = config.FILL_SLOT_ENABLED if fill_slot is None else fill_slot
    rng = rng or random.Random()
    # 尾巴的位移基准：默认就是槽位本身（名字左缘 → 原名字右缘）。擦掉等级标时
    # 名字从等级标左沿起写，基准得换成「等级标左沿 → 原名字右缘」那一整块。
    shift_base = slot_width if shift_base_width is None else int(shift_base_width)

    clean = new_name
    stripped = ""
    if config.STRIP_EMOJI:
        clean, stripped = strip_emoji(new_name)
        if stripped:
            LOGGER.warning(
                "新名字里的 emoji 已剔除：%r（共 %d 个字符）—— 规格要求新写的文本"
                "不含表情；原图里的 emoji 不受影响",
                stripped, len(stripped),
            )
        if not clean.strip():
            raise ValueError(f"新用户名去掉 emoji 后为空：{new_name!r}")

    # 字号：默认由**这一张图**量出来的字高决定；``font_size`` 给定时**钉死**用它。
    #   为什么要能钉死（2026-09-30）：字高是从**名字槽位**的墨迹行范围量的，而槽位里
    #   除了文字还可能有**比文字高的非文字元素**（emoji：实测 `00口谋財0口渡凡尘-4/3`
    #   里 💕 高 35、3️⃣ 高 31，而同一行的汉字只有 26~28、同行正文 27）⇒ 字号被撑到 35，
    #   新名字比原文字大一圈。同语料的**文字**高度是恒定的（这份语料 27~28px），
    #   所以「按一张图的量」会让同一个名字在不同素材上大小不一 —— 那正是要避免的观感。
    if font_size is None:
        # 量哪串字定字号：默认样例是中文（= 中文墨高对齐目标）；名字自带数字/英文时
        # 由调用方传 `fit_text`，让**那串字自己**去对目标高度（见 `fit_text` 那段）。
        # 去 emoji：emoji 比汉字高，量它会把字号撑大（与本函数上面那段同一个理由）。
        sample = strip_emoji(fit_text)[0] if fit_text else ""
        font = fit_font_size(glyph_height, sample=sample if sample.strip() else "爱国者")
    else:
        size = max(config.FONT_SIZE_MIN, min(config.FONT_SIZE_MAX, int(font_size)))
        if size != int(font_size):
            LOGGER.warning(
                "给定的字号 %d 超出 [%d, %d]，已夹到 %d",
                int(font_size), config.FONT_SIZE_MIN, config.FONT_SIZE_MAX, size,
            )
        font = load_font(size)
        LOGGER.debug("字号按给定值钉死：%d（本图量出来的字高是 %dpx）", size, glyph_height)
    number, font, truncated = fit_name(clean, font, max_width, fit)
    w, _h, off_x, off_y = measure_ink(number, font)

    filled = 0
    if fill_slot and w < slot_width:
        before_len = len(number)
        number, w = fill_to_slot(
            number, font, target_width=slot_width, max_width=max_width, rng=rng,
            ascii_weight=fill_ascii_weight,
        )
        filled = len(number) - before_len

    overflow = max(0, w - max_width)

    report = DrawReport(
        font=font,
        font_size=font.size,
        drawn_name=number,
        text_width=w,
        max_width=max_width,
        overflow_px=overflow,
        truncated_chars=truncated,
        shift_px=w - shift_base,
        off_x=off_x,
        off_y=off_y,
        font_path=resolve_font_path(),
        stripped_chars=len(stripped),
        filled_chars=filled,
    )

    if truncated:
        LOGGER.warning(
            "新名字「%s」放不下（预算 %dpx，其中尾巴只让得出 %dpx）："
            "按 fit=clip 截成「%s」，丢掉 %d 个字；"
            "要保住完整名字请用 --fit width（缩字号）",
            clean, max_width, max_width - shift_base, number, truncated,
        )
    elif overflow:
        LOGGER.warning(
            "新名字「%s」超出预算：需要 %dpx、预算 %dpx，超出 %dpx（fit=%s，字号 %d）"
            "—— 尾巴会被夹在框内，名字可能压到冒号",
            clean, w, max_width, overflow, fit, font.size,
        )
    else:
        LOGGER.debug(
            "排布「%s」字号 %d 宽 %dpx（预算 %dpx = 槽 %d + 尾巴余量 %d）"
            "%s⇒ 尾巴要平移 %+dpx",
            number, font.size, w, max_width, shift_base, max_width - shift_base,
            f"（补了 {filled} 个字把槽位填满）" if filled else "",
            report.shift_px,
        )
    return report


def render_name(
    img_rgb: np.ndarray,
    report: DrawReport,
    *,
    slot_left: int,
    glyph_top: int,
    text_color: tuple[int, int, int],
) -> np.ndarray:
    """按 :func:`plan_name` 的方案把名字画上去，**左对齐、按墨迹包围盒对齐**。

    ⚠️ 必须在 :func:`shift_tail` **之后**调用。理由见 :func:`shift_tail`。

    Args:
        img_rgb:    ``(H, W, 3)`` 图（尾巴已经搬好）。
        report:     :func:`plan_name` 的结果。
        slot_left:  原用户名墨迹的左缘，新字从这里起写。
        glyph_top:  原用户名墨迹的上边缘（绝对行号）。
        text_color: 新用户名的颜色（沿用原色）。

    Returns:
        新数组。
    """
    canvas = Image.fromarray(img_rgb)
    ImageDraw.Draw(canvas).text(
        (slot_left - report.off_x, glyph_top - report.off_y),
        report.drawn_name,
        font=report.font,
        fill=text_color,
    )
    return np.array(canvas)


def draw_username(
    img_rgb: np.ndarray,
    new_name: str,
    *,
    slot: tuple[int, int],
    max_width: int,
    glyph_top: int,
    glyph_height: int,
    text_color: tuple[int, int, int],
    fit: str = config.FIT_MODE,
    fill_slot: bool | None = None,
    rng: random.Random | None = None,
) -> tuple[np.ndarray, DrawReport]:
    """``plan_name`` + ``render_name`` 的便捷组合 —— **不动尾巴**。

    只在「不需要挪尾巴」的场合直接用它（比如 ``fit="width"`` 只缩字号、
    或单独想看看字写出来什么样）。生产流程走的是三段式：

    ``plan_name → shift_tail → render_name``

    ⚠️ 别用「先调这个、再调 :func:`shift_tail`」的写法。名字一旦先画上去，
    搬尾巴时为抹掉旧位置而填的背景色就会**啃掉名字的右半边**（当新名字比原名字宽时，
    腾出的那条空白正好压在名字身上）。顺序反了就是 bug —— 实测漏了 51px 的冒号偏移。

    Args:
        img_rgb:      ``(H, W, 3)`` RGB 图（此时槽位已是干净背景，冒号还在）。
        new_name:     新用户名。
        slot:         原用户名墨迹的列区间 ``(left, right)``，新字左对齐从这里起写。
        max_width:    允许的最大墨迹宽度。
        glyph_top:    原用户名墨迹的上边缘（绝对行号），新字与之对齐。
        glyph_height: 原用户名墨迹高度，作为字号目标。
        text_color:   新用户名的颜色（沿用原色）。
        fit:          ``clip`` / ``width`` / ``height``。
        fill_slot:    是否补满槽位；None 取 ``config.FILL_SLOT_ENABLED``。
        rng:          补字用的随机源。

    Returns:
        ``(新图, DrawReport)``。
    """
    report = plan_name(
        new_name,
        slot_width=slot[1] - slot[0] + 1,
        glyph_height=glyph_height,
        max_width=max_width,
        fit=fit,
        fill_slot=fill_slot,
        rng=rng,
    )
    return render_name(
        img_rgb, report, slot_left=slot[0], glyph_top=glyph_top, text_color=text_color
    ), report


def shift_tail(
    img_rgb: np.ndarray,
    source_rgb: np.ndarray,
    *,
    tail_span: tuple[int, int],
    tail_rows: tuple[int, int],
    bg_color: tuple[int, int, int],
    shift: int,
    box_rect: tuple[int, int, int, int],
    box_right: int,
    tail_win: tuple[int, int] | None = None,
    pad: int = config.TAIL_MOVE_PAD,
) -> tuple[np.ndarray, int, tuple[int, int, int, int]]:
    """把「冒号 + 正文」这一整条**尾巴整体平移** ``shift`` 像素。

    规格是「『：』距离名字的距离不变」**加上**「正文也是相对距离不变」——
    两句合起来说的是：``[名字][间距][：][间距][正文]`` 是**一整块**，
    名字一短，后面全跟着往左挪，所有间距原样保持。

    ⚠️ **必须在写名字之前调用。** 这是踩过坑的（bug-153）。
    名字比原名字宽时，尾巴要右移，于是它**原来占的那条竖带**
    （``[冒号左缘-pad, +shift)``）得填背景色 —— 而那条竖带**正好压在
    新名字的右半身上**。先写名字再搬尾巴，就等于拿背景色把名字啃掉一块。

    走过的弯路是「给本函数加个 ``name_right`` 参数，把左边界切到名字右缘之后」——
    看着能保名字，实际**把冒号从补丁里切掉了**：名字一变宽，``name_right`` 就越过
    冒号左缘，补丁从 796 列开始取，而冒号在 745~748 —— 冒号根本没被搬走，
    只是原地被填成了背景，结果 2.jpg 的冒号跑到了 852（该在 801），偏移 51px。
    正解不是「保护名字」，而是**换顺序**。

    ⚠️ **平移，不是重画。** 重画过一次，栽在 bug-150：位置能对齐到 0px，
    但 Pillow 渲染的「：」墨迹宽 **6px**、原图只有 **4px**，多出来的像素漏到右边。
    平移则是**同一批像素搬家**，字形天然一致。

    ⚠️ **位移要夹在框内。** 名字变长时尾巴右移，越界就会**盖到绿色标注框上**。
    调用方已经用 ``max_width`` 把名字限制住了，这里再夹一道，保证「框」这条线
    是硬的 —— 宁可位置差一点并如实上报，也不能把框涂了。

    前提：**背景在平移距离内是平的**。本语料底色 std ≈ 5，成立；背景有渐变/纹理时，
    搬过去的矩形会带上原位置的背景残影 —— 那时得改成「只搬墨迹像素 + 重新羽化边缘」。

    ⚠️ 腾出来的那条竖带**从旁边就地取背景**（:func:`inpaint_local.fill_local`），
    不是拿一个全框 mode 色涂掉 —— 为什么，见 :data:`config.LOCAL_FILL_SPAN_PX`。

    Args:
        img_rgb:    ``(H, W, 3)`` **已擦干净、但还没写名字**的图。
        source_rgb: ``(H, W, 3)`` **原图** —— 尾巴像素从它身上取。
        tail_span:  尾巴墨迹的列区间 ``(left, right)``。
        tail_rows:  尾巴墨迹的行区间 ``(top, bottom)``。
        bg_color:   背景色，用于填掉尾巴腾出来的位置。
        shift:      期望位移（正 = 右移）= ``新名字宽 - 原槽位宽``。
        box_rect:   内缩后的标注框 ``(x0, y0, x1, y1)`` —— 搬家矩形夹在它里面，
                    一个描边像素都不碰。
        box_right:  标注框内缩后的右边界，尾巴不许越过它。
        pad:        平移矩形在墨迹外额外扩的像素，别把抗锯齿边切掉。

    Returns:
        ``(新图, 实际位移)``。实际位移可能与 ``shift`` 不同（被夹住了）。
    """
    h, w = img_rgb.shape[:2]
    bx0, by0, bx1, by1 = box_rect
    ty0, ty1 = tail_rows
    # ⚠️ 四条边都**夹在内缩框里面**（bug-154）。``pad`` 那一圈本来是为了别切掉
    #    抗锯齿过渡带，但它会伸出内缩框、盖到**绿色描边的抗锯齿边**上 ——
    #    而这一步既「填背景色」又「贴原像素」，等于把描边的边线一截截搬走。
    #    实测 4/5/6/7.jpg 各有 124~549 个描边像素被改动。夹住之后那一圈本来也不存在
    #    （文字与描边之间没有余量），不会切掉任何有用的像素。
    sy0, sy1 = max(by0, ty0 - pad), min(by1, ty1 + 1 + pad)
    # ⚠️ **墨迹探出框时按 ``tail_win`` 放宽**（2026-10-11 用户「移动正文，没有完全移动」）：
    #    正文里的 emoji 比汉字高（实测 `天-3/1.jpg`：框 1223~1253、墨迹 1222~1257），
    #    只搬框内那一条 ⇒ 搬过去的 emoji 上下被削平、**原位置还留半截**。
    #    放宽只在那**确实探出的那一侧**做：没探出的那侧照旧夹在框里 ——
    #    「改动不出框」这条不变量不该被无谓地打破（出片报告里的 `diff_in_box` 看的就是它）。
    if tail_win and tail_win[1] > tail_win[0]:
        wy0, wy1 = tail_win
        if ty0 < by0:
            sy0 = max(wy0, ty0 - pad)
        if ty1 > by1 - 1:
            sy1 = min(wy1 + 1, ty1 + 1 + pad)
    sx0 = max(bx0, tail_span[0] - pad)
    sx1 = min(w, bx1, box_right, tail_span[1] + 1 + pad)
    if sx0 >= sx1 or sy0 >= sy1:
        return img_rgb, 0, (sx0, sy0, sx1, sy1)

    # 夹紧：不许越出图像、不许越过框右边界
    shift = max(-sx0, min(shift, w - sx1, box_right - sx1))
    if shift == 0:
        return img_rgb, 0, (sx0, sy0, sx1, sy1)

    patch = source_rgb[sy0:sy1, sx0:sx1].copy()
    # ---- 腾出来的那条竖带**就地取背景** ----
    # ⚠️ 不能拿全框一个 mode 色平涂（2026-10-11 用户「背景填补超过 roi 框，并且背景色
    #    不对」）：本语料的框内底色**沿 x 会跳**（实测 `于亚洲3-8/5.jpg`：x≤1800 处
    #    ≈(33,33,32)、x≥1850 处 ≈(51,53,48)，差 18 级），而 ``bg_color`` 是全框的 mode
    #    ⇒ 尾巴正好搬在暗区时，那条带子被刷成亮区那个色，凭空多出一块浅灰板；
    #    它还盖掉了下方的**绿色标注线**（那几行本来跟着尾巴一起搬，填的时候也得续上）。
    #    改成从**背对尾巴新位置**的那一侧、**同样这几行**上量背景（另一侧马上要贴上
    #    刚搬过来的墨迹，不是背景）。填满整个矩形无妨 —— 紧接着的贴图会把非竖带的部分
    #    全部盖回去，于是「填了多少」与「要填多少」天然一致。
    out = inpaint_local.fill_local(
        img_rgb,
        (sx0, sy0, sx1, sy1),
        sides=("left",) if shift > 0 else ("right",),
        clamp=(bx0, bx1),
        bg_color=bg_color,
    )
    out[sy0:sy1, sx0 + shift : sx1 + shift] = patch               # 新位贴原像素
    return out, shift, (sx0, sy0, sx1, sy1)
