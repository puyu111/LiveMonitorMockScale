"""``livepipe.tools.verify_run.common`` —— 校验报告的数据结构与读盘小工具。

这里只做**读盘 + 记账**，不跑任何识别/模型；也不 import 交付工具的重包
（``export_user_images`` 的 ``__init__`` 会把 frames→OCR 依赖一起拉起来），
所以平台映射/选人规则在下面按同口径复刻了一份，改口径时两边要一起改。
"""

from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path


#: 校验结论的严重度（数字越大越严重）。
LEVEL_ORDER = {"ok": 0, "info": 1, "warn": 2, "fail": 3}
ICON = {"ok": "✔", "info": "·", "warn": "⚠", "fail": "✘"}

#: 与 ``tools/export_user_images/common.py`` 的 ``PLATFORM_CN`` 逐项一致。
PLATFORM_CN = {
    "douyin": "抖音",
    "douyin_app": "抖音",
    "douyin_app_480": "抖音",
    "wechat": "视频号",
    "wechat_app": "视频号",
    "weixin": "视频号",
    "weibo": "微博",
    "weibo_app": "微博",
    "kuaishou": "快手",
    "kuaishou_app": "快手",
    "kuaishou_480": "快手",
    "kuaishou_720": "快手",
    "kuaishou_large": "快手",
    "xiaohongshu": "小红书",
    "bilibili": "B站",
}


@dataclass
class Check:
    """一条校验结论：一个步骤 + 一个对象（段/全局文件）+ 一句话。"""

    step: str
    level: str
    target: str
    message: str
    details: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"step": self.step, "level": self.level, "target": self.target,
                "message": self.message, "details": self.details}


