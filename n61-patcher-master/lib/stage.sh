# shellcheck shell=bash
# 各阶段实现。对应 Python 版 n56patcher.py 的 Flow。

require_root() {
    [ "$(id -u)" -eq 0 ] || die "$1 需要写设备存储，请用 sudo 运行：sudo ./n61.sh $1"
}

dry() { [ "${OPT_DRY_RUN:-0}" -eq 1 ]; }

# data_protection 的唯一判断入口（n61.sh 负责缺省与合法性校验）
data_is_encrypted() { [ "${T_DATA_PROTECTION:-}" = "encrypted" ]; }

# 选挂清单文件：dry-run 用临时文件，避免"只演练却写工作目录"
patch_selection_file() {
    if dry; then
        mktemp -t n61-patches || die "无法创建临时清单文件"
    else
        printf '%s\n' "$WK/patches-selected.txt"
    fi
}

# 选挂清单 → 排序后的 patch id（build 时与安装时对比，不一致就警告）
selection_ids() {   # listfile
    awk -F'\t' '{ print $3 }' "$1" | LC_ALL=C sort
}

# ---------- 构建/安装前置检查（放在设备操作之前，避免写入 25 分钟后才失败）----------

preflight_derived() {
    derived_path ibss_src >/dev/null
    derived_path ibec_dec >/dev/null
    derived_path j96_kernel >/dev/null
    derived_path trustcache >/dev/null
}

preflight_donor_ipsw() {
    local kind entry ipsw var override seen="" kinds="${T_PRIMARY_DONOR:-n66} j96"
    # UI donor（n71/n66）只在选了 ui 相关 feature 时才需要
    case " ${OPT_FEATURES:-} " in
        *" ui "*) kinds="$kinds ${T_UI_DONOR:-n66}" ;;
    esac
    for kind in $kinds; do
        case " $seen " in *" $kind "*) continue ;; esac
        seen="$seen $kind"
        case "$kind" in
            n66) entry="${T_ROOTFS_ENTRY_N66:-}"; ipsw="${T_IPSW_N66:-}"; var=N56_ROOTFS_N66 ;;
            n71) entry="${T_ROOTFS_ENTRY_N71:-}"; ipsw="${T_IPSW_N71:-}"; var=N56_ROOTFS_N71 ;;
            j96) entry="${T_ROOTFS_ENTRY_J96:-}"; ipsw="${T_IPSW_J96:-}"; var=N56_ROOTFS_J96 ;;
            n61) entry="${T_ROOTFS_ENTRY_N61:-}"; ipsw="${T_IPSW_N61:-}"; var=N56_ROOTFS_N61 ;;
            n69) entry="${T_ROOTFS_ENTRY_N69:-}"; ipsw="${T_IPSW_N69:-}"; var=N56_ROOTFS_N69 ;;
            *)   continue ;;
        esac
        # If kind is n69, ensure the vars are resolved from T_IPSW_N69 (already defined in manifest)
        if [ "$kind" = "n69" ]; then
             entry="${T_ROOTFS_ENTRY_N69:-}"
             ipsw="${T_IPSW_N69:-}"
        fi
        eval "override=\${$var:-}"
        [ -n "$override" ] && continue
        [ -n "$entry" ] && [ -n "$ipsw" ] ||
            die "清单缺少 ${kind} 的 rootfs_entry/ipsw 声明"
        [ -f "$REPO/ipsw/cache/$entry" ] || [ -f "$REPO/ipsw/$ipsw" ] ||
            die "缺少 ${kind} donor：ipsw/${ipsw}（或缓存 ipsw/cache/${entry}）"
    done
}

preflight_common_assets() {
    preflight_donor_ipsw
    case " ${OPT_FEATURES:-} " in
        *" checkra1n "*)
            [ -d "$(checkra1n_assets)" ] || die "缺少 checkra1n 资产: $(checkra1n_assets)（先跑 tools/setup.py）"
            ;;
    esac
}

preflight_build_inputs() {
    preflight_common_assets
    preflight_derived
    # Ensure REGISTRY is set, falling back to default
    : "${REGISTRY:=$REPO/patches/registry.tsv}"
    [ -f "$REGISTRY" ] || die "缺少补丁注册表: $REGISTRY"
    [ -f "$REPO/patches/dtdata/$(dt_board).meta" ] ||
        die "缺少 DeviceTree 资产: patches/dtdata/$(dt_board).meta（先跑 python3 tools/export_dtdata.py）"
}

preflight_install_inputs() {
    preflight_common_assets
    case " ${OPT_FEATURES:-} " in
        *" health "*)
            health_libs_dir >/dev/null || die "缺少 Health 库缓存（50 个文件），rootfs.health 无法完整部署。
  用 N56_HEALTH_LIBS=/path 指定，或先跑一次 Python 版 patch-rootfs 做一次性派生；
  不想装 Health 时在 --features 里去掉 health。"
            ;;
    esac
    # iOS14 fsck/apfs.fs 只在 iOS13 目标里需要；清单留空则跳过（iOS16 目标不需要）。
    if [ -n "${T_DONOR_APFS14:-}" ]; then
        [ -d "$(donor_dir)/${T_DONOR_APFS14}" ] ||
            die "缺少 iOS14 fsck 资产: $(donor_dir)/${T_DONOR_APFS14}"
    fi
}

preflight_ramdisk_assets() {
    local dir name
    dir="$REPO/${T_RAMDISK_DIR}"
    for name in iBSS iBEC ramdisk devicetree trustcache kernelcache; do
        [ -f "$dir/$name.img4" ] || die "缺少 ramdisk 组件: $dir/$name.img4"
    done
}

