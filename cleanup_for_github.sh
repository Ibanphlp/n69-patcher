#!/bin/bash
# cleanup_for_github.sh
# Safely clean the project for GitHub release.
# Run this from the project root: ./cleanup_for_github.sh
# Review each deletion before running. Files removed are only upstream refs
# and old generated artifacts — no source of the N69AP pipeline.

set -uo pipefail

echo "=========================================="
echo "N69AP iOS 16 - GitHub Cleanup Script"
echo "=========================================="
echo ""

CONFIRM=""
read -r -p "This removes upstream project copies and old docs. Continue? [y/N] " CONFIRM
case "$CONFIRM" in
    y|Y|yes|Yes) ;;
    *) echo "Cancelled."; exit 0 ;;
esac

cd "$(dirname "$0")"

echo ""
echo "[1/8] Removing old root scripts (replaced by new pipeline)..."
rm -f boot_ios_16_se.sh boot_se.py build.py full_boot.sh kernel_launcher.py patch_kernelcache.py capture_logs.sh

echo "[2/8] Removing duplicate/old docs (consolidated into N69AP_BOOT_GUIDE.md + FINAL_AUDIT_TABLE.md)..."
rm -f AUDIT_SUMMARY_TABLE.md N69AP_Installation_Next_Steps.md PROJECT_AUDIT.md PROJECT_COMPLETION_TABLE.md REMAINING_TASKS.md IMPLEMENTATION_PLAN.md NEXT_STEPS.md RAMDISK_EXTRACTION.md README_BOOT.md
rm -rf docs/

echo "[3/8] Removing old configs (replaced by n69ap_ios16.toml)..."
rm -f profiles/n69ap.toml pyproject.toml

echo "[4/8] Removing duplicate target/registry (replaced by n69-ios16.sh + registry_n69_ios16.tsv)..."
rm -f n61-patcher-master/targets/n69_ios16.py
rm -f n61-patcher-master/patches/registry.tsv

echo "[5/8] Removing unused shell scripts in patches (replaced by Python patches)..."
rm -f n61-patcher-master/patches/rootfs/n69_finish.sh
rm -f n61-patcher-master/patches/rootfs/n69_set_roles.sh
rm -f n61-patcher-master/patches/rootfs/n69_ios16_install.py

echo "[6/8] Removing unused tools/tests/reports..."
rm -f tools/re_pinot_analyze.py
# KEEP: ramdisk-main/Darwin/ ramdisk-main/Windows/ (for cross-platform)
# KEEP: ramdisk-main/sshrd.sh ramdisk-main/README.md
rm -rf tests/ n61-patcher-master/tests/
rm -rf reports/

echo "[7/8] Removing generated dirs (will be recreated)..."
rm -rf artifacts/ work/ state/
mkdir -p state firmware
touch state/.gitkeep firmware/.gitkeep

echo "[8/8] KEEPING (not deleting):"
echo "  - Upstream references: n56-patcher/ SevenUP-main/ ipad6-ipados18-master/ ipodtouch6-ios13-main/ kairos-master/ kairos-0.2.1-linux-x64/"
echo "  - Cross-platform ramdisk: ramdisk-main/Darwin/ ramdisk-main/Windows/ ramdisk-main/sshrd.sh ramdisk-main/README.md"
echo "  - Your pipeline source: n61-patcher-master/, src/ios16se/, scripts/, profiles/, run_*.sh, run_*.py"
echo "  - Docs: N69AP_BOOT_GUIDE.md, FINAL_AUDIT_TABLE.md"
echo "  - Config: profiles/n69ap_ios16.toml, ramdisk-main/Darwin/"

echo ""
echo "Done. Project cleaned for GitHub."
echo ""
echo "Next:"
echo "  1. Create .gitignore"
echo "  2. Create LICENSE"
echo "  3. Write README.md"
echo "  4. git init && git add . && git commit"
echo ""
echo "Current structure:"
ls -la