@dataclass
class Report:
    """一整次校验的结果（给人看 + ``--json`` 机读）。"""

    config: str
    name: str
    output_dir: str
    steps: list[str]
    checks: list[Check] = field(default_factory=list)

    def add(self, step: str, level: str, target: str, message: str,
            details: list[str] | None = None) -> None:
        if level not in LEVEL_ORDER:
            raise ValueError(f"未知结论级别：{level}")
        self.checks.append(Check(step, level, target, message, list(details or [])))

    @property
    def errors(self) -> list[Check]:
        return [c for c in self.checks if c.level == "fail"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.level == "warn"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def step_level(self, step: str) -> str:
        levels = [c.level for c in self.checks if c.step == step]
        return max(levels, key=LEVEL_ORDER.get, default="ok")

    def to_dict(self) -> dict:
        return {
            "kind": "livepipe_run_verify",
            "schema_version": 1,
            "config": self.config,
            "name": self.name,
            "output_dir": self.output_dir,
            "steps": self.steps,
            "ok": self.ok,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "checks": [c.as_dict() for c in self.checks],
        }


# --------------------------------------------------------------------------- #
# 读盘
# --------------------------------------------------------------------------- #

def read_json(path: Path) -> tuple[dict | None, str | None]:
    """读 JSON 对象；返回 ``(doc, 错误)``。"""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "文件不存在"
    except OSError as e:
        return None, f"读不了：{e}"
    except json.JSONDecodeError as e:
        return None, f"不是合法 JSON：{e}"
    if not isinstance(doc, dict):
        return None, f"根节点不是对象（{type(doc).__name__}）"
    return doc, None


def count_jsonl(path: Path, *, validate: bool = False) -> tuple[int, list[str]]:
    """JSONL 行数（空行不算）。``validate=True`` 时逐行解析，返回坏行描述。

    文件不存在返回 ``-1`` —— 调用方自己决定这算错误还是「本来就没有」。
    """
    n, bad = 0, []
    try:
        with Path(path).open("r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                s = line.strip()
                if not s:
                    continue
                n += 1
                if validate:
                    try:
                        json.loads(s)
                    except json.JSONDecodeError as e:
                        bad.append(f"第 {i} 行不是合法 JSON：{e}")
    except FileNotFoundError:
        return -1, ["文件不存在"]
    except OSError as e:
        return -1, [f"读不了：{e}"]
    return n, bad


def read_jsonl(path: Path) -> tuple[list[dict], list[str]]:
    """读 JSONL 为 dict 列表；返回 ``(rows, 坏行)``。文件不存在返回 ``([], ["文件不存在"])``。"""
    rows: list[dict] = []
    bad: list[str] = []
    try:
        with Path(path).open("r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except json.JSONDecodeError as e:
                    bad.append(f"第 {i} 行不是合法 JSON：{e}")
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
                else:
                    bad.append(f"第 {i} 行不是对象（{type(obj).__name__}）")
    except FileNotFoundError:
        return [], ["文件不存在"]
    except OSError as e:
        return [], [f"读不了：{e}"]
    return rows, bad


def file_size(path: Path) -> int:
    try:
        return Path(path).stat().st_size if Path(path).is_file() else -1
    except OSError:
        return -1


def path_exists(path: Path | None) -> bool:
    """``Path.exists()`` 的安全版：权限不足/坏链接一律当「不可达」，不抛异常。"""
    try:
        return bool(path) and Path(path).exists()
    except OSError:
        return False


def path_isdir(path: Path | None) -> bool:
    try:
        return bool(path) and Path(path).is_dir()
    except OSError:
        return False


def mtime(path: Path) -> float | None:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return None


def stale(output: Path, source: Path, slack: float = 1.0) -> bool:
    """``output`` 比 ``source`` 旧 ⇒ True（source 重新生成后没重跑 output）。

    ``slack`` 给文件系统时间戳精度/同一秒写入留一点余量。
    """
    a, b = mtime(output), mtime(source)
    return a is not None and b is not None and a + slack < b


# --------------------------------------------------------------------------- #
# 视频时间（文件名区间 × 实际时长）
# --------------------------------------------------------------------------- #

#: 标准写法：``19：40-20：20`` / ``09.09-11.57`` / ``16： 00-17 25``。
_RANGE_STD = re.compile(
    r"(?<!\d)(\d{1,2})\s*[：:.．]\s*(\d{1,3})\s*[-—–~－]\s*(\d{1,2})\s*[：:.．]\s*(\d{1,3})(?!\d)")
#: 终点用 ``-`` 当冒号的写法：``12：29-15-19``（实为 12:29-15:19）。
_RANGE_DASH_END = re.compile(
    r"(?<!\d)(\d{1,2})\s*[：:.．]\s*(\d{1,3})\s*[-—–~－]\s*(\d{1,2})\s*[-—–~－]\s*(\d{2})(?!\d)")
#: 终点用空格当冒号的写法：``16： 00-17 25``（实为 16:00-17:25）。
_RANGE_SPACE_END = re.compile(
    r"(?<!\d)(\d{1,2})\s*[：:.．]\s*(\d{1,3})\s*[-—–~－]\s*(\d{1,2})\s+(\d{2})(?!\d)")


def _minute(value: int) -> int | None:
    """分钟字段归一：``011`` 这种多打一位的按末两位（11）读；≥60 且末两位也 ≥60 就不认。"""
    if value <= 59:
        return value
    tail = value % 100
    return tail if tail <= 59 else None


def parse_time_range(stem: str) -> tuple[int, int] | None:
    """文件名主干 → ``(起始分钟, 结束分钟)``（自 0 点起；跨午夜自动 +24h）。

    支持本项目实际出现过的写法：``19：40-20：20``、``09.09-11.57``、
    ``16： 00-17 25``、``16：011-19：00``（多打一位）、``12：29-15-19``（终点用连字符）。
    认不出返回 ``None``（例如裁剪副本 ``dy_22-09-22-17_live.mp4``）。
    """
    s = unicodedata.normalize("NFKC", str(stem or ""))
    m = (_RANGE_STD.search(s) or _RANGE_DASH_END.search(s)
         or _RANGE_SPACE_END.search(s))
    if not m:
        return None
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    m1, m2 = _minute(m1), _minute(m2)
    if m1 is None or m2 is None or not (0 <= h1 <= 23 and 0 <= h2 <= 23):
        return None
    a, b = h1 * 60 + m1, h2 * 60 + m2
    if b < a:
        b += 24 * 60
    return a, b


def fmt_minute(minutes: float) -> str:
    """分钟 → ``HH:MM``（超过 24h 的部分按当天钟点显示）。"""
    total = int(round(minutes))
    return f"{total // 60 % 24:02d}:{total % 60:02d}"


def compare_media_time(rng: tuple[int, int], duration_s: float,
                       tolerance_s: float = 120.0) -> tuple[str, float]:
    """文件名区间 × 实际时长 → ``(结论级别, 差值秒)``。

    - 实际**短于**区间（``diff < -tolerance``）⇒ ``fail``：少了一段，或文件根本不是这一段；
    - 实际**长于**区间（``diff > tolerance``）⇒ ``warn``：常见于录屏尾部带推荐页/花絮，
      不一定是错，但要在清单里看得见；
    - 否则 ``ok``。
    """
    want = float(rng[1] - rng[0]) * 60.0
    diff = float(duration_s) - want
    if diff < -float(tolerance_s):
        return "fail", diff
    if diff > float(tolerance_s):
        return "warn", diff
    return "ok", diff


def probe_media(path: Path, *, timeout: float = 20.0) -> tuple[dict | None, str | None]:
    """``ffprobe`` 读 ``format.duration`` / ``creation_time``（不解码，不读画面）。

    返回 ``({"duration": float|None, "creation_time": str|None}, 错误)``。
    """
    exe = shutil.which("ffprobe")
    if not exe:
        return None, "没装 ffprobe"
    cmd = [exe, "-v", "error", "-show_entries",
           "format=duration:format_tags=creation_time", "-of", "json", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"ffprobe 跑不起来：{e}"
    if r.returncode != 0:
        msg = (r.stderr or "").strip().splitlines()
        return None, (msg[-1] if msg else f"ffprobe 返回 {r.returncode}")
    try:
        doc = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return None, "ffprobe 输出不是合法 JSON"
    fmt = doc.get("format") or {}
    dur = fmt.get("duration")
    tags = fmt.get("tags") or {}
    ct = next((v for k, v in tags.items() if str(k).lower() == "creation_time"), None)
    try:
        duration = float(dur) if dur not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        duration = None
    return {"duration": duration, "creation_time": ct}, None


def creation_local_minutes(ct: str | None) -> float | None:
    """``creation_time`` → **本机时区**的当日分钟数（用于和文件名区间对照）。"""
    if not ct:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(ct).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.hour * 60 + dt.minute + dt.second / 60.0


def seg_label(seg: dict) -> str:
    return str(seg.get("name") or Path(str(seg.get("video") or "?")).stem)


def account_key(user: dict) -> tuple[str, str]:
    """账号身份 ``(昵称, 等级标)`` —— 与 ``merge_segments`` / ``account_id`` 同口径。"""
    from ...account_id import account_key as _key
    return _key(user.get("username"), user.get("level"))


def display_name(user: dict) -> str:
    from ...account_id import display
    return display(user.get("username"), user.get("level"))


# --------------------------------------------------------------------------- #
# 交付口径（复刻 export_user_images，避免把 OCR 依赖拉进 verify）
# --------------------------------------------------------------------------- #

def platform_dir(platform: str | None) -> str:
    p = (platform or "").strip()
    return PLATFORM_CN.get(p.lower(), p) or "未标平台"


def pick_comments(rec: dict, labels: set[str]) -> list[dict]:
    cs = [c for c in (rec.get("comments") or []) if c.get("label") in labels]
    return sorted(cs, key=lambda c: float(c.get("t") or 0.0))


def platform_of(comment: dict, panel_platform: dict[str, str]) -> str:
    return panel_platform.get(str(comment.get("panel")), "未标平台")


def select_users(users: list[dict], panel_platform: dict[str, str],
                 labels: set[str], min_positive: int,
                 scope: str = "platform") -> dict[str, list[tuple[dict, list[dict]]]]:
    """与 ``export_user_images/select.py`` 的 ``select`` 同口径：``{平台: [(用户, 评论)]}``。"""
    out: dict[str, list[tuple[dict, list[dict]]]] = {}
    for rec in users:
        cs = pick_comments(rec, labels)
        if not cs:
            continue
        by_p: dict[str, list[dict]] = {}
        for c in cs:
            by_p.setdefault(platform_of(c, panel_platform), []).append(c)
        if scope == "global":
            if len(cs) < min_positive:
                continue
            for p, lst in by_p.items():
                out.setdefault(p, []).append((rec, lst))
        else:
            for p, lst in by_p.items():
                if len(lst) >= min_positive:
                    out.setdefault(p, []).append((rec, lst))
    return out


def load_panel_platform(*candidates: Path) -> dict[str, str]:
    """面板键 → 平台键；优先 ``panels.resolved.json``，退回 ``panels.json``（同 export）。"""
    for p in candidates:
        if not path_exists(p):
            continue
        doc, err = read_json(Path(p))
        if err or not doc:
            continue
        ps = doc.get("panels") or doc
        out = {str(k): str(v.get("platform") or "") for k, v in ps.items()
               if isinstance(v, dict)}
        if out:
            return out
    return {}