# ---------- list ----------
stage_list() {
    local f layer role id feat
    # 用临时文件：list 不能覆盖 build/patch-rootfs 的选挂清单
    f="$(mktemp -t n61-list)" || die "无法创建临时清单文件"
    log "目标 $T_NAME  features=[$OPT_FEATURES]"
    select_patches "$OPT_FEATURES" "$OPT_PATCHES" "$OPT_EXCLUDE" "$T_BASE_DT" "$T_BASE_KC" \
        "${OPT_PATCHES_SET:-0}" "${T_PATCHES:-}" >"$f" ||
        { rm -f "$f"; die "补丁选择失败"; }
    while IFS="$(printf '\t')" read -r layer role id feat; do
        printf '  [%-6s] %-9s %-9s %s\n' "$layer" "$role" "$feat" "$id"
    done <"$f"
    rm -f "$f"
    return 0
}

# ---------- status ----------
stage_status() {
    if dry; then log "dry-run：跳过读取卷信息。"; return 0; fi
    ssh_ensure
    apfs_list
}

# ---------- ramdisk ----------
stage_ramdisk() {
    if dry; then log "dry-run：跳过设备发送。"; return 0; fi
    if have_tool sshpass && have_tool iproxy; then
        proxy_start
        if ssh_alive; then
            ok "设备已经在 SSH ramdisk 里，跳过发送。"
            if [ ! -f "$WK/.ramdisk-sep-sent" ] && data_is_encrypted; then
                warn "当前 ramdisk 不是带 SEP 启动的；加密 DataB 需要强制重启进 DFU 后重跑 ramdisk。"
            fi
            return 0
        fi
    fi
    prepare_ramdisk_sep
    log "进入 SSH ramdisk…"
    send_ramdisk_chain
}

# ---------- capture-sep ----------
stage_capture_sep() {
    local root="$REPO/.." mount="${PREBOOT_MNT:-/mnt8}" slot active candidates source tmp img4tool_bin
    local -a select_args
    [ "${T_NAME:-}" = "n69-ios16" ] || die "capture-sep 仅支持 n69-ios16"
    if dry; then log "dry-run: skip Preboot SEP capture"; return 0; fi
    ssh_ensure
    slot="$(preboot_slot)" || die "Cannot find the Preboot volume"
    rsh "test -d $mount" || die "Preboot mount point does not exist: $mount"
    rsh "m=\$(/sbin/mount | /usr/bin/grep -F ' on $mount ' || true); [ -z \"\$m\" ] || printf '%s\n' \"\$m\" | /usr/bin/grep -Fq '/dev/disk0s1s${slot} on $mount '" ||
        die "Preboot mount point is occupied by another filesystem"
    mount_vol "$slot" "$mount" ro || die "Cannot mount Preboot"
    active="$(ssh_run_timeout 30 "if [ -f $mount/active ]; then /bin/cat $mount/active; fi")" || die "Cannot read Preboot active slot"
    candidates="$(ssh_run_timeout 90 "/usr/bin/find $mount -name sep-firmware.img4 -type f")" || die "Cannot enumerate Preboot SEP images"
    select_args=(select --mount "$mount" --active "$active" --stdin)
    [ -z "${N69_PREBOOT_HASH:-}" ] || select_args+=(--requested "$N69_PREBOOT_HASH")
    source="$(printf '%s\n' "$candidates" | python3 "$root/tools/sep_image.py" "${select_args[@]}")" || die "Cannot select a unique Preboot SEP image"
    img4tool_bin="$root/ramdisk-main/Darwin/img4tool"
    [ -x "$img4tool_bin" ] || img4tool_bin="$(tool img4tool)" || die "img4tool unavailable"
    mkdir -p "$root/state" || die "Cannot create state directory"
    tmp="$(umask 077; mktemp -d "$root/state/.sep-capture.XXXXXXXX")" || die "Cannot create private capture directory"
    if ! ssh_run_timeout 90 "/bin/cat '$source'" >"$tmp/sep-firmware.img4"; then
        rm -rf "$tmp"
        die "SEP download failed; existing capture was not changed"
    fi
    if ! python3 "$root/tools/sep_image.py" validate --image "$tmp/sep-firmware.img4" \
        --source "$source" --mount "$mount" --img4tool "$img4tool_bin" \
        --ticket-out "$tmp/IM4M.bin" --receipt-out "$tmp/sep-capture.json"; then
        rm -rf "$tmp"
        die "SEP validation failed; existing capture was not changed"
    fi
    mv -f "$tmp/sep-firmware.img4" "$root/state/sep-firmware.img4" || die "Cannot install SEP capture: $tmp"
    mv -f "$tmp/IM4M.bin" "$root/state/IM4M.bin" || die "Cannot install SEP ticket: $tmp"
    mv -f "$tmp/sep-capture.json" "$root/state/sep-capture.json" || die "Cannot install SEP receipt: $tmp"
    rmdir "$tmp" || die "Cannot remove capture directory: $tmp"
    ok "Captured complete Preboot SEP and embedded IM4M in $root/state"
}

