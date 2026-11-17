# shellcheck shell=bash
# APFS 卷操作：列举 / 建卷 / 改 role（经 ramdisk 的 dd 读写原始块）。
#
# 为什么 role 要事后改：newfs_apfs 建卷时 System/Data 落在 fs 槽位 0/1，
# 这两个槽被 iOS12 卷占用，所以第二系统只能先以 role 0 建出来，最后直接改
# 卷超级块的 apfs_role（偏移 0x3C4）并重算 Fletcher-64。
# 所有读写都是 /dev/rdisk0s1 的 4K 原始块，写前备份、写后读回校验。

APFS_RDEV="/dev/rdisk0s1"
APFS_BS=4096
APFS_ROLE_OFF=0x3C4
APFS_NAME_OFF=0x2C0
APFS_FS_FLAGS_OFF=0x108
APFS_ALLOC_OFF=0x58

# ---------- 原始块读写 ----------
# 块内容按进程缓存（同一阶段里反复读同一块不重复走 USB），
# 不落到工作目录，避免下次运行时读到上一次的过期块。
APFS_CACHE="${TMPDIR:-/tmp}/n61-apfs.$$"
apfs_dir() {
    [ -d "$APFS_CACHE" ] || mkdir -p "$APFS_CACHE"
    printf '%s\n' "$APFS_CACHE"
}

apfs_cleanup() {
    [ -n "$APFS_CACHE" ] && rm -rf "$APFS_CACHE"
    return 0
}

apfs_read_block() {
    local n="$1" f try=0 sz=0 rc=0 err
    f="$(apfs_dir)/$n.bin"
    err="$f.err"
    if [ -f "$f" ]; then
        printf '%s\n' "$f"
        return 0
    fi
    # 实机遇到过偶发 SSH 认证失败（Permission denied）导致单块读失败；
    # 只读的块读取重试 3 次，最终失败就中止，绝不带着空块继续解析。
    while [ "$try" -lt 3 ]; do
        try=$(( try + 1 ))
        rm -f "$f"
        rc=0
        if ssh_run_timeout 20 "dd if=$APFS_RDEV bs=$APFS_BS skip=$n count=1" >"$f" 2>"$err"; then
            sz="$(wc -c <"$f" | tr -d ' ')"
            if [ "$sz" -eq "$APFS_BS" ]; then
                rm -f "$err"
                printf '%s\n' "$f"
                return 0
            fi
        else
            rc=$?
        fi
        sz="$(wc -c <"$f" | tr -d ' ')"
        warn "APFS block $n: SSH exit=$rc, bytes=$sz/$APFS_BS; $(cat "$err")"
        if [ "$try" -lt 3 ]; then
            warn "块 $n 读取失败，重试（${try}/3）"
            # A live ramdisk still rejects the next login until the USB tunnel is new.
            if [ "$rc" -eq 255 ]; then
                proxy_restart
                ssh_ensure || true
            fi
            sleep 1
        fi
    done
    rm -f "$f"
    die "块 $n 读取失败（已重试 3 次；检查 ramdisk SSH 是否稳定）"
}

apfs_read_block_fresh() {
    rm -f "$(apfs_dir)/$1.bin"
    apfs_read_block "$1"
}

apfs_write_block() {   # n file
    local n="$1" file="$2" dev out rc b64 size
    # sshpass reads stdin while it watches for a password prompt, so a raw
    # 4K image piped into `dd` never reaches the disk. The same block reads
    # back because reads do not use stdin. Send the block as base64 instead.
    size="$(wc -c <"$file" | tr -d ' ')"
    [ "$size" -eq "$APFS_BS" ] || die "块 $n 本地镜像不是 ${APFS_BS} 字节（实际 $size）"
    b64="$(base64 <"$file" | tr -d '\n')"
    for dev in "$APFS_RDEV" /dev/disk0s1; do
        rc=0
        out="$(ssh_run "printf '%s' '$b64' | /usr/bin/base64 -D | /bin/dd of=$dev bs=$APFS_BS seek=$n count=1 conv=notrunc 2>&1 && /bin/sync")" || rc=$?
        if [ "$rc" -eq 0 ]; then
            return 0
        fi
        warn "块 $n 写入 $dev 失败: ${out:-ssh rc $rc}"
    done
    die "块 $n 写入失败: ${out:-ssh rc $rc}"
}

