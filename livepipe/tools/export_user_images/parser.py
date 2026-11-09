"""livepipe.tools.export_user_images.parser"""

from __future__ import annotations

from .doc import DOC
import argparse



def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=DOC,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--users", help="filter 步的 users.jsonl（必填，--selftest 除外）")
    p.add_argument("--out", help="输出根目录（默认 users.jsonl 所在目录）")
    p.add_argument("--summary", help="读取帧口径/video 的 summary.json（默认 <users 同目录>/summary.json）")
    p.add_argument("--panels", help="面板表（默认 <users 同目录>/panels.resolved.json，退回 panels.json）")
    p.add_argument("--video", help="视频路径（默认取 summary.json 里的 video）")
    p.add_argument("--frames", help="标注图目录（--base annotated 时用，默认 <users 同目录>/frames）")
    p.add_argument("--base", choices=["frame", "annotated"], default="frame",
                   help="底图：frame=视频原帧（交付形态，默认）/ annotated=复制 frames/ 里的标注图（调试）")
    p.add_argument("--sample-mode", choices=["adaptive", "interval", "backoff"],
                   help="取帧口径（默认取 summary.json 里的，兜底 adaptive）")
    p.add_argument("--labels", default="正面", help="出哪些标签的评论，逗号分隔（默认 正面）")
    p.add_argument("--min-positive", type=int, default=3,
                   help="至少几条命中标签才出（默认 3；默认按平台各算，见 --min-scope）")
    p.add_argument("--min-scope", choices=["platform", "global"], default="platform",
                   help="阈值算在哪一层：platform=每个平台各自够数（默认）/ global=整段视频够数")
    p.add_argument("--flat", action="store_true", help="不分平台，全并到一个目录（阈值走 global）")
    p.add_argument("--no-box", action="store_true", help="不画绿框，出纯原帧")
    p.add_argument("--color", default="0,255,0", help="绿框颜色 R,G,B（默认 0,255,0，对齐参考语料）")
    p.add_argument("--width", type=int, default=3, help="框线宽（默认 3，对齐参考语料）")
    p.add_argument("--pad", type=int, default=0,
                   help="框外扩的**像素下限**（默认 0；实际外扩取 max(pad, pad-ratio×行高)）")
    p.add_argument("--pad-ratio", type=float, default=0.25,
                   help="框按行高外扩的比例（默认 0.25：行高 29px ⇒ 外扩 7px；0 = 只按 --pad）")
    p.add_argument("--quality", type=int, default=90,
                   help="JPEG 质量（默认 90 —— 与参考交付语料的 530KB/张 同量级；95 会涨到 ~860KB/张）")
    p.add_argument("--limit", type=int, help="每个平台只出前 N 个用户（试跑用）")
    p.add_argument("--force", action="store_true", help="输出目录非空也照写")
    p.add_argument("--dry-run", action="store_true", help="只列用户，不写文件")
    p.add_argument("--selftest", action="store_true", help="跑纯函数自检后退出")
    return p
