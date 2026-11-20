"""``livepipe.tools.verify_run.checks`` —— 从盘上核对一次 ``livepipe run`` 的产物是否完整。

判定口径：**按配置实际要跑的步骤**（``steps.order`` − ``steps.skip``，再叠加
``--only/--skip``）逐步骤看盘上产物。配置里 ``enabled=false`` 的步骤（filter / qualify /
export / collect / draw）默认不要求 —— 但 ``--only`` 点名会像 ``run`` 一样强制打开它们。

只读，不跑任何识别/模型/写盘；所有结论都来自文件存在性、行数、JSON 结构、manifest 计数
与时间戳。**硬错误**（缺文件/结构不对/计数对不上）退出码 1；**警告**（0 条评论、0 个用户、
文件偏旧、素材路径不可达）不影响退出码，除非 ``--strict``。
"""

from __future__ import annotations

from pathlib import Path

from ... import config as C
from .common import (
    LEVEL_ORDER,
    Report,
    account_key,
    compare_media_time,
    count_jsonl,
    creation_local_minutes,
    file_size,
    fmt_minute,
    load_panel_platform,
    parse_time_range,
    path_exists,
    path_isdir,
    platform_dir,
    probe_media,
    read_json,
    read_jsonl,
    seg_label,
    select_users,
    stale,
)

#: 交付图/标注图后缀（与 list_match.common.IMAGE_SUFFIXES 一致）。
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def effective_steps(cfg: dict, only=None, skip=None) -> list[str]:
    """与 ``pipeline.run_pipeline`` 同一套步骤消解（含配置里的 skip）。"""
    order = C.resolve(cfg)
    if only:
        wanted = set(only)
        order = [s for s in order if s in wanted]
    if skip:
        dropped = set(skip)
        order = [s for s in order if s not in dropped]
    return order


def step_enabled(cfg: dict, step: str, only=None) -> bool:
    """``run`` 的 ``--only`` 会强制打开 ``enabled=false`` 的步骤，这里照样算。"""
    path = C._STEP_SWITCH.get(step)
    if not path:
        return True
    node = cfg
    for k in path[:-1]:
        node = node.get(k) or {}
    return bool(node.get(path[-1])) or bool(only and step in only)


def _out(seg: dict) -> Path:
    return Path(seg["out"])


