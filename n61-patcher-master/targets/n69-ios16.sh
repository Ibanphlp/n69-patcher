#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# shellcheck shell=bash
# N69AP iOS 16.7.16 target manifest for n61.sh
# iPhone SE 1st Gen (N69AP, A9, iPhone8,4)
# Donors: SE 15.8.8 (bootchain, DT, SEP, baseband, WiFi) + iPad5 16.7.16 (kernel, rootfs) + iPhone6s 15.8.8 (UI)

# ============= 目标身份 =============
T_NAME="n69-ios16"
T_PRIMARY_DONOR="n69"
T_SOURCE_ROOT="donors"

# ============= 设备级常量（跨 iOS 版本不变） =============
T_MODEL="n69ap"
T_PRODUCT="iPhone8,4"
T_ECID=""       # 填入设备实际 ECID（从 irecovery -q 获取）
T_UDID=""       # 填入设备实际 UDID
T_PMU="d2255"
T_TOUCH="spin69"
T_CODEC="CS42L71"
T_CHARGER="C26"
T_AMP="CS35L19"

# 第二系统根分区：s8 (disk0s1s8)——s4=Update/s5=xART/s6=Preboot/s7=Hardware 已被占用
T_ROOT_PART=8

# s8 is the iOS 16 system volume. s9 may already exist from the earlier
# two-volume attempt; it is not the Data volume iOS 16 boots.
# SevenUP keeps the stock Data role (0x40) on the existing volume and selects
# the new system with rd=. Role 0x4 is Recovery, which is what SevenUP assigns
# the new volume. Do not move the iOS 15 System or Data roles.
T_VOL_CREATE="SystemB"
T_ROLES="8:0x4"
T_DATA_PROTECTION="none"
T_MNT_DATA_STOCK=2
# Preserve existing roles on other volumes, including Recovery.
T_ROLES_DEMOTE_OLD=0
T_MNT_SYSTEM_B=8
T_MNT_SYSTEM12=1

# ramdisk 配置
T_RAMDISK_PORT=2222
T_RAMDISK_PASSWORD="alpine"
T_RAMDISK_DIR="../ramdisk-main/sshramdisk"

# ============= 版本级常量（随 iOS 版本变） =============
T_BOOTARGS="rd=disk0s1s8 amfi_get_out_of_my_way=1 cs_enforcement_disable=1 wdt=-1 debug=0x2014e keepsyms=1 serial=3"

# 源 firmware profiles
# n69: iPhone SE 15.8.8 (本机 bootchain、DT、SEP、baseband、WiFi、无线固件)
# j96: iPad 5 16.7.16 (干净内核基底、rootfs)
# n71: iPhone 6s 15.8.8 (4.7"@2x UI resources)
T_BUILD_N69="19H422"
T_IPSW_N69="iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw"
T_ROOTFS_ENTRY_N69="098-68807-067.dmg"

T_BUILD_J96="20H392"
T_IPSW_J96="iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw"
T_ROOTFS_ENTRY_J96="087-86634-044.dmg"

T_CRYPTEX_ENTRY_J96="087-86624-047.dmg"

T_BUILD_N71="19H422"
T_IPSW_N71="iPhone_4.7_15.8.8_19H422_Restore.ipsw"
T_ROOTFS_ENTRY_N71="098-68805-067.dmg"

# UI donor 用于 4.7"@2x 资源
T_UI_DONOR="n71"

# ============= 从 IPSW 生成的派生输入 =============
# 这些文件由 Python 版提取并缓存到 ipsw/cache/derived/
# 也可以由 sudo python3 n56patcher.py all --target n69-ios16 生成

T_DERIVED_IBSS="extracted/n69ap_ios12/iBSS.n69.patched"
T_DERIVED_IBEC="extracted/n69ap_ios12/iBEC.n69.dec"
T_DERIVED_J96_KERNEL="extracted/j96ap_ios16/kernelcache.release.ipad5"
T_DERIVED_TRUSTCACHE="extracted/j96ap_ios16/087-86634-044.dmg.trustcache"

# WiFi 模块 (BCM4345 类似)
T_WIFI_SRCDIR="usr/share/firmware/wifi/C-43452__s-A2"
T_WIFI_FILES="hans.trx hans.clmb hans.txcb"

# ============= 补丁基座与选挂 =============
# DeviceTree: 使用本机 N69AP DT (native 硬件)
T_BASE_DT="dt.base"
# KernelCache: 使用 iPad5 16.7.16 干净内核
T_BASE_KC="kc.j96_clean"

# N69 hardware-driver features remain off until the corresponding kernel
# transplants have validated output and runtime compatibility evidence.
T_FEATURES="core"

# 可选补丁排除
T_EXCLUDE=""

# ============= donor 源 =============
T_DONOR_APFS14=""
T_SYS12_MOUNT_HINT="disk0s1s1"

# ============= 完整 trustcache 模式 =============
T_FULL_TRUSTCACHE=1

# ============= 启动链发送顺序 =============
T_BOOT_CHAIN="iBSS iBEC DeviceTree TrustCache SEP KernelCache"
