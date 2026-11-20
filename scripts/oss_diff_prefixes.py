#!/usr/bin/env python3
"""对比两个 OSS 前缀并可选把「旧前缀有、新前缀没有」的对象补过去。

用于 2026-11-19 路径更正：旧前缀 ``奇瑞汽车/直播看管`` 与正确位置
``web/1/奇瑞汽车/直播看管`` 双向比对。

分类（按相对路径）：
- ``identical``：两边都有，大小 + ETag 相同；
- ``metadata_diff``：两边都有、大小相同但 ETag 不同（独立上传时 multipart ETag
  可能天然不同，不代表内容不同；默认不覆盖，只报告）；
- ``size_diff``：两边都有但大小不同（默认不覆盖，只报告）；
- ``only_old``：旧前缀有、新前缀没有 —— ``--fill`` 时服务端 CopyObject 补过去；
- ``only_new``：新前缀有、旧前缀没有（迁移后新写的数据，正常）。

用法：
    python3 scripts/oss_diff_prefixes.py \
        --old 奇瑞汽车/直播看管 --new web/1/奇瑞汽车/直播看管 [--fill]

    # --fill 只补 only_old；size_diff / metadata_diff 永远只报告
    # （内容不一致时人工判断，不自动覆盖正确位置的数据）
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
COPY_OBJECT_MAX = 5 * 1024 ** 3


def make_bucket() -> oss2.Bucket:
    cp = configparser.ConfigParser()
    cp.read(CREDS)
    sec = cp["default"]
    auth = oss2.Auth(sec["aws_access_key_id"], sec["aws_secret_access_key"])
    return oss2.Bucket(auth, ENDPOINT, BUCKET)


def list_objects(bucket, prefix: str) -> dict[str, tuple[int, str]]:
    prefix = prefix.strip("/") + "/"
    out: dict[str, tuple[int, str]] = {}
    for obj in oss2.ObjectIterator(bucket, prefix=prefix, max_keys=1000):
        out[obj.key[len(prefix):]] = (obj.size, obj.etag)
    return out


def _retry(fn, tries=3, delay=1.0):
    last = None
    for i in range(tries):
        try:
            return fn()
        except (oss2.exceptions.RequestError, oss2.exceptions.ServerError) as exc:
            last = exc
            time.sleep(delay * (i + 1))
    raise last


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", required=True, help="旧前缀（桶内相对路径）")
    ap.add_argument("--new", required=True, help="正确前缀（桶内相对路径）")
    ap.add_argument("--fill", action="store_true", help="把 only_old 复制到新前缀")
    ap.add_argument("--workers", type=int, default=32, help="复制并发数")
    ap.add_argument("--report", default=None, help="报告 JSON 路径")
    args = ap.parse_args()

    bucket = make_bucket()
    t0 = time.time()
    old = list_objects(bucket, args.old)
    print(f"旧 {args.old}: {len(old)} 对象 / {sum(s for s, _ in old.values())} B", flush=True)
    new = list_objects(bucket, args.new)
    print(f"新 {args.new}: {len(new)} 对象 / {sum(s for s, _ in new.values())} B", flush=True)

    only_old, only_new, size_diff, meta_diff = [], [], [], []
    for rel, (size, etag) in old.items():
        hit = new.get(rel)
        if hit is None:
            only_old.append({"key": rel, "bytes": size})
        elif hit[0] != size:
            size_diff.append({"key": rel, "old_bytes": size, "new_bytes": hit[0]})
        elif hit[1] != etag:
            meta_diff.append({"key": rel, "bytes": size, "old_etag": etag, "new_etag": hit[1]})
    for rel, (size, _) in new.items():
        if rel not in old:
            only_new.append({"key": rel, "bytes": size})

    only_old.sort(key=lambda x: -x["bytes"])
    size_diff.sort(key=lambda x: -x["old_bytes"])
    meta_diff.sort(key=lambda x: -x["bytes"])
    only_new.sort(key=lambda x: -x["bytes"])

    def total(items, field):
        return sum(i[field] for i in items)

    print(f"only_old {len(only_old)} / {total(only_old, 'bytes')} B")
    print(f"only_new {len(only_new)} / {total(only_new, 'bytes')} B（迁移后新写，正常）")
    print(f"size_diff {len(size_diff)}")
    print(f"metadata_diff {len(meta_diff)}（大小相同、ETag 不同）")
    for label, items in (("only_old", only_old), ("size_diff", size_diff),
                         ("metadata_diff", meta_diff)):
        for it in items[:10]:
            print(f"  {label}: {it}")

    filled = {"copied": 0, "failed": 0}
    failures: list[dict] = []
    if args.fill and only_old:
        old_prefix = args.old.strip("/") + "/"
        new_prefix = args.new.strip("/") + "/"

        def work(item):
            rel = item["key"]
            if item["bytes"] > COPY_OBJECT_MAX:
                return rel, f"对象 {item['bytes']} B 超过 CopyObject 5GB 上限"
            try:
                _retry(lambda: bucket.copy_object(BUCKET, old_prefix + rel, new_prefix + rel))
                return rel, None
            except Exception as exc:  # noqa: BLE001
                return rel, f"{type(exc).__name__}: {exc}"

        print(f"补 {len(only_old)} 个对象到新前缀", flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = [pool.submit(work, it) for it in only_old]
            for fut in as_completed(futs):
                rel, err = fut.result()
                if err:
                    filled["failed"] += 1
                    failures.append({"key": rel, "error": err})
                else:
                    filled["copied"] += 1
        rep_new = list_objects(bucket, args.new)
        still = [it for it in only_old if it["key"] not in rep_new]
        print(f"补完：copied {filled['copied']} / failed {filled['failed']} / "
              f"新前缀仍缺 {len(still)}")
    else:
        still = []

    rep = {
        "old": args.old, "new": args.new,
        "old_objects": len(old), "old_bytes": sum(s for s, _ in old.values()),
        "new_objects": len(new), "new_bytes": sum(s for s, _ in new.values()),
        "only_old": len(only_old), "only_old_bytes": total(only_old, "bytes"),
        "only_new": len(only_new), "only_new_bytes": total(only_new, "bytes"),
        "size_diff": len(size_diff), "metadata_diff": len(meta_diff),
        "samples": {
            "only_old": only_old[:20], "only_new": only_new[:20],
            "size_diff": size_diff[:20], "metadata_diff": meta_diff[:20],
        },
        "fill": args.fill, **filled,
        "still_missing_after_fill": [i["key"] for i in still[:20]],
        "elapsed_s": round(time.time() - t0, 1),
    }
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n")
    return 0 if not only_old and not size_diff else 1


if __name__ == "__main__":
    sys.exit(main())