# ---------- extract-im4m ----------
# v0-n69-onboard-extraction-fix-v1
stage_extract_im4m_n69() {
    local tmp raw shsh candidate img4tool_bin size rc=0
    if dry; then log "dry-run: skip N69 onboard ticket extraction"; return 0; fi
    ssh_ensure
    mkdir -p "$REPO/state" || die "Cannot create state directory"
    img4tool_bin="$REPO/../ramdisk-main/Darwin/img4tool"
    [ -x "$img4tool_bin" ] || img4tool_bin="$(tool img4tool)" || die "img4tool unavailable"
    tmp="$(umask 077; mktemp -d "$REPO/state/.n69-onboard.XXXXXXXX")" || die "Cannot create private working directory"
    raw="$tmp/dump.raw"
    shsh="$tmp/dumped.shsh2"
    candidate="$tmp/IM4M.bin"
    log "Reading first 4 MiB from /dev/rdisk1 with 4096-byte blocks (read-only)..."
    ssh_run_timeout 120 "dd if=/dev/rdisk1 bs=4096 count=1024" >"$raw" || rc=$?
    if [ "$rc" -ne 0 ]; then
        rm -f "$raw"; rmdir "$tmp" 2>/dev/null || true
        die "Onboard read failed (status $rc); existing IM4M remains unchanged"
    fi
    size="$(wc -c <"$raw" | tr -d ' ')"
    if [ "$size" -ne 4194304 ]; then
        rm -f "$raw"; rmdir "$tmp" 2>/dev/null || true
        die "Onboard read incomplete ($size bytes); existing IM4M remains unchanged"
    fi
    if ! "$img4tool_bin" --convert -s "$shsh" "$raw" >"$tmp/convert.log" 2>&1; then
        die "Onboard conversion failed. Private diagnostic files: $tmp; existing IM4M remains unchanged"
    fi
    if ! "$img4tool_bin" -e -s "$shsh" -m "$candidate" >"$tmp/extract.log" 2>&1 || [ ! -s "$candidate" ]; then
        die "No usable ticket extracted. Private diagnostic files: $tmp; existing IM4M remains unchanged"
    fi
    rm -f "$raw"
    log "Candidate IM4M: $candidate"
    warn "Candidate is not yet verified for this device or SEP; state/IM4M.bin was not replaced."
    die "Stopped before installation: validate ticket identity and SEP compatibility first."
}

stage_extract_im4m() {
    if [ "${T_NAME:-}" = "n69-ios16" ]; then
        stage_capture_sep
        return $?
    fi
    local disk2 out size
    if dry; then log "dry-run：跳过重提 IM4M。"; return 0; fi
    ssh_ensure
    mkdir -p "$REPO/state" || die "无法创建 state/ 目录"
    disk2="$REPO/state/disk2.bin"
    out="$REPO/state/IM4M.bin"
    log "从 NOR(/dev/disk2) 读取 4MB 并提取 IM4M…"
    rm -f "$disk2"
    local rc=0
    ssh_run_timeout 120 "dd if=/dev/disk2 bs=256 count=16384 2>/dev/null" >"$disk2" || rc=$?
    [ "$rc" -eq 0 ] || {
        rm -f "$disk2"
        [ "$rc" -eq 124 ] && die "NOR 读取超时（120s）：重启设备进 DFU 后重跑 ramdisk/extract-im4m"
        die "NOR 读取失败"
    }
    size="$(wc -c <"$disk2" | tr -d ' ')"
    [ "$size" -ge 4096 ] ||
        { rm -f "$disk2"; die "NOR 读取异常，仅 ${size} 字节；确认 ramdisk 已就绪且 /dev/disk2 存在"; }
    "$(tool img4)" -i "$disk2" -m "$out" || die "IM4M 提取失败"
    [ -s "$out" ] || die "IM4M 提取失败：${out} 为空"
    ok "新 IM4M: $out ($(wc -c <"$out" | tr -d ' ') 字节)"
}

# ---------- volumes ----------
stage_volumes() {
    local encrypt=0 state=0 data_name
    data_name="$(printf '%s' "${T_VOL_CREATE:-DataB}" | awk '{print $2}')"
    [ -n "$data_name" ] || data_name="DataB"
    if dry; then log "dry-run：跳过创建卷。"; return 0; fi
    ssh_ensure
    data_is_encrypted && encrypt=1

    if [ "${OPT_RECREATE_DATA:-0}" -eq 1 ] && apfs_vol_exists "$T_MNT_DATA_B"; then
        # 先把可能残留的挂载卸掉，否则 apfs_deletefs 会 resource busy
        umount_vols /mnt7 /mnt1
        log "删除已有 ${data_name}（--recreate-data）…"
        rsh_timeout 120 "/sbin/apfs_deletefs /dev/disk0s1s${T_MNT_DATA_B}"
    fi

    if apfs_vol_exists "$T_MNT_DATA_B"; then
        apfs_vol_exists "$T_MNT_SYSTEM_B" ||
            die "卷布局异常：存在 s${T_MNT_DATA_B} 但没有 s${T_MNT_SYSTEM_B}，请先处理卷再重试"
        apfs_name_encrypted "$data_name"
        state=$?
        case "$state" in
            0) [ "$encrypt" -eq 1 ] ||
                   die "${data_name} 已存在且为 encrypted，但本次 --data-protection 不是 encrypted。
  换保护模式需要重建 DataB：加 --recreate-data 重跑 volumes（会删除 DataB 数据）。" ;;
            1) [ "$encrypt" -eq 0 ] ||
                   die "${data_name} 已存在且为 UNENCRYPTED，但本次 --data-protection 是 encrypted。
  换保护模式需要重建 DataB：加 --recreate-data 重跑 volumes（会删除 DataB 数据）。" ;;
            *) die "找不到已存在的 ${data_name} 卷状态，无法确认加密模式" ;;
        esac
        log "s4/s5 已存在且加密模式一致，跳过建卷。"
    fi

    apfs_create_volumes "$T_VOL_CREATE" "$encrypt"
    if [ "$encrypt" -eq 1 ]; then
        apfs_report_data_encryption "$data_name"
    fi
    return 0
}

