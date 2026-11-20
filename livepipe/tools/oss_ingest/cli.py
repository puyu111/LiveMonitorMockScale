"""``oss_ingest`` 命令行入口（**只用标准库**）。

子命令：

    manifest <config...> [--oss-root PATH] [--disk-root PATH]
                         [--source-map JSON] [--out PATH] [--out-dir DIR]
    audit    <config...> [--oss-root PATH] [--disk-root PATH]
                         [--source-map JSON] [--report-only] [--out PATH]
    sync <src> <dst> [--sha256|--no-sha256] [--delete-local] [--dry-run]
                     [--workers N]
    verify <src> <dst> [--sha256|--no-sha256]
    verify-media <path> [--allow-unknown-duration]
    ingest <part> [--src PATH] [--final PATH] [--force]
                  [--expect-bytes N] [--expect-duration S]
                  [--allow-no-duration]
                  [--event E] [--day D] [--platform P] [--account A] [--trim R]
    check-env [--oss-root PATH] [--network] [--role all|download|compute|review]
    --selftest

未传 ``--oss-root`` 时按 :data:`common.OSS_ROOT_CANDIDATES` 的顺序自动选择
第一个「存在且至少含一个视频文件」的根，并打印实际使用的根。

约定：正常结束返回 0；可预期错误打印 ``[oss_ingest][error] ...`` 到 stderr 并
返回 1；argparse 的用法错误返回 2。``manifest`` / ``sync`` 两个实现模块按需
延迟导入，缺模块时给出一条可行动的报错，而不是在 ``--help`` 阶段就崩。
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from . import doc
from .common import (
    MANIFEST_VERSION,
    OssIngestError,
    resolve_oss_root,
    write_json_atomic,
)
from .ingest import finalize_part, manifest_path, verify_media


# --------------------------------------------------------------------------- #
# 延迟导入：manifest.py / sync.py 由其他模块提供
# --------------------------------------------------------------------------- #
def _manifest_module():
    """返回 :mod:`.manifest`；单独成函数便于自检 monkeypatch。"""
    from . import manifest
    return manifest


def _sync_module():
    """返回 :mod:`.sync`；单独成函数便于自检 monkeypatch。"""
    from . import sync
    return sync


# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #
def make_parser(prog: str | None = None) -> argparse.ArgumentParser:
    """构造 ``oss_ingest`` 的参数解析器。

    ``prog`` 省略时用模块入口的调用形式；从 ``python -m livepipe oss`` 进来时
    由 :func:`main` 传入 ``python -m livepipe oss``，保证 usage 与用户实际
    输入的命令一致。
    """
    parser = argparse.ArgumentParser(
        prog=prog or "python -m livepipe.tools.oss_ingest",
        description="OSS 入库工具：配置→OSS 映射清单、只读审计、按内容同步、"
                    ".part 收尾、环境体检。")
    parser.add_argument("--selftest", action="store_true",
                        help="跑离线自检（临时目录、无网络、无 FUSE）")
    sub = parser.add_subparsers(dest="command", metavar="<子命令>")

    p = sub.add_parser("manifest", help="为配置生成「磁盘素材 → OSS」映射清单")
    p.add_argument("config", nargs="+", metavar="CONFIG", help="JSON/JSONC 配置文件")
    p.add_argument("--oss-root", default=None,
                   help="OSS 根目录（默认按候选顺序自动选择真实录屏根，见 README）")
    p.add_argument("--disk-root", default=None,
                   help="素材盘根目录（只影响清单里 disk 字段的展示）")
    p.add_argument("--source-map", default=None, metavar="JSON",
                   help="可选：裁尾段「磁盘路径/文件名 → OSS 源对象」映射 JSON")
    p.add_argument("--out", default=None, help="把清单 JSON 写到这个路径")
    p.add_argument("--out-dir", default=None, metavar="DIR",
                   help="每份配置各写 DIR/<配置文件名去扩展名>.json")

    p = sub.add_parser("audit", help="只读审计多份配置的引用与映射覆盖")
    p.add_argument("config", nargs="+", metavar="CONFIG", help="JSON/JSONC 配置文件")
    p.add_argument("--oss-root", default=None,
                   help="OSS 根目录（默认按候选顺序自动选择真实录屏根，见 README）")
    p.add_argument("--disk-root", default=None,
                   help="素材盘根目录（只影响清单里 disk 字段的展示）")
    p.add_argument("--source-map", default=None, metavar="JSON",
                   help="可选：裁尾段「磁盘路径/文件名 → OSS 源对象」映射 JSON")
    p.add_argument("--report-only", "--allow-missing", dest="report_only",
                   action="store_true",
                   help="只报告：即使有缺失 / 坏配置也返回 0（默认有缺失即返回 1）")
    p.add_argument("--out", default=None, help="把审计 JSON 写到这个路径")

    p = sub.add_parser("sync", help="把目录树按大小/哈希同步到目标")
    p.add_argument("src", help="源目录（支持 ~）")
    p.add_argument("dst", help="目标目录（支持 ~；不能与源相互嵌套）")
    sha = p.add_mutually_exclusive_group()
    sha.add_argument("--sha256", dest="sha256", action="store_true",
                     default=False,
                     help="大小相同时再比 SHA256（默认关闭；常规同步只比大小）")
    sha.add_argument("--no-sha256", dest="sha256", action="store_false",
                     help="（默认）只比大小，不比 SHA256（更快）")
    p.add_argument("--delete-local", action="store_true",
                   help="目标校验通过后删除源文件（会强制 SHA256，并清理 src 下空目录）")
    p.add_argument("--dry-run", action="store_true", help="只统计，不写盘、不删源")
    p.add_argument("--workers", type=int, default=1,
                   help="并发复制线程数（默认 1，测试与确定性优先）")

    p = sub.add_parser("verify", help="只读校验 src 是否已完整同步到 dst")
    p.add_argument("src", help="源目录（支持 ~）")
    p.add_argument("dst", help="目标目录（支持 ~）")
    vsha = p.add_mutually_exclusive_group()
    vsha.add_argument("--sha256", dest="sha256", action="store_true",
                      default=False,
                      help="同时比 SHA256（默认关闭）")
    vsha.add_argument("--no-sha256", dest="sha256", action="store_false",
                      help="（默认）只比大小")

    p = sub.add_parser("verify-media", help="只读校验一个成品媒体的字节数与时长")
    p.add_argument("path", help="成品媒体文件")
    p.add_argument("--allow-unknown-duration", action="store_true",
                   help="探不到时长时按字节数放行（默认判为 UNKNOWN 并返回 1）")

    p = sub.add_parser("ingest", help="把 .part 收尾成正式文件并写 manifest")
    p.add_argument("part", nargs="?", default=None,
                   help="待收尾的 .part 文件（也可用 --src）")
    p.add_argument("--src", default=None,
                   help="--src 别名（兼容迁移文档里的 ingest.py --src 写法）")
    p.add_argument("--final", default=None, help="目标路径（默认去掉 .part 后缀）")
    p.add_argument("--force", action="store_true",
                   help="目标已存在且大小不同时仍覆盖")
    p.add_argument("--expect-bytes", type=int, default=None,
                   help="期望字节数；不符则拒绝并保留 .part")
    p.add_argument("--expect-duration", type=float, default=None,
                   help="期望时长（秒，容差 0.5s）；不符则拒绝并保留 .part")
    p.add_argument("--allow-no-duration", action="store_true",
                   help="探查不到有效时长时仍允许收尾（默认拒绝；manifest "
                        "duration_s 记为 null）")
    p.add_argument("--event", default=None, help="写入 manifest 的事件名")
    p.add_argument("--day", default=None, help="写入 manifest 的日期")
    p.add_argument("--platform", default=None, help="写入 manifest 的平台")
    p.add_argument("--account", default=None, help="写入 manifest 的账号")
    p.add_argument("--trim", default=None,
                   help="写入 manifest 的裁尾区间，如 0-2854")

    p = sub.add_parser("check-env", help="体检本机 OSS 入库环境")
    p.add_argument("--oss-root", default=None,
                   help="OSS 根目录（默认按候选顺序自动选择真实录屏根，见 README）")
    p.add_argument("--network", action="store_true",
                   help="额外跑 bypy info（只打印 Quota/Used 行）")
    p.add_argument("--role", choices=("all", "download", "compute", "review"),
                   default="all",
                   help="机器角色：compute/review 只需 goofys，bypy 缺失降级为 WARN")
    return parser


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _error(message: str) -> int:
    print(f"[oss_ingest][error] {message}", file=sys.stderr)
    return 1


_ROOT_SOURCE_LABELS = {
    "explicit": "--oss-root 显式指定",
    "auto": "自动选择：第一个含视频文件的候选根",
    "unresolved": "未找到候选根，回退首个候选路径做诊断",
}


def _resolve_root(args: argparse.Namespace, *, strict: bool = True) -> Path:
    """解析实际使用的 OSS 根；未显式指定时按候选顺序自动选择。

    解析结果会打印出来（含来源），避免用户误以为仍在使用旧的空根。
    """
    root, source = resolve_oss_root(getattr(args, "oss_root", None),
                                    strict=strict)
    print(f"OSS 根：{root}（{_ROOT_SOURCE_LABELS.get(source, source)}）")
    return root


def _load_source_map(path: str | None) -> dict | None:
    """读可选的 ``--source-map`` JSON（``{磁盘路径/文件名: OSS 源对象}``）。"""
    if not path:
        return None
    from .common import read_json
    try:
        data = read_json(path)
    except OssIngestError as e:
        raise OssIngestError(f"--source-map 不可用：{e}") from e
    return {str(k): str(v) for k, v in data.items() if v}


def _parse_trim(text: str | None):
    """``0-2854`` → ``[0.0, 2854.0]``；解析不了就原样保存。"""
    if text is None:
        return None
    raw = text.strip()
    for sep in ("-", "~", ","):
        if sep in raw:
            left, _, right = raw.partition(sep)
            try:
                return [float(left), float(right)]
            except ValueError:
                break
    return raw


#: bootstrap 钉住的工具版本；机器上版本不同时 check-env 只给 WARN。
_GOOFYS_VERSION = "0.24.0"
_BYPY_VERSION = "1.8.9"


def _exe_version(exe: str, flag: str = "--version") -> str | None:
    """跑 ``<exe> --version`` 取第一行；失败 / 无输出返回 ``None``（不判失败）。"""
    try:
        proc = subprocess.run([exe, flag], capture_output=True, text=True,
                              timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    text = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    return text.splitlines()[0].strip() if text else None


def _version_status(name: str, exe: str, expected: str) -> dict:
    """可执行文件存在时的版本行：不同版本给 WARN（不影响通过）。"""
    version = _exe_version(exe)
    if version and expected not in version:
        return {"check": name, "status": "WARN",
                "detail": f"{exe}（版本 {version}，bootstrap 钉的是 {expected}）"}
    detail = f"{exe}（{version}）" if version else exe
    return {"check": name, "status": "PASS", "detail": detail}


def _is_executable(path: str | Path) -> str | None:
    """可执行文件存在且可执行 → 返回其字符串路径，否则 ``None``。"""
    p = Path(str(path)).expanduser()
    try:
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    except OSError:
        return None
    return None


def _find_exe(name: str, *fallbacks: str | Path) -> str | None:
    """先查 ``PATH``，再依次查显式候选路径。"""
    found = shutil.which(name)
    if found:
        return found
    for candidate in fallbacks:
        ok = _is_executable(candidate)
        if ok:
            return ok
    return None


def _libfuse3() -> str | None:
    """找一个 libfuse3 共享库（找不到返回 ``None``）。"""
    patterns = (
        "/usr/lib/*/libfuse3.so*",
        "/usr/lib64/libfuse3.so*",
        "/lib/*/libfuse3.so*",
        "/lib64/libfuse3.so*",
    )
    for pattern in patterns:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
    return None


def _find_mount(path: str | Path) -> str | None:
    """从 ``path`` 逐级向上找挂载点；排除文件系统根 ``/``（它永远算挂载）。"""
    p = Path(os.path.abspath(str(path)))
    root = os.path.sep
    while True:
        try:
            if str(p) != root and os.path.ismount(str(p)):
                return str(p)
        except OSError:
            pass
        parent = p.parent
        if parent == p:
            return None
        p = parent


def _unescape_mount(text: str) -> str:
    """还原 /proc mountinfo 里的八进制转义（\\040 空格等）。"""
    return (text.replace("\\040", " ").replace("\\011", "\t")
            .replace("\\012", "\n").replace("\\134", "\\"))


def _mountinfo_lookup(path: str | Path) -> dict | None:
    """从 ``/proc/self/mountinfo`` 找 ``path`` 所属挂载点（最长前缀）。"""
    target = os.path.abspath(str(path))
    best: dict | None = None
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                try:
                    sep = parts.index("-")
                except ValueError:
                    continue
                if len(parts) <= max(sep + 2, 4):
                    continue
                point = _unescape_mount(parts[4])
                if (target == point
                        or target.startswith(point.rstrip("/") + "/")):
                    if best is None or len(point) > len(best["target"]):
                        best = {"target": point,
                                "fstype": parts[sep + 1],
                                "source": parts[sep + 2]}
    except OSError:
        return None
    return best


def _mount_info(path: str | Path) -> dict | None:
    """返回 ``{target, fstype, source}``；优先 ``findmnt``，回退 mountinfo。"""
    findmnt = shutil.which("findmnt")
    if findmnt:
        try:
            proc = subprocess.run(
                [findmnt, "-T", str(path), "-n", "-o", "TARGET,FSTYPE,SOURCE"],
                capture_output=True, text=True, timeout=5)
            if proc.returncode == 0 and proc.stdout.strip():
                parts = proc.stdout.strip().splitlines()[0].split(None, 2)
                if len(parts) >= 2:
                    return {"target": parts[0], "fstype": parts[1],
                            "source": parts[2] if len(parts) > 2 else ""}
        except (OSError, subprocess.SubprocessError):
            pass
    return _mountinfo_lookup(path)


def _fuse_conf_row() -> dict:
    """``/etc/fuse.conf`` 是否启用 ``user_allow_other``（只读，缺失只 WARN）。"""
    path = Path("/etc/fuse.conf")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"check": "/etc/fuse.conf", "status": "WARN",
                "detail": "读不到；goofys -o allow_other 需要 user_allow_other"}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            if stripped.split()[0] == "user_allow_other":
                return {"check": "/etc/fuse.conf", "status": "PASS",
                        "detail": "已启用 user_allow_other"}
    return {"check": "/etc/fuse.conf", "status": "WARN",
            "detail": "未启用 user_allow_other（goofys -o allow_other 需要）"}


def _credential_row(name: str, path: str | Path) -> dict:
    """凭据文件只查「存在 + 普通文件 + 0600」，**绝不读取内容**。"""
    p = Path(str(path)).expanduser()
    try:
        st = p.stat()
    except FileNotFoundError:
        return {"check": name, "status": "FAIL", "detail": f"不存在：{p}"}
    except OSError as e:
        return {"check": name, "status": "FAIL", "detail": f"无法 stat：{p}（{e}）"}
    if not stat.S_ISREG(st.st_mode):
        return {"check": name, "status": "FAIL", "detail": f"不是普通文件：{p}"}
    mode = stat.S_IMODE(st.st_mode)
    if mode != 0o600:
        return {"check": name, "status": "FAIL",
                "detail": f"权限 {mode:04o}，应为 0600：{p}"}
    return {"check": name, "status": "PASS", "detail": f"{p}（0600）"}


def _network_row(bypy: str | None) -> dict:
    """``bypy info``：只摘出含 Quota / Used 的行，绝不回显整段输出。"""
    if not bypy:
        return {"check": "bypy info", "status": "FAIL",
                "detail": "bypy 不可用，跳过联网检查"}
    try:
        proc = subprocess.run([bypy, "info"], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return {"check": "bypy info", "status": "FAIL",
                "detail": f"调用失败：{e}"}
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    picked = [line.strip() for line in text.splitlines()
              if "quota" in line.lower() or "used" in line.lower()]
    if proc.returncode != 0:
        detail = f"退出码 {proc.returncode}"
        if picked:
            detail += "；" + "；".join(picked)
        return {"check": "bypy info", "status": "FAIL", "detail": detail}
    return {"check": "bypy info", "status": "PASS",
            "detail": "；".join(picked) if picked else "无 Quota/Used 输出"}


# --------------------------------------------------------------------------- #
# check-env
# --------------------------------------------------------------------------- #
def check_env(root: Path, *, network: bool = False,
              role: str = "all") -> tuple[list[dict], bool]:
    """体检本机环境，返回 ``(行列表, 是否全部通过)``。

    每行是 ``{"check", "status", "detail"}``；``status`` ∈ PASS/WARN/FAIL。
    WARN 不影响“通过”，任何 FAIL 都让第二个返回值为 ``False``。

    ``role`` 为 ``compute`` / ``review`` 时只要求 goofys：bypy 可执行与凭据
    缺失降级为 WARN（这些机器只读 OSS，不需要百度下载）。
    """
    root = Path(root)
    home = Path.home()
    rows: list[dict] = []

    # 1. Python
    version = ".".join(str(n) for n in sys.version_info[:3])
    if sys.version_info >= (3, 8):
        rows.append({"check": "python3 ≥ 3.8", "status": "PASS",
                     "detail": f"Python {version}"})
    else:
        rows.append({"check": "python3 ≥ 3.8", "status": "FAIL",
                     "detail": f"Python {version} 太旧"})

    # 2. bypy（PATH / ~/anaconda3/bin / 用户级 pip 的 ~/.local/bin）
    bypy = _find_exe("bypy", home / "anaconda3" / "bin" / "bypy",
                     home / ".local" / "bin" / "bypy")
    if bypy:
        rows.append(_version_status("bypy 可执行", bypy, _BYPY_VERSION))
    else:
        rows.append({"check": "bypy 可执行", "status": "FAIL",
                     "detail": "PATH、~/anaconda3/bin/bypy、~/.local/bin/bypy "
                               "都没找到"})

    # 3. bypy 凭据（只看权限位）
    rows.append(_credential_row("~/.bypy/bypy.json", home / ".bypy" / "bypy.json"))

    # 4. goofys
    goofys = _find_exe("goofys", "/usr/local/bin/goofys", home / "goofys")
    if goofys:
        rows.append(_version_status("goofys 可执行", goofys, _GOOFYS_VERSION))
    else:
        rows.append({"check": "goofys 可执行", "status": "FAIL",
                     "detail": "PATH、/usr/local/bin/goofys、~/goofys 都没找到"})

    # 5. /dev/fuse
    if os.path.exists("/dev/fuse"):
        rows.append({"check": "/dev/fuse", "status": "PASS", "detail": "/dev/fuse"})
    else:
        rows.append({"check": "/dev/fuse", "status": "FAIL",
                     "detail": "缺少 /dev/fuse（可能需要 modprobe fuse）"})

    # 6. fuse3（缺失只 WARN：goofys 可能静态链接，但仍建议装）
    fusermount = _find_exe("fusermount3")
    libfuse = _libfuse3()
    if fusermount:
        rows.append({"check": "fuse3", "status": "PASS",
                     "detail": f"fusermount3：{fusermount}"})
    elif libfuse:
        rows.append({"check": "fuse3", "status": "PASS",
                     "detail": f"libfuse3：{libfuse}"})
    else:
        rows.append({"check": "fuse3", "status": "WARN",
                     "detail": "未发现 fusermount3 / libfuse3"})

    # 6b. /etc/fuse.conf user_allow_other（goofys -o allow_other 的前提）
    rows.append(_fuse_conf_row())

    # 7. OSS 挂载：必须是 fuse/goofys 祖先挂载，且 OSS 根真实存在。
    #    只看到「某个祖先挂载点」不够：$HOME 单独分区时 goofys 没挂载也会
    #    命中 /home，导致 sync 往本地目录里写。
    mount = _find_mount(root)
    if mount is None:
        rows.append({"check": "OSS 挂载", "status": "FAIL",
                     "detail": f"未发现挂载祖先：{root}"})
    else:
        info = _mount_info(root) or _mount_info(mount) or {}
        fstype = str(info.get("fstype") or "").lower()
        source = str(info.get("source") or "").lower()
        is_fuse = fstype.startswith("fuse") or "goofys" in source
        if not root.is_dir():
            rows.append({"check": "OSS 挂载", "status": "FAIL",
                         "detail": (f"OSS 根不存在：{root}"
                                    f"（挂载祖先 {mount}，"
                                    f"fstype={fstype or '?'}）")})
        elif is_fuse:
            rows.append({"check": "OSS 挂载", "status": "PASS",
                         "detail": (f"挂载祖先：{mount}"
                                    f"（fstype={fstype or '?'}，"
                                    f"source={source or '?'}）")})
        else:
            rows.append({"check": "OSS 挂载", "status": "FAIL",
                         "detail": (f"挂载祖先 {mount} 不是 fuse/goofys"
                                    f"（fstype={fstype or '?'}，"
                                    f"source={source or '?'}）；"
                                    "goofys 可能未挂载")})

    # 8. ffprobe（可选：缺了只影响时长探测）
    ffprobe = _find_exe("ffprobe")
    if ffprobe:
        rows.append({"check": "ffprobe（可选）", "status": "PASS",
                     "detail": ffprobe})
    else:
        rows.append({"check": "ffprobe（可选）", "status": "WARN",
                     "detail": "未安装：时长探测会返回 null"})

    # 9. goofys 的 AWS 凭据（只看权限位）
    rows.append(_credential_row("~/.aws/credentials",
                                home / ".aws" / "credentials"))

    # 10. 可选联网检查
    if network:
        rows.append(_network_row(bypy))

    # 11. 角色降级：compute/review 节点不需要 bypy。
    if role in ("compute", "review"):
        for row in rows:
            if (row["check"] in ("bypy 可执行", "~/.bypy/bypy.json",
                                 "bypy info")
                    and row["status"] == "FAIL"):
                row["status"] = "WARN"
                row["detail"] += f"（role={role} 只需要 goofys，降级为 WARN）"

    ok = not any(r["status"] == "FAIL" for r in rows)
    return rows, ok


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def _cmd_manifest(args: argparse.Namespace) -> int:
    try:
        manifest_mod = _manifest_module()
    except ImportError as e:
        return _error(f"manifest 模块不可用（{e}）")
    try:
        root = _resolve_root(args)
    except OssIngestError as e:
        return _error(str(e))
    try:
        source_map = _load_source_map(getattr(args, "source_map", None))
    except OssIngestError as e:
        return _error(str(e))
    disk_root = getattr(args, "disk_root", None)

    # 批量构建：_build_oss_index 只跑一次（禁止每份配置重扫 221GB 树）。
    batch = getattr(manifest_mod, "build_config_manifests", None)
    if batch is not None:
        manifests, errors = batch(args.config, root, disk_root=disk_root,
                                  source_map=source_map)
        rc = 0
        for err in errors:
            print(f"[oss_ingest][error] {err['config']}：{err['error']}",
                  file=sys.stderr)
            rc = 1
        results = manifests
    else:                                   # 兼容旧版 / 测试替身
        results, rc = [], 0
        for config in args.config:
            try:
                results.append(manifest_mod.build_config_manifest(config, root))
            except Exception as e:
                print(f"[oss_ingest][error] {config}：{e}", file=sys.stderr)
                rc = 1

    for manifest in results:
        print(doc.manifest_summary(manifest))
        print(doc.manifest_table(manifest))
        if len(args.config) > 1:
            print()
    if args.out and results:
        payload = (results[0] if len(results) == 1
                   else {"version": MANIFEST_VERSION, "manifests": results})
        try:
            write_json_atomic(args.out, payload)
        except OssIngestError as e:
            return _error(str(e))
        print(f"清单 → {args.out}")
    if args.out_dir and results:
        rc = _write_manifests_to_dir(results, args.config, args.out_dir) or rc
    return rc


def _write_manifests_to_dir(results: list[dict], configs: list[str],
                            out_dir: str) -> int:
    """``manifest --out-dir``：每份配置写 ``DIR/<配置文件名去扩展名>.json``。

    先检查文件名冲突再写，避免写到一半才发现互相覆盖；任一写入失败返回 1。
    """
    targets: list[tuple[Path, dict]] = []
    seen: dict[str, str] = {}
    for i, manifest in enumerate(results):
        config = manifest.get("config")
        if not config:
            config = configs[i] if i < len(configs) else f"manifest{i}"
        name = Path(str(config)).stem + ".json"
        if name in seen:
            return _error(
                f"--out-dir 文件名冲突：{name} 同时来自 {seen[name]} 与 "
                f"{config}；请分目录输出或先改配置文件名")
        seen[name] = str(config)
        targets.append((Path(out_dir).expanduser() / name, manifest))

    try:
        Path(out_dir).expanduser().mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return _error(f"无法创建 --out-dir：{out_dir}（{e}）")

    rc = 0
    for path, manifest in targets:
        try:
            write_json_atomic(path, manifest)
        except OssIngestError as e:
            print(f"[oss_ingest][error] 清单写入失败：{path}（{e}）",
                  file=sys.stderr)
            rc = 1
            continue
        print(f"清单 → {path}")
    return rc


def _cmd_audit(args: argparse.Namespace) -> int:
    try:
        manifest_mod = _manifest_module()
    except ImportError as e:
        return _error(f"manifest 模块不可用（{e}）")
    try:
        root = _resolve_root(args)
    except OssIngestError as e:
        return _error(str(e))
    try:
        source_map = _load_source_map(getattr(args, "source_map", None))
    except OssIngestError as e:
        return _error(str(e))
    try:
        audit = manifest_mod.audit_configs(
            args.config, root, disk_root=getattr(args, "disk_root", None),
            source_map=source_map)
    except Exception as e:
        return _error(f"审计失败：{e}")
    print(doc.audit_summary(audit))
    if audit.get("details"):
        print(doc.audit_table(audit))
    missing_lines = doc.audit_missing_lines(audit)
    if missing_lines:
        print("未映射的全片 / 本地缺失（需要补传或确认素材盘）：")
        print("\n".join(missing_lines))
    if args.out:
        try:
            write_json_atomic(args.out, audit)
        except OssIngestError as e:
            return _error(str(e))
        print(f"审计 → {args.out}")

    # P0 / CI 门禁：坏配置或缺失 > 0 都是失败；--report-only 强制 0。
    failed = int(audit.get("failed_configs", 0))
    missing = int(audit.get("missing", 0))
    local_missing = int(audit.get("local_missing", 0))
    bad = (failed > 0) or any(not d.get("ok") for d in audit.get("details") or [])
    if args.report_only:
        print(f"结论：仅报告（缺失 {missing}，失败配置 {failed}，"
              f"本地缺失 {local_missing}）")
        return 0
    if bad or missing > 0:
        print(f"结论：FAIL（缺失 {missing}，失败配置 {failed}，"
              f"本地缺失 {local_missing}）")
        return 1
    trimmed = int(audit.get("trimmed", 0))
    if trimmed:
        print(f"结论：PASS（清单完成：缺失 0，失败配置 0，本地缺失 "
              f"{local_missing}；{trimmed} 段裁尾副本不在本次检查内，"
              f"P1 落盘请用 build_oss_inventory.py --verify-uploads）")
    else:
        print(f"结论：PASS（缺失 0，失败配置 0，本地缺失 {local_missing}）")
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    try:
        sync_mod = _sync_module()
    except ImportError as e:
        return _error(f"sync 模块不可用（{e}）")
    kwargs = dict(sha256=args.sha256, delete_local=args.delete_local,
                  dry_run=args.dry_run, workers=args.workers)
    try:
        result = sync_mod.sync_tree(args.src, args.dst, **kwargs)
    except OssIngestError as e:
        return _error(str(e))
    if args.dry_run:
        print("[dry-run] 不写盘、不删源；下面只统计“会复制 / 会跳过 / 会删源”")
    print(doc.sync_summary(result))
    for failure in result.get("failures") or []:
        print(f"  ✗ {failure.get('src')} → {failure.get('dst')}："
              f"{failure.get('error')}", file=sys.stderr)
        if failure.get("marker"):
            print(f"    失败标记：{failure['marker']}", file=sys.stderr)
    if args.dry_run:
        print(f"[dry-run] 结论：需复制 {result.get('copied', 0)} | "
              f"已一致 {result.get('skipped', 0)} | "
              f"会删源 {result.get('deleted', 0)}")
    return 0 if not result.get("failed") and not result.get("walk_errors") else 1


def _cmd_verify(args: argparse.Namespace) -> int:
    try:
        sync_mod = _sync_module()
    except ImportError as e:
        return _error(f"sync 模块不可用（{e}）")
    kwargs = {"sha256": args.sha256}
    try:
        result = sync_mod.verify_tree(args.src, args.dst, **kwargs)
    except OssIngestError as e:
        return _error(str(e))
    print(doc.verify_summary(result))
    bad = (result.get("missing", 0) or result.get("size_mismatch", 0)
           or result.get("sha256_mismatch", 0) or result.get("extra", 0)
           or result.get("walk_errors"))
    if bad:
        print("结论：FAIL（缺失 / 大小 / 哈希 / 多余对象 / 遍历错误不为 0）")
        return 1
    print(f"结论：PASS（{result.get('ok', 0)} 个文件大小"
          f"{' + sha256' if kwargs.get('sha256') else ''}一致）")
    return 0


def _cmd_verify_media(args: argparse.Namespace) -> int:
    try:
        info = verify_media(args.path)
    except OssIngestError as e:
        return _error(str(e))
    dur = info.get("duration_s")
    print(f"文件：{args.path}")
    print(f"字节：{info['bytes']} B")
    print(f"时长：{'-' if dur is None else f'{float(dur):.3f}s'}")
    if dur is None or float(dur) <= 0:
        if getattr(args, "allow_unknown_duration", False):
            print("结论：UNKNOWN（未探到有效时长；字节数已校验，"
                  "--allow-unknown-duration 放行）")
            return 0
        print("结论：UNKNOWN（未探到有效时长：ffprobe 可能不可用或文件不是视频；"
              "确需只校验字节请加 --allow-unknown-duration）")
        return 1
    print("结论：PASS")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    part = args.part or getattr(args, "src", None)
    if not part:
        print("[oss_ingest][error] 需要 .part 路径（位置参数或 --src）",
              file=sys.stderr)
        return 2
    metadata = {}
    for key in ("event", "day", "platform", "account"):
        value = getattr(args, key, None)
        if value is not None:
            metadata[key] = value
    trim = _parse_trim(getattr(args, "trim", None))
    if trim is not None:
        metadata["trim"] = trim
    try:
        manifest = finalize_part(
            part, args.final, force=args.force,
            expect_bytes=args.expect_bytes,
            expect_duration=args.expect_duration,
            allow_no_duration=args.allow_no_duration,
            metadata=metadata or None)
    except OssIngestError as e:
        return _error(str(e))
    print(doc.ingest_summary(manifest, manifest_path(manifest["final"])))
    return 0


def _cmd_check_env(args: argparse.Namespace) -> int:
    try:
        # check-env 通常用于「还没挂载好」的机器：定位不到候选根时仍用首个
        # 候选路径继续诊断，让 OSS 挂载一项明确显示 FAIL，而不是整条命令报错。
        root = _resolve_root(args, strict=False)
    except OssIngestError as e:
        return _error(str(e))
    rows, ok = check_env(root, network=args.network,
                         role=getattr(args, "role", "all"))
    print(doc.check_env_table(rows))
    failed = sum(1 for r in rows if r["status"] == "FAIL")
    warned = sum(1 for r in rows if r["status"] == "WARN")
    if ok:
        print(f"结论：通过（WARN {warned} 项）" if warned else "结论：通过")
    else:
        print(f"结论：{failed} 项失败（WARN {warned} 项）")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
_HANDLERS = {
    "manifest": _cmd_manifest,
    "audit": _cmd_audit,
    "sync": _cmd_sync,
    "verify": _cmd_verify,
    "verify-media": _cmd_verify_media,
    "ingest": _cmd_ingest,
    "check-env": _cmd_check_env,
}


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    """``oss_ingest`` 入口，返回进程退出码。

    ``prog`` 用于让 ``--help`` 显示用户实际输入的命令（例如
    ``python -m livepipe oss``）；省略时用模块入口形式。
    """
    parser = make_parser(prog)
    try:
        args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as e:          # argparse 的 --help / 用法错误
        code = e.code
        return 0 if code is None else int(code)
    if args.selftest:
        from .selftest import selftest
        return selftest()
    if not args.command:
        parser.print_help()
        return 2
    handler = _HANDLERS.get(args.command)
    if handler is None:
        return 2
    return handler(args)
