"""livepipe.config.constants"""

from __future__ import annotations


VERSION = 1
#: 步骤分两类：
#:   **逐段** —— panels / parse / filter / export / draw：有几段就按段各跑一遍（段见 :func:`segments`）
#:   **全局** —— emoji_tables / qualify / collect：整批只跑一次（要看到所有段的产物）
#: 顺序有依赖：filter（逐段）→ qualify（跨段判合格）→ export（逐段）→ collect（拼树）。
STEPS = ("emoji_tables", "panels", "parse", "filter", "qualify",
         "export", "collect", "draw")
#: 逐段跑的步骤（其余是全局步骤）。
PER_SEGMENT_STEPS = ("panels", "parse", "filter", "export", "draw")
#: 抽帧策略（与 parse_live_comments.SAMPLE_MODES 同步）。
SAMPLE_MODES = ("interval", "backoff", "adaptive")


#: 这些位置的键由用户自定义（平台名 / 面板名 / 表情表源名），不查拼写。
_OPEN_PATHS = {"platforms", "emoji.tables", "panels.manual", "panels.auto.platforms"}


#: 把目录当输入时认哪些扩展名（大小写不敏感）。
_VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".flv", ".ts", ".m4v")


#: 步骤名 → 它的开关在配置里的位置（``enabled``）。**没有开关的步骤**（panels/parse/
#: emoji_tables）不在这里：它们要么总是该跑，要么有自己的存在性判断。
_STEP_SWITCH = {
    "filter": ("filter", "enabled"),
    "qualify": ("qualify", "enabled"),
    "export": ("export", "enabled"),
    "collect": ("collect", "enabled"),
    "draw": ("draw", "enabled"),
}