# ---------- 字段读取 ----------
rd_u16() { od -An -v -tu2 -j "$2" -N 2 "$1" | tr -d ' \n'; }
rd_u32() { od -An -v -tu4 -j "$2" -N 4 "$1" | tr -d ' \n'; }
rd_u64() {
    local lo hi
    lo="$(rd_u32 "$1" "$2")"
    hi="$(rd_u32 "$1" $(( $2 + 4 )))"
    printf '%s\n' $(( lo + hi * 4294967296 ))
}

magic_is() {   # file off "MAGIC"
    [ "$(dd if="$1" bs=1 skip="$2" count=4 2>/dev/null)" = "$3" ]
}

# ---------- Fletcher-64 ----------
apfs_fletcher_raw() {
    od -An -v -tu4 -j 8 "$1" | awk '
        { for (i = 1; i <= NF; i++) { s1 = (s1 + $i) % 4294967295; s2 = (s2 + s1) % 4294967295 } }
        END {
            lo = 4294967295 - ((s1 + s2) % 4294967295)
            hi = 4294967295 - ((s1 + lo) % 4294967295)
            printf "%d %d\n", hi, lo
        }'
}

apfs_ck_bytes() {   # hi lo → 8 字节（小端）到 stdout
    local hi="$1" lo="$2" hex i
    hex="$(printf '%08x%08x' "$hi" "$lo")"
    for i in 14 12 10 8 6 4 2 0; do
        printf "\\x${hex:$i:2}"
    done
}

apfs_checksum_ok() {
    local hi lo ck
    read -r hi lo <<EOF
$(apfs_fletcher_raw "$1")
EOF
    ck="$(apfs_dir)/.cksum"
    apfs_ck_bytes "$hi" "$lo" >"$ck"
    head -c 8 "$1" | cmp -s - "$ck"
}

apfs_seal() {   # 原地写入正确的校验和
    local f="$1" hi lo tmp
    read -r hi lo <<EOF
$(apfs_fletcher_raw "$f")
EOF
    tmp="$f.ck"
    apfs_ck_bytes "$hi" "$lo" >"$tmp"
    dd if="$tmp" of="$f" bs=1 count=8 conv=notrunc 2>/dev/null
    rm -f "$tmp"
}

# ---------- 容器/卷遍历 ----------
apfs_latest_nxsb() {
    local blk0 xp_blocks xp_base i b best best_xid xid batch sz rc
    blk0="$(apfs_read_block 0)" || die "读取块 0 失败"
    magic_is "$blk0" 32 NXSB || die "块 0 不是 APFS 容器超级块"
    xp_blocks=$(( $(rd_u32 "$blk0" 0x68) & 0x7fffffff ))
    xp_base="$(rd_u64 "$blk0" 0x70)"
    # Read the checkpoint ring over one SSH connection instead of authenticating
    # once per block. Keep the allocation bounded for unexpected container layouts.
    if [ "$xp_blocks" -gt 0 ] && [ "$xp_blocks" -le 4096 ]; then
        batch="$(apfs_dir)/checkpoints.bin"
        rc=0
        ssh_run_timeout 60 "dd if=$APFS_RDEV bs=$APFS_BS skip=$xp_base count=$xp_blocks" \
            >"$batch" 2>"$batch.err" || rc=$?
        sz="$(wc -c <"$batch" | tr -d ' ')"
        if [ "$rc" -ne 0 ] || [ "$sz" -ne $(( xp_blocks * APFS_BS )) ]; then
            die "APFS checkpoint read failed: SSH exit=$rc, bytes=$sz/$(( xp_blocks * APFS_BS )).
$(cat "$batch.err")"
        fi
        i=0
        while [ "$i" -lt "$xp_blocks" ]; do
            dd if="$batch" of="$(apfs_dir)/$(( xp_base + i )).bin" \
                bs="$APFS_BS" skip="$i" count=1 2>/dev/null || die "无法缓存 checkpoint 块"
            i=$(( i + 1 ))
        done
        rm -f "$batch" "$batch.err"
    fi
    best="$blk0"
    best_xid="$(rd_u64 "$blk0" 16)"
    i=0
    while [ "$i" -lt "$xp_blocks" ]; do
        b="$(apfs_read_block $(( xp_base + i )))" || die "checkpoint 块 $(( xp_base + i )) 读取失败"
        if magic_is "$b" 32 NXSB && apfs_checksum_ok "$b"; then
            xid="$(rd_u64 "$b" 16)"
            if [ "$xid" -gt "$best_xid" ]; then
                best="$b"
                best_xid="$xid"
            fi
        fi
        i=$(( i + 1 ))
    done
    printf '%s\n' "$best"
}

