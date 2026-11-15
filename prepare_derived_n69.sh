#!/bin/bash
# prepare_derived_n69.sh
# Runs the n69 boot build and maps results to the locations expected by n61.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Preparing Derived Inputs for N69-iOS16 ==="

# 1. Run the Python build pipeline to get decrypted/patched components
# This uses the device for gaster decrypt
echo "[1/2] Running build pipeline (requires pwned DFU device)..."
python3 run_n69_ios16_boot.py --im4m state/IM4M.bin --mode "${1:-full}" --build-only

# 2. Create directory structure in n61-patcher-master
echo "[2/2] Mapping artifacts to n61-patcher-master/ipsw/cache/derived/..."

DERIVED_DIR="n61-patcher-master/ipsw/cache/derived"
EXT_N69="$DERIVED_DIR/extracted/n69ap_ios12"
EXT_J96="$DERIVED_DIR/extracted/j96ap_ios16"

mkdir -p "$EXT_N69"
mkdir -p "$EXT_J96"

# Map the files
# iBSS.patched -> iBSS.n69.patched
cp work/build/decrypt/iBSS.patched "$EXT_N69/iBSS.n69.patched"
# iBEC.dec -> iBEC.n69.dec
cp work/build/decrypt/iBEC.dec "$EXT_N69/iBEC.n69.dec"
# final_kc.complzss -> kernelcache.release.ipad5
cp work/build/final_kc.complzss "$EXT_J96/kernelcache.release.ipad5"
# trustcache.im4p -> 087-86634-044.dmg.trustcache
cp work/build/trustcache.im4p "$EXT_J96/087-86634-044.dmg.trustcache"

echo "✓ Derived inputs ready"
ls -R "$DERIVED_DIR"
