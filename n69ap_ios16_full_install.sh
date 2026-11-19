#!/bin/bash
# n69ap_ios16_full_install.sh
# Full automated installation for iPhone SE 1st Gen (N69AP) iOS 16 tethered boot
# Usage: ./n69ap_ios16_full_install.sh [full|minimal|safe-minimal] [--resume-ramdisk]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# This installer writes APFS volumes and boots an experimental kernel. Its
# Python-built stock trust cache is not synchronized with patch-rootfs output,
# and N69 hardware/SEP support is not yet validated. Stop before sudo or writes.
if [[ "${N69_EXPERIMENTAL_INSTALL_ACK:-}" != "1" ]]; then
    echo "ERROR: N69 iOS 16 installation is experimental and not boot-validated." >&2
    echo "The boot trust cache may not match installed modifications; APFS role changes may affect the existing OS." >&2
    echo "Do not continue without verified backups and a recovery plan." >&2
    echo "To explicitly accept the risks: N69_EXPERIMENTAL_INSTALL_ACK=1 $0 $*" >&2
    exit 1
fi

# hdiutil owners mounts and the rootfs/Cryptex deployment need root.
if [[ $EUID -ne 0 ]]; then
    exec sudo env "N69_EXPERIMENTAL_INSTALL_ACK=1" "N69_PREBOOT_HASH=${N69_PREBOOT_HASH:-}" \
        "$SCRIPT_DIR/$(basename "${BASH_SOURCE[0]}")" "$@"
fi

# Colors
RED='\033[0;31m'
GREEN='\033[1;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Mode
MODE="${1:-full}"
case "$MODE" in
    full|minimal|safe-minimal) ;;
    *) echo "Unsupported mode: $MODE (use full, minimal, or safe-minimal)" >&2; exit 2 ;;