apfs_omap_lookup() {   # tree toc keystart valend nkeys oid → paddr
    local tree="$1" toc="$2" ks="$3" ve="$4" nkeys="$5" oid="$6"
    local i ko vo o paddr xid best_xid=""
    i=0
    while [ "$i" -lt "$nkeys" ]; do
        ko=$(rd_u16 "$tree" $(( toc + i * 4 )))
        vo=$(rd_u16 "$tree" $(( toc + i * 4 + 2 )))
        o="$(rd_u64 "$tree" $(( ks + ko )))"
        if [ "$o" = "$oid" ]; then
            xid="$(rd_u64 "$tree" $(( ks + ko + 8 )))"
            if [ -z "$best_xid" ] || [ "$xid" -gt "$best_xid" ]; then
                best_xid="$xid"
                paddr="$(rd_u64 "$tree" $(( ve - vo + 8 )))"
            fi
        fi
        i=$(( i + 1 ))
    done
    [ -n "${paddr:-}" ] && printf '%s\n' "$paddr"
}

# 填充 APFS_V_IDX / APFS_V_PADDR / APFS_V_BLOCK（按 fs 槽位顺序）
apfs_volumes() {
    APFS_V_IDX=()
    APFS_V_PADDR=()
    APFS_V_BLOCK=()
    local nx omap tree max_fs i oid idx=0 paddr blk
    local flags level nkeys toff tlen toc keystart valend
    nx="$(apfs_latest_nxsb)" || die "读取 NXSB 失败"
    omap="$(apfs_read_block "$(rd_u64 "$nx" 0xA0)")" || die "读取 omap 块失败"
    tree="$(apfs_read_block "$(rd_u64 "$omap" 0x30)")" || die "读取 omap 树块失败"
    flags=$(rd_u16 "$tree" 32)
    level=$(rd_u16 "$tree" 34)
    nkeys=$(rd_u32 "$tree" 36)
    [ "$level" = "0" ] || die "omap 树不是单叶，解析器需扩展"
    toff=$(rd_u16 "$tree" 40)
    tlen=$(rd_u16 "$tree" 42)
    toc=$(( 56 + toff ))
    keystart=$(( 56 + toff + tlen ))
    valend=$APFS_BS
    [ $(( flags & 1 )) -ne 0 ] && valend=$(( APFS_BS - 40 ))
    max_fs=$(rd_u32 "$nx" 0xB4)
    i=0
    while [ "$i" -lt "$max_fs" ]; do
        oid="$(rd_u64 "$nx" $(( 0xB8 + i * 8 )))"
        if [ "$oid" != "0" ]; then
            idx=$(( idx + 1 ))
            paddr="$(apfs_omap_lookup "$tree" "$toc" "$keystart" "$valend" "$nkeys" "$oid")"
            if [ -n "$paddr" ]; then
                blk="$(apfs_read_block "$paddr")" || die "s$idx: 读取卷超级块失败"
                magic_is "$blk" 32 APSB || die "s$idx: 块 $paddr 不是卷超级块"
                APFS_V_IDX[idx]="$idx"
                APFS_V_PADDR[idx]="$paddr"
                APFS_V_BLOCK[idx]="$blk"
            fi
        fi
        i=$(( i + 1 ))
    done
}