# ---------- rootfs ----------
stage_rootfs() {
    local mnt
    if dry; then log "dry-run：跳过 rootfs 写入。"; return 0; fi
    require_root rootfs
    preflight_install_inputs
    ssh_ensure
    # donor_mount 负责 owners 模式、复用已挂载点、校验 rootfs build 号（17H35）；
    # 卸载交给 EXIT trap（donor_umount_all），避免失败/中断时留下挂载。
    mnt="$(donor_mount j96)" || die "rootfs DMG 挂载失败"
    [ -n "$mnt" ] || die "rootfs DMG 挂载失败（空挂载点）"
    guard_target_volumes system
    mount_vol "$T_MNT_SYSTEM_B" /mnt6
    log "清空目标卷 /mnt6…"
    rsh 'test "$(/sbin/mount | /usr/bin/grep -c "/dev/disk0s1s'"$T_MNT_SYSTEM_B"' on /mnt6")" -eq 1 && /bin/rm -rf /mnt6/.[!.]* /mnt6/*'

    log "流式写入 rootfs（约 25 分钟）…"
    /usr/bin/tar -cpf - -C "$mnt" . | ssh_run "/usr/bin/tar --numeric-owner -xpf - -C /mnt6" ||
        die "rootfs 流式写入失败"

    ssh_ensure
    log "$(rsh 'ls -ln /mnt6/sbin/launchd /mnt6/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64')"
    return 0
}

# ---------- patch-rootfs ----------
# 与 Python 版一致：只跑 rootfs 层里 role ∈ {overlay, finalize} 的补丁，
# 顺序取自注册表（已做 role 排序 + 同层依赖拓扑）。
patch_apply() {   # id
    case "$1" in
        rootfs.finish)    stage_finish ;;
        rootfs.set_roles) stage_set_roles ;;
        *)                ov_apply "$1" ;;
    esac
}

stage_patch_rootfs() {
    local layer role id feat note ran=0 skipped=0 expected=0 list
    local saw_finish=0 saw_roles=0
    list="$(patch_selection_file)" || die "无法创建补丁清单文件"
    select_patches "$OPT_FEATURES" "$OPT_PATCHES" "$OPT_EXCLUDE" \
        "$T_BASE_DT" "$T_BASE_KC" "${OPT_PATCHES_SET:-0}" "${T_PATCHES:-}" >"$list" ||
        { rm -f "$list"; die "补丁选择失败"; }

    expected="$(awk -F'\t' '$1 == "rootfs" && ($2 == "overlay" || $2 == "finalize")' "$list" |
        wc -l | tr -d ' ')"
    [ "$expected" -gt 0 ] ||
        { rm -f "$list"; die "选挂里没有 rootfs overlay/finalize 补丁（检查 --features/--exclude）"; }

    if dry; then
        # 用 fd 3 读清单：循环体里的命令（尤其 ssh）会继承 stdin，
        # 如果清单在 stdin 上，远端命令会把剩余行吃掉，导致只跑前几个补丁。
        while IFS="$(printf '\t')" read -r -u 3 layer role id feat; do
            [ "$layer" = "rootfs" ] || continue
            case "$role" in overlay|finalize) ;; *) continue ;; esac
            if [ "$(patch_manual "$id")" = "1" ] && [ "${OPT_ALLOW_MANUAL:-0}" -ne 1 ]; then
                warn "跳过 manual patch ${id}（加 --allow-manual 才执行）；$(patch_note "$id")"
                continue
            fi
            log "→ $id  ($(patch_note "$id"))"
        done 3<"$list"
        log "dry-run：选挂已校验（${expected} 个 rootfs 补丁），跳过 rootfs 补丁应用。"
        [ "$list" = "$WK/patches-selected.txt" ] || rm -f "$list"
        return 0
    fi

    ssh_ensure
    guard_target_volumes
    mount_vol "$T_MNT_SYSTEM_B" "$OV_MNT"
    while IFS="$(printf '\t')" read -r -u 3 layer role id feat; do
        [ "$layer" = "rootfs" ] || continue
        case "$role" in overlay|finalize) ;; *) continue ;; esac
        case "$id" in
            rootfs.finish)    saw_finish=1 ;;
            rootfs.set_roles) saw_roles=1 ;;
        esac
        if [ "$(patch_manual "$id")" = "1" ] && [ "${OPT_ALLOW_MANUAL:-0}" -ne 1 ]; then
            warn "跳过 manual patch ${id}（加 --allow-manual 才执行）；$(patch_note "$id")"
            skipped=$(( skipped + 1 ))
            continue
        fi
        note="$(patch_note "$id")"
        log "→ $id  (${note})"
        patch_apply "$id"
        ran=$(( ran + 1 ))
    done 3<"$list"
    sync_dev
    donor_umount_all

    # 完整性闸门：之前这里被 ssh 吃掉 stdin，只跑了前两个补丁却报"完成"，
    # 导致 rootfs 没有 finish/set_roles（开机即挂）。
    [ $(( ran + skipped )) -eq "$expected" ] ||
        die "rootfs 补丁只执行了 $((ran + skipped))/${expected} 个（stdin 被吃掉或中途退出），rootfs 不完整；请重跑 patch-rootfs"
    if awk -F'\t' '$3 == "rootfs.finish" { f = 1 } END { exit !f }' "$list"; then
        [ "$saw_finish" -eq 1 ] || die "rootfs.finish 未执行，rootfs 不完整"
    fi
    if awk -F'\t' '$3 == "rootfs.set_roles" { f = 1 } END { exit !f }' "$list"; then
        [ "$saw_roles" -eq 1 ] || die "rootfs.set_roles 未执行，role 未设置"
    fi

    # 记录安装时的选挂，供 build 对比（避免构建组件与已安装 rootfs 不一致却无提示）
    selection_ids "$list" >"$WK/install-selection.txt"
    cp "$list" "$WK/patches-install.txt" 2>/dev/null || true
    ok "rootfs 补丁完成，共 ${ran} 个（跳过 manual ${skipped} 个，共 ${expected} 个）。"
    return 0
}

