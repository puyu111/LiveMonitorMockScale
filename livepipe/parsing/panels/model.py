"""livepipe.parsing.panels.model — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from pathlib import Path
import sys

from ..common.log import _log
from ..constants import COMMENT_SEARCH_FRAC
from ..constants import DEFAULT_PANEL_MODEL
from ..constants import _BOX_RE
from ..text.patterns import _COLON_RE



def read_frame_rgb(video: str, at: float):
    """读 ``at`` 秒处的一帧，返回 **RGB 的 ndarray**（定位与 OCR 都用这个形态）。"""
    import cv2
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"无法打开视频：{video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(at * fps)))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"读不到 {at}s 处的帧：{video}")
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


# 面板模型的**进程级缓存**（键 = 模型 / 量化 / 设备）：一次跑里各段共用同一份权重。
#
# 为什么非缓存不可（实测，bug-353）：多段素材的 ``panels`` 步是在**同一个进程**里逐段跑的
# （``pipeline.py`` 的 ``for step → for seg``），而 ``locate_panels_auto`` 每次进来都建一个
# 新 worker。**函数返回 ≠ 把上一份还回去** —— worker 被引用环扣住，实测第一段跑完
# ``torch.cuda.memory_allocated()`` 仍是 2853MiB，要 ``gc.collect()`` 才掉回（回收 9459 个
# 对象）。于是每段往 8GiB 卡上再叠 ~1.6–2.8GiB：跑到第 2/3 段的 ``predict`` 就 CUDA OOM，
# 而 OOM 又被 ``locate_panel_in_window`` 吞成「模型没给框」—— 日志读起来像模型/提示词/
# 阈值的问题，其实是显存。哪一段死只看当时的空闲显存（与段内容无关）。
# 缓存之后显存恒定（实测三段都是 2853MiB；seg2/seg3 还从 21s 掉到 ~4s，省掉 checkpoint 加载）。
#
# 代价：权重活到进程退出（跑完一整条管线本来也该如此）。真要提前还：
# ``_PANEL_WORKERS.clear()`` + ``gc.collect()`` + ``torch.cuda.empty_cache()``。
_PANEL_WORKERS: dict[tuple, object] = {}


def load_panel_worker(model_path: str = DEFAULT_PANEL_MODEL, quant: str = "4bit",
                      device: str = "cuda", verbose: bool = False):
    """加载 LocateAnything（4bit + LLM sdpa + vision flash_attention_2）。

    同参数的重复调用返回**同一份**权重（见 ``_PANEL_WORKERS``）—— 多段素材因此只加载一次，
    显存不随段数累加，这是 ``panels`` 步能在多段上跑完的前提。

    torch / transformers 是重依赖，所以只在 ``--auto-panels model|hybrid`` 时才走到这里，
    纯 OCR 路径一行都不导入。要在没网的机器上跑，先设 ``HF_HUB_OFFLINE=1``。
    """
    key = (str(model_path), str(quant), str(device))
    cached = _PANEL_WORKERS.get(key)
    if cached is not None:
        if verbose:
            _log(f"  [panels] 复用已加载的 {model_path}（{quant}）—— 同进程只加载一次",
                 flush=True)
        return cached
    if verbose:
        _log(f"  [panels] 加载 {model_path}（{quant}）…", flush=True)
    import torch
    from transformers import (
        AutoConfig, AutoModel, AutoProcessor, AutoTokenizer, BitsAndBytesConfig,
    )
    # locateanything_worker.py 在仓库根目录，而本脚本是从 livepipe/ 起的（sys.path[0]=livepipe），
    # 所以只有走到模型这条路时才把它挂上去 —— 纯 OCR / 文本路径一行都不碰。
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    from locateanything_worker import LocateAnythingWorker

    cfg = AutoConfig.from_pretrained(model_path, trust_remote_code=True)
    cfg._attn_implementation = "sdpa"
    cfg.text_config._attn_implementation = "sdpa"
    if hasattr(cfg, "vision_config"):
        cfg.vision_config._attn_implementation = "flash_attention_2"

    load_kwargs: dict = {"trust_remote_code": True, "config": cfg}
    if quant == "4bit":
        load_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True)
        load_kwargs["device_map"] = "auto"
    elif quant == "8bit":
        load_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
        load_kwargs["device_map"] = "auto"

    worker = LocateAnythingWorker.__new__(LocateAnythingWorker)
    worker.device = device
    worker.dtype = torch.bfloat16
    worker.use_batch_runtime = False
    worker.scheduler = "pipeline"
    worker.group_size = 0
    worker.tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    worker.processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=True)
    worker.model = AutoModel.from_pretrained(model_path, **load_kwargs).eval()
    _PANEL_WORKERS[key] = worker
    return worker


def parse_model_boxes(answer: str) -> list[list[int]]:
    """解析答案里的 ``<box>``，返回 ``[x1,y1,x2,y2]``（min/max 归一化，防反序）。"""
    out = []
    for m in _BOX_RE.finditer(answer):
        x1, y1, x2, y2 = (int(g) for g in m.groups())
        out.append([min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)])
    return out


def collect_comment_rows(engine, frame_rgb, win_box, search_frac: float = COMMENT_SEARCH_FRAC,
                         min_h: int = 14, max_h: int = 60) -> list[list[int]]:
    """OCR 窗口**下半部**，返回含冒号的行框（原图像素 ``[x1,y1,x2,y2]``）。

    只扫下半部是因为评论区总在窗口下方 —— 上半部是视频区 / 浏览器 chrome，扫它纯属浪费
    （分界见 ``COMMENT_SEARCH_FRAC``）。评论行几乎都是「昵称：正文」，而观众列表条目、
    视频字幕、系统行（``X 赞了直播``）都没有冒号，所以「含冒号」+ 行高合理（14~60px）
    就足够把评论行挑出来。

    ⚠ **返回空列表是合法结果，不是错误** —— 只说明这一帧（这个窗口）没有评论，比如
    刚开播那一屏只剩系统行。调用方**不该**据此判定「没有面板」；面板矩形是版式的一部分，
    由 ``locate_panel_rect`` 内容无关地给出。
    """
    x0, y0, x1, y1 = win_box
    if x1 <= x0 or y1 <= y0:
        return []                    # 空窗口（split_x 超出帧宽）——OCR 会除以 0 宽，直接返回
    sy = y0 + int((y1 - y0) * search_frac)
    res, _ = engine(frame_rgb[sy:y1, x0:x1])
    rows = []
    for box, text, _score in (res or []):
        if not _COLON_RE.search(text):
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        if not (min_h <= max(ys) - min(ys) <= max_h):
            continue
        rows.append([min(xs) + x0, min(ys) + sy, max(xs) + x0, max(ys) + sy])
    return rows


def scroll_area_from_rows(rows: list[list[int]], win_box, pad_left: int = 90,
                          pad_right: int | None = None,
                          pad_y: int = 16) -> list[int] | None:
    """把多帧收集到的评论行合成一个滚动区矩形（并集，裁到窗口内）。

    用并集而非单帧：单帧可能一条评论都没有（右窗口 t=0 全是系统行），多帧并集既补这种
    空缺，也让 x 范围扩到长评论的实际宽度。

    左右留白**分开给**：左边是给等级徽章列留位置（徽章在昵称左侧，固定 90 够用），
    右边是给**框外的表情**留余量 —— 表情在 OCR 文字框右边，留窄了 ROI 就会把表情裁掉，
    所以右边跟 ``--pad-x`` 同一个旋钮（默认与 hybrid 一致）。
    """
    if not rows:
        return None
    if pad_right is None:
        pad_right = pad_left
    x0, y0, x1, y1 = win_box
    return [max(x0, min(r[0] for r in rows) - pad_left),
            max(y0, min(r[1] for r in rows) - pad_y),
            min(x1, max(r[2] for r in rows) + pad_right),
            min(y1, max(r[3] for r in rows) + pad_y)]