human_size() {
    awk -v n="$1" 'BEGIN {
        split("B KiB MiB GiB TiB", u, " "); f = n
        for (i = 1; i <= 5; i++) {
            if (f < 1024 || i == 5) { if (i == 1) printf "%.0f%s", f, u[i]; else printf "%.2f%s", f, u[i]; exit }
            f /= 1024
        }
    }'
}

apfs_role_name() {   # 入参为十进制 role
    case "$1" in
        0) echo "None" ;; 1) echo "System" ;; 2) echo "User" ;;
        4) echo "Recovery" ;; 8) echo "VM" ;; 16) echo "Preboot" ;;
        32) echo "Installer" ;; 64) echo "Data" ;; 128) echo "Baseband" ;;
        192) echo "Update" ;; 256) echo "xART" ;; 320) echo "Hardware" ;;
        *) echo "?" ;;
    esac
}

apfs_describe() {   # idx blockfile paddr
    local idx="$1" blk="$2" paddr="$3" role name flags used ck enc
    role="$(rd_u16 "$blk" $APFS_ROLE_OFF)"
    name="$(apfs_block_name "$blk")"
    flags="$(rd_u64 "$blk" $APFS_FS_FLAGS_OFF)"
    used=$(( $(rd_u64 "$blk" $APFS_ALLOC_OFF) * APFS_BS ))
    if [ $(( flags & 1 )) -ne 0 ]; then enc="UNENCRYPTED"; else enc="encrypted"; fi
    if apfs_checksum_ok "$blk"; then ck="ok"; else ck="BAD"; fi
    printf 's%s  slot=%s  %-16s role=0x%x (%-8s)  %-11s  已用 %10s  block=%s  cksum=%s\n' \
        "$idx" $(( idx - 1 )) "$name" "$role" "$(apfs_role_name "$role")" "$enc" \
        "$(human_size "$used")" "$paddr" "$ck"
}

apfs_list() {
    local i total blk_size blk_count used_sum=0
    apfs_volumes
    local nxsb
    nxsb="$(apfs_latest_nxsb)"
    blk_size=$(rd_u32 "$nxsb" 0x24)
    [ "$blk_size" = "0" ] && blk_size=$APFS_BS
    blk_count="$(rd_u64 "$nxsb" 0x28)"
    total=$(( blk_count * blk_size ))
    for i in "${APFS_V_IDX[@]}"; do
        used_sum=$(( used_sum + $(rd_u64 "${APFS_V_BLOCK[$i]}" $APFS_ALLOC_OFF) * APFS_BS ))
    done
    log "容器 disk0s1：容量 $(human_size "$total")，各卷已用合计 $(human_size "$used_sum")（APFS 空间共享）"
    for i in "${APFS_V_IDX[@]}"; do
        log "$(apfs_describe "$i" "${APFS_V_BLOCK[$i]}" "${APFS_V_PADDR[$i]}")"
    done
}

