# shellcheck shell=bash
# iOS 16 SystemOS Cryptex 安装 + Preboot 兼容链接。
#
# iOS 16 的 dyld shared cache、系统库、大量 /usr 内容不在 rootfs（OS dmg）里，
# 而在单独的 Cryptex1,SystemOS 镜像里。没有它 launchd 一启动就会因缺少
# dyld cache 而死。做法与 SevenUP 的 install_cryptex/install_preboot 一致：
#   1) 把 Cryptex 的 System/usr/Library 内容铺到系统卷根（/mnt6）；
#   2) 在 Preboot 卷的 Cryptexes/OS 下建指回根路径的符号链接。

# SSH ramdisk's root is read-only; /mnt8 already exists and is distinct from
# SystemB (/mnt6) and DataB (/mnt7).
PREBOOT_MNT="/mnt8"

# cryptex_dmg → 本地 dmg 路径（优先 ipsw/cache，否则从 IPSW 解压）
cryptex_dmg() {
    local entry="${T_CRYPTEX_ENTRY_J96:-}" ipsw="${T_IPSW_J96:-}" cache
    [ -n "$entry" ] && [ -n "$ipsw" ] || die "清单缺少 T_CRYPTEX_ENTRY_J96 / T_IPSW_J96"
    cache="$REPO/ipsw/cache/$entry"
    [ -f "$cache" ] && { printf '%s\n' "$cache"; return 0; }
    [ -f "$REPO/ipsw/$ipsw" ] || die "找不到 IPSW: ipsw/$ipsw"
    log "从 IPSW 提取 Cryptex ${entry}…" >&2
    mkdir -p "$REPO/ipsw/cache"
    ( cd "$REPO/ipsw/cache" && unzip -o -q "../$ipsw" "$entry" ) || die "Cryptex 提取失败: $entry"
    [ -f "$cache" ] || die "IPSW 中没有 $entry"
    printf '%s\n' "$cache"
}

# cryptex_mount → 挂载点（只读，owners on）
cryptex_mount() {
    local mnt dmg
    mnt="$(donor_mounts_dir)/cryptex"
    if /sbin/mount | grep -q " on $mnt "; then
        printf '%s\n' "$mnt"; return 0
    fi
    dmg="$(cryptex_dmg)" || return 1
    mkdir -p "$mnt"
    hdiutil attach -readonly -owners on -nobrowse -mountpoint "$mnt" "$dmg" >/dev/null ||
        die "Cryptex DMG 挂载失败: $dmg"
    if /sbin/mount | grep -q " on $mnt .*noowners"; then
        hdiutil detach "$mnt" >/dev/null 2>&1 || true
        die "Cryptex 未以 owners 模式挂载（需 sudo 运行）"
    fi
    if [ ! -d "$mnt/System" ] && [ ! -d "$mnt/usr" ]; then
        hdiutil detach "$mnt" >/dev/null 2>&1 || true
        die "Cryptex 镜像布局异常（没有 System 或 usr）: $dmg"
    fi
    printf '%s\n' "$mnt"
}

# 找出 Preboot 卷（role 0x10）所在槽位并挂到 $PREBOOT_MNT
preboot_slot() {
    local i role
    apfs_volumes
    for i in "${APFS_V_IDX[@]}"; do
        role="$(rd_u16 "${APFS_V_BLOCK[$i]}" "$APFS_ROLE_OFF")"
        if [ "$role" = "16" ]; then printf '%s\n' "$i"; return 0; fi
    done
    return 1
}

preboot_mount() {
    local n
    n="$(preboot_slot)" || die "找不到 Preboot 卷（role 0x10）"
    # Do not mount over an unrelated filesystem or create directories on the
    # read-only ramdisk root.
    rsh "test -d $PREBOOT_MNT" || die "Preboot 挂载点不存在: $PREBOOT_MNT"
    rsh "m=\$(/sbin/mount | /usr/bin/grep -F ' on $PREBOOT_MNT ' || true); [ -z \"\$m\" ] || printf '%s\\n' \"\$m\" | /usr/bin/grep -Fq '/dev/disk0s1s${n} on $PREBOOT_MNT '" ||
        die "Preboot 挂载点 $PREBOOT_MNT 已被其他文件系统占用"
    mount_vol "$n" "$PREBOOT_MNT"
}

