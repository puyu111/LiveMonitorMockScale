"""``manifest`` 的离线自检：临时目录造假 OSS 树 + 假配置，不碰真实挂载点。

对外两个入口：

- ``run(h)`` —— 供聚合自检 ``selftest.py`` 调用，``h`` 只需有
  ``eq(name, got, want)`` / ``raises(name, exc, fn)``。
- ``selftest()`` —— 独立跑，打印结果并返回 0/1。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import manifest as M

#: 假 ffprobe 的时长表（按文件名）。真实测试不调用 ffprobe。
_DURATIONS = {
    "one.mp4": 10.0,
    "two.mp4": 20.0,
    "dup1.mp4": 100.0,
    "dup2.mp4": 100.2,
    "dup_local.mp4": 100.2,
    "raw.mp4": 30.0,
    "seg_a.mp4": 10.0,
    "dy_qq_16-31-17-00_live.mp4": 5455.716,
}


def _fake_probe(path) -> float | None:
    return _DURATIONS.get(Path(path).name)


class _Fixture:
    """临时目录里的假 OSS 树与假素材盘。"""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.oss = self.root / "oss"
        self.disk = self.root / "disk"
        self.oss.mkdir(parents=True, exist_ok=True)
        self.disk.mkdir(parents=True, exist_ok=True)

    def touch(self, path: str | Path, size: int) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"x" * size)
        return p


def _doc(base_dir: Path, *, videos=None, video=None,
         start=None, duration=None) -> dict:
    inp: dict = {}
    if video is not None:
        inp["video"] = str(video)
    else:
        inp["videos"] = [str(v) for v in (videos or [])]
    if start is not None:
        inp["start"] = start
    if duration is not None:
        inp["duration"] = duration
    return {
        "version": 1,
        "name": "manifest-selftest",
        "base_dir": str(base_dir),
        "input": inp,
        "output": {"dir": str(base_dir / "out")},
        "panels": {"mode": "auto"},
        "emoji": {"enabled": False},
    }


def _write_json(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def run(h) -> None:
    """跑全部 manifest 用例；``h`` 为外部/本地 Harness。"""
    original = M.probe_duration
    M.probe_duration = _fake_probe
    try:
        with tempfile.TemporaryDirectory() as td:
            _run_cases(h, Path(td))
    finally:
        M.probe_duration = original


def _run_cases(h, root: Path) -> None:
    fx = _Fixture(root)

    # ---- 假 OSS 树（只按视频扩展名建索引；notes.txt 用来证明非视频被忽略）----
    fx.touch(fx.oss / "batch/a/one.mp4", 1000)
    fx.touch(fx.oss / "batch/a/two.mp4", 2000)
    fx.touch(fx.oss / "batch/b/dup1.mp4", 3000)
    fx.touch(fx.oss / "batch/b/dup2.mp4", 3000)
    fx.touch(fx.oss / "batch/c/raw.mp4", 4000)
    fx.touch(fx.oss / "batch/a/notes.txt", 1000)   # 与 one.mp4 同尺寸，但不是视频
    fx.touch(fx.oss / "batch/z/zero.mp4", 0)       # 0 字节，索引应跳过
    fx.touch(fx.oss / "batch/z/SVID_20261001_baiduyun.p.mp4", 7777)  # 临时名

    # OSS 索引：跳过 0 字节与下载临时名
    idx = M._build_oss_index(fx.oss)
    h.eq("index 跳过 0 字节", 0 in idx, False)
    h.eq("index 跳过 baiduyun 临时名",
         any("baiduyun" in e["rel"] for es in idx.values() for e in es), False)
    h.eq("index 保留正常视频",
         any(e["rel"].endswith("one.mp4") for es in idx.values() for e in es),
         True)
    h.eq("index 时长惰性（不跑 ffprobe）",
         all(e["duration"] is None for es in idx.values() for e in es), True)
    dup1 = next(e for es in idx.values() for e in es
                if e["rel"].endswith("dup1.mp4"))
    h.eq("index 候选初始 duration", dup1["duration"], None)
    h.eq("candidate_duration 现探", M._candidate_duration(dup1), 100.0)
    h.eq("candidate_duration 写回缓存", dup1["duration"], 100.0)

    # ---- 假素材盘 ----
    fx.touch(fx.disk / "seg_a.mp4", 1000)
    fx.touch(fx.disk / "dup_local.mp4", 3000)
    fx.touch(fx.disk / "dup_local_none.mp4", 3000)
    fx.touch(fx.disk / "trim/_live/clip.mp4", 5000)
    fx.touch(fx.disk / "trim/clip_live.mp4", 5001)
    fx.touch(fx.disk / "out/media/clip_live_extra.mp4", 5002)
    fx.touch(fx.disk / "trim2/_live/clip_0-2854.mp4", 6000)
    fx.touch(fx.disk / "trim3/dy_qq_16-31-17-00_live.mp4", 7000)

    # ================================================================== #
    # 1. 整段映射 + schema + 同尺寸多候选按时长选
    # ================================================================== #
    audit_cfg = root / "audit.json"
    _write_json(audit_cfg, _doc(fx.disk, videos=[
        fx.disk / "seg_a.mp4",
        fx.disk / "dup_local.mp4",
        fx.disk / "trim/_live/clip.mp4",
        fx.disk / "missing.mp4",
    ]))
    man = M.build_config_manifest(audit_cfg, fx.oss, disk_root=fx.disk)
    segs = man["segments"]

    h.eq("version", man["version"], M.MANIFEST_VERSION)
    h.eq("top_keys", set(man), {"version", "config", "generated_from", "segments"})
    h.eq("seg_keys", set(segs[0]), {
        "seg", "disk", "oss", "trimmed", "source_oss", "oss_missing",
        "bytes", "duration_s", "trim_s"})
    h.eq("config_abs", man["config"], str(audit_cfg))
    h.eq("generated_keys", set(man["generated_from"]),
         {"config", "oss_root", "disk_root", "generated_at"})
    h.eq("generated_oss_root", man["generated_from"]["oss_root"], str(fx.oss))
    h.eq("generated_disk_root", man["generated_from"]["disk_root"], str(fx.disk))
    h.eq("generated_at_nonempty", bool(man["generated_from"]["generated_at"]), True)

    h.eq("seg1_name", segs[0]["seg"], "seg1")
    h.eq("seg1_disk_rel", segs[0]["disk"], "seg_a.mp4")
    h.eq("unique_hit", segs[0]["oss"], str(fx.oss / "batch/a/one.mp4"))
    h.eq("unique_bytes", segs[0]["bytes"], 1000)
    h.eq("unique_duration", segs[0]["duration_s"], 10.0)
    h.eq("unique_trimmed", segs[0]["trimmed"], False)
    h.eq("unique_source_oss", segs[0]["source_oss"], None)

    h.eq("duration_pref_hit", segs[1]["oss"], str(fx.oss / "batch/b/dup2.mp4"))
    h.eq("duration_pref_value", segs[1]["duration_s"], 100.2)

    h.eq("missing_oss", segs[3]["oss"], None)
    h.eq("missing_bytes", segs[3]["bytes"], -1)

    # ================================================================== #
    # 2. audit 计数：refs 4 / full 2 / trimmed 1 / missing 1 / local 1 / failed 0
    #    missing 含“非裁尾、oss 为空”的全部段（含本地不可用段）；
    #    local_missing 作为独立诊断；ok 要求两者都为 0（严格门禁）。
    # ================================================================== #
    audit = M.audit_configs([audit_cfg], fx.oss)
    h.eq("audit_configs", audit["configs"], 1)
    h.eq("audit_refs", audit["refs"], 4)
    h.eq("audit_full_mapped", audit["full_mapped"], 2)
    h.eq("audit_trimmed", audit["trimmed"], 1)
    h.eq("audit_missing", audit["missing"], 1)
    h.eq("audit_local_missing", audit["local_missing"], 1)
    h.eq("audit_failed_configs", audit["failed_configs"], 0)
    h.eq("audit_details_len", len(audit["details"]), 1)
    h.eq("audit_detail_ok", audit["details"][0]["ok"], False)
    h.eq("audit_detail_error", audit["details"][0]["error"], None)
    h.eq("audit_detail_local_missing",
         audit["details"][0]["local_missing"], 1)
    h.eq("audit_detail_segments", len(audit["details"][0]["segments"]), 4)

    # ================================================================== #
    # 3. 裁尾三种写法 + trim_s
    # ================================================================== #
    trim_cfg = root / "trim.json"
    _write_json(trim_cfg, _doc(
        fx.disk,
        videos=[
            fx.disk / "trim/_live/clip.mp4",          # /_live/
            fx.disk / "trim/clip_live.mp4",           # *_live.mp4
            fx.disk / "out/media/clip_live_extra.mp4",  # /out/ + /media/ + _live
        ],
        start=0, duration=2854.4))
    tsegs = M.build_config_manifest(trim_cfg, fx.oss)["segments"]
    h.eq("trim_slash_live", tsegs[0]["trimmed"], True)
    h.eq("trim_slash_live_oss", tsegs[0]["oss"], None)
    h.eq("trim_basename_live", tsegs[1]["trimmed"], True)
    h.eq("trim_out_media_live", tsegs[2]["trimmed"], True)
    h.eq("trim_source_oss", tsegs[0]["source_oss"], None)
    h.eq("trim_s_value", tsegs[0]["trim_s"], [0.0, 2854.4])
    h.eq("trim_s_all_three", [s["trim_s"] for s in tsegs],
         [[0.0, 2854.4]] * 3)
    h.eq("trim_bytes", tsegs[0]["bytes"], 5000)

    # ================================================================== #
    # 3b. 裁尾区间从 _live 文件名回退解析；source-map 才填 source_oss
    # ================================================================== #
    live_cfg = root / "trim-name.json"
    _write_json(live_cfg, _doc(
        fx.disk, video=fx.disk / "trim2/_live/clip_0-2854.mp4"))
    live_seg = M.build_config_manifest(live_cfg, fx.oss)["segments"][0]
    h.eq("trim_name_trimmed", live_seg["trimmed"], True)
    h.eq("trim_name_trim_s", live_seg["trim_s"], [0.0, 2854.0])
    h.eq("trim_name_source_none", live_seg["source_oss"], None)

    source_map = {"clip_0-2854.mp4": str(fx.oss / "batch/a/one.mp4")}
    mapped_seg = M.build_config_manifest(
        live_cfg, fx.oss, source_map=source_map)["segments"][0]
    h.eq("trim_source_map", mapped_seg["source_oss"],
         str(fx.oss / "batch/a/one.mp4"))

    # 3c. 配置无 duration、文件名也无 _<start>-<end>：用探到的时长回退
    #     10-19 风格 `dy_qq_16-31-17-00_live.mp4` → [0, 5455.716]
    fallback_cfg = root / "trim-fallback.json"
    _write_json(fallback_cfg, _doc(
        fx.disk, video=fx.disk / "trim3/dy_qq_16-31-17-00_live.mp4"))
    fb = M.build_config_manifest(fallback_cfg, fx.oss)["segments"][0]
    h.eq("trim_fallback_trimmed", fb["trimmed"], True)
    h.eq("trim_fallback_duration", fb["duration_s"], 5455.716)
    h.eq("trim_fallback_trim_s", fb["trim_s"], [0.0, 5455.716])
    h.eq("trim_fallback_oss", fb["oss"], None)

    fallback_start_cfg = root / "trim-fallback-start.json"
    _write_json(fallback_start_cfg, _doc(
        fx.disk, video=fx.disk / "trim3/dy_qq_16-31-17-00_live.mp4", start=10))
    fb_start = M.build_config_manifest(
        fallback_start_cfg, fx.oss)["segments"][0]
    h.eq("trim_fallback_start_trim_s", fb_start["trim_s"], [10.0, 5465.716])

    # ================================================================== #
    # 4. 配置本身已指向 OSS
    # ================================================================== #
    raw_cfg = root / "raw.json"
    _write_json(raw_cfg, _doc(fx.disk, video=fx.oss / "batch/c/raw.mp4"))
    rseg = M.build_config_manifest(raw_cfg, fx.oss)["segments"][0]
    h.eq("oss_raw_hit", rseg["oss"], str(fx.oss / "batch/c/raw.mp4"))
    h.eq("oss_raw_trimmed", rseg["trimmed"], False)
    h.eq("oss_raw_bytes", rseg["bytes"], 4000)
    h.eq("oss_raw_duration", rseg["duration_s"], 30.0)
    h.eq("oss_raw_disk_abs", rseg["disk"], str(fx.oss / "batch/c/raw.mp4"))

    # 4b. 配置指向 oss_root 下但对象不存在：不能当作已映射
    ghost_cfg = root / "ghost.json"
    ghost_path = fx.oss / "batch/c/nope.mp4"
    _write_json(ghost_cfg, _doc(fx.disk, video=ghost_path))
    ghost = M.build_config_manifest(ghost_cfg, fx.oss)["segments"][0]
    h.eq("oss_ghost_oss", ghost["oss"], None)
    h.eq("oss_ghost_bytes", ghost["bytes"], -1)
    h.eq("oss_ghost_flag", ghost.get("oss_missing"), True)
    ghost_audit = M.audit_configs([ghost_cfg], fx.oss)
    h.eq("oss_ghost_missing", ghost_audit["missing"], 1)
    h.eq("oss_ghost_local_missing", ghost_audit["local_missing"], 1)

    # 4c. 批量清单：OSS 索引只建一次（2 份配置 → 1 次索引）
    original_index = M._build_oss_index
    builds: list[int] = []

    def counting_index(root_path):
        builds.append(1)
        return original_index(root_path)

    M._build_oss_index = counting_index
    try:
        batch, errors = M.build_config_manifests(
            [audit_cfg, trim_cfg], fx.oss)
    finally:
        M._build_oss_index = original_index
    h.eq("batch_index_builds", len(builds), 1)
    h.eq("batch_manifests", len(batch), 2)
    h.eq("batch_errors", errors, [])
    h.eq("batch_cfg", batch[0]["config"], str(audit_cfg))

    # ================================================================== #
    # 5. JSONC 兜底（故意让 config.load 因 panels.mode 失败）
    # ================================================================== #
    jsonc_cfg = root / "fallback.jsonc"
    jsonc_cfg.write_text(
        "// 顶部注释\n"
        "{\n"
        '  "version": 1,\n'
        f'  "base_dir": {json.dumps(str(fx.disk))},\n'
        '  "input": {\n'
        f'    "videos": [{json.dumps(str(fx.disk / "seg_a.mp4"))}], // 行尾注释\n'
        '    "segment_names": ["开场"],\n'
        '    "start": 0,\n'
        '    "duration": 10,\n'
        '  },\n'
        '  "panels": {"mode": "bogus"} /* 故意非法，逼 load 失败 */\n'
        "}\n",
        encoding="utf-8")
    jsegs = M.build_config_manifest(jsonc_cfg, fx.oss)["segments"]
    h.eq("jsonc_segments", len(jsegs), 1)
    h.eq("jsonc_segment_name", jsegs[0]["seg"], "开场")
    h.eq("jsonc_hit", jsegs[0]["oss"], str(fx.oss / "batch/a/one.mp4"))
    h.eq("jsonc_trim_s", jsegs[0]["trim_s"], None)

    # ================================================================== #
    # 6. 坏配置：单独 build 抛错；audit 不中断
    # ================================================================== #
    bad_cfg = root / "bad.json"
    bad_cfg.write_text("{ 这不是 JSON", encoding="utf-8")
    h.raises("bad_build_raises", M.OssIngestError,
             lambda: M.build_config_manifest(bad_cfg, fx.oss))
    mixed = M.audit_configs([audit_cfg, bad_cfg], fx.oss)
    h.eq("mixed_configs", mixed["configs"], 2)
    h.eq("mixed_refs", mixed["refs"], 4)
    h.eq("mixed_ok0", mixed["details"][0]["ok"], False)
    h.eq("mixed_ok1", mixed["details"][1]["ok"], False)
    h.eq("mixed_failed_configs", mixed["failed_configs"], 1)
    h.eq("mixed_error_nonempty", bool(mixed["details"][1]["error"]), True)
    h.eq("mixed_error_no_dup_prefix",
         "无法读取配置：无法读取配置" in (mixed["details"][1]["error"] or ""),
         False)

    # ================================================================== #
    # 7. 目录/通配符展开不了 ⇒ 整份记 error
    # ================================================================== #
    glob_cfg = root / "glob.jsonc"
    glob_cfg.write_text(
        "{\n"
        '  "version": 1,\n'
        f'  "base_dir": {json.dumps(str(fx.disk))},\n'
        '  "input": {"videos": ["*.no-such-ext"]},\n'
        '  "panels": {"mode": "bogus"}\n'
        "}\n",
        encoding="utf-8")
    gaudit = M.audit_configs([glob_cfg], fx.oss)
    h.eq("glob_audit_ok", gaudit["details"][0]["ok"], False)
    h.eq("glob_error_mentions", "没有命中" in (gaudit["details"][0]["error"] or ""),
         True)

    # ================================================================== #
    # 8. 本地探不到时长 ⇒ 同尺寸取排序第一个
    # ================================================================== #
    none_cfg = root / "dupnone.json"
    _write_json(none_cfg, _doc(fx.disk, video=fx.disk / "dup_local_none.mp4"))
    nseg = M.build_config_manifest(none_cfg, fx.oss)["segments"][0]
    h.eq("probe_none_first", nseg["oss"], str(fx.oss / "batch/b/dup1.mp4"))
    h.eq("probe_none_duration", nseg["duration_s"], 100.0)

    # ================================================================== #
    # 9. 空审计
    # ================================================================== #
    empty = M.audit_configs([], fx.oss)
    h.eq("empty_configs", empty["configs"], 0)
    h.eq("empty_details", empty["details"], [])


# --------------------------------------------------------------------------- #
# 独立运行
# --------------------------------------------------------------------------- #
class _LocalHarness:
    """最小 Harness：与 ``selftest.Harness`` 同接口（eq / raises）。"""

    def __init__(self):
        self.count = 0
        self.failures: list[str] = []

    def eq(self, name: str, got, want) -> None:
        self.count += 1
        if got != want:
            self.failures.append(f"{name}: got={got!r} want={want!r}")

    def raises(self, name: str, exc, fn) -> None:
        self.count += 1
        try:
            fn()
        except exc:
            return
        except Exception as e:                      # noqa: BLE001
            self.failures.append(f"{name}: 抛了 {type(e).__name__}: {e}")
        else:
            self.failures.append(f"{name}: 没有抛出 {exc}")


def selftest() -> int:
    """独立跑 manifest 自检，成功打印 ``全过`` 并返回 0。"""
    h = _LocalHarness()
    run(h)
    for msg in h.failures:
        print(f"[selftest_manifest][fail] {msg}")
    if h.failures:
        print(f"manifest 自检 {h.count} 项，失败 {len(h.failures)} 项 ❌")
        return 1
    print(f"manifest 自检 {h.count} 项全过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
