"""抖音/视频号/微博/快手直播评论抽取 —— 一体化管线（JSON 配置驱动）。

各脚本（`:mod:`parse_live_comments` / `:mod:`locate_panels` /
`:mod:`batch_locate_panels` / `:mod:`draw_boxes` / `:mod:`douyin_emoji_table` /
`:mod:`wechat_emoji_table` / :mod:`weibo_emoji_table` / :mod:`kuaishou_emoji_table`）
合并到本包，由 :mod:`livepipe.config` + :mod:`livepipe.pipeline` 用一个 JSON 文件串起来。

快速开始
--------
    # 一条命令跑完整条流水线（建表 → 定位 → 抽评论 → 出图）
    python -m livepipe run livepipe/examples/pipeline.example.json

    # 只看配置解析成什么、会执行哪些步骤，不真跑
    python -m livepipe run cfg.json --dry-run

    # 只跑某几步 / 跳过某几步
    python -m livepipe run cfg.json --only parse
    python -m livepipe run cfg.json --skip emoji_tables,draw

也可以直接跑单个脚本（与合并前一致）：

    python livepipe/parse_live_comments.py --video V.mp4 --auto-panels hybrid
"""

from __future__ import annotations

__all__ = ["VERSION", "STEPS", "PER_SEGMENT_STEPS", "DEFAULTS", "load", "resolve",
           "segments", "build_parse_args", "resolve_panels", "normalize_panels",
           "read_manifest", "run_pipeline"]

VERSION = 1
#: 跑一遍管线的步骤顺序（``steps.order`` 默认值）。分两类：
#: **逐段**（panels/parse/filter/export/draw，见 :data:`PER_SEGMENT_STEPS`）——
#: 多段素材时每段各跑一遍；**全局**（emoji_tables/qualify/collect）—— 整批只跑一次。
STEPS = ("emoji_tables", "panels", "parse", "filter", "qualify",
         "export", "collect", "draw")
PER_SEGMENT_STEPS = ("panels", "parse", "filter", "export", "draw")


def __getattr__(name):  # 懒加载，避免 import livepipe 时就把 cv2/PIL 拉进来
    if name == "DEFAULTS":
        from .config import DEFAULTS
        return DEFAULTS
    if name in ("load", "resolve", "build_parse_args", "resolve_panels",
                "normalize_panels", "read_manifest", "panel_emoji_spec",
                "write_manifest", "segments"):
        from . import config
        return getattr(config, name)
    if name == "run_pipeline":
        from .pipeline import run_pipeline
        return run_pipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
