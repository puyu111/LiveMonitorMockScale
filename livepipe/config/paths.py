"""livepipe.config.paths"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import glob as _glob
import re

from .constants import _VIDEO_EXTS
from .errors import ConfigError



def _p(base: Path, v: Any) -> Any:
    """相对路径 → 绝对路径；``None``/``-``/绝对路径原样返回。"""
    if v is None or not isinstance(v, str):
        return v
    if v == "-" or v.startswith(("http://", "https://")):
        return v
    p = Path(v).expanduser()
    return str(p if p.is_absolute() else (base / p))


def _auto_out_dir(cfg: dict) -> str:
    """``output.dir`` 缺省时自动定名（不用手写输出目录）：优先配置 ``name``，其次视频文件名；
    名字里的路径不友好字符（空格、斜杠、全角标点…）统一换成 ``-``。"""
    raw = (cfg.get("name") or (Path(cfg["input"]["video"]).stem
                               if cfg["input"].get("video") else None) or "livepipe_run")
    safe = re.sub(r"[^\w.-]+", "-", str(raw)).strip("-")
    return f"out/{safe or 'livepipe_run'}"


def _expand_videos(spec: Any, base: Path) -> list[str]:
    """``input.videos`` → **绝对路径**列表（目录 / 通配符 / 数组都吃，一律按文件名排序）。

    排序 = 时间顺序（录屏文件名普遍带时间前缀），段名与它一一对应。
    """
    if spec is None:
        return []
    items = list(spec) if isinstance(spec, (list, tuple)) else [spec]
    out: list[str] = []
    for it in items:
        if not isinstance(it, str):
            raise ConfigError(f"input.videos 的项必须是字符串路径，给了 {type(it).__name__}：{it!r}")
        p = Path(it).expanduser()
        if not p.is_absolute():
            p = base / p
        s = str(p)
        if any(ch in s for ch in "*?["):
            hits = sorted(_glob.glob(s))
            if not hits:
                raise ConfigError(f"input.videos 的匹配式没命中任何文件：{s}")
            out += [str(Path(h).resolve()) for h in hits]
        elif p.is_dir():
            hits = sorted(q for q in p.iterdir()
                          if q.is_file() and q.suffix.lower() in _VIDEO_EXTS)
            if not hits:
                raise ConfigError(f"input.videos 指向的目录里没有视频文件：{p}")
            out += [str(q.resolve()) for q in hits]
        else:
            if not p.exists():
                raise ConfigError(f"input.videos 里的文件不存在：{p}")
            out.append(str(p.resolve()))
    return out


def segments(cfg: dict) -> list[dict]:
    """输入 → **段列表**。单视频也算一段（``name=None``）⇒ 下游只有一条代码路径。

    每段：``{name, video, out, panels_file, rows, start, duration}``
      · ``name`` 段名（= 输出子目录名）；单视频为 None
      · ``out``  该段的输出目录；单视频 = ``output.dir``，多段 = ``output.dir/<段名>``
      · ``rows`` 该段已有 rows.jsonl 时就填上 ⇒ **零 OCR 复算**（改解析逻辑后重出结果用得上）
    """
    if "_segments" in cfg:
        return cfg["_segments"]
    inp, out, pn = cfg["input"], cfg["output"], cfg["panels"]
    vids = cfg.get("_videos") or []
    names = inp.get("segment_names")
    if names is not None:
        if not isinstance(names, (list, tuple)):
            raise ConfigError("input.segment_names 必须是数组（一段一个名字）")
        if len(names) != len(vids):
            raise ConfigError(f"input.segment_names 有 {len(names)} 个名字，"
                              f"但 input.videos 展开出 {len(vids)} 段")
    segs: list[dict] = []
    if not vids:
        segs.append({"i": 0, "name": None, "video": inp["video"], "out": out["dir"],
                     "panels_file": pn["file"], "rows": inp["rows"],
                     "start": inp["start"], "duration": inp["duration"]})
    else:
        files = pn["files"]
        for i, v in enumerate(vids):
            nm = str(names[i]) if names else f"seg{i + 1}"
            d = Path(out["dir"]) / nm
            pf = (files[i] if (files and i < len(files)) else str(d / "panels.json"))
            rw = d / "rows.jsonl"
            segs.append({"i": i, "name": nm, "video": v, "out": str(d), "panels_file": pf,
                         "rows": str(rw) if rw.exists() else None,
                         "start": inp["start"], "duration": inp["duration"]})
    cfg["_segments"] = segs
    return segs


def _resolve_paths(cfg: dict) -> dict:
    base = Path(cfg["base_dir"]).expanduser() if cfg.get("base_dir") else Path.cwd()
    cfg["base_dir"] = str(base)
    inp = cfg["input"]
    if inp.get("videos") and inp.get("video"):
        raise ConfigError("input.video 与 input.videos 只能给一个"
                          "（多段用 videos，单段用 video）")
    # 展开成绝对路径，缓存到 _videos（后续 segments() 只认它）
    cfg["_videos"] = _expand_videos(inp.get("videos"), base)
    multi = bool(cfg["_videos"])
    if inp.get("videos") is not None:
        inp["videos"] = cfg["_videos"] or None
    for k in ("video", "rows", "text"):
        inp[k] = _p(base, inp[k])
    for t in cfg["emoji"]["tables"].values():
        t["path"] = _p(base, t["path"])
        b = t["build"]
        for k in ("out", "src", "api", "panel", "map"):
            if k in b:
                b[k] = _p(base, b[k])
    if cfg["output"]["dir"] is None:
        cfg["output"]["dir"] = _auto_out_dir(cfg)
    cfg["output"]["dir"] = _p(base, cfg["output"]["dir"])
    cfg["panels"]["file"] = _p(base, cfg["panels"]["file"])
    if cfg["panels"]["files"]:
        cfg["panels"]["files"] = [_p(base, f) for f in cfg["panels"]["files"]]
    cfg["export"]["users"] = _p(base, cfg["export"]["users"])
    cfg["export"]["frames"] = _p(base, cfg["export"]["frames"])
    cfg["collect"]["out"] = _p(base, cfg["collect"]["out"])
    cfg["draw"]["source"] = _p(base, cfg["draw"]["source"])
    cfg["draw"]["out"] = _p(base, cfg["draw"]["out"])
    cfg["filter"]["source"] = _p(base, cfg["filter"]["source"])
    cfg["filter"]["out"] = _p(base, cfg["filter"]["out"])
    cfg["filter"]["cache"] = _p(base, cfg["filter"]["cache"])
    # 派生默认值（配置没写时才填）。**多段素材下这些一律留空** —— 它们每段一个，
    # 由 segments() / 各 build_*_args 按段拼（output.dir 在多段下只是「段的父目录」）。
    if multi:
        # 多段共用一份判定缓存（同一句话只判一次）
        if not cfg["filter"]["cache"]:
            cfg["_judgments_cache"] = str(Path(cfg["output"]["dir"]) / "judgments.jsonl")
        return cfg
    if cfg["panels"]["file"] is None:
        cfg["panels"]["file"] = str(Path(cfg["output"]["dir"]) / "panels.json")
    if cfg["draw"]["source"] is None:
        cfg["draw"]["source"] = str(
            Path(cfg["output"]["dir"]) / "comments.jsonl")
    if cfg["draw"]["out"] is None:
        cfg["draw"]["out"] = str(
            Path(cfg["output"]["dir"]) / f"draw_{cfg['draw']['at']:g}.jpg")
    if cfg["filter"]["source"] is None:
        cfg["filter"]["source"] = str(
            Path(cfg["output"]["dir"]) / "comments.jsonl")
    if cfg["filter"]["out"] is None:
        cfg["filter"]["out"] = str(
            Path(cfg["output"]["dir"]) / "users.jsonl")
    return cfg
