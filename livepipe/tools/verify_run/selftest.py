"""``livepipe.tools.verify_run.selftest`` —— 用临时目录造一次「完整 run」的产物再故意弄坏。"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
from pathlib import Path

from ... import config as C
from .checks import effective_steps, step_enabled, verify
from .common import compare_media_time, parse_time_range

FAILS: list[str] = []
COUNT = 0


def _eq(name: str, got, want) -> None:
    global COUNT
    COUNT += 1
    if got != want:
        FAILS.append(f"{name}: got={got!r} want={want!r}")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _jsonl(path: Path, rows: list[dict]) -> None:
    _write(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def _user(n_positive: int = 2) -> dict:
    return {
        "username": "u1", "level": "", "n_positive": n_positive, "n_comments": n_positive,
        "comments": [{"label": "正面", "t": float(i + 1), "panel": "comments",
                      "roi": [1, 1, 20, 20], "content": "好"}
                     for i in range(n_positive)],
    }


def _config_doc(tmp: Path, video_names: tuple[str, str] = ("a.mp4", "b.mp4")) -> dict:
    return {
        "version": 1,
        "name": "verify-selftest",
        "base_dir": str(tmp),
        "input": {"videos": list(video_names), "segment_names": ["seg1", "seg2"]},
        "output": {"dir": "out"},
        "platforms": {"douyin": {"label": "抖音", "emoji": "douyin", "row_pitch": 50,
                                 "y_phase": 10, "x_text": 40}},
        "emoji": {"tables": {"douyin": {"path": "emoji.json",
                                        "build": {"enabled": False}}}},
        "panels": {"mode": "manual", "manual": [
            {"comments": {"rect": [0, 0, 100, 100], "platform": "douyin"}},
            {"comments": {"rect": [0, 0, 100, 100], "platform": "douyin"}},
        ]},
        "filter": {"enabled": True, "min_positive": 1},
        "qualify": {"enabled": True, "min_positive": 1, "stage_min": 1},
        "export": {"enabled": True, "labels": "正面", "min_positive": None,
                   "min_scope": "platform"},
        "collect": {"enabled": True, "force": True},
        "steps": {"order": list(C.STEPS), "skip": []},
    }


def _build_run(tmp: Path,
               video_names: tuple[str, str] = ("a.mp4", "b.mp4")) -> tuple[Path, dict]:
    """造一次「完整 run」的盘上产物（两段，各一个抖音账号）。"""
    paths = [tmp / name for name in video_names]
    for p in paths:
        p.write_bytes(b"x")
    (tmp / "emoji.json").write_text("{}", encoding="utf-8")
    (tmp / "out").mkdir(parents=True, exist_ok=True)
    (tmp / "out" / "judgments.jsonl").write_text(
        json.dumps({"k": "好", "label": "正面"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    doc = _config_doc(tmp, video_names)
    cfg_p = tmp / "cfg.json"
    cfg_p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    cfg = C.load(cfg_p, check_videos=False)

    for seg in C.segments(cfg):
        d = Path(seg["out"])
        _write(d / "panels.json", json.dumps(
            {"panels": {"comments": {"rect": [0, 0, 100, 100],
                                     "platform": "douyin"}}}, ensure_ascii=False))
        _write(d / "panels.resolved.json", json.dumps(
            {"panels": {"comments": {"rect": [0, 0, 100, 100], "platform": "douyin",
                                     "row_pitch": 50, "y_phase": 10, "x_text": 40}}},
            ensure_ascii=False))
        # 仿 parse 的写入顺序：rows 先、comments 后、summary 最后
        _jsonl(d / "rows.jsonl", [{"text": f"row{i}"} for i in range(10)])
        _jsonl(d / "comments.jsonl", [
            {"username": "u1", "content": "好", "t": 1.0, "panel": "comments",
             "roi": [1, 1, 20, 20]},
            {"username": "u1", "content": "好", "t": 2.0, "panel": "comments",
             "roi": [1, 1, 20, 20]},
        ])
        _write(d / "summary.json", json.dumps({
            "video": str(seg["video"]), "rows": 10, "frames_sampled": 2,
            "unique_comments": 2, "frames_drawn": 2,
            "sample_mode": "adaptive", "start": 0.0}, ensure_ascii=False))
        for i in (1, 2):
            (d / "frames").mkdir(parents=True, exist_ok=True)
            (d / "frames" / f"000{i}.0_roi.jpg").write_bytes(b"\xff\xd8\xff")
        # filter → qualify
        _jsonl(d / "users.jsonl", [_user()])
        _jsonl(d / "users_qualified.jsonl", [_user()])
        # export 树
        target = d / "抖音" / "all_users_正面_1张以上"
        _write(target / "manifest.json", json.dumps({
            "kind": "all_users_positive_images",
            "counts": {"users": 1, "images": 2, "missing_frames": 0},
            "users": [{"username": "u1", "level": "", "dir": "u1-2", "count": 2,
                       "images": [{"rel": "u1-2/1.jpg"}, {"rel": "u1-2/2.jpg"}]}],
        }, ensure_ascii=False))
        _write(target / "正面账号.txt", "u1\n")
        for i in (1, 2):
            (target / "u1-2").mkdir(parents=True, exist_ok=True)
            (target / "u1-2" / f"{i}.jpg").write_bytes(b"\xff\xd8\xff")

    out = Path(cfg["output"]["dir"])
    _write(out / "merged_users.json", json.dumps({
        "kind": "merged_users", "min_positive": 1, "stage_min": 1,
        "n_qualified": 1, "segments": ["seg1", "seg2"],
        "users": [{"username": "u1", "level": "", "n_positive": 4,
                   "n_comments": 4, "segs": {"seg1": 2, "seg2": 2}}],
    }, ensure_ascii=False))
    tree = out / "抖音" / "all_users_正面_1张以上"
    _write(tree / "manifest.json", json.dumps({
        "kind": "all_users_positive_images_merged_segments",
        "platform_dir": "抖音", "labels": ["正面"], "min_positive": 1,
        "min_scope": "platform", "stage_min": 1,
        "counts": {"users": 1, "images": 2, "mismatch": 0},
        "users": [{"username": "u1", "level": "", "dir": "u1-4", "count": 2,
                   "images": [{"rel": "u1-4/1.jpg"}, {"rel": "u1-4/2.jpg"}]}],
        "mismatch": [],
    }, ensure_ascii=False))
    _write(tree / "正面账号.txt", "u1\n")
    _write(tree / "README.md", "# 交付说明\n")
    for i in (1, 2):
        (tree / "u1-4").mkdir(parents=True, exist_ok=True)
        (tree / "u1-4" / f"{i}.jpg").write_bytes(b"\xff\xd8\xff")
    return cfg_p, cfg


def _fresh(video_names: tuple[str, str] = ("a.mp4", "b.mp4")):
    td = tempfile.TemporaryDirectory()
    cfg_p, cfg = _build_run(Path(td.name), video_names)
    return td, cfg_p, cfg


def selftest() -> int:
    global FAILS, COUNT
    FAILS = []
    COUNT = 0

    # ---- 0 · 纯函数：步骤消解 ----
    with tempfile.TemporaryDirectory() as td:
        p, cfg = _build_run(Path(td))
        _eq("effective_steps 默认含八步", effective_steps(cfg),
            list(C.STEPS))
        _eq("--only parse", effective_steps(cfg, only=["parse"]), ["parse"])
        _eq("--skip collect", "collect" in effective_steps(cfg, skip=["collect"]), False)
        cfg2 = dict(cfg)
        cfg2["filter"] = dict(cfg["filter"], enabled=False)
        _eq("enabled=false 默认关", step_enabled(cfg2, "filter"), False)
        _eq("--only 强制打开", step_enabled(cfg2, "filter", only=["filter"]), True)

    # ---- 0b · 视频时间解析 / 比较（纯函数）----
    _eq("区间 19:40-20:20", parse_time_range("19：40-20：20"), (1180, 1220))
    _eq("区间 09.09-11.57",
        parse_time_range("9.9抖音丨奇瑞汽车丨09.09-11.57"), (549, 717))
    _eq("区间 16:00-17:25（空格）", parse_time_range("16： 00-17 25"), (960, 1045))
    _eq("区间 16:011-19:00（多一位）", parse_time_range("16：011-19：00"), (971, 1140))
    _eq("区间 12:29-15-19（终点连字符）", parse_time_range("12：29-15-19"), (749, 919))
    _eq("裁剪副本无区间", parse_time_range("dy_22-09-22-17_live"), None)
    _eq("时长偏短 ⇒ fail",
        compare_media_time((1180, 1220), 1800.0, 120.0)[0], "fail")
    _eq("时长偏长 ⇒ warn",
        compare_media_time((1180, 1220), 3180.0, 120.0)[0], "warn")
    _eq("容差内 ⇒ ok",
        compare_media_time((1180, 1220), 2415.8, 120.0)[0], "ok")

    # ---- 0c · 视频时间 × seg1：文件名 40min，实际 30min ⇒ fail ----
    td, cfg_p, cfg = _fresh(("19：40-20：20.mp4", "20：20-20：58.mp4"))
    with td:
        import livepipe.tools.verify_run.checks as CK
        orig = CK.probe_media
        CK.probe_media = lambda p, **kw: ({
            "duration": 1800.0 if "19：40-20：20" in str(p) else 2280.0,
            "creation_time": None}, None)
        try:
            rep = verify(cfg, only=["parse"])
            rep_no = verify(cfg, only=["parse"], media_time=False)
        finally:
            CK.probe_media = orig
        bad = [c for c in rep.checks if c.step == "parse" and c.target == "seg1"]
        good = [c for c in rep.checks if c.step == "parse" and c.target == "seg2"]
        _eq("seg1 视频时间 fail", any(c.level == "fail" for c in bad), True)
        _eq("seg2 视频时间 ok",
            all(c.level in ("ok", "info") for c in good), True)
        _eq("--no-media-time 不产时间检查",
            any("视频 " in c.message for c in rep_no.checks), False)

    # ---- 1 · 完整 run ⇒ 0 错误 0 警告 ----
    td, cfg_p, cfg = _fresh()
    with td:
        rep = verify(cfg)
        _eq("完整 run ok", rep.ok, True)
        _eq("完整 run 无错误", len(rep.errors), 0)
        _eq("完整 run 无警告", len(rep.warnings), 0)
        _eq("步骤齐全", "collect" in rep.steps and "parse" in rep.steps, True)

    # ---- 2 · 只跑 parse ⇒ 不要求下游产物 ----
    td, cfg_p, cfg = _fresh()
    with td:
        out = Path(cfg["output"]["dir"])
        for seg in C.segments(cfg):
            for name in ("users.jsonl", "users_qualified.jsonl"):
                (Path(seg["out"]) / name).unlink()
        (out / "merged_users.json").unlink()
        rep = verify(cfg, only=["parse"])
        _eq("--only parse ok", rep.ok, True)
        _eq("--only parse 只核 parse", rep.steps, ["parse"])

    # ---- 3 · 缺 comments.jsonl ----
    td, cfg_p, cfg = _fresh()
    with td:
        (Path(C.segments(cfg)[0]["out"]) / "comments.jsonl").unlink()
        rep = verify(cfg)
        _eq("缺 comments ⇒ fail", any(c.step == "parse" and c.level == "fail"
                                       for c in rep.checks), True)

    # ---- 4 · 0 字节 rows.jsonl（已知崩溃残留）----
    td, cfg_p, cfg = _fresh()
    with td:
        (Path(C.segments(cfg)[1]["out"]) / "rows.jsonl").write_bytes(b"")
        rep = verify(cfg)
        _eq("0 字节 rows ⇒ fail",
            any("0 字节" in d for c in rep.errors for d in c.details), True)

    # ---- 5 · 缺某段 users_qualified.jsonl ----
    td, cfg_p, cfg = _fresh()
    with td:
        (Path(C.segments(cfg)[1]["out"]) / "users_qualified.jsonl").unlink()
        rep = verify(cfg)
        _eq("缺 users_qualified ⇒ fail", any(c.step == "qualify" and c.level == "fail"
                                              for c in rep.checks), True)

    # ---- 6 · 交付树缺图 ----
    td, cfg_p, cfg = _fresh()
    with td:
        tree = Path(cfg["output"]["dir"]) / "抖音" / "all_users_正面_1张以上"
        (tree / "u1-4" / "1.jpg").unlink()
        rep = verify(cfg)
        _eq("交付树缺图 ⇒ fail", any(c.step == "collect" and c.level == "fail"
                                     for c in rep.checks), True)

    # ---- 7 · 合格账号没进树 ----
    td, cfg_p, cfg = _fresh()
    with td:
        mp = Path(cfg["output"]["dir"]) / "merged_users.json"
        doc = json.loads(mp.read_text(encoding="utf-8"))
        doc["n_qualified"] = 2
        doc["users"].append({"username": "u2", "level": "", "n_positive": 1,
                             "n_comments": 1, "segs": {"seg1": 1}})
        mp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        rep = verify(cfg)
        _eq("合格账号不在树里 ⇒ fail",
            any("不在交付树" in d for c in rep.errors for d in c.details), True)

    # ---- 8 · summary 条数不一致 ----
    td, cfg_p, cfg = _fresh()
    with td:
        sp = Path(C.segments(cfg)[0]["out"]) / "summary.json"
        doc = json.loads(sp.read_text(encoding="utf-8"))
        doc["unique_comments"] = 99
        sp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        rep = verify(cfg)
        _eq("summary 与盘上不一致 ⇒ fail",
            any("99" in d for c in rep.errors for d in c.details), True)

    # ---- 9 · 坏 JSON 行 ----
    td, cfg_p, cfg = _fresh()
    with td:
        cp = Path(C.segments(cfg)[0]["out"]) / "comments.jsonl"
        with cp.open("a", encoding="utf-8") as f:
            f.write("{not json}\n")
        rep = verify(cfg)
        _eq("comments 坏行 ⇒ fail", any("坏 JSON" in d for c in rep.errors
                                         for d in c.details), True)

    # ---- 10 · 警告 + --strict 退出码 ----
    td, cfg_p, cfg = _fresh()
    with td:
        fd = Path(C.segments(cfg)[0]["out"]) / "frames"
        for p in fd.glob("*.jpg"):
            p.unlink()
        rep = verify(cfg, only=["parse"])
        _eq("缺标注图只是警告", (rep.ok, len(rep.warnings) > 0), (True, True))
        from .cli import main as cli_main
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_plain = cli_main([str(cfg_p), "--only", "parse"])
            rc_strict = cli_main([str(cfg_p), "--only", "parse", "--strict"])
        _eq("警告默认退出 0", rc_plain, 0)
        _eq("--strict 警告退出 1", rc_strict, 1)

    # ---- 11 · --json 是合法 JSON ----
    td, cfg_p, cfg = _fresh()
    with td:
        from .cli import main as cli_main
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli_main([str(cfg_p), "--json"])
        doc = json.loads(buf.getvalue())
        _eq("--json 退出 0", rc, 0)
        _eq("--json kind", doc.get("kind"), "livepipe_run_verify")
        _eq("--json ok", doc.get("ok"), True)

    if FAILS:
        for msg in FAILS:
            print(f"  ✘ {msg}")
        print(f"自检 {len(FAILS)} 项失败")
        return 1
    print(f"自检 {COUNT} 项全过 ✅")
    return 0
