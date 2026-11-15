#!/bin/bash
# n69ap_mac_setup.sh
# Downloads macOS/Darwin tools and IPSWs for iPhone SE 1st Gen (N69AP) iOS 16 tethered boot

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "N69AP iOS 16 - Mac Setup"
echo "=========================================="

# Check Intel Mac
if [[ "$(uname)" != "Darwin" ]]; then
    echo "ERROR: This script must run on macOS"
    exit 1
fi

if [[ "$(uname -m)" == "arm64" ]]; then
    echo "ERROR: Apple Silicon not supported. Use Intel Mac."
    exit 1
fi

echo "✓ Intel Mac detected"

# Create directories
mkdir -p tools/SSHRD_Script/Darwin
mkdir -p tools/complzss
mkdir -p ramdisk-main/Darwin
mkdir -p firmware
mkdir -p state
mkdir -p artifacts

echo ""
echo "=== Downloading macOS Tools ==="

# Download from palera1n releases
BASE_URL="https://github.com/palera1n/palera1n/releases/download/v2.0"

for tool in gaster irecovery img4; do
    url="${BASE_URL}/${tool}-macos"
    output="tools/SSHRD_Script/Darwin/${tool}"

    if [[ -x "$output" ]]; then
        echo "  ✓ $tool already exists"
        continue
    fi

    echo "  Downloading $tool..."
    if curl -f -L -o "$output" "$url" 2>/dev/null; then
        chmod +x "$output"
        echo "    ✓ $tool ready"
    else
        echo "    ⚠ Failed - manual download needed from palera1n releases"
    fi
done

# Copy img4 to ramdisk-main/Darwin for compatibility
if [[ -x "tools/SSHRD_Script/Darwin/img4" ]]; then
    cp tools/SSHRD_Script/Darwin/img4 ramdisk-main/Darwin/ 2>/dev/null || true
    chmod +x ramdisk-main/Darwin/img4 2>/dev/null || true
fi

echo ""
echo "=== Downloading IPSWs ==="

# IPSW download URLs
declare -A IPSWS=(
    ["iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw"]="https://github.com/axi0mX/ipwshack/releases/download/v1.0/iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw"
    ["iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw"]="https://github.com/axi0mX/ipwshack/releases/download/v1.0/iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw"
    ["iPhone_4.7_15.8.8_19H422_Restore.ipsw"]="https://github.com/axi0mX/ipwshack/releases/download/v1.0/iPhone_4.7_64bit_15.8.8_19H422_Restore.ipsw"
)

for ipsw in "${!IPSWS[@]}"; do
    url="${IPSWS[$ipsw]}"
    output="firmware/$ipsw"

    if [[ -f "$output" ]]; then
        echo "  ✓ $ipsw already exists"
        continue
    fi

    echo "  Downloading $ipsw..."
    if curl -f -L -o "$output" "$url" 2>/dev/null; then
        echo "    ✓ $ipsw downloaded"
    else
        echo "    ⚠ Failed - try alternative source"
    fi
done

echo ""
echo "=== Status ==="

check_file() {
    local file=$1
    if [[ -f "$file" ]]; then
        local size
        size=$(du -h "$file" | cut -f1)
        echo "  ✓ $file ($size)"
        return 0
    else
        echo "  ✗ $file (MISSING)"
        return 1
    fi
}

echo "Tools:"
check_file "tools/SSHRD_Script/Darwin/irecovery"

echo ""
echo "IPSWs:"
check_file "firmware/iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw"
check_file "firmware/iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw"

echo ""
echo "IM4M:"
check_file "state/IM4M.bin"

echo ""
echo "=========================================="
echo "Setup complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Put iPhone SE in DFU mode"
echo "  2. Run: ./n69ap_ios16_full_install.sh full"
echo ""
echo "Or use one command:"
echo "  ./n69ap_mac_install.sh full"