def _images_in(d: Path) -> list[Path]:
    if not path_isdir(d):
        return []
    try:
        return sorted(p for p in d.iterdir()
                      if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    except OSError:
        return []


def _details(bad: list[str], limit: int = 6) -> list[str]:
    return bad[:limit] + ([f"…共 {len(bad)} 条"] if len(bad) > limit else [])


# --------------------------------------------------------------------------- #
# 各步骤
# --------------------------------------------------------------------------- #

def _referenced_platforms(cfg: dict, segs: list[dict]) -> set[str]:
    """真正被面板用到的平台键（不是 ``platforms`` 里所有档案 —— DEFAULTS 永远带 douyin/wechat）。"""
    refs: set[str] = set()
    # auto.platforms 只在 auto/builtin/batch 下生效；reuse/locate/manual 吃的是面板文件。
    if cfg.get("panels", {}).get("mode") in ("auto", "builtin", "batch"):
        for v in (cfg.get("panels", {}).get("auto", {}).get("platforms") or {}).values():
            if v:
                refs.add(str(v))

    def walk(obj) -> None:
        if isinstance(obj, dict):
            p = obj.get("platform")
            if p:
                refs.add(str(p))
            for v in obj.values():
                walk(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                walk(v)

    walk(cfg.get("panels", {}).get("manual"))
    for seg in segs:
        for p in (Path(seg["panels_file"]), _out(seg) / "panels.resolved.json"):
            if not path_exists(p):
                continue
            doc, err = read_json(p)
            if err or not doc:
                continue
            ps = doc.get("panels") or doc
            refs |= {str(v.get("platform")) for v in ps.values()
                     if isinstance(v, dict) and v.get("platform")}
    return refs


def check_emoji(cfg: dict, report, steps: list[str], segs: list[dict]) -> None:
    # 纯文本输入不走面板/OCR，表情表与它无关。
    if cfg["input"].get("text") and not (cfg["input"].get("video")
                                        or cfg.get("_videos") or cfg["input"].get("rows")):
        return
    refs = _referenced_platforms(cfg, segs)
    used = {str((cfg.get("platforms") or {}).get(p, {}).get("emoji"))
            for p in refs if (cfg.get("platforms") or {}).get(p, {}).get("emoji")}
    for name, table in (cfg.get("emoji", {}).get("tables") or {}).items():
        build = bool((table.get("build") or {}).get("enabled"))
        if name not in used and not build:
            continue
        p = Path(table["path"])
        if not path_exists(p):
            level = "fail" if (build and "emoji_tables" in steps) else "warn"
            report.add("emoji_tables", level, name,
                       f"表情表不存在：{p}"
                       + ("（本步 build.enabled=true）" if build else "（先建表或改 path）"))
        elif file_size(p) <= 0:
            report.add("emoji_tables", "fail", name, f"表情表是空文件：{p}")
        else:
            report.add("emoji_tables", "ok", name,
                       f"{p.name}（{file_size(p) / 1024:.0f} KB）")


def check_panels(cfg: dict, report, segs: list[dict], steps: list[str]) -> None:
    mode = cfg["panels"]["mode"]
    if mode in ("auto", "builtin"):
        report.add("panels", "info", "全局",
                   f"mode={mode}：parse 阶段自行定位，没有待人工标注的面板文件")
        return
    if mode == "batch":
        d = Path(cfg["output"]["dir"]) / "panel_batch"
        if "panels" in steps and not path_isdir(d):
            report.add("panels", "warn", "全局",
                       f"mode=batch 的稳定性研究目录还没生成：{d}")
        else:
            report.add("panels", "ok", "全局", f"batch 产物目录：{d}")
        return

    for seg in segs:
        label = seg_label(seg)
        pf = Path(seg["panels_file"])
        if not path_exists(pf):
            report.add("panels", "fail", label, f"面板文件不存在：{pf}")
            continue
        doc, err = read_json(pf)
        if err:
            report.add("panels", "fail", label, f"面板文件读不了：{pf}（{err}）")
            continue
        panels = {k: v for k, v in (doc.get("panels") or doc).items()
                  if isinstance(v, dict)}
        if not panels:
            report.add("panels", "fail", label, f"面板文件里没有面板：{pf}")
            continue
        missing = [k for k, v in panels.items() if not v.get("platform")]
        rect_missing = [k for k, v in panels.items() if not v.get("rect")]
        if missing:
            # locate 的第一步本来就把 platform 留空等人工填；后面 parse 会因此拒跑。
            level = "fail" if "parse" in steps else "info"
            problem = f"还有 {len(missing)} 个面板没填 platform：{'、'.join(missing)}"
            if "parse" in steps:
                problem += "（parse 会因此拒跑）"
            if rect_missing:
                problem += f"；另外 {len(rect_missing)} 个面板缺 rect"
            report.add("panels", level, label, problem)
        elif rect_missing:
            report.add("panels", "fail", label,
                       f"{len(rect_missing)} 个面板缺 rect：{'、'.join(rect_missing)}")
        else:
            resolved = _out(seg) / "panels.resolved.json"
            note = "（parse 已展开 panels.resolved.json）" if path_exists(resolved) else ""
            report.add("panels", "ok", label,
                       f"{len(panels)} 个面板，platform 已填{note}")


def _comments_summary(report, step: str, label: str, out: Path, cfg: dict):
    """``comments.jsonl`` + ``summary.json`` 的共同检查。

    返回 ``(comments 数, summary, 问题列表, 是否硬错误)``。
    """
    comments_p = out / "comments.jsonl"
    n_comments, bad = count_jsonl(comments_p, validate=True)
    problems: list[str] = []
    fatal = False
    if n_comments < 0:
        problems.append(f"comments.jsonl 不存在：{comments_p}")
        return -1, None, problems, True
    if bad:
        problems.append(f"comments.jsonl 有 {len(bad)} 行坏 JSON")
        problems.extend(_details(bad))
        fatal = True
    summary_p = out / "summary.json"
    summary, err = read_json(summary_p)
    if err:
        # 纯文本输入（input.text）没有帧/几何，parse 本来就不写 summary。
        if (cfg["input"].get("text") and not cfg["input"].get("video")
                and not cfg["input"].get("videos")):
            report.add(step, "info", label, "纯文本输入：没有 summary.json（正常）")
        else:
            problems.append(f"summary.json 读不了：{summary_p}（{err}）")
            fatal = True
        return n_comments, None, problems, fatal
    declared = summary.get("unique_comments")
    if declared is None:
        problems.append("summary.json 缺 unique_comments")
        fatal = True
    elif int(declared) != n_comments:
        problems.append(f"summary 说 {declared} 条唯一评论，盘上 comments.jsonl 是 {n_comments} 行")
        fatal = True
    if int(summary.get("rows") or 0) <= 0 and n_comments > 0:
        problems.append(f"summary.rows={summary.get('rows')!r}，但有 {n_comments} 条评论")
        fatal = True
    if summary.get("frames_sampled") is None:
        # 旧版 parse 的 summary 没有这个键；只要 comments/rows/frames 都在，不影响完整性。
        problems.append("summary.json 缺 frames_sampled（旧版 parse 产物，可忽略）")
    return n_comments, summary, problems, fatal


def check_parse(cfg: dict, report, segs: list[dict], steps: list[str]) -> None:
    if "parse" not in steps:
        return
    if not cfg["output"]["write"]:
        report.add("parse", "info", "全局",
                   "output.write=false ⇒ parse 只打 stdout，不落盘；下游步骤会读到空产物")
        return

    for seg in segs:
        label = seg_label(seg)
        out = _out(seg)
        if not path_isdir(out):
            report.add("parse", "fail", label, f"段输出目录不存在：{out}")
            continue
        n_comments, summary, problems, fatal = _comments_summary(
            report, "parse", label, out, cfg)
        level = "fail" if fatal else "ok"
        text_only = bool(cfg["input"].get("text")) and not seg.get("video") \
            and not seg.get("rows")
        if n_comments == 0 and not problems:
            level = "warn"
            problems.append("0 条唯一评论（可能整场没人评论；若不是，先查 parse 日志）")

        # rows.jsonl（可回喂复算的那份）
        rows_note = ""
        rows_p = Path(seg["rows"]) if seg.get("rows") else out / "rows.jsonl"
        if path_exists(rows_p):
            size = file_size(rows_p)
            if size == 0:
                if cfg["output"].get("rows") or seg.get("rows"):
                    problems.append(f"rows.jsonl 是 0 字节（上次崩掉的残留，segments() 会当没有）：{rows_p}")
                    level = "fail"
                else:
                    problems.append(f"rows.jsonl 是 0 字节（output.rows=false，可忽略）：{rows_p}")
            else:
                n_rows, _ = count_jsonl(rows_p)
                rows_note = f" · rows {n_rows} 行"
                if stale(out / "comments.jsonl", rows_p):
                    problems.append("comments.jsonl 比 rows.jsonl 旧（可能只重跑了抽行、没复算）")
                    level = max(level, "warn", key=LEVEL_ORDER.get)
        elif cfg["output"].get("rows") and not text_only:
            problems.append(f"缺 rows.jsonl（output.rows=true）：{rows_p}")
            level = "fail"

        # frames/ 标注图
        frames_note = ""
        if text_only:
            frames_note = " · 纯文本输入无标注图"
        elif cfg["output"].get("frames"):
            fd = out / "frames"
            imgs = _images_in(fd)
            frames_note = f" · 标注图 {len(imgs)} 张"
            if n_comments > 0 and not imgs:
                problems.append(f"标注图目录为空：{fd}（output.frames=true；看 parse 日志是不是 --no-frames）")
                level = max(level, "warn", key=LEVEL_ORDER.get)
            # 不拿 summary.frames_drawn 对张数：同一 t 的几条评论共用一张图（文件名会撞），
            # 盘上张数只会 ≤ frames_drawn，不是缺失。
        else:
            frames_note = " · 标注图关闭"

        # panels.resolved.json（reuse/locate/manual 才有；纯文本输入不走面板）
        if cfg["panels"]["mode"] in ("reuse", "locate", "manual") and not text_only:
            rp = out / "panels.resolved.json"
            rdoc, rerr = read_json(rp)
            if rerr:
                problems.append(f"panels.resolved.json 读不了：{rp}（{rerr}）")
                level = "fail"
            else:
                ps = {k: v for k, v in (rdoc.get("panels") or rdoc).items()
                      if isinstance(v, dict)}
                no_plat = [k for k, v in ps.items() if not v.get("platform")]
                if no_plat:
                    problems.append(f"panels.resolved.json 里 {len(no_plat)} 个面板没有 platform")
                    level = "fail"

        # summary 记录的视频 vs 当前配置
        sv = (summary or {}).get("video")
        if sv and seg.get("video") and Path(sv) != Path(seg["video"]):
            problems.append(f"summary.video={sv} 与配置素材 {seg['video']} 不同（复算口径？）")
        if sv and not path_exists(Path(sv)):
            problems.append(f"素材路径不可达：{sv}（挪盘/未挂载不影响已落盘产物）")

        msg = f"唯一评论 {n_comments}{rows_note}{frames_note}"
        report.add("parse", level, label, msg, _details(problems))


def _map_video(path, video_map) -> str:
    """素材盘挪位置后，用 ``--video-map 旧前缀=新前缀`` 把路径映射回来。"""
    s = str(path or "")
    for old, new in video_map or []:
        if old and s.startswith(old):
            return str(new) + s[len(old):]
    return s


def check_media_time(cfg: dict, report, segs: list[dict], steps: list[str], *,
                     tolerance: float = 120.0, enabled: bool = True,
                     video_map=None) -> None:
    """视频文件名里的时间区间 × 文件实际时长/creation_time。

    认的是**段视频文件名**（``19：40-20：20.mp4``）—— 也就是 batch 命名里的「seg 时间」。
    裁剪副本（``dy_22-09-22-17_live.mp4``）没有区间，跳过；素材不可达也跳过（只提示）。
    """
    if "parse" not in steps or not enabled:
        return
    for seg in segs:
        label = seg_label(seg)
        video = _map_video(seg.get("video"), video_map)
        used_summary = False
        if not path_exists(video):
            summary, _ = read_json(_out(seg) / "summary.json")
            alt = _map_video((summary or {}).get("video"), video_map)
            if alt and path_exists(alt):
                video, used_summary = alt, True
            else:
                report.add("parse", "info", label,
                           f"视频时间：素材不可达，跳过核对（{video}）")
                continue
        rng = parse_time_range(Path(str(video)).stem)
        if not rng:
            report.add("parse", "info", label,
                       "视频时间：文件名里没有时间区间，跳过（裁剪副本/合并文件）")
            continue
        info, err = probe_media(Path(str(video)))
        if err:
            report.add("parse", "warn", label, f"视频时间：{err}", [str(video)])
            continue
        dur = info.get("duration")
        if dur is None:
            report.add("parse", "warn", label, "视频时间：ffprobe 读不到时长",
                       [str(video)])
            continue
        a, b = rng
        level, diff = compare_media_time(rng, dur, tolerance)
        msg = (f"视频 {dur / 60:.2f}min · 文件名 {fmt_minute(a)}-{fmt_minute(b)}"
               f" = {b - a}min · 差 {diff:+.1f}s")
        details: list[str] = []
        if used_summary:
            details.append(f"素材路径取自 summary.video：{video}")
        cmin = creation_local_minutes(info.get("creation_time"))
        if cmin is not None:
            nearest = min(abs(cmin - a), abs(cmin - b))
            details.append(f"creation_time {fmt_minute(cmin)}（本机时区）"
                           f" · 距区间端点最近 {nearest:.0f}min")
            if nearest > max(tolerance / 60 * 2, 10):
                details.append("creation_time 与文件名区间相差较大"
                               "（可能是复制/转码时间，仅供参考）")
        summary, _ = read_json(_out(seg) / "summary.json")
        if summary:
            ss = summary.get("start")
            if ss is not None and float(ss) != float(seg.get("start") or 0.0):
                details.append(f"summary.start={ss} 与配置 start={seg.get('start')} 不一致")
                level = max(level, "warn", key=LEVEL_ORDER.get)
            sd = summary.get("duration")
            if (seg.get("duration") is not None and sd is not None
                    and float(sd) != float(seg["duration"])):
                details.append(f"summary.duration={sd} 与配置 duration={seg['duration']} 不一致")
                level = max(level, "warn", key=LEVEL_ORDER.get)
        if level == "fail":
            details.append("实际时长明显短于文件名区间：可能少录了一段，或这个文件不是这一段")
        elif level == "warn" and diff > 0:
            details.append("实际时长明显长于文件名区间：常见于录尾带推荐页/花絮，"
                           "核对直播结束时间")
        report.add("parse", level, label, msg, details)


def check_filter(cfg: dict, report, segs: list[dict], steps: list[str], only=None) -> None:
    if "filter" not in steps or not step_enabled(cfg, "filter", only):
        return
    multi = len(segs) > 1
    q_on = bool(cfg["qualify"]["enabled"])
    threshold = (int(cfg["qualify"]["stage_min"]) if (q_on and multi)
                 else int(cfg["filter"]["min_positive"]))
    for seg in segs:
        label = seg_label(seg)
        out = _out(seg)
        comments_p = Path(cfg["filter"]["source"]) if cfg["filter"].get("source") \
            else out / "comments.jsonl"
        users_p = Path(cfg["filter"]["out"]) if cfg["filter"].get("out") \
            else out / "users.jsonl"
        n_comments, _ = count_jsonl(comments_p)
        if n_comments < 0:
            report.add("filter", "warn", label,
                       f"评论文件不存在，filter 会跳过：{comments_p}")
            continue
        if not path_exists(users_p):
            report.add("filter", "fail", label, f"users.jsonl 不存在：{users_p}")
            continue
        n_users, bad = count_jsonl(users_p, validate=True)
        if bad:
            report.add("filter", "fail", label,
                       f"users.jsonl 有 {len(bad)} 行坏 JSON", _details(bad))
            continue
        problems: list[str] = []
        level = "ok"
        if n_users == 0 and n_comments > 0:
            problems.append(f"0 个合格用户（评论 {n_comments} 条；可能确实没有 ≥{threshold} 条正面）")
            level = "warn"
        if n_users > 0:
            users, _ = read_jsonl(users_p)
            below = [u for u in users if int(u.get("n_positive") or 0) < threshold]
            if below:
                problems.append(f"{len(below)} 个用户的正面数低于本步阈值 {threshold}"
                                "（users.jsonl 可能是旧阈值产物，先重跑 filter）")
                level = "warn"
            cache = (Path(cfg["filter"]["cache"]) if cfg["filter"].get("cache")
                     else (Path(cfg["_judgments_cache"]) if cfg.get("_judgments_cache")
                           else comments_p.parent / "judgments.jsonl"))
            if not path_exists(cache):
                problems.append(f"判定缓存不存在：{cache}（过滤结果无法复核/复用）")
                level = max(level, "warn", key=LEVEL_ORDER.get)
            elif count_jsonl(cache)[0] <= 0:
                problems.append(f"判定缓存是空的：{cache}")
                level = max(level, "warn", key=LEVEL_ORDER.get)
        if stale(users_p, comments_p):
            problems.append("users.jsonl 比 comments.jsonl 旧（parse 重跑后没重跑 filter）")
            level = max(level, "warn", key=LEVEL_ORDER.get)
        report.add("filter", level, label,
                   f"合格用户 {n_users}（评论 {n_comments} 条，阈值 ≥{threshold}）",
                   _details(problems))


def check_qualify(cfg: dict, report, segs: list[dict], steps: list[str], only=None) -> None:
    if "qualify" not in steps or not step_enabled(cfg, "qualify", only):
        return
    out = Path(cfg["output"]["dir"])
    merged_p = out / "merged_users.json"
    if not path_exists(merged_p):
        report.add("qualify", "fail", "全局", f"merged_users.json 不存在：{merged_p}")
        return
    merged, err = read_json(merged_p)
    if err:
        report.add("qualify", "fail", "全局", f"merged_users.json 读不了：{err}")
        return
    users = merged.get("users") or []
    n_q = merged.get("n_qualified")
    problems: list[str] = []
    level = "ok"
    if n_q is None:
        problems.append("merged_users.json 缺 n_qualified")
        level = "fail"
    elif int(n_q) != len(users):
        problems.append(f"n_qualified={n_q}，但 users 有 {len(users)} 个")
        level = "fail"
    if int(merged.get("min_positive") or 0) != int(cfg["qualify"]["min_positive"]):
        problems.append(f"merged_users.min_positive={merged.get('min_positive')}，"
                        f"配置是 {cfg['qualify']['min_positive']}（阈值改过没重跑 qualify？）")
        level = max(level, "warn", key=LEVEL_ORDER.get)

    merged_keys = {account_key(u) for u in users}
    union: set[tuple[str, str]] = set()
    sums: dict[tuple[str, str], int] = {}
    for seg in segs:
        qp = _out(seg) / "users_qualified.jsonl"
        if not path_exists(qp):
            problems.append(f"{seg_label(seg)} 缺 users_qualified.jsonl：{qp}")
            level = "fail"
            continue
        rows, bad = read_jsonl(qp)
        if bad:
            problems.append(f"{seg_label(seg)} 的 users_qualified.jsonl 有坏行：{bad[0]}")
            level = "fail"
            continue
        for u in rows:
            k = account_key(u)
            union.add(k)
            sums[k] = sums.get(k, 0) + int(u.get("n_positive") or 0)
        if stale(merged_p, qp):
            problems.append(f"merged_users.json 比 {seg_label(seg)}/users_qualified.jsonl 旧")
            level = max(level, "warn", key=LEVEL_ORDER.get)
        up = _out(seg) / "users.jsonl"
        if path_exists(up) and stale(qp, up):
            problems.append(f"{seg_label(seg)}/users_qualified.jsonl 比 users.jsonl 旧")
            level = max(level, "warn", key=LEVEL_ORDER.get)

    missing = merged_keys - union
    extra = union - merged_keys
    if missing:
        sample = "、".join(f"{u}◆{lv}" if lv else u for u, lv in sorted(missing)[:3])
        problems.append(f"{len(missing)} 个合计合格账号在各段 users_qualified.jsonl 里找不到"
                        f"（如 {sample}）")
        level = "fail"
    if extra:
        problems.append(f"{len(extra)} 个账号在 users_qualified.jsonl 里、却不在 merged_users.json")
        level = "fail"
    merged_pos = {account_key(u): int(u.get("n_positive") or 0) for u in users}
    bad_sum = [k for k in merged_keys & union if sums.get(k, 0) != merged_pos.get(k)]
    if bad_sum:
        problems.append(f"{len(bad_sum)} 个账号的各段正面数之和与 merged_users 不一致")
        level = max(level, "warn", key=LEVEL_ORDER.get)
    report.add("qualify", level, "全局",
               f"合计合格 {len(users)} 个账号（阈值 ≥{cfg['qualify']['min_positive']}）",
               _details(problems))


def _export_settings(cfg: dict, segs: list[dict]) -> tuple[str, set[str], int, str]:
    e, q = cfg["export"], cfg["qualify"]
    labels = {s.strip() for s in str(e["labels"]).split(",") if s.strip()}
    min_pos = (int(e["min_positive"]) if e["min_positive"] is not None
               else (int(q["stage_min"]) if q["enabled"]
                     else int(cfg["filter"]["min_positive"])))
    tag = "_".join(sorted(labels))
    return tag, labels, min_pos, str(e.get("min_scope") or "platform")


def _export_users_path(cfg: dict, seg: dict) -> Path:
    if cfg["export"].get("users"):
        return Path(cfg["export"]["users"])
    qual = _out(seg) / "users_qualified.jsonl"
    if path_exists(qual) or cfg["qualify"]["enabled"]:
        return qual
    return _out(seg) / "users.jsonl"


def check_export(cfg: dict, report, segs: list[dict], steps: list[str], only=None) -> None:
    if "export" not in steps or not step_enabled(cfg, "export", only):
        return
    tag, labels, min_pos, scope = _export_settings(cfg, segs)
    for seg in segs:
        label = seg_label(seg)
        out = _out(seg)
        users_p = _export_users_path(cfg, seg)
        if not path_exists(users_p):
            report.add("export", "fail", label, f"待出图用户表不存在：{users_p}")
            continue
        users, bad = read_jsonl(users_p)
        if bad:
            report.add("export", "fail", label,
                       f"{users_p.name} 有坏行：{bad[0]}")
            continue
        panel_platform = load_panel_platform(out / "panels.resolved.json",
                                             Path(seg["panels_file"]))
        picked = select_users(users, panel_platform, labels, min_pos, scope)
        if not picked:
            report.add("export", "info", label,
                       f"没有可交付用户（{users_p.name}，标签 {sorted(labels)}，≥{min_pos} 条）")
            continue
        problems: list[str] = []
        level = "ok"
        counts = []
        for plat in sorted(picked):
            group = picked[plat]
            target = out / platform_dir(plat) / f"all_users_{tag}_{min_pos}张以上"
            counts.append(f"{platform_dir(plat)} {len(group)} 人")
            man_p = target / "manifest.json"
            if not path_isdir(target):
                problems.append(f"导出目录不存在：{target}")
                level = "fail"
                continue
            doc, err = read_json(man_p)
            if err:
                problems.append(f"manifest.json 读不了：{man_p}（{err}）")
                level = "fail"
                continue
            got = len(doc.get("users") or [])
            if got != len(group):
                problems.append(f"{platform_dir(plat)}：manifest 有 {got} 个用户，"
                                f"按当前用户表应出 {len(group)} 个")
                # 缺帧会让 export 主动丢掉整个用户（manifest.counts.missing_frames>0），算警告；
                # manifest 比预期多 ⇒ 用户表/阈值对不上，算错误。
                level = "fail" if got > len(group) else max(level, "warn", key=LEVEL_ORDER.get)
            counts_doc = doc.get("counts") or {}
            if int(counts_doc.get("missing_frames") or 0) > 0:
                problems.append(f"{platform_dir(plat)}：{counts_doc['missing_frames']} 条评论没取到帧")
                level = max(level, "warn", key=LEVEL_ORDER.get)
            img_total = 0
            for u in doc.get("users") or []:
                d = target / str(u.get("dir") or "")
                if not path_isdir(d):
                    problems.append(f"{platform_dir(plat)}：账号目录不存在 {u.get('dir')}")
                    level = "fail"
                    continue
                imgs = _images_in(d)
                want = int(u.get("count") or 0)
                img_total += len(imgs)
                if len(imgs) != want:
                    problems.append(f"{platform_dir(plat)}/{u.get('dir')}：manifest 写 {want} 张，盘上 {len(imgs)} 张")
                    level = "fail"
                for im in u.get("images") or []:
                    rel = im.get("rel")
                    if rel and not path_exists(target / str(rel)):
                        problems.append(f"{platform_dir(plat)}/{u.get('dir')}：缺图 {rel}")
                        level = "fail"
                        break
            if int(counts_doc.get("images") or 0) != img_total:
                problems.append(f"{platform_dir(plat)}：manifest 记 {counts_doc.get('images')} 张，盘上 {img_total} 张")
                level = "fail"
            if not path_exists(target / "正面账号.txt"):
                problems.append(f"{platform_dir(plat)}：缺 正面账号.txt")
                level = max(level, "warn", key=LEVEL_ORDER.get)
            if stale(man_p, users_p):
                problems.append(f"{platform_dir(plat)}：manifest 比 {users_p.name} 旧（用户表重跑后没重跑 export）")
                level = max(level, "warn", key=LEVEL_ORDER.get)
        report.add("export", level, label, " · ".join(counts), _details(problems))


def _find_stage_dirs(seg: dict, stage_name: str, platform_hint: str | None) -> list[Path]:
    """复刻 ``merge_segments.collect._stage_dirs``。"""
    base = _out(seg)
    pat = f"{platform_hint}/{stage_name}" if platform_hint else f"*/{stage_name}"
    try:
        return sorted(p for p in base.glob(pat) if path_exists(p / "manifest.json"))
    except OSError:
        return []


def check_collect(cfg: dict, report, segs: list[dict], steps: list[str], only=None) -> None:
    if "collect" not in steps or not step_enabled(cfg, "collect", only):
        return
    c, q = cfg["collect"], cfg["qualify"]
    stage = int(c["stage_min"]) if c.get("stage_min") is not None else int(q["stage_min"])
    stage_name = f"all_users_正面_{stage}张以上"
    n = int(q["min_positive"])

    platform_hint = c.get("platform_dir")
    plat_seen: list[str] = []
    export_manifests: list[Path] = []
    for seg in segs:
        for d in _find_stage_dirs(seg, stage_name, platform_hint):
            plat_seen.append(d.parent.name)
            export_manifests.append(d / "manifest.json")
    if len(set(plat_seen)) > 1:
        report.add("collect", "warn", "全局",
                   f"各段导出目录的平台名不一致（{'、'.join(sorted(set(plat_seen)))}），"
                   f"collect 按下标第一段取 {plat_seen[0]}")
    chosen = platform_hint or (plat_seen[0] if plat_seen else None)
    if c.get("out"):
        dst = Path(c["out"])
    elif chosen:
        dst = Path(cfg["output"]["dir"]) / chosen / f"all_users_正面_{n}张以上"
    else:
        report.add("collect", "fail", "全局",
                   "找不到导出目录，也没写 collect.out ⇒ 无法推断交付树位置"
                   "（先跑 export，或显式写 collect.out）")
        return

    man_p = dst / "manifest.json"
    if not path_isdir(dst):
        report.add("collect", "fail", "全局", f"交付树不存在：{dst}")
        return
    doc, err = read_json(man_p)
    if err:
        report.add("collect", "fail", "全局", f"交付树 manifest.json 读不了：{err}")
        return

    problems: list[str] = []
    level = "ok"
    users = doc.get("users") or []
    counts = doc.get("counts") or {}
    if int(counts.get("users") or 0) != len(users):
        problems.append(f"counts.users={counts.get('users')}，users 有 {len(users)} 个")
        level = "fail"
    img_total = 0
    for u in users:
        d = dst / str(u.get("dir") or "")
        if not path_isdir(d):
            problems.append(f"账号目录不存在：{u.get('dir')}")
            level = "fail"
            continue
        imgs = _images_in(d)
        want = int(u.get("count") or 0)
        img_total += len(imgs)
        if len(imgs) != want:
            problems.append(f"{u.get('dir')}：manifest 写 {want} 张，盘上 {len(imgs)} 张")
            level = "fail"
        for im in u.get("images") or []:
            rel = im.get("rel")
            if rel and not path_exists(dst / str(rel)):
                problems.append(f"{u.get('dir')}：缺图 {rel}")
                level = "fail"
                break
    if int(counts.get("images") or 0) != img_total:
        problems.append(f"counts.images={counts.get('images')}，盘上 {img_total} 张")
        level = "fail"
    if int(doc.get("min_positive") or 0) != n:
        problems.append(f"manifest.min_positive={doc.get('min_positive')}，配置是 {n}（阈值改过？）")
        level = "fail"
    if int(counts.get("mismatch") or 0) > 0:
        problems.append(f"{counts['mismatch']} 个账号的图数与合计不符（见 manifest.mismatch）")
        level = max(level, "warn", key=LEVEL_ORDER.get)
    if not path_exists(dst / "正面账号.txt"):
        problems.append("缺 正面账号.txt")
        level = max(level, "warn", key=LEVEL_ORDER.get)
    if not path_exists(dst / "README.md"):
        problems.append("缺 README.md")
        level = max(level, "warn", key=LEVEL_ORDER.get)

    # 与 qualify 的合计名单对账：合格账号必须都进了树。
    merged_p = Path(cfg["output"]["dir"]) / "merged_users.json"
    if path_exists(merged_p):
        merged, _ = read_json(merged_p)
        if merged:
            mkeys = {account_key(u) for u in (merged.get("users") or [])}
            tkeys = {account_key(u) for u in users}
            gone = mkeys - tkeys
            if gone:
                problems.append(f"{len(gone)} 个合计合格账号不在交付树里"
                                "（0 图或身份对不上；collect 日志有 [warn]）")
                level = "fail"
            extra = tkeys - mkeys
            if extra:
                problems.append(f"{len(extra)} 个交付树账号不在 merged_users.json 里")
                level = "fail"
    else:
        problems.append("没有 merged_users.json，无法核对合格账号是否都进了树")
        level = max(level, "warn", key=LEVEL_ORDER.get)

    if export_manifests:
        newest = max(export_manifests, key=lambda p: p.stat().st_mtime)
        if stale(man_p, newest):
            problems.append("交付树 manifest 比各段导出 manifest 旧（export 重跑后没重跑 collect）")
            level = max(level, "warn", key=LEVEL_ORDER.get)

    report.add("collect", level, "全局",
               f"{len(users)} 个账号 / {img_total} 张图 → {dst}", _details(problems))


def check_draw(cfg: dict, report, segs: list[dict], steps: list[str], only=None) -> None:
    if "draw" not in steps or not step_enabled(cfg, "draw", only):
        return
    d = cfg["draw"]
    src = Path(d["source"]) if d.get("source") else _out(segs[0]) / "comments.jsonl"
    if not path_exists(src):
        report.add("draw", "warn", "全局", f"框来源不存在，draw 会跳过：{src}")
        return
    dst = Path(d["out"]) if d.get("out") else _out(segs[0]) / f"draw_{float(d['at']):g}.jpg"
    if path_exists(dst) and file_size(dst) > 0:
        report.add("draw", "ok", "全局", f"{dst}")
    else:
        report.add("draw", "warn", "全局", f"描框图不存在：{dst}")


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def verify(cfg: dict, *, only=None, skip=None, media_time: bool = True,
           time_tolerance: float = 120.0, video_map=None) -> Report:
    """按配置核对盘上产物；返回 :class:`Report`（不打印、不写盘）。"""
    steps = effective_steps(cfg, only=only, skip=skip)
    segs = C.segments(cfg)
    report = Report(
        config=str(cfg.get("_path") or ""),
        name=str(cfg.get("name") or Path(str(cfg.get("_path") or "")).stem),
        output_dir=str(cfg["output"]["dir"]),
        steps=steps,
    )
    if not steps:
        report.add("config", "info", "全局", "步骤列表为空（steps.order/skip 或 --only/--skip 全滤掉了）")
        return report
    if not segs:
        report.add("config", "fail", "全局", "没有解析出任何段（input.video/videos/rows/text）")
        return report
    if not path_exists(Path(cfg["output"]["dir"])):
        report.add("config", "fail", "全局", f"输出目录不存在：{cfg['output']['dir']}")
    if not cfg["output"]["write"] and any(
            s in steps for s in ("filter", "qualify", "export", "collect")):
        report.add("config", "fail", "全局",
                   "output.write=false ⇒ parse 不落盘，但 filter/qualify/export/collect 已打开；"
                   "这些步骤会读到空产物")

    check_emoji(cfg, report, steps, segs)
    if "panels" in steps:
        check_panels(cfg, report, segs, steps)
    if "parse" in steps:
        check_parse(cfg, report, segs, steps)
        check_media_time(cfg, report, segs, steps,
                         tolerance=time_tolerance, enabled=media_time,
                         video_map=video_map)
    check_filter(cfg, report, segs, steps, only)
    check_qualify(cfg, report, segs, steps, only)
    check_export(cfg, report, segs, steps, only)
    check_collect(cfg, report, segs, steps, only)
    check_draw(cfg, report, segs, steps, only)
    return report
