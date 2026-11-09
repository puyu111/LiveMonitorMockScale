"""livepipe.parsing.panels.locate — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

import time

from ..common.log import _log
from ..constants import COMMENT_SEARCH_FRAC
from ..constants import DEFAULT_PANEL_MODEL
from ..constants import DEFAULT_SPLIT_X
from ..constants import PANEL_PROMPT
from ..constants import PANEL_PROMPT_INPUT
from ..ocr.engine import _rapidocr
from ..panels.model import collect_comment_rows
from ..panels.model import load_panel_worker
from ..panels.model import parse_model_boxes
from ..panels.model import read_frame_rgb
from ..panels.model import scroll_area_from_rows



def locate_panel_in_window(worker, sub, sub_width: int = 1000,
                           prompt: str = PANEL_PROMPT) -> list[int] | None:
    """在一个窗口子图里定位目标区域，返回**子图内像素**框；找不到返回 None。

    子图缩到 ``sub_width`` 宽再推理（整帧 2560x1520 会让全序列 logits 在 8GB 卡上 OOM）。
    提示词可能返回多个候选，**只取面积最大的**那个 —— 最大的通常就是整个评论区。
    """
    import torch
    from PIL import Image

    sw, sh = sub.size
    scale = sub_width / sw
    subr = sub.resize((int(round(sw * scale)), int(round(sh * scale))), Image.LANCZOS)
    try:
        ans = worker.predict(subr, prompt, generation_mode="hybrid",
                             max_new_tokens=256)["answer"]
    except torch.OutOfMemoryError:
        # **必须留痕**：调用方只看「框是不是 None」，一句日志都没有就会把**显存不足**报成
        # 「模型没给框」（bug-353：多段素材跑到第 2/3 段显存被上一段的权重吃掉，报出来的
        # 却是模型的问题，于是有人去调提示词 / 阈值 / sub_width —— 越调越远）。
        # 真要是显存不够：看 ``_PANEL_WORKERS``（同进程只该有一份权重）、``sub_width``、
        # 或者别的进程占了多少卡。
        torch.cuda.empty_cache()
        free, _total = torch.cuda.mem_get_info()       # empty_cache 之后再量，才是真实余量
        _log(f"  [panels] ⚠ 显存不足（OOM）：模型没给框是**显存**的问题，不是提示词/阈值"
             f"（清缓存后仍只剩 {free / 2**20:.0f}MiB）", flush=True)
        return None
    boxes = parse_model_boxes(ans)
    if not boxes:
        return None
    rw, rh = subr.size
    best = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    px = [best[0] / 1000 * rw, best[1] / 1000 * rh,
          best[2] / 1000 * rw, best[3] / 1000 * rh]
    return [int(round(v / scale)) for v in px]


def locate_panel_rect(worker, frame_rgb, win_box, *, chat_box=None, sub_width: int = 1000,
                      pad_top: int = 80, pad_y: int = 16,
                      search_frac: float = COMMENT_SEARCH_FRAC) -> tuple[list[int] | None, dict]:
    """**内容无关**地框出窗口内的评论区 —— 不读冒号、不要求有任何评论。

    返回 ``(rect, meta)``；``rect=None`` 表示连地板都定不出来（没有聊天面板框可依）。

    这是「定位面板」和「定位评论」两件事里的**前者**：面板矩形是**录屏版式**的一部分，
    时不变，跟「这一刻有没有人说话」无关。旧实现把矩形绑在含冒号的评论行上（``hybrid``
    的 y 与 x2 全用内容），于是「采样窗内恰好没评论」被误判成「面板不存在」—— 冷启动
    录屏（t=0 那一屏只剩 ``X 赞了直播`` 系统行）就会整块丢面板。这里把它降级成
    「用一个宽一点的框」，而不是丢弃。

    四条边都取自内容之外：
      x1  模型「聊天评论面板」框的左沿（``chat_box``）—— 实测极准（575 / 1202，同素材
          多次跑极差 3px）。**不用模型的 y 和 x2**：y 不稳（两次跑能给 646 / 357），
          x2 框的是整个聊天窗口、会跨进视频区。
      y2  模型「输入框」框的上沿 **+ ``pad_y``** —— 评论区正下方就是输入框，输入框小而独特、
          定位比大区域稳（见 ``PANEL_PROMPT_INPUT``）。方向是**往下放**（与 ``hybrid`` 的
          「末行 + pad_y」同向）：上沿已在最后一行之下，再往上收就切进最后一行（裁到行 =
          整行丢），往下多圈的只是输入框顶边、没有冒号。探不到输入框就退回窗口底。
      x2  窗口右沿（超集）—— 面板右沿是内容派生的（跟着最长评论摆），取超集是为了**不裁内容**。
          代价：超集会把评论列**右侧的浮动水印/浮层**也圈进来（实测视频号右上角那颗 CHERY
          品牌水印就被读成了 ``CHER``，变成一条匿名评论）。要收紧：该面板用 ``x2_from:
          content``，再把面板文件里那份 rect 的 x2 收到「内容摆动上限之外、水印之内」
          （本素材取 2100）。另有一道兜底过滤见 ``ASCII_FRAGMENT_RE``。
      y1  取「下半部扫描线（``search_frac``）」与「模型聊天列表框上沿」中**更靠下**的那个，
          再往上放 ``pad_top`` —— 扫描线是**安全地板**（评论区总在下半部）；模型上沿只在
          更靠下时用来**收紧**。两者都放 pad_top：实测模型上沿离首行只 6px（1125 vs 1131），
          不留余量就会切到首行。两个面板上沿的可信度正好相反：视频号那种「浏览器里一块
          纯聊天列表」模型给得很准（1125），抖音那种「列表上方还有观众列表」会飘
          （348 / 646 vs 首行 749）。取 max 的净效果是**该紧的紧、该退回几何线的退回**，
          且永远不会高过安全地板。夹进 ``[y0, y2)``。

    ⚠ 浏览器套壳版式（视频号）的窗口下半部**含视频画面**，这版 y1 可能把视频**下缘**也圈进来 ——
    多花一点 OCR，但视频/字幕没有冒号，到不了下游（``classify_line`` 会滤）。这是**兜底**：
    能拿到评论行时仍走内容精修；**质量最高的做法是把采样窗（``panels.auto.at`` / ``span``）
    移到有评论的时段**，让精修路径生效。
    """
    x0, y0, x1, y1 = win_box
    if x1 <= x0 or y1 <= y0:
        return None, {"reason": "empty_window"}
    if chat_box is None:
        if worker is None:                 # 没有模型 ⇒ 没有内容之外的锚点，别硬造
            return None, {"reason": "no_chat_box"}
        from PIL import Image
        chat_box = locate_panel_in_window(worker, Image.fromarray(frame_rgb[:, x0:x1]),
                                          sub_width)
    if chat_box is None:
        return None, {"reason": "no_chat_box"}

    sy = y0 + int((y1 - y0) * search_frac)                     # 下半部扫描线
    xa = max(x0, min(x1, int(chat_box[0]) + x0))               # x1 ← 模型左沿
    yb = y1                                                    # y2 ← 优先输入框上沿
    input_box = None
    if worker is not None:
        from PIL import Image
        input_box = locate_panel_in_window(worker, Image.fromarray(frame_rgb[:, x0:x1]),
                                           sub_width, prompt=PANEL_PROMPT_INPUT)
        if input_box is not None and input_box[1] > y0 + 1:    # 输入框上沿必须在窗口内
            yb = min(y1, int(input_box[1]) + pad_y)            # 往下放 pad_y（与 hybrid 同向）
    # y1 ← 两个候选上沿（几何扫描线 sy / 模型聊天框上沿）取**更保守**（更靠下）的那个，
    # 再统一往上放 pad_top（实测模型上沿离首行只 6px，不留余量就会切到首行），夹进 [y0, y2)
    ya = min(max(y0, max(sy, int(chat_box[1])) - pad_top), max(y0, yb - 1))
    if yb - ya < 2 or x1 - xa < 2:                             # 退化框不如不给
        return None, {"reason": "degenerate"}

    meta = {"method": "geometry", "degraded": True, "reason": "no_comment_rows",
            "chat_box": [int(chat_box[0]) + x0, int(chat_box[1]),
                         int(chat_box[2]) + x0, int(chat_box[3])],
            "input_box": None if input_box is None else
                         [int(input_box[0]) + x0, int(input_box[1]),
                          int(input_box[2]) + x0, int(input_box[3])],
            "scan_y": sy, "n_rows": 0}
    return [int(xa), int(ya), int(x1), int(yb)], meta


def locate_panels_auto(video: str, method: str = "hybrid", *, at: float = 0.0,
                       split_x: int | None = DEFAULT_SPLIT_X, frames: int = 5,
                       span: float | None = None, sub_width: int = 1000,
                       pad_x: int = 180, pad_y: int = 16, pad_top: int = 80,
                       model: str = DEFAULT_PANEL_MODEL, quant: str = "4bit",
                       device: str = "cuda", engine=None,
                       verbose: bool = True) -> tuple[dict, dict]:
    """自动定位**左右两个**评论面板，返回 ``({名: [x1,y1,x2,y2]}, meta)``。

    坐标是原图像素，可直接当面板的 ``rect``（裁切窗口）用。左右是两个直播间、两套版式，
    所以先按 ``split_x`` 把画面切成两个窗口，**每个窗口里只认一个评论区** —— 整帧一次
    提问时模型会在两个面板之间摇摆（只稳定命中较大的那个），切完再问就 3/3 稳。

    **不切分**（``split_x=None`` 或 ``0``）⇒ 整帧只当一个窗口。竖屏单直播间录屏就是这种
    版式：一列评论、没有左右两个直播间，按半宽切反而把它劈成两半 —— 左半内容被切断、模型
    给不出框，右半只剩半截行、一条含冒号的行都凑不出来，于是两个窗口一起失败。
    ``split_x ≥ 帧宽`` 也能得到单窗口，但那要先知道帧宽；写 ``None`` 是「按版式说，不是
    按像素说」，换素材不用改。窗口名仍是 ``left``（唯一个），因此 ``panels.auto.platforms``
    要写成 ``{"left": "wechat_app"}``，别再写 ``comments``。

    ``span=None`` 时退化成「``frames`` 帧 × 1 秒」。调用方一般会传更贴切的跨度：
    定位取样的时间窗最好**覆盖后面真正要解析的那些帧**，否则只在定位帧里出现过的短评论
    会把 x2 收得过窄，后面更长的评论就被裁掉半行（RapidOCR 对裁切边界不是平移不变的）。

    面板矩形是**版式**的一部分，时不变，跟「这一刻有没有评论」无关。所以某个窗口的采样窗
    恰好一条评论都没有时（冷启动录屏最常见），``hybrid`` **不再丢弃该面板**，而是退回
    ``locate_panel_rect`` 给的**内容无关几何框**（``meta[名]["degraded"]=True``）—— 用宽
    一点的框换「面板不消失」。要拿回精确框：把 ``panels.auto.at`` / ``span`` 移到有评论的
    时段。

    两条路都**不轻易丢面板**：有内容行、但模型没给框时（多半是显存不足，见
    ``locate_panel_in_window``），退回 ``scroll_area_from_rows`` 的**纯内容框**
    （``degraded=True`` / ``reason="no_model_box"``）。真到「跳过」只有两种情况：
    ``ocr`` 方法（没模型可依）或**连内容行都没有、几何锚点也定不出来**。
    """
    import numpy as np

    full = read_frame_rgb(video, at)
    H, W = full.shape[:2]
    # split_x=None/0 ⇒ **不切分**：整帧一个窗口（竖屏单直播间，一列评论）。
    # split_x 超出帧宽时结果相同：左窗口退化成整帧、右窗口为空 —— **丢掉空窗口**，
    # 否则空裁切送进 OCR 会除以 0 宽（ZeroDivisionError）。单窗口 ⇒ 只剩 left（=整帧）。
    sx = W if not split_x else min(int(split_x), W)
    windows = {n: (a, b) for n, (a, b) in
               {"left": (0, sx), "right": (sx, W)}.items() if b > a}

    span = float(frames - 1) if span is None else max(1.0, span)
    stamps = [max(0.0, at + span * i / max(frames - 1, 1)) for i in range(frames)]

    engine = engine if engine is not None else _rapidocr()
    per_window: dict[str, list[list[int]]] = {n: [] for n in windows}
    if method in ("ocr", "hybrid"):
        for t in stamps:
            rgb = np.asarray(read_frame_rgb(video, t))
            for name, (x0, x1) in windows.items():
                per_window[name].extend(collect_comment_rows(engine, rgb, [x0, 0, x1, H]))
    worker = None
    if method in ("model", "hybrid"):
        # 「加载 / 复用」由 load_panel_worker 自己说 —— 多段共用一个进程时，第一段之后
        # 这里是**复用**，不是加载（见 ``_PANEL_WORKERS``）。
        worker = load_panel_worker(model, quant, device, verbose=verbose)

    result: dict[str, list[int]] = {}
    meta: dict[str, dict] = {}
    for name, (x0, x1) in windows.items():
        t0 = time.time()
        win_box = [x0, 0, x1, H]
        rows = per_window[name]
        local = None
        if worker is not None:
            from PIL import Image
            sub = Image.fromarray(full[:, x0:x1])
            local = locate_panel_in_window(worker, sub, sub_width)
        if method == "model":
            if local is None:
                _log(f"  [panels] {name} 定位失败（无框/OOM），跳过", flush=True)
                continue
            box = [local[0] + x0, local[1], local[2] + x0, local[3]]
            meta[name] = {"method": "model", "window": win_box, "local_box": local}
        elif not rows:
            # 采样窗内一条含冒号的评论行都没有。**不再直接丢面板** —— 那等于把「这一刻
            # 没评论」当成「面板不存在」。改用内容无关的几何框兜底（见 locate_panel_rect）：
            # 面板是版式的一部分，跟有没有人说话无关。
            # ocr 方法没有内容之外的锚点（没模型 ⇒ 没有 x1 / 输入框框），只能像以前那样跳过。
            box, fmeta = None, None
            if method == "hybrid" and local is not None:
                box, fmeta = locate_panel_rect(
                    worker, full, win_box, chat_box=local, sub_width=sub_width,
                    pad_top=pad_top, pad_y=pad_y)
            if box is None:
                why = fmeta["reason"] if fmeta else "ocr 方法无模型锚点"
                _log(f"  [panels] {name} 定位失败（{len(stamps)} 帧里没有一条含冒号的行，"
                     f"{why}），跳过", flush=True)
                continue
            meta[name] = {"window": win_box, **fmeta}
            _log(f"  [panels] {name} ⚠ 采样窗内无评论行 ⇒ 退回内容无关的几何框"
                 f"（把 panels.auto.at/span 移到有评论的时段可换回精确框）", flush=True)
        elif method == "ocr":
            box = scroll_area_from_rows(rows, win_box, pad_left=90, pad_right=pad_x,
                                        pad_y=pad_y)
            if box is None:
                continue
            meta[name] = {"method": "ocr", "window": win_box, "n_rows": len(rows)}
        else:
            # x1 用模型（判「面板在哪一列」很准）；x2 **不能用模型的** —— 它框的是整个
            # 聊天窗口宽度（右面板实测 2536，跨过视频区），改用内容右沿 + 余量。
            # y **也全用内容**：模型的 y 在同一素材上两次跑能给 646 和 357（4bit 下不稳），
            # 357 会把上方观众列表整个拉进来。上边 = 首个冒号行再往上放 pad_top（滚动区
            # 顶行的冒号常常不在，或被裁掉一半）；下边 = 末个冒号行 + pad_y（不能多放，
            # 右面板下面是输入框）。
            if local is None:
                # 模型没给框（多半是显存不足 —— 见 ``locate_panel_in_window`` 的 OOM 日志）。
                # **别整块丢面板**：内容还在，退回**纯内容框**（与 method="ocr" 同一条路，
                # ``scroll_area_from_rows`` 就是为「没有模型锚点」写的）。丢面板的代价是
                # 这一步 rc=1、整条管线中止；退化的代价只是 x 少了模型锚点、跟着内容摆动 ——
                # 后者小得多，且调用方随时能把 at/span 挪到有评论的时段重取精确框。
                box = scroll_area_from_rows(rows, win_box, pad_left=90, pad_right=pad_x,
                                            pad_y=pad_y)
                if box is None:                        # rows 非空时到不了这儿，纯防御
                    _log(f"  [panels] {name} 模型没给框、内容也框不出滚动区，跳过",
                         flush=True)
                    continue
                meta[name] = {"method": "hybrid", "degraded": True,
                              "reason": "no_model_box", "window": win_box,
                              "n_rows": len(rows)}
                _log(f"  [panels] {name} ⚠ 模型没给框 ⇒ 退回纯内容框"
                     f"（x1/x2 跟着内容摆动，精度低于模型锚点；先看上面有没有 OOM 那一行）",
                     flush=True)
            else:
                mx1, mx2 = local[0] + x0, local[2] + x0
                cy1 = min(r[1] for r in rows)
                cy2 = max(r[3] for r in rows)
                box = [mx1, max(0, cy1 - pad_top),
                       min(mx2, max(r[2] for r in rows) + pad_x), min(H, cy2 + pad_y)]
                meta[name] = {"method": "hybrid", "window": win_box,
                              "model_box": [mx1, local[1], mx2, local[3]],
                              "content": [min(r[0] for r in rows), cy1,
                                          max(r[2] for r in rows), cy2],
                              "n_rows": len(rows)}
        result[name] = [int(v) for v in box]
        meta[name]["t"] = round(time.time() - t0, 2)
        if verbose:
            ow, oh = x1 - x0, H
            bw, bh = result[name][2] - result[name][0], result[name][3] - result[name][1]
            _log(f"  [panels] {name} {result[name]}  "
                 f"（窗口 {ow}x{oh} -> 裁切 {bw}x{bh}，面积的 "
                 f"{100 * bw * bh / max(ow * oh, 1):.0f}%）", flush=True)
    return result, meta