esac
RESUME_RAMDISK=0
if [[ "${2:-}" == "--resume-ramdisk" && $# -eq 2 ]]; then
    RESUME_RAMDISK=1
elif [[ $# -gt 1 ]]; then
    echo "Usage: $0 [full|minimal|safe-minimal] [--resume-ramdisk]" >&2
    exit 2
fi

# Paths (n56-standard)
IM4M="state/IM4M.bin"
SEP="state/sep-firmware.img4"
IRECOVERY="tools/SSHRD_Script/Darwin/irecovery"
GASTER="tools/SSHRD_Script/Darwin/gaster"
IMG4="tools/SSHRD_Script/Darwin/img4"
IMG4TOOL="ramdisk-main/Darwin/img4tool"

echo -e "${BOLD}${CYAN}"
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║           N69AP iOS 16 Complete Installation                  ║"
echo "║              iPhone SE 1st Gen (A9) → iOS 16.7.16             ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo "Mode: ${BOLD}${MODE}${NC}"
echo ""

# Preflight
echo -e "${CYAN}━━━ Preflight Checks ━━━${NC}"

# Check tools
for tool in "$IRECOVERY" "$GASTER" "$IMG4"; do
    if [[ ! -x "$tool" ]]; then
        echo -e "${RED}[✗]${NC} Tool missing: $tool"
        echo "    Run: ./n69ap_mac_setup.sh"
        exit 1
    fi
done
echo -e "${GREEN}[✓]${NC} All tools present"

# Check IPSWs
for ipsw in "firmware/iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw" \
             "firmware/iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw"; do
    if [[ ! -f "$ipsw" ]]; then
        echo -e "${RED}[✗]${NC} IPSW missing: $ipsw"
        echo "    Run: ./n69ap_mac_setup.sh"
        exit 1
    fi
done
echo -e "${GREEN}[✓]${NC} All IPSWs present"

# Device check
echo ""
echo -e "${CYAN}━━━ Device Check ━━━${NC}"
require_n69_dfu() {
    local probe
    probe=$("$IRECOVERY" -q 2>&1) || { echo "Cannot query the connected device." >&2; return 1; }
    if ! printf '%s\n' "$probe" | grep -qi 'n69ap' ||
       ! printf '%s\n' "$probe" | grep -Eq 'MODE: DFU'; then
        echo "N69AP not detected in DFU mode. Check the USB connection and try again." >&2
        return 1
    fi
}

if [[ "$RESUME_RAMDISK" -eq 1 ]]; then
    echo "Resuming SEP capture from the running SSH ramdisk; keep the iPhone connected."
else
    echo "Place iPhone SE 1st Gen in DFU mode:"
    echo "  1. Connect to Mac via USB"
    echo "  2. Hold Power + Home for 8 seconds"
    echo "  3. Release Power, keep holding Home for 15 seconds"
    echo "  4. Screen stays BLACK = DFU mode"
    echo ""
    read -p "Press Enter when device is in DFU mode..." -r
    require_n69_dfu
    echo -e "${GREEN}[✓]${NC} N69AP detected in DFU mode"
fi

# The generated ramdisk is needed both for capture and for installation.
echo ""
echo -e "${CYAN}━━━ Preparing SSH Ramdisk ━━━${NC}"
ensure_ramdisk_images() {
    local component ramdisk_missing=0
    for component in iBSS iBEC ramdisk devicetree trustcache kernelcache; do
        [[ -s "ramdisk-main/sshramdisk/$component.img4" ]] || ramdisk_missing=1
    done
    if [[ "$ramdisk_missing" -eq 1 ]]; then
        echo "Generating the stock 15.8.8 SSH ramdisk..."
        (cd ramdisk-main && ./sshrd.sh 15.8.8)
        for component in iBSS iBEC ramdisk devicetree trustcache kernelcache; do
            [[ -s "ramdisk-main/sshramdisk/$component.img4" ]] || {
                echo "SSH ramdisk generation did not produce $component.img4; stopping." >&2
                exit 1
            }
        done
    fi
}
if [[ "$RESUME_RAMDISK" -eq 0 ]]; then
    ensure_ramdisk_images
fi

# Capture the complete device-specific IMG4 and its embedded IM4M before the build.
echo ""
echo -e "${CYAN}━━━ SEP and IM4M ━━━${NC}"
captured_now=0
if [[ ! -s "$SEP" || ! -s "$IM4M" ]]; then
    echo "Capturing the stock SEP from this device's Preboot volume..."
    if [[ "$RESUME_RAMDISK" -eq 0 ]]; then
        N61_RAMDISK_SEP=0 ./n61.sh ramdisk --target n69-ios16
    fi
    ./n61.sh capture-sep --target n69-ios16
    captured_now=1
fi
if [[ "$RESUME_RAMDISK" -eq 1 || "$captured_now" -eq 1 ]]; then
    echo "Return the phone to DFU mode so its captured SEP can be verified and the boot images built."
    read -p "Press Enter when device is in DFU mode..." -r
    require_n69_dfu
fi
if [[ ! -x "$IMG4TOOL" ]]; then
    echo "Missing executable: $IMG4TOOL; cannot verify the captured SEP." >&2
    exit 1
fi
"$IRECOVERY" -q | python3 tools/sep_image.py verify-device \
    --image "$SEP" --expect-ticket "$IM4M" --img4tool "$IMG4TOOL"
echo -e "${GREEN}[✓]${NC} Captured SEP and IM4M match the connected device"
if [[ "$RESUME_RAMDISK" -eq 1 ]]; then
    ensure_ramdisk_images
fi

# Build boot chain
echo ""
echo -e "${CYAN}━━━ Building Boot Chain ━━━${NC}"

# Boot images come from the Python pipeline (iOS 16 / A9 aware). The bash
# The bash build chain targets iOS 13, so use the N69 iOS 16 Python pipeline.
case "$MODE" in
    full|minimal|safe-minimal) BUILD_MODE="$MODE" ;;
    *) BUILD_MODE="full" ;;
esac
./prepare_derived_n69.sh "$BUILD_MODE"

# Keep the N69 hardware-driver feature chain disabled until its kernel
# transplants have validated output and runtime compatibility evidence.
case "$MODE" in
    full|minimal|safe-minimal) FEATURES="core" ;;
    *) echo "Unsupported mode: $MODE (use full, minimal, or safe-minimal)" >&2; exit 2 ;;
esac

# SSH ramdisk (iOS 15.8.8) - needed by volumes / rootfs / cryptex / patch-rootfs
echo ""
echo -e "${CYAN}━━━ SSH Ramdisk ━━━${NC}"
if [[ ! -f ramdisk-main/sshramdisk/ramdisk.img4 ]]; then
    echo -e "${RED}[✗]${NC} SSH ramdisk not generated yet."
    echo "    With the SE in DFU, run once:  (cd ramdisk-main && ./sshrd.sh 15.8.8)"
    exit 1
fi
echo "Put the SE back in DFU mode if it is not already."
read -p "Press Enter when device is in DFU mode..." -r
require_n69_dfu
# The existing Data volume keeps its systembag.
N61_RAMDISK_SEP=0 ./n61.sh ramdisk --target n69-ios16

# Flash volumes
echo ""
echo -e "${CYAN}━━━ Flashing Volumes ━━━${NC}"

./n61.sh volumes --target n69-ios16 --data-protection none -y
echo "SystemB ready in s8. The existing iOS 15 Data volume is shared."

# Stream rootfs
echo ""
echo -e "${CYAN}━━━ Streaming RootFS (~25 min) ━━━${NC}"
echo -e "${YELLOW}Do NOT disconnect device during this step!${NC}"

./n61.sh rootfs --target n69-ios16 -y
echo "✓ RootFS flashed"

