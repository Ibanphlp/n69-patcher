#!/bin/bash
# Send the already-built N69 boot chain. Does not rewrite the system volume.
# The static iPad trust cache is sent only after it is marked for this install:
# the core rootfs steps copied plists, an activation ticket and the SEP image,
# and did not replace a Mach-O.
set -euo pipefail
cd "$(dirname "$0")"

mark_trustcache() {
    python3 - << 'PY'
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, ".")
from src.ios16se.trustcache import mark_shared_data
manifest_path = Path("artifacts/boot/boot.json")
cache = Path("artifacts/boot/boot_TC.img4")
if not manifest_path.is_file() or not cache.is_file() or cache.stat().st_size == 0:
    raise SystemExit("Missing artifacts/boot/boot.json or boot_TC.img4. Build the boot images first.")
manifest = json.loads(manifest_path.read_text())
text = json.dumps(mark_shared_data(manifest), indent=2) + "\n"
try:
    manifest_path.write_text(text)
except PermissionError:
    # A previous sudo build left this file owned by root.
    print("NEED_ROOT")
    Path("/tmp/n69-boot.json").write_text(text)
else:
    print("Trust cache marked for the shared-data install. No Mach-O was replaced.")
PY
}

result="$(mark_trustcache)"
if [[ "$result" == *NEED_ROOT* ]]; then
    echo "artifacts/boot/boot.json is owned by root. Updating it with sudo."
    sudo cp /tmp/n69-boot.json artifacts/boot/boot.json
    sudo chown "$(id -un)" artifacts/boot/boot.json
    rm -f /tmp/n69-boot.json
    echo "Trust cache marked for the shared-data install. No Mach-O was replaced."
else
    printf '%s\n' "$result"
fi

if [[ -x ramdisk-main/Darwin/irecovery ]]; then
    IRECOVERY="ramdisk-main/Darwin/irecovery"
    GASTER="ramdisk-main/Darwin/gaster"
elif [[ -x tools/SSHRD_Script/Darwin/irecovery ]]; then
    IRECOVERY="tools/SSHRD_Script/Darwin/irecovery"
    GASTER="tools/SSHRD_Script/Darwin/gaster"
else
    echo "irecovery is missing." >&2
    exit 1
fi

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
    [[ -s "artifacts/boot/${img}.img4" ]] || { echo "Missing artifacts/boot/${img}.img4" >&2; exit 1; }
done
# SevenUP refuses a full-iOS iBEC that does not name its root volume.
# A9 uses disk0, not the A10 disk1 path.
grep -a -q 'rd=disk0s1s8' artifacts/boot/boot_iBEC.img4 || {
    echo "boot_iBEC.img4 does not contain rd=disk0s1s8. Refusing to send a chain that would stay in recovery." >&2
    exit 1
}
LOGO="ramdisk-main/sshramdisk/bootlogo.img4"
[[ -s "$LOGO" ]] || { echo "Missing iBEC boot logo: $LOGO" >&2; exit 1; }

echo "The phone must be in DFU. From the ramdisk: hold Power and Home, release Power after about 8 seconds, keep holding Home until the screen stays black."
read -r -p "Press Enter when the screen is black in DFU. "

PROBE="$("$IRECOVERY" -q 2>&1 || true)"
printf '%s\n' "$PROBE"
printf '%s\n' "$PROBE" | grep -qi n69 || { echo "irecovery did not see an N69 in DFU." >&2; exit 1; }
if [[ "$PROBE" != *"PWND"* ]]; then
    "$GASTER" pwn
    "$GASTER" reset
    sleep 2
fi

for entry in "${components[@]}"; do
    IFS=':' read -r img name cmd <<< "$entry"
    echo "Sending ${name}..."
    "$IRECOVERY" -f "artifacts/boot/${img}.img4"
    if [[ "$cmd" == "go" ]]; then
        case "$("$IRECOVERY" -q 2>/dev/null | awk '/CPID/ {print $2}')" in
            0x8010|0x8011|0x8012|0x8015) "$IRECOVERY" -c go ;;
            *) echo "(A9: not sending go)" ;;
        esac
    elif [[ -n "$cmd" ]]; then
        "$IRECOVERY" -c "$cmd"
    fi
    if [[ "$name" == "iBEC" ]]; then
        echo "Sending iBEC boot logo..."
        "$IRECOVERY" -f "$LOGO"
        "$IRECOVERY" -c "setpicture 0x1"
    fi
    # SevenUP waits 2s after iBSS and after iBEC. A9 does not receive go.
    if [[ "$name" == "iBSS" || "$name" == "iBEC" ]]; then
        sleep 2
    else
        sleep 1
    fi
done

echo "Sending bootx..."
"$IRECOVERY" -c bootx
echo "Boot chain sent. A black screen is not Setup.app. The serial log is the result that matters."
