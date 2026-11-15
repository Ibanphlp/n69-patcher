# shellcheck shell=bash
# ramdisk SSH 会话：iproxy + sshpass + 挂卷 + tar 落盘。
#
# 约束（沿用 Python 版踩过的坑）：
#   - 端口 2222→22（iproxy），密码 alpine。
#   - 挂载点：s1→/mnt1(只读)、s4→/mnt6、s5→/mnt7。
#   - 绝不挂载 s2（iOS12 加密 Data）：连只读挂载都会锁死容器。
#   - 远端 tar 后不接 | head（SIGPIPE 会杀 tar）；解包一律 --numeric-owner。

SSH_PORT="${RAMDISK_PORT:-${T_RAMDISK_PORT:-2222}}"
SSH_PW="${RAMDISK_PASSWORD:-${T_RAMDISK_PASSWORD:-alpine}}"
# Isolate USB ramdisk connections from host aliases and keys.
SSH_OPTIONS=(-F /dev/null
    -o PreferredAuthentications=password,keyboard-interactive
    -o PubkeyAuthentication=no -o NumberOfPasswordPrompts=1)
IPROXY_PID=""
SSH_CONNECTED=0
# 掉线时附加到错误信息里的上下文（例如"刚加载完 SEP"）；由调用方设置/清空
SSH_LOST_HINT=""

ssh_argv() {
    printf '%s\0' "$(tool sshpass)" -p "$SSH_PW" ssh "${SSH_OPTIONS[@]}" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR -o ConnectTimeout=10 -p "$SSH_PORT" root@localhost
}

proxy_start() {
    # Reuse a proxy only when it actually reaches the ramdisk. A stale
    # iproxy may target another USB device/port and can cause intermittent
    # SSH authentication failures during APFS block reads.
    ssh_alive && return 0
    if [ -n "${IPROXY_PID:-}" ]; then
        kill "$IPROXY_PID" 2>/dev/null || true
        wait "$IPROXY_PID" 2>/dev/null || true
        IPROXY_PID=""
    fi
    pkill -x iproxy 2>/dev/null || true
    sleep 1
    "$(tool iproxy)" "$SSH_PORT" 22 >/dev/null 2>&1 &
    IPROXY_PID=$!
    # disown：不让 bash 把 iproxy 当作业管理，退出清理时不会打印 "Terminated" 噪音
    disown 2>/dev/null || true
    sleep 2
}

# 重启 iproxy：处理上一次运行残留 / 端口被陈旧进程占用
proxy_restart() {
    if [ -n "${IPROXY_PID:-}" ]; then
        kill "$IPROXY_PID" 2>/dev/null || true
        wait "$IPROXY_PID" 2>/dev/null || true
    fi
    pkill -x iproxy 2>/dev/null || true
    sleep 1
    IPROXY_PID=""
    proxy_start
}

# 带看门狗的命令执行：secs<=0 表示不超时；超时返回 124（避免远端命令卡死整个流程）
run_timeout() {   # secs cmd...
    local secs="$1"; shift
    local pid wd rc=0
    if [ "$secs" -le 0 ]; then
        "$@"
        return $?
    fi
    "$@" &
    pid=$!
    ( sleep "$secs"; kill "$pid" 2>/dev/null; sleep 5; kill -9 "$pid" 2>/dev/null ) >/dev/null 2>&1 &
    wd=$!
    wait "$pid" 2>/dev/null || rc=$?
    kill "$wd" 2>/dev/null || true
    wait "$wd" 2>/dev/null || true
    [ "$rc" -eq 143 ] && return 124
    return "$rc"
}

# ssh_run <cmd>   （stdin/stdout 直通，二进制安全）
ssh_run() {
    "$(tool sshpass)" -p "$SSH_PW" ssh "${SSH_OPTIONS[@]}" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR -o ConnectTimeout=10 \
        -p "$SSH_PORT" root@localhost "$@"
}

# 带超时的远端命令（stdin/stdout/stderr 直通，适合 dd/加载固件这类可能卡住的命令）
ssh_run_timeout() {   # secs cmd
    local secs="$1"; shift
    run_timeout "$secs" "$(tool sshpass)" -p "$SSH_PW" ssh "${SSH_OPTIONS[@]}" -n \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR -o ConnectTimeout=10 \
        -p "$SSH_PORT" root@localhost "$@"
}