# ---------- fix-icons（已安装系统的主屏图标修复）----------
# iOS13 的 Mail/Maps/Notes 等系统 App 在 DataB 的 /staged_system_apps 下，
# 安装时用的是 j96(iPad) 的 Assets.car（只有 pad idiom），iPhone 会显示白色占位图标。
# 这里只替换这些 App 的图标资源 + 清 SpringBoard 图标缓存，不动用户数据。
stage_fix_icons() {
    local donor j96 list tmp n
    if dry; then log "dry-run：跳过图标修复。"; return 0; fi
    ssh_ensure
    donor="$(donor_mount "$T_UI_DONOR")" || die "挂载 UI donor 失败: $T_UI_DONOR"
    j96="$(donor_mount j96)" || die "挂载 j96 rootfs 失败"
    list="$WK/phone-ui/fix-staged-icons.txt"
    tmp="$WK/phone-ui/fix-staged-icons.deploy.txt"
    ov_icon_scan_root "$donor" "$j96" "private/var/staged_system_apps" "$list"
    [ -s "$list" ] || die "没有找到需要修复的 staged App 图标（UI donor 不匹配？）"
    sed 's#^private/var/##' "$list" >"$tmp"

    mount_vol "$T_MNT_SYSTEM12" /mnt1 ro
    ensure_sep
    mount_data_b
    ssh_run "test -d /mnt7/staged_system_apps" >/dev/null 2>&1 ||
        die "DataB 上没有 /staged_system_apps（系统尚未完成安装？）"

    n="$(wc -l <"$tmp" | tr -d ' ')"
    log "更新 ${n} 份 staged App 图标（来自 ${T_UI_DONOR}）…"
    # iOS 可能给 staged App 加 uchg/schg（或 DataB 挂载为只读），先清掉再写
    ssh_run "/usr/bin/chflags -R nouchg /mnt7/staged_system_apps" >/dev/null 2>&1 || true
    ssh_run "/usr/bin/chflags -R noschg /mnt7/staged_system_apps" >/dev/null 2>&1 || true

    # 先确认目标目录能"新建并写入"文件：iOS 首次启动后给 staged App 加的保护
    # 会同时禁止覆盖和新建，这种情况下 ramdisk 改不了，只能重建 DataB 让 finish 写入。
    tdir="/mnt7/$(dirname "$(head -1 "$tmp")")"
    if ! ssh_run "echo x > \"$tdir/.n61-wt\" && rm -f \"$tdir/.n61-wt\"" >/dev/null 2>&1; then
        warn "目标目录属性: $(ssh_run "ls -ldO \"$tdir\"" 2>&1 || true)"
        die "DataB 上的 staged App 目录不允许新建/写入文件（iOS 数据保护）。
  ramdisk 下无法覆盖这类文件，请改用重建 DataB（图标会在 finish 时写入）：
    sudo ./n61.sh -y volumes --recreate-data
    sudo ./n61.sh -y patch-rootfs
  （DataB 上的数据/激活记录会丢）
  之后 DFU 重新 boot。"
    fi

    # DataB 上的 staged App 受 iOS 数据保护时，"覆盖写"会 EPERM，但 unlink 通常仍允许。
    # 先把旧的 Assets.car 删掉，再让 tar 新建，绕过“覆盖已保护文件”的限制。
    if ! ssh_run "cd /mnt7 && while IFS= read -r f; do /bin/rm -f \"\$f\" || exit 1; done" <"$tmp"; then
        warn "目标文件属性: $(ssh_run "ls -ldO \"/mnt7/$(head -1 "$tmp")\"" 2>&1 || true)"
        die "无法删除旧 staged 图标（文件受 flags/数据保护）。
  可改用重建 DataB（在保护生效前写入）：
    sudo ./n61.sh -y volumes --recreate-data
    sudo ./n61.sh -y patch-rootfs
  （DataB 上的数据/激活记录会丢失）"
    fi
    log "  已删除旧图标，重新写入…"

    deploy_relative_paths "$donor/private/var" "/mnt7" "$tmp"

    log "清理 SpringBoard 图标缓存…"
    rsh "/bin/rm -rf /mnt7/mobile/Library/Caches/com.apple.IconsCache"
    sync_dev
    umount_vols /mnt1 /mnt7
    ok "图标修复完成（${n} 个文件）。重新 boot 进 iOS13 后生效。"
}

