#!/usr/bin/env python3
"""评测台：把「一次评测」跑成可复现的对照实验。

    python3 eval/bench.py snapshot --corpus "$SRC" --into "$EVAL/corpus"
    python3 eval/bench.py run      --snapshot "$EVAL/corpus" --tag A --repeat 3 \
                                   --arm eval/arms/A-baseline.json --eval-root "$EVAL"
    python3 eval/bench.py compare  --a "$EVAL/runs/A" --b "$EVAL/runs/B" \
                                   --thresholds eval/thresholds.json --out "$EVAL/compare.json"
    python3 eval/bench.py pair     --snapshot "$EVAL/corpus" --tag-a A --tag-b A2 \
                                   --a-arm eval/arms/A-baseline.json \
                                   --b-arm eval/arms/A-baseline.json --eval-root "$EVAL"
    python3 eval/bench.py baseline --run "$EVAL/runs/A" --run "$EVAL/runs/A2" \
                                   --eval-root "$EVAL" --out "$EVAL/baseline.json"
    python3 eval/bench.py verify   --corpus "$SRC" --md5 "$EVAL/corpus.md5"

设计约束（见 `docs/评测方案.html` / `docs/评测落地计划.html`）：
* **不 import 产品代码** —— 只跑命令行、读产物、对拍 md5；产品怎么改都不用动这里。
* **不碰原件** —— 所有运行都在 snapshot 副本上，run 一律 `--out`，绝不给 `--yes`。
* **缺指标写 null** —— 不拿 0 顶替；`compare` 的退出码区分「回退 / 不可比 / 未测」。
* **产品的 3 不算失败** —— 那是「缺口 >5%」的口径判定（产物已完整落盘），照跑照记；
  只有其他非 0 退出码才当崩溃。评测语料的缺口本来就可能很大。

退出码：0 通过（含「无显著差异」）· 3 有指标回退 · 4 指纹不可比 · 5 原件被改 · 6 有指标未测。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PRODUCT = REPO / "expand" / "expand_produce.py"
CACHE_FILE = REPO / "expand" / "measure_cache.json"

# 语料里由**产品自己**写出来的东西：快照/对拍时排除，避免把上一次的账带进这次评测。
ARTIFACT_NAMES = {
    "expand_plan.json", "expand_run_report.json", "expand_reconcile.csv",
    "expand_measure_timings.jsonl", "expand_pairs.jsonl",
    # A 档对账表与 E 档进度 journal：都是产品自己写出来的，不该进语料指纹
    # （否则开了 A 的臂和基线臂会因「多了一个文件」被判不可比）。
    "livepipe_join.json", "expand_run_progress.jsonl",
}
IMG_EXT = {".jpg", ".jpeg", ".png"}


# ---------------------------------------------------------------- 通用

def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _corpus_files(root: Path) -> list[Path]:
    """参与指纹的文件：图 + manifest + 语料自带的 json/jsonl（排除产品产物与扩展图）。"""
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.name in ARTIFACT_NAMES or p.name.startswith("扩展"):
            continue
        if p.suffix.lower() in IMG_EXT or p.name == "manifest.json" or p.suffix in (".json", ".jsonl"):
            out.append(p)
    return out


def _md5_lines(root: Path) -> list[str]:
    lines = []
    for p in _corpus_files(root):
        lines.append(f"{_md5(p)}  {p.relative_to(root).as_posix()}")
    return lines


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _git_head() -> str:
    try:
        head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--",
                                str(PRODUCT.relative_to(REPO))],
                               capture_output=True, text=True, check=True).stdout.strip()
        return head + ("+dirty" if dirty else "")
    except Exception:  # noqa: BLE001 - 没有 git 也能评测，只是指纹弱一点
        # 仓库还没有第一个 commit 时 `rev-parse HEAD` 会失败 —— 退化成**产品文件内容 hash**，
        # 至少能区分「代码改没改」，别写成 `unknown` 让指纹形同虚设
        try:
            h = hashlib.sha256(PRODUCT.read_bytes()).hexdigest()[:16]
            return f"file:{h}"
        except OSError:
            return "unknown"


# ---------------------------------------------------------------- snapshot

def cmd_snapshot(args) -> int:
    src = Path(args.corpus).expanduser().resolve()
    dst = Path(args.into).expanduser().resolve()
    if not (src / "manifest.json").exists():
        print(f"不是语料根（没有 manifest.json）：{src}", file=sys.stderr)
        return 2
    if dst.exists() and any(dst.iterdir()):
        if not args.force:
            print(f"目标非空：{dst}（要覆盖就加 --force）", file=sys.stderr)
            return 2
        shutil.rmtree(dst)

    def _ignore(_dir, names):
        drop = set()
        for n in names:
            p = Path(_dir) / n
            if n in ARTIFACT_NAMES or n.startswith("扩展"):
                drop.add(n)
            elif p.is_file() and p.suffix.lower() not in IMG_EXT | {".json", ".jsonl"}:
                drop.add(n)
        return drop

    shutil.copytree(src, dst, ignore=_ignore)
    lines = _md5_lines(dst)
    md5_path = dst.parent / "corpus.md5"
    md5_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    fp = {"corpus_sha256": _sha256_text("\n".join(lines)), "files": len(lines),
          "source": str(src), "snapshot": str(dst)}
    _write_json(dst.parent / "fingerprint.json", fp)
    print(f"快照 {len(lines)} 个文件 → {dst}\n指纹 {fp['corpus_sha256'][:16]}… → {md5_path}")
    return 0


# ---------------------------------------------------------------- run

def _run_once(arm: dict, snapshot: Path, out_dir: Path, log_dir: Path) -> dict:
    """跑一次 plan+run，返回指标（不含两臂对比）。"""
    workers = int(arm.get("workers", 8))
    seed = int(arm.get("seed", 20261010))
    common = ["--source", str(snapshot), "--workers", str(workers)]
    env_extra = arm.get("env") or {}

    # B 档：两臂都从**空缓存**起步；热臂在同一轮里先跑一遍填缓存（预热，不计时），
    # 计时的是第二遍 plan —— 这才是「缓存已经热了再跑」的收益。预热耗时单独记 `warmup_s`。
    cache_warm = str(arm.get("cache", "cold")) == "warm"
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
    # A 档对账表是 plan 的旁挂产物，写在快照根。交错跑时两臂共用快照：
    # 先跑 A-join 再跑基线，不删就会把上一臂的表读成这一臂的指标。
    (snapshot / "livepipe_join.json").unlink(missing_ok=True)

    def _call(phase: str, extra: list[str]) -> tuple[float, int]:
        cmd = [sys.executable, str(PRODUCT)] + extra
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
        elapsed = time.perf_counter() - t0
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"{phase}.log").write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr,
                                              encoding="utf-8")
        # 产品的 3 不是崩溃，是**口径判定**：缺口 >5%（`expand_produce.py` 在写完 report /
        # reconcile **之后**才说这句话，产物是全的）。评测语料本来就可能缺口大（无贴图源的
        # 用户按口径④ 不出片），所以 3 照跑照记，**不能当崩溃**；其余非 0 才算失败。
        if proc.returncode not in (0, 3):
            raise RuntimeError(f"{phase} 退出码 {proc.returncode}，见 {log_dir / (phase + '.log')}")
        if proc.returncode == 3:
            print(f"  [{phase}] 退出码 3（缺口 >5% 的口径判定，产物完整）")
        return round(elapsed, 3), proc.returncode

    plan_flags = list(arm.get("plan_flags") or [])
    if cache_warm and "--cache" not in plan_flags:
        plan_flags.append("--cache")
    warmup_s = None
    if cache_warm:
        # 预热：同一轮内先完整跑一遍，把缓存写出来；**不计入 T**（B 的判据是"热了再跑"）。
        warmup_s, warm_rc = _call("plan-warmup",
                                  ["--mode", "plan"] + common + ["--seed", str(seed)]
                                  + plan_flags)
        if warm_rc not in (0, 3):
            raise RuntimeError(f"plan-warmup 退出码 {warm_rc}")
    plan_s, plan_rc = _call("plan", ["--mode", "plan"] + common + ["--seed", str(seed)]
                            + plan_flags)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    run_s, run_rc = _call("run", ["--mode", "run"] + common + ["--out", str(out_dir)]
                          + list(arm.get("run_flags") or []))
    return {"plan": plan_s, "run": run_s, "total": round(plan_s + run_s, 3),
            "warmup": warmup_s,
            "plan_rc": plan_rc, "run_rc": run_rc, **env_extra}


def _collect(snapshot: Path, out_dir: Path) -> dict:
    plan = json.loads((snapshot / "expand_plan.json").read_text(encoding="utf-8"))
    rep_path = out_dir / "expand_run_report.json"
    rep = json.loads(rep_path.read_text(encoding="utf-8")) if rep_path.exists() else {}

    users = plan.get("users") or []
    short = plan.get("short") or []
    need_targets = sum(1 for u in users if u.get("count", 0) > 0 or u.get("short_reason"))
    blank_targets = sum(1 for u in users if u.get("short_reason") == "NO_CANDIDATE")

    pairs = []
    pairs_path = snapshot / "expand_pairs.jsonl"
    if pairs_path.exists():
        pairs = [json.loads(l) for l in pairs_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    # C 档（等级）：`R_lvl` 的分母是**双方都已知**的配对。第 3 层排除（`tier=excluded`）
    # **不是配对**——它是审计行，必须跳过；否则「正确地排除掉不同等级」会把 R_lvl 拉低成
    # 惩罚。目标等级未知（`degraded`）时 b_level 为 null ⇒ 本来就不进分母。
    lv = [(p.get("t_level"), p.get("b_level")) for p in pairs
          if p.get("tier") != "excluded"
          and p.get("t_level") not in (None, "") and p.get("b_level") not in (None, "")]
    level_consistent = (sum(1 for a, b in lv if a == b) / len(lv)) if lv else None
    level_tiers = dict(Counter(p.get("tier") for p in pairs if p.get("tier")))
    level_known_targets = sum(1 for u in users if u.get("t_level") not in (None, ""))

    outputs = {}
    for p in sorted(Path(out_dir).rglob("扩展*.jpg")):
        outputs[p.relative_to(out_dir).as_posix()] = _md5(p)

    # plan 侧分项（T_stage）：`expand_measure_timings.jsonl` 逐图一行 → 每段 p50/p95
    # B 档：命中缓存的行三段恒 0（这次没量），聚合时必须排除 —— 否则热臂的 p50 被 0 拉低，
    # 读起来像"量得更快"，其实是"没量"。
    stage = {}
    tpath = snapshot / "expand_measure_timings.jsonl"
    if tpath.exists():
        rows_t = [json.loads(l) for l in tpath.read_text(encoding="utf-8").splitlines() if l.strip()]
        rows_t = [r for r in rows_t if not r.get("cached")]
        for seg in ("decode", "locate", "badge"):
            vals = sorted(float(r.get(f"ms_{seg}") or 0.0) for r in rows_t)
            if vals:
                stage[seg] = {"n": len(vals),
                              "p50": round(statistics.median(vals), 3),
                              "p95": round(_p95(vals), 3),
                              "mean": round(statistics.fmean(vals), 3)}

    # A 档（`--livepipe-join`）：对账表只在开了 flag 的臂里存在。**只读指标**——
    # 它不参与 T/N/R 判据，只证明「每张导出图都能沿主键回到 livepipe 那一行」。
    join = None
    jpath = snapshot / "livepipe_join.json"
    if jpath.exists():
        j = json.loads(jpath.read_text(encoding="utf-8"))
        join = {k: j.get(k) for k in (
            "status", "images", "matched", "coverage",
            "rows_matched", "rows_coverage", "rows_exact", "rows_multi",
            "conflict_count", "unmatched_count",
            "badge_text_covered", "badge_text_ratio")}

    pc = (plan.get("meta") or {}).get("counters", {})
    hit, miss = pc.get("cache_hit"), pc.get("cache_miss")
    # B 档分母 = **所有像素量测次数**：底图量测 + 字号拟合扫描（见评测方案 §3 B）。
    fit_hit, fit_miss = pc.get("fit_hit") or 0, pc.get("fit_miss") or 0
    denom = (hit or 0) + (miss or 0) + fit_hit + fit_miss
    h_cache = (((hit or 0) + fit_hit) / denom) if denom > 0 else None

    return {
        "plan": {"schema": plan.get("schema_version"),
                 "counters": pc,
                 "short": len(short), "need_targets": need_targets,
                 "blank_targets": blank_targets},
        # D 档：徽章量测的**实际工作量**（plan 侧；缓存命中的行为 0 = 这次没扫/没量）
        "work": {k: (pc.get(k) or 0) for k in
                 ("badge_calls", "scan_px", "badge_deferred")},
        "run": {"schema": rep.get("schema_version"), "jobs": rep.get("jobs"),
                "ok": rep.get("ok"), "status": rep.get("status", {}),
                "timings": rep.get("timings", {}), "counters": rep.get("counters", {}),
                "resume": rep.get("resume")},
        "cache": {"hit": hit, "miss": miss, "fit_hit": fit_hit,
                  "fit_miss": fit_miss, "h": h_cache,
                  "enabled": pc.get("cache_enabled")},
        "join": join,
        "plan_timings": stage,
        "quality": {
            "blank_rate": (blank_targets / need_targets) if need_targets else None,
            "level_consistent": level_consistent,
            "level_tiers": level_tiers,
            "level_known_targets": level_known_targets,
            "n_out": len(outputs),
        },
        "outputs": outputs,
    }


def _fingerprint_of(eval_root: Path, arm: dict, tag: str) -> dict:
    fp = json.loads((eval_root / "fingerprint.json").read_text(encoding="utf-8"))
    return {"corpus_sha256": fp["corpus_sha256"], "code": _git_head(),
            "workers": int(arm.get("workers", 8)),
            "seed": int(arm.get("seed", 20261010)),
            "cache": str(arm.get("cache", "cold")),
            "arm": arm.get("name", tag)}


def _one_rep(arm: dict, snapshot: Path, eval_root: Path, tag: str, i: int,
             fingerprint: dict, run_dir: Path) -> dict:
    out_dir = eval_root / "out" / f"{tag}-{i}"
    wall = _run_once(arm, snapshot, out_dir, run_dir / f"rep{i}")
    metrics = _collect(snapshot, out_dir)
    rec = {"tag": tag, "repeat": i, "arm": arm, "fingerprint": fingerprint,
           "wall_s": wall, **metrics}
    _write_json(run_dir / f"rep{i}.json", rec)
    print(f"[{tag}] rep{i} plan {wall['plan']}s + run {wall['run']}s "
          f"· 出片 {metrics['quality']['n_out']} · 配空 {metrics['plan']['blank_targets']}")
    return rec


def _finish_tag(tag: str, arm: dict, fingerprint: dict, reps: list[dict], run_dir: Path) -> dict:
    # 同一臂内的噪声底（评测台用它判断「差多少才算真的」）
    # 口径按 docs/评测方案.html §4 预注册：CV = (max-min) / median（不是 stdev/mean ——
    # 后者对 3 次重复只有前者约 0.4 倍，会把「带内抖动」判成收益）
    totals = [r["wall_s"]["total"] for r in reps]
    cv = ((max(totals) - min(totals)) / statistics.median(totals)) if len(totals) > 1 else None
    # `compare` 的 H_cache 判据读这份摘要（不是逐遍的 deltas.json）⇒ 汇总后必须留一个
    # 同名字段；此前只有 `*_median` 散字段，H_cache 会误判成「计划里没有 cache_hit」。
    _cache = {
        "hit": _safe_median([(r.get("cache") or {}).get("hit") for r in reps]),
        "miss": _safe_median([(r.get("cache") or {}).get("miss") for r in reps]),
        "fit_hit": _safe_median([(r.get("cache") or {}).get("fit_hit") for r in reps]),
        "fit_miss": _safe_median([(r.get("cache") or {}).get("fit_miss") for r in reps]),
        "h": _safe_median([(r.get("cache") or {}).get("h") for r in reps]),
        "enabled": all((r.get("cache") or {}).get("enabled") for r in reps),
    }
    summary = {
        "tag": tag, "arm": arm, "fingerprint": fingerprint, "repeats": len(reps),
        # 产品退出码（0 产够 / 3 缺口 >5% 的口径判定）：照记不隐藏，两臂不一致时能在报告里看到
        "rc_values": {"plan": sorted({r["wall_s"]["plan_rc"] for r in reps}),
                      "run": sorted({r["wall_s"]["run_rc"] for r in reps})},
        "wall_s_median": statistics.median(totals),
        "wall_s_values": totals,
        # 热臂的预热耗时（不计入 total）：它证明"填一次缓存"要多久，用于摊销估算
        "warmup_s_median": _safe_median([r["wall_s"].get("warmup") for r in reps]),
        "cv": cv,
        "cache": _cache,
        "h_cache_median": _cache["h"],
        "cache_hit_median": _cache["hit"],
        "cache_miss_median": _cache["miss"],
        "fit_hit_median": _cache["fit_hit"],
        "fit_miss_median": _cache["fit_miss"],
        "n_out_median": statistics.median(r["quality"]["n_out"] for r in reps),
        "blank_targets_median": statistics.median(r["plan"]["blank_targets"] for r in reps),
        "need_targets_median": statistics.median(r["plan"]["need_targets"] for r in reps),
        "blank_rate_median": statistics.median(
            r["quality"]["blank_rate"] for r in reps
            if r["quality"]["blank_rate"] is not None) if any(
            r["quality"]["blank_rate"] is not None for r in reps) else None,
        "level_consistent_median": statistics.median(
            r["quality"]["level_consistent"] for r in reps
            if r["quality"]["level_consistent"] is not None) if any(
            r["quality"]["level_consistent"] is not None for r in reps) else None,
        # C 档：三层命中数的中位（`same` / `unknown` / `degraded` 是配对，`excluded` 是审计行）
        "level_tiers_median": {
            k: statistics.median((r["quality"].get("level_tiers") or {}).get(k, 0) for r in reps)
            for k in ("same", "unknown", "degraded", "cross", "excluded")},
        "level_known_targets_median": statistics.median(
            r["quality"].get("level_known_targets", 0) for r in reps),
        "counters_median": {
            k: statistics.median((r["plan"]["counters"] or {}).get(k, 0) for r in reps)
            for k in ("measure", "badge_calls", "badge_found")},
        "work_median": {
            k: statistics.median((r.get("work") or {}).get(k, 0) for r in reps)
            for k in ("badge_calls", "scan_px", "badge_deferred")},
        "outputs": reps[0]["outputs"],   # 同一臂内已用 md5 验过复现（见 compare 的自检）
        # A 档对账（只有开了 `--livepipe-join` 的臂才有；确定性产物，取第一遍即可）
        "join": reps[0].get("join"),
    }
    _write_json(run_dir / "summary.json", summary)
    print(f"→ {run_dir / 'summary.json'}"
          + (f"（CV {cv:.1%}）" if cv is not None else "（单次，无噪声底）"))
    return summary


def cmd_run(args) -> int:
    arm = json.loads(Path(args.arm).read_text(encoding="utf-8"))
    snapshot = Path(args.snapshot).expanduser().resolve()
    eval_root = Path(args.eval_root).expanduser().resolve()
    tag = args.tag
    repeat = int(args.repeat or arm.get("repeat", 3))
    run_dir = eval_root / "runs" / tag
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)

    fingerprint = _fingerprint_of(eval_root, arm, tag)
    reps = [_one_rep(arm, snapshot, eval_root, tag, i, fingerprint, run_dir)
            for i in range(1, repeat + 1)]
    _finish_tag(tag, arm, fingerprint, reps, run_dir)
    return 0


def cmd_pair(args) -> int:
    """两臂**交错**跑：奇数轮 A→B、偶数轮 B→A（压热漂移，评测方案 §4）。

    同配置两臂时它就是 placebo（噪声底）；不同配置时它就是一次 A/B。两臂的 rep 文件名
    用同一个轮次号，`compare`/`baseline` 照旧按 `runs/<tag>` 读。
    """
    arms = {"a": json.loads(Path(args.a_arm).read_text(encoding="utf-8")),
            "b": json.loads(Path(args.b_arm).read_text(encoding="utf-8"))}
    tags = {"a": args.tag_a, "b": args.tag_b}
    snapshot = Path(args.snapshot).expanduser().resolve()
    eval_root = Path(args.eval_root).expanduser().resolve()
    repeat = int(args.repeat or max(int(arms["a"].get("repeat", 3)),
                                    int(arms["b"].get("repeat", 3))))
    run_dirs = {k: eval_root / "runs" / t for k, t in tags.items()}
    for d in run_dirs.values():
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    fps = {k: _fingerprint_of(eval_root, arms[k], tags[k]) for k in arms}
    reps: dict[str, list[dict]] = {"a": [], "b": []}
    for i in range(1, repeat + 1):
        order = ["a", "b"] if i % 2 else ["b", "a"]
        for k in order:
            reps[k].append(_one_rep(arms[k], snapshot, eval_root, tags[k], i, fps[k], run_dirs[k]))

    for k in ("a", "b"):
        s = _finish_tag(tags[k], arms[k], fps[k], reps[k], run_dirs[k])
        s["interleaved_with"] = tags["b" if k == "a" else "a"]
        _write_json(run_dirs[k] / "summary.json", s)
    print(f"交错完成：{repeat} 轮 × 2 臂（第 1 轮 A→B，第 2 轮 B→A，交替）")
    return 0


# ---------------------------------------------------------------- crash / resume (E)

def _journal_lines(path: Path) -> int:
    """进度 journal 的有效行数。被 SIGKILL 打断时最后一行可能是残行，这里只数非空行；
    `--resume` 自己会跳过解析不了的残行（见 `expand_produce.cmd_run`）。"""
    try:
        with path.open(encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except FileNotFoundError:
        return 0


def _product_cmd(arm: dict, snapshot: Path, mode: str, out_dir: Path | None = None,
                 extra: tuple | list = ()) -> list[str]:
    cmd = [sys.executable, str(PRODUCT), "--mode", mode, "--source", str(snapshot),
           "--workers", str(int(arm.get("workers", 8))),
           "--seed", str(int(arm.get("seed", 20261010)))]
    if out_dir is not None:
        cmd += ["--out", str(out_dir)]
    flags = (arm.get("plan_flags") or []) if mode == "plan" else (arm.get("run_flags") or [])
    return cmd + list(flags) + list(extra)


def cmd_crash(args) -> int:
    """E 档验收：真 SIGKILL 一次，再用 `--resume` 续跑，对拍三件事。

    1. **接得上**：journal 里已完成的张数被复用（`reused > 0`），不是从零重来；
    2. **一字不差**：续跑后的成品逐图 md5 与完整参照跑比较，差异必须为 0；
    3. **指纹会挡**：拿同一份 journal、换一个 seed 再 `--resume`，必须一张都不复用
       （`reused == 0` 且 `stale_fingerprint > 0`）——否则「复用」就成了盲复用。

    杀进程用 `start_new_session` + `killpg`，worker 子进程一起收掉，和真断电/被 OOM
    杀的现实一致；只 kill 父进程会让 worker 继续写盘，测出来的是假的。
    """
    arm = json.loads(Path(args.arm).read_text(encoding="utf-8"))
    snapshot = Path(args.snapshot).expanduser().resolve()
    eval_root = Path(args.eval_root).expanduser().resolve()
    tag = args.tag or "E-crash"
    kill_after = int(args.kill_after or 20)
    run_dir = eval_root / "runs" / tag
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    fp = _fingerprint_of(eval_root, arm, tag)
    env = os.environ.copy()
    env.update(arm.get("env") or {})

    # ① 参照跑：完整一遍，逐图 md5 是「续跑后必须一模一样」的基准
    ref_dir = eval_root / "runs" / f"{tag}-ref"
    if ref_dir.exists():
        shutil.rmtree(ref_dir)
    ref_dir.mkdir(parents=True)
    ref = _one_rep(arm, snapshot, eval_root, f"{tag}-ref", 1, fp, ref_dir)

    # ② 崩溃跑：plan 完再 Popen，等 journal 攒到 K 张，SIGKILL 整个进程组
    out_dir = eval_root / "out" / f"{tag}-crash"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    t_plan = time.perf_counter()
    plan_proc = subprocess.run(_product_cmd(arm, snapshot, "plan"), cwd=str(REPO),
                               capture_output=True, text=True, env=env)
    (run_dir / "crash-plan.log").write_text(
        plan_proc.stdout + "\n--- stderr ---\n" + plan_proc.stderr, encoding="utf-8")
    if plan_proc.returncode not in (0, 3):
        print(f"plan 退出码 {plan_proc.returncode}，见 {run_dir / 'crash-plan.log'}", file=sys.stderr)
        return 3
    plan_s = round(time.perf_counter() - t_plan, 3)

    journal = out_dir / "expand_run_progress.jsonl"
    t0 = time.perf_counter()
    proc = subprocess.Popen(_product_cmd(arm, snapshot, "run", out_dir), cwd=str(REPO),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, start_new_session=True)
    killed_at, kill_reason = None, None
    # ⚠️ deadline 必须和 `time.time()` 同一口径：`t0` 是 perf_counter（单调、无单位语义），
    # 拿它加秒数去和 wall clock 比，第一次循环就会「超时」并把刚起的进程杀掉（已踩过）。
    deadline = time.time() + float(args.timeout)
    while True:
        n = _journal_lines(journal)
        if n >= kill_after:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:   # 极少数卡在 uninterruptible 的 IO
                proc.kill()
                proc.wait(timeout=10)
            killed_at, kill_reason = _journal_lines(journal), "journal"
            break
        if proc.poll() is not None:              # 自己跑完了，没杀成
            break
        if time.time() > deadline:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=30)
            killed_at, kill_reason = _journal_lines(journal), "timeout"
            break
        time.sleep(0.2)
    wall_crash = round(time.perf_counter() - t0, 3)
    out, err = proc.communicate()
    (run_dir / "crash-run.log").write_text(
        (out or "") + "\n--- stderr ---\n" + (err or ""), encoding="utf-8")
    if killed_at is None:
        print(f"⚠️ 进程在 journal 攒到 {kill_after} 行前就退出了（退出码 {proc.returncode}）",
              file=sys.stderr)
        verdicts = [{"metric": "E_kill", "status": "fail",
                     "note": "没杀掉：进程提前结束，测不到断点"}]
        result = {"kind": "crash_resume", "tag": tag, "fingerprint": fp,
                  "kill": {"requested_after_jobs": kill_after, "killed_at": None,
                           "exit": proc.returncode},
                  "verdicts": verdicts, "exit": 3}
        _write_json(Path(args.out), result)
        return 3

    # ③ 续跑：同一 out 目录 + `--resume`
    t1 = time.perf_counter()
    res_proc = subprocess.run(_product_cmd(arm, snapshot, "run", out_dir, ["--resume"]),
                              cwd=str(REPO), capture_output=True, text=True, env=env)
    wall_resume = round(time.perf_counter() - t1, 3)
    (run_dir / "resume.log").write_text(
        res_proc.stdout + "\n--- stderr ---\n" + res_proc.stderr, encoding="utf-8")
    rep_path = out_dir / "expand_run_report.json"
    rep = json.loads(rep_path.read_text(encoding="utf-8")) if rep_path.exists() else {}
    rs = rep.get("resume") or {}
    got = {p.relative_to(out_dir).as_posix(): _md5(p)
           for p in sorted(out_dir.rglob("扩展*.jpg"))}
    diff = _md5_diff(ref["outputs"], got)

    # ④ 指纹闸：拷一份 out（journal 一起带走），换 seed 再 --resume —— 必须零复用。
    #    拷出来跑，避免把已经验过的续跑产物覆盖掉。
    seed_dir = eval_root / "out" / f"{tag}-seed"
    if seed_dir.exists():
        shutil.rmtree(seed_dir)
    shutil.copytree(out_dir, seed_dir)
    seed_proc = subprocess.run(_product_cmd(arm, snapshot, "run", seed_dir,
                                            ["--resume", "--seed", "12345"]),
                               cwd=str(REPO), capture_output=True, text=True, env=env)
    (run_dir / "seed-mismatch.log").write_text(
        seed_proc.stdout + "\n--- stderr ---\n" + seed_proc.stderr, encoding="utf-8")
    srep_path = seed_dir / "expand_run_report.json"
    srep = json.loads(srep_path.read_text(encoding="utf-8")) if srep_path.exists() else {}
    srs = srep.get("resume") or {}

    verdicts = [
        {"metric": "E_kill", "status": "pass" if killed_at >= kill_after else "fail",
         "requested": kill_after, "killed_at": killed_at, "reason": kill_reason},
        {"metric": "E_reuse", "status": "pass" if int(rs.get("reused") or 0) > 0 else "fail",
         "reused": rs.get("reused"), "redone": rs.get("redone"),
         "missing_file": rs.get("missing_file")},
        {"metric": "E_identical", "status": "pass" if diff == 0 else "fail", "md5_diff": diff},
        {"metric": "E_seed_guard",
         "status": "pass" if (int(srs.get("reused") or 0) == 0
                              and int(srs.get("stale_fingerprint") or 0) > 0) else "fail",
         "reused": srs.get("reused"), "stale_fingerprint": srs.get("stale_fingerprint")},
    ]
    code = 0 if all(v["status"] == "pass" for v in verdicts) else 3
    result = {
        "kind": "crash_resume", "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tag": tag, "arm": arm.get("name", tag), "fingerprint": fp,
        "kill": {"requested_after_jobs": kill_after, "killed_at": killed_at,
                 "reason": kill_reason, "exit": proc.returncode,
                 "journal_lines_at_kill": killed_at,
                 "plan_s": plan_s, "run_wall_s": wall_crash},
        "reference": {"outputs": len(ref["outputs"]), "wall_s": ref["wall_s"]},
        "resume": rs, "resume_wall_s": wall_resume,
        "outputs": {"count": len(got), "md5_diff_vs_reference": diff,
                    "sha256": _sha256_text("".join(f"{k}:{v}\n" for k, v in sorted(got.items())))[:16]},
        "seed_mismatch": {"seed": 12345, **srs},
        "verdicts": verdicts, "exit": code,
    }
    _write_json(Path(args.out), result)
    print(f"崩溃 @ journal {killed_at} 行（{kill_reason}）→ 续跑复用 {rs.get('reused')} 张"
          f" · 重出 {rs.get('redone')} 张 · md5 差 {diff}")
    for v in verdicts:
        print(f"  {v['metric']:<13} {v['status']}")
    print(f"→ {args.out}（退出码 {code}）")
    return code


# ---------------------------------------------------------------- compare

def _load_summary(p: Path) -> dict:
    p = Path(p)
    if p.is_dir():
        p = p / "summary.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _md5_diff(a: dict, b: dict) -> int:
    keys = set(a) | set(b)
    return sum(1 for k in keys if a.get(k) != b.get(k))


def _p95(vals_sorted: list[float]) -> float:
    """nearest-rank p95（不插值）：N 很小时 p95 就等于最大值，报告里要看得见这一点。"""
    if not vals_sorted:
        raise ValueError("empty")
    return vals_sorted[max(0, min(len(vals_sorted) - 1, int(round(0.95 * (len(vals_sorted) - 1)))))]


def _stat(vals: list[float], digits: int = 3) -> dict | None:
    vals = sorted(float(v) for v in vals if v is not None)
    if not vals:
        return None
    return {"n": len(vals),
            "median": round(statistics.median(vals), digits),
            "p95": round(_p95(vals), digits),
            "min": round(vals[0], digits), "max": round(vals[-1], digits)}


def _safe_median(vals) -> float | None:
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def cmd_compare(args) -> int:
    a = _load_summary(Path(args.a))
    b = _load_summary(Path(args.b))
    th = json.loads(Path(args.thresholds).read_text(encoding="utf-8"))
    verdicts = []

    # 四指纹（评测方案 §2）：语料 / 代码 / 环境。任一不同 ⇒ 两臂不可比（退出 4）。
    fa, fb = a.get("fingerprint") or {}, b.get("fingerprint") or {}
    same_corpus = bool(fa.get("corpus_sha256")) and fa.get("corpus_sha256") == fb.get("corpus_sha256")
    same_code = bool(fa.get("code")) and fa.get("code") == fb.get("code")
    same_env = (fa.get("workers") == fb.get("workers")
                and fa.get("seed") == fb.get("seed"))
    fp_ok = same_corpus and same_code and same_env
    if not fp_ok:
        why = [n for n, ok in (("语料", same_corpus), ("代码", same_code), ("环境", same_env)) if not ok]
        print(f"⚠️ 两臂指纹不一致（{'/'.join(why)}）⇒ 不可比", file=sys.stderr)

    # 噪声带：**优先用冻结的 baseline.json**（那是预注册判据）；没有才退回本臂 CV。
    # `baseline.noise.band_pct` 已经是「2×CV」，门槛再从 thresholds 乘上倍数。
    band_pct = None
    noise_source = "none"
    if getattr(args, "baseline", None):
        bl = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        if (bl.get("fingerprint") or {}).get("corpus_sha256") != fa.get("corpus_sha256"):
            print("⚠️ baseline 的语料指纹与本轮不同 ⇒ 噪声带不适用，本轮判不可比", file=sys.stderr)
            fp_ok = False
        band_pct = (bl.get("noise") or {}).get("band_pct")
        noise_source = "baseline"
    if band_pct is None:
        for s in (a, b):
            if s.get("cv") is not None:
                band_pct = s["cv"] * 200.0     # CV → 2×CV（与 baseline 同口径）
                noise_source = "arm-cv"
                break
    cv_pct = band_pct / 2.0 if band_pct is not None else None
    mult = float((th.get("T_total") or {}).get("min_delta_over_noise", 2))
    gate_pct = cv_pct * mult if cv_pct is not None else None

    a_med, b_med = a["wall_s_median"], b["wall_s_median"]
    gain_pct = (a_med - b_med) / a_med * 100.0 if a_med else None
    common = {"noise_pct": None if cv_pct is None else round(cv_pct, 2),
              "band_pct": None if band_pct is None else round(band_pct, 2),
              "gate_pct": None if gate_pct is None else round(gate_pct, 2),
              "noise_source": noise_source}
    if gain_pct is None or gate_pct is None:
        verdicts.append({"metric": "T_total", "status": "inconclusive", "note": "缺噪声底", **common})
    elif gain_pct > max(gate_pct, 0.01):
        verdicts.append({"metric": "T_total", "status": "pass", "value_pct": round(gain_pct, 2),
                         "note": "超出噪声带", **common})
    elif gain_pct < -max(gate_pct, 0.01):
        verdicts.append({"metric": "T_total", "status": "fail", "value_pct": round(gain_pct, 2),
                         "note": "显著更慢", **common})
    else:
        verdicts.append({"metric": "T_total", "status": "no_difference",
                         "value_pct": round(gain_pct, 2),
                         "note": "带内 ⇒ 不记收益", **common})

    if th.get("N_out") and a["n_out_median"] is not None and b["n_out_median"] is not None:
        ok = b["n_out_median"] >= a["n_out_median"]
        verdicts.append({"metric": "N_out", "status": "pass" if ok else "fail",
                         "a": a["n_out_median"], "b": b["n_out_median"]})

    # D 档：徽章量测的**实际工作量**（plan meta.counters）。
    # `N_badge` = 本趟真的调了 `badge_rect_of` 的图数；`N_scan_px` = 列扫描处理的像素数。
    # 两者都只许**下降**（D 只该省工作量，不该改变任何结论）——「不上升」是硬线，
    # 降幅是收益；`badge_deferred` 只报告（说明有多少图被延后，0 也是合法结果）。
    wa, wb = a.get("work_median") or {}, b.get("work_median") or {}
    if th.get("N_badge"):
        va, vb = wa.get("badge_calls", 0), wb.get("badge_calls", 0)
        ok = vb <= va
        verdicts.append({"metric": "N_badge", "status": "pass" if ok else "fail",
                         "a": va, "b": vb, "deferred_b": wb.get("badge_deferred", 0),
                         "note": "工作量只许下降；deferred=0 说明这份语料没有可延后的图"
                                 "（目标自己的图必须每张都量，bug-284）"})
    if th.get("N_scan_px"):
        va, vb = wa.get("scan_px", 0), wb.get("scan_px", 0)
        red = ((va - vb) / va * 100.0) if va else None
        need = float(th["N_scan_px"].get("min_reduction_pct", 0))
        ok = red is not None and red >= need
        verdicts.append({"metric": "N_scan_px", "status": "pass" if ok else "fail",
                         "a": va, "b": vb,
                         "reduction_pct": None if red is None else round(red, 2)})

    if th.get("R_blank"):
        rb, ra = th["R_blank"], a["blank_rate_median"]
        if ra is None or b["blank_rate_median"] is None:
            verdicts.append({"metric": "R_blank", "status": "inconclusive", "note": "缺配空率"})
        else:
            over = (b["blank_rate_median"] - ra) * 100.0
            ok = over <= rb["max_over_baseline_pp"] and b["blank_rate_median"] * 100.0 <= rb["abs_max"]
            verdicts.append({"metric": "R_blank", "status": "pass" if ok else "fail",
                             "a_pct": round(ra * 100, 2), "b_pct": round(b["blank_rate_median"] * 100, 2),
                             "over_baseline_pp": round(over, 2)})

    if th.get("R_lvl"):
        v = b["level_consistent_median"]
        if v is None:
            verdicts.append({"metric": "R_lvl", "status": "inconclusive",
                             "note": "等级字段为 null 或分母为 0（C 未开）",
                             "tiers": b.get("level_tiers_median")})
        else:
            ok = v >= th["R_lvl"]["min"]
            verdicts.append({"metric": "R_lvl", "status": "pass" if ok else "fail",
                             "b": round(v, 4),
                             "tiers": b.get("level_tiers_median"),
                             "known_targets": b.get("level_known_targets_median")})

    if th.get("E_out") is not None:
        diff = _md5_diff(a.get("outputs") or {}, b.get("outputs") or {})
        ok = diff <= int(th["E_out"].get("max", 0))
        verdicts.append({"metric": "E_out", "status": "pass" if ok else "fail", "diff": diff})

    # A 档：只有 B 臂真落了 `livepipe_join.json` 才判。基线臂没有这张表时**跳过**——
    # 否则 thresholds 里一写 J_*，所有对比（包括 placebo）都会被判成 inconclusive。
    jb = b.get("join")
    if jb:
        if th.get("J_join"):
            cov = jb.get("coverage")
            if cov is None:
                verdicts.append({"metric": "J_join", "status": "inconclusive",
                                 "note": "对账表缺覆盖率"})
            else:
                ok = cov >= float(th["J_join"].get("min", 1.0))
                verdicts.append({"metric": "J_join", "status": "pass" if ok else "fail",
                                 "coverage_pct": round(cov * 100, 2),
                                 "images": jb.get("images")})
        if th.get("J_conflict") is not None:
            n_cf = jb.get("conflict_count")
            ok = n_cf is not None and n_cf <= int(th["J_conflict"].get("max", 0))
            verdicts.append({"metric": "J_conflict", "status": "pass" if ok else "fail",
                             "conflicts": n_cf, "rows_multi": jb.get("rows_multi")})

    # B 档：只在这条臂**真的开了缓存**（arm cache = warm）时判命中率 ——
    # 冷臂 hit=0 是定义，不是失败；A/A、placebo 之类的对比不该被这条拖成 inconclusive。
    cb = b.get("cache") or {}
    if th.get("H_cache") and (b.get("fingerprint") or {}).get("cache") == "warm":
        h = cb.get("h")
        if h is None:
            verdicts.append({"metric": "H_cache", "status": "inconclusive",
                             "note": "计划里没有 cache_hit/cache_miss"})
        else:
            ok = h >= float(th["H_cache"].get("min", 1.0))
            verdicts.append({"metric": "H_cache", "status": "pass" if ok else "fail",
                             "h": round(h, 4), "hit": cb.get("hit"),
                             "miss": cb.get("miss"), "fit_hit": cb.get("fit_hit"),
                             "fit_miss": cb.get("fit_miss")})

    statuses = {v["status"] for v in verdicts}
    if not fp_ok:
        code = 4
    elif "fail" in statuses:
        code = 3
    elif "inconclusive" in statuses:
        code = 6
    else:
        code = 0

    out = {"a": {"tag": a["tag"], "median_s": a_med, "cv": a.get("cv"),
                 "rc": a.get("rc_values"), "cache": a.get("cache") or None},
           "b": {"tag": b["tag"], "median_s": b_med, "cv": b.get("cv"),
                 "rc": b.get("rc_values"), "cache": b.get("cache") or None,
                 "warmup_s": b.get("warmup_s_median")},
           "noise_pct": None if cv_pct is None else round(cv_pct, 2),
           "band_pct": None if band_pct is None else round(band_pct, 2),
           "gate_pct": None if gate_pct is None else round(gate_pct, 2),
           "noise_source": noise_source,
           "delta_pct": None if gain_pct is None else round(gain_pct, 2),
           "fingerprint_ok": fp_ok, "same_corpus": same_corpus,
           "same_code": same_code, "same_env": same_env,
           "join": jb or None,
           "verdicts": verdicts, "exit": code}
    _write_json(Path(args.out), out)

    if gain_pct is not None and gate_pct is not None:
        print(f"A {a['tag']} {a_med:.2f}s · B {b['tag']} {b_med:.2f}s · Δ {gain_pct:+.1f}%"
              f"（CV {cv_pct:.1f}% · 门槛 {gate_pct:.1f}% · 来源 {noise_source}）")
    else:
        print(f"A {a['tag']} {a_med:.2f}s · B {b['tag']} {b_med:.2f}s")
    for v in verdicts:
        print(f"  {v['metric']:<8} {v['status']:<14} {v.get('note', '')}")
    print(f"→ {args.out}（退出码 {code}）")
    return code


# ---------------------------------------------------------------- verify

def cmd_verify(args) -> int:
    corpus = Path(args.corpus).expanduser().resolve()
    expect = Path(args.md5).read_text(encoding="utf-8").strip().splitlines()
    now = _md5_lines(corpus)
    if expect == now:
        print(f"原件未改动（{len(now)} 个文件）✅")
        return 0
    exp_set, now_set = set(expect), set(now)
    print(f"⚠️ 原件被改动：少了 {len(exp_set - now_set)} 条，多了/变了 {len(now_set - exp_set)} 条",
          file=sys.stderr)
    for line in list(now_set - exp_set)[:5]:
        print("  " + line, file=sys.stderr)
    return 5


# ---------------------------------------------------------------- baseline

def cmd_baseline(args) -> int:
    """把若干 `runs/<tag>`（同语料、同参数）合成一份基线：中位数 / p95 / CV / 噪声带。

    `--run` 可以给多次：同一个臂跑了几遍（如 A 和 placebo A2）就都并进来 —— 基线要的是
    **同一配置下的离散度**，不是两个不同配置的对比（那是 `compare`）。
    """
    reps = []
    for d in args.run:
        d = Path(d).expanduser().resolve()
        files = sorted(d.glob("rep*.json"))
        if not files:
            print(f"没有 rep*.json：{d}", file=sys.stderr)
            return 2
        reps += [json.loads(p.read_text(encoding="utf-8")) for p in files]

    corpus = {r["fingerprint"]["corpus_sha256"] for r in reps}
    envs = {(r["fingerprint"]["workers"], r["fingerprint"]["seed"]) for r in reps}
    if len(corpus) > 1:
        print("⚠️ 语料指纹不一致 ⇒ 不能合成一份基线", file=sys.stderr)
        return 4
    if len(envs) > 1:
        print(f"⚠️ workers/seed 不一致：{sorted(envs)} ⇒ 不可比", file=sys.stderr)
        return 4

    totals = [r["wall_s"]["total"] for r in reps]
    med = statistics.median(totals)
    # 与评测方案 §4 同一口径：CV = (max-min)/median
    cv = (max(totals) - min(totals)) / med if len(totals) > 1 and med else None

    wall = {phase: _stat([r["wall_s"][phase] for r in reps]) for phase in ("plan", "run", "total")}
    if cv is not None:
        wall["total"]["cv_pct"] = round(cv * 100.0, 2)

    counters_plan = [r["plan"]["counters"] or {} for r in reps]
    counters_run = [r["run"]["counters"] or {} for r in reps]
    counts = {
        "measure": _stat([c.get("measure", 0) for c in counters_plan], 1),
        "badge_calls": _stat([c.get("badge_calls", 0) for c in counters_plan], 1),
        "badge_found": _stat([c.get("badge_found", 0) for c in counters_plan], 1),
        "jobs": _stat([r["run"].get("jobs") or 0 for r in reps], 1),
        "ok": _stat([r["run"].get("ok") or 0 for r in reps], 1),
        "n_out": _stat([r["quality"]["n_out"] for r in reps], 1),
        "need_targets": _stat([r["plan"]["need_targets"] for r in reps], 1),
        "blank_targets": _stat([r["plan"]["blank_targets"] for r in reps], 1),
        "run_counters": {k: _safe_median([c.get(k, 0) for c in counters_run])
                         for k in ("cache_hit", "cache_miss", "badge_calls", "scan_px",
                                   "resume_reused")},
        # B 档：plan 侧缓存命中/未命中（冷臂 hit=0、miss=measure；热臂相反）
        "plan_cache": {k: _stat([c.get(k, 0) for c in counters_plan], 1)
                       for k in ("cache_hit", "cache_miss", "fit_hit", "fit_miss")},
    }

    # 分项：每遍先算 p50/p95，再跨遍取中位数（别把所有图混成一个大池 —— 那样会把
    # 「遍与遍之间的漂移」抹掉，正好是噪声底要量的东西）
    plan_timings = {}
    for seg in ("decode", "locate", "badge"):
        p50s = [r["plan_timings"][seg]["p50"] for r in reps if seg in (r.get("plan_timings") or {})]
        p95s = [r["plan_timings"][seg]["p95"] for r in reps if seg in (r.get("plan_timings") or {})]
        if p50s:
            plan_timings[seg] = {"p50": round(statistics.median(p50s), 3),
                                 "p95": round(statistics.median(p95s), 3)}
    run_timings = {}
    for seg in ("sprite", "render", "badge", "check", "save"):
        vals = [(r["run"].get("timings") or {}).get(seg) for r in reps]
        vals = [v for v in vals if v is not None]
        if vals:
            run_timings[seg] = round(statistics.median(vals), 3)

    fp_in = {}
    if args.eval_root:
        fp_path = Path(args.eval_root).expanduser().resolve() / "fingerprint.json"
        if fp_path.exists():
            fp_in = json.loads(fp_path.read_text(encoding="utf-8"))

    out = {
        "schema_version": 1,
        "label": args.label or "baseline",
        # 适用范围（机器可读）：`sample-73` 这类标签提醒后来者「这不是生产噪声带」
        "scope": args.scope or "",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "runs": [Path(d).name for d in args.run],
        "repeats": len(reps),
        "fingerprint": {
            "corpus_sha256": next(iter(corpus)),
            "corpus_files": fp_in.get("files"),
            "corpus_source": fp_in.get("source"),
            "code": reps[0]["fingerprint"].get("code"),
            "workers": reps[0]["fingerprint"]["workers"],
            "seed": reps[0]["fingerprint"]["seed"],
            "arm": reps[0]["fingerprint"].get("arm"),
        },
        "wall_s": wall,
        "noise": {
            "cv_pct": None if cv is None else round(cv * 100.0, 2),
            # 判定门槛：Δ% > 2 × CV%（评测方案 §4 预注册；跑完不许改）
            "band_pct": None if cv is None else round(cv * 200.0, 2),
            "rule": "Δ > 2×CV 才算收益；带内写 no_difference",
            "method": "CV = (max-min)/median，同臂多遍",
        },
        "counts": counts,
        "plan_timings_ms": plan_timings,
        # ⚠️ 这是**全部 job 该段耗时之和（CPU 秒）**，不是墙钟：除以 workers 才是墙钟量级。
        # 报告里的 `run_timings{}` 字段本来就是这个口径（`cmd_run` 把逐张 ms 求和再 /1000）。
        "run_timings_agg_s": run_timings,
        "definitions": {
            "plan_timings_ms": "逐图该段耗时（ms），先按遍算 p50/p95，再跨遍取中位数",
            "run_timings_agg_s": "全部 job 的该段耗时之和（CPU 秒）；除以 workers ≈ 墙钟",
        },
        "quality": {
            "blank_rate": _safe_median([r["quality"]["blank_rate"] for r in reps]),
            "level_consistent": _safe_median([r["quality"]["level_consistent"] for r in reps]),
        },
        # 只存输出树的**摘要**（不存逐文件 md5 表）：那张表的键是用户目录名，落到仓库里就是泄漏；
        # 基线只需要「这一版产物长什么样」的指纹，逐文件对拍是 `compare` 的事。
        "outputs": {"count": len(reps[0]["outputs"]),
                    "sha256": _sha256_text("\n".join(f"{k}  {v}" for k, v in
                                                     sorted(reps[0]["outputs"].items())))},
        "notes": args.note or [],
    }
    _write_json(Path(args.out).expanduser().resolve(), out)

    print(f"基线「{out['label']}」{len(reps)} 遍 · 语料 {out['fingerprint']['corpus_files'] or '?'} 文件"
          f" · 指纹 {out['fingerprint']['corpus_sha256'][:12]}…")
    for phase in ("plan", "run", "total"):
        s = wall[phase]
        print(f"  {phase:<5} 中位 {s['median']:.3f}s · p95 {s['p95']:.3f}s "
              f"· min {s['min']:.3f} / max {s['max']:.3f}")
    if cv is not None:
        print(f"  CV {cv * 100:.2f}% ⇒ 噪声带 ±{cv * 200:.2f}%（Δ 超过这个数才记收益）")
    print(f"→ {args.out}")
    return 0


# ---------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser(description="评测台：快照 / 跑臂 / 对比 / 校验")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="复制语料快照并算指纹")
    s.add_argument("--corpus", required=True)
    s.add_argument("--into", required=True)
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_snapshot)

    r = sub.add_parser("run", help="在快照上跑一臂 N 遍")
    r.add_argument("--snapshot", required=True)
    r.add_argument("--tag", required=True)
    r.add_argument("--arm", required=True)
    r.add_argument("--eval-root", required=True)
    r.add_argument("--repeat", type=int, default=None)
    r.set_defaults(func=cmd_run)

    pr = sub.add_parser("pair", help="两臂交错跑（A→B / B→A 交替），用于 placebo 或 A/B")
    pr.add_argument("--snapshot", required=True)
    pr.add_argument("--a-arm", required=True)
    pr.add_argument("--b-arm", required=True)
    pr.add_argument("--tag-a", required=True)
    pr.add_argument("--tag-b", required=True)
    pr.add_argument("--eval-root", required=True)
    pr.add_argument("--repeat", type=int, default=None)
    pr.set_defaults(func=cmd_pair)

    cr = sub.add_parser("crash", help="E 档：SIGKILL 后 --resume 续跑，验复用/一致/指纹闸")
    cr.add_argument("--snapshot", required=True)
    cr.add_argument("--arm", required=True)
    cr.add_argument("--eval-root", required=True)
    cr.add_argument("--tag", default="E-crash")
    cr.add_argument("--kill-after", type=int, default=20,
                    help="journal 攒到多少张时 SIGKILL（默认 20；本语料 53 张）")
    cr.add_argument("--timeout", type=float, default=300.0, help="等 journal 的上限秒数")
    cr.add_argument("--out", required=True)
    cr.set_defaults(func=cmd_crash)

    c = sub.add_parser("compare", help="对比两臂并判定")
    c.add_argument("--a", required=True)
    c.add_argument("--b", required=True)
    c.add_argument("--thresholds", required=True)
    c.add_argument("--baseline", default=None,
                   help="冻结的 baseline.json：用它预注册的噪声带判定（推荐）")
    c.add_argument("--out", required=True)
    c.set_defaults(func=cmd_compare)

    b = sub.add_parser("baseline", help="同臂多遍合成一份基线（中位数 / p95 / CV / 噪声带）")
    b.add_argument("--run", action="append", required=True, help="runs/<tag>，可重复（如 A 与 placebo）")
    b.add_argument("--out", required=True)
    b.add_argument("--label", default="")
    b.add_argument("--scope", default="", help="适用范围，如 sample-73 / full-1478")
    b.add_argument("--eval-root", default=None, help="读它的 fingerprint.json（语料文件数 / 来源）")
    b.add_argument("--note", action="append", default=[], help="写进报告的说明，可重复")
    b.set_defaults(func=cmd_baseline)

    v = sub.add_parser("verify", help="核对原件没被改动")
    v.add_argument("--corpus", required=True)
    v.add_argument("--md5", required=True)
    v.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
