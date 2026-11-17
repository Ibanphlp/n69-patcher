#!/bin/bash
# Send the already-built N69 boot chain. Does not rewrite the system volume.
# The static iPad trust cache is sent only after it is marked for this install:
# the core rootfs steps copied plists, an activation ticket and the SEP image,
# and did not replace a Mach-O.
set -euo pipefail
cd "$(dirname "$0")"

BOOT_DIR="${N69_BOOT_DIR:-artifacts/boot}"
IM4M="${N69_IM4M:-state/IM4M.bin}"
IMG4="${N69_IMG4_BIN:-ramdisk-main/Darwin/img4}"
IMG4TOOL="${N69_IMG4TOOL_BIN:-ramdisk-main/Darwin/img4tool}"
LOG_DIR="${N69_LOG_DIR:-logs}"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/boot-$(date '+%Y%m%d-%H%M%S').log"
IRECOVERY_OUTPUT=""

run_irecovery() {
    local step="$1" output rc
    shift
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] irecovery: $step ($*)" | tee -a "$LOG_FILE"
    if output="$("$IRECOVERY" "$@" 2>&1)"; then
        rc=0
    else
        rc=$?
    fi
    IRECOVERY_OUTPUT="$output"
    if [[ -n "$output" ]]; then
        printf '%s\n' "$output" | tee -a "$LOG_FILE"
    fi
    if (( rc != 0 )); then
        echo "ERROR: irecovery step '$step' failed (exit $rc). See $LOG_FILE" | tee -a "$LOG_FILE" >&2
        return "$rc"
    fi
}

preflight_img4() {
    local image="$1" label="$2" output ticket payload
    output="$(mktemp -d)"
    ticket="$output/ticket.im4m"
    payload="$output/payload.im4p"
    if ! "$IMG4TOOL" -e -m "$ticket" -p "$payload" "$image" >"$output/tool.log" 2>&1; then
        echo "IMG4 preflight failed for $label: img4tool could not extract the original IMG4/IM4M." >&2
        cat "$output/tool.log" >&2
        rm -rf "$output"
        return 1
    fi
    if ! cmp -s "$ticket" "$IM4M"; then
        echo "IMG4 preflight failed for $label: embedded IM4M does not match $IM4M." >&2
        rm -rf "$output"
        return 1
    fi
    if [[ "$label" == "KernelCache" ]] &&
        ! python3 - "$payload" <<'PY'
import sys
from pathlib import Path
from src.ios16se.complzss import decompress_bytes
from src.ios16se.img4 import inspect_bytes
try:
    im4p = Path(sys.argv[1]).read_bytes()
    info = inspect_bytes(im4p)
    payload = im4p[info.payload_offset:info.payload_offset + info.payload_length]
    compressed, kernel = decompress_bytes(payload)
    tail_size = len(payload) - 0x180 - compressed.compressed_size
    if tail_size != 0xc000:
        raise ValueError(f"Watchtower tail has {tail_size:#x} bytes; expected 0xc000")
except Exception as error:
    raise SystemExit(f"invalid KernelCache complzss stream: {error}")
print(f"KernelCache complzss OK: {len(kernel)} decoded bytes")
PY
    then
        echo "IMG4 preflight failed for KernelCache: refusing an invalid compressed kernel." >&2
        rm -rf "$output"
        return 1
    fi
    if [[ "$label" != "KernelCache" ]] &&
        ! "$IMG4" -i "$image" -M "$IM4M" -o "$output/repacked.img4" >>"$output/tool.log" 2>&1; then
        echo "IMG4 preflight failed for $label ($image)." >&2
        cat "$output/tool.log" >&2
        rm -rf "$output"
        return 1
    fi
    rm -rf "$output"
    echo "IMG4 preflight OK: $label"
}

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
    [[ -s "$BOOT_DIR/${img}.img4" ]] || { echo "Missing $BOOT_DIR/${img}.img4" >&2; exit 1; }
done
[[ -s "$IM4M" ]] || { echo "Missing $IM4M" >&2; exit 1; }
preflight_img4 "$BOOT_DIR/boot_KC.img4" KernelCache
preflight_img4 "$BOOT_DIR/boot_DT.img4" DeviceTree
preflight_img4 "$BOOT_DIR/boot_TC.img4" TrustCache
preflight_img4 "$BOOT_DIR/sep.img4" SEP
if [[ "${N69_PREFLIGHT_ONLY:-0}" == "1" ]]; then
    exit 0
fi
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
# SevenUP refuses a full-iOS iBEC that does not name its root volume.
# A9 uses disk0, not the A10 disk1 path.
grep -a -q 'rd=disk0s1s8' "$BOOT_DIR/boot_iBEC.img4" || {
    echo "boot_iBEC.img4 does not contain rd=disk0s1s8. Refusing to send a chain that would stay in recovery." >&2
    exit 1
}
grep -a -F -q 'panic-wait-forever=1' "$BOOT_DIR/boot_iBEC.img4" || {
    echo "boot_iBEC.img4 lacks panic-wait-forever=1. Rebuild the full boot chain (not --kernel-only) before this diagnostic boot." >&2
    exit 1
}
LOGO_SOURCE="ramdisk-main/other/bootlogo.im4p"
[[ -s "$LOGO_SOURCE" ]] || { echo "Missing iBEC boot logo source: $LOGO_SOURCE" >&2; exit 1; }
LOGO_TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$LOGO_TMP_DIR"' EXIT
LOGO="$LOGO_TMP_DIR/bootlogo.img4"
if ! "$IMG4" -i "$LOGO_SOURCE" -A -T rlgo -M "$IM4M" -o "$LOGO"; then
    echo "Could not sign the iBEC boot logo with $IM4M." >&2
    exit 1
fi
preflight_img4 "$LOGO" BootLogo

echo "The phone must be in DFU. From the ramdisk: hold Power and Home, release Power after about 8 seconds, keep holding Home until the screen stays black."
read -r -p "Press Enter when the screen is black in DFU. "

run_irecovery "DFU device probe" -q || exit $?
PROBE="$IRECOVERY_OUTPUT"
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
    run_irecovery "send ${name}" -f "$BOOT_DIR/${img}.img4" || exit $?
    if [[ "$cmd" == "go" ]]; then
        run_irecovery "query CPID before iBEC go" -q || exit $?
        case "$(printf '%s\n' "$IRECOVERY_OUTPUT" | awk '/CPID/ {print $2}')" in
            0x8010|0x8011|0x8012|0x8015) run_irecovery "iBEC go" -c go || exit $? ;;
            *) echo "(A9: not sending go)" ;;
        esac
    elif [[ -n "$cmd" ]]; then
        run_irecovery "${name} command: ${cmd}" -c "$cmd" || exit $?
    fi
    if [[ "$name" == "iBEC" ]]; then
        echo "Sending iBEC boot logo..."
        run_irecovery "send iBEC boot logo" -f "$LOGO" || exit $?
        run_irecovery "show iBEC boot logo" -c "setpicture 0x1" || exit $?
    fi
    # SevenUP waits 2s after iBSS and after iBEC. A9 does not receive go.
    if [[ "$name" == "iBSS" || "$name" == "iBEC" ]]; then
        sleep 2
    else
        sleep 1
    fi
done

echo "Sending bootx..."
run_irecovery "bootx" -c bootx || exit $?
echo "Boot chain sent. A black screen is not Setup.app. The serial log is the result that matters."
