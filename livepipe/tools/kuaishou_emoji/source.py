"""livepipe.tools.kuaishou_emoji.source — 快手表情的来源常量与页面解析。"""

from __future__ import annotations

import json
import re

PAGE_URL = "https://live.kuaishou.com"
"""快手直播 Web 首页。页面里内嵌 ``pcLive.webConfig.emojiPanel``：

    {"[666]": "//ali2.a.yximgs.com/bs2/emotion/1704763447505...png", ...}

207 个官方表情（2026-11-03 实测）。这是**PC 直播**的表情面板；快手手机 App 的直播评论
渲染同一套资产（实测 ``[赞]`` 在 590×1280 App 录屏上 NCC 0.94~0.96）。
"""

PANEL_KEY = "pcLive.webConfig.emojiPanel"

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")

# 图片在 ali2 / bd 两个 yximgs 镜像上，协议相对路径（//host/...）。
_IMG_HOSTS = ("ali2.a.yximgs.com", "bd.a.yximgs.com")


KUAISHOU_THRESHOLDS = {
    # 实测（2026-11-03，CTCC 8.8 快手 App 录屏 590×1280、行距 45）：
    # 官方图 100×100 PNG 被渲染成 **30px 高**（`[赞]` 实测 28~32 三档里 30 最好，
    # NCC 0.945~0.964）；30 / 45 = 0.667。
    "height_ratio": 0.667,
    # 真官方表情实测 NCC：`[赞]` 0.94~0.96、`[666]` 0.845~0.868；
    # 纯文字行最高只到 0.80（`[优秀]` 误报 0.801 / 0.788）⇒ 0.82 卡在两者之间。
    "looks_like": 0.82,
    # `[666]` 的低分档 0.845 必须能命名；0.83 留 1.5% 余量。
    "min_score": 0.83,
    "min_margin": 0.10,
    # 半尺寸粗筛：抖音/微信的 0.55 在快手 30px 上实测能提议出峰（见 2026-11-03 验证）。
    "coarse_min": 0.55,
    "coarse_cap": 400,
    # 未命名的候选里若整块是白字（S≈0）就丢。真表情是彩色位图；这条只兜底，
    # 命名的真表情不经过它。
    "min_saturation": 20.0,
}


def normalize_url(url: str) -> str:
    """``//ali2.a.yximgs.com/...`` → ``https://...``；已是绝对 URL 原样返回。"""
    url = str(url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    return url


def _panel_from_obj(obj) -> dict[str, str] | None:
    """从 JSON 对象里找 token→URL 映射（兼容裸映射 / {"emojiPanel": {...}} / 整页配置）。"""
    if not isinstance(obj, dict):
        return None
    for key in (PANEL_KEY, "emojiPanel"):
        v = obj.get(key)
        if isinstance(v, dict) and v:
            return {str(k): normalize_url(v) for k, v in v.items()}
    # 裸映射：值都是表情图 URL
    if obj and all(isinstance(v, str) and ("yximgs.com" in v or "emotion" in v)
                   for v in obj.values()):
        return {str(k): normalize_url(v) for k, v in obj.items()}
    return None


def extract_panel(html: str) -> dict[str, str]:
    """从 live.kuaishou.com 页面 HTML 里抽出 ``emojiPanel``（token → 绝对图片 URL）。

    Args:
        html: 页面源码（curl / 浏览器另存 / ``--src`` 给的本地文件）。

    Raises:
        ValueError: 页面里没有这个配置（快手改版 / 登录墙 / 不是直播首页）。
    """
    m = re.search(r'"?pcLive\.webConfig\.emojiPanel"?\s*:', html)
    if m is None:
        raise ValueError(
            f"页面里没有 {PANEL_KEY}（快手改版或返回的不是直播首页）"
            " —— 用 --src 给一份另存页面/panel.json，或稍后重试")
    start = html.find("{", m.end())
    if start < 0:
        raise ValueError(f"{PANEL_KEY} 后面没有对象")
    try:
        panel, _end = json.JSONDecoder().raw_decode(html, start)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{PANEL_KEY} 不是合法 JSON：{exc}") from exc
    if not isinstance(panel, dict) or not panel:
        raise ValueError(f"{PANEL_KEY} 是空对象")
    out: dict[str, str] = {}
    for token, url in panel.items():
        u = normalize_url(url)
        if not u or not any(h in u for h in _IMG_HOSTS):
            continue
        out[str(token)] = u
    if not out:
        raise ValueError(f"{PANEL_KEY} 里没有 yximgs 表情图 URL")
    return out
