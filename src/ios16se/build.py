"""Build orchestrator for iOS 16 SE tethered boot images.

Matches n56-patcher's core/flow.py exactly:
- iBSS/iBEC decryption via gaster on-device (DFU)
- iBEC patching with iBoot64Patcher (boot-args) + kairos (root-partition)
- KernelCache: extract -> patch -> inject drivers -> mkcomplzss -> append_watchtower -> sign rkrn
- KC roundtrip verification
- DeviceTree: base + transforms -> devicetree-repack -> sign rdtr
- TrustCache: donor + cdhashes -> wrap rtsc
- Full boot flow: gaster pwn/reset -> iBSS -> iBEC -> DT -> TC -> KC -> bootx
"""

from __future__ import annotations

import subprocess
import sys
import os
import shutil
import tempfile
import json
import time
import hashlib
from pathlib import Path
from dataclasses import dataclass

# Python 3.11+ uses tomllib, older versions need tomli
try:
    import tomllib
except ImportError:
    import tomli as tomllib

from typing import Optional

from .img4_tool import Img4Tool, get_img4_tool
from .img4 import Im4pError, inspect as inspect_im4p
from . import sep as captured_sep
from .kc_patch import apply_patches
from .manifest import inventory, find_identity, extract_component
from .devicetree import inspect as inspect_devicetree, diff as diff_devicetree
from .complzss import decompress as decompress_complzss
# from .kernel_patch_inject import inject_n69_drivers  # Disabled for minimal boot


@dataclass
class BuildConfig:
    """Configuration loaded from n69ap_ios16.toml."""
    target: dict
    source_profiles: dict
    boot_args: dict
    volumes: dict
    component_sources: dict
    kernelcache_patches: dict
    watchtower: dict
    trustcache: dict
    im4m: dict
    tools: dict
    output: dict


@dataclass
class BuildArtifacts:
    """Paths to all built artifacts."""
    ibss: Path
    ibec: Path
    devicetree: Path
    trustcache: Path
    sep: Path
    kernelcache: Path
    manifest: Path
    im4m: Path


class BuildError(RuntimeError):
    """Build process error."""
    pass


def selected_identity(config: BuildConfig):
    """Samsung N69AP uses j71sap; TSMC N69uAP uses j71tap. Model match is exact."""

    from .soc import SAMSUNG, TSMC
    target = getattr(config, "target", None) or {}
    model = str(target.get("board_config", "n69ap")).lower()
    if model == SAMSUNG.model:
        return SAMSUNG
    if model == TSMC.model:
        return TSMC
    raise BuildError(f"board_config {model} is not n69ap or n69uap")


def check_device_pwned(irecovery_bin: Path, gaster_bin: Path, board: str) -> None:
    """Ensure a device matching `board` is connected and pwned in DFU mode.

    `gaster decrypt` is a device-bound operation: it sends the iBSS/iBEC
    KBAG to the target's AES engine via checkm8 and reads back the plaintext.
    Without a connected, pwned device it exits non-zero and the build crashes
    with a bare ``CalledProcessError``. Fail fast with a clear message instead.
    """
    probe = subprocess.run([str(irecovery_bin), "-q"], capture_output=True, text=True)
    output = (probe.stdout or "") + (probe.stderr or "")
    from .soc import SocMismatch, identity_from_probe
    try:
        identity = identity_from_probe(output)
    except SocMismatch as error:
        raise BuildError(
            f"Device probe is not an exact N69 model (irecovery -q output: {output.strip()!r}). {error}"
        ) from error
    if identity.model != board.lower():
        raise BuildError(
            f"Device MODEL is {identity.model} ({identity.cpid_text}), not {board}. "
            f"Refusing a Samsung artifact set on a TSMC phone and the reverse."
        )
    if "PWND" not in output:
        print(f"  Device not pwned, running gaster pwn...")
        subprocess.run([str(gaster_bin), "pwn"], check=True, capture_output=False)
        subprocess.run([str(gaster_bin), "reset"], check=True, capture_output=False)
        time.sleep(2)
        # Re-check after pwn
        probe = subprocess.run([str(irecovery_bin), "-q"], capture_output=True, text=True)
        output = (probe.stdout or "") + (probe.stderr or "")
        if "PWND" not in output:
            raise BuildError(
                f"Device did not reach pwned DFU after 'gaster pwn' "
                f"(irecovery -q output: {output.strip()!r})."
            )


