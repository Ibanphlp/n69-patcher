"""Strict, non-writing preflight for the experimental N69AP tethered boot path.

This module never sends USB commands.  It validates that a future DFU loader
would receive the N69AP DeviceTree and the iPadOS 16 kernel in their intended
roles, rather than silently mixing hardware-specific assets.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import tomllib

from .devicetree import DeviceTreeError, inspect as inspect_devicetree, property_value
from .host import usable_tool
from .img4 import Im4pError, inspect as inspect_im4p
from .macho import MachOError, MH_MAGIC_64, inspect as inspect_macho


class BootPreflightError(ValueError):
    """The local artifacts cannot safely be handed to a tethered boot loader."""


@dataclass(frozen=True)
class BootPreflight:
    target_board: str
    target_model: str
    target_tree_sha256: str
    donor_kernel_sha256: str
    donor_kernel_raw_sha256: str
    policy_path: Path | None
    missing_tools: tuple[str, ...]


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _nul_strings(value: bytes) -> tuple[str, ...]:
    return tuple(item.decode("ascii", "replace") for item in value.split(b"\0") if item)


def _check_policy(profile_path: Path, target_tree_sha256: str, donor_kernel_sha256: str) -> None:
    try:
        policy = tomllib.loads(profile_path.read_text())
        target_expected = policy["reference_artifacts"]["device_tree_sha256"]
        donor_expected = policy["donor_artifacts"]["kernelcache_sha256"]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as error:
        raise BootPreflightError(f"politique d'artefacts invalide : {profile_path}") from error
    if target_tree_sha256 != target_expected:
        raise BootPreflightError("empreinte du DeviceTree N69AP différente de la référence")
    if donor_kernel_sha256 != donor_expected:
        raise BootPreflightError("empreinte du KernelCache donneur différente de la référence")


def preflight(
    target_tree_im4p: str | Path,
    target_tree_raw: str | Path,
    donor_kernel_im4p: str | Path,
    donor_kernel_raw: str | Path,
    policy: str | Path | None = None,
) -> BootPreflight:
    """Validate artifact roles and report host tools still needed for DFU.

    Passing this check does *not* make the image bootable. It only proves that
    the local inputs are correctly assigned for a future tethered loader.
    """

    target_tree_im4p_path = Path(target_tree_im4p)
    target_tree_raw_path = Path(target_tree_raw)
    donor_kernel_im4p_path = Path(donor_kernel_im4p)
    donor_kernel_raw_path = Path(donor_kernel_raw)
    for path in (target_tree_im4p_path, target_tree_raw_path, donor_kernel_im4p_path, donor_kernel_raw_path):
        if not path.is_file():
            raise BootPreflightError(f"artefact requis absent : {path}")

    try:
        target_im4p = inspect_im4p(target_tree_im4p_path)
        donor_im4p = inspect_im4p(donor_kernel_im4p_path)
        tree = inspect_devicetree(target_tree_raw_path)
        macho = inspect_macho(donor_kernel_raw_path)
    except (Im4pError, DeviceTreeError, MachOError) as error:
        raise BootPreflightError(str(error)) from error
    if target_im4p.image_type != "dtre":
        raise BootPreflightError(f"DeviceTree IM4P attendu, reçu : {target_im4p.image_type}")
    if donor_im4p.image_type != "krnl":
        raise BootPreflightError(f"KernelCache IM4P attendu, reçu : {donor_im4p.image_type}")
    if macho.header.cputype != 0x0100000C or macho.header.filetype != 2:
        raise BootPreflightError("KernelCache donneur ARM64 exécutable attendu")
    compatible = property_value(tree.root, "compatible")
    model = property_value(tree.root, "model")
    if compatible is None or model is None:
        raise BootPreflightError("DeviceTree N69AP sans propriétés compatible/model")
    compatible_values = _nul_strings(compatible)
    model_values = _nul_strings(model)
    if "N69AP" not in compatible_values or "iPhone8,4" not in compatible_values:
        raise BootPreflightError(f"DeviceTree cible N69AP attendu, reçu : {', '.join(compatible_values)}")
    if model_values != ("iPhone8,4",):
        raise BootPreflightError(f"modèle DeviceTree inattendu : {', '.join(model_values)}")
    if target_tree_raw_path.read_bytes() == donor_kernel_raw_path.read_bytes():
        raise BootPreflightError("DeviceTree et KernelCache ne peuvent pas être le même fichier")

    target_tree_hash = _sha256(target_tree_im4p_path)
    donor_kernel_hash = _sha256(donor_kernel_im4p_path)
    policy_path = Path(policy) if policy is not None else None
    if policy_path is not None:
        _check_policy(policy_path, target_tree_hash, donor_kernel_hash)
    tools = ("gaster", "palera1n", "irecovery", "pongoterm")
    missing = tuple(tool for tool in tools if usable_tool(tool) is None)
    return BootPreflight(
        target_board="N69AP",
        target_model="iPhone8,4",
        target_tree_sha256=target_tree_hash,
        donor_kernel_sha256=donor_kernel_hash,
        donor_kernel_raw_sha256=_sha256(donor_kernel_raw_path),
        policy_path=policy_path,
        missing_tools=missing,
    )
