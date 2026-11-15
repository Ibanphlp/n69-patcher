#!/bin/bash
# download_darwin_tools.sh
# Downloads macOS/Darwin tools for n69ap iOS 16 tethered boot
# Based on n56-patcher flash.sh tool detection pattern

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "Downloading macOS/Darwin Tools"
echo "=========================================="

# Create tool directories
mkdir -p tools/SSHRD_Script/Darwin
mkdir -p tools/complzss

# Download tools from palera1n releases
echo ""
echo "Downloading tools from palera1n GitHub releases..."

BASE_URL="https://github.com/palera1n/palera1n/releases/download/v2.0"

# Check if curl supports progress bar
CURL_PROGRESS="-# -L"

# Download tools (try v2.0 first, then v3.0)
download_tool() {
    local tool=$1
    local url1="${BASE_URL}/${tool}-macos"
    local url2="${BASE_URL%v2.0}v3.0/${tool}-macos"
    local output="tools/SSHRD_Script/Darwin/$tool"

    if [[ -x "$output" ]]; then
        echo "  ✓ $tool already exists"
        return 0
    fi

    echo "  Downloading $tool..."
    for url in "$url1" "$url2"; do
        if curl -f -o "$output" "$url" 2>/dev/null; then
            chmod +x "$output"
            echo "    ✓ $tool downloaded"
            return 0
        fi
    done

    echo "    ⚠ $tool - failed to download from GitHub"
    echo "    Please download manually from: https://github.com/palera1n/palera1n/releases"
    return 1
}

# Download essential tools
TOOLS=("gaster" "irecovery" "img4")
FAILED=0

for tool in "${TOOLS[@]}"; do
    download_tool "$tool" || FAILED=1
done

# Download optional tools
for tool in "iBoot64Patcher" "kairos"; do
    download_tool "$tool" || echo "    (optional) $tool not available"
done

# Build mkcomplzss if source exists
if [[ -f "tools/complzss/complzss.c" ]] || [[ -f "tools/complzss/mkcomplzss.c" ]]; then
    echo ""
    echo "Building mkcomplzss..."
    cd tools/complzss
    if command -v gcc &>/dev/null; then
        SOURCES=("mkcomplzss.c" "complzss.c")
        for src in "${SOURCES[@]}"; do
            if [[ -f "$src" ]]; then
                gcc -o mkcomplzss "$src" 2>/dev/null && echo "  ✓ mkcomplzss built from $src" && break
            fi
        done
    fi
    cd "$SCRIPT_DIR"
fi

# Verify tools
echo ""
echo "Tool verification:"
for tool in "${TOOLS[@]}"; do
    if [[ -x "tools/SSHRD_Script/Darwin/$tool" ]]; then
        echo "  ✓ $tool"
    else
        echo "  ✗ $tool (missing)"
        FAILED=1
    fi
done

if [[ $FAILED -eq 1 ]]; then
    echo ""
    echo "ERROR: Some tools failed to download"
    echo "You can manually download Darwin binaries from:"
    echo "  https://github.com/palera1n/palera1n/releases"
    echo ""
    echo "Required files:"
    echo "  tools/SSHRD_Script/Darwin/gaster"
    echo "  tools/SSHRD_Script/Darwin/irecovery"
    echo "  tools/SSHRD_Script/Darwin/img4"
    exit 1
fi

echo ""
ok "All tools ready!"

# Helper for green output
ok() { echo -e "\033[1;32m[✓]\033[0m $*"; }