def run_gaster(gaster_bin: Path, args: list[str]) -> None:
    """Run a gaster subcommand with robust retry logic."""
    cmd = [str(gaster_bin), *args]

    for attempt in range(3):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return

        detail = (result.stdout or "").strip() or (result.stderr or "").strip()
        print(f"  Gaster {' '.join(args)} failed (attempt {attempt+1}/3): {detail}")

        if "usb_timeout" in detail or "no device" in detail.lower():
            print("  USB connection lost or timed out. Waiting for DFU...")
            time.sleep(5)
            # Try to force a re-pwn if decrypt failed
            subprocess.run([str(gaster_bin), "pwn"], capture_output=True)
            time.sleep(2)
        else:
            # Fatal error (e.g., file not found, bad arguments)
            break

    raise BuildError(f"gaster {' '.join(args)} failed after 3 attempts: {detail}")


def run_checked(cmd: list[str], label: str) -> None:
    """Run a tool, surfacing its real output on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or (result.stdout or "").strip() or "(no output)"
        raise BuildError(f"{label} failed (exit {result.returncode}): {detail}")


def load_config(path: Path) -> BuildConfig:
    """Load build configuration from TOML."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    return BuildConfig(
        target=data["target"],
        source_profiles=data["source_profiles"],
        boot_args=data["boot_args"],
        volumes=data["volumes"],
        component_sources=data["component_sources"],
        kernelcache_patches=data["kernelcache_patches"],
        watchtower=data["watchtower"],
        trustcache=data["trustcache"],
        im4m=data["im4m"],
        tools=data["tools"],
        output=data["output"],
    )


def get_ipsw_path(config: BuildConfig, profile_key: str) -> Path:
    """Resolve IPSW path from config."""
    ipsw_name = config.source_profiles[profile_key]
    return Path("firmware") / ipsw_name


def extract_ipsw_component(
    ipsw_path: Path,
    board: str,
    component: str,
    output: Path,
    restore_behavior: str = "Erase"
) -> Path:
    """Extract a component from IPSW using existing manifest code."""
    report = inventory(str(ipsw_path))
    identity = find_identity(report, board, restore_behavior)

    if output.exists():
        output.unlink()

    return Path(extract_component(str(ipsw_path), identity, component, str(output)))


