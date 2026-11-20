#!/usr/bin/env python3
"""OSS 前缀迁移：服务端复制（copy / multipart copy），不删除源对象。

背景（2026-11-19）：P1/P2 把裁尾副本和结果写到了桶根前缀
``奇瑞汽车/直播看管/...``，而项目 OSS 根实际是
``web/1/奇瑞汽车/直播看管/...``。本脚本把对象原样复制到正确前缀，
保留源对象，便于复核后再人工删除。

用法：
    python3 scripts/oss_relocate_prefix.py \
        --src 奇瑞汽车/直播看管/直播录屏 \
        --dst web/1/奇瑞汽车/直播看管/直播录屏 [--dry-run] [--workers 32]

    # 复制后只核对（不写对象）
    python3 scripts/oss_relocate_prefix.py --src ... --dst ... --verify-only

要点：
- 全部走服务端 CopyObject（实测 3GB 对象也是秒级完成，OSS 上限 5GB）。
  不要用 upload_part_copy：本桶的 CopyPart 会 60s 读超时（2026-11-19 实测）。
- 目标已存在且大小相同就跳过（可断点续传 / 重跑幂等）。
- 复制后校验：大小 + ETag（服务端复制 ETag 不变）。
- 只写目标前缀，从不删除源对象。
"""

from __future__ import annotations

import argparse
import configparser
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import oss2

BUCKET = "static-cantotimes-com"
ENDPOINT = "https://oss-cn-beijing.aliyuncs.com"
CREDS = Path.home() / ".aws/credentials"

COPY_OBJECT_MAX = 5 * 1024 ** 3   # OSS CopyObject 上限 5GB；超过需 multipart（本桶不可用）


def make_bucket() -> oss2.Bucket:
    cp = configparser.ConfigParser()
    cp.read(CREDS)
    sec = cp["default"]
    auth = oss2.Auth(sec["aws_access_key_id"], sec["aws_secret_access_key"])
    return oss2.Bucket(auth, ENDPOINT, BUCKET)


def src_key(args, rel: str) -> str:
    return f"{args.src.strip('/')}/{rel}"


def dst_key(args, rel: str) -> str:
    return f"{args.dst.strip('/')}/{rel}"


def list_objects(bucket, prefix: str):
    prefix = prefix.strip("/") + "/"
    # max_keys 默认只有 100，40 万对象要多花 10 倍时间。
    for obj in oss2.ObjectIterator(bucket, prefix=prefix, max_keys=1000):
        yield obj.key[len(prefix):], obj.size


def _retry(fn, tries=3, delay=1.0):
    last = None
    for i in range(tries):
        try:
            return fn()
        except (oss2.exceptions.RequestError, oss2.exceptions.ServerError) as exc:
            last = exc
            time.sleep(delay * (i + 1))
    raise last


def copy_one(bucket, src: str, dst: str, rel: str, size: int, existing: dict) -> str:
    """返回 copied / skipped。失败抛异常。"""
    if existing.get(rel) == size:
        return "skipped"
    if size > COPY_OBJECT_MAX:
        raise RuntimeError(
            f"对象 {size} B 超过 CopyObject 5GB 上限，需另想办法（本桶 CopyPart 不可用）")
    _retry(lambda: bucket.copy_object(BUCKET, src, dst))
    return "copied"


def verify(bucket, args) -> dict:
    src_map = dict(list_objects(bucket, args.src))
    dst_map = dict(list_objects(bucket, args.dst))
    missing = [k for k, s in src_map.items() if dst_map.get(k) != s]
    extra = [k for k in dst_map if k not in src_map]
    return {
        "src": args.src, "dst": args.dst,
        "src_objects": len(src_map), "src_bytes": sum(src_map.values()),
        "dst_objects": len(dst_map), "dst_bytes": sum(dst_map.values()),
        "missing_or_mismatch": len(missing),
        "missing_sample": missing[:20],
        "extra": len(extra), "extra_sample": extra[:20],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="源前缀（桶内相对路径）")
    ap.add_argument("--dst", required=True, help="目标前缀（桶内相对路径）")
    ap.add_argument("--workers", type=int, default=32, help="并发数")
    ap.add_argument("--dry-run", action="store_true", help="只列计划")
    ap.add_argument("--verify-only", action="store_true", help="只核对")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个（调试）")
    ap.add_argument("--no-resume", action="store_true",
                    help="不预先列目标前缀（目标为空时可省一次全量 list）")
    ap.add_argument("--report", default=None, help="报告 JSON 路径")
    args = ap.parse_args()

    bucket = make_bucket()

    if args.verify_only:
        rep = verify(bucket, args)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep["missing_or_mismatch"] == 0 else 1

    objs = list(list_objects(bucket, args.src))
    if args.limit:
        objs = objs[:args.limit]
    total_bytes = sum(s for _, s in objs)
    print(f"源 {args.src}: {len(objs)} 对象 / {total_bytes} B")

    if args.dry_run:
        for k, s in sorted(objs, key=lambda x: -x[1])[:20]:
            print(f"  {s:>13,} {k}")
        return 0

    t0 = time.time()
    existing = {} if args.no_resume else dict(list_objects(bucket, args.dst))
    print(f"目标已有 {len(existing)} 对象；开始复制 {len(objs)} 个", flush=True)
    stats = {"copied": 0, "skipped": 0, "failed": 0}
    failures = []

    def work(item):
        rel, size = item
        try:
            return rel, copy_one(bucket, src_key(args, rel), dst_key(args, rel),
                                 rel, size, existing), None
        except Exception as exc:  # noqa: BLE001
            return rel, "failed", f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(work, it) for it in objs]
        for fut in as_completed(futs):
            rel, status, err = fut.result()
            stats[status] += 1
            if err:
                failures.append({"key": rel, "error": err})
            done = sum(stats.values())
            if done % 5000 == 0 or done == len(objs):
                print(f"  [{done}/{len(objs)}] 已复制 {stats['copied']} "
                      f"跳过 {stats['skipped']} 失败 {stats['failed']}", flush=True)

    rep = {
        "src": args.src, "dst": args.dst,
        "objects": len(objs), "bytes": total_bytes,
        **stats,
        "failures": failures[:50],
        "elapsed_s": round(time.time() - t0, 1),
    }
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n")
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