# iOS 16 keeps dyld cache + system libs in a separate SystemOS Cryptex
echo -e "${CYAN}━━━ Cryptex + Preboot ━━━${NC}"
./n61.sh cryptex --target n69-ios16 -y
echo "✓ Cryptex installed"

# Finalize
echo ""
echo -e "${CYAN}━━━ Finalizing ━━━${NC}"

echo "Kernel mode: $MODE | Rootfs feature group: $FEATURES"
./n61.sh patch-rootfs --target n69-ios16 --features "$FEATURES" -y

# Boot
echo ""
echo -e "${CYAN}━━━ Booting to SpringBoard ━━━${NC}"

IRECOVERY="tools/SSHRD_Script/Darwin/irecovery"
GASTER="tools/SSHRD_Script/Darwin/gaster"

# Require the complete chain before any DFU exploit/reset or image transfer.
components=(
    "boot_iBSS:iBSS:"
    "boot_iBEC:iBEC:go"
    "boot_DT:DeviceTree:devicetree"
    "boot_TC:TrustCache:firmware"
    "sep:SEP:rsepfirmware"
    "boot_KC:KernelCache:"
)
for entry in "${components[@]}"; do
    IFS=':' read -r img name cmd <<< "$entry"
    file="artifacts/boot/${img}.img4"
    if [[ ! -s "$file" ]]; then
        echo -e "${RED}[✗]${NC} Required boot image missing or empty: $file" >&2
        exit 1
    fi
done
if [[ ! -x "$IRECOVERY" || ! -x "$GASTER" ]]; then
    echo -e "${RED}[✗]${NC} irecovery or gaster is unavailable; refusing to start boot." >&2
    exit 1
fi

echo -e "${YELLOW}The device is still in the SSH ramdisk. Reboot it into DFU mode again now:${NC}"
echo "  Hold Power + Home 8s, release Power, keep Home ~15s (screen stays black)"
read -p "Press Enter when device is in DFU mode..." -r

# Check if pwned
PROBE=$("$IRECOVERY" -q 2>&1 || true)
cmp -s state/sep-firmware.img4 artifacts/boot/sep.img4 || { echo "Boot SEP differs from the Preboot capture" >&2; exit 1; }
printf '%s\n' "$PROBE" | python3 tools/sep_image.py verify-device --image artifacts/boot/sep.img4 --expect-ticket "$IM4M" --img4tool ramdisk-main/Darwin/img4tool || exit 1
if [[ "$PROBE" != *"PWND"* ]]; then
    echo "  Device not pwned, running gaster pwn..."
    "$GASTER" pwn
    "$GASTER" reset
    sleep 2
fi

python3 - << 'PY'
import json
import sys
from pathlib import Path
sys.path.insert(0, ".")
from src.ios16se.trustcache import boot_trustcache_allowed
manifest = json.loads(Path("artifacts/boot/boot.json").read_text())
if not boot_trustcache_allowed(manifest):
    raise SystemExit(
        "Refusing to send artifacts/boot/boot_TC.img4: it is still the unmarked "
        "stock iPad trust cache. Run ./send_n69_boot.sh after patch-rootfs."
    )
PY

echo "  Sending boot chain..."

for entry in "${components[@]}"; do
    IFS=':' read -r img name cmd <<< "$entry"
    file="artifacts/boot/${img}.img4"

    echo "  Sending $name..."
    if ! "$IRECOVERY" -f "$file"; then
        echo -e "${RED}[✗]${NC} Failed to send $img"
        exit 1
    fi

    if [[ "$cmd" == "go" ]]; then
        # `go` is only needed on A10+; on A9 (N69AP) iBEC runs on load and
        # irecovery would error on the unknown command.
        case "$("$IRECOVERY" -q 2>/dev/null | awk '/CPID/ {print $2}')" in
            0x8010|0x8011|0x8012|0x8015) echo "  Executing go..."; "$IRECOVERY" -c go ;;
            *) echo "  (A9: skipping go)" ;;
        esac
    elif [[ -n "$cmd" ]]; then
        echo "  Executing $cmd..."
        "$IRECOVERY" -c "$cmd"
    fi

    # Sleep to allow device to process
    if [[ "$name" == "iBSS" || "$name" == "iBEC" ]]; then
        sleep 3
    else
        sleep 1
    fi
done

echo "  Executing bootx..."
"$IRECOVERY" -c bootx

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    INSTALLATION COMPLETE                       ║${NC}"
echo -e "${GREEN}║                                                              ║${NC}"
echo -e "${GREEN}║  Boot chain sent; successful boot is not verified            ║${NC}"
echo -e "${GREEN}║  Check serial console for kernel output                      ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "Next steps after boot:"
echo "  1. Select language/country"
echo "  2. Connect to WiFi"
echo "  3. Sign in with Apple ID"