ssh_alive() {
    # 探测用短超时：iproxy 在但没有 ramdisk 时，长超时会让等待变得很慢
    "$(tool sshpass)" -p "$SSH_PW" ssh "${SSH_OPTIONS[@]}" -n \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR -o ConnectTimeout=3 \
        -p "$SSH_PORT" root@localhost true >/dev/null 2>&1
}

# 设备是否已经掉出 ramdisk、回到 DFU/Recovery（ramdisk 崩溃/panic 重启的确证）。
# irecovery 不可用时无法判断，返回 1（不误报）。
ramdisk_fell_back() {
    have_tool irecovery || return 1
    device_in_recovery
}

# USB 上是否还能看到 iPhone（任意模式：ramdisk/正常系统/Recovery/DFU）。
# 只用 macOS 的 ioreg；不可用时返回 0（视为"在"，不误报）。
usb_iphone_present() {
    [ -x /usr/sbin/ioreg ] || return 0
    /usr/sbin/ioreg -p IOUSB -w0 2>/dev/null | grep -Eq 'iPhone|Apple Mobile Device'
}

# SEP 加载后设备从 USB 上整个消失、屏幕黑但背光亮 = AP 被 sepOS 启动卡死（不会自己恢复）。
ssh_sep_hang_die() {
    die "SEP 固件加载后设备卡死：USB 上已看不到 iPhone（Finder 也看不到），屏幕黑但背光亮。
  这不是 SSH/端口问题，设备不会自己恢复；ramdisk 在内存里，重启后也回不来。
  newfs_apfs 尚未执行，APFS 卷未被改动。
  处理：
    1) 强制重启：按住 电源+Home 约 8s，松开电源、继续按 Home 约 10s → 进入 DFU。
    2) 这台设备的 ramdisk 无法加载 SEP，加密 DataB 走不通；改用：
         ./n61.sh ramdisk --target ${T_NAME:-n69-ios16}
         ./n61.sh volumes --target ${T_NAME:-n69-ios16} --data-protection none -y
       （patch-rootfs 的 finish 步骤需要 state/keybags.tar）"
}

# ramdisk 已确认不在了：立即中止，不再空等。
ssh_lost_die() {
    local mode
    mode="$(device_query | awk -F': ' '/^MODE/ { print $2; exit }')"
    die "ramdisk 已经不在运行：设备回到了 ${mode:-DFU/Recovery}（ramdisk 崩溃或 panic 后重启）。
  继续等待 SSH 没有意义，已中止。${SSH_LOST_HINT:+
$SSH_LOST_HINT}
  恢复：让设备进 DFU → ./n61.sh ramdisk --target ${T_NAME:-n69-ios16}
        → ./n61.sh status --target ${T_NAME:-n69-ios16} 先确认卷状态，再决定是否重跑。"
}