# ---------- 建卷 ----------
apfs_create_volumes() {   # names encrypt(0/1)
    local names="$1" encrypt="$2" s name flag want_last
    s="$(slices)" || die "无法读取 APFS 卷列表；已中止建卷。"
    case " $s " in *"disk0s1s${T_MNT_DATA_B} "*)
        log "s${T_MNT_SYSTEM_B}/s${T_MNT_DATA_B} 已存在，跳过: $s"
        return 0
        ;;
    esac
    case " $s " in *"disk0s1s${T_MNT_SYSTEM_B} "*)
        warn "s${T_MNT_SYSTEM_B} 已存在，只补建 DataB: $s"
        names="${names#* }"
        ;;
    esac
    # newfs_apfs 只会把新卷追加到下一个空闲槽位：必须正好落在 清单里的 SystemB/DataB 槽位，
    # 否则后面会把别的卷（Update/xART/Preboot…）当成目标并清空。
    want_last=$(( T_MNT_SYSTEM_B - 1 ))
    if ! printf '%s' "$names" | grep -q "SystemB"; then want_last=$(( T_MNT_DATA_B - 1 )); fi
    case " $s " in
        *"disk0s1s$(( want_last + 1 )) "*) die "s$(( want_last + 1 )) 已被占用，新卷不会落在清单槽位：$s" ;;
    esac
    case " $s " in
        *"disk0s1s${want_last} "*) ;;
        *) die "清单要求新卷从 s$(( want_last + 1 )) 开始，但 s${want_last} 不存在：${s}（检查 T_MNT_SYSTEM_B/T_MNT_DATA_B）" ;;
    esac
    if [ "$encrypt" -eq 1 ]; then
        # 未加载 SEP 时 -P 建卷会一直卡住并锁死容器
        mount_vol "$T_MNT_SYSTEM12" /mnt1 ro
        ensure_sep
        # SEP is loaded into the device now. Unmount the source and Preboot
        # before changing the APFS container; keeping SystemA mounted while
        # newfs_apfs initializes protected DataB can leave the device wedged.
        local umount_rc mount_state mounted_apfs
        umount_rc=0
        ssh_run "/sbin/umount /mnt1" || umount_rc=$?
        if [ "$umount_rc" -ne 0 ]; then
            warn "普通卸载 SystemA (/mnt1) 失败（rc=${umount_rc}），重新连接并检查挂载状态…"
            # An SSH reset may happen after the remote umount has already run.
            # Reconnect and inspect the device before deciding whether a force
            # unmount is needed. If SSH cannot recover, stop before formatting.
            ssh_ensure || die "卸载 SystemA 后 SSH 未恢复；为避免格式化时锁住 APFS 容器，已中止。"
        fi
        mount_state="$(ssh_run "/sbin/mount" 2>/dev/null)" || {
            warn "读取设备挂载状态时 SSH 中断，尝试重新连接…"
            ssh_ensure || die "无法重新连接设备读取挂载状态；已中止格式化。"
            mount_state="$(ssh_run "/sbin/mount")" ||
                die "无法读取设备挂载状态；为避免格式化时锁住 APFS 容器，已中止。"
        }
        if printf '%s\n' "$mount_state" | grep -Fq "/dev/disk0s1s${T_MNT_SYSTEM12} on /mnt1 "; then
            warn "SystemA 仍挂载，尝试强制卸载只读卷…"
            # SystemA is mounted read-only above. Darwin's -f detaches a busy
            # read-only mount; the checks below still block formatting if it fails.
            ssh_run "/sbin/umount -f /mnt1" >/dev/null 2>&1 || true
            mount_state="$(ssh_run "/sbin/mount" 2>/dev/null)" || {
                warn "强制卸载后 SSH 中断，尝试重新连接…"
                ssh_ensure || die "强制卸载后无法重新连接设备；已中止格式化。"
                mount_state="$(ssh_run "/sbin/mount")" ||
                    die "无法读取设备挂载状态；为避免格式化时锁住 APFS 容器，已中止。"
            }
        fi
        if printf '%s\n' "$mount_state" | grep -Fq "/dev/disk0s1s${T_MNT_SYSTEM12} on /mnt1 "; then
            die "SEP 已加载，但 SystemA (/mnt1) 仍处于挂载状态；为避免格式化时锁住 APFS 容器，已中止。设备挂载表: $mount_state"
        fi
        # ensure_sep keeps its Preboot firmware source mounted until the SEP
        # ping succeeds. Detach it here, then re-read the mount table; never
        # proceed to newfs if an unmount or the subsequent check is uncertain.
        if printf '%s\n' "$mount_state" | grep -Fq " on ${PREBOOT_MNT:-/mnt8} "; then
            umount_rc=0
            ssh_run "/sbin/umount ${PREBOOT_MNT:-/mnt8}" || umount_rc=$?
            if [ "$umount_rc" -ne 0 ]; then
                warn "卸载 Preboot (${PREBOOT_MNT:-/mnt8}) 失败（rc=${umount_rc}），重新连接并检查挂载状态…"
                ssh_ensure || die "卸载 Preboot 后 SSH 未恢复；已中止格式化。"
            fi
            mount_state="$(ssh_run "/sbin/mount" 2>/dev/null)" || {
                warn "读取设备挂载状态时 SSH 中断，尝试重新连接…"
                ssh_ensure || die "无法重新连接设备读取挂载状态；已中止格式化。"
                mount_state="$(ssh_run "/sbin/mount")" ||
                    die "无法读取设备挂载状态；已中止格式化。"
            }
        fi
        if printf '%s\n' "$mount_state" | grep -Fq " on ${PREBOOT_MNT:-/mnt8} "; then
            die "Preboot 挂载点仍被占用；已中止格式化。设备挂载表: $mount_state"
        fi
        mounted_apfs="$(printf '%s\n' "$mount_state" | /usr/bin/grep '^/dev/disk0s1s' || true)"
        [ -z "$mounted_apfs" ] ||
            die "APFS 卷卸载后仍有卷挂载：${mounted_apfs}；已中止格式化。"
    fi
    for name in $names; do
        flag=""
        case "$name" in *Data*) [ "$encrypt" -eq 1 ] && flag=" -P" ;; esac
        log "建卷 $name (role 0${flag:+, 加密})…"
        # 实测：SEP 未就绪时 newfs_apfs -P 会无限挂起并锁死容器，必须带超时
        # Match the 180s window used by the Python implementation; encryption
        # initialization can take longer than ordinary APFS volume creation.
        rsh_timeout 180 "/sbin/newfs_apfs -A$flag -v $name /dev/disk0s1"
        sync_dev
    done
    log "当前卷: $(slices)"
}

