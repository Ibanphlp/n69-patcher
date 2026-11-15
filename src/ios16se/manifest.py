"""Read IPSW BuildManifest files without extracting or changing an IPSW."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import plistlib
from zipfile import BadZipFile, ZipFile


class ManifestError(ValueError):
    """An IPSW does not contain a usable BuildManifest.plist."""


@dataclass(frozen=True)
class BuildIdentity:
    """The subset of a BuildIdentity needed to plan a device port."""

    board_config: str
    product_type: str
    build_train: str
    build_number: str
    restore_behavior: str
    variant: str
    components: tuple[str, ...]
    unmapped_components: tuple[str, ...]
    component_paths: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class FirmwareInventory:
    """Device identities and components advertised by one IPSW."""

    path: Path
    product_version: str
    product_build_version: str
    identities: tuple[BuildIdentity, ...]


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _read_manifest(ipsw: Path) -> dict[str, object]:
    try:
        with ZipFile(ipsw) as archive:
            try:
                raw = archive.read("BuildManifest.plist")
            except KeyError as error:
                raise ManifestError(f"{ipsw}: BuildManifest.plist absent") from error
    except FileNotFoundError as error:
        raise ManifestError(f"{ipsw}: fichier introuvable") from error
    except BadZipFile as error:
        raise ManifestError(f"{ipsw}: archive IPSW invalide") from error

    try:
        manifest = plistlib.loads(raw)
    except (plistlib.InvalidFileException, ValueError) as error:
        raise ManifestError(f"{ipsw}: BuildManifest.plist invalide") from error
    if not isinstance(manifest, dict):
        raise ManifestError(f"{ipsw}: manifeste inattendu")
    return manifest


def inventory(ipsw: str | Path) -> FirmwareInventory:
    """Return a deterministic, read-only inventory of an IPSW."""

    path = Path(ipsw)
    manifest = _read_manifest(path)
    raw_identities = manifest.get("BuildIdentities")
    if not isinstance(raw_identities, list):
        raise ManifestError(f"{path}: BuildIdentities absent")

    identities: list[BuildIdentity] = []
    for raw_identity in raw_identities:
        if not isinstance(raw_identity, dict):
            continue
        info = raw_identity.get("Info")
        components = raw_identity.get("Manifest")
        if not isinstance(info, dict) or not isinstance(components, dict):
            continue
        paths: list[tuple[str, str]] = []
        unmapped: list[str] = []
        for name, raw_component in components.items():
            if not isinstance(raw_component, dict):
                unmapped.append(str(name))
                continue
            component_info = raw_component.get("Info")
            if not isinstance(component_info, dict):
                unmapped.append(str(name))
                continue
            component_path = _text(component_info.get("Path"))
            if component_path:
                paths.append((str(name), component_path))
            else:
                unmapped.append(str(name))
        identities.append(
            BuildIdentity(
                board_config=_text(info.get("DeviceClass")).lower(),
                product_type=_text(info.get("ProductType")),
                build_train=_text(info.get("BuildTrain")),
                build_number=_text(info.get("BuildNumber")),
                restore_behavior=_text(info.get("RestoreBehavior")),
                variant=_text(info.get("Variant")),
                components=tuple(sorted(name for name, _path in paths)),
                unmapped_components=tuple(sorted(unmapped)),
                component_paths=tuple(sorted(paths)),
            )
        )

    if not identities:
        raise ManifestError(f"{path}: aucune identité de build lisible")
    return FirmwareInventory(
        path=path,
        product_version=_text(manifest.get("ProductVersion")),
        product_build_version=_text(manifest.get("ProductBuildVersion")),
        identities=tuple(identities),
    )


def find_identity(
    report: FirmwareInventory,
    board_config: str,
    restore_behavior: str | None = None,
) -> BuildIdentity:
    """Find a board identity, optionally selecting its IPSW restore behaviour."""

    wanted = board_config.lower()
    matches = [identity for identity in report.identities if identity.board_config == wanted]
    if restore_behavior is not None:
        behavior = restore_behavior.lower()
        matches = [identity for identity in matches if identity.restore_behavior.lower() == behavior]
    if matches:
        return matches[0]

    choices = sorted(
        {
            f"{identity.board_config}/{identity.restore_behavior or 'sans-comportement'}"
            for identity in report.identities
        }
    )
    if restore_behavior is None:
        raise ManifestError(f"{report.path}: {wanted} absent (disponibles : {', '.join(choices)})")
    raise ManifestError(
        f"{report.path}: {wanted}/{restore_behavior} absent "
        f"(disponibles : {', '.join(choices)})"
    )


def component_delta(target: BuildIdentity, donor: BuildIdentity) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return components only in the target, then only in the donor."""

    target_components = set(target.components)
    donor_components = set(donor.components)
    return (
        tuple(sorted(target_components - donor_components)),
        tuple(sorted(donor_components - target_components)),
    )


def component_path(identity: BuildIdentity, component: str) -> str:
    """Find an archive path for one named component in an identity."""

    for name, path in identity.component_paths:
        if name == component:
            return path
    raise ManifestError(f"{identity.board_config}: composant sans chemin : {component}")


def extract_component(
    ipsw: str | Path,
    identity: BuildIdentity,
    component: str,
    output: str | Path,
) -> Path:
    """Copy exactly one manifest-declared component without overwriting output."""

    source_path = component_path(identity, component)
    if Path(source_path).is_absolute() or ".." in Path(source_path).parts:
        raise ManifestError(f"chemin de composant non sûr : {source_path}")
    destination = Path(output)
    if destination.exists():
        raise ManifestError(f"refus d'écraser l'artefact existant : {destination}")
    try:
        with ZipFile(ipsw) as archive:
            try:
                content = archive.read(source_path)
            except KeyError as error:
                raise ManifestError(f"{ipsw}: composant absent de l'archive : {source_path}") from error
    except BadZipFile as error:
        raise ManifestError(f"{ipsw}: archive IPSW invalide") from error

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    try:
        with temporary.open("xb") as file:
            file.write(content)
        os.replace(temporary, destination)
    except FileExistsError as error:
        raise ManifestError(f"artefact partiel existant : {temporary}") from error
    return destination
