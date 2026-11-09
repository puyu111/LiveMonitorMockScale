"""按 JSON 配置依次执行管线步骤。

四个步骤，顺序固定（``steps.order`` 可调），每步都只是「把配置翻成 Namespace，
调用既有脚本的 ``run()``」：

    1. emoji_tables  建/刷新表情表（douyin / wechat / weibo / kuaishou_emoji_table）
    2. panels        定位或落盘面板矩形（locate_panels / batch_locate_panels / 手写）
    3. parse         核心：OCR 抽行 → 表情识别 → 解析 → 跨帧去重 → 出图
                      （parse_live_comments.run）
    4. draw          把某时刻附近的框画到一帧上（draw_boxes）

任一步返回非 0 即中止。``dry_run=True`` 时只打印「要做什么」，一行都不执行。
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config as C


def _log(msg: str) -> None:
    print(msg, flush=True)


def _to_cli(mod, args) -> str:
    """把 Namespace 还原成命令行（仅与默认值不同的项）—— --dry-run 用。"""
    base = C._base_args(mod)
    parts = [Path(mod.__file__).name]
    for k, v in sorted(vars(args).items()):
        if v == getattr(base, k, None):
            continue
        flag = "--" + k.replace("_", "-")
        if isinstance(v, bool):
            if v:
                parts.append(flag)
        elif v is not None:
            parts.append(f"{flag} {v if not isinstance(v, str) or ' ' not in v else repr(v)}")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# 步骤
# --------------------------------------------------------------------------- #

def _step_emoji_tables(cfg: dict, *, dry_run: bool = False) -> int:
    """建/刷新抖音、微信、微博、快手表情表（仅 build.enabled=true 的表）。"""
    built = 0
    for src, t in cfg["emoji"]["tables"].items():
        b = t["build"]
        if not b["enabled"]:
            _log(f"[emoji_tables] 跳过 {src}（build.enabled=false，假定表已在 {t['path']}）")
            continue
        _log(f"[emoji_tables] 建 {src} 表：mode={b['mode']} out={b['out']}")
        if dry_run:
            continue
        args = C.build_emoji_args(cfg, src)
        mod = _emoji_mod(src)
        rc = mod.run(args)
        if rc:
            return rc
        built += 1
    if built:
        _log(f"[emoji_tables] 完成 {built} 张表")
    return 0


def _emoji_mod(src: str):
    if src == "douyin":
        from . import douyin_emoji_table as m
    elif src == "wechat":
        from . import wechat_emoji_table as m
    elif src == "weibo":
        from . import weibo_emoji_table as m
    elif src == "kuaishou":
        from . import kuaishou_emoji_table as m
    else:
        raise C.ConfigError(
            f"不认识的表情表源：{src!r}（只有 douyin / wechat / weibo / kuaishou）")
    return m


def _step_panels(cfg: dict, *, seg: dict | None = None, dry_run: bool = False) -> int:
    """按 panels.mode 定位 / 落盘 / 复用面板矩形（产出待人工标注的面板文件）。"""
    pn = cfg["panels"]
    s = seg or C.segments(cfg)[0]
    mode, f = pn["mode"], s["panels_file"]

    if mode in ("auto", "reuse", "builtin"):
        _log(f"[panels] mode={mode}：面板交给 parse 步骤处理，本步无操作")
        return 0

    if mode == "batch":
        from . import batch_locate_panels as mod
        args = C.build_batch_args(cfg)
        _log(f"[panels] 稳定性研究 {args.count} 次 → {args.out}")
        if dry_run:
            _log("    " + _to_cli(mod, args))
            return 0
        return mod.main(args)

    if mode not in ("locate", "manual"):
        raise C.ConfigError(f"未知 panels.mode：{mode!r}")

    if mode == "manual":
        # manual ⇒ 配置是**唯一来源**：面板文件只是它的落盘副本。文件存在但与配置不一致
        # （改了 rect / platform）就按配置重写 —— 否则 parse 会一直吃旧矩形，而描框
        # （draw_boxes）画的又是配置值，两边对不上。overwrite=true 时无条件重写。
        entries = C.manual_entries(cfg, s)
        if Path(f).exists() and not pn["overwrite"]:
            drift = C.manifest_drift(entries, f)
            if not drift:
                _log(f"[panels] 面板文件与配置一致，保持不动：{f}")
                return 0
            _log(f"[panels] 配置的 panels.manual 与面板文件不一致 ⇒ 按配置重写：{f}")
            for d in drift[:6]:
                _log(f"          {d}")
            if len(drift) > 6:
                _log(f"          …共 {len(drift)} 处差异")
        _log(f"[panels] 手写面板 → {f}")
        if dry_run:
            _log("    " + json.dumps(entries, ensure_ascii=False))
            return 0
        dst = C.write_manifest(cfg, f, entries)
        _log(f"[panels] 已写出：{dst}")
        return 0

    # locate：已存在就不动（保住人工填的 platform）。
    if Path(f).exists() and not pn["overwrite"]:
        _log(f"[panels] 面板文件已存在，保持不动：{f}")
        _log("          （要重新生成：删掉它，或设 panels.overwrite=true）")
        return 0

    # locate：定位 → 写出「platform 待填」的面板文件
    video = s["video"]
    if not video:
        _log("[panels][error] mode=locate 需要视频（input.video 或 input.videos）")
        return 2
    au = pn["auto"]
    at = au["at"] if au["at"] is not None else s["start"]
    span = au["span"]
    if span is None:
        mf = cfg["input"]["max_frames"]
        span = cfg["input"]["interval"] * (mf - 1) if (mf and mf > 1) else 60.0
        span = max(1.0, min(float(span), 120.0))
    _log(f"[panels] 定位（method={au['method']} at={at}s span={span:g}s "
         f"{'不切分·整帧' if not au['split_x'] else 'split_x=' + str(au['split_x'])}）→ {f}")
    if dry_run:
        return 0
    plc = C._plc()
    rects, meta = plc.locate_panels_auto(
        video, au["method"], at=at, split_x=au["split_x"], frames=au["frames"],
        span=span, sub_width=au["sub_width"], pad_x=au["pad_x"], pad_y=au["pad_y"],
        pad_top=au["pad_top"], model=au["model"], quant=au["quant"],
        device=au["device"], verbose=True)
    if not rects:
        _log("[panels][error] 没定位到任何评论区。调 auto.split_x / auto.at / auto.span，"
             "或改用手写 mode=manual")
        return 1
    entries = {n: {"rect": [int(v) for v in r], "platform": None}
               for n, r in rects.items()}
    dst = C.write_manifest(cfg, f, entries, extra={
        "_source": {"video": video, "at": at, "method": au["method"],
                    "model": au["model"] if au["method"] != "ocr" else None,
                    "split_x": au["split_x"], "span": span},
        "_locate_meta": meta})
    _log(f"[panels] 已写出待标注的面板文件：{dst}")
    for n, r in rects.items():
        _log(f"          {n}: {r}")
    _log("          ⚠ 下一步：打开该文件，给每个面板填 platform，再重跑（本文件不会被覆盖）")
    return 0


def _step_parse(cfg: dict, *, seg: dict | None = None, dry_run: bool = False) -> int:
    """核心：OCR 抽行 → 表情识别 → 解析 → 跨帧去重 → 出图。"""
    plc = C._plc()
    a = C.build_parse_args(cfg, write=not dry_run, seg=seg)
    src = a.rows or a.video or a.text
    _log(f"[parse] 输入={src}")
    _log(f"[parse] 输出={a.out}  标注图={'否' if a.no_frames else '是'}"
         f"  面板={'自动(' + str(a.auto_panels) + ')' if a.auto_panels else (a.panels_file or '内置')}")
    if a.emoji_table:
        _log(f"[parse] 表情表：{a.emoji_table}")
    if a.badge_reread:
        _log("[parse] 等级标二次读数：开 —— 没读到药丸的行回原帧按药丸带补读"
             "（只补不改；parse.badge_reread 未写时按 account_key 绑定）")
    if dry_run:
        _log("    " + _to_cli(plc, a))
        return 0
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)
    return plc.run(a)


def _step_filter(cfg: dict, *, seg: dict | None = None, dry_run: bool = False) -> int:
    """评论 → 合格用户：用户名 ≥N 条正面评论（大模型判定，表情一起发）。"""
    f = cfg["filter"]
    if not f["enabled"]:
        _log("[filter] 跳过（filter.enabled=false；这一步要花钱调模型，按需开）")
        return 0
    from . import user_filter as mod
    args = C.build_filter_args(cfg, seg=seg)
    if (cfg["qualify"]["enabled"] and len(C.segments(cfg)) > 1
            and int(f["min_positive"]) != int(args.min_positive)):
        _log(f"[filter] ⚠ 本步阈值取 qualify.stage_min={args.min_positive}"
             f"（配置里 filter.min_positive 写的是 {f['min_positive']}）——"
             f"各段这一刀必须最松，合格与否由 qualify 步按**跨段合计**判")
    if not Path(args.comments).exists():
        _log(f"[filter][warn] 评论文件不存在：{args.comments} —— 跳过")
        return 0
    nlp = f["nlp"]
    key_src = ("配置里的 api_key" if nlp["api_key"]
               else f"环境变量 {nlp['api_key_env']}")
    _log(f"[filter] {Path(args.comments).name} → {args.out}")
    _log(f"[filter] ≥{args.min_positive} 条正面 · 模型 {args.model} · key 来自 {key_src}"
         f" · batch={args.batch_size} 并发={args.concurrency}"
         f"{' · limit=' + str(args.limit) if args.limit else ''}")
    if dry_run:
        _log("[filter]    （dry-run 只估算调用量；要真算调用数就单独跑："
             f"python livepipe/user_filter.py --comments {args.comments} --dry-run）")
        return 0
    return mod.run(args)


def _step_qualify(cfg: dict, *, dry_run: bool = False) -> int:
    """跨段判合格（全局步）：各段评论按**合计**算，合格用户回写成每段 users_qualified.jsonl。"""
    q = cfg["qualify"]
    if not q["enabled"]:
        _log("[qualify] 跳过（qualify.enabled=false；需要跨段合计判阈值时打开）")
        return 0
    from . import merge_segments as mod
    segs = C.segments(cfg)
    if not any((Path(s["out"]) / "users.jsonl").exists() for s in segs):
        _log("[qualify][warn] 哪一段都还没有 users.jsonl（filter 步的产物）—— 跳过"
             "（要判合格先跑：--only filter）")
        return 0
    _log(f"[qualify] {len(segs)} 段 · 合计阈值 ≥{q['min_positive']} 条正面"
         f"（各段 filter/export 用 {q['stage_min']}）")
    if dry_run:
        return 0
    return mod.qualify(cfg, segs)


def _step_export(cfg: dict, *, seg: dict | None = None, dry_run: bool = False) -> int:
    """⑥ 逐段出交付图：读该段合格用户表，按底帧画绿框、一人一个目录。"""
    e = cfg["export"]
    if not e["enabled"]:
        _log("[export] 跳过（export.enabled=false；要出交付图就打开）")
        return 0
    from . import export_user_images as mod
    args = C.build_export_args(cfg, seg=seg)
    if not Path(args.users).exists():
        _log(f"[export][warn] 合格用户表不存在：{args.users} —— 跳过"
             f"（先跑 filter；跨段判合格时还要先跑 qualify）")
        return 0
    _log(f"[export] {Path(args.users).name} → {Path(args.users).parent}"
         f"  base={args.base} labels={args.labels} ≥{args.min_positive}"
         f" scope={args.min_scope}{'  force' if args.force else ''}")
    if dry_run:
        _log("    " + _to_cli(mod, args))
        return 0
    return mod.run(args)


def _step_collect(cfg: dict, *, dry_run: bool = False) -> int:
    """拼最终交付树（全局步）：各段导出目录按昵称并成一个账号目录、图号跨段连续编号。"""
    c = cfg["collect"]
    if not c["enabled"]:
        _log("[collect] 跳过（collect.enabled=false；要拼交付树就打开）")
        return 0
    from . import merge_segments as mod
    segs = C.segments(cfg)
    merged = Path(cfg["output"]["dir"]) / "merged_users.json"
    if not merged.exists():
        _log(f"[collect][warn] 还没有 {merged.name}（qualify 步的产物）—— 跳过"
             "（要拼接交付树先跑：--only filter,qualify,export）")
        return 0
    _log(f"[collect] {len(segs)} 段 → 一棵交付树"
         f"（阈值 ≥{cfg['qualify']['min_positive']} 条正面）")
    if dry_run:
        return 0
    return mod.collect(cfg, segs)


def _step_draw(cfg: dict, *, seg: dict | None = None, dry_run: bool = False) -> int:
    """把某时刻附近的框画到一帧上（独立描框）。"""
    d = cfg["draw"]
    if not d["enabled"]:
        _log("[draw] 跳过（draw.enabled=false）")
        return 0
    s = seg or C.segments(cfg)[0]
    if not s["video"]:
        _log("[draw][warn] 需要底帧，但没有视频 —— 跳过")
        return 0
    from . import draw_boxes as mod
    args = C.build_draw_args(cfg, seg=seg)
    if not Path(args.json).exists():
        _log(f"[draw][warn] 框来源不存在：{args.json} —— 跳过")
        return 0
    _log(f"[draw] {Path(args.json).name} @ t={args.at}s → {args.out}")
    if dry_run:
        _log("    " + _to_cli(mod, args))
        return 0
    return mod.run(args)


_STEPS = {
    "emoji_tables": _step_emoji_tables,
    "panels": _step_panels,
    "parse": _step_parse,
    "filter": _step_filter,
    "qualify": _step_qualify,
    "export": _step_export,
    "collect": _step_collect,
    "draw": _step_draw,
}


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def run_pipeline(cfg: dict, *, only=None, skip=None, dry_run: bool = False) -> int:
    """按配置跑管线。``only``/``skip`` 覆盖配置里的 ``steps``。"""
    order = C.resolve(cfg)
    forced: list[str] = []
    if only:
        bad = [s for s in only if s not in C.STEPS]
        if bad:
            raise C.ConfigError(f"--only 含未知步骤 {bad}；可用：{list(C.STEPS)}")
        order = [s for s in order if s in set(only)]
        # 点名的步骤一律执行（配置里的 enabled=false 只是「默认这一趟别跑」）——
        # 否则 `--only filter` 会静默跳过 filter，然后 qualify/export/collect 全空转。
        forced = C.force_enable(cfg, only)
    if skip:
        order = [s for s in order if s not in set(skip)]

    name = cfg.get("name") or Path(cfg["_path"]).stem
    segs = C.segments(cfg)
    _log(f"=== livepipe: {name} ===")
    _log(f"    配置   {cfg['_path']}")
    _log(f"    基准目录 {cfg['base_dir']}")
    _log(f"    步骤   {' → '.join(order) or '(空)'}")
    if forced:
        _log(f"           （--only 点名 ⇒ 打开 {'、'.join(forced)}：配置里是 enabled=false）")
    if len(segs) == 1 and segs[0]["name"] is None:
        _log(f"    视频   {segs[0]['video']}")
    else:
        _log(f"    素材   {len(segs)} 段（{', '.join(str(s['name']) for s in segs)}）"
             f"，每段各跑一遍逐段步骤 → {cfg['output']['dir']}/<段名>/")
        for s in segs:
            _log(f"           {s['name']:<8} {Path(s['video']).name}"
                 f"{'  （已有 rows.jsonl ⇒ 复算）' if s['rows'] else ''}")

    for i, step in enumerate(order, 1):
        per_seg = step in C.PER_SEGMENT_STEPS and len(segs) > 1
        for s in (segs if per_seg else [None]):
            tag = f"{step} · {s['name']}" if per_seg else step
            head = f"--- [{i}/{len(order)}] {tag} " + "-" * max(0, 40 - len(tag))
            _log(head)
            # 全局步（emoji_tables/qualify/collect）不吃 seg：它们看的是整批产物
            rc = _STEPS[step](cfg, dry_run=dry_run, **({"seg": s} if per_seg else {}))
            if rc:
                _log(f"!!! 步骤 {tag} 返回 {rc}，中止")
                return rc
    _log(f"=== 完成（{'dry-run' if dry_run else '实际执行'}）===")
    return 0
