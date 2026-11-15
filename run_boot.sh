#!/bin/bash
# iOS 16 Tethered Boot Automation Shell Script
# Runs build followed by automated device boot
#
# Usage:
#   ./run_boot.sh [full|minimal|safe-minimal] [--boot]
#
# Modes:
#   full          - All driver patches + WiFi + SEP compatibility
#   minimal       - Only essential patches (remove_ipad_drivers, generic_security)
#   safe-minimal  - PMU + SEP legacy patches only (safest for unknown hardware)
#
# Examples:
#   ./run_boot.sh                 # Build safe-minimal mode
#   ./run_boot.sh full --boot     # Build full mode and boot device
#   ./run_boot.sh minimal         # Build minimal mode only

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Default mode and boot flag
MODE="${1:-safe-minimal}"
BOOT_FLAG=""

# Parse arguments
[ $# -gt 0 ] && shift
for arg in "$@"; do
    case "$arg" in
        --boot|-b)
            BOOT_FLAG="--boot"
            ;;
        *)
            echo "Unknown argument: $arg"
            exit 1
            ;;
    esac
done

echo "=========================================="
echo "N69AP iOS 16 Tethered Boot Pipeline"
echo "Mode: $MODE"
echo "=========================================="

IM4M="state/IM4M.bin"

# 1. Ensure IM4M exists
if [ ! -f "$IM4M" ]; then
    echo "ERROR: IM4M missing at $IM4M."
    echo "       Extract it via SSH ramdisk or ./ios16se extract-im4m <nor_dump> $IM4M"
    exit 1
fi

# 2. Check for required IPSWs
IPSW_DIR="firmware"
IPSWS=(
    "iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw"
    "iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw"
)

for ipsw in "${IPSWS[@]}"; do
    if [ ! -f "$IPSW_DIR/$ipsw" ]; then
        echo "ERROR: IPSW missing: $IPSW_DIR/$ipsw"
        exit 1
    fi
done
echo "[✓] All IPSWs present"

# 3. Run the unified Python boot script
if [ "$BOOT_FLAG" = "--boot" ]; then
    python3 run_n69_ios16_boot.py --im4m "$IM4M" --mode "$MODE" --boot
else
    python3 run_n69_ios16_boot.py --im4m "$IM4M" --mode "$MODE" --build-only
fi

echo "=========================================="
echo "BOOT SEQUENCE COMPLETED"
echo "=========================================="