# ssh_wait [tries] [boot]
#   刚发完 ramdisk 启动链时用 boot=1：设备会短暂仍被 irecovery 看到，不能据此判定失败；
#   之后若 irecovery 还看得到设备，说明启动链没成功。
#   其他情况（status/volumes 等）boot=0：一开始就看到设备停在 DFU/recovery 时立即报错。
#   已建立的会话重连用 boot=2：允许 USB 暂时消失；但只要 irecovery 看到设备回到
#   DFU/Recovery，就说明 ramdisk 已崩溃/重启，立即中止（不再空等 60s）。
ssh_wait() {
    local tries="${1:-120}" boot="${2:-0}" i=0 last="" _t
    for _t in sshpass iproxy; do
        have_tool "$_t" || die "缺少工具: ${_t}（应在 n61-patcher-master/bin/ 下，指向 ramdisk-main/Darwin/${_t}）"
    done
    proxy_start
    if ssh_alive; then
        SSH_CONNECTED=1
        ok "ramdisk SSH 已连通。"
        return 0
    fi
    if [ "$boot" -eq 0 ] && ! devices_present; then
        die "未检测到 USB recovery/DFU 设备，SSH ramdisk 也未响应。status/volumes 不会启动 ramdisk。
  先检查 USB 连接并让 iPhone 进入 DFU（irecovery -q 应显示 MODE: DFU），再运行 ./n61.sh ramdisk --target n69-ios16；SSH 连通后再运行 status。"
    fi
    if [ "$boot" -eq 0 ] && device_in_recovery; then
        die "设备还在 DFU/recovery，尚未进入 ramdisk：先跑 ./n61.sh ramdisk（或确认 ramdisk 已启动）"
    fi
    if [ "$boot" -eq 2 ] && ramdisk_fell_back; then
        ssh_lost_die
    fi
    log "等待 ramdisk SSH…（最多 ${tries}s）"
    local usb_gone=0
    while [ "$i" -lt "$tries" ]; do
        if ssh_alive; then
            SSH_CONNECTED=1
            ok "ramdisk SSH 已连通。"
            return 0
        fi
        i=$(( i + 1 ))
        if [ "$boot" -eq 1 ] && [ "$i" -eq 40 ] && device_in_recovery; then
            die "发送 ramdisk 后 40s 设备仍停在 DFU/recovery：启动链可能没成功，重跑 ./n61.sh ramdisk${SSH_LOST_HINT:+
$SSH_LOST_HINT}"
        fi
        # 重连模式：每 5s 看一次设备是否已掉回 DFU/Recovery
        if [ "$boot" -eq 2 ] && [ $(( i % 5 )) -eq 0 ] && ramdisk_fell_back; then
            ssh_lost_die
        fi
        # 刚加载完 SEP 时：iPhone 连续 10s 不在 USB 上 = 设备卡死，立即中止
        if [ "$boot" -eq 2 ] && [ -n "$SSH_LOST_HINT" ]; then
            if usb_iphone_present; then
                usb_gone=0
            else
                usb_gone=$(( usb_gone + 1 ))
                [ "$usb_gone" -ge 10 ] && ssh_sep_hang_die
            fi
        fi
        if [ "$i" -eq 60 ]; then
            warn "SSH 60s 未通，重启 iproxy 后继续等待…"
            proxy_restart
        fi
        if [ $(( i % 10 )) -eq 0 ]; then
            log "  仍在等待 SSH…（${i}s）"
        fi
        sleep 1
    done
    [ "$boot" -eq 2 ] && ramdisk_fell_back && ssh_lost_die
    last="$(ssh_run true 2>&1 | tail -3 || true)"
    local why=""
    case "$last" in
        *"Connection reset by peer"*|*"Connection closed"*)
            why="
  诊断：'Connection reset by peer' = iproxy 在 USB 上找不到监听 22 端口的设备。
        这不是端口 ${SSH_PORT} 被占用，而是 ramdisk 已不在运行
        （设备可能 panic 后重启进了正常系统、Recovery 或黑屏 DFU）。" ;;
        *"Connection refused"*)
            why="
  诊断：'Connection refused' = 本机端口 ${SSH_PORT} 上没有 iproxy 或被别的程序占用。" ;;
    esac
    die "ramdisk SSH 不通（${tries}s）。
  确认设备屏幕已进入 SSHRD ramdisk、USB 连接正常、没有别的程序占用端口 ${SSH_PORT}。
  最近 SSH 错误: ${last:-无输出}${why}${SSH_LOST_HINT:+
$SSH_LOST_HINT}"
}

ssh_ensure() {
    proxy_start
    if ssh_alive; then
        SSH_CONNECTED=1
        return 0
    fi
    if [ "$SSH_CONNECTED" -eq 1 ]; then
        ssh_wait 60 2
    else
        ssh_wait
    fi
}

# 远端命令，失败即中止；stdout 透传。secs>0 时超时返回 124。
rsh_timeout() {   # secs cmd
    local secs="$1" cmd="$2" out rc=0
    out="$(ssh_run_timeout "$secs" "$cmd" 2>"$WORK/.ssh-err")" || rc=$?
    if [ "$rc" -ne 0 ]; then
        if [ "$rc" -eq 124 ]; then
            die "远端命令超时（${secs}s）: ${cmd}
$(cat "$WORK/.ssh-err" 2>/dev/null)
  若这是 newfs_apfs，容器可能已被锁住：请重启设备进 DFU 后重试。"
        fi
        if [ "$rc" -eq 255 ] && grep -q "Permission denied" "$WORK/.ssh-err"; then
            die "ramdisk SSH authentication failed on port ${SSH_PORT}.
  Default password: alpine. Export RAMDISK_PASSWORD to use a different password.
  Check that this port forwards to the intended ramdisk.
$(cat "$WORK/.ssh-err")"
        fi
        if [ "$rc" -eq 255 ] && [[ "$cmd" == *newfs_apfs* ]]; then
            die "创建 APFS 卷时 SSH 连接中断：${cmd}
$(cat "$WORK/.ssh-err" 2>/dev/null)
  设备或 ramdisk 可能已重启，DataB 状态未知。重新进入 DFU 并启动 ramdisk 后，先运行 ./n61.sh status --target n69-ios16 检查卷，再决定是否重试；不要直接重复格式化。"
        fi
        die "远端命令失败($rc): $cmd
$(cat "$WORK/.ssh-err" 2>/dev/null)"
    fi
    printf '%s' "$out"
}