def decrypt_iboot(ibss_im4p: Path, ibec_im4p: Path, output_dir: Path, gaster_bin: Path, iboot64patcher_bin: Path, board: str, irecovery_bin: Path) -> tuple[Path, Path]:
    """Decrypt iBSS/iBEC using gaster on-device (requires DFU mode).

    n56 flow:
    1. gaster pwn
    2. gaster decrypt ibss.im4p iBSS.dec
    3. gaster decrypt ibec.im4p iBEC.dec
    4. iBoot64Patcher iBSS.dec iBSS.patched (no boot-args for iBSS)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    ibss_dec = output_dir / "iBSS.dec"
    ibec_dec = output_dir / "iBEC.dec"
    ibss_patched = output_dir / "iBSS.patched"

    # Old caches had no provenance and could contain iBSS masquerading as iBEC.
    # Reuse only intermediates produced by this implementation for these inputs.
    cache_file = output_dir / "inputs.json"
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    expected = {"ibss": digest(ibss_im4p), "ibec": digest(ibec_im4p),
                "patcher": digest(iboot64patcher_bin)}
    try:
        cached = json.loads(cache_file.read_text())
        if (cached.get("inputs") == expected and ibss_patched.is_file() and ibec_dec.is_file()
                and ibss_patched.stat().st_size and ibec_dec.stat().st_size
                and cached.get("ibss_output") == digest(ibss_patched)
                and cached.get("ibec_output") == digest(ibec_dec)
                and digest(ibss_patched) != digest(ibec_dec)):
            print("  Using verified-input iBSS/iBEC intermediate cache.")
            return ibss_patched, ibec_dec
    except (OSError, ValueError):
        pass
    cache_file.unlink(missing_ok=True)

    print("  Decrypting iBSS/iBEC via gaster (device must be in DFU)...")
    # gaster decrypt is device-bound: it sends the iBSS/iBEC KBAG to the
    # target's AES engine via checkm8 and reads back the plaintext.
    check_device_pwned(irecovery_bin, gaster_bin, board)

    # Retry logic if decrypt fails: the device sometimes loses its pwned state
    try:
        run_gaster(gaster_bin, ["decrypt", str(ibss_im4p), str(ibss_dec)])
    except BuildError:
        print("  Gaster decrypt failed, retrying pwn...")
        subprocess.run([str(gaster_bin), "pwn"], check=True, capture_output=False)
        run_gaster(gaster_bin, ["decrypt", str(ibss_im4p), str(ibss_dec)])

    try:
        run_gaster(gaster_bin, ["decrypt", str(ibec_im4p), str(ibec_dec)])
    except BuildError:
        print("  Gaster decrypt iBEC failed, retrying pwn...")
        subprocess.run([str(gaster_bin), "pwn"], check=True, capture_output=False)
        run_gaster(gaster_bin, ["decrypt", str(ibec_im4p), str(ibec_dec)])

    print("  Patching iBSS with iBoot64Patcher...")
    run_checked([str(iboot64patcher_bin), str(ibss_dec), str(ibss_patched)],
                "iBoot64Patcher iBSS")

    if digest(ibss_dec) == digest(ibec_dec):
        raise BuildError("Decrypted iBSS and iBEC are identical; refusing invalid boot chain")
    cache_file.write_text(json.dumps({"inputs": expected, "ibss_output": digest(ibss_patched),
                                     "ibec_output": digest(ibec_dec)}, sort_keys=True))
    return ibss_patched, ibec_dec


def patch_ibec(ibec_dec: Path, boot_args: str, root_partition: int,
               iboot64patcher_bin: Path, kairos_bin: Path, work_dir: Path) -> Path:
    """Patch iBEC with boot-args (iBoot64Patcher) + root-matching (kairos).

    n56 flow:
    1. iBoot64Patcher ibec.dec ibec.patched -b \"boot-args\" -n
    2. kairos ibec.patched ibec.kairos -d <root_partition>
    """
    ibec_patched = work_dir / "ibec.patched"
    ibec_kairos = work_dir / "ibec.kairos"

    print(f"  Patching iBEC boot-args: {boot_args}")
    run_checked([
        str(iboot64patcher_bin), str(ibec_dec), str(ibec_patched),
        "-b", boot_args, "-n"
    ], "iBoot64Patcher iBEC")

    print(f"  Patching iBEC root-partition with kairos: disk0s1s{root_partition}")
    run_checked([
        str(kairos_bin), str(ibec_patched), str(ibec_kairos),
        "-d", str(root_partition)
    ], "kairos iBEC")

    return ibec_kairos


def build_ibss(config: BuildConfig, work_dir: Path, im4m: Path) -> Path:
    """Build iBSS image."""
    output = Path(config.output["boot_ibss"])

    print("\n=== Building iBSS ===")

    ipsw = get_ipsw_path(config, config.component_sources["boot_chain"])
    board = config.target["board_config"]

    # Extract iBSS from target IPSW
    ibss_im4p = work_dir / "ibss.im4p"
    extract_ipsw_component(ipsw, board, "iBSS", ibss_im4p)

    ibec_im4p = work_dir / "ibec.im4p"
    extract_ipsw_component(ipsw, board, "iBEC", ibec_im4p)

    # Decrypt iBSS/iBEC using gaster (requires DFU)
    gaster_bin = Path(config.tools["gaster"])
    iboot64patcher_bin = Path(config.tools["iBoot64Patcher"])
    irecovery_bin = Path(config.tools["irecovery"])

    decrypt_dir = work_dir / "decrypt"
    ibss_patched, _ = decrypt_iboot(ibss_im4p, ibec_im4p, decrypt_dir, gaster_bin, iboot64patcher_bin, board, irecovery_bin)

    # Sign with IM4M as ibss
    img4 = get_img4_tool(im4m)
    output = Path(config.output["boot_ibss"])
    output.parent.mkdir(parents=True, exist_ok=True)

    img4.sign(ibss_patched, output, "ibss", "iBSS")

    print(f"  iBSS: {output}")
    return output


def build_ibec(config: BuildConfig, work_dir: Path, im4m: Path) -> Path:
    """Build iBEC image with boot-args."""
    output = Path(config.output["boot_ibec"])

    print("\n=== Building iBEC ===")

    ipsw = get_ipsw_path(config, config.component_sources["boot_chain"])
    board = config.target["board_config"]

    # Extract iBEC from target IPSW
    ibec_im4p = work_dir / "ibec.im4p"
    extract_ipsw_component(ipsw, board, "iBEC", ibec_im4p)

    ibss_im4p = work_dir / "ibss.im4p"
    if not ibss_im4p.is_file():
        extract_ipsw_component(ipsw, board, "iBSS", ibss_im4p)

    # Decrypt iBSS/iBEC (reuse decrypt step from iBSS build)
    gaster_bin = Path(config.tools["gaster"])
    iboot64patcher_bin = Path(config.tools["iBoot64Patcher"])
    kairos_bin = Path(config.tools["kairos"])
    irecovery_bin = Path(config.tools["irecovery"])

    decrypt_dir = work_dir / "decrypt"
    _, ibec_dec = decrypt_iboot(ibss_im4p, ibec_im4p, decrypt_dir, gaster_bin, iboot64patcher_bin, board, irecovery_bin)

    # Patch iBEC: iBoot64Patcher (boot-args) -> kairos (root-part)
    boot_args = config.boot_args["args"]
    root_partition = config.boot_args["root_partition"]

    ibec_final = patch_ibec(ibec_dec, boot_args, root_partition,
                            iboot64patcher_bin, kairos_bin, work_dir)

    # Sign with IM4M as ibec
    img4 = get_img4_tool(im4m)
    output = Path(config.output["boot_ibec"])
    output.parent.mkdir(parents=True, exist_ok=True)

    img4.sign(ibec_final, output, "ibec", "iBoot")

    print(f"  iBEC: {output}")
    return output


def build_devicetree(config: BuildConfig, work_dir: Path, im4m: Path) -> Path:
    """Build DeviceTree."""
    output = Path(config.output["boot_dt"])
    print("\n=== Building DeviceTree ===")

    # Extract N69AP DeviceTree from iOS 15 (native hardware)
    target_ipsw = get_ipsw_path(config, config.component_sources["boot_chain"])
    identity = selected_identity(config)
    target_dt = work_dir / f"dt_{identity.se_board}.im4p"
    extract_ipsw_component(target_ipsw, identity.se_board, "DeviceTree", target_dt)
    try:
        source_info = inspect_im4p(target_dt)
    except Im4pError as error:
        raise BuildError(f"Native N69AP DeviceTree is not a valid IM4P: {error}") from error
    if source_info.image_type != "dtre":
        raise BuildError(f"Native N69AP DeviceTree has unexpected IM4P type {source_info.image_type!r}")

    # Decompress
    img4 = get_img4_tool(im4m)
    target_dt_raw = work_dir / "dt_n69ap.raw"
    if target_dt_raw.exists():
        target_dt_raw.unlink()

    img4.extract(target_dt, target_dt_raw)
    from .devicetree import property_value

    try:
        tree = inspect_devicetree(target_dt_raw)
    except Exception as error:
        raise BuildError(f"Native N69AP DeviceTree payload is invalid: {error}") from error
    compatible = property_value(tree.root, "compatible") or b""
    model = property_value(tree.root, "model") or b""
    board = identity.se_board.upper().encode() + b"\0"
    other = (b"N69uAP\0" if identity.se_board == "n69ap" else b"N69AP\0")
    if board not in compatible or other in compatible or b"iPhone8,4\0" not in compatible or model.rstrip(b"\0") != b"iPhone8,4":
        raise BuildError(f"Extracted DeviceTree is not the {identity.se_board} / iPhone8,4 tree")

    # Sign as rdtr (native N69AP DeviceTree already has correct hardware)
    output = Path(config.output["boot_dt"])
    output.parent.mkdir(parents=True, exist_ok=True)

    staged = output.with_name(output.name + ".tmp")
    if staged.exists():
        staged.unlink()
    img4.sign(target_dt_raw, staged, "rdtr", "DeviceTree")
    roundtrip = work_dir / "dt_n69ap.signed.raw"
    if roundtrip.exists():
        roundtrip.unlink()
    img4.extract(staged, roundtrip)
    if roundtrip.read_bytes() != target_dt_raw.read_bytes():
        staged.unlink(missing_ok=True)
        raise BuildError("Signed N69AP DeviceTree payload failed round-trip validation")
    os.replace(staged, output)

    print(f"  DeviceTree: {output} (native N69AP hardware)")
    return output


def build_trustcache(config: BuildConfig, work_dir: Path, im4m: Path) -> Path:
    """Build TrustCache."""
    output = Path(config.output["boot_tc"])
    print("\n=== Building TrustCache ===")
    donor_ipsw = get_ipsw_path(config, "donor_ipados16")
    tc_im4p = work_dir / "trustcache.im4p"
    extract_ipsw_component(donor_ipsw, selected_identity(config).donor_board, "StaticTrustCache", tc_im4p)
    img4 = get_img4_tool(im4m)
    raw = work_dir / "trustcache.raw"
    img4.extract(tc_im4p, raw)
    if not raw.is_file() or not raw.stat().st_size:
        raise BuildError("Donor trust cache payload is empty")
    output.parent.mkdir(parents=True, exist_ok=True)
    # This image is the stock donor cache. publish_trustcache() must replace it
    # after patch-rootfs and the cryptex, from the binaries that were written.
    img4.wrap_trustcache(raw, output)
    (work_dir / "trustcache.stock.raw").write_bytes(raw.read_bytes())
    print("  TrustCache is still the stock donor cache. Do not boot it.")
    print(f"  TrustCache: {output}")
    return output


def captured_sep_path(config: BuildConfig) -> Path:
    """Return the complete Preboot IMG4 captured by the ramdisk stage."""

    return Path(config.output.get("sep_source", "state/sep-firmware.img4"))


def validate_captured_sep(config: BuildConfig, im4m: Path) -> tuple[Path, captured_sep.SepImage]:
    """Require the stock image and its ticket to agree with the build ticket."""

    source = captured_sep_path(config)
    if not source.is_file() or source.stat().st_size == 0:
        raise BuildError(
            f"Captured stock SEP image not found: {source}\n"
            "Boot the 15.8.8 SSH ramdisk without SEP, then run:\n"
            "  ./n61.sh capture-sep --target n69-ios16\n"
            "Reboot to DFU and boot the ramdisk again before building."
        )
    try:
        ticket = im4m.read_bytes()
        image = captured_sep.validate_file(source, expected_ticket=ticket)
    except (OSError, ValueError) as error:
        raise BuildError(f"Captured stock SEP image is invalid: {error}") from error
    return source, image


def verify_sep_for_device(config: BuildConfig, im4m: Path, output: Path, probe: str) -> None:
    """Reject altered SEP images and tickets belonging to another device."""

    source, image = validate_captured_sep(config, im4m)
    try:
        if output.read_bytes() != source.read_bytes():
            raise BuildError("Boot SEP differs from the captured Preboot image")
        ecid, cpid = captured_sep.parse_irecovery_identity(probe)
        captured_sep.validate_identity(
            image.ticket, expected_ecid=ecid, expected_cpid=cpid,
            img4tool=config.tools.get("img4tool", "ramdisk-main/Darwin/img4tool"),
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise BuildError(f"Cannot verify SEP against the connected device: {error}") from error


def build_sep(config: BuildConfig, work_dir: Path, im4m: Path) -> Path:
    """Copy the complete Preboot IMG4 unchanged; never repackage IPSW SEP."""

    output = Path(config.output.get("boot_sep", "artifacts/boot/sep.img4"))
    print("\n=== Using Captured Stock SEP (SevenUP method) ===")
    source, image = validate_captured_sep(config, im4m)
    output.parent.mkdir(parents=True, exist_ok=True)
    staged = output.with_name(output.name + ".tmp")
    try:
        shutil.copyfile(source, staged)
        staged.chmod(0o600)
        if staged.read_bytes() != source.read_bytes():
            raise BuildError("Captured SEP image changed while copying")
        os.replace(staged, output)
    finally:
        staged.unlink(missing_ok=True)
    print(f"  SEP source: {source}")
    print(f"  SEP ticket: {image.preboot_hash}")
    print(f"  SEP: {output} (bytes unchanged)")
    return output


def build_kernelcache(config: BuildConfig, work_dir: Path, im4m: Path) -> Path:
    """Build patched KernelCache."""
    output = Path(config.output["boot_kc"])
    print("\n=== Building KernelCache ===")

    donor_ipsw = get_ipsw_path(config, "donor_ipados16")

    # Extract donor kernelcache
    identity = selected_identity(config)
    donor_kc_im4p = work_dir / "donor_kc.im4p"
    extract_ipsw_component(donor_ipsw, identity.donor_board, "KernelCache", donor_kc_im4p)

    img4 = get_img4_tool(im4m)
    donor_kc_payload_path = work_dir / "donor_kc.payload"
    img4.extract(donor_kc_im4p, donor_kc_payload_path)

    # Read payload for check
    payload_data = donor_kc_payload_path.read_bytes()
    is_macho = payload_data[:4] in [b'\xce\xfa\xed\xfe', b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf']

    donor_kc_raw = work_dir / "donor_kc.raw"
    if is_macho:
        print("  Payload is already raw Mach-O, skipping decompression.")
        shutil.copy2(donor_kc_payload_path, donor_kc_raw)
    else:
        print("  Payload is compressed, searching for complzss magic...")
        idx = payload_data.find(b'complzss')
        if idx != -1:
            print(f"  Found complzss at offset {idx}, stripping wrapping...")
            stripped_path = work_dir / "donor_kc.stripped"
            stripped_path.write_bytes(payload_data[idx:])
            decompress_complzss(stripped_path, donor_kc_raw)
        else:
            print("  WARNING: No Mach-O magic and no complzss magic found in payload.")
            shutil.copy2(donor_kc_payload_path, donor_kc_raw)

    if not donor_kc_raw.exists() or donor_kc_raw.stat().st_size == 0:
        raise BuildError("Failed to extract kernelcache payload")
    print(f"  Extracted kernel: {donor_kc_raw.stat().st_size} bytes")

    from .kc_patch import patches_for_mode
    mode = os.environ.get("BUILD_MODE", "full")
    patch_names = patches_for_mode(mode, config.kernelcache_patches)
    print(f"  [{mode}] patches: {', '.join(patch_names)}")
    se_kernel = work_dir / "se_kc.raw"
    se_ipsw = get_ipsw_path(config, config.component_sources.get("sep", "target_ios15"))
    se_im4p = work_dir / "se_kc.im4p"
    extract_ipsw_component(se_ipsw, identity.se_board, "KernelCache", se_im4p)
    se_payload = work_dir / "se_kc.payload"
    img4.extract(se_im4p, se_payload)
    se_bytes = se_payload.read_bytes()
    if se_bytes[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
        se_kernel.write_bytes(se_bytes)
    else:
        magic = se_bytes.find(b"complzss")
        if magic < 0:
            raise BuildError("SE kernel payload is not a Mach-O or complzss image")
        stripped = work_dir / "se_kc.stripped"
        stripped.write_bytes(se_bytes[magic:])
        decompress_complzss(stripped, se_kernel)

    patched_kc = apply_patches(
        kernel_path=donor_kc_raw,
        patch_names=patch_names,
        work_dir=work_dir / "kc_patches",
        im4m=im4m,
        config={"se_kernel": se_kernel},
        donor_kc=se_kernel,
    )

    # Inject N69AP drivers (Disabled for now - trying minimal boot)
    # inject_n69_drivers(...)
    final_kc = patched_kc

    # Recompress with complzss (mkcomplzss)
    compressed_kc = work_dir / "final_kc.complzss"
    complzss_bin = Path("tools/complzss/mkcomplzss")
    # The checked-in mkcomplzss is a Linux ELF; prefer the macOS build we compile
    # from the same source (tools/complzss/mkcomplzss.mac) on Darwin.
    if not sys.platform.startswith("darwin"):
        complzss_bin = Path("tools/complzss/mkcomplzss")
    else:
        mac_bin = Path("tools/complzss/mkcomplzss.mac")
        complzss_bin = mac_bin if mac_bin.exists() else complzss_bin
    if complzss_bin.exists():
        print(f"  Compressing kernel with complzss: {complzss_bin}")
        subprocess.run([str(complzss_bin), str(final_kc), str(compressed_kc)], check=True)
        kernel_to_sign = compressed_kc
    else:
        raise BuildError("mkcomplzss not found; cannot produce the required compressed kernel")

    # Append watchtower
    full_kc = work_dir / "kc.full.complzss"
    append_wt = Path("src/ios16se/append_wt_fix.py")
    if append_wt.exists():
        print("  Appending watchtower...")
        env = dict(os.environ)
        env["PYTHONPATH"] = "."
        subprocess.run(["python3", str(append_wt), str(donor_kc_im4p), str(kernel_to_sign), str(full_kc)], env=env, check=True)
        kernel_to_sign = full_kc
    else:
        raise BuildError(f"Watchtower packaging helper not found: {append_wt}")

    # Sign as rkrn using img4tool (handles large payloads)
    output = Path(config.output["boot_kc"])
    output.parent.mkdir(parents=True, exist_ok=True)

    img4_tool = get_img4_tool(im4m)
    img4_tool.sign_kernelcache(kernel_to_sign, output)
    print(f"  KernelCache signed with img4tool + IM4M: {output}")

    # Verify the complete IMG4 payload and embedded ticket
    img4_bin = img4_tool.binary

    # KC Roundtrip Verification
    # Verify the signed KernelCache can be extracted and matches original
    if verify_kc_roundtrip(output, kernel_to_sign, im4m, img4_bin):
        print(f"  ✓ KC Roundtrip verification PASSED")
    else:
        print(f"  ✗ KC Roundtrip verification FAILED - kernel may not boot!")
        raise BuildError("KernelCache roundtrip verification failed")

    return output


def verify_kc_roundtrip(signed_kc: Path, original_raw: Path, im4m: Path, img4_bin: Path) -> bool:
    """Verify the complete IMG4 payload and embedded ticket without decoding.

    The bundled img4 cannot open this large IMG4; parsing its DER directly
    avoids recompression, lossy partial comparisons and tool size limits.
    """
    from .img4 import _element, inspect_bytes
    try:
        data = signed_kc.read_bytes()
        tag, start, end, root_end = _element(data, 0, len(data))
        if tag != 0x30 or root_end != len(data):
            return False
        tag, a, b, cursor = _element(data, start, end)
        if tag != 0x16 or data[a:b] != b"IMG4":
            return False
        im4p_start = cursor
        tag, a, b, cursor = _element(data, cursor, end)
        if tag != 0x30:
            return False
        im4p = data[im4p_start:cursor]
        info = inspect_bytes(im4p)
        if info.image_type != "rkrn" or im4p[info.payload_offset:info.payload_offset + info.payload_length] != original_raw.read_bytes():
            return False
        tag, a, b, cursor = _element(data, cursor, end)
        if tag != 0xa0 or cursor != end or data[a:b] != im4m.read_bytes():
            return False
        return True
    except (OSError, ValueError, IndexError) as error:
        print(f"    Roundtrip: verification error: {error}")
        return False


def build_all(config: BuildConfig, im4m: Optional[Path] = None) -> BuildArtifacts:
    """Build all 5 boot images."""
    work_dir = Path("work/build")
    work_dir.mkdir(parents=True, exist_ok=True)

    # Ensure IM4M exists
    if im4m is None:
        im4m = Path(config.output["im4m"])
    if not im4m.exists():
        raise BuildError(f"IM4M not found: {im4m}")

    # Fail before producing any component with a ticket that differs from the
    # one embedded in the SEP image iBoot will load.
    validate_captured_sep(config, im4m)

    # A failed or interrupted build must never leave a success manifest.
    Path(config.output["boot_manifest"]).unlink(missing_ok=True)
    artifacts = BuildArtifacts(
        ibss=build_ibss(config, work_dir, im4m),
        ibec=build_ibec(config, work_dir, im4m),
        devicetree=build_devicetree(config, work_dir, im4m),
        trustcache=build_trustcache(config, work_dir, im4m),
        sep=build_sep(config, work_dir, im4m),
        kernelcache=build_kernelcache(config, work_dir, im4m),
        manifest=Path(config.output["boot_manifest"]),
        im4m=im4m,
    )

    # Write boot manifest (like n56's boot.json)
    manifest_data = {
        "ibss": str(artifacts.ibss),
        "ibec": str(artifacts.ibec),
        "devicetree": str(artifacts.devicetree),
        "trustcache": str(artifacts.trustcache),
        "sep": str(artifacts.sep),
        "kernelcache": str(artifacts.kernelcache),
        "kernelcache_mode": os.environ.get("BUILD_MODE", "full"),
        "soc_model": selected_identity(config).model,
        "soc_cpid": selected_identity(config).cpid_text,
        "donor_board": selected_identity(config).donor_board,
        "trustcache_is_stock": True,
        "boot_args": config.boot_args["args"],
        "root_partition": config.boot_args["root_partition"],
        "target": config.target["board_config"],
    }
    artifacts.manifest.parent.mkdir(parents=True, exist_ok=True)
    artifacts.manifest.write_text(json.dumps(manifest_data, indent=2))

    print(f"\n=== Build Complete ===")
    print(f"Manifest: {artifacts.manifest}")

    return artifacts


def boot_flow(artifacts: BuildArtifacts, config: BuildConfig) -> int:
    """Execute boot sequence via irecovery.

    n56 boot() sequence:
    1. gaster pwn + reset
    2. irecovery -f iBSS (wait 3s)
    3. irecovery -f iBEC (wait 3s)
    4. irecovery -f DeviceTree -> devicetree (wait 1s)
    5. irecovery -f TrustCache -> firmware (wait 1s)
    6. irecovery -f KernelCache
    7. irecovery -c bootx
    """
    print("\n=== Booting Device ===")

    imgs = [
        (artifacts.ibss, "iBSS", None),
        (artifacts.ibec, "iBEC", "go"),
        (artifacts.devicetree, "DeviceTree", "devicetree"),
        (artifacts.trustcache, "TrustCache", "firmware"),
        (artifacts.sep, "SEP", "rsepfirmware"),
        (artifacts.kernelcache, "KernelCache", None),
    ]
    validate_boot_images((image, name) for image, name, _command in imgs)

    irecovery = Path(config.tools["irecovery"])
    gaster = Path(config.tools["gaster"])

    # Check device identity
    result = subprocess.run([str(irecovery), "-q"], capture_output=True, text=True)
    probe_output = (result.stdout or "") + (result.stderr or "")
    from .soc import SocMismatch, ibec_needs_go, identity_from_probe
    try:
        identity = identity_from_probe(probe_output)
    except SocMismatch as error:
        raise BuildError(f"Device probe is not a matched N69 board: {error}") from error
    manifest_model = json.loads(Path(config.output["boot_manifest"]).read_text()).get("soc_model")
    if manifest_model and manifest_model != identity.model:
        raise BuildError(
            f"artifact set is {manifest_model}, probe is {identity.model} ({identity.cpid_text})"
        )
    boot_manifest = json.loads(Path(config.output["boot_manifest"]).read_text())
    if boot_manifest.get("trustcache_is_stock", True):
        raise BuildError(
            "boot trust cache is still the stock iPad cache sealed before patch-rootfs; refusing to send it"
        )
    verify_sep_for_device(config, artifacts.im4m, artifacts.sep, probe_output)

    # Pwn if needed
    if "PWND" not in result.stdout:
        print("  Device not pwned, running gaster pwn...")
        subprocess.run([str(gaster), "pwn"], check=True)
        subprocess.run([str(gaster), "reset"], check=True)
        time.sleep(1)

    for img, name, cmd in imgs:
        print(f"  Sending {name}...")
        subprocess.run([str(irecovery), "-f", str(img)], check=True)
        if cmd == "go":
            if ibec_needs_go(identity.cpid):
                subprocess.run([str(irecovery), "-c", "go"], check=True)
        elif cmd:
            subprocess.run([str(irecovery), "-c", cmd], check=True)
        time.sleep(3 if name in ("iBSS", "iBEC") else 1)

    # Final bootx
    print("  Executing bootx...")
    subprocess.run([str(irecovery), "-c", "bootx"], check=True)

    print("  Boot sequence sent. Check serial console for kernel output.")
    return 0


def validate_boot_images(images) -> None:
    """Reject an incomplete boot chain before issuing any device command."""
    missing = []
    for image, name in images:
        path = Path(image)
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(f"{name}: {path}")
    if missing:
        raise BuildError("Boot chain image preflight failed: " + "; ".join(missing))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m ios16se.build <config.toml> [--im4m IM4M.bin] [--boot]")
        sys.exit(1)

    config_path = Path("profiles/n69ap_ios16.toml")
    config = load_config(config_path)

    im4m_path = None
    if "--im4m" in sys.argv:
        idx = sys.argv.index("--im4m")
        im4m_path = Path(sys.argv[idx + 1])

    artifacts = build_all(config, im4m_path)

    if "--boot" in sys.argv:
        boot_flow(artifacts, config)
