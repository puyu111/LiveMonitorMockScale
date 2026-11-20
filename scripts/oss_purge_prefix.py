#!/usr/bin/env python3
"""清空一个 OSS 前缀，但只在每个对象都能在目标前缀找到同大小副本时才删。

用于 2026-11-19 路径更正后的收尾：P1/P2 曾把对象写到桶根
``奇瑞汽车/直播看管/...``，已用 ``oss_relocate_prefix.py`` 服务端复制到
``web/1/奇瑞汽车/直播看管/...``；本脚本复核后删除桶根旧前缀。

用法：
    # 只核对 + 打计划（默认，不删）
    python3 scripts/oss_purge_prefix.py \
        --prefix 奇瑞汽车/直播看管/直播录屏 \
        --ensure web/1/奇瑞汽车/直播看管/直播录屏

    # 核对通过后真正删除
    python3 scripts/oss_purge_prefix.py --prefix ... --ensure ... --delete

要点：
- 删除前把源前缀与目标前缀全量 list 一遍；源里每个对象都必须能在目标里
  找到同名同字节的副本（``*/`` 目录占位对象只要求同名存在）。任何缺失/
  字节不符 ⇒ 退出码 2，一个对象都不删。
- 删除走 ``batch_delete_objects``（1000 键/请求），失败重试；不经过 goofys。
- ``--delete`` 后再全量 list 源前缀，必须为 0 才算成功（退出码 0）。
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
BATCH = 1000


def make_bucket() -> oss2.Bucket:
    cp = configparser.ConfigParser()
    cp.read(CREDS)
    sec = cp["default"]
    auth = oss2.Auth(sec["aws_access_key_id"], sec["aws_secret_access_key"])
    return oss2.Bucket(auth, ENDPOINT, BUCKET)


def list_objects(bucket, prefix: str) -> dict[str, int]:
    prefix = prefix.strip("/") + "/"
    out: dict[str, int] = {}
    for obj in oss2.ObjectIterator(bucket, prefix=prefix, max_keys=1000):
        out[obj.key[len(prefix):]] = obj.size
    return out


def _retry(fn, tries=5, delay=1.0):
    last = None
    for i in range(tries):
        try:
            return fn()
        except (oss2.exceptions.RequestError, oss2.exceptions.ServerError) as exc:
            last = exc
            time.sleep(delay * (i + 1))
    raise last


def check(src: dict[str, int], dst: dict[str, int]) -> list[dict]:
    """返回不能安全删除的对象（缺失或字节不符）。"""
    unsafe = []
    for rel, size in src.items():
        if rel.endswith("/") and size == 0:
            continue  # 目录占位对象没有数据
        if dst.get(rel) != size:
            unsafe.append({"key": rel, "src_bytes": size, "dst_bytes": dst.get(rel)})
    return unsafe


def delete_batch(bucket, keys: list[str]) -> int:
    res = _retry(lambda: bucket.batch_delete_objects(keys))
    return len(res.deleted_keys)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prefix", required=True, help="要清空的源前缀（桶内相对路径）")
    ap.add_argument("--ensure", required=True, help="安全副本所在前缀")
    ap.add_argument("--delete", action="store_true", help="核对通过后真正删除")
    ap.add_argument("--workers", type=int, default=8, help="并发删除批次数")
    ap.add_argument("--report", default=None, help="报告 JSON 路径")
    args = ap.parse_args()

    bucket = make_bucket()
    t0 = time.time()

    src = list_objects(bucket, args.prefix)
    print(f"源 {args.prefix}: {len(src)} 对象 / {sum(src.values())} B", flush=True)
    dst = list_objects(bucket, args.ensure)
    print(f"目标 {args.ensure}: {len(dst)} 对象", flush=True)

    unsafe = check(src, dst)
    if unsafe:
        print(json.dumps({"status": "refused", "unsafe": len(unsafe),
                          "sample": unsafe[:20]}, ensure_ascii=False, indent=2))
        return 2
    print("核对通过：源对象全部在目标有同字节副本", flush=True)

    if not args.delete:
        print("dry-run：未删除（加 --delete 执行）")
        return 0

    src_prefix = args.prefix.strip("/") + "/"
    keys = [src_prefix + rel for rel in src]
    chunks = [keys[i:i + BATCH] for i in range(0, len(keys), BATCH)]
    deleted = 0
    failures: list[str] = []

    def work(chunk):
        try:
            return delete_batch(bucket, chunk), None
        except Exception as exc:  # noqa: BLE001
            return 0, f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(work, c): c for c in chunks}
        for fut in as_completed(futs):
            n, err = fut.result()
            deleted += n
            if err:
                failures.append(err)
            if deleted % 50000 == 0:
                print(f"  已删除 {deleted}/{len(keys)}", flush=True)

    left = list_objects(bucket, args.prefix)
    rep = {
        "prefix": args.prefix, "ensure": args.ensure,
        "objects": len(keys), "bytes": sum(src.values()),
        "deleted": deleted, "failures": failures[:20],
        "remaining_objects": len(left),
        "elapsed_s": round(time.time() - t0, 1),
    }
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n")
    return 0 if not left and not failures else 1


if __name__ == "__main__":
    sys.exit(main())