rsh() { rsh_timeout 0 "$1"; }

rsh_ok() { ssh_run "$1" >/dev/null 2>&1; }

slices() {
    local out
    out="$(rsh "ls /dev/disk0s1s*")" || return 1
    printf '%s\n' "$out" | tr '\n' ' '
}

mount_vol() {   # n mnt [ro]
    local n="$1" mnt="$2" ro="${3:-}"
    local opt=""
    [ "$ro" = "ro" ] && opt="-o ro "
    rsh "/sbin/mount | /usr/bin/grep -q \"disk0s1s$n on $mnt\" || /sbin/mount_apfs ${opt}/dev/disk0s1s$n $mnt"
}

umount_vols() {
    local m
    for m in "$@"; do
        ssh_run "/sbin/umount $m" >/dev/null 2>&1 || true
    done
}

sync_dev() { ssh_run sync >/dev/null 2>&1 || true; }

# SEP 是否可用：seputil 失败时可能打印 "ping failed" / "Failed to connect to sep" /
# "No SEP present on this device" 等，任一出现或退出码非 0 都算不可用。
# 注意：不能只 grep "ping failed"（实测别的失败文案会被误判成"SEP 就绪"，随后 -P 卡死）。
sep_ping_ok() {
    local out rc
    rc=0
    out="$(ssh_run_timeout 15 '/usr/libexec/seputil --ping 2>&1' 2>/dev/null)" || rc=$?
    if [ "$rc" -eq 255 ]; then
        warn "SEP ping 的 SSH 连接中断，检查设备是否仍在 ramdisk…"
        # 已掉回 DFU/Recovery 时 ssh_ensure 会立即中止（见 ssh_wait boot=2）
        ssh_ensure || return 1
        rc=0
        out="$(ssh_run_timeout 15 '/usr/libexec/seputil --ping 2>&1' 2>/dev/null)" || rc=$?
    fi
    log "  SEP ping: rc=${rc}${out:+ → $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-120)}"
    [ "$rc" -eq 0 ] || return 1
    case "$out" in
        *"ping failed"*|*"Failed to connect"*|*"No SEP present"*|*"ERROR"*|*"error"*) return 1 ;;
    esac
    return 0
}

# A9/A9X：在 ramdisk 里 seputil --load 会让 AP 卡死（黑屏、背光亮、USB 消失；SSHRD_Script #319）
device_is_a9() {
    local m
    m="$(ssh_run_timeout 10 "uname -m" 2>/dev/null | tr -d '\r\n ' || true)"
    case "$m" in
        iPhone8,1|iPhone8,2|iPhone8,4|iPad6,3|iPad6,4|iPad6,7|iPad6,8|iPad6,11|iPad6,12) return 0 ;;
    esac
    case "${T_MODEL:-}" in n66*|n69*|n71*) return 0 ;; esac
    return 1
}

