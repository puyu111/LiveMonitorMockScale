"""livepipe.parsing.ocr.engine — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations



# OCR 引擎调参（onnxruntime）。两处都**只影响速度、不影响结果**：
#
# ① ``intra_op_num_threads``：RapidOCR 默认 ``-1`` = 让 onnxruntime 用满所有核。但 PP-OCRv4
#    的 det/rec 模型都很小，在 16 核机器上几十个线程抢同一个卷积只会互相拖累 —— 同一张
#    575x825 面板裁切实测 **1/2/4/8/16 线程 = 0.88/0.68/0.87/0.63/2.25s**（默认满核最慢）。
#    取 8：实测最快，且留出核给并行的读帧/IPC。``inter_op`` 固定 1（模型内没有可并行的算子图）。
# ② ``use_cls``：角度分类器只判 0°/180°，直播评论的横排文字不会倒着写；关掉省掉每个
#    文本框一次前向。对正立文字结果**逐字不变**（分类器给 0° 时本来就不旋转）。
#
# ⚠️ **别动 det 的 ``limit_side_len`` / ``limit_type``**：默认是 ``min``/736，会把小于 736
# 的面板裁切**上采样**后再送 det。看着像浪费，实测是在帮**小字号**（等级徽章数字）的检测：
# 改成 ``max``/原生分辨率后 det 反而把徽章数字并进正文（``@余生都是你`` → ``13@余生都是你``、
# ``平安幸福`` → ``+.15平安幸福``），还会把 ``0`` 读成 ``o`` —— 徽章列的判据（``_in_badge_gutter``）
# 当场失效。省的那点时间不值这个回归。
OCR_INTRA_THREADS = 8


def _rapidocr():
    """建一个 RapidOCR 引擎（定位与解析共用同一个实例，别加载两遍）。"""
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(use_cls=False, intra_op_num_threads=OCR_INTRA_THREADS,
                    inter_op_num_threads=1)
