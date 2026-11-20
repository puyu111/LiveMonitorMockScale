#!/usr/bin/env python3
"""P3 下载链路：直播直写 OSS 的 ``.part`` 收尾；百度来源 bypy 列目录 + PCS 流式直写 OSS。

两个子命令：

``live``
    跑一个外部下载/录屏命令，让它把直播写到
    ``<root>/<事件>/<日期>/<平台>/<账号>/<名>.mp4.part``；命令退出 0 后校验字节/
    时长、改名成正式文件并写 ``.manifest.json``。下载失败绝不留正式文件。

    ``--cmd`` 里的 ``{part}`` / ``{final}`` / ``{dir}`` 会替换成 shell 转义后的
    路径；也可以直接用环境变量 ``OSS_PART`` / ``OSS_FINAL`` / ``OSS_DIR``。
    dypy 的参数不写死在这层，换下载器只改 ``--cmd``。

``baidu``
    ``bypy`` 只用来授权与列目录；文件内容走 bypy 的 PCS 下载接口**单请求顺序流式**
    直接写进 goofys 挂载点（``<final>.part`` → 校验 → 改名 + manifest），**不落
    本地暂存**。bypy 自带的下载器按 20MB 分块、每块重新打开文件 seek 写，goofys
    上大文件会在第二块开始失败；这里改成一次 open、顺序写，和 ``cp`` 的写模式
    相同。下载失败保留 ``.part``（``--resume`` 可断点续传），正式文件一个都不会
    出现。非视频文件跳过并计数，不污染 OSS。

退出码：0 = 成功；1 = 下载/校验/收尾失败；2 = 参数错误。
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import shlex
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from livepipe.tools.oss_ingest.common import OssIngestError  # noqa: E402
from livepipe.tools.oss_ingest.ingest import (  # noqa: E402
    finalize_part,
    manifest_path,
    verify_media,
)

DEFAULT_ROOT = "~/oss-bucket/web/1/奇瑞汽车/直播看管/直播录屏"
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".ts", ".m4v"}
CHUNK = 8 * 1024 * 1024


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1000.0
    return f"{n:.2f} TB"


def log(msg: str) -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 2) -> None:
    print(f"[oss_download][error] {msg}", file=sys.stderr)
    raise SystemExit(code)


def _component(value: str, label: str) -> str:
    value = (value or "").strip()
    if not value or value in (".", "..") or "/" in value or "\\" in value:
        die(f"{label} 不是合法的单级目录名：{value!r}")
    return value


def _name(value: str) -> str:
    name = (value or "").strip()
    if not name or "/" in name or "\\" in name:
        die(f"--name 不是合法文件名：{value!r}")
    if name.endswith(".part"):
        name = name[:-5]
    if Path(name).suffix.lower() not in VIDEO_EXTS:
        name += ".mp4"
    return name


def _dest_dir(args) -> Path:
    root = Path(os.path.expanduser(args.root))
    return root / _component(args.event, "--event") / _component(
        args.day, "--day") / _component(args.platform, "--platform") / _component(
        args.account, "--account")


def _metadata(args, **extra) -> dict:
    meta = {"event": args.event, "day": args.day,
            "platform": args.platform, "account": args.account}
    meta.update({k: v for k, v in extra.items() if v})
    return meta


def _run(cmd: str, env: dict) -> int:
    log(f"执行：{cmd}")
    return subprocess.run(cmd, shell=True, env=env).returncode


def _finalize(part: Path, final: Path, *, expect_bytes: int | None,
              expect_duration: float | None, allow_no_duration: bool,
              metadata: dict) -> dict:
    log(f"收尾：{part.name} → {final.name}"
        + (f"（期望 {human_bytes(expect_bytes)}）" if expect_bytes else ""))
    manifest = finalize_part(
        part, final, expect_bytes=expect_bytes,
        expect_duration=expect_duration,
        allow_no_duration=allow_no_duration, metadata=metadata)
    mp = manifest_path(manifest["final"])
    log("完成：" + json.dumps({
        "final": manifest["final"], "bytes": manifest["bytes"],
        "duration_s": manifest["duration_s"],
        "manifest": str(mp),
    }, ensure_ascii=False))
    return manifest


def cmd_live(args) -> int:
    t0 = time.monotonic()
    dest = _dest_dir(args)
    final = dest / _name(args.name)
    part = Path(str(final) + ".part")
    env = dict(os.environ, OSS_DIR=str(dest), OSS_FINAL=str(final),
               OSS_PART=str(part))
    cmd = args.cmd.replace("{dir}", shlex.quote(str(dest))).replace(
        "{final}", shlex.quote(str(final))).replace(
        "{part}", shlex.quote(str(part)))

    if final.exists():
        try:
            info = verify_media(final)
        except OssIngestError as e:
            return _err(f"正式文件已存在但校验失败：{final}（{e}）")
        log(f"正式文件已存在且可读：{final}"
            f"（{human_bytes(info['bytes'])}，{info['duration_s']}s）；"
            "不覆盖，直接返回 0")
        return 0
    if part.exists() and args.clean_part:
        log(f"清理旧 .part：{part}")
        part.unlink()
    if part.exists() and not (args.resume or args.clean_part):
        log(f"[warn] .part 已存在（{human_bytes(part.stat().st_size)}）；"
            "按原样交给下载器续传。要重来加 --clean-part。")

    if args.dry_run:
        log(f"目标目录：{dest}")
        log(f"正式文件：{final}")
        log(f"临时文件：{part}")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    rc = _run(cmd, env)
    if rc != 0:
        # 下载器违约写了正式名：失败时绝不留正式文件（只删本次新建的）。
        if final.exists():
            try:
                final.unlink()
                log(f"[warn] 下载失败但发现正式文件，已删除：{final}")
                mp = manifest_path(final)
                if mp.exists():
                    mp.unlink()
            except OSError as e:
                log(f"[error] 下载失败，正式文件删不掉：{final}（{e}）")
        if args.clean_part_on_fail and part.exists():
            try:
                part.unlink()
            except OSError:
                pass
        return _err(f"下载命令退出码 {rc}；未生成正式文件", 1)

    if not part.exists():
        if final.exists():
            try:
                final.unlink()
                log(f"[warn] 下载命令没写 .part 却生成了正式文件，已删除：{final}")
            except OSError:
                pass
        return _err(f"下载命令成功但找不到 .part：{part}（命令必须写到 {{part}}）", 1)
    try:
        _finalize(part, final, expect_bytes=args.expect_bytes,
                  expect_duration=args.expect_duration,
                  allow_no_duration=args.allow_no_duration,
                  metadata=_metadata(args, source="live"))
    except OssIngestError as e:
        return _err(f"收尾失败：{e}（.part 保留，可重跑）", 1)
    log(f"总耗时 {time.monotonic() - t0:.1f}s")
    return 0


def _baidu_client():
    """构造 bypy 客户端（只用于授权 / 列目录 / 取下载流）。"""
    try:
        from bypy import ByPy
    except ImportError as e:  # pragma: no cover - 环境缺依赖
        die(f"baidu 模式需要 bypy 1.8.9（pip install --user bypy==1.8.9）：{e}")
    try:
        bp = ByPy(verbose=0)
        bp._extraupdate()
    except Exception as e:  # bypy 的异常类型不稳定，统一转成大白话
        die(f"bypy 初始化失败（检查 ~/.bypy/bypy.json 授权）：{e}")
    return bp


def _baidu_list(bp, remote: str) -> list[tuple[str, str, int]]:
    """递归列出远端目录里的视频：``[(pcs_path, 相对路径, 字节数)]``。"""
    from bypy import const
    from bypy.bypy import get_pcs_path

    root = get_pcs_path(remote.rstrip("/")).rstrip("/")
    found: list[tuple[str, str, int]] = []

    def collect(_result, _remotepath, _dirjs, filejs, _args):
        for f in filejs:
            p = f.get("path") or ""
            rel = posixpath.relpath(p, root)
            if rel.startswith(".."):
                continue
            if posixpath.splitext(rel)[1].lower() in VIDEO_EXTS:
                found.append((p, rel, int(f.get("size") or 0)))
            else:
                log(f"  [skip] 非视频：{rel}")
        return const.ENoError

    rc = bp._walk_proceed_remote_dir(root, collect, args=None, recursive=True)
    if rc != const.ENoError:
        die(f"bypy 列目录失败（错误码 {rc}）：{remote}", 1)
    return sorted(found, key=lambda x: x[1])


def _baidu_request(bp, pcs_path: str, *, start: int, size: int, timeout: float):
    """向 PCS 要 ``[start, size)`` 的字节流（单请求，不落盘）。"""
    from bypy import const

    headers = {"User-Agent": const.UserAgent,
               "Range": f"bytes={start}-{size - 1}"}
    params = {"method": "download", "path": pcs_path,
              "access_token": bp._access_token}
    for attempt in (1, 2):
        r = bp._requester.request("GET", const.DPcsUrl + "file",
                                  params=params, cookies=bp._cookies,
                                  headers=headers, stream=True, timeout=timeout)
        if r.status_code in (200, 206):
            return r
        # token 过期（110/111）刷新后重试一次；其它错误直接失败
        if attempt == 1 and r.status_code in (401, 403):
            try:
                r.close()
            except Exception:
                pass
            try:
                bp._refresh_token()
            except Exception:
                pass
            params["access_token"] = bp._access_token
            continue
        body = ""
        try:
            body = r.text[:200]
        except Exception:
            pass
        raise OssIngestError(
            f"PCS 下载失败：HTTP {r.status_code} {body}（{pcs_path}）")


def _baidu_stream(bp, pcs_path: str, part: Path, size: int, *,
                  resume: bool, overwrite: bool, timeout: float) -> int:
    """把远端文件顺序流式写进 ``part``；返回最终字节数。"""
    offset = 0
    if part.exists():
        if resume:
            offset = part.stat().st_size
            if offset > size:
                log(f"[warn] .part 比远端大（{offset} > {size}），从头重下：{part}")
                part.unlink()
                offset = 0
        elif overwrite:
            part.unlink()
        else:
            raise OssIngestError(
                f".part 已存在（--resume 续传 / --overwrite 重来）：{part}")
    if size > 0 and offset >= size:
        return part.stat().st_size

    r = _baidu_request(bp, pcs_path, start=offset, size=size, timeout=timeout)
    part.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(part, "r+b" if offset else "wb") as fh:
            if offset:
                fh.seek(offset)
            try:
                for chunk in r.iter_content(chunk_size=CHUNK):
                    if chunk:
                        fh.write(chunk)
            except Exception as e:
                raise OssIngestError(f"下载中断：{e}（{pcs_path}）") from e
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        got = part.stat().st_size
        if got != size:
            raise OssIngestError(f"下载字节不符：{got} != {size}（{part}）")
    except BaseException:
        try:
            r.close()
        except Exception:
            pass
        raise
    return got


def cmd_baidu(args) -> int:
    t0 = time.monotonic()
    root = Path(os.path.expanduser(args.root))
    if args.dest_rel:
        rel_parts = [_component(p, "--dest-rel") for p in
                     args.dest_rel.strip("/").split("/") if p]
        dest_rel = Path(*rel_parts)
    else:
        dest_rel = Path(_component(args.event, "--event"),
                        _component(args.day, "--day"),
                        _component(args.platform, "--platform"),
                        _component(args.account, "--account"))
    remote = args.remote.rstrip("/")

    if args.dry_run:
        log(f"百度：{remote}")
        log(f"OSS 目标：{root / dest_rel}（直写，不落本地暂存）")
        return 0

    bp = _baidu_client()
    files = _baidu_list(bp, remote)
    log(f"远端 {remote}：视频 {len(files)} 个")
    if not files:
        return _err(f"远端目录里没有视频：{remote}", 1)

    ok = 0
    for i, (pcs_path, rel, size) in enumerate(files, 1):
        final = root / dest_rel / Path(rel)
        part = Path(str(final) + ".part")
        log(f"[{i}/{len(files)}] {rel}  {human_bytes(size)}")
        if final.exists():
            try:
                info = verify_media(final)
                good = (info["bytes"] == size
                        and (info["duration_s"] or args.allow_no_duration))
            except OssIngestError:
                good = False
            if good:
                log(f"  [skip] 已存在且可读（{human_bytes(info['bytes'])}）")
                ok += 1
                continue
            if not args.overwrite:
                log(f"[error] 正式文件已存在但不可用（大小/时长不符，"
                    f"加 --overwrite 重下）：{final}")
                continue
        try:
            _baidu_stream(bp, pcs_path, part, size,
                          resume=args.resume, overwrite=args.overwrite,
                          timeout=args.timeout)
            _finalize(part, final, expect_bytes=size, expect_duration=None,
                      allow_no_duration=args.allow_no_duration,
                      metadata=_metadata(
                          args, source="baidu:" + remote,
                          remote_rel=rel, remote_path=pcs_path))
            ok += 1
        except (OssIngestError, OSError) as e:
            log(f"[error] {rel}：{e}（.part 保留，可 --resume）")
            if args.clean_part_on_fail:
                try:
                    part.unlink()
                except OSError:
                    pass
    if ok != len(files):
        return _err(f"成功 {ok}/{len(files)} 个，失败 "
                    f"{len(files) - ok} 个", 1)
    log(f"完成：{ok} 个视频已直写 OSS 并写 manifest → {root / dest_rel}"
        f"（耗时 {time.monotonic() - t0:.1f}s）")
    return 0


def _err(msg: str, code: int = 2) -> int:
    print(f"[oss_download][error] {msg}", file=sys.stderr)
    return code


def make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="P3 下载链路：直播直写 OSS .part 收尾 / 百度 bypy → OSS")
    sub = ap.add_subparsers(dest="mode", required=True)

    def common(p):
        p.add_argument("--root", default=DEFAULT_ROOT,
                       help=f"OSS 录屏根（默认 {DEFAULT_ROOT}）")
        p.add_argument("--event", default=None)
        p.add_argument("--day", default=None)
        p.add_argument("--platform", default=None)
        p.add_argument("--account", default=None)
        p.add_argument("--allow-no-duration", action="store_true",
                       help="探不到时长时仍收尾（默认拒绝）")
        p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("live", help="跑下载命令写 .part，成功后收尾")
    common(p)
    p.add_argument("--name", required=True,
                   help="文件名（无扩展名时补 .mp4；不要带路径）")
    p.add_argument("--cmd", required=True,
                   help="下载命令；{part}/{final}/{dir} 会替换成转义后的路径")
    p.add_argument("--expect-bytes", type=int, default=None)
    p.add_argument("--expect-duration", type=float, default=None)
    p.add_argument("--clean-part", action="store_true",
                   help="开始前删除已有 .part")
    p.add_argument("--resume", action="store_true",
                   help="允许保留已有 .part 交给下载器续传（默认只警告）")
    p.add_argument("--clean-part-on-fail", action="store_true",
                   help="下载失败后删除 .part（默认保留以便续传）")
    p.set_defaults(func=cmd_live)

    p = sub.add_parser("baidu", help="bypy 列目录 + PCS 流式直写 OSS")
    common(p)
    p.add_argument("--remote", required=True,
                   help="百度网盘目录，如 /直播录屏/奇瑞探秘局")
    p.add_argument("--dest-rel", default=None,
                   help="OSS 根下的相对目录；不写则用 event/day/platform/account")
    p.add_argument("--resume", action="store_true",
                   help=".part 已存在时从它的字节数续传")
    p.add_argument("--overwrite", action="store_true",
                   help="正式文件校验失败时重下；.part 存在时从头重写")
    p.add_argument("--timeout", type=float, default=300.0,
                   help="HTTP 读超时（秒，默认 300）")
    p.add_argument("--clean-part-on-fail", action="store_true",
                   help="下载失败后删除 .part（默认保留以便 --resume）")
    p.set_defaults(func=cmd_baidu)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if args.mode == "live":
        for key in ("event", "day", "platform", "account"):
            _component(getattr(args, key), "--" + key)
    if args.mode == "baidu" and not args.dest_rel:
        for key in ("event", "day", "platform", "account"):
            _component(getattr(args, key), "--" + key)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