# SEP：加密建卷与 systembag 依赖它（需已只读挂载 /mnt1）。
# 优先使用 ramdisk 启动时由 iBoot（rsepfirmware）加载的 SEP；
# 只有非 A9 设备才退回到 ramdisk 内 seputil --load。
ensure_sep() {
    local fw="/usr/standalone/firmware/sep-firmware.img4" path="" hash=""
    local marker="${WK:-/nonexistent}/.ramdisk-sep-sent" attempt
    if sep_ping_ok; then
        return 0
    fi

    if [ -f "$marker" ]; then
        # SEP 已由 iBoot 交给内核，可能仍在启动：只重试 ping，绝不再 seputil --load
        log "ramdisk 带 SEP 启动，等待 SEP 就绪…"
        for attempt in 1 2 3 4 5 6; do
            sleep 2
            if sep_ping_ok; then
                log "SEP 已就绪（ramdisk 启动时由 iBoot 加载）。"
                return 0
            fi
        done
        die "SEP image was sent before ramdisk boot ($(cat "$marker" 2>/dev/null)), but seputil --ping still fails.
  A successful transfer or matching device identity does not prove SEP accepted or started the image.
  The cause is unknown; inspect the SEP transfer output, ramdisk configuration, and firmware compatibility.
  Do not use seputil --load on A9 or bypass this check. Stopping before continuing."
    fi

    if device_is_a9 && [ "${N61_FORCE_SEPUTIL_LOAD:-0}" != "1" ]; then
        die "A9 设备不能在 ramdisk 里用 seputil --load 加载 SEP：内核会卡死
  （黑屏、背光亮、Finder 看不到设备；SSHRD_Script #319）。已中止，APFS 卷未被改动。
  正确做法：让 iBoot 在 ramdisk 启动时加载 SEP（rsepfirmware）：
    1) 强制重启进 DFU：按住 电源+Home 约 8s，松开电源、继续按 Home 约 10s。
    2) ./n61.sh ramdisk --target ${T_NAME:-n69-ios16}
       （有 state/IM4M.bin 时会自动构建并发送 ramdisk_SEP.img4，
        日志里应出现「发送 SEP（iBoot rsepfirmware）」。）
    3) 再重跑本步骤。"
    fi

    log "加载原生 SEP 固件…"
    # 旧系统：固件在 s1；iOS15：在 Preboot 的 <active-hash>/usr/standalone/firmware 下
    if ssh_run "test -f \"/mnt1${fw}\"" >/dev/null 2>&1; then
        path="/mnt1${fw}"
    else
        preboot_mount
        hash="$(ssh_run "cat ${PREBOOT_MNT:-/mnt8}/active 2>/dev/null" 2>/dev/null | tr -d '\r\n ' || true)"
        if [ -n "$hash" ] && ssh_run "test -f \"${PREBOOT_MNT:-/mnt8}/${hash}${fw}\"" >/dev/null 2>&1; then
            path="${PREBOOT_MNT:-/mnt8}/${hash}${fw}"
        else
            path="$(ssh_run "ls ${PREBOOT_MNT:-/mnt8}/*${fw} 2>/dev/null | head -1" 2>/dev/null | tr -d '\r' || true)"
        fi
        [ -n "$path" ] || die "找不到 SEP 固件：/mnt1${fw} 与 Preboot(<hash>${fw}) 都没有。"
    fi
    log "SEP 固件: $path"

    # 从这里起若 ramdisk 掉线，几乎可以确定是 SEP 启动导致内核 panic；
    # 此时 newfs_apfs 还没执行，APFS 容器未被改动。
    SSH_LOST_HINT="  原因：SEP 固件刚加载完 ramdisk 就掉线 —— sepOS 启动触发了内核 panic，设备自动重启。
        已加载的固件: ${path}
        newfs_apfs 尚未执行，APFS 卷未被改动。
  常见原因与处理：
    1) ramdisk 的 iOS 版本必须与该 SEP 固件（本机已装系统，通常 15.8.8）一致；
       检查 ${T_RAMDISK_DIR:-T_RAMDISK_DIR} 里的 ramdisk 是否用同一版本生成（如 sshrd.sh 15.8.8）。
    2) 不需要加密 DataB 时改用 --data-protection none（volumes 将不再加载 SEP；
       patch-rootfs 的 finish 步骤需要 state/keybags.tar）。"

    local load_out load_rc
    load_rc=0
    load_out="$(ssh_run_timeout 90 "/usr/libexec/seputil --load \"${path}\"" 2>&1)" || load_rc=$?
    log "SEP load: rc=${load_rc}${load_out:+ → $(printf '%s' "$load_out" | tr '\n' ' ' | cut -c1-160)}"
    if [ "$load_rc" -ne 0 ]; then
        warn "SEP load 命令返回非零，确认 ramdisk SSH 已恢复后继续检查…"
        ssh_ensure || die "SEP load 后 ramdisk SSH 未恢复；已中止加密建卷。"
    fi
    # sepOS 启动需要一点时间；立刻 ping 只会得到 "ping failed"
    sleep 3
    # Keep the firmware source mounted through the verification ping. The
    # caller that formats APFS must unmount it before changing the container.
    ssh_ensure || die "SEP load 后 ramdisk SSH 未恢复；已中止加密操作。"
    # Loading can return before SEP answers. Retry readiness only; never
    # reload firmware or permit protected formatting after a failed ping.
    local attempt
    for attempt in 1 2 3 4 5; do
        if sep_ping_ok; then
            SSH_LOST_HINT=""
            log "SEP 已就绪。"
            return 0
        fi
        [ "$attempt" -eq 5 ] || sleep 2
    done
    die "SEP 仍不可用（seputil --ping 失败）。
  此时执行 newfs_apfs -P 会卡死并锁死容器，已中止。
  处理：重启设备 → 重新进 DFU → 重跑 ./n61.sh ramdisk；
  不想用加密 DataB 可改 --data-protection none（需要 state/keybags.tar）。"
}

# 本地文件/目录 → 设备（流式 tar，不生成中间包；目录可指定属主）
deploy_path() {
    local src="$1" dest="$2" uid="${3:-}" gid="${4:-}" rc=0
    src="$(cd "$(dirname "$src")" && pwd)/$(basename "$src")"
    if [ -d "$src" ]; then
        rsh "mkdir -p \"$dest\""
        COPYFILE_DISABLE=1 /usr/bin/tar --no-xattrs ${uid:+--uid "$uid"} ${gid:+--gid "$gid"} \
            -cpf - -C "$src" . \
            | ssh_run "/usr/bin/tar --numeric-owner -xpf - -C \"$dest\"" || rc=$?
        [ "$rc" -eq 0 ] || die "部署目录失败（tar 退出码 ${rc}）: $src → $dest"
        ssh_run "test -d \"$dest\"" || die "部署目录失败（目标不存在）: $dest"
    else
        deploy_file "$src" "$dest"
    fi
}

# 本地文件 → 设备指定路径（目标文件名可与源不同，如 mrm.plist.sig）
deploy_file() {
    local src="$1" dest="$2"
    rsh "mkdir -p \"$(dirname "$dest")\""
    ssh_run "cat > \"$dest\"" <"$src" || die "部署文件失败: $src → $dest"
    ssh_run "test -s \"$dest\"" || die "部署文件后为空: $dest"
}

# 按相对路径清单从本地 root 流式部署（tar -T，避免 argv 过长）
deploy_relative_paths() {   # root dest listfile
    local root="$1" dest="$2" list="$3"
    [ -s "$list" ] || { log "  （清单为空，跳过）"; return 0; }
    root="$(cd "$root" && pwd)"
    rsh "mkdir -p \"$dest\""
    COPYFILE_DISABLE=1 /usr/bin/tar --no-xattrs -cpf - -C "$root" -T "$list" \
        | ssh_run "/usr/bin/tar --numeric-owner -xpf - -C \"$dest\"" ||
        die "文件集流式写入失败: $(wc -l <"$list" | tr -d ' ') 项 → $dest"
}

# 设备 → 本地
pull_file() {   # remote local
    mkdir -p "$(dirname "$2")"
    ssh_run "cat \"$1\"" >"$2" || die "读取设备文件失败: $1"
    [ -s "$2" ] || die "读取设备文件为空: $1"
}

# 设备文件区间读成十六进制串（od 在本地做，避免二进制过 shell 变量丢 NUL）
read_hex() {   # remote offset len
    ssh_run "dd if=\"$1\" bs=1 skip=$2 count=$3 2>/dev/null" | od -An -v -tx1 | tr -d ' \n'
}

# 十六进制串写回设备文件区间（printf %b 解释 \xNN，二进制安全）
write_hex() {   # remote offset hex
    local esc="" i=0 hex="$3"
    while [ "$i" -lt "${#hex}" ]; do
        esc="$esc\\x${hex:$i:2}"
        i=$(( i + 2 ))
    done
    printf '%b' "$esc" | ssh_run "dd of=\"$1\" bs=1 seek=$2 conv=notrunc 2>/dev/null"
}