# 卷是否存在（按 slice 号）
apfs_vol_exists() {   # n
    local current_slices
    current_slices="$(slices)" || die "无法读取 APFS 卷列表；不能判断卷是否存在。"
    case " $current_slices " in *"disk0s1s$1 "*) return 0 ;; esac
    return 1
}

# 卷名 → fs_flags（找不到返回 1）
apfs_vol_fs_flags() {   # name → 打印 flags
    local want="$1" i name
    apfs_volumes
    for i in "${APFS_V_IDX[@]}"; do
        name="$(dd if="${APFS_V_BLOCK[$i]}" bs=1 skip=$APFS_NAME_OFF count=256 2>/dev/null | LC_ALL=C tr -d '\000')"
        [ "$name" = "$want" ] || continue
        rd_u64 "${APFS_V_BLOCK[$i]}" $APFS_FS_FLAGS_OFF
        return 0
    done
    return 1
}

# 指定卷的加密状态：encrypted → 0，UNENCRYPTED → 1，找不到 → 2
apfs_name_encrypted() {   # name
    local flags
    flags="$(apfs_vol_fs_flags "$1")" || return 2
    [ $(( flags & 1 )) -eq 0 ] && return 0
    return 1
}

apfs_report_data_encryption() {   # data_name
    local want="$1" flags
    if ! flags="$(apfs_vol_fs_flags "$want")"; then
        warn "未找到卷 ${want}，无法核验加密状态"
        return 0
    fi
    if [ $(( flags & 1 )) -ne 0 ]; then
        log "  $want 实际加密状态: UNENCRYPTED (apfs_fs_flags=0x$(printf '%x' "$flags"))"
        warn "  $want 仍为 UNENCRYPTED：当前 ramdisk 的 newfs_apfs 未置加密位"
    else
        log "  $want 实际加密状态: encrypted (apfs_fs_flags=0x$(printf '%x' "$flags"))"
    fi
    return 0
}

