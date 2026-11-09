"""平台 profile —— 把「语料差异」收在一个地方。

**为什么需要它。** ``inkmask.find_erase_region`` 的规则 4（按**核心色饱和度**认名字）
是**为抖音语料量出来的**。三份语料的实测名字段彩度：

============  ==============================  ============
平台           名字段核心色                      彩度
============  ==============================  ============
抖音           青蓝 ``RGB(165,218,239)``        **74**
视频号         白 ``RGB(142,143,144)``          **2**
微博           灰 ``RGB(188,188,188)``          **0**
============  ==============================  ============

``USERNAME_SAT_MIN = 60`` 在后两个平台上**不是「偏一点」，是 0 命中**（实测定位 0/17、0/8）。
而排除定位之后的一切（洗源 / 只贴蒙版内 / plan→shift→render / 判据 / 报告）已实测**平台无关**
（抖音 464/464 干净、1392/1392 合规）。所以平台差异**只在定位一层**，本模块就管这一层。

**两条设计决定：**

1. **锚点从「颜色」换成「冒号」。** 冒号是三份语料里唯一都稳定的特征 ——
   实测：宽 4~6px、填充率 15~33%、**墨迹只占行高的下半截**（代码里早有这句注释：
   「冒号只有下半截」）。颜色降级为 tie-break，不再作必要条件。
2. **平台从路径取，不猜。** 三批语料的路径形状一致：
   ``<活动名>/<平台名>/all_users_正面_3张以上/<昵称-张数>/<n>.jpg``。
   取不到路径线索时再用 :func:`detect_platform_from_stats`（探针量出来的特征判定）。

⚠️ **这是新增件，不改变现有行为**：抖音仍走 ``saturation_run``（规则 4 原样保留），
``colon_anchor`` 只对新平台启用。验收线是「抖音 650 张逐图零回归」。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

# --------------------------------------------------------------------------
# profile
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlatformProfile:
    """一个平台的全部**语料相关**参数。

    Attributes:
        key:                内部标识（``douyin`` / ``wechat_channels`` / ``weibo`` / ``generic``）。
        label:              给人看的名字。
        name_strategy:      ``saturation_run`` = 规则 4（按彩度连续段，抖音现状）；
                            ``colon_anchor`` = 按冒号定位（跨平台）。
        username_sat_min:   仅 ``saturation_run`` 用（彩度下限）。
        colon_w_range:      冒号段宽范围（含端点，px）。
        colon_fill_max:     冒号填充率上限。冒号是两点、填充率天然低，这道闸只为挡**实心块**
                            （徽章 0.74）。**实测**：三份语料的真冒号 0.15~**0.37**（微博那张最高的
                             撞到了原来拍的 0.35 ⇒ 5领跑者5-3/3.jpg 漏掉）；汉字 0.37~0.49 会撞上限，
                             但它们被宽度闸（28px vs 5px）挡在外面。⇒ 取 0.45。
        colon_h_ratio:      冒号段高 / 行带高 的允许区间 —— 冒号约占**六成**行高，
                            只要「比它矮的」别放进来。**实测**：真冒号 0.59~0.63；
                            正文里的逗号/句点只有 **0.26**（4.jpg 就是被这个假候选抢走的）；
                            汉字 0.93~1.00。⇒ 取 0.40~0.80。
        colon_lower_ratio:  冒号墨迹**上缘**必须落在行带的下部：``(top-band_top)/band_h >= 它``。
                            **实测**：真冒号上缘在 **0.28~0.30**；汉字 0.00~0.11。
                            ⇒ 取 0.20。⚠️ 原来拍的 0.35 会把**真冒号**挡掉 ——
                            4/6/9.jpg 三张当时就是这么漏的，而且报的是「没有候选」，
                            看不出是被门槛挡的。
        colon_body_min:     冒号右边紧跟的正文合计宽度下限（正文越长越像真分界）。
        colon_gap_min:      **候选左边那段的缝**下限（px），0 = 关（现状：只按分数挑）。
                            开成 >0 ⇒ :func:`pick_colon` 走「两层挑法」：先只在
                            「缝 ≥ 它」的候选里挑，一个都没有才退回全体。
                            为什么需要它：分数里的 `body`（右侧正文长度）**偏向更靠左的候选**
                            —— 名字**内部**的窄碎段右边还跟着半个名字，正文比真冒号右边的长，
                            分数自然更高 ⇒ 碎段抢走真冒号（bug-358）。而「名字↔冒号的缝」
                            是结构性的（实测 5~6px），字内缝是 1~4px，这一层与分数无关。
        colon_right_gap_min: **候选右边那段的缝**下限（px），0 = 关（现状）。
                            开成 >0 ⇒ :func:`pick_colon` 在 `colon_gap_min` 那层**之前**
                            先走一层「右缝 ≥ 它」：一个都没有才落到下一层 / 全体。
                            为什么右缝比左缝干净（bug-367，视频号网页版实测 352 张）：
                            真冒号右边紧跟的是**正文的排版空格**（实测聚在 17~18px），
                            字内碎段右边紧跟的是**它自己那一笔**（1~4px）；而左边
                            「名字↔冒号」在网页版上只有 4~6px，和字内缝 1~4px 两簇**挨着**，
                            单靠左缝挑会误伤 14 张（`colon_gap_min` 的实测口径见
                            :mod:`expand_produce.视频号app.config.json` 的注）。
                            右缝这一层对**已经挑对了的**候选是恒等的（真冒号右缝都 ≥ 它），
                            所以对开着的 profile 也是零回归。
        badge_min_width:    徽章最小宽度。
        badge_fill_ratio:   徽章最小填充率。
        bg_mode_share_min:  框内众数色占比下限 —— 低于它说明「背景不是近纯色」，该拒跑。
        skip_pure_en:       ``SKIP_PURE_EN`` 这条**规格**是否启用（⚠️ 是规格不是阈值：
                            抖音 9.2% 命中、微博 60%）。
        frame_colors:       标注框描边颜色档（``box_detect`` 用）。
        bg_quantize:        背景众数的**量化步长**（0 = 精确众数，现状）。视频压缩噪声会把
                            同一块底色的像素打散到许多相邻色值上，精确众数可能被**文字**抢走
                            （快手 App 实测：白字 100 个同色 vs 暗底每种 ~90 个 ⇒ 背景估成
                            白色、墨迹占比 0.90）。>0 时先按 ``floor(c / bg_quantize)`` 分桶再取
                            众数桶、返回桶内均值；只对有需要的平台开，默认 0 = 逐字节不变。
        geom_row_from_colon:
                            上游有 ``colon_box`` 时，把取样窗口的**纵向**收到冒号那一行
                            （冒号高度 ± 0.8 倍，含行内升降部），并把带外的 ``badge_box``
                            / 表情丢弃。快手的交付框是**融合块**（ROI 常装 2~6 行），
                            不按冒号行收窄时列投影会把相邻行并成一段 ⇒ 冒号认不出来。
                            默认 False = 现状（仍然按 ``text_box`` 的纵向范围）。
        needs_geom:         这份语料的交付框是**多行融合框**，定位必须靠上游几何
                            （``comments.jsonl`` 的 ``colon_box``）才能选中目标行。
                            各入口看到它会自动从交付树重建 ``geom``（``upstream_geom``）；
                            默认 False = 不提要求、行为不变。
        note:               一句话说明这个 profile 的来历 / 待办。
    """

    key: str
    label: str
    name_strategy: str
    username_sat_min: int
    colon_w_range: tuple[int, int]
    colon_fill_max: float
    colon_h_ratio: tuple[float, float]
    colon_lower_ratio: float
    colon_body_min: int
    colon_gap_min: int
    colon_right_gap_min: int
    badge_min_width: int
    badge_fill_ratio: float
    bg_mode_share_min: float
    skip_pure_en: bool
    frame_colors: tuple[str, ...]
    bg_quantize: int = 0
    geom_row_from_colon: bool = False
    needs_geom: bool = False
    note: str = ""


PROFILES: dict[str, PlatformProfile] = {
    "douyin": PlatformProfile(
        key="douyin",
        label="抖音（主语料）",
        name_strategy="saturation_run",
        username_sat_min=60,
        colon_w_range=(4, 8),
        colon_fill_max=0.45,
        colon_h_ratio=(0.40, 0.80),
        colon_lower_ratio=0.20,
        colon_body_min=60,
        colon_gap_min=0,
        colon_right_gap_min=0,
        badge_min_width=40,
        badge_fill_ratio=0.60,
        bg_mode_share_min=0.15,
        skip_pure_en=True,
        frame_colors=("green", "purple"),
        note="现状路径，一行不改；650 张的结论都是它跑出来的",
    ),
    "wechat_channels": PlatformProfile(
        key="wechat_channels",
        label="视频号",
        name_strategy="colon_anchor",
        username_sat_min=60,  # colon_anchor 不用它，留着只为日志口径一致
        # 网页版的真冒号实测只有 **2~3px**（黄标那张圆圆的真冒号 173~175=3px），
        # 从抖音抄来的 (4,8) 把它整个挡在外面 ⇒ 一张候选都不剩，只能挑到名字里的碎段。
        # 上限放宽到 10：网页版的窄碎段 5~9px 落进来，靠 colon_right_gap_min 那层挡。
        colon_w_range=(2, 10),
        colon_fill_max=0.45,
        colon_h_ratio=(0.40, 0.80),
        colon_lower_ratio=0.20,
        colon_body_min=60,
        colon_gap_min=0,
        # 交付线实测 352 张（produce_20261012_205559，非 filler 行）：
        # 真冒号右缝聚在 **17~18px**（{18:114, 17:26, 9:15}），字内碎段右缝 **1~4px**
        # （{2:135, 1:22}）⇒ 5 是这两簇之间的大空档。左缝在网页版上分不开：
        # 「名字↔冒号」只有 4~6px（{4:73, 5:63, 6:21}），与碎段那簇贴着，照抄 App 的
        # 左缝 5 会误伤 14 张（见 bug-367 的 A/B）。
        colon_right_gap_min=5,
        badge_min_width=40,
        badge_fill_ratio=0.60,
        bg_mode_share_min=0.15,
        skip_pure_en=True,
        frame_colors=("green", "purple"),
        note=("昵称白（彩度 2）、正文里有彩色贴纸；实测 17 张全 LOC_NONE。"
              "窄缝问题在**交付线**上首次实测（bug-367，2026-10-12）：352 张里 173 张挑到了"
              "名字里的碎段（黄标那张圆圆会发光-5/2 就是，真冒号 3px 被宽度闸挡掉）；"
              "开宽度 (2,10) + 右缝 ≥5 后 173 → 18，174 张改善、0 张回归，"
              "抖音线走 saturation_run，不受影响。"),
    ),
    "wechat_app": PlatformProfile(
        key="wechat_app",
        label="视频号 App 端（720x1600）",
        name_strategy="colon_anchor",
        username_sat_min=60,
        # App 的真冒号只有 **2~3px**（小糯米 122~123=2px；开心/球球/韩=3px），
        # generic 的下限 3 恰好把 2px 那类整个挡掉 ⇒ 一张候选都不剩。
        # 上限仍留 10：App 的碎段 5~9px 落在里面，靠 colon_gap_min 那层挡（见 profile 的注）。
        colon_w_range=(2, 10),
        colon_fill_max=0.40,
        colon_h_ratio=(0.35, 0.85),
        colon_lower_ratio=0.18,
        colon_body_min=50,
        # 名字↔冒号的缝实测 **5~6px**（小糯米 5、开心 5、球球 5、韩 5、A-幸运小福星 6），
        # 字内缝实测 **2~4px**（小糯米 3、开心 2、A-幸运小福星 3）⇒ 5 是这两簇之间的空档。
        colon_gap_min=5,
        colon_right_gap_min=0,  # App 语料上右缝没标定过；留 0 ⇒ 只走上面的左缝那层，行为一字不变
        badge_min_width=40,
        badge_fill_ratio=0.60,
        bg_mode_share_min=0.12,
        skip_pure_en=True,
        frame_colors=("green", "purple"),
        note=("App 端自己的一条线（bug-358）。只动了**两处**，其余与 generic 同档——"
              "因为在 App 语料上真冒号与「名字内部的窄碎段」在填充率/高比/上缘上**分不开**"
              "（真冒号 15~17% / 0.48~0.52 / 0.32~0.38，碎段 16~24% / 0.42~0.58 / 0.20~0.33，全重叠）；"
              "能分开的只有「缝」这一条结构性的证据。"
              "⚠️ 宽度在 App 上也分不开：真等级丸 49px 与「名字前两字粘成一段」48px 同宽"
              "（A-幸运小福星-4 实测），别拿宽度当判据；扇形丸 57px 也不是等级标。"),
    ),
    "kuaishou_app": PlatformProfile(
        key="kuaishou_app",
        label="快手 App 端（590x1280）",
        name_strategy="colon_anchor",
        username_sat_min=60,          # colon_anchor 不用它，留着只为日志口径一致
        # 实测（2026-11-02，CTCC 8.8 交付树 750 张，只统计**目标行**的选中候选）：
        # 真冒号列段宽 **3px**（p5~p75 全是 3），p95~max 为 10（点与正文之间的抗锯齿粘连）；
        # 汉字/emoji 段宽 20~78。⇒ 上限取 12 留 2px 余量。
        colon_w_range=(2, 12),
        # 实测填充率 0.106~0.400（p95 = 0.289）⇒ 0.45 与抖音同档，汉字 0.37+ 被宽度闸挡。
        colon_fill_max=0.45,
        # 实测 段高/行带 = 0.391~0.720 ⇒ (0.35, 0.80)。下限比抖音略松：Kuaishou 的字号
        # 小（行距 45、字带 22~25px），冒号两点合计只有 12~13px，行带量窄时比值会贴近 0.39。
        colon_h_ratio=(0.35, 0.80),
        # 实测冒号上缘 (top-band_top)/band_h = 0.192~0.391 ⇒ 0.15 留余量。
        colon_lower_ratio=0.15,
        # 实测右侧正文连续宽 60~354px（p5 = 82）⇒ 50，与 generic 同档。
        colon_body_min=50,
        # 实测「名字↔冒号」「冒号↔正文」的缝两簇没有分开（4~18px 连续分布），
        # 开任何一道缝闸都会误伤；750 张上按分数直接挑已 750/750 与上游 colon_box 对位。
        colon_gap_min=0,
        colon_right_gap_min=0,
        # 药丸实测宽 45~87px（榜N / 粉丝团 / 等级），填充率 0.616~0.903 ⇒ 与其它平台同档。
        badge_min_width=40,
        badge_fill_ratio=0.60,
        # 背景众数色占比（量化步长 32 之后）实测最低 0.241、中位 0.520 ⇒ 下限 0.15。
        bg_mode_share_min=0.15,
        # 纯英文昵称跳过率 10%（75/750），与抖音 9.2% 同档 ⇒ 沿用这条规格。
        skip_pure_en=True,
        frame_colors=("green", "purple"),   # 交付框实测绿色 RGB(17~61, 213~242, 21~66)
        bg_quantize=32,
        geom_row_from_colon=True,
        needs_geom=True,
        note=("快手 App（竖屏 590x1280，CTCC 8.8 交付树 750 张实测，2026-11-02）。"
              "三条与其它平台不同的硬事实：① 彩色交付框是**多行融合框**（ROI 是"
              "comments 里那个组块，常装 2~6 行），目标行不一定是第一行（750 张里 91 张"
              "不是）⇒ 必须用上游 comments.jsonl 的 colon_box 定行（needs_geom）；"
              "② 评论底是叠在视频上的深色半透明层，精确众数会被白字抢走 ⇒ bg_quantize=32；"
              "③ 药丸簇内联在行首（榜N / 粉丝团 / 等级），没有固定徽章列。"
              "验证：geom + 冒号行窗口 + q32 在 750/750 张上定位成功且冒号位置与 "
              "colon_box 逐张对位。"),
    ),
    "weibo": PlatformProfile(
        key="weibo",
        label="微博",
        name_strategy="colon_anchor",
        username_sat_min=60,
        colon_w_range=(4, 8),
        colon_fill_max=0.45,
        colon_h_ratio=(0.40, 0.80),
        colon_lower_ratio=0.20,
        colon_body_min=60,
        colon_gap_min=0,
        colon_right_gap_min=0,
        badge_min_width=40,
        badge_fill_ratio=0.60,
        bg_mode_share_min=0.15,
        # ⚠️ 规格决定，不是阈值：昵称灰 188 vs 正文白 254（只差亮度不差色相）。
        #    这条开关的默认值**待用户确认**；先关掉，否则 60% 的图会被静默跳过。
        skip_pure_en=False,
        frame_colors=("green", "purple"),
        note="昵称灰（彩度 0）、正文纯白；实测 8 张全 LOC_NONE。skip_pure_en=false 待确认",
    ),
    "generic": PlatformProfile(
        key="generic",
        label="未知平台（冒号锚点，阈值放宽）",
        name_strategy="colon_anchor",
        username_sat_min=60,
        colon_w_range=(3, 10),
        colon_fill_max=0.40,
        colon_h_ratio=(0.35, 0.85),
        colon_lower_ratio=0.18,
        colon_body_min=50,
        colon_gap_min=0,
        colon_right_gap_min=0,
        badge_min_width=40,
        badge_fill_ratio=0.60,
        bg_mode_share_min=0.12,
        skip_pure_en=True,
        frame_colors=("green", "purple"),
        note="探测不出平台时的兜底；阈值比抖音松一档",
    ),
}


NAME_CHROMA_SPREAD: int = 15
"""「名字文字的彩度带」半宽 —— 用来把**名字段里的 emoji** 挑出来。

