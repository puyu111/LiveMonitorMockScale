"""livepipe.tools.export_user_images.io"""

from __future__ import annotations

from pathlib import Path
import json

from .common import frame_name


SCHEMA_VERSION = 2


# --------------------------------------------------------------------------- #
# 读盘
# --------------------------------------------------------------------------- #

def read_jsonl(path: Path) -> list[dict]:
    out = []
    for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise SystemExit(f"{path}:{ln} 不是合法 JSON：{e}") from None
        if isinstance(obj, dict):
            out.append(obj)
    return out


def load_contract(run_dir: Path, summary: Path | None) -> dict:
    """从 ``summary.json`` 读这批行的取帧口径（出身证明）；文件不在就返回空。"""
    p = summary or (run_dir / "summary.json")
    if not Path(p).exists():
        return {}
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {"path": str(p), "video": d.get("video"),
            "sample_mode": d.get("sample_mode"), "start": float(d.get("start") or 0.0)}


def load_panel_platform(run_dir: Path, panels: Path | None) -> dict[str, str]:
    """面板键 → 平台键。优先 ``panels.resolved.json``（平台已展开的），退回 ``panels.json``。"""
    cands = [panels] if panels else [run_dir / "panels.resolved.json", run_dir / "panels.json"]
    for p in cands:
        if p and Path(p).exists():
            try:
                d = json.loads(Path(p).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            ps = d.get("panels") or d
            out = {str(k): str(v.get("platform") or "") for k, v in ps.items()
                   if isinstance(v, dict)}
            if out:
                return out
    return {}


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def _job_base(job: dict, frames: Path) -> Path | None:
    """这条评论的底图来源路径（仅 ``--base annotated`` 用；缺图返回 None）。"""
    src = frames / frame_name(job["t"])
    return src if src.exists() else None