# 确保 DataB 以「可写」方式挂到 /mnt7（protect 优先，失败/只读则退回 plain）
# 实机踩过：/mnt7 已挂载但只读，tar 写每个文件都报 "Operation not permitted"。
mount_data_b() {
    local dev="/dev/disk0s1s$T_MNT_DATA_B" mode mline
    if ssh_run "/sbin/mount | /usr/bin/grep -q \"$dev on /mnt7\"" >/dev/null 2>&1; then
        mline="$(ssh_run "/sbin/mount | /usr/bin/grep \" on /mnt7 \"" 2>/dev/null || true)"
        if ssh_run "touch /mnt7/.n61-wt && rm -f /mnt7/.n61-wt" >/dev/null 2>&1; then
            log "  DataB 已挂载: ${mline}"
            return 0
        fi
        warn "DataB 已挂载但不可写（${mline}），重新挂载…"
        umount_vols /mnt7
    fi
    for mode in protect plain; do
        if [ "$mode" = protect ]; then
            ssh_run "/sbin/mount_apfs -o protect $dev /mnt7" >/dev/null 2>&1 ||
                { warn "DataB protect 挂载失败"; continue; }
        else
            ssh_run "/sbin/mount_apfs $dev /mnt7" >/dev/null 2>&1 ||
                { warn "DataB plain 挂载失败"; continue; }
        fi
        if ssh_run "touch /mnt7/.n61-wt && rm -f /mnt7/.n61-wt" >/dev/null 2>&1; then
            log "  DataB 挂载方式: ${mode}（可写）"
            return 0
        fi
        warn "DataB 以 ${mode} 挂载后仍不可写，换一种方式…"
        umount_vols /mnt7
    done
    die "DataB（${dev}）无法以可写方式挂载：SEP/容器状态异常"
}

