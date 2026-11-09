"""运行配置（JSON）—— 每条线的「这次怎么跑」写在一个 JSON 文件里，命令行只用来覆盖。

为什么要这一层
--------------
**「跑什么」的参数曾经散在命令行里**：抖音、视频号、微博各一串十几个 flag，
复现一次要翻历史命令，抄错一个值没人发现。所以每个入口都读一份同名
``<脚本名>.config.json``（2026-10-27 合并后，共用管线的配置跟着它们住进 `core/`）：

===============  ==========================================================
``core/``        `batch_produce.config.json` · `batch_run.config.json`
``mock/``        `make_spec.config.json`
``expand/``      `expand_produce.config.json`（抖音）·
                 `expand_produce.视频号.config.json`（视频号网页版，`platform=generic`）·
                 `expand_produce.视频号app.config.json`（视频号 **App 端**，`platform=wechat_app`）·
                 `expand_produce.快手.config.json`（快手 **App 端**，`platform=kuaishou_app`）
===============  ==========================================================

⚠️ **App 端那条线 `platform` 必须写死**：视频号的语料目录名就叫「视频号」（720x1600 的 App
截图），`auto` 判不出 App 与网页版，只会判成 `wechat_channels` —— 见 `platforms.PROFILES["wechat_app"]`。
快手同理：它只有 App 档（`kuaishou_app`），且 `needs_geom`（融合框要靠上游 `colon_box` 定行）。

**一个平台一份配置文件**，于是「出抖音」与「出视频号」不再靠记忆里的参数区分。

用法::

    python3 core/batch_produce.py                       # 读同目录的配置文件
    python3 core/batch_produce.py mock/names/x.json     # 名单仍可走位置参数
    python3 core/batch_produce.py --limit 3 --dry-run   # 任何命令行参数覆盖 JSON
    python3 expand/expand_produce.py --config expand/expand_produce.视频号.config.json

优先级：**命令行显式给 > JSON 里写的 > 脚本里的默认值**（脚本里那份叫 ``DEFAULTS``）。
实现上，可被 JSON 控制的命令行参数在 argparse 里一律 ``default=None`` ——「None = 这次没给」，
parse 完之后由 :func:`fill` 依次回填。**别给这些参数写 `default=` 的实值**：写了就分不出
「用户给了这个值」和「这是默认值」，JSON 会被静默忽略（比报错难查得多）。

路径基准
--------
配置里的**相对路径按 :data:`config.PROJECT_ROOT`（工程根）解析** —— 与 `config.py` 自己的
`data/` `outputs/` 同基准，于是「在哪个目录下敲命令」不影响结果，`"out": "outputs/xx"`
从工程根或从 `core/` 里敲都落到同一处。绝对路径原样。用 :func:`path` 转换。

严格报错
--------
JSON 里出现不认识的键 ⇒ :class:`ConfigError`，并把合法键全列出来。这条与名单 JSON 的
「**宽容输入严格报错**」是同一条口径：配置写错一个字母就静默失效，是最难查的那种 bug。
以 ``_`` 开头的键当注释看（`_note` / `_comment`），不进合法键检查，也不进结果。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

import config


class ConfigError(Exception):
    """运行配置写错了（未知键 / 类型不对 / 文件读不了）—— 一律**报错不猜**。"""


# 注释键 / 元信息键：出现在配置里但不参与「合法键」检查
_IGNORED_KEYS = frozenset({"kind", "note", "$schema"})


def default_path(script_file: str, name: Optional[str] = None) -> Optional[Path]:
    """``<脚本名>.config.json`` 的默认位置（跟脚本同目录）；不存在返回 None。

    ``name`` 给出时用 ``<脚本名>.<name>.config.json``（本工程用它区分平台，见模块 docstring）。
    """
    stem = Path(script_file).name
    if stem.endswith(".py"):
        stem = stem[:-3]
    p = Path(script_file).resolve().parent / (f"{stem}.{name}.config.json" if name
                                              else f"{stem}.config.json")
    return p if p.is_file() else None


def open_config(path: Optional[str], *, script_file: str) -> tuple[dict, Optional[Path]]:
    """读一份运行配置，返回 ``(配置字典, 实际用的文件路径)``。

    - ``path`` 为空 ⇒ 用 :func:`default_path`（没有配置文件就返回空配置 —— 纯命令行照旧能跑）
    - 相对路径先按当前目录找，找不到再按工程根找（两种敲法都成立）
    - 文件不存在 / 不是 JSON 对象 ⇒ :class:`ConfigError`（**不静默退化成空配置**）
    """
    if not path:
        p = default_path(script_file)
        if p is None:
            return {}, None
    else:
        p = Path(path)
        if not p.is_file() and not p.is_absolute():
            alt = config.PROJECT_ROOT / p
            if alt.is_file():
                p = alt
        if not p.is_file():
            raise ConfigError(f"运行配置不存在：{p}（相对路径按当前目录或工程根 {config.PROJECT_ROOT} 找）")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"运行配置不是合法 JSON：{p}（{exc}）") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"运行配置的顶层必须是对象：{p}")
    return data, p


def _coerce(key: str, value: Any, default: Any, loose: bool = False) -> Any:
    """按「脚本里那个默认值」的类型把 JSON 值掰过来；掰不动就报错。

    ``loose=True`` 的键**不查类型**（默认值本身就是多态的，例如 `font_size`：
    `"auto"` 是字符串、`28` 是整数，两种都合法 ⇒ 由脚本自己解释）。
    """
    if loose:
        return value
    if isinstance(default, bool):                       # 注意 bool 要在 int 前面判
        if not isinstance(value, bool):
            raise ConfigError(f"`{key}` 要 true/false，给的是 {value!r}")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"`{key}` 要整数，给的是 {value!r}")
        return value
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"`{key}` 要数字，给的是 {value!r}")
        return float(value)
    if isinstance(default, str):
        if not isinstance(value, str):
            raise ConfigError(f"`{key}` 要字符串，给的是 {value!r}")
        return value
    if isinstance(default, (list, tuple)):
        if not isinstance(value, list):
            raise ConfigError(f"`{key}` 要数组，给的是 {value!r}")
        return value
    return value                                        # None / 其它：原样，由脚本自己转


def fill(args, cfg: Mapping[str, Any], defaults: Mapping[str, Any],
         loose: tuple[str, ...] = ()) -> tuple[list[str], list[str]]:
    """把配置回填进 ``args``（就地），返回 ``(命令行显式给的键, JSON 写了值的键)``。

    ``defaults`` 同时当**合法键清单**与**类型表**：值就是「命令行和 JSON 都没给」时用的那个。
    ``args`` 上对应的属性必须是 ``None`` 才认为「这次没给」（见模块 docstring）。
    ``loose`` 里的键不查类型（见 :func:`_coerce`）。
    """
    unknown = sorted(k for k in cfg
                     if k not in defaults and k not in _IGNORED_KEYS and not k.startswith("_"))
    if unknown:
        raise ConfigError(
            f"运行配置里有不认识的键：{' '.join(unknown)}\n"
            f"  合法键：{' '.join(sorted(defaults))}（以 `_` 开头的键当注释，随便写）"
        )
    from_cli: list[str] = []
    from_json: list[str] = []                           # JSON **写了**的键（含被命令行压掉的）
    for key, default in defaults.items():
        cli = getattr(args, key, None)
        in_cfg = key in cfg and cfg[key] is not None
        if in_cfg:
            from_json.append(key)
        if cli is not None:
            from_cli.append(key)
            continue
        if in_cfg:
            setattr(args, key, _coerce(key, cfg[key], default, key in loose))
        else:
            setattr(args, key, default)
    return from_cli, from_json


def path(value: Any) -> Optional[Path]:
    """配置里的路径 → :class:`Path`：相对路径以工程根为基准，绝对路径原样；空值返回 None。"""
    if value is None or value == "":
        return None
    p = Path(value)
    return p if p.is_absolute() else (config.PROJECT_ROOT / p)


def add_argument(parser) -> None:
    """给命令行加 ``--config``（留空 ⇒ 读与脚本同目录同名的 ``<脚本名>.config.json``）。"""
    parser.add_argument(
        "--config", default=None, metavar="JSON",
        help="运行配置 JSON（默认读与脚本同目录同名的 `<脚本名>.config.json`；"
             "给了就整份用它，命令行参数仍可逐项覆盖）",
    )


def load(args, defaults: Mapping[str, Any], *, script_file: str,
         loose: tuple[str, ...] = ()):
    """一次做完：读配置 → 回填 → 返回 ``(命令行给的键, JSON 写的键, 用的配置文件)``。"""
    cfg, cfg_path = open_config(args.config, script_file=script_file)
    from_cli, from_json = fill(args, cfg, defaults, loose)
    return from_cli, from_json, cfg_path


def describe(cfg_path: Optional[Path], from_cli: list[str], from_json: list[str]) -> str:
    """一行汇报：用的哪份配置、JSON 生效了几项、被命令行压掉哪几项（进日志，便于复现）。"""
    where = str(cfg_path) if cfg_path else "（无配置文件，纯命令行）"
    overridden = [k for k in from_json if k in from_cli]
    applied = len(from_json) - len(overridden)
    return (f"运行配置 {where} · JSON 生效 {applied} 项 · 命令行给 {len(from_cli)} 项"
            + (f"（其中 {', '.join(overridden)} 压掉了 JSON）" if overridden else ""))