为什么不用一个固定色带：``config.NAME_EMOJI_SAT_MIN/MAX = 60/92`` 是**在抖音语料上标定的**
（名字青蓝、彩度 70~82）。视频号/微博的名字是白/灰（彩度 **0~5**），一律落在那个带**外面**
⇒ 名字本身会被整片报成 emoji，而批量流程据此 **跳过整张图** ⇒ 那两个平台一张都出不了片。

所以：非抖音平台按**名字段自己的彩度中位数**现算色带（``中位 ± 本常量``）。
抖音名字中位 76 ⇒ 算出 61~91，**正好复现**标定的 60~92 —— 这也是这个数取 15 的依据。
"""

DEFAULT_KEY = "douyin"

# 语料路径里可能出现的平台名（目录名 / 别名）→ profile key
PLATFORM_ALIASES: dict[str, str] = {
    "抖音": "douyin", "douyin": "douyin", "tiktok": "douyin", "dy": "douyin",
    # ⚠️ 快手的别名同样要「长的在前」：含 App 的目录名（「快手app」/「快手App端」）
    #    在包含匹配里会同时命中 `快手app` 与 `快手`，排后面就永远轮不到 App 档。
    #    另外活动目录名里常带「抖音」（如「11-…抖音CTCC…」），**逐段往上找时最近的
    #    「快手」先命中**才不会被活动名抢走 —— 这条依赖 `detect_platform_from_path`
    #    先做精确、再做包含的顺序。
    "快手app": "kuaishou_app", "快手app端": "kuaishou_app",
    "kuaishou_app": "kuaishou_app", "kuaishou": "kuaishou_app",
    "快手直播": "kuaishou_app", "快手": "kuaishou_app",
    # ⚠️ App 端的别名**必须排在「视频号」前面**：包含匹配是**按本表顺序**取第一个命中的，
    #    目录名叫「视频号app」时两个别名都在里面，排在后面就永远轮不到 App 档。
    "视频号app": "wechat_app", "微信视频号app": "wechat_app",
    "wechat_app": "wechat_app", "wechatapp": "wechat_app",
    "视频号": "wechat_channels", "微信视频号": "wechat_channels",
    "wechat_channels": "wechat_channels", "channels": "wechat_channels", "wxsp": "wechat_channels",
    "微博": "weibo", "weibo": "weibo", "wb": "weibo",
}


def resolve_key(name: Optional[str]) -> Optional[str]:
    """把用户给的平台名解析成 profile key；认不出来返回 None。"""
    if not name:
        return None
    return PLATFORM_ALIASES.get(name.strip().lower()) or PLATFORM_ALIASES.get(name.strip())


def profile_for(name: Optional[str]) -> PlatformProfile:
    """按名字取 profile；认不出时返回 ``generic``。"""
    return PROFILES[resolve_key(name) or "generic"]


def detect_platform_from_path(root: Path | str) -> Optional[str]:
    """从语料路径里认平台 —— **不猜，读目录名**。

    三批语料的路径形状一致，平台名就在倒数第三段：
    ``<活动名>/<平台名>/all_users_正面_3张以上/<昵称-张数>/<n>.jpg``。
    逐段往上找，命中别名表就返回；找不到返回 None（调用方再想别的办法）。

    Args:
        root: 语料根目录（`all_users_正面_3张以上` 那一层，或它的上层）。

    Returns:
        profile key，或 None。
    """
    parts = [Path(root).name, *(p.name for p in Path(root).parents)]
    for part in reversed(parts):
        key = resolve_key(part)
        if key:
            return key
    # 目录名常是**复合名**（实测：``抖音奇瑞汽车``、``2-2026-06-17艾瑞泽8征服版上市``），
    # 精确相等认不出，所以再试一次**包含**匹配。
    for part in reversed(parts):
        low = part.lower()
        for alias, key in PLATFORM_ALIASES.items():
            if alias in low:
                return key
    return None


# --------------------------------------------------------------------------
# 冒号锚点：跨语料的定位判据
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ColonCandidate:
    """一个「像冒号」的列段及其证据。"""

    index: int
    span: tuple[int, int]
    width: int
    height: int
    fill: float
    body_width: int
    score: float

    def as_note(self) -> str:
        return (f"idx={self.index} 列{self.span[0]}-{self.span[1]} "
                f"宽{self.width} 高{self.height} 填充{self.fill:.2f} 右侧正文{self.body_width}px")


def _run_width(segs: Sequence[tuple[int, int]], i: int, gap_max: int) -> int:
    """从第 ``i`` 段起、向右连续（缝 ≤ ``gap_max``）的段合计宽度。"""
    total = 0
    prev_end: Optional[int] = None
    for s, e in segs[i:]:
        if prev_end is not None and s - prev_end - 1 > gap_max:
            break
        total += e - s + 1
        prev_end = e
    return total


def find_colon_candidates(
    segs: Sequence[tuple[int, int]],
    fills: Sequence[float],
    rows: Sequence[tuple[int, int]],
    band: tuple[int, int],
    profile: PlatformProfile,
    *,
    gap_max: int = 40,
) -> list[ColonCandidate]:
    """找出所有「像冒号」的列段，按分数降序。

    **只看几何，不看颜色** —— 这是它能在三份语料上都成立的原因。五道闸：

    1. **窄**：段宽 ∈ ``profile.colon_w_range``；
    2. **低填充**：``fill <= profile.colon_fill_max``（冒号是两点，汉字 0.37~0.49、徽章 0.74）；
    3. **不高不矮**：段高 / 行带高 ∈ ``profile.colon_h_ratio`` —— 比它矮的是句点，
       比它高的是字母 ``l`` 和汉字（这两类会顶到字带上下沿）；
    4. **上缘落在下半**：``(top - band_top) / band_h >= profile.colon_lower_ratio``
       —— 「冒号只有下半截」，这一条把占满行高的字母挡得最干净；
    5. **右边是长正文**：冒号右侧连续段的合计宽度 ≥ ``profile.colon_body_min``。

    另外要求候选左边**至少还有一段**（名字不能在冒号右边）。

    Args:
        segs:   ``[(start, end)]`` 列段（patch 坐标，闭区间）。
        fills:  每段填充率。
        rows:   每段墨迹的行范围 ``(top, bottom)``（patch 坐标）。
        band:   名字所在行带 ``(top, bottom)``。
        profile: 平台 profile。
        gap_max: 判断「右边连续」时允许的段间缝。

    Returns:
        :class:`ColonCandidate` 列表，按 ``score`` 降序（空 = 一个都不像）。
    """
    band_h = max(1, band[1] - band[0] + 1)
    w_lo, w_hi = profile.colon_w_range
    h_lo, h_hi = profile.colon_h_ratio
    out: list[ColonCandidate] = []

    for i in range(1, len(segs)):  # ≥1：左边必须有段（名字）
        s, e = segs[i]
        width = e - s + 1
        if not (w_lo <= width <= w_hi):
            continue
        if fills[i] > profile.colon_fill_max:
            continue
        top, bottom = rows[i]
        height = bottom - top + 1
        h_ratio = height / band_h
        if not (h_lo <= h_ratio <= h_hi):
            continue
        if (top - band[0]) / band_h < profile.colon_lower_ratio:
            continue
        body = _run_width(segs, i + 1, gap_max) if i + 1 < len(segs) else 0
        if body < profile.colon_body_min:
            continue
        # 分数：正文越长越像分界；同样长时偏向「更矮、更空」的那一段（更像两点）
        score = body - 3.0 * h_ratio - 10.0 * fills[i]
        out.append(
            ColonCandidate(index=i, span=(s, e), width=width, height=height,
                           fill=float(fills[i]), body_width=body, score=score)
        )
    out.sort(key=lambda c: -c.score)
    return out


def preceding_gap(segs: Sequence[tuple[int, int]], cand: ColonCandidate) -> int:
    """候选**左边那一段**与它之间空了几列（px）；它左边没段时返回 ``-1``。"""
    if cand.index <= 0:
        return -1
    return segs[cand.index][0] - segs[cand.index - 1][1] - 1


def following_gap(segs: Sequence[tuple[int, int]], cand: ColonCandidate) -> int:
    """候选**右边那一段**与它之间空了几列（px）；它右边没段时返回 ``-1``。

    「右边没段」＝这个候选就是行带里最后一段墨迹 —— 真冒号右边必有正文，
    所以这种多半是碎段（或整行只有名字），一律算不合格。
    """
    if cand.index + 1 >= len(segs):
        return -1
    return segs[cand.index + 1][0] - cand.span[1] - 1


def pick_colon(
    cands: Sequence[ColonCandidate],
    segs: Sequence[tuple[int, int]],
    profile: PlatformProfile,
) -> Optional[ColonCandidate]:
    """从候选里挑**真冒号**。

    默认（两个闸都 ≤ 0）= 取 ``cands[0]``，即分数最高的那个 —— 抖音 / generic
    走的就是这条，行为一字不变。

    每开一个闸就多**一层挑法**，顺序固定、且都是「先挑子集，一个都没有才落到下一层」：

    1. ``colon_right_gap_min > 0``：只看「**后面的缝** ≥ 它」的候选（真冒号后面是正文的
       排版空格，碎段后面是它自己那一笔）；
    2. ``colon_gap_min > 0``：再看「**前面的缝** ≥ 它」的候选（名字↔冒号的结构性缝）；
    3. 都不剩 ⇒ 退回全体（＝现状 ``cands[0]``）。

    哪一层该开、开哪个方向，看的是**语料**：App（bug-358）左缝干净、右缝没标定 ⇒ 只开左缝；
    视频号网页版（bug-367）右缝干净、左缝与字内缝两簇贴着 ⇒ 只开右缝。
    每一层都是**单调**的：只会把「没有结构性缝」的候选换成「有」的，不会凭空造出候选，
    所以「一个候选都没有」的图照样报原来的错，不会变成新的错。

    **为什么需要这层**（bug-358，2026-10-12 App 语料 229 张实测）：
    :func:`find_colon_candidates` 的分数里 ``body`` 是**右侧正文长度**，而名字**内部**的
    窄碎段（米/礻的左偏旁、心的左点、窄字母）右边还跟着**半个名字 + 冒号 + 正文**，
    正文比真冒号右边的**还长** ⇒ 分数更高 ⇒ ``cands[0]`` 取到碎段，名字槽位被截到第一个字
    （小糯米 idx1 缝3 body≈193 压过真冒号 idx4 缝5 body≈119；开心 idx1 缝2 压过 idx3 缝5；
    Fingerprints idx5 缝4 压过 idx12 缝6）。

    **为什么是「缝」**：这是唯一**结构性**的证据 —— 名字↔冒号之间是排版留白（App 实测 5~6px，
    网页版 4~6px），字内缝是字形内部的白（1~4px）；而填充率/高比/上缘两簇**完全重叠**（见 profile 注）。
    两个方向**不对称**：碎段右边一定是它自己的下一笔（1~4px），真冒号右边一定是正文的空格
    （网页版实测 17~18px）⇒ 右缝的两簇分得开；左缝要看「名字↔冒号」有多少留白，
    网页版只有 4~6px、和字内缝贴着，所以那条路上右缝更干净，反过来 App 上左缝够宽、右缝没数据。

    Returns:
        选中的候选；``cands`` 为空时返回 ``None``（调用方按「没有候选」报错）。
    """
    if not cands:
        return None
    pool: Sequence[ColonCandidate] = cands
    if profile.colon_right_gap_min > 0:
        struct = [c for c in pool if following_gap(segs, c) >= profile.colon_right_gap_min]
        if struct:
            pool = struct
    if profile.colon_gap_min > 0:
        struct = [c for c in pool if preceding_gap(segs, c) >= profile.colon_gap_min]
        if struct:
            pool = struct
    return pool[0]


def is_badge_like(
    span: tuple[int, int], fill: float, profile: PlatformProfile,
) -> bool:
    """「实心宽块」＝徽章/牌子 —— 宽 ≥ ``badge_min_width``（汉字只有 26~32px）**且**
    填充率 ≥ ``badge_fill_ratio``（文字 ≤0.49、实心牌 0.72~0.94）。两条缺一不可，
    否则宽 emoji（26~28px）会被误当徽章剔掉。"""
    return (span[1] - span[0] + 1) >= profile.badge_min_width and fill >= profile.badge_fill_ratio


NAME_RUN_GAP_MAX: int = 40
"""「同一条名字串里」允许的段间缝（像素）。走描名字时缝超过它就停。

