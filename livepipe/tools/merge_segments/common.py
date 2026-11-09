"""livepipe.tools.merge_segments.common"""

from __future__ import annotations

from ... import account_id as aid
from pathlib import Path
import hashlib
import json
import re


#: 与 export_user_images.py 的 sanitize() 同口径（目录名只清非法字符，保留 CJK/emoji）。
_WS = re.compile(r"\s+")
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def log(msg: str) -> None:
    print(msg, flush=True)


def sanitize(name: str | None, maxlen: int = 60) -> str:
    s = _WS.sub(" ", (name or "").strip())
    s = _BAD.sub("_", s).strip(" .")
    if not s:
        return "匿名"
    if len(s) > maxlen:
        s = s[:maxlen - 9] + "_" + hashlib.md5(s.encode("utf-8")).hexdigest()[:8]
    return s


def read_jsonl(p: Path) -> list[dict]:
    out = []
    for ln, line in enumerate(Path(p).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise SystemExit(f"{p}:{ln} 不是合法 JSON：{e}") from None
    return out


def write_jsonl(p: Path, rows: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with Path(p).open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _label(seg: dict) -> str:
    return seg.get("name") or Path(seg["video"] or "?").name


def _ukey(u: dict) -> tuple[str, str]:
    """用户表一条记录的**账号身份** ``(昵称, 等级标)``（老记录没有 level ⇒ 空串）。"""
    return aid.account_key(u.get("username"), u.get("level"))


def _uname(u: dict) -> str:
    """给人看的账号名（``用***◆16``；没等级就是昵称，与旧输出逐字节相同）。"""
    return aid.display(u.get("username"), u.get("level"))