preboot_unmount() {
    umount_vols "$PREBOOT_MNT"
}

stage_cryptex() {
    local cmnt roots="" d
    if dry; then log "dry-run：跳过 Cryptex 安装。"; return 0; fi
    require_root cryptex
    ssh_ensure
    cmnt="$(cryptex_mount)" || die "Cryptex 挂载失败"
    for d in System usr Library Applications; do
        [ -d "$cmnt/$d" ] && roots="$roots $d"
    done
    [ -n "$roots" ] || die "Cryptex 里没有可铺设的目录"
    guard_target_volumes system
    mount_vol "$T_MNT_SYSTEM_B" /mnt6
    rsh '/sbin/mount | /usr/bin/grep -q "disk0s1s'"$T_MNT_SYSTEM_B"' on /mnt6"' ||
        die "SystemB 未挂载到 /mnt6，拒绝写入 Cryptex"

    # 目标上若某目录是符号链接，tar 无法把目录铺进去：先改名为 .bak（不覆盖已有 .bak）
    log "检查 Cryptex 目标目录中的符号链接…"
    ( cd "$cmnt" && find $roots -type d -print ) | sed 's#^#/mnt6/#' |
        ssh_run 'while IFS= read -r p; do
            if [ -L "$p" ]; then
                if [ -e "$p.bak" ] || [ -L "$p.bak" ]; then echo "无法保留符号链接（.bak 已存在）: $p" >&2; exit 22; fi
                echo "保留符号链接为 .bak: $p"; mv "$p" "$p.bak"
            elif [ -e "$p" ] && [ ! -d "$p" ]; then
                echo "目录被文件占用: $p" >&2; exit 23
            fi
        done' || die "Cryptex 目录预检失败"

    log "流式写入 Cryptex（${roots# }）到 /mnt6 …"
    COPYFILE_DISABLE=1 /usr/bin/tar -cpf - -C "$cmnt" $roots |
        ssh_run "/usr/bin/tar --numeric-owner -xpf - -C /mnt6" ||
        die "Cryptex 流式写入失败"
    rsh "/usr/sbin/chown -R -h root:wheel /mnt6/System /mnt6/usr; sync"
    rsh "test -e /mnt6/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64" ||
        die "Cryptex 写入后缺少 /mnt6/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64（20H392 的 SystemOS 成员是 ${T_CRYPTEX_ENTRY_J96:-087-86624-047.dmg}）。没有 dyld shared cache 时 launchd 起不来。"
    ok "Cryptex 已写入 SystemB。"
    stage_preboot
}

stage_preboot() {
    local hash base name dest
    if dry; then log "dry-run：跳过 Preboot 链接。"; return 0; fi
    ssh_ensure
    mount_vol "$T_MNT_SYSTEM_B" /mnt6
    preboot_mount
    hash="$(rsh "ls -d $PREBOOT_MNT/*/usr/standalone/firmware 2>/dev/null | head -1 | cut -d/ -f3")"
    [ -n "$hash" ] || die "Preboot 里没有 <boot-hash>/usr/standalone/firmware：先让原系统完整启动过一次再重试"
    log "Preboot boot-hash: $hash"
    base="$PREBOOT_MNT/Cryptexes/OS"
    rsh "mkdir -p $base/System $base/usr"
    for name in System/Library System/DriverKit usr/lib usr/libexec usr/bin usr/sbin usr/share; do
        # 只给 rootfs/Cryptex 里真的存在的目录建链接
        ssh_run "test -d /mnt6/$name" >/dev/null 2>&1 || continue
        dest="$base/$name"
        rsh "if [ -e $dest ] && [ ! -L $dest ]; then echo 'Preboot 中已有真实目录: $dest' >&2; exit 21; fi; ln -sfn /$name $dest" ||
            die "Preboot 链接失败: $dest"
    done
    sync_dev
    preboot_unmount
    umount_vols /mnt6
    ok "Preboot Cryptex 兼容链接已建立。"
}
