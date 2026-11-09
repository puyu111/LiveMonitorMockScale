"""livepipe.parsing.emoji.gallery — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable
import json
import re
import sys

from ..common.log import _log
from ..emoji.constants import DEFAULT_THRESHOLDS
from ..emoji.constants import EMOJI_BG
from ..emoji.constants import SOURCE_TABLES


_EMOJI_CACHE: dict[str, "EmojiGallery"] = {}


class EmojiGallery(list):
    """模板库 = 一批模板 + **这张表自己的阈值**。

    为什么不是裸 ``list``：阈值必须跟着表走（见上面那段），而模板列表在扫描里到处传
    （``scan_emoji_strip`` / ``_nms_emoji_points`` / ``_token_scores_near``）。做成 ``list``
    子类之后扫描里 ``for g in gallery`` / ``len(gallery)`` / ``gallery[i]`` **一行都不用改**，
    阈值挂在 ``gallery.thresholds`` 上顺路带走 —— 比给每个函数再加一个 thresholds 参数
    少改一大片（也就少一处传漏的机会）。
    """

    def __init__(self, items: list[dict], *, path: str, source: str | None,
                 thresholds: dict) -> None:
        super().__init__(items)
        self.path = str(path)
        self.source = source
        self.thresholds = dict(thresholds)

    def __repr__(self) -> str:      # 打日志时一眼看出是哪张表、几个模板
        return (f"<EmojiGallery {self.source or '?'} {len(self)} 个 "
                f"{Path(self.path).name}>")


def emoji_heights(pitch: int, gallery: "EmojiGallery | None" = None) -> tuple[int, ...]:
    """表情渲染高度的梯子（原图 px）。

    渲染高度是 UI 属性、不随行变，但**不同录屏、不同源都会不同**（抖音侧实测 36~40，
    视频号侧 28~32）—— 而它跟行距同源（同一个字号缩放），所以用行距推：
    ``ratio × pitch`` 上下各留 2px，三个高度都试，由 NCC 自己挑最高的那个。
    ``ratio`` 按表取（同一次运行里左右面板可能各用各的表），不给 gallery 就用默认。
    """
    ratio = (gallery.thresholds.get("height_ratio")
             if gallery is not None else DEFAULT_THRESHOLDS["height_ratio"])
    base = int(round(pitch * float(ratio)))
    return (base - 2, base, base + 2)


def _tight_crop(bgr, bg_bgr, thr: int = 55):
    """裁到「与背景差得远」的紧致框 —— 官方图四周有透明留白，不裁掉就会把留白算进 NCC。"""
    import numpy as np
    d = np.abs(bgr.astype(int) - np.asarray(bg_bgr, int)).sum(axis=2)
    ys, xs = np.where(d > thr)
    if len(xs) == 0:
        return None
    return bgr[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def load_emoji_gallery(path: str = SOURCE_TABLES["douyin"], *,
                       verbose: bool = False) -> EmojiGallery:
    """一张表情表 + 同目录图 → 模板库（紧致、已合成背景的 BGR 图 + 该表的阈值）。

    ``path`` 是**一张表的路径**。``--emoji-table`` 那个按面板的写法（``left=douyin,…``）
    先由 :func:`parse_emoji_tables` 拆成 ``{面板: 路径}``，再逐个面板调这里 ——
    别把那个串直接传进来。

    表的两个形态都认（见 ``livepipe/wechat_emoji_table.py`` 的段首说明）：
      * 抖音：身份是 32 位图床 hash ⇒ 索引键 ``byHash``，图在 ``img/<hash>.webp``；
      * 微信：没有 hash，身份是顺序号 001~109 ⇒ 索引键 ``byId``，图名逐条写在记录的
        ``img`` 字段里（``080_强.png``）。
    优先用 ``byId``，没有就退回 ``byHash``；图名同理（记录里有 ``img`` 就用它，
    否则按 ``img/<id>.webp`` 拼）。这样加一个源不用改这里的代码。

    阈值优先级：**表里的 thresholds > 内置默认**。表没带就出声警告 —— 用错阈值的表现是
    「全部静默丢掉」，外观和「这屏真没表情」一模一样，不警告根本查不出来。

    Returns:
        :class:`EmojiGallery`（``list`` 子类）：``[{"id","token","name","tmpl"}, …]``
        按 id 排序，同一 id 只留一个；``.thresholds`` / ``.source`` / ``.path`` 挂在对象上。
    """
    import cv2
    import numpy as np
    from PIL import Image

    if path in _EMOJI_CACHE:
        return _EMOJI_CACHE[path]
    p = Path(path)
    if not p.exists():
        raise SystemExit(
            f"表情表不存在：{p}\n"
            f"  抖音：python3 livepipe/douyin_emoji_table.py --api --out out/douyin_emoji\n"
            f"  微信：python3 livepipe/wechat_emoji_table.py --fetch --out out/wechat_emoji"
            f"（或 --src 指向已下好的目录）\n"
            f"  微博：python3 livepipe/weibo_emoji_table.py --out out/weibo_emoji --config"
            f"（自动读 config；手工模式才需要面板 DOM + 解析表，--src 指向已下好的目录则不联网）\n"
            f"  快手：python3 livepipe/kuaishou_emoji_table.py --out out/kuaishou_emoji"
            f"（抓 live.kuaishou.com 页面内嵌的 emojiPanel；--src 可给另存的 HTML/JSON）")
    table = json.loads(p.read_text(encoding="utf-8"))
    index = table.get("byId") or table.get("byHash") or {}
    raw_th = table.get("thresholds") or {}
    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update({k: v for k, v in raw_th.items() if k in DEFAULT_THRESHOLDS})
    if not raw_th:
        print(f"[emoji] 警告：表 {p.name} 里没有 thresholds，按**抖音侧**的默认值跑"
              f"（looks_like={thresholds['looks_like']}）。若这张表来自别的源，"
              f"真表情的分数会整体偏低 ⇒ 会被静默丢光。用 "
              f"livepipe/wechat_emoji_table.py 或 livepipe/weibo_emoji_table.py "
              f"建表可自动带上阈值。", file=sys.stderr)
    bg_rgb, bg_bgr = EMOJI_BG, tuple(int(v) for v in EMOJI_BG[::-1])
    gal: list[dict] = []
    missing: list[str] = []
    for aid, rec in sorted(index.items()):
        f = p.parent / "img" / (rec.get("img") or f"{aid}.webp")
        if not f.exists():
            missing.append(aid)
            continue
        im = Image.open(f).convert("RGBA")
        canvas = Image.new("RGB", im.size, bg_rgb)
        canvas.paste(im, mask=im.split()[3])          # alpha 合成到聊天背景色
        bgr = cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR)
        tmpl = _tight_crop(bgr, bg_bgr)
        if tmpl is None:
            continue
        gal.append({"id": aid, "token": rec.get("token"), "name": rec.get("name"),
                    "tmpl": tmpl})
    if not gal:
        raise SystemExit(f"表情表 {p} 里一条可用的模板都没有（图没下全？）")
    if missing:
        print(f"[emoji] 表里有 {len(missing)} 个 id 没下到图，已跳过：{missing[:3]}…",
              file=sys.stderr)
    g = EmojiGallery(gal, path=str(p), source=table.get("source"), thresholds=thresholds)
    _EMOJI_CACHE[path] = g
    if verbose:
        _log(f"  [emoji] {g} 阈值 looks_like={thresholds['looks_like']} "
             f"min_score={thresholds['min_score']} 高度比 {thresholds['height_ratio']}",
             flush=True)
    return g


def parse_emoji_tables(spec: str | None, panels: Iterable[str]) -> dict[str, str | None]:
    """``--emoji-table`` 的取值 → ``{面板: 表路径}``（``None`` ⇒ 该面板不跑表情）。

    写法：

    * ``路径``            所有面板都用这一张；
    * ``源名``            所有面板都用这个源的表（``SOURCE_TABLES``：douyin / wechat / weibo）；
    * ``面板=路径或源名`` 只给该面板（多个用逗号分隔，如 ``left=douyin,right=wechat``）。

    **面板名写错就直接报错**，不猜也不静默忽略：写错面板名的表现是「那个面板没跑表情」，
    产物里只少东西、没有痕迹，属于最难查的一类。同理，源名不在表里也报错并列出可用的。
    """
    names = list(panels)
    got: dict[str, str | None] = {n: None for n in names}
    if not spec:
        return got

    def resolve(v: str) -> str:
        v = v.strip()
        if not v:
            raise SystemExit(f"--emoji-table 里有空项：{spec!r}")
        if "/" in v or v.endswith(".json"):
            return v
        if v in SOURCE_TABLES:
            return SOURCE_TABLES[v]
        raise SystemExit(
            f"--emoji-table 不认识 {v!r}：既不是路径（没有 / 也不以 .json 结尾），"
            f"也不是已知的源名。已知源名：{'、'.join(SOURCE_TABLES)}"
            f"（源→表的默认路径见 SOURCE_TABLES）")

    items = [s for s in re.split(r"[,\s]+", spec.strip()) if s]
    for it in items:
        if "=" in it:
            k, v = it.split("=", 1)
            k = k.strip()
            if k not in got:
                raise SystemExit(f"--emoji-table 里的面板名 {k!r} 不存在；"
                                 f"这个录屏的面板是：{'、'.join(names)}")
            got[k] = resolve(v)
        else:
            r = resolve(it)
            for n in names:
                got[n] = r
    return got
