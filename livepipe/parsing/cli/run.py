"""livepipe.parsing.cli.run — 从 parse_live_comments.py 拆出的同源实现。"""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

from ..cli.draw import _draw
from ..comments.build import _log_badge_fill
from ..comments.dedup import dedup_comments
from ..comments.input import has_geometry
from ..comments.input import load_rows
from ..comments.input import parse_rows_any
from ..common.log import _log
from ..ocr.overrides import parse_x2_from
from ..ocr.pills import _reread_for
from ..ocr.row_ids import assign_row_ids
from ..ocr.video_rows import rows_from_video
from ..reporting import _emit
from ..reporting import _load_summary
from ..reporting import _report
from ..reporting import _stats
from ..text.classify import parse_live_comments



def run(args: argparse.Namespace) -> int:
    # 两个矩形来源同时给就是写法错误：到底听谁的没说清，别猜。
    if args.auto_panels and args.panels_file:
        raise SystemExit(
            "--auto-panels 与 --panels-file 二选一。想复用定位结果就先 --dump-panels 存下来，"
            "下次用 --panels-file 喂回去（省掉一次定位）。")
    # 定位是**给 OCR 划范围**用的，而 --rows 意味着行已经抽完了 —— 这时定位不仅白跑，
    # 还会给出一个「看起来生效了」的假象（改不了任何东西）。当场说清比静默忽略好。
    if args.auto_panels and args.rows:
        raise SystemExit(
            "--auto-panels 只在 --video 抽行时生效；--rows 是复算上游产物，行已经抽好了，"
            "改矩形也回不去。要换矩形就重新抽：--video … --auto-panels。")
    # --video 单独用 ⇒ 跑 OCR；--video 配 --rows ⇒ 只当画标注图的底帧来源。
    if args.video and not args.rows:
        sample_opts = None
        if getattr(args, "sample", None):
            try:
                sample_opts = json.loads(args.sample)
            except json.JSONDecodeError as e:
                raise SystemExit(f"--sample 不是合法 JSON：{e}")
            if not isinstance(sample_opts, dict):
                raise SystemExit('--sample 要是一个 JSON 对象，如 \'{"safety_rows": 3}\'')
        # 断点续跑：--dump-rows 就是检查点。--resume 且文件在 ⇒ 读回已处理的行、接着往下
        # OCR（新行**追加**进同一文件，跑一帧写一帧，中断不丢）；否则正常从头写。
        resume_rows: list[dict] = []
        rows_fh = None
        on_frame = None
        if args.dump_rows:
            dst = Path(args.dump_rows)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if getattr(args, "resume", False) and dst.exists():
                resume_rows = load_rows(str(dst))
                rows_fh = dst.open("a", encoding="utf-8")
                _log(f"[rows] 续跑：复用已处理 {len(resume_rows)} 行，接着写 {dst}")
            else:
                rows_fh = dst.open("w", encoding="utf-8")

            def on_frame(_t, fr, _fh=rows_fh):
                for r in fr:
                    _fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                _fh.flush()
        try:
            rows, meta = rows_from_video(
                args.video, start=args.start, duration=args.duration, interval=args.interval,
                max_frames=args.max_frames, panels_file=args.panels_file,
                sample_mode=getattr(args, "sample_mode", "interval"), sample_opts=sample_opts,
                calibrate=not args.no_calibrate, auto_panels=args.auto_panels,
                at=args.at, split_x=args.split_x, panel_frames=args.panel_frames,
                panel_span=args.panel_span, sub_width=args.sub_width, pad_x=args.pad_x,
                pad_y=args.pad_y, pad_top=args.pad_top, panel_model=args.panel_model,
                quant=args.quant, device=args.device, dump_panels=args.dump_panels,
                emoji_table=None if args.no_emoji else args.emoji_table,
                emoji_workers=args.emoji_workers,
                x2_from=parse_x2_from(args.x2_from),
                resume_rows=resume_rows or None, on_frame=on_frame)
        finally:
            if rows_fh is not None:
                rows_fh.close()
        if args.dump_rows:
            _log(f"[rows] {len(rows)} 行 -> {args.dump_rows}")
        if args.report:
            return _report(rows, args)
        comments = parse_rows_any(rows, args)
        n_frame = len(comments)
        _draw(args, comments, meta)          # 逐帧的评论 ⇒ 标注图对得上当时的屏幕
        if not args.no_dedup:
            dst = {}
            comments = dedup_comments(comments, dst, reread=_reread_for(args, comments))
            _log(f"[ocr] {meta['frames_sampled']} 帧 / {len(rows)} 行 -> 逐帧 {n_frame} 条 "
                  f"-> 唯一评论 {len(comments)} 条（滚动对齐跨帧去重）")
            _log_badge_fill(dst, comments)
        else:
            _log(f"[ocr] {meta['frames_sampled']} 帧 / {len(rows)} 行 -> {n_frame} 条（未去重）")
        _stats(comments, "=> 解析")
        _emit(comments, args, meta)
        return 0

    if args.rows:
        rows = load_rows(args.rows)
        if args.report:
            return _report(rows, args)
        # 带几何 ⇒ 同一套跨帧去重。rid 必须**先定、再解析** —— 解析时 rid 会写进评论记录，
        # 顺序反了就全成 None，dedup 会静默退回文本键（结果偏大且看不出来）。
        do_dedup = has_geometry(rows) and not args.no_dedup
        if do_dedup:
            assign_row_ids(rows)      # 从文件复算：现场补算，与新算的一致
        comments = parse_rows_any(rows, args)
        # 这条路径原本不写 summary.json，于是「配了 --video 出的标注图」不会登进清单。
        # 出一个最小 summary：来源 + 行数 + 出图清单，和 --video 路径同样的键。
        # **已有 summary 就接着改**（见 _load_summary）：定位/抽样那些元信息复算不出来。
        # frames 清单先清掉 —— 它是「这一轮画出来的」的账，不清就会留着上一轮的旧清单。
        meta = _load_summary(args.out)
        meta.pop("frames", None)
        meta.pop("frames_drawn", None)
        meta.update({"video": args.video, "engine": "rapidocr-onnxruntime",
                     "source_rows": args.rows, "rows": len(rows)})
        if has_geometry(rows):
            _draw(args, comments, meta)      # 配了 --video 就顺便出标注图
        if do_dedup:
            n_frame = len(comments)
            dst = {}
            comments = dedup_comments(comments, dst, reread=_reread_for(args, comments))
            _log(f"[rows] {n_frame} 条（逐帧）-> 唯一评论 {len(comments)} 条"
                  f"（滚动对齐跨帧去重）")
            _log_badge_fill(dst, comments)
        _stats(comments, "=> 解析")
        _emit(comments, args, meta)
        return 0

    # 纯文本：每行一条（文件或 stdin）。没有帧的概念，所以不去重。
    lines = ([l.rstrip("\n") for l in sys.stdin] if args.text == "-"
             else Path(args.text).read_text(encoding="utf-8").splitlines())
    if args.report:
        return _report([{"text": l} for l in lines], args)
    comments = parse_live_comments(lines, merge_separator=args.sep,
                                   max_username_len=args.max_username_len,
                                   merge_continuation=not args.no_merge)
    _stats(comments, "=> 解析")
    _emit(comments, args)
    return 0
