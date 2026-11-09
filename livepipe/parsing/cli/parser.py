"""livepipe.parsing.cli.parser — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import argparse

from ..constants import DEFAULT_PANEL_MODEL
from ..constants import DEFAULT_SPLIT_X
from ..emoji.constants import DEFAULT_EMOJI_TABLE
from ..emoji.constants import SOURCE_TABLES
from ..ocr.sampling import SAMPLE_MODES



def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_argument_group("输入（选一个；都不给则跑内置示例自检）")
    src.add_argument("--video", default=None,
                     help="录屏 mp4：OCR 抽行 + 解析（需 rapidocr/opencv）；配 --rows 用时只当"
                          "标注图的底帧来源")
    src.add_argument("--rows", "--jsonl", dest="rows", default=None,
                     help="JSONL 行序（--dump-rows 的产出）；带 socket/box ⇒ 出版式解析与 ROI。"
                          "``-`` 读 stdin")
    src.add_argument("--text", default=None, help="纯文本行文件（``-`` 读 stdin）")

    ocr = p.add_argument_group("OCR（--video）")
    ocr.add_argument("--start", type=float, default=0.0, help="起始时间（秒）")
    ocr.add_argument("--duration", type=float, default=None, help="处理多长（秒；默认整段）")
    ocr.add_argument("--interval", type=float, default=2.0, help="抽样帧间隔（秒）")
    ocr.add_argument("--max-frames", type=int, default=None)
    ocr.add_argument("--sample-mode", default="interval", choices=list(SAMPLE_MODES),
                     help="抽帧策略：interval=固定间隔（默认）；backoff=按变化自适应间隔"
                          "（降级版，便宜）；adaptive=按面板位移/停稳/入口内容触发"
                          "（不漏且少重复，推荐长跑）。后两种只在 --video 时生效")
    ocr.add_argument("--sample", default=None,
                     help="adaptive/backoff 的参数（JSON 对象），键见 config.input.sample："
                          "stride/safety_rows/settle_frames/eps_px/entry_thr/max_gap_s/"
                          "min_interval/max_interval/min_score")
    ocr.add_argument("--panels-file", default=None,
                     help="JSON 覆盖内置面板与行网格（可用 --dump-panels 的产出）")
    ocr.add_argument("--no-calibrate", action="store_true",
                     help="关闭面板几何自校准（默认开启：从采样帧自动估**行距 / 续行间距比 / "
                          "浮层右界**，并**逐帧**估行相位）")
    ocr.add_argument("--dump-rows", default=None,
                     help="把行序写到这个 JSONL（便于复算/调参，不必重跑 OCR）")
    ocr.add_argument("--resume", action="store_true",
                     help="断点续跑：--dump-rows 文件已存在就接着它跑（已处理过的帧/面板跳过，"
                          "新行追加进同一文件）。中途中断/失败后重跑不必从头 OCR。")
    ocr.add_argument("--no-frames", action="store_true",
                     help="不产出标注图（默认**产出** <out>/frames/<t>_roi.jpg：每条评论的 ROI "
                          "+ 编号 + 用户名，另用细灰框画出纯 OCR 文字框做对比）")

    pn = p.add_argument_group("面板自动定位（--auto-panels 时生效）")
    pn.add_argument("--auto-panels", nargs="?", const="hybrid", default=None,
                    choices=["model", "ocr", "hybrid"],
                    help="**先自动定位左右两个评论面板，再只在该区域内 OCR**。"
                         "hybrid（默认）= x 用 LocateAnything、y 用含冒号的评论行；"
                         "ocr = 只用评论行并集（不占 GPU）；model = 全用模型。"
                         "与 --panels-file 二选一")
    pn.add_argument("--dump-panels", default=None,
                    help="把定位到的面板写这个 JSON（下次可直接 --panels-file 复用）")
    pn.add_argument("--at", type=float, default=None,
                    help="用第几秒的帧定位（默认 = --start）")
    pn.add_argument("--split-x", type=int, default=DEFAULT_SPLIT_X,
                    help=f"左右窗口分界 x（原图像素，默认 {DEFAULT_SPLIT_X}）；"
                         "0 = **不切分**（整帧一个窗口，竖屏单直播间用）")
    pn.add_argument("--panel-frames", type=int, default=5,
                    help="定位时跨几帧取样评论行（默认 5）")
    pn.add_argument("--panel-span", type=float, default=None,
                    help="定位取样跨多少秒（默认覆盖后面真正要解析的帧：interval×(max-frames-1)，"
                         "上限 120s）—— 取样窗要盖住解析帧，否则只在短评论帧上定位会把右边界收窄")
    pn.add_argument("--pad-x", type=int, default=180,
                    help="hybrid：右边界在「内容右沿」之外再留多少（给框外表情 + 采样帧里"
                         "没出现过的更长评论留余量；模型给的窗口宽度不能用）")
    pn.add_argument("--pad-top", type=int, default=80,
                    help="hybrid：首行之上再放宽多少（滚动区顶行常常没有冒号或被裁半行）")
    pn.add_argument("--pad-y", type=int, default=16, help="hybrid/ocr：行上下各留的边距")
    pn.add_argument("--panel-model", default=DEFAULT_PANEL_MODEL)
    pn.add_argument("--sub-width", type=int, default=1000,
                    help="窗口子图推理前缩到的宽度（避免 OOM，默认 1000）")
    pn.add_argument("--quant", default="4bit", choices=["none", "4bit", "8bit"])
    pn.add_argument("--device", default="cuda")
    pn.add_argument("--x2-from", default=None,
                    help="每个面板的**裁切右沿**怎么定：``面板=content|window``，逗号/空格分隔。"
                         "content（默认）= 跟内容走（裁切边 = 内容右沿 + --pad-x）；"
                         "window = 固定取窗口右沿（超集，按构造不裁内容）。"
                         "面板右边跟着内容伸缩的（视频号）必须用 window —— 右沿由内容定，"
                         "后面更宽的评论就被裁掉。ROI 右边界会自动改用该帧的内容右沿，"
                         "不会被拉到窗口边。写法同 --emoji-table：``right=window``")

    pr = p.add_argument_group("解析")
    pr.add_argument("--sep", default=" ", help="续行拼接分隔符（默认一个空格）")
    pr.add_argument("--max-username-len", type=int, default=24,
                    help="冒号前超过这个长度就不当用户名（默认 24）")
    pr.add_argument("--no-merge", action="store_true", help="不把无用户名的行并进上一条")
    pr.add_argument("--no-adjacency", action="store_true",
                    help="关闭「socket 必须相邻」守卫（回到纯文本判续行，仅在无从版式时用）")
    pr.add_argument("--right-pad", type=int, default=0,
                    help="ROI 右边界距面板右边的余量（默认 0 = 贴右边界，把表情圈进来）")
    pr.add_argument("--rank-pill", default=None,
                    help="哪些面板的等级标是**跟在昵称右边**的「榜N」药丸（微博）：面板名，"
                         "逗号/空格分隔（如 ``left``）。给了就按药丸切「昵称 | 榜N | 正文」，"
                         "药丸本身丢弃。走配置/面板文件时不用它 —— 平台档案里的 rank_pill "
                         "已经随面板带过来了")
    pr.add_argument("--fan-badge", default=None, metavar="面板=X",
                    help="哪些面板的行首有**被 OCR 并进正文**的「粉丝」药丸徽章，X 是该面板"
                         "徽章块左沿的上限（视频号手机 App 实测 37）：逗号/空格分隔的 "
                         "``面板=X``。走配置/面板文件时不用它 —— 面板/平台档案里的 "
                         "fan_badge_x_max 已经随面板带过来了")
    pr.add_argument("--badge-alias", default=None, metavar="面板,...",
                    help="哪些面板要做**行首等级药丸并进正文**的别名归一（视频号手机 App）："
                         "面板名，逗号/空格分隔（如 ``comments``）。同一条评论被读成多种昵称时，"
                         "取文字框最靠右的那版（药丸只会让框往左多起一段）。走配置/面板文件时"
                         "不用它 —— 面板/平台档案里的 badge_alias 会随面板带过来")
    pr.add_argument("--username-alias", default=None, metavar="面板,...",
                    help="哪些面板要做**昵称尾巴**别名归一（视频号手机 App）：面板名，"
                         "逗号/空格分隔（如 ``comments``）。同一条评论在不同帧被读成只差"
                         "一个尾巴的两个昵称（`W.`/`W`、`期待sj`/`期待si`、`李素熵`/`李素`）"
                         "时，按面板内行数最多的那版统一。证据 = 同一条评论的拍摄里两个读法"
                         "同现（同 rid 组各 ≥2 次），等级标不一致的不并。走配置/面板文件时"
                         "不用它 —— 面板/平台档案里的 username_alias 会随面板带过来")
    pr.add_argument("--no-dedup", action="store_true",
                    help="跨帧不去重：每一帧的行各成一条（默认按滚动对齐去重出唯一评论表）")
    pr.add_argument("--badge-reread", action="store_true",
                    help="等级标**二次读数**（要配 --video）：面板那一遍是整屏 det+rec，"
                         "药丸太小常常读不出（30~40%% 有读数）。这里按每行正文框左沿把药丸带"
                         "裁出来、只跑识别（跳过 det）跨帧投票，把漏读的补回来。"
                         "只补不改：已有面板读数的记录一个字节都不动，只用它给这一层记账"
                         "（两边独立，对得上的比例就是这一层的自证）")
    pr.add_argument("--overlay-cards", default=None, metavar="面板[=额外正则],...",
                    help="哪些面板会被**浮层卡**压住（视频号手机 App 的公告卡/主题卡/公众号卡/"
                         "广告气泡）：面板名，逗号/空格分隔（如 ``comments``）。命中卡片短语的"
                         "无冒号行会被当系统行丢掉，它紧跟的下一行也一并丢（卡片的第二行没有"
                         "特征词）。``=`` 后面可补**本素材**才有的短语（正则片段，如 "
                         "``comments=奇瑞汽车``：广告气泡的品牌行，内置表里不敢放 —— 真评论也会"
                         "提品牌名）。走配置/面板文件时不用它 —— 平台档案里的 overlay_cards "
                         "会随面板带过来")

    em = p.add_argument_group("表情识别（--video 时生效；表不在则自动降级）")
    em.add_argument("--emoji-table", default=DEFAULT_EMOJI_TABLE,
                    help=f"表情表**按面板**选（默认 {DEFAULT_EMOJI_TABLE}）。写法："
                         "「路径」「源名」或「面板=路径/源名」，多个用逗号分隔。"
                         f"源名见 SOURCE_TABLES：{'、'.join(SOURCE_TABLES)}。"
                         "同目录需有表里写明的图（抖音 img/<hash>.webp / 微信 img/<序号_名>.png /"
                         "微博 img/<序号_名>.<png|gif>）。"
                         "建表：python3 livepipe/douyin_emoji_table.py --api --out out/douyin_emoji"
                         "；python3 livepipe/wechat_emoji_table.py --fetch --out out/wechat_emoji"
                         "；python3 livepipe/weibo_emoji_table.py --out out/weibo_emoji"
                         "（微博表 thresholds 未标定，见该脚本段首）")
    em.add_argument("--no-emoji", action="store_true",
                    help="关掉表情识别（只出 OCR 文字，纯表情评论的 content 会是空串）")
    em.add_argument("--emoji-workers", type=int, default=1, metavar="N",
                    help="表情扫描的线程数（默认 1 = 不开）。表情是抽行这一步的大头、且"
                         "只吃一个核，N 取 4~8 能把整段解析加快 1.5~2.5 倍（结果与串行"
                         "逐字节一致，只是把「一行一次」的扫描摊到多个核上）")

    out = p.add_argument_group("输出")
    out.add_argument("--out", default=None,
                     help="输出目录（comments.jsonl + summary.json + frames/ 标注图）")
    out.add_argument("--print", action="store_true", help="除写盘外也把评论打到 stdout")
    out.add_argument("--report", action="store_true", help="只做逐行归类统计，不输出评论")
    out.add_argument("--no-colon-marks", action="store_true",
                     help="标注图里**不**圈分隔冒号（默认圈：昵称与正文之间的 ``:`` / ``：`` "
                          "用青色小方框标出）。只影响 frames/ 标注图，交付图不受影响")
    return p
