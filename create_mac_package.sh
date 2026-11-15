#!/bin/bash
# create_mac_package.sh
# Creates a portable package for Mac installation

set -euo pipefail

PROJECT_DIR="/home/iban/iOS-16-SE"
PACKAGE_NAME="n69ap-ios16-mac-$(date +%Y%m%d).tar.gz"
OUTPUT="/tmp/$PACKAGE_NAME"

echo "=========================================="
echo "Creating Mac Installation Package"
echo "=========================================="

cd "$PROJECT_DIR"

# Create temp directory for clean package
TEMP_DIR="/tmp/n69ap-mac-pkg"
rm -rf "$TEMP_DIR"
mkdir -p "$TEMP_DIR"

# Copy essential files
echo "Gathering files..."
cp -r \
    README_MAC.md \
    MAC_INSTALL_STATUS.md \
    LICENSE \
    n69ap_mac_setup.sh \
    n69ap_ios16_full_install.sh \
    n69ap_mac_install.sh \
    profiles/n69ap_ios16.toml \
    src/ "$TEMP_DIR/" 2>/dev/null || true

# Create symlinks
ln -sf n61-patcher-master/n61.sh "$TEMP_DIR/n61.sh"
cp run_boot.sh run_n69_ios16_boot.py "$TEMP_DIR/" 2>/dev/null || true
cp ios16se_cli.py "$TEMP_DIR/" 2>/dev/null || true

# Copy ramdisk-main structure (without binaries)
mkdir -p "$TEMP_DIR/ramdisk-main/Darwin"
touch "$TEMP_DIR/ramdisk-main/Darwin/.gitkeep"
mkdir -p "$TEMP_DIR/ramdisk-main/Linux"
touch "$TEMP_DIR/ramdisk-main/Linux/.gitkeep"

# Copy tools structure
mkdir -p "$TEMP_DIR/tools/complzss"
touch "$TEMP_DIR/tools/complzss/.gitkeep"

# Create placeholder for IM4M
echo "Creating state directory with placeholder..."
mkdir -p "$TEMP_DIR/state"
echo "# Place your IM4M.bin (6123 bytes) here after extraction" > "$TEMP_DIR/state/.gitkeep"

# Create firmware directory
mkdir -p "$TEMP_DIR/firmware"
echo "# IPSWs will be downloaded by n69ap_mac_setup.sh" > "$TEMP_DIR/firmware/.gitkeep"

# Create artifacts directory
mkdir -p "$TEMP_DIR/artifacts/boot"

echo "Creating tarball..."
cd /tmp
tar -czf "$OUTPUT" -C /tmp n69ap-mac-pkg

echo ""
echo "Package created: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
echo ""
echo "=== On Mac, extract and run ==="
echo "  tar -xzf $PACKAGE_NAME"
echo "  cd n69ap-mac-pkg"
echo "  ./n69ap_mac_setup.sh     # Downloads tools + IPSWs"
echo "  ./n69ap_ios16_full_install.sh full  # Or ./n69ap_mac_install.sh full"
echo ""
echo "⚠️  IPSWs are NOT included - they are 11GB+ and downloaded separately"