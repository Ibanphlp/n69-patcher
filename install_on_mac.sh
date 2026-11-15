#!/bin/bash
# install_on_mac.sh - ONE COMPLETE SCRIPT FOR MAC

set -euo pipefail

MODE="${1:-full}"
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║           N69AP iOS 16 Complete Install                      ║${NC}"
echo -e "${CYAN}║              iPhone SE 1st Gen (A9)                           ║${NC}"
echo -e "${CYAN}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "Mode: ${MODE}"
echo ""

# Check Intel Mac
if [[ "$(uname)" != "Darwin" ]]; then
    echo -e "${RED}[✗]${NC} Must run on macOS"
    exit 1
fi
if [[ "$(uname -m)" == "arm64" ]]; then
    echo -e "${RED}[✗]${NC} Apple Silicon not supported"
    exit 1
fi
echo -e "${GREEN}[✓]${NC} Intel Mac confirmed"

# Check IM4M
if [[ ! -f "state/IM4M.bin" ]]; then
    echo -e "${RED}[✗]${NC} IM4M.bin missing"
    exit 1
fi
SIZE=$(stat -f%z state/IM4M.bin 2>/dev/null || stat -c%s state/IM4M.bin)
if [[ $SIZE -lt 1000 ]]; then
    echo -e "${RED}[✗]${NC} IM4M.bin too small ($SIZE bytes)"
    exit 1
fi
echo -e "${GREEN}[✓]${NC} IM4M.bin ($SIZE bytes)"

# Check tools
for tool in gaster irecovery img4 kairos; do
    if [[ ! -x "ramdisk-main/Darwin/$tool" ]] && [[ ! -x "tools/dualra1n/binaries/Darwin/$tool" ]]; then
        echo -e "${RED}[✗]${NC} Tool missing: $tool"
        exit 1
    fi
done
echo -e "${GREEN}[✓]${NC} All tools present"

# Check IPSWs
for ipsw in "firmware/iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw" \
             "firmware/iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw"; do
    if [[ ! -f "$ipsw" ]]; then
        echo -e "${RED}[✗]${NC} IPSW missing: $ipsw"
        exit 1
    fi
done
echo -e "${GREEN}[✓]${NC} All IPSWs present"

# Device check
echo ""
echo -e "${CYAN}━━━ Device Check ━━━${NC}"
echo "Put iPhone SE in DFU mode:"
echo "  1. Connect to Mac via USB"
echo "  2. Hold Power + Home for 8 seconds"
echo "  3. Release Power, keep holding Home for 15 seconds"
echo "  4. Screen stays BLACK = DFU mode"
echo ""
read -p "Press Enter when device is in DFU mode..." _

if ramdisk-main/Darwin/irecovery -q 2>/dev/null | grep -qi "n69ap"; then
    echo -e "${GREEN}[✓]${NC} N69AP detected in DFU mode"
else
    echo -e "${RED}[✗]${NC} N69AP not detected"
    echo "  Running gaster pwn to establish exploit..."
    ramdisk-main/Darwin/gaster pwn 2>/dev/null || true
    sleep 2
    if ramdisk-main/Darwin/irecovery -q 2>/dev/null | grep -qi "n69ap"; then
        echo -e "${GREEN}[✓]${NC} N69AP now detected"
    else
        echo -e "${RED}[✗]${NC} Device not detected - check DFU mode"
        exit 1
    fi
fi

# ========== BOOT CHAIN BUILD ==========
echo ""
echo -e "${CYAN}━━━ BUILDING BOOT CHAIN ━━━${NC}"
echo "Building iBSS, iBEC, DT, TC, KC, SEP images..."

# CRITICAL: Establish checkm8 exploit before build
echo "Establishing checkm8 exploit..."
ramdisk-main/Darwin/gaster pwn

# Verify device state
echo "Verifying device state..."
for i in {1..10}; do
    if ramdisk-main/Darwin/irecovery -q 2>/dev/null | grep -qi "PWND"; then
        echo -e "${GREEN}[✓]${NC} Device pwnd and ready for build"
        break
    fi
    echo "  Waiting for PWND state... ($i/10)"
    sleep 1
done

# Reset to establish SSH ramdisk
ramdisk-main/Darwin/gaster reset
sleep 3

# Run build
python3 run_n69_ios16_boot.py --im4m state/IM4M.bin --mode "$MODE" --build-only
echo -e "${GREEN}[✓]${NC} Boot images built successfully"

# ========== VOLUMES ==========
echo ""
echo -e "${CYAN}━━━ CREATING VOLUMES ━━━${NC}"
./n61.sh volumes --target n69-ios16 -y
echo -e "${GREEN}[✓]${NC} APFS volumes created"

# ========== ROOTFS ==========
echo ""
echo -e "${CYAN}━━━ STREAMING ROOTFS (~25 min) ━━━${NC}"
echo -e "${YELLOW}Keep device connected!${NC}"
./n61.sh rootfs --target n69-ios16 -y
echo -e "${GREEN}[✓]${NC} RootFS streamed"

# ========== FINALIZE ==========
echo ""
echo -e "${CYAN}━━━ PATCHING ROOTFS ━━━${NC}"
./n61.sh patch-rootfs --target n69-ios16 -y
echo -e "${GREEN}[✓]${NC} RootFS patched"

# ========== BOOT ==========
echo ""
echo -e "${CYAN}━━━ BOOTING TO SPRINGBOARD ━━━${NC}"
for img in boot_iBSS boot_iBEC boot_DT boot_TC sep boot_KC; do
    ramdisk-main/Darwin/irecovery -f "artifacts/boot/${img}.img4" && echo "  ✓ $img sent"
done

ramdisk-main/Darwin/irecovery -c bootx
echo "  ✓ bootx executed"

# ========== SUCCESS ==========
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    INSTALLATION COMPLETE                       ║${NC}"
echo -e "${GREEN}║  Device should now boot to SpringBoard                       ║${NC}"
echo -e "${GREEN}║  Select language → WiFi → Sign in                           ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
