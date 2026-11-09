"""livepipe.tools.export_user_images.common"""

from __future__ import annotations

import hashlib
import re

_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')      # Windows/Linux 都不该出现在文件名里
_WS = re.compile(r"\s+")

# 平台键 → 交付目录名（参考语料用的是中文平台名，如 ``视频号/``、``抖音/``、``微博/``）
# 同一个平台可能有好几个**档案名**（版式不同 ⇒ 在 platforms 里各起一个名字，见 livepipe/*.json）：
# 它们该落到**同一个交付目录**，所以这里逐个列出来 —— 漏一个就会多出一个以档案名命名的目录。
PLATFORM_CN = {
    "douyin": "抖音",
    "douyin_app": "抖音",       # 抖音**手机 App**竖屏（livepipe/风云T7.json 起的档案名）
    "wechat": "视频号",
    "wechat_app": "视频号",     # 视频号**手机 App**竖屏（livepipe/ctcc.json 起的档案名）
    "weixin": "视频号",
    "weibo": "微博",
    "kuaishou": "快手",
    "kuaishou_app": "快手",     # 快手**手机 App**（mock_scale 的档案名；livepipe 配置用 kuaishou）
    "xiaohongshu": "小红书",
    "bilibili": "B站",
}


def _log(*a) -> None:
    print(*a, flush=True)


# --------------------------------------------------------------------------- #
# 纯函数（自检覆盖，不读盘、不依赖三方库）
# --------------------------------------------------------------------------- #

def frame_name(t: float) -> str:
    """标注图文件名 —— 必须与 ``draw_frames`` 的命名**逐字符一致**（``:07.1f``）。"""
    return f"{float(t):07.1f}_roi.jpg"


def frame_index(t: float, fps: float) -> int:
    """t → 帧计数器 —— 与 ``draw_frames`` / ``sample_frames_adaptive`` 同一把尺子。"""
    return int(round(float(t) * float(fps)))


def platform_dir(platform: str | None) -> str:
    """平台键 → 目录名（``wechat`` → ``视频号``）；不认识就原样用，空则 ``未标平台``。"""
    p = (platform or "").strip()
    return PLATFORM_CN.get(p.lower(), p) or "未标平台"


def sanitize(name: str | None, maxlen: int = 60) -> str:
    """用户名 → 能当目录名的字符串（保留 CJK/emoji，只清非法字符 + 限长）。

    截断时补 8 位哈希，免得两个长名字截成同一个目录。
    """
    s = _WS.sub(" ", (name or "").strip())
    s = _BAD.sub("_", s)
    s = s.strip(" .")                      # Windows 不喜欢结尾的点/空格
    if not s:
        return "匿名"
    if len(s) > maxlen:
        h = hashlib.md5(s.encode("utf-8")).hexdigest()[:8]
        s = s[:maxlen - 9] + "_" + h
    return s


def unique_dir(used: set[str], base: str) -> str:
    """目录名去重：``张三-3`` → ``张三-3`` / ``张三-3-2`` / ``张三-3-3``…"""
    if base not in used:
        used.add(base)
        return base
    i = 2
    while f"{base}-{i}" in used:
        i += 1
    used.add(f"{base}-{i}")
    return f"{base}-{i}"


def pick_comments(rec: dict, labels: set[str]) -> list[dict]:
    """取某个用户要出图的评论：标签命中，按时间升序（同一 t 保持原序，稳定）。"""
    cs = [c for c in (rec.get("comments") or []) if c.get("label") in labels]
    return sorted(cs, key=lambda c: float(c.get("t") or 0.0))


def platform_of(comment: dict, panel_platform: dict[str, str]) -> str:
    """一条评论属于哪个平台 —— 由它的 ``panel`` 查面板表（面板才是平台的载体）。"""
    return panel_platform.get(str(comment.get("panel")), "未标平台")


def parse_color(s: str) -> tuple[int, int, int]:
    parts = [int(v) for v in re.split(r"[,\s]+", s.strip()) if v]
    if len(parts) != 3 or not all(0 <= v <= 255 for v in parts):
        raise SystemExit(f"--color 要写成 R,G,B（各 0~255），收到 {s!r}")
    return tuple(parts)  # type: ignore[return-value]


def expand_box(roi, pad: int, pad_ratio: float, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """评论 ROI → 要画的框：**外扩留白** + 夹在画面内。

    外扩量 = ``max(pad, pad_ratio × 行高)`` —— 固定像素当**下限**，行高比例让
    带表情/折行的高条也拿到同等观感的留白（实测同一批语料行高 20~109px，差 5 倍，
    只用一个固定像素值要么紧贴矮行、要么在高行上小得看不见）。

    外扩后夹进 ``[0, w-1] × [0, h-1]``：贴面板边的评论原来的框会算出负数坐标，
    PIL 会**静默裁掉**半条边框，画出来像缺了一边。
    """
    x1, y1, x2, y2 = [float(v) for v in roi]
    x1, x2 = min(x1, x2), max(x1, x2)
    y1, y2 = min(y1, y2), max(y1, y2)
    e = int(round(max(float(pad), float(pad_ratio) * (y2 - y1))))
    w, h = int(size[0]), int(size[1])
    return (max(0, int(round(x1)) - e), max(0, int(round(y1)) - e),
            min(w - 1, int(round(x2)) + e), min(h - 1, int(round(y2)) + e))
