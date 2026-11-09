"""livepipe.config.io"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import copy
import json
import sys

from .constants import SAMPLE_MODES
from .constants import STEPS
from .constants import VERSION
from .constants import _OPEN_PATHS
from .defaults import DEFAULTS
from .deps import account_id
from .errors import ConfigError
from .paths import _resolve_paths
from .paths import segments



def _deep_merge(base: dict, override: dict, *, path: str = "") -> dict:
    """把 override 深合并进 base 的副本（override 优先）。未知键保留，交给校验报错。"""
    out = copy.deepcopy(base)
    for k, v in override.items():
        here = f"{path}.{k}" if path else k
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v, path=here)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _check_keys(user: dict, ref: dict, where: str = "") -> None:
    """递归挑出 DEFAULTS 里没有的键 —— 多半是拼写错，静默忽略最难查。

    ``_``/``$`` 开头的键一律当注释放行（``_comment`` / ``$schema`` …）。
    另外这几处是**开放字典**（键由用户定义）—— 里面的名字随便起，不查：
    """
    if where in _OPEN_PATHS:
        return
    for k, v in user.items():
        if isinstance(k, str) and k.startswith(("_", "$")):
            continue
        here = f"{where}.{k}" if where else k
        if k not in ref:
            print(f"[config][warn] 未知配置项 `{here}`（将原样忽略）", file=sys.stderr)
            continue
        if isinstance(v, dict) and isinstance(ref[k], dict):
            _check_keys(v, ref[k], here)


def _strip_jsonc(text: str) -> str:
    """去掉 ``//`` 行注释、``/* */`` 块注释，以及末尾多余逗号。

    只在**字符串外**识别注释（``"http://x"`` 里的 ``//`` 不是注释），字符串原样保留。
    于是配置文件可以写成带 ``//`` 备注的 JSONC，逐参数就地讲解。
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1

    # 第二遍：删掉「后面只剩 } 或 ]」的逗号（注释掉一行后常留下它）。同样避开字符串。
    s, out2, i, n, in_str = "".join(out), [], 0, len(out), False
    while i < n:
        c = s[i]
        if in_str:
            out2.append(c)
            if c == "\\" and i + 1 < n:
                out2.append(s[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
        elif c == ",":
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] in "}]":
                i += 1
                continue
        out2.append(c)
        i += 1
    return "".join(out2)


def _parse_json(text: str, src: Path) -> Any:
    """先按严格 JSON 解析；失败再按 JSONC（去注释 + 去尾逗号）解析。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError as e_strict:
        stripped = _strip_jsonc(text)
        if stripped == text:
            raise ConfigError(f"配置文件不是合法 JSON：{src}\n  {e_strict}") from e_strict
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as e_jsonc:
            raise ConfigError(
                f"配置文件去掉注释后仍不是合法 JSON：{src}\n  {e_jsonc}") from e_jsonc


def load(path: str | Path) -> dict:
    """读 JSON/JSONC 配置 → 校验 → 补默认值 → 展开路径。返回可直接喂管线的 dict。"""
    src = Path(path)
    if not src.exists():
        raise ConfigError(f"找不到配置文件：{src}")
    raw = _parse_json(src.read_text(encoding="utf-8"), src)
    if not isinstance(raw, dict):
        raise ConfigError(f"配置根节点必须是对象（{{…}}），实际是 {type(raw).__name__}")

    ver = raw.get("version", VERSION)
    if ver != VERSION:
        raise ConfigError(f"配置 version={ver}，本工具只认 {VERSION}")

    cfg = _deep_merge(DEFAULTS, raw)
    cfg["version"] = VERSION
    cfg["_path"] = str(src)
    _check_keys(raw, DEFAULTS)

    cfg = _resolve_paths(cfg)
    # panels.mode 不写（None）⇒ 自动：面板文件已在就复用，没有就定位。这样「跑一次定位、
    # 填好 platform、再跑」全程不用手改 mode —— 存在性自己说明该走哪条路。
    # 多段时没有单一的面板文件（每段一个 <段目录>/panels.json）⇒ 看**每一段**在不在。
    if cfg["panels"]["mode"] is None:
        if cfg["_videos"]:
            pfs = [s["panels_file"] for s in segments(cfg)]
            cfg["panels"]["mode"] = ("reuse" if all(Path(f).exists() for f in pfs)
                                     else "locate")
        else:
            cfg["panels"]["mode"] = ("reuse" if Path(cfg["panels"]["file"]).exists()
                                     else "locate")
    _validate(cfg)
    return cfg


def _validate(cfg: dict) -> None:
    inp, panels = cfg["input"], cfg["panels"]
    if not (inp["video"] or inp["videos"] or inp["rows"] or inp["text"]):
        raise ConfigError(
            "input 里至少要给 `video`（单段抽行）、`videos`（多段：目录/通配符/数组）、"
            "`rows`（复算已有行序）或 `text`（纯文本行）之一")
    if inp["video"] and inp["videos"]:
        raise ConfigError("input.video 与 input.videos 互斥：单段用 video，多段用 videos")
    if inp["videos"] and (inp["rows"] or inp["text"]):
        raise ConfigError("input.videos 与 input.rows/input.text 互斥"
                          "（多段时每段自己的 rows.jsonl 在 <段目录>/ 下，会在那儿自动认）")
    if inp["rows"] and inp["text"]:
        raise ConfigError("input.rows 与 input.text 互斥：二者都表示「行已经抽好了」")
    if panels["mode"] not in ("auto", "reuse", "manual", "locate", "batch", "builtin"):
        raise ConfigError(
            f"panels.mode={panels['mode']!r} 不合法；"
            "可选 auto/reuse/manual/locate/batch/builtin")
    if panels["mode"] == "reuse":
        missing = [f for f in ([panels["file"]] if panels["file"] else
                               (panels["files"] or [])) if f and not Path(f).exists()]
        if missing or not (panels["file"] or panels["files"]):
            raise ConfigError(
                f"panels.mode=reuse 但面板文件不存在：{missing or panels['file']}\n"
                "  先用 mode=locate/auto 生成，或改用 mode=manual 手写")
    if panels["mode"] == "manual" and not panels["manual"]:
        raise ConfigError(
            "panels.mode=manual 但 panels.manual 是空的。写法：\n"
            '  "manual": {"left": {"rect": [x1,y1,x2,y2], "platform": "douyin"}}\n'
            '  多段素材写成数组：一段一项，顺序与 input.videos 一致')
    if inp["videos"]:
        segs = segments(cfg)          # 段名个数、manual 项数在这里就报错，别等跑到第三步
        if isinstance(panels["manual"], (list, tuple)) \
                and panels["mode"] == "manual" and len(panels["manual"]) != len(segs):
            raise ConfigError(
                f"panels.manual 有 {len(panels['manual'])} 项，但 input.videos 展开出 "
                f"{len(segs)} 段（一段一项）")
    if inp["interval"] <= 0:
        raise ConfigError("input.interval 必须 > 0（单位：秒）")
    smp = inp.get("sample") or {}
    if smp.get("mode", "interval") not in SAMPLE_MODES:
        raise ConfigError(
            f"input.sample.mode={smp.get('mode')!r} 不合法；可选 {'/'.join(SAMPLE_MODES)}")
    if smp.get("stride", 1) < 1:
        raise ConfigError("input.sample.stride 必须 ≥ 1")
    sr = smp.get("safety_rows", 3.0)
    if not 0 < sr <= 5:
        raise ConfigError(
            f"input.sample.safety_rows={sr} 要在 (0, 5] 之间。上界不是随口定的：跨帧滚动"
            "对齐（_best_shift）的 max_shift 是 8 行，连续两次拍摄的间隔一旦超 8 行就会"
            "放弃对齐 —— 同一条评论被判成新行，重复评论直接冒到输出里。")
    if smp.get("settle_frames", 3) < 1:
        raise ConfigError("input.sample.settle_frames 必须 ≥ 1")
    if smp.get("min_interval", 0.2) <= 0 or \
            smp.get("max_interval", 6.0) < smp.get("min_interval", 0.2):
        raise ConfigError("input.sample.min_interval 必须 > 0 且 ≤ max_interval")
    if cfg["parse"]["emoji_workers"] < 1:
        raise ConfigError(
            f"parse.emoji_workers={cfg['parse']['emoji_workers']} 必须 ≥ 1"
            "（1 = 不开线程，串行；4~8 = 把表情扫描摊到多个核上）")
    if not cfg["output"]["write"] and cfg["output"]["frames"]:
        print("[config][warn] output.write=false ⇒ 不建输出目录，output.frames 无处可放，"
              "标注图会被跳过", file=sys.stderr)
    order = cfg["steps"]["order"]
    bad = [s for s in order if s not in STEPS]
    if bad:
        raise ConfigError(f"steps.order 含未知步骤 {bad}；可用：{list(STEPS)}")
    # 平台档案里认人的口径：写错了**当场报**。静默退回「只看昵称」是最坏的结果 ——
    # 名单看着照出，其实几十个人还并在一起（分析层看不到任何异常）。
    for pname, prof in (cfg["platforms"] or {}).items():
        if not isinstance(prof, dict):
            continue
        ak = prof.get("account_key")
        if ak is not None and str(ak) not in account_id.KEY_CHOICES:
            raise ConfigError(
                f"platforms.{pname}.account_key={ak!r} 不合法；"
                f"可选 {'/'.join(account_id.KEY_CHOICES)}"
                '（"nickname_level" = 一律按 (昵称, 等级标 ◆N) 认交付账号；'
                '"nickname_level_if_masked" = 只有打码昵称按 (昵称, 等级标)、'
                f'不打码昵称只看昵称；见 livepipe/account_id.py）')
    # 只在真的会跑 parse 时才卡「矩形来源」的跨步骤约束 —— 只跑 draw/建表的配置不该被牵连。
    runs_parse = "parse" in order and "parse" not in set(cfg["steps"]["skip"])
    if runs_parse and panels["mode"] == "auto" and inp["rows"]:
        raise ConfigError(
            "panels.mode=auto 只在 input.video 抽行时生效；input.rows 是复算，"
            "行已抽好、改矩形也回不去。要换矩形请重新跑 video，或用 mode=reuse/manual")