# Stock Data is role 0x40. T_MNT_DATA_STOCK is used only when that slot really
# has the Data role; otherwise the single other Data-role volume is selected.
resolve_stock_data_slot() {
    local configured="${T_MNT_DATA_STOCK:-}" role found="" i
    apfs_volumes || die "无法读取 APFS 卷，不能定位 stock Data"
    if [ -n "$configured" ] && [ -n "${APFS_V_BLOCK[$configured]:-}" ]; then
        role="$(rd_u16 "${APFS_V_BLOCK[$configured]}" "$APFS_ROLE_OFF")"
        if [ "$role" = "64" ] && [ "$configured" != "${T_MNT_DATA_B:-}" ]; then
            printf '%s\n' "$configured"
            return 0
        fi
    fi
    for i in "${APFS_V_IDX[@]}"; do
        [ "$i" = "${T_MNT_DATA_B:-}" ] && continue
        role="$(rd_u16 "${APFS_V_BLOCK[$i]}" "$APFS_ROLE_OFF")"
        if [ "$role" = "64" ]; then
            [ -z "$found" ] || die "不止一个 stock Data 卷（role 0x40）：s${found} 和 s${i}"
            found="$i"
        fi
    done
    [ -n "$found" ] || die "没有 role 0x40 的 stock Data 卷，拒绝把 keybag 拷到错误的卷"
    log "stock Data 卷是 disk0s1s${found}（role 0x40），不是配置的 s${configured:-?}" >&2
    printf '%s\n' "$found"
}

# ---------- finish（rootfs.finish）----------
stage_finish() {
    local apfs14 encrypted=0 keybags
    if [ -n "${T_DONOR_APFS14:-}" ]; then
        apfs14="$(donor_dir)/$T_DONOR_APFS14"
        [ -d "$apfs14" ] || die "缺少 iOS14 fsck 资产: $apfs14"
    fi
    data_is_encrypted && encrypted=1

    guard_target_volumes
    mount_vol "$T_MNT_SYSTEM_B" /mnt6
    mount_vol "$T_MNT_SYSTEM12" /mnt1 ro
    if [ "$encrypted" -eq 1 ]; then
        ensure_systembag
    else
        mount_vol "$T_MNT_DATA_B" /mnt7
    fi
    # 兜底：确认 /mnt7 真的是 DataB（防止把 var/keybags 写进 ramdisk 根）
    rsh "/sbin/mount | /usr/bin/grep -q \"/dev/disk0s1s${T_MNT_DATA_B} on /mnt7\"" ||
        die "DataB 未挂载到 /mnt7，拒绝写入 private/var/keybags"

    log "private/var → DataB…"
    rsh "/bin/cp -a /mnt6/private/var/. /mnt7/"
    # 清掉旧的 SpringBoard 图标缓存（staged App 图标来自 UI donor）
    rsh "/bin/rm -rf /mnt7/mobile/Library/Caches/com.apple.IconsCache"

    log "xarts / preboot 目录…"
    rsh "mkdir -p /mnt6/private/xarts /mnt6/private/preboot && chmod 755 /mnt6/private/xarts /mnt6/private/preboot"

    if [ -n "${T_DONOR_APFS14:-}" ]; then
        log "换 iOS14 fsck + apfs.fs（原文件改名 *Backup）…"
        rsh "cd /mnt6 && { [ -e sbin/fsckBackup ] || mv sbin/fsck sbin/fsckBackup; } &&
            { [ -e System/Library/Filesystems/apfs.fsBackup ] ||
              mv System/Library/Filesystems/apfs.fs System/Library/Filesystems/apfs.fsBackup; }"
        COPYFILE_DISABLE=1 /usr/bin/tar --uid 0 --gid 0 --no-xattrs -cf - \
            -C "$apfs14" ./sbin/fsck ./System/Library/Filesystems/apfs.fs |
            ssh_run "/usr/bin/tar --numeric-owner -xpf - -C /mnt6 &&
                chmod 755 /mnt6/System/Library/Filesystems/apfs.fs" ||
            die "fsck/apfs.fs 替换失败"

    fi

    log "SEP 固件（来自 iOS12 s1）…"
    # iOS15 的 SEP 固件在 Preboot（<hash>/usr/standalone/firmware），旧系统才在 s1。
    rsh "mkdir -p /mnt6/usr/standalone/firmware"
    if ! ssh_run "test -f /mnt1/usr/standalone/firmware/sep-firmware.img4" >/dev/null 2>&1; then
        preboot_mount
        rsh "f=\$(ls ${PREBOOT_MNT:-/mnt8}/*/usr/standalone/firmware/sep-firmware.img4 2>/dev/null | head -1); [ -n \"\$f\" ] || exit 1; /bin/cp -a \"\$f\" /mnt6/usr/standalone/firmware/" ||
            die "找不到本机 sep-firmware.img4（s1 与 Preboot 都没有）"
    else
        rsh "/bin/cp -a /mnt1/usr/standalone/firmware/sep-firmware.img4 /mnt6/usr/standalone/firmware/"
    fi

    if [ "$encrypted" -eq 1 ]; then
        log "保留本次为加密 DataB 创建的 systembag…"
        ssh_run "test -s /mnt7/keybags/systembag.kb" ||
            die "systembag 缺失：加密 DataB 需要 SEP 正常加载并成功执行 create_systembag（检查上面的 SEP/ensure_systembag 输出）"
    else
        keybags="$(state_dir)/keybags.tar"
        if [ -f "$keybags" ]; then
            log "keybags → DataB…"
            ssh_run "/usr/bin/tar --numeric-owner -xpf - -C /mnt7" <"$keybags" ||
                die "keybags 写入失败"
        elif stock_data="$(resolve_stock_data_slot)"; then
            # The stock systembag is wrapped by this device's effaceable key, so
            # copying it read-only from the stock Data volume needs no ramdisk SEP.
            # resolve_stock_data_slot uses APFS role 0x40 when s2 is not Data.
            log "Copying stock keybags from disk0s1s${stock_data} → DataB…"
            rsh "/sbin/mount | /usr/bin/grep -q ' on /mnt2 ' && ! /sbin/mount | /usr/bin/grep -q 'disk0s1s${stock_data} on /mnt2 ' && exit 1;
                /sbin/mount | /usr/bin/grep -q 'disk0s1s${stock_data} on /mnt2 ' || /sbin/mount_apfs -o ro /dev/disk0s1s${stock_data} /mnt2" ||
                die "Cannot mount the stock Data volume read-only at /mnt2"
            rsh "test -s /mnt2/keybags/systembag.kb" ||
                { umount_vols /mnt2; die "Stock systembag not found at /mnt2/keybags/systembag.kb"; }
            rsh "mkdir -p /mnt7/keybags && /bin/cp -a /mnt2/keybags/. /mnt7/keybags/ && test -s /mnt7/keybags/systembag.kb" ||
                { umount_vols /mnt2; die "Stock keybag copy failed"; }
            umount_vols /mnt2
        else
            die "缺少 ${keybags}（--data-protection none 时需自备越狱 iOS12 的 keybags）"
        fi
    fi

    log "$(rsh "ls -ln /mnt6/usr/standalone/firmware/sep-firmware.img4 /mnt7/keybags/systembag.kb")"
    sync_dev
    umount_vols /mnt1 /mnt7 /mnt6
    log "finish 完成，卷已卸载。"
}