# ---------- 改 role ----------
apfs_set_roles() {   # "3:0x80 4:0x1 5:0x40" write(0/1)
    local spec="$1" write="$2" pair idx role i name paddr blk old tmp bak readback
    local mnts used_roles="" stamp
    apfs_volumes
    # 旧系统（iOS15 的 SystemA/DataA）已占用 System/Data role 时，iOS16 靠 role 找 Data 卷，
    # 必须把旧持有者降为 role 0；仅在清单显式设置 T_ROLES_DEMOTE_OLD=1 时执行（每个块先备份）。
    local demoted="" spec_idx="" prole pidx
    for pair in $spec; do spec_idx="$spec_idx ${pair%%:*}"; done
    if [ "${T_ROLES_DEMOTE_OLD:-0}" = "1" ]; then
        for pair in $spec; do
            pidx="${pair%%:*}"; prole=$(( ${pair##*:} ))
            [ "$prole" -ne 0 ] || continue
            for i in "${APFS_V_IDX[@]}"; do
                case " $spec_idx " in *" $i "*) continue ;; esac
                cur="$(rd_u16 "${APFS_V_BLOCK[$i]}" $APFS_ROLE_OFF)"
                [ "$cur" = "$prole" ] || continue
                # iOS 15 stays bootable. System is 0x1 and Data is 0x40.
                # Demote may only free the role named in the spec, and never those two.
                case "$cur" in
                    1|64)
                        die "s$i holds System (0x1) or Data (0x40). Refusing to clear it."
                        ;;
                esac
                demoted="$demoted $i"
            done
        done
        if [ -n "$demoted" ]; then
            warn "将把旧卷 s${demoted# } 的 role 降为 0（原块已备份到 $WK/apfs-role-backup）。"
            warn "之后 iOS15 将无法再从这些卷正常启动；要还原，把备份块写回对应 paddr。"
            local dspec=""
            for i in $demoted; do dspec="$dspec $i:0x0"; done
            spec="${dspec# } $spec"
        fi
    fi
    if [ "$write" -eq 1 ]; then
        mnts="$(rsh '/sbin/mount')"
        for pair in $spec; do
            idx="${pair%%:*}"
            case "$mnts" in *"/dev/disk0s1s$idx "*) die "s$idx 已挂载，先卸载" ;; esac
        done
    fi
    stamp="$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$WK/apfs-role-backup"
    for pair in $spec; do
        idx="${pair%%:*}"
        role=$(( ${pair##*:} ))
        [ -n "${APFS_V_BLOCK[$idx]:-}" ] || die "s$idx 不存在"
        blk="${APFS_V_BLOCK[$idx]}"
        paddr="${APFS_V_PADDR[$idx]}"
        apfs_checksum_ok "$blk" || die "s$idx: 现有校验和无效，拒绝改动"
        # role 唯一性：其他卷不得占用同一 role
        if [ "$role" -ne 0 ]; then
            for i in "${APFS_V_IDX[@]}"; do
                [ "$i" = "$idx" ] && continue
                case " $demoted " in *" $i "*) continue ;; esac
                if [ "$(rd_u16 "${APFS_V_BLOCK[$i]}" $APFS_ROLE_OFF)" = "$role" ]; then
                    die "role 0x$(printf '%x' "$role") 已被 s$i 占用"
                fi
            done
        fi
        old="$(rd_u16 "$blk" $APFS_ROLE_OFF)"
        log "s$idx: role 0x$(printf '%x' "$old") -> 0x$(printf '%x' "$role")"
        [ "$write" -eq 1 ] || continue
        bak="$WK/apfs-role-backup/block-$paddr-s$idx-$stamp.bin"
        cp -p "$blk" "$bak"
        tmp="$WK/.role-s$idx.bin"
        cp -p "$blk" "$tmp"
        printf "\\x$(printf '%02x' $(( role & 0xff )))\\x$(printf '%02x' $(( (role >> 8) & 0xff )))" |
            dd of="$tmp" bs=1 seek=$(( APFS_ROLE_OFF )) count=2 conv=notrunc 2>/dev/null
        apfs_seal "$tmp"
        apfs_write_block "$paddr" "$tmp"
        readback="$(apfs_read_block_fresh "$paddr")" || die "s$idx: 读回失败"
        if ! cmp -s "$tmp" "$readback"; then
            die "s$idx: 读回不一致！用备份恢复: $bak"
        fi
        ok "  已写入并校验（备份: ${bak}）"
        rm -f "$tmp"
    done
    if [ "$write" -eq 1 ]; then
        APFS_CACHE_CLEAR=1
        rm -rf "$(apfs_dir)"
        apfs_list
    fi
}

# 卷名是以 NUL 结尾的 C 字符串。删掉全部 NUL 会把结束符后面的残留字节拼进来，
# 于是名字看起来是 SystemB，比较却失败。
apfs_block_name() {   # blockfile
    dd if="$1" bs=1 skip=$APFS_NAME_OFF count=256 2>/dev/null |
        LC_ALL=C awk 'BEGIN { RS="\0"; ORS="" } { gsub(/\r/, ""); sub(/[[:space:]]+$/, ""); print; exit }'
}

# 槽位 N 的卷名
apfs_slot_name() {   # n
    local n="$1"
    apfs_volumes
    [ -n "${APFS_V_BLOCK[$n]:-}" ] || return 1
    apfs_block_name "${APFS_V_BLOCK[$n]}"
}

# 破坏性阶段前确认目标槽位真的是 SystemB / DataB，绝不碰原有卷（Update/xART/Preboot/...）
guard_target_volumes() {   # [system|data|both]
    local what="${1:-both}" data_name sys_name
    data_name="$(printf '%s' "${T_VOL_CREATE:-SystemB DataB}" | awk '{print $2}')"
    sys_name="$(printf '%s' "${T_VOL_CREATE:-SystemB DataB}" | awk '{print $1}')"
    if [ "$what" != "data" ]; then
        [ "$(apfs_slot_name "$T_MNT_SYSTEM_B" 2>/dev/null)" = "$sys_name" ] ||
            die "拒绝操作：s${T_MNT_SYSTEM_B} 不是 ${sys_name}（实际: '$(apfs_slot_name "$T_MNT_SYSTEM_B" 2>/dev/null)'）。先跑 volumes。"
    fi
    if [ "$what" != "system" ]; then
        [ "$(apfs_slot_name "$T_MNT_DATA_B" 2>/dev/null)" = "$data_name" ] ||
            die "拒绝操作：s${T_MNT_DATA_B} 不是 ${data_name}（实际: '$(apfs_slot_name "$T_MNT_DATA_B" 2>/dev/null)'）。先跑 volumes。"
    fi
}