⚠️ 只此一处定义：:func:`name_run_left_of` 的 ``gap_max`` 与 :func:`badge_left_of`
都用它 —— 两个 40 各自漂移会让「谁是药丸」在两处给出不同答案。
"""


def badge_left_of(
    segs: Sequence[tuple[int, int]],
    fills: Sequence[float],
    first: int,
    profile: PlatformProfile,
    *,
    gap_max: int = NAME_RUN_GAP_MAX,
) -> Optional[tuple[int, int]]:
    """名字**紧邻左边**那一段，是等级标吗？是就返回它的列区间，否则 ``None``。

    **为什么不能只看「左边还有段」**（bug-325，2026-10-11 用户「胶囊头没有了」）：
    ``first > 0`` 有三种来路，只有第一种是药丸 ——

    1. 走过一段「实心宽块」（= 药丸，正常）；
    2. **缝太大走不过去**（> ``gap_max``）：左边那段是从框左沿糊进来的 **1px 描边残渣**，
       或者上一行评论漏进框里的一角。实测 20:51 那批 1466 张里 **663 张（45%）** 是这一种，
       ``segs[first-1]`` 的宽度**中位 1px**（最大 8px）；
    3. 更左边还有别的段（跨过两段以上）。

    ②的代价不只是「多擦一点」：调用方拿它当「药丸左沿」⇒ 新名字整条（名字+冒号+正文）
    **从残渣那列起写**，整段左移 ~50px；还顺手把框左沿一块 16px 宽的地填平
    （这正是那张图「胶囊头没有了」的原因）。

    判据两条**都要**：那段**像药丸**（:func:`is_badge_like`，与「跨过徽章」同一条尺子）·
    且它**就在名字这一串里**（缝 ≤ ``gap_max``，与走描时同一把尺子）。
    实测这道闸不误伤：770 张真药丸的填充率 min **0.628** / 中位 **0.843**，全部通过；
    663 张残渣**全部**被挡下（宽度中位 1px、缝**全部** > 40）。

    Returns:
        药丸的 ``(左列, 右列)``（绝对值，段坐标）；不是药丸则 ``None``。
    """
    if first <= 0 or first >= len(segs):
        return None
    span = segs[first - 1]
    if segs[first][0] - span[1] - 1 > gap_max:
        return None
    if not is_badge_like(span, fills[first - 1], profile):
        return None
    return span


def name_run_left_of(
    segs: Sequence[tuple[int, int]],
    fills: Sequence[float],
    colon_index: int,
    profile: PlatformProfile,
    *,
    gap_max: int = NAME_RUN_GAP_MAX,
) -> tuple[int, int]:
    """冒号左边的那一串**文字段** = 名字（返回 ``(首段下标, 末段下标)``）。

    从冒号往左走，缝 ≤ ``gap_max`` 就一直并。遇到「实心宽块」（徽章）**跨过去、不收进来、
    也不停** —— 因为牌子在**哪一侧**逐平台不同（实测）：

    * 抖音 ``[等级牌][名字][：]`` —— 牌在名字**左边**，跨过它就到底了；
    * 视频号 ``[✨11 牌][名字][：]`` —— 同上；
    * 微博 ``[名字][铁粉牌][：]`` —— 牌夹在名字与冒号**之间**，不跨过去就一段名字都取不到。

    所以「跨过徽章」这一个动作同时满足三种版式：**收进来的是文字段，牌子一律不进名字**
    （否则槽位会把牌子圈进去 ⇒ 字号、取色全跟着错，bug 里记过这个后果）。

    Returns:
        ``(first, last)``；若左边一个文字段都没有则抛 :class:`ValueError`。
    """
    picked: list[int] = []
    j = colon_index - 1
    prev_left = segs[colon_index][0]
    while j >= 0:
        s, e = segs[j]
        if prev_left - e - 1 > gap_max:
            break
        if not is_badge_like(segs[j], fills[j], profile):
            picked.append(j)
        prev_left = s
        j -= 1
    if not picked:
        raise ValueError("冒号左边没有文字段（只有徽章？），不成结构")
    picked.sort()
    return picked[0], picked[-1]


def classify_strategy(
    name_chroma: Sequence[int],
    name_lum: Sequence[float] = (),
    body_lum: Sequence[float] = (),
) -> tuple[str, str, str]:
    """按探针量出来的特征判「该用哪套定位策略」。

    ⚠️ **只判二元：彩度判据还能不能用。** 别再往下细分成「视频号 / 微博」——
    实测这两个平台的名字**都**比正文暗（视频号 142 vs 251、微博 188 vs 254），
    亮度差分不开它们；而它们的**处置完全一样**（都走 ``colon_anchor``）。
    平台 key 优先从**路径**取（:func:`detect_platform_from_path`），那里才是权威来源。

    判据（实测定，见模块 docstring 的表）：

    * 名字段彩度中位 **≥ 60** ⇒ ``saturation_run``（名字是框内唯一高饱和文字，抖音）；
    * 否则 ⇒ ``colon_anchor``（白名字 / 灰名字，彩度判据 0 命中）。

    Args:
        name_chroma: 各图「名字段」的核心色彩度。
        name_lum:    名字段平均亮度（只用来写依据）。
        body_lum:    正文段平均亮度（只用来写依据）。

    Returns:
        ``(策略, 建议的 profile key, 判定依据)``。
    """
    chromas = sorted(name_chroma)
    if not chromas:
        return "colon_anchor", "generic", "没有量到任何名字段"
    peak = chromas[len(chromas) // 2]
    if peak >= 60:
        return "saturation_run", "douyin", f"名字段彩度中位 {peak} ≥ 60 ⇒ 彩度判据可用"
    detail = ""
    if name_lum and body_lum:
        delta = sorted(b - n for n, b in zip(name_lum, body_lum))[len(name_lum) // 2]
        detail = f"（名字比正文暗 {delta:.0f}，名字与正文只差亮度）"
    return (
        "colon_anchor", "generic",
        f"名字段彩度中位 {peak} < 60 ⇒ 彩度判据 0 命中，改用冒号锚点{detail}",
    )