# ---------- set_roles（rootfs.set_roles，必须最后一步、卷未挂载）----------
stage_set_roles() {
    apfs_set_roles "$T_ROLES" 1
}

# ---------- build（Phase C）----------

# iBSS：iOS12 已打过 n61 补丁的 iBSS，直接签 ibss
build_ibss() {   # out
    local src im4m
    src="$(derived_path ibss_src)" || die "缺少派生输入 ibss_src"
    im4m="$(im4m_path)" || die "缺少 IM4M（先跑 ./n61.sh extract-im4m）"
    "$(tool img4)" -i "$src" -A -T ibss -M "$im4m" -o "$1" >/dev/null ||
        die "iBSS 签名失败"
}

# iBEC：iBoot64Patcher 注入 boot-args → kairos 让 iBoot 认 rd= 根分区 → 签 ibec
build_ibec() {   # out
    local patched="$WK/iBEC.patched" kaired="$WK/iBEC.kairos" src im4m
    src="$(derived_path ibec_dec)" || die "缺少派生输入 ibec_dec"
    im4m="$(im4m_path)" || die "缺少 IM4M（先跑 ./n61.sh extract-im4m）"
    "$(tool iBoot64Patcher)" "$src" "$patched" -b "$T_BOOTARGS" -n >/dev/null 2>&1 ||
        die "iBoot64Patcher 失败"
    "$(tool kairos)" "$patched" "$kaired" -d "$T_ROOT_PART" >/dev/null 2>&1 ||
        die "kairos 失败"
    "$(tool img4)" -i "$kaired" -A -T ibec -M "$im4m" -o "$1" >/dev/null ||
        die "iBEC 签名失败"
}

# Reuse the complete personalized IMG4 captured from the stock Preboot volume.
build_sep() {   # out
    local source="$REPO/../state/sep-firmware.img4" im4m tmp
    [ "${T_NAME:-}" = "n69-ios16" ] || die "当前 target 未声明 SEP 来源"
    [ -s "$source" ] || die "Missing captured Preboot SEP: run ./n61.sh capture-sep --target n69-ios16"
    im4m="$(im4m_path)" || die "Missing captured IM4M"
    python3 "$REPO/../tools/sep_image.py" validate --image "$source" --expect-ticket "$im4m" || die "Captured SEP and IM4M do not match"
    tmp="$1.tmp.$$"
    (umask 077; cp "$source" "$tmp") || die "Cannot copy captured SEP"
    cmp -s "$source" "$tmp" || { rm -f "$tmp"; die "Captured SEP changed during copy"; }
    mv -f "$tmp" "$1" || { rm -f "$tmp"; die "Cannot publish SEP image"; }
}

# ramdisk 启动时让 iBoot 加载 SEP（rsepfirmware）。
# 原因：A9 上在 ramdisk 里执行 seputil --load 会让内核卡死（SSHRD_Script #319），
# 而 iBoot 在 bootx 之前加载 SEP 是 restore/正常启动走的原生路径。
# N61_RAMDISK_SEP: auto（默认，存在已捕获 SEP 时加载）| 1（必须带）| 0（不带）| /path/to/sep.img4
prepare_ramdisk_sep() {
    local mode="${N61_RAMDISK_SEP:-auto}" out im4m
    RAMDISK_SEP=""
    case "$mode" in
        0|no|off|none)
            log "N61_RAMDISK_SEP=0：本次 ramdisk 不带 SEP。"
            return 0 ;;
        auto|1|yes|on) ;;
        *)
            [ -s "$mode" ] || die "N61_RAMDISK_SEP 指向的文件不存在或为空: $mode"
            RAMDISK_SEP="$mode"
            log "ramdisk SEP（手动指定）: $RAMDISK_SEP"
            return 0 ;;
    esac
    if [ "${T_NAME:-}" != "n69-ios16" ]; then
        case "$mode" in 1|yes|on) die "当前 target 没有 SEP 来源，无法让 ramdisk 带 SEP" ;; esac
        return 0
    fi
    if [ ! -s "$REPO/../state/sep-firmware.img4" ]; then
        case "$mode" in
            1|yes|on) die "No captured SEP: boot without SEP and run ./n61.sh capture-sep --target n69-ios16" ;;
        esac
        warn "No captured Preboot SEP; starting ramdisk without SEP. Run capture-sep before the next boot."
        return 0
    fi
    out="$WK/ramdisk_SEP.img4"
    log "Reusing complete captured Preboot SEP for ramdisk…"
    build_sep "$out"
    RAMDISK_SEP="$out"
}

