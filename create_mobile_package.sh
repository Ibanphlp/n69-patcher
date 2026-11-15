#!/bin/bash
# Create a portable package for Mac installation
# Excludes large IPSWs (download separately on Mac)

set -euo pipefail

PROJECT_DIR="/home/iban/iOS-16-SE"
PACKAGE_NAME="n69ap-ios16-install-$(date +%Y%m%d).tar.gz"
OUTPUT="/tmp/$PACKAGE_NAME"

echo "=== Creating portable installation package ==="

cd "$PROJECT_DIR"

# What to include (code, scripts, configs, pre-built artifacts, tools)
# What to EXCLUDE (IPSWs - download on Mac, large build artifacts, cache)

tar -czf "$OUTPUT" \
    --exclude='*.ipsw' \
    --exclude='firmware/*.ipsw' \
    --exclude='firmware/*.dmg' \
    --exclude='work/driver_re*' \
    --exclude='work/kernel_inject*' \
    --exclude='work/test_*' \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='.agents' \
    --exclude='ipad6-ipados18-master' \
    --exclude='ipodtouch6-ios13-main' \
    --exclude='kairos-master' \
    --exclude='SevenUP-main' \
    --exclude='n56-patcher' \
    \
    README.md LICENSE requirements.txt \
    run_boot.sh run_n69_ios16_boot.py n69ap_ios16_full_install.sh n69ap_mac_setup.sh \
    create_mobile_package.sh cleanup_for_github.sh \
    N69AP_BOOT_GUIDE.md N69AP_Installation_Next_Steps.md \
    PROJECT_COMPLETION_TABLE.md PROJECT_AUDIT.md AUDIT_SUMMARY_TABLE.md FINAL_AUDIT_TABLE.md REMAINING_TASKS.md \
    profiles src artifacts state ramdisk-main tools n61-patcher-master 2>/dev/null || true

echo "Package created: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
echo ""
echo "=== On Mac, you'll also need to download ==="
echo "1. iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw (SE 15.8.8) ~3.5GB"
echo "2. iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw (iPad5 16.7.16) ~4.2GB"
echo "3. iPhone_4.7_15.8.8_19H422_Restore.ipsw (iPhone6s 15.8.8) ~3.5GB (optional, for UI)"
echo ""
echo "Place in: ./firmware/ directory"
echo ""
echo "Also copy these tool dirs from this machine:"
echo "  cp -r ramdisk-main/ /path/to/package/"
echo "  cp -r tools/ /path/to/package/"
echo ""
echo "Or download pre-built tools from GitHub releases"