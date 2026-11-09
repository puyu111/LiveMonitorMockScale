"""JSON 配置 → 既有脚本的 ``argparse.Namespace``。

设计原则：**配置不改管线，只喂管线。**
本模块不做任何识别/解析逻辑，只做两件事：

1. :func:`load` —— 读 JSON、与 :data:`DEFAULTS` 深合并、校验，并把相对路径按
   ``base_dir``（默认 = 当前工作目录）展开成绝对路径。
2. ``build_*_args`` —— 把配置节翻成对应脚本的 Namespace。**起点一律是
   ``make_parser().parse_args([])``**（拿到该脚本全部默认值），再按配置覆盖。
   这样配置里没写的字段永远与 CLI 默认值一致，不会因为漏字段而走样。

所以「JSON 里的值 == 直接敲 CLI 的对应参数」，二者可以互相翻译、逐字对照。
"""

from .constants import (
    VERSION,
    STEPS,
    PER_SEGMENT_STEPS,
    SAMPLE_MODES,
    _OPEN_PATHS,
    _VIDEO_EXTS,
    _STEP_SWITCH,
)
from .errors import (
    ConfigError,
)
from .defaults import (
    DEFAULTS,
)
from .paths import (
    _auto_out_dir,
    _expand_videos,
    _p,
    _resolve_paths,
    segments,
)
from .io import (
    _check_keys,
    _deep_merge,
    _parse_json,
    _strip_jsonc,
    _validate,
    load,
)
from .panels import (
    _panel_entry,
    _window_right,
    identity_panels,
    level_identity_declared,
    manifest_drift,
    masked_identity_panels,
    manual_entries,
    normalize_panels,
    panel_emoji_spec,
    read_manifest,
    resolve_panels,
    segment_files,
    write_manifest,
)
from .args import (
    _base_args,
    _plc,
    build_batch_args,
    build_draw_args,
    build_emoji_args,
    build_export_args,
    build_filter_args,
    build_parse_args,
)
from .steps import (
    force_enable,
    resolve,
)