# KernelCache：基底 → 变换链 → mkcomplzss + watchtower → 签 rkrn（含 roundtrip 校验）
build_kc() {   # out list
    local out="$1" list="$2" id base
    base="$(awk -F'\t' '$1 == "kc" && $2 == "base" { print $3; exit }' "$list")"
    [ "$base" = "kc.j96_clean" ] ||
        warn "kc base 为 ${base:-无}，bash 版构建链按 kc.j96_clean 实现，产物可能不符合预期"
    kc_base
    for id in $(awk -F'\t' '$1 == "kc" && $2 == "transform" { print $3 }' "$list"); do
        log "  kc transform: $id"
        kc_transform "$id" "$KC_ART" "$WK/$(kc_out_name "$id")"
        KC_ART="$WK/$(kc_out_name "$id")"
    done
    kc_pack "$KC_ART" "$out"
}

stage_build() {
    local list sig n
    list="$(patch_selection_file)" || die "无法创建补丁清单文件"
    select_patches "$OPT_FEATURES" "$OPT_PATCHES" "$OPT_EXCLUDE" \
        "$T_BASE_DT" "$T_BASE_KC" "${OPT_PATCHES_SET:-0}" "${T_PATCHES:-}" >"$list" ||
        { rm -f "$list"; die "补丁选择失败"; }

    if dry; then
        log "dry-run：选挂已校验（$(wc -l <"$list" | tr -d ' ') 项），跳过构建。"
        [ "$list" = "$WK/patches-selected.txt" ] || rm -f "$list"
        return 0
    fi

    preflight_build_inputs
    awk -F'\t' '$1 == "kc" && $2 == "base"' "$list" | grep -q . ||
        die "未选中 kc base（清单 bases['kc']）"
    awk -F'\t' '$1 == "dt" && $2 == "base"' "$list" | grep -q . ||
        die "未选中 dt base（清单 bases['dt']）"

    sig="$(selection_ids "$list")"
    if [ -f "$WK/install-selection.txt" ] && [ "$(cat "$WK/install-selection.txt")" != "$sig" ]; then
        warn "本次选挂与上次 patch-rootfs 不同，构建出的启动组件可能与已安装 rootfs 不匹配。"
        warn "  安装时 $(wc -l <"$WK/install-selection.txt" | tr -d ' ') 项；本次 $(printf '%s\n' "$sig" | wc -l | tr -d ' ') 项"
        warn "  安装清单: $WK/install-selection.txt；本次清单: $list"
    fi

    log "构建启动组件 → $WK"

    log "iBSS…"
    build_ibss "$WK/boot_iBSS.img4"

    log "iBEC（boot-args + kairos + 签名）…"
    build_ibec "$WK/boot_iBEC.img4"

    if [ "${T_NAME:-}" = "n69-ios16" ]; then
        log "SEP（N69AP iOS 15.8.8）…"
        build_sep "$WK/boot_SEP.img4"
    fi

    log "DeviceTree（dt patch 资产 + 签 rdtr）…"
    build_dt "$WK/boot_DT.img4" "$list"

    if [ "${T_FULL_TRUSTCACHE:-0}" = "1" ]; then
        log "TrustCache（完整：原版 TC ∪ 本次新增二进制）…"
        build_tc "$WK/boot_TC.img4"
    else
        local tc im4m
        tc="$(derived_path trustcache)" || die "缺少派生输入 trustcache"
        im4m="$(im4m_path)" || die "缺少 IM4M（先跑 ./n61.sh extract-im4m）"
        log "TrustCache（原版静态 TC 直接签 rtsc）…"
        "$(tool img4)" -i "$tc" -A -T rtsc -M "$im4m" \
            -o "$WK/boot_TC.img4" >/dev/null || die "TrustCache 签名失败"
    fi

    log "KernelCache（基底 + 变换 + mkcomplzss + watchtower + 签 rkrn）…"
    build_kc "$WK/boot_KC.img4" "$list"

    log "已构建的组件："
    for n in iBSS iBEC DT TC KC; do
        printf '  boot_%s.img4  %s\n' "$n" \
            "$([ -f "$WK/boot_$n.img4" ] && bin_len "$WK/boot_$n.img4" || echo 缺)"
    done
    return 0
}

# ---------- install ----------
stage_install() {
    # 独立跑 install 时也要在写盘前检查（Health 缓存/donor/APFS14）
    if ! dry; then
        preflight_install_inputs
    fi
    stage_volumes || return 1
    stage_rootfs || return 1
    stage_cryptex || return 1
    stage_patch_rootfs || return 1
    return 0
}

# ---------- boot ----------
stage_boot() {
    local n missing=0
    if dry; then log "dry-run：跳过设备发送。"; return 0; fi
    for n in iBSS iBEC DT TC KC; do
        [ -f "$WK/boot_$n.img4" ] || missing=1
    done
    if [ "${T_NAME:-}" = "n69-ios16" ]; then
        build_sep "$WK/boot_SEP.img4"
    fi
    if [ "$missing" -eq 1 ]; then
        log "缺少启动组件，先构建…"
        stage_build || return 1
    fi
    send_boot_chain
}

# ---------- all ----------
stage_all() {
    if ! dry; then
        preflight_install_inputs
        preflight_build_inputs
        preflight_ramdisk_assets
    fi
    stage_ramdisk || return 1
    stage_extract_im4m || return 1
    stage_install || return 1
    stage_build || return 1
    stage_boot || return 1
    return 0
}
