"""``oss_ingest`` 离线自检（临时目录、无网络、无 FUSE、只用标准库）。

用法::

    python -m livepipe.tools.oss_ingest --selftest
    python livepipe/oss_ingest.py --selftest

聚合三部分：

1. 本文件的 common / ingest / CLI 用例；
2. :mod:`.selftest_manifest`（若已提供 ``run(h)``）；
3. :mod:`.selftest_sync`（若已提供 ``run(h)``）。

后两者用 ``try/except ImportError`` 防御式聚合：并行开发时缺模块只跳过、
不把整个自检拖垮。全部通过打印 ``自检 N 项全过 ✅`` 并返回 0，否则逐条
打印失败项并返回 1。
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from . import cli, common, doc, ingest


class Harness:
    """极简断言收集器：只记总数与失败项，不抛异常。"""

    def __init__(self) -> None:
        self.total = 0
        self.failures: list[tuple[str, str]] = []

    def eq(self, name: str, got, want) -> None:
        """断言 ``got == want``。"""
        self.total += 1
        if got != want:
            self.failures.append((name, f"got {got!r}，want {want!r}"))

    def truthy(self, name: str, value, detail: str = "") -> None:
        """断言 ``bool(value)`` 为真。"""
        self.total += 1
        if not value:
            self.failures.append((name, detail or f"{value!r} 不是真值"))

    def raises(self, name: str, exc: type[BaseException], fn) -> None:
        """断言 ``fn()`` 抛出 ``exc``。"""
        self.total += 1
        try:
            fn()
        except exc:
            return
        except Exception as e:      # noqa: BLE001 - 自检需要看清实际异常
            self.failures.append(
                (name, f"抛出 {type(e).__name__}: {e}（期望 {exc.__name__}）"))
        else:
            self.failures.append((name, f"没有抛异常（期望 {exc.__name__}）"))


# --------------------------------------------------------------------------- #
# common.py
# --------------------------------------------------------------------------- #
def _run_common_tests(h: Harness) -> None:
    h.eq("human_bytes(0)", common.human_bytes(0), "0 B")
    h.eq("human_bytes(1023)", common.human_bytes(1023), "1023 B")
    h.eq("human_bytes(1024)", common.human_bytes(1024), "1.0 KB")
    h.eq("human_bytes(133.4GB)",
         common.human_bytes(1334 * 1024 ** 3 // 10), "133.4 GB")
    h.eq("rel_to 内部", common.rel_to("/a/b/c.mp4", "/a"), "b/c.mp4")
    h.eq("rel_to 越界", common.rel_to("/x/y", "/a"), "..")

    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        h.raises("read_json 缺失", common.OssIngestError,
                 lambda: common.read_json(td / "nope.json"))
        bad = td / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        h.raises("read_json 坏 JSON", common.OssIngestError,
                 lambda: common.read_json(bad))
        arr = td / "arr.json"
        arr.write_text("[1, 2]", encoding="utf-8")
        h.raises("read_json 顶层非对象", common.OssIngestError,
                 lambda: common.read_json(arr))

        out = td / "sub" / "x.json"
        common.write_json_atomic(out, {"a": 1, "中文": "值"})
        h.eq("write_json_atomic 内容", common.read_json(out),
             {"a": 1, "中文": "值"})
        h.eq("write_json_atomic 无 .tmp 残留",
             sorted(p.name for p in out.parent.glob("*.tmp")), [])

        root = td / "tree"
        (root / "d").mkdir(parents=True)
        (root / "a.mp4").write_bytes(b"a")
        (root / "d" / "b.mp4").write_bytes(b"bb")
        link = root / "link.mp4"
        try:
            link.symlink_to(root / "a.mp4")
            can_link = True
        except OSError:
            can_link = False
        h.eq("iter_files 默认跳过软链",
             [common.rel_to(p, root) for p in common.iter_files(root)],
             ["a.mp4", "d/b.mp4"])
        if can_link:
            h.eq("iter_files 跟随软链",
                 [common.rel_to(p, root)
                  for p in common.iter_files(root, follow_symlinks=True)],
                 ["a.mp4", "d/b.mp4", "link.mp4"])
        else:
            h.eq("iter_files 跟随软链（无权限，跳过）", True, True)

    h.eq("oss_root 默认值",
         common.oss_root(), common.oss_root(common.DEFAULT_OSS_ROOT))
    h.eq("oss_root 展开 ~", common.oss_root("~/x"), Path.home() / "x")

    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        empty = td / "empty-root"
        empty.mkdir()
        (empty / "notes.txt").write_text("not a video", encoding="utf-8")
        good = td / "good-root"
        (good / "sub").mkdir(parents=True)
        (good / "sub" / "a.mp4").write_bytes(b"v")
        later = td / "later-root"
        later.mkdir()
        (later / "b.mov").write_bytes(b"v")
        both = td / "both-root"
        both.mkdir()
        (both / "c.mp4.part").write_bytes(b"v")   # .part 不算视频

        half = td / "half-root"
        half.mkdir()
        (half / "zero.mp4").write_bytes(b"")          # 0 字节不算视频
        (half / "x.mp4.baiduyun.p.mp4").write_bytes(b"v")  # 下载临时名不算

        explicit_root = td / "explicit-root"
        explicit_root.mkdir()

        h.eq("has_video_file 空目录", common.has_video_file(empty), False)
        h.eq("has_video_file 忽略 .part",
             common.has_video_file(both), False)
        h.eq("has_video_file 忽略 0 字节 / 下载临时名",
             common.has_video_file(half), False)
        h.eq("has_video_file 命中嵌套视频",
             common.has_video_file(good), True)

        root, source = common.resolve_oss_root(
            None, candidates=[str(empty), str(half), str(good), str(later)])
        h.eq("resolve_oss_root 跳过空目录", root, good)
        h.eq("resolve_oss_root 来源 auto", source, "auto")

        explicit, source2 = common.resolve_oss_root(
            explicit_root, candidates=[str(good)])
        h.eq("resolve_oss_root 显式路径",
             explicit, common.oss_root(explicit_root))
        h.eq("resolve_oss_root 来源 explicit", source2, "explicit")
        h.raises("resolve_oss_root 显式路径不存在报错", common.OssIngestError,
                 lambda: common.resolve_oss_root(td / "no-such-root"))
        missing_root, source4 = common.resolve_oss_root(
            td / "no-such-root", strict=False)
        h.eq("resolve_oss_root 非严格保留显式路径",
             missing_root, common.oss_root(td / "no-such-root"))
        h.eq("resolve_oss_root 非严格显式来源", source4, "explicit")

        h.raises("resolve_oss_root 全部无效报错", common.OssIngestError,
                 lambda: common.resolve_oss_root(
                     None, candidates=[str(empty)]))
        fallback, source3 = common.resolve_oss_root(
            None, candidates=[str(empty)], strict=False)
        h.eq("resolve_oss_root 非严格回退首个候选", fallback, empty)
        h.eq("resolve_oss_root 来源 unresolved", source3, "unresolved")


# --------------------------------------------------------------------------- #
# ingest.py
# --------------------------------------------------------------------------- #
def _run_ingest_tests(h: Harness) -> None:
    original_probe = ingest.probe_duration
    try:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)

            # 正常收尾
            part = td / "clip.mp4.part"
            part.write_bytes(b"x" * 1234)
            ingest.probe_duration = lambda _p: 2854.414
            manifest = ingest.finalize_part(part)
            final = td / "clip.mp4"
            h.eq("finalize 改名", final.exists(), True)
            h.eq("finalize part 消失", part.exists(), False)
            h.eq("finalize bytes", manifest["bytes"], 1234)
            h.eq("finalize duration", manifest["duration_s"], 2854.414)
            h.eq("finalize version", manifest["version"],
                 common.MANIFEST_VERSION)
            h.eq("finalize sha256 默认 null", manifest["sha256"], None)
            h.truthy("finalize finalized_at 是 UTC",
                     str(manifest["finalized_at"]).endswith("Z"),
                     str(manifest["finalized_at"]))
            mpath = ingest.manifest_path(final)
            h.eq("finalize manifest 落盘", mpath.exists(), True)
            h.eq("finalize manifest 内容",
                 common.read_json(mpath)["final"], str(final))
            h.eq("finalize 无 .tmp 残留",
                 sorted(p.name for p in td.glob("*.tmp")), [])

            # 目标异尺寸：默认拒绝，--force 覆盖
            part2 = td / "clip2.mp4.part"
            part2.write_bytes(b"y" * 10)
            (td / "clip2.mp4").write_bytes(b"z" * 99)
            h.raises("finalize 异尺寸拒绝", common.OssIngestError,
                     lambda: ingest.finalize_part(part2))
            ingest.probe_duration = lambda _p: 10.0
            manifest2 = ingest.finalize_part(part2, force=True)
            h.eq("finalize --force 覆盖后大小",
                 (td / "clip2.mp4").stat().st_size, 10)
            h.eq("finalize --force 返回 bytes", manifest2["bytes"], 10)

            # 完整性闸门：默认必须有有效时长；逃生阀 allow_no_duration 才放行
            part_gate = td / "gate.mp4.part"
            part_gate.write_bytes(b"g" * 8)
            ingest.probe_duration = lambda _p: None
            h.raises("finalize 无时长默认拒绝", common.OssIngestError,
                     lambda: ingest.finalize_part(part_gate))
            h.eq("finalize 无时长保留 part", part_gate.exists(), True)
            h.eq("finalize 无时长不产生 final",
                 (td / "gate.mp4").exists(), False)
            h.raises("finalize expect_duration 且无时长仍拒绝",
                     common.OssIngestError,
                     lambda: ingest.finalize_part(
                         part_gate, expect_duration=12.0,
                         allow_no_duration=True))
            gate_ok = ingest.finalize_part(part_gate, allow_no_duration=True)
            h.eq("finalize allow_no_duration 放行",
                 (td / "gate.mp4").exists(), True)
            h.eq("finalize allow_no_duration duration null",
                 gate_ok["duration_s"], None)

            # 0 / 负数 / NaN 都不算有效时长
            part_bad_dur = td / "baddur.mp4.part"
            part_bad_dur.write_bytes(b"b" * 3)
            for bad_duration in (0.0, -1.0, float("nan")):
                ingest.probe_duration = lambda _p, d=bad_duration: d
                h.raises(f"finalize 无效时长 {bad_duration!r} 拒绝",
                         common.OssIngestError,
                         lambda p=part_bad_dur: ingest.finalize_part(p))
            h.eq("finalize 无效时长保留 part", part_bad_dur.exists(), True)

            # 有效时长下：expect_bytes / expect_duration 不符仍拒绝
            part_gate2 = td / "gate2.mp4.part"
            part_gate2.write_bytes(b"h" * 8)
            ingest.probe_duration = lambda _p: 12.0
            h.raises("finalize expect_bytes 拒绝", common.OssIngestError,
                     lambda: ingest.finalize_part(part_gate2, expect_bytes=99))
            h.raises("finalize expect_duration 拒绝", common.OssIngestError,
                     lambda: ingest.finalize_part(
                         part_gate2, expect_duration=20.0))
            gate = ingest.finalize_part(
                part_gate2, expect_bytes=8, expect_duration=12.2,
                metadata={"event": "e", "day": "d"})
            h.eq("finalize metadata event", gate["event"], "e")
            h.eq("finalize metadata day", gate["day"], "d")

            # manifest 写失败：rename 已发生（代码刻意先改名再写 manifest，
            # 避免留下指向不存在 final 的假 manifest），错误消息提示补写。
            part_mf = td / "mf.mp4.part"
            part_mf.write_bytes(b"m" * 6)
            original_writer = ingest.write_json_atomic
            original_replace = ingest.os.replace

            def boom_writer(_path, _data):
                raise common.OssIngestError("磁盘满")

            ingest.write_json_atomic = boom_writer
            try:
                h.raises("finalize manifest 写失败抛错",
                         common.OssIngestError,
                         lambda: ingest.finalize_part(part_mf))
            finally:
                ingest.write_json_atomic = original_writer
            h.eq("finalize manifest 写失败已改名",
                 (td / "mf.mp4").exists(), True)
            h.eq("finalize manifest 写失败 part 已消失",
                 part_mf.exists(), False)

            # rename 失败：.part 仍在，且不会留下 manifest
            part_rn = td / "rn.mp4.part"
            part_rn.write_bytes(b"r" * 6)

            def boom_replace(a, b):
                # 放行 manifest 的原子写，只让成品 rename 失败
                if str(b).endswith(".manifest.json") or str(b).endswith(".tmp"):
                    return original_replace(a, b)
                raise OSError("rename boom")

            ingest.os.replace = boom_replace
            try:
                h.raises("finalize rename 失败抛错", common.OssIngestError,
                         lambda: ingest.finalize_part(part_rn))
            finally:
                ingest.os.replace = original_replace
            h.eq("finalize rename 失败保留 part", part_rn.exists(), True)
            h.eq("finalize rename 失败无 final",
                 (td / "rn.mp4").exists(), False)
            h.eq("finalize rename 失败无 manifest",
                 ingest.manifest_path(td / "rn.mp4").exists(), False)

            # write_manifest=False
            part3 = td / "clip3.mp4.part"
            part3.write_bytes(b"q" * 5)
            manifest3 = ingest.finalize_part(part3, write_manifest=False)
            h.eq("finalize write_manifest=False 不写文件",
                 ingest.manifest_path(td / "clip3.mp4").exists(), False)
            h.eq("finalize write_manifest=False 仍返回",
                 manifest3["final"], str(td / "clip3.mp4"))

            # 错误路径
            h.raises("finalize 缺失 part", common.OssIngestError,
                     lambda: ingest.finalize_part(td / "nope.part"))
            plain = td / "plain.mp4"
            plain.write_bytes(b"p" * 3)
            h.raises("finalize 非 .part 且无 final", common.OssIngestError,
                     lambda: ingest.finalize_part(plain))
            empty = td / "empty.mp4.part"
            empty.write_bytes(b"")
            h.raises("finalize 空 part", common.OssIngestError,
                     lambda: ingest.finalize_part(empty))

            # verify_media
            media = td / "done.mp4"
            media.write_bytes(b"m" * 77)
            ingest.probe_duration = lambda _p: 12.5
            info = ingest.verify_media(media)
            h.eq("verify_media bytes", info["bytes"], 77)
            h.eq("verify_media duration", info["duration_s"], 12.5)
            h.raises("verify_media 缺失", common.OssIngestError,
                     lambda: ingest.verify_media(td / "no.mp4"))
            h.raises("verify_media 空文件", common.OssIngestError,
                     lambda: ingest.verify_media(empty))
    finally:
        ingest.probe_duration = original_probe


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
class _FakeManifest:
    """替代 manifest.py 的最小实现，只验证 CLI 取数与落盘。"""

    @staticmethod
    def build_config_manifest(config, _root):
        return {
            "version": common.MANIFEST_VERSION,
            "config": str(config),
            "generated_from": {},
            "segments": [
                {"seg": "seg1", "disk": "a.mp4", "oss": "oss/a.mp4",
                 "trimmed": False, "source_oss": None, "bytes": 10,
                 "duration_s": 1.0, "trim_s": None},
                {"seg": "seg2", "disk": "b.mp4", "oss": None,
                 "trimmed": True, "source_oss": None, "bytes": 20,
                 "duration_s": None, "trim_s": [0, 5]},
            ],
        }

    @staticmethod
    def audit_configs(configs, _root, **_kwargs):
        return {
            "configs": len(configs), "refs": 2, "full_mapped": 1,
            "trimmed": 1, "missing": 0,
            "details": [{
                "config": str(configs[0]), "ok": True, "refs": 2,
                "full_mapped": 1, "trimmed": 1, "missing": 0,
                "error": None, "segments": [],
            }],
        }


class _FakeSync:
    """替代 sync.py 的最小实现，只验证 CLI 计数与退出码。"""

    @staticmethod
    def sync_tree(_src, _dst, **_kwargs):
        return {"files": 2, "bytes": 30, "copied": 1, "skipped": 1,
                "symlinks_skipped": 0, "deleted": 0, "failed": 0,
                "failures": []}

    @staticmethod
    def verify_tree(_src, _dst, **_kwargs):
        return {"files": 2, "bytes": 30, "ok": 2, "missing": 0,
                "size_mismatch": 0, "sha256_mismatch": 0,
                "symlinks_skipped": 0, "extra": 0}


def _quiet(fn):
    """吞掉 CLI 的正常输出，只留返回值。"""
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        return fn()


def _run_cli_tests(h: Harness) -> None:
    parser = cli.make_parser()
    h.eq("CLI 解析 check-env", parser.parse_args(["check-env"]).command,
         "check-env")
    h.eq("CLI 解析 sync --workers", parser.parse_args(
        ["sync", "a", "b", "--workers", "4"]).workers, 4)
    h.eq("CLI make_parser 自定义 prog",
         cli.make_parser("python -m livepipe oss").prog,
         "python -m livepipe oss")
    usage = cli.make_parser("python -m livepipe oss").format_usage()
    h.truthy("CLI usage 前缀",
             usage.startswith("usage: python -m livepipe oss"), usage)

    original_parser = cli.make_parser
    seen_prog: dict = {}
    cli.make_parser = lambda prog=None: (seen_prog.update(prog=prog),
                                         original_parser(prog))[1]
    try:
        _quiet(lambda: cli.main([], prog="python -m livepipe oss"))
    finally:
        cli.make_parser = original_parser
    h.eq("CLI main 透传 prog", seen_prog.get("prog"), "python -m livepipe oss")

    h.eq("CLI 空参数返回 2", _quiet(lambda: cli.main([])), 2)
    h.eq("CLI 坏子命令返回 2", _quiet(lambda: cli.main(["nope"])), 2)

    original_probe = ingest.probe_duration
    try:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)

            # ingest（位置参数）
            part = td / "v.mp4.part"
            part.write_bytes(b"v" * 42)
            ingest.probe_duration = lambda _p: 3.0
            rc = _quiet(lambda: cli.main(["ingest", str(part)]))
            h.eq("CLI ingest 返回 0", rc, 0)
            h.eq("CLI ingest 落盘", (td / "v.mp4").exists(), True)

            # ingest（--src 别名 + 事件元数据）
            part_src = td / "s.mp4.part"
            part_src.write_bytes(b"s" * 7)
            rc_src = _quiet(lambda: cli.main(
                ["ingest", "--src", str(part_src), "--event", "E",
                 "--day", "D", "--platform", "P", "--account", "A",
                 "--trim", "0-10"]))
            h.eq("CLI ingest --src 返回 0", rc_src, 0)
            meta = common.read_json(ingest.manifest_path(td / "s.mp4"))
            h.eq("CLI ingest 事件落 manifest", meta.get("event"), "E")
            h.eq("CLI ingest trim 解析", meta.get("trim"), [0.0, 10.0])

            # ingest 无时长：默认拒绝并保留 .part；--allow-no-duration 放行
            part_nd = td / "nd.mp4.part"
            part_nd.write_bytes(b"n" * 4)
            ingest.probe_duration = lambda _p: None
            h.eq("CLI ingest 无时长默认返回 1",
                 _quiet(lambda: cli.main(["ingest", str(part_nd)])), 1)
            h.eq("CLI ingest 无时长保留 part", part_nd.exists(), True)
            h.eq("CLI ingest --allow-no-duration 返回 0",
                 _quiet(lambda: cli.main(
                     ["ingest", str(part_nd), "--allow-no-duration"])), 0)
            h.eq("CLI ingest --allow-no-duration 落盘",
                 (td / "nd.mp4").exists(), True)
            ingest.probe_duration = lambda _p: 3.0

            # manifest / audit（用假模块，不依赖并行开发的 manifest.py）
            cfg = td / "cfg.json"
            cfg.write_text("{}", encoding="utf-8")
            out = td / "manifest.json"
            out_dir = td / "manifest-dir"
            audit_out = td / "audit.json"
            fake_root = str(td / "fake-oss")
            (td / "fake-oss").mkdir()
            original_manifest = cli._manifest_module
            cli._manifest_module = lambda: _FakeManifest
            try:
                rc_m = _quiet(lambda: cli.main(
                    ["manifest", str(cfg), "--oss-root", fake_root,
                     "--out", str(out), "--out-dir", str(out_dir)]))
                rc_a = _quiet(lambda: cli.main(
                    ["audit", str(cfg), "--oss-root", fake_root,
                     "--out", str(audit_out)]))
            finally:
                cli._manifest_module = original_manifest
            h.eq("CLI manifest 返回 0", rc_m, 0)
            h.eq("CLI manifest 写出", out.exists(), True)
            h.eq("CLI manifest --out-dir 写出",
                 (out_dir / "cfg.json").exists(), True)
            h.eq("CLI manifest --out-dir 内容",
                 common.read_json(out_dir / "cfg.json").get("config"),
                 str(cfg))

            # 多份配置：每份各写一个 <配置名>.json
            cfg2 = td / "cfg2.json"
            cfg2.write_text("{}", encoding="utf-8")
            multi_dir = td / "manifest-multi"
            cli._manifest_module = lambda: _FakeManifest
            try:
                rc_multi = _quiet(lambda: cli.main(
                    ["manifest", str(cfg), str(cfg2), "--oss-root", fake_root,
                     "--out-dir", str(multi_dir)]))
            finally:
                cli._manifest_module = original_manifest
            h.eq("CLI manifest 多份 --out-dir 返回 0", rc_multi, 0)
            h.eq("CLI manifest 多份 --out-dir 文件数",
                 sorted(p.name for p in multi_dir.glob("*.json")),
                 ["cfg.json", "cfg2.json"])
            h.eq("CLI audit 返回 0", rc_a, 0)
            h.eq("CLI audit 写出", audit_out.exists(), True)

            # audit 门禁：missing > 0 返回 1；--report-only 强制 0
            class _BadAudit:
                @staticmethod
                def audit_configs(configs, _root, **_kwargs):
                    return {"configs": 1, "refs": 1, "full_mapped": 0,
                            "trimmed": 0, "missing": 1, "local_missing": 0,
                            "failed_configs": 0,
                            "details": [{"config": str(configs[0]),
                                         "ok": False, "refs": 1,
                                         "full_mapped": 0, "trimmed": 0,
                                         "missing": 1, "local_missing": 0,
                                         "error": None, "segments": []}]}

            cli._manifest_module = lambda: _BadAudit
            try:
                rc_bad = _quiet(lambda: cli.main(
                    ["audit", str(cfg), "--oss-root", fake_root]))
                rc_report = _quiet(lambda: cli.main(
                    ["audit", str(cfg), "--oss-root", fake_root,
                     "--report-only"]))
            finally:
                cli._manifest_module = original_manifest
            h.eq("CLI audit 缺失返回 1", rc_bad, 1)
            h.eq("CLI audit --report-only 返回 0", rc_report, 0)

            # sync / verify
            original_sync = cli._sync_module
            cli._sync_module = lambda: _FakeSync
            try:
                rc_s = _quiet(lambda: cli.main(
                    ["sync", str(td), str(td / "dst")]))
                rc_v = _quiet(lambda: cli.main(
                    ["verify", str(td), str(td / "dst")]))
            finally:
                cli._sync_module = original_sync
            h.eq("CLI sync 返回 0", rc_s, 0)
            h.eq("CLI verify 返回 0", rc_v, 0)

            # verify-media
            media = td / "media.mp4"
            media.write_bytes(b"m" * 33)
            ingest.probe_duration = lambda _p: 5.5
            h.eq("CLI verify-media 返回 0",
                 _quiet(lambda: cli.main(["verify-media", str(media)])), 0)
            ingest.probe_duration = lambda _p: None
            h.eq("CLI verify-media 无时长返回 1",
                 _quiet(lambda: cli.main(["verify-media", str(media)])), 1)
            h.eq("CLI verify-media --allow-unknown-duration 返回 0",
                 _quiet(lambda: cli.main([
                     "verify-media", str(media),
                     "--allow-unknown-duration"])), 0)
            ingest.probe_duration = lambda _p: 5.5

            # check-env：全 PASS（mount info 用 fuse 替身，fuse.conf 用 PASS 替身）
            home = td / "home"
            (home / ".bypy").mkdir(parents=True)
            (home / ".aws").mkdir()
            for cred in (home / ".bypy" / "bypy.json",
                         home / ".aws" / "credentials"):
                cred.write_text("placeholder", encoding="utf-8")
                os.chmod(cred, 0o600)

            original_which = cli.shutil.which
            original_ismount = os.path.ismount
            original_exists = os.path.exists
            original_mount_info = cli._mount_info
            original_fuse_conf = cli._fuse_conf_row
            cli.shutil.which = lambda name: f"/fake/{name}"
            os.path.ismount = lambda _p: True
            os.path.exists = (lambda p: True if str(p) == "/dev/fuse"
                              else original_exists(p))
            cli._mount_info = lambda p: {
                "target": str(p), "fstype": "fuse.goofys",
                "source": "goofys"}
            cli._fuse_conf_row = lambda: {
                "check": "/etc/fuse.conf", "status": "PASS",
                "detail": "user_allow_other"}
            try:
                with mock.patch.dict(os.environ, {"HOME": str(home)}):
                    rows, ok = cli.check_env(td)
            finally:
                cli.shutil.which = original_which
                os.path.ismount = original_ismount
                os.path.exists = original_exists
                cli._mount_info = original_mount_info
                cli._fuse_conf_row = original_fuse_conf
            h.eq("check_env 全 PASS", ok, True)
            h.eq("check_env 行数", len(rows), 10)
            header = doc.check_env_table(rows).splitlines()[0]
            h.eq("check_env 表头固定",
                 [c.strip() for c in header.split("|")],
                 ["检查", "结果", "说明"])
            h.eq("check_env 状态集合",
                 sorted({r["status"] for r in rows}), ["PASS"])

            # check-env：非 fuse 祖先挂载 → FAIL（不再因为 /home 是挂载就 PASS）
            cli.shutil.which = lambda name: f"/fake/{name}"
            os.path.ismount = lambda _p: True
            os.path.exists = (lambda p: True if str(p) == "/dev/fuse"
                              else original_exists(p))
            cli._mount_info = lambda p: {
                "target": str(p), "fstype": "ext4", "source": "/dev/sda1"}
            cli._fuse_conf_row = lambda: {
                "check": "/etc/fuse.conf", "status": "PASS", "detail": ""}
            try:
                with mock.patch.dict(os.environ, {"HOME": str(home)}):
                    rows_ext4, ok_ext4 = cli.check_env(td)
            finally:
                cli.shutil.which = original_which
                os.path.ismount = original_ismount
                os.path.exists = original_exists
                cli._mount_info = original_mount_info
                cli._fuse_conf_row = original_fuse_conf
            mount_ext4 = next(r for r in rows_ext4 if r["check"] == "OSS 挂载")
            h.eq("check_env 非 fuse 挂载 → FAIL", mount_ext4["status"], "FAIL")
            h.eq("check_env 非 fuse 挂载 → 不通过", ok_ext4, False)

            # check-env：没有挂载祖先 → FAIL
            cli.shutil.which = lambda name: f"/fake/{name}"
            os.path.ismount = lambda _p: False
            os.path.exists = (lambda p: True if str(p) == "/dev/fuse"
                              else original_exists(p))
            try:
                with mock.patch.dict(os.environ, {"HOME": str(home)}):
                    rows2, ok2 = cli.check_env(td)
            finally:
                cli.shutil.which = original_which
                os.path.ismount = original_ismount
                os.path.exists = original_exists
            mount_row = next(r for r in rows2 if r["check"] == "OSS 挂载")
            h.eq("check_env 无挂载 → FAIL", mount_row["status"], "FAIL")
            h.eq("check_env 无挂载 → 不通过", ok2, False)

            # check-env：compute 角色把缺失的 bypy 降级为 WARN
            empty_home = td / "empty-home"
            (empty_home / ".aws").mkdir(parents=True)
            aws_cred = empty_home / ".aws" / "credentials"
            aws_cred.write_text("placeholder", encoding="utf-8")
            os.chmod(aws_cred, 0o600)
            original_find_exe = cli._find_exe
            cli._find_exe = (
                lambda name, *fallbacks: None if name == "bypy"
                else f"/fake/{name}")
            cli._mount_info = lambda p: {
                "target": str(p), "fstype": "fuse.goofys", "source": "goofys"}
            cli._fuse_conf_row = lambda: {
                "check": "/etc/fuse.conf", "status": "PASS", "detail": ""}
            os.path.ismount = lambda _p: True
            os.path.exists = (lambda p: True if str(p) == "/dev/fuse"
                              else original_exists(p))
            try:
                with mock.patch.dict(os.environ, {"HOME": str(empty_home)}):
                    rows_all, ok_all = cli.check_env(td, role="all")
                    rows_comp, ok_comp = cli.check_env(td, role="compute")
            finally:
                cli._find_exe = original_find_exe
                cli.shutil.which = original_which
                os.path.ismount = original_ismount
                os.path.exists = original_exists
                cli._mount_info = original_mount_info
                cli._fuse_conf_row = original_fuse_conf
            h.eq("check_env all 缺 bypy → 不通过", ok_all, False)
            h.eq("check_env compute 缺 bypy → 通过", ok_comp, True)
    finally:
        ingest.probe_duration = original_probe


# --------------------------------------------------------------------------- #
# 聚合
# --------------------------------------------------------------------------- #
def _run_optional(h: Harness, name: str) -> bool:
    """导入并运行 ``<name>.run(h)``；模块或 ``run`` 缺失返回 ``False``。"""
    try:
        module = importlib.import_module(f"{__package__}.{name}")
    except ImportError:
        return False
    run = getattr(module, "run", None)
    if run is None:
        return False
    try:
        run(h)
    except Exception as e:          # noqa: BLE001 - 一个子自检崩了也要汇总
        h.total += 1
        h.failures.append((f"{name}.run", f"抛出异常：{e!r}"))
    return True


def selftest() -> int:
    """跑全部离线自检，返回进程退出码（0 = 全过）。"""
    h = Harness()
    _run_common_tests(h)
    _run_ingest_tests(h)
    _run_cli_tests(h)
    for name in ("selftest_manifest", "selftest_sync"):
        if not _run_optional(h, name):
            print(f"[oss_ingest][warn] 未找到 {name}.run，跳过其用例",
                  file=sys.stderr)
    if h.failures:
        print(f"自检失败 {len(h.failures)}/{h.total} 项：")
        for name, detail in h.failures:
            print(f"  ✗ {name}：{detail}")
        return 1
    print(f"自检 {h.total} 项全过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
