"""livepipe.tools.remedy_badges.common"""

from __future__ import annotations

from pathlib import Path
import json



def _log(msg: str) -> None:
    print(msg, flush=True)


def _iden(im: dict) -> str:
    """一张交付图的**身份** = 它画的是哪条评论：``段名 · 时刻 · 正文 · 表情``。

    ⚠️ **不要拿 sha1 当身份。** 图是每次跑 `export` **重新画的**，同一帧解出来的像素会在
    **肉眼不可辨**的范围内漂（2026-10-14 实测：参数、视频、帧号全同，`--apply` 那一趟的
    166 张 seg1 图与事后**同命令重跑**的产物**没有一张逐字节相同**，差值集中在 ±29/通道、
    绿框位置一字不差；而事后那两次重跑彼此逐字节相同，且与几天前交付的那棵树逐字节相同）。
    两个方向的结论不一样：**字节相同 ⇒ 确实没变**（可信的「没动」证据）；**字节不同 ⇏ 变了**
    （可能只是编码/解码漂了）⇒ 核账必须按**身份**对，不能按字节对。
    """
    return "\x1f".join([
        str(im.get("seg") or ""),
        f"{float(im.get('t') or 0.0):.3f}",
        im.get("content") or "",
        im.get("emoji_text") or "",
    ])


def _jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for ln, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"{p}:{ln} 不是合法 JSON：{e}") from None
    return out


# --------------------------------------------------------------------------- #
# ① 现状：交付树
# --------------------------------------------------------------------------- #

def _stage_name(cfg: dict) -> str:
    """导出目录的档名（``all_users_正面_<stage>张以上``）—— 与 ``collect`` 同一条式子。"""
    c = cfg["collect"]
    stage = str(c["stage_min"] if c["stage_min"] is not None
                else cfg["qualify"]["stage_min"])
    return f"all_users_正面_{stage}张以上"
