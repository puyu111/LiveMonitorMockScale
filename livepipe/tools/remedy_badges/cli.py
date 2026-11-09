"""livepipe.tools.remedy_badges.cli"""

from __future__ import annotations

from ... import config as C
from ... import pipeline as P
from pathlib import Path
import argparse
import json
import os
import shutil
import time

from .common import _log
from .common import _stage_name
from .levels import new_level_index
from .levels import totals_of
from .report import report
from .tree import check_cache
from .tree import check_rows
from .tree import delivery_root
from .tree import read_tree
from .verify import verify



# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m livepipe.remedy_badges",
        description="按修好的解析层重算交付树（先印单子，--apply 才落盘）")
    ap.add_argument("config", help="档案，例：livepipe/风云T7.json（在 Embodied/ 下跑）")
    ap.add_argument("--apply", action="store_true",
                    help="真落盘：export → collect(force) 重建交付树，并按身份核账")
    ap.add_argument("--show", default="",
                    help="逐图印归属的目录名（逗号分隔），例：初___-5,白___-14")
    ap.add_argument("--only-parse", action="store_true",
                    help="只跑到 parse+filter+qualify 印单子，不碰 export/collect")
    ap.add_argument("--no-parse", action="store_true",
                    help="复用上一次 parse 的 comments.jsonl（**只改阈值**时用：读数没变就不必再回原帧重读）")
    a = ap.parse_args(argv)

    cfg = C.load(a.config)
    segs = C.segments(cfg)
    min_n = int(cfg["qualify"]["min_positive"])
    root = delivery_root(cfg, segs)

    _log(f"=== 补救 {cfg.get('name')} ===")
    _log(f"    配置 {cfg['_path']}   基准目录 {cfg['base_dir']}")
    _log(f"    交付树 {root}")
    if not (root / "manifest.json").exists():
        _log("[补救][error] 交付树里没有 manifest.json —— 多半是在**错的目录**下跑的：")
        _log("          output.dir 是相对路径，基准目录 = 当前工作目录；")
        _log("          请在仓库根（Embodied/）下跑："
             "python3 -m livepipe.remedy_badges livepipe/风云T7.json")
        return 2
    if check_rows(segs):
        return 2

    # ① 冻结现状（先于任何写操作：落盘前的一切判断都对着这一份）
    _doc, old_tree = read_tree(root)
    _log(f"[补救] 已冻结现状：{len(old_tree)} 档 / "
         f"{sum(r['count'] for r in old_tree.values())} 张（含身份）")

    # ③ 重算（parse 零 OCR：rows.jsonl 在）
    # 标注图这一趟不重画：这一轮改的是 dedup 的 badge_text，与 frames/ 上的标注无关；而交付
    # 图走 export.base=frame（**直接从视频裁底帧**，见 export_user_images.write_images），
    # frames/ 只有 --base annotated 才读 ⇒ 重画一遍纯属烧盘烧时间（七段约 1.2GB）。
    # 视频不在/没裁过 frames 时不碰这个开关（宁可慢，也别把产物弄空）。
    if cfg["output"].get("frames") and all((Path(s["out"]) / "frames").is_dir() for s in segs):
        cfg["output"]["frames"] = False
        _log("[补救] frames/ 沿用已有（不重画；交付图的底帧直接取自视频）")
    if a.no_parse:
        # **只改了阈值**的这一趟：口径没变，parse 算出来的东西（含二次读数补的等级标）已经在
        # 各段 ``comments.jsonl`` 里，再回原帧重读一遍只是白等半小时。
        # 复用别人的旧产物是可以的，但得**看得见**它有多旧 —— 逐段印时间戳；缺一段就停，
        # 半新半旧的单子比慢一点的单子危险得多。
        for s in segs:
            p = Path(s["out"]) / "comments.jsonl"
            if not p.exists():
                _log(f"[补救][error] --no-parse 要用 {p}，它不在 —— 去掉 --no-parse 重跑一遍 parse")
                return 2
            st = p.stat()
            _log(f"[补救] 复用 {s['name']:>10} comments.jsonl  "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}"
                 f"（{(time.time() - st.st_mtime) / 3600:.1f} 小时前，{st.st_size / 1024:.0f} KB）")
        _log("[补救] --no-parse：跳过 parse，读数沿用上面这几份")
    else:
        _log("[补救] --- parse（从 rows.jsonl 复算，不重抽行）---")
        # parse.badge_reread 不写（None）⇒ 跟着该段的 account_key 走（与 build_parse_args 同一条
        # 规矩）；每段的面板文件可能不一样，所以逐段判，别拿整份配置一句话概括。
        br = cfg["parse"].get("badge_reread")
        for s in segs:
            on = C.level_identity_declared(cfg, s) if br is None else bool(br)
            if on:
                _log(f"[补救] {s['name'] or '':>10} 等级标二次读数已开："
                     f"会按药丸带回原帧重读（只跑识别，不重抽行）")
        rc = P.run_pipeline(cfg, only=["parse"])
        if rc:
            return rc
    if check_cache(cfg, segs):                       # 未命中且没 key ⇒ 停在这里，别半途
        return 2
    if not os.environ.get(cfg["filter"]["nlp"]["api_key_env"]):
        # 缓存 100% 命中 ⇒ filter 一次都不调模型；user_filter 仍要求 key 非空，给个占位。
        os.environ[cfg["filter"]["nlp"]["api_key_env"]] = "cache-only-no-call"
        _log("[补救] 判定缓存全命中 ⇒ 本趟零模型调用（占位 key，不会发出去）")
    _log("[补救] --- filter（只读判定缓存）+ qualify（跨段合计，账号重新分档）---")
    rc = P.run_pipeline(cfg, only=["filter", "qualify"])
    if rc:
        return rc

    # ④ 印单子
    idx = new_level_index(cfg, segs)
    tot = totals_of(cfg, segs)
    show = [s.strip() for s in a.show.split(",") if s.strip()]
    plan = report(cfg, root, old_tree, idx, tot, min_n, show)
    plan_p = Path(cfg["output"]["dir"]) / "remedy_plan.json"
    plan_p.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    _log(f"[补救] 单子 → {plan_p}")

    if not a.apply or a.only_parse:
        _log("[补救] 预览结束：**交付树没动**（段目录里的中间产物已更新）。"
             "要落盘加 --apply")
        return 0

    # ⑤ 落盘 + 核账
    # export **不会**清上一轮的目录（它只 mkdir + 覆盖同名文件，见 write_images）⇒ 旧分组的
    # 目录会在段目录里过夜，人翻到就会以为那些还在交付。先把几段的目标目录整个清掉 ——
    # 它们全部是从评论 + 视频重建的派生数据（清掉不影响任何原始素材）。
    for s in segs:
        for d in Path(s["out"]).glob(f"*/{_stage_name(cfg)}"):
            # 单段素材上 `seg["out"]` 就可能**等于** `output.dir` ⇒ 这个 glob 会指到交付树
            # 自己头上。交付树归 `collect` 管（它会按 force 重建），这里一根手指都别碰。
            if d.resolve() == root.resolve():
                _log(f"[补救] 跳过（这是交付树本身，交给 collect）：{d}")
                continue
            if (d / "manifest.json").exists():
                _log(f"[补救] 清掉上一轮导出目录（可重建）：{d}")
                shutil.rmtree(d)
    _log("[补救] --- export + collect(force)：重建交付树 ---")
    rc = P.run_pipeline(cfg, only=["export", "collect"])
    if rc:
        return rc
    return verify(old_tree, root, plan)
