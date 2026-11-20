#!/usr/bin/env bash
# OSS 入库环境 bootstrap（goofys + bypy + fuse3）。
#
#   bash scripts/bootstrap_oss_env.sh            # 安装 / 修复
#   bash scripts/bootstrap_oss_env.sh --check    # 只读体检，打印 PASS/FAIL
#
# 安全约定：
#   - --check 不 sudo、不写盘、不联网。
#   - 凭据只从 /secure/bypy.json、/secure/aws_credentials 复制，权限 600；
#     脚本**从不 cat / echo 文件内容**。
#   - 脚本不后台挂载；最后只打印挂载命令，由人确认后执行。
set -euo pipefail

GOOFYS_VERSION="0.24.0"
BYPY_VERSION="1.8.9"
OSS_ROOT_PARENT="${OSS_ROOT_PARENT:-$HOME/oss-bucket}"

GOOFYS_URL="https://github.com/kahing/goofys/releases/download/v${GOOFYS_VERSION}/goofys"

FAILS=0
pass() { printf '  [PASS] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; FAILS=$((FAILS + 1)); }

usage() {
    cat <<'EOF'
用法：
  bash scripts/bootstrap_oss_env.sh            安装 / 修复（apt + goofys + bypy + 凭据）
  bash scripts/bootstrap_oss_env.sh --check    只读体检（PASS/FAIL/WARN，无 sudo、无写盘）
  bash scripts/bootstrap_oss_env.sh --systemd  只打印 systemd unit（可重定向保存）
环境变量：
  OSS_ROOT_PARENT   挂载父目录，默认 $HOME/oss-bucket
EOF
}

find_exe() {
    # 先查 PATH，再依次查显式候选；找到就打印路径。
    local name="$1"; shift
    local candidate
    if candidate="$(command -v "$name" 2>/dev/null)" && [[ -n "$candidate" ]]; then
        printf '%s\n' "$candidate"
        return 0
    fi
    for candidate in "$@"; do
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

check_cred() {
    # 只查存在与权限位，绝不读取内容。
    local label="$1" path="$2" mode
    if [[ ! -e "$path" ]]; then
        bad "$label 不存在：$path"
        return
    fi
    mode="$(stat -c '%a' "$path" 2>/dev/null || true)"
    if [[ "$mode" == "600" ]]; then
        pass "$label 存在且 0600：$path"
    else
        bad "$label 权限 ${mode:-未知}，应为 600：$path"
    fi
}

is_mount() {
    local path="$1"
    if command -v mountpoint >/dev/null 2>&1; then
        mountpoint -q "$path" 2>/dev/null
        return
    fi
    [[ "$(stat -c '%d' "$path" 2>/dev/null)" != \
       "$(stat -c '%d' "$(dirname "$path")" 2>/dev/null)" ]]
}

# --------------------------------------------------------------------------- #
# --check：只读体检
# --------------------------------------------------------------------------- #
run_check() {
    echo "== OSS 入库环境体检（只读） =="

    if command -v python3 >/dev/null 2>&1; then
        pass "python3：$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
    else
        bad "python3 不存在"
    fi

    if python3 -m pip --version >/dev/null 2>&1; then
        pass "pip 可用"
    else
        bad "python3 -m pip 不可用（apt install python3-pip）"
    fi

    if command -v fusermount3 >/dev/null 2>&1; then
        pass "fusermount3：$(command -v fusermount3)"
    elif compgen -G '/usr/lib/*/libfuse3.so*' >/dev/null 2>&1 \
        || compgen -G '/lib/*/libfuse3.so*' >/dev/null 2>&1; then
        pass "libfuse3 已安装"
    else
        warn "未发现 fuse3（apt install fuse3）"
    fi

    if [[ -e /dev/fuse ]]; then
        pass "/dev/fuse 存在"
    else
        bad "/dev/fuse 不存在（modprobe fuse）"
    fi

    local goofys bypy
    if goofys="$(find_exe goofys /usr/local/bin/goofys "$HOME/goofys")"; then
        pass "goofys：$goofys"
    else
        bad "goofys 不存在（PATH、/usr/local/bin/goofys、~/goofys）"
    fi

    if bypy="$(find_exe bypy "$HOME/anaconda3/bin/bypy")"; then
        pass "bypy：$bypy"
    else
        bad "bypy 不存在（PATH、~/anaconda3/bin/bypy）"
    fi

    check_cred "bypy 凭据" "$HOME/.bypy/bypy.json"
    check_cred "AWS 凭据" "$HOME/.aws/credentials"

    if [[ ! -d "$OSS_ROOT_PARENT" ]]; then
        warn "挂载父目录不存在：$OSS_ROOT_PARENT（安装时会 mkdir -p）"
    elif is_mount "$OSS_ROOT_PARENT"; then
        pass "OSS 已挂载：$OSS_ROOT_PARENT"
    else
        bad "OSS 未挂载：$OSS_ROOT_PARENT（见下方挂载命令）"
    fi

    if [[ -f /etc/fuse.conf ]] && grep -Eq '^[[:space:]]*user_allow_other' /etc/fuse.conf; then
        pass "/etc/fuse.conf 已启用 user_allow_other"
    else
        warn "/etc/fuse.conf 未启用 user_allow_other（goofys -o allow_other 需要它）"
    fi

    echo
    if [[ "$FAILS" -eq 0 ]]; then
        echo "结论：通过（WARN 不影响挂载）"
        return 0
    fi
    echo "结论：${FAILS} 项失败"
    return 1
}

# --------------------------------------------------------------------------- #
# 安装
# --------------------------------------------------------------------------- #
install_deps() {
    echo "== 安装系统依赖：fuse3 python3-pip =="
    local sudo=""
    if [[ "$(id -u)" -ne 0 ]]; then
        sudo="sudo"
    fi
    if command -v apt-get >/dev/null 2>&1; then
        $sudo apt-get update
        $sudo apt-get install -y fuse3 python3-pip
    else
        warn "未找到 apt-get；请手动安装 fuse3 / python3-pip"
    fi
}

install_goofys() {
    echo "== 安装 goofys v${GOOFYS_VERSION} → /usr/local/bin/goofys =="
    local tmp
    tmp="$(mktemp -d)"
    # shellcheck disable=SC2064
    trap "rm -rf '$tmp'" RETURN
    if ! curl -fsSL -o "$tmp/goofys" "$GOOFYS_URL"; then
        bad "下载 goofys 失败：$GOOFYS_URL"
        return
    fi
    local sudo=""
    if [[ "$(id -u)" -ne 0 ]]; then
        sudo="sudo"
    fi
    $sudo install -m 755 "$tmp/goofys" /usr/local/bin/goofys
    pass "goofys 已安装：/usr/local/bin/goofys"
}

install_bypy() {
    echo "== 安装 bypy==${BYPY_VERSION}（--user） =="
    python3 -m pip install --user "bypy==${BYPY_VERSION}"
    pass "bypy 已安装（python3 -m pip show bypy 可查）"
}

install_cred() {
    local src="$1" dst="$2"
    if [[ -f "$src" ]]; then
        install -D -m 600 "$src" "$dst"
        pass "已安装 $dst（0600；内容未回显）"
    else
        warn "未找到 $src，跳过 $dst（请手动放置）"
    fi
}

print_systemd_unit() {
    # 可直接重定向成 /etc/systemd/system/goofys.service（或 --user 单元）。
    cat <<EOF
[Unit]
Description=goofys mount for OSS（奇瑞汽车/直播看管）
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(id -un)
ExecStart=/usr/local/bin/goofys -f -o allow_other \\
  --endpoint http://oss-cn-beijing.aliyuncs.com \\
  --subdomain static-cantotimes-com \\
  $OSS_ROOT_PARENT
ExecStop=/bin/fusermount3 -u $OSS_ROOT_PARENT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

print_mount_help() {
    cat <<EOF

== 下一步：挂载 OSS（脚本不后台自动挂载） ==
  1) 确认 /etc/fuse.conf 里有：
       user_allow_other
  2) 前台调试（去掉 -f 可后台运行）：
       goofys -f -o allow_other \\
         --endpoint http://oss-cn-beijing.aliyuncs.com \\
         --subdomain static-cantotimes-com \\
         "$OSS_ROOT_PARENT"
  3) 生产建议交给 systemd（避免 pts 断开导致挂载消失）：
       bash scripts/bootstrap_oss_env.sh --systemd \\
         | sudo tee /etc/systemd/system/goofys.service
       sudo systemctl daemon-reload
       sudo systemctl enable --now goofys.service
  4) 健康检查（断挂时应看到非 fuse 或 check-env FAIL）：
       findmnt -T "$OSS_ROOT_PARENT" -o TARGET,FSTYPE,SOURCE
       python3 -m livepipe.tools.oss_ingest check-env
EOF
}

run_install() {
    install_deps
    install_goofys
    install_bypy
    echo "== 安装凭据（仅当 /secure 下有源文件） =="
    install_cred "/secure/bypy.json" "$HOME/.bypy/bypy.json"
    install_cred "/secure/aws_credentials" "$HOME/.aws/credentials"
    mkdir -p "$OSS_ROOT_PARENT"
    pass "挂载父目录就绪：$OSS_ROOT_PARENT"
    print_mount_help
    if [[ "$FAILS" -ne 0 ]]; then
        echo
        echo "有 ${FAILS} 项失败，请按上面的 FAIL 处理。"
        return 1
    fi
}

# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
MODE="install"
case "${1:-}" in
    --check) MODE="check" ;;
    --systemd) MODE="systemd" ;;
    "")      MODE="install" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
esac

if [[ "$MODE" == "check" ]]; then
    run_check
elif [[ "$MODE" == "systemd" ]]; then
    print_systemd_unit
else
    run_install
fi
