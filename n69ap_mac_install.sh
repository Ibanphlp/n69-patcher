#!/bin/bash
# n69ap_mac_install.sh - ONE COMMAND for complete installation
# Usage: ./n69ap_mac_install.sh [full|minimal|safe-minimal]

set -euo pipefail

MODE="${1:-full}"

echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║       N69AP iOS 16 - ONE-CLICK INSTALLATION                  ║"
echo "║              iPhone SE 1st Gen → iOS 16.7.16                   ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "Prerequisites:"
echo "  ✓ Intel-based Mac"
echo "  ✓ iPhone SE in DFU mode"
echo ""
read -p "Press Enter when device is in DFU mode..." _ || true

# Step 1: Download tools
echo ""
echo "[1/4] Downloading macOS tools..."

mkdir -p tools/SSHRD_Script/Darwin firmware state artifacts

for tool in gaster irecovery img4; do
    url="https://github.com/palera1n/palera1n/releases/download/v2.0/${tool}-macos"
    output="tools/SSHRD_Script/Darwin/$tool"

    if [[ ! -x "$output" ]]; then
        echo "  Downloading $tool..."
        curl -f -L -o "$output" "$url" 2>/dev/null && chmod +x "$output" || echo "    (may need manual download)"
    fi
done

# Step 2: Download IPSWs
echo ""
echo "[2/4] Downloading IPSWs..."

IPSWS=(
    "iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw"
    "iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw"
)

for ipsw in "${IPSWS[@]}"; do
    output="firmware/$ipsw"
    if [[ ! -f "$output" ]]; then
        echo "  Download $ipsw manually or run: ./n69ap_mac_setup.sh"
    fi
done

# Step 3: Get IM4M
echo ""
echo "[3/4] Checking IM4M..."

IM4M="state/IM4M.bin"
if [[ ! -s "$IM4M" || ! -s state/sep-firmware.img4 ]]; then
    echo "  Missing captured Preboot SEP and embedded IM4M."
    echo "  Boot the SSH ramdisk without SEP, then run: ./n61.sh capture-sep --target n69-ios16"
    exit 1
fi
python3 tools/sep_image.py validate --image state/sep-firmware.img4 --expect-ticket "$IM4M" || exit 1

if [[ -f "$IM4M" ]]; then
    echo "  ✓ IM4M ready ($(stat -f%z "$IM4M") bytes)"
else
    echo "  ⚠ IM4M not found - build may fail"
fi

# Step 4: Run full installation
echo ""
echo "[4/4] Running installation..."

# Translate mode to features for n61.sh
FEATURES=""
case "$MODE" in
    full|minimal|safe-minimal)
        # The N69 hardware transplant chain is disabled pending validation.
        FEATURES="core"
        ;;
    *)
        echo "Unsupported mode: $MODE (use full, minimal, or safe-minimal)" >&2
        exit 2
        ;;
esac

echo "  Building boot chain..."
./n61.sh build --target n69-ios16 --features "$FEATURES" -y

echo "  Creating volumes..."
./n61.sh volumes --target n69-ios16 -y

echo "  Streaming rootfs (25 min)..."
./n61.sh rootfs --target n69-ios16 -y

echo "  Patching rootfs..."
./n61.sh patch-rootfs --target n69-ios16 -y

# Step 5: Boot device
echo ""
echo "[5/5] Booting device..."

IRECOVERY="tools/SSHRD_Script/Darwin/irecovery"
GASTER="tools/SSHRD_Script/Darwin/gaster"

# Validate the whole chain before pwn/reset or the first image transfer.
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
        echo "[✗] Required boot image missing or empty: $file" >&2
        exit 1
    fi
done

if [[ ! -x "$IRECOVERY" || ! -x "$GASTER" ]]; then
    echo "[✗] irecovery or gaster is unavailable; refusing to start boot." >&2
    exit 1
fi

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

echo "  Sending boot chain..."

for entry in "${components[@]}"; do
    IFS=':' read -r img name cmd <<< "$entry"
    file="artifacts/boot/${img}.img4"

    echo "  Sending $name..."
    if ! "$IRECOVERY" -f "$file"; then
        echo "  [✗] Failed to send $img"
        exit 1
    fi

    if [[ -n "$cmd" ]]; then
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
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                    INSTALLATION COMPLETE                       ║"
echo "║                                                              ║"
echo "║  Device should now boot to SpringBoard                       ║"
echo "║  Check serial console for kernel output                      ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
