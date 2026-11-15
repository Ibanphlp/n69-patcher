"""Pin and verify the firmware inputs used by the research build."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path

from .manifest import FirmwareInventory, inventory


@dataclass(frozen=True)
class FirmwareRequirement:
    role: str
    product_version: str
    build: str
    sha1_digest: str
    accepted_boards: frozenset[str]


@dataclass(frozen=True)
class Verification:
    requirement: FirmwareRequirement
    report: FirmwareInventory
    actual_sha1: str
    matching_boards: tuple[str, ...]


TARGET_15_8_8 = FirmwareRequirement(
    role="target-ios15",
    product_version="15.8.8",
    build="19H422",
    sha1_digest="cf4309309ba1194915669302db5394c03c635180",
    accepted_boards=frozenset({"n69ap", "n69uap"}),
)

IPAD5_16_7_16 = FirmwareRequirement(
    role="donor-ipados16",
    product_version="16.7.16",
    build="20H392",
    sha1_digest="0c4849129810d926355952e0bd749740304707e8",
    accepted_boards=frozenset({"j71sap", "j72tap"}),
)

REQUIREMENTS = {requirement.role: requirement for requirement in (TARGET_15_8_8, IPAD5_16_7_16)}


class FirmwareVerificationError(ValueError):
    """A local IPSW does not match the pinned research input."""


def file_sha1(path: str | Path) -> str:
    """Hash an IPSW incrementally, so large files do not fill memory."""

    digest = sha1()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(ipsw: str | Path, role: str) -> Verification:
    """Check a downloaded IPSW before any extraction is permitted."""

    try:
        requirement = REQUIREMENTS[role]
    except KeyError as error:
        choices = ", ".join(sorted(REQUIREMENTS))
        raise FirmwareVerificationError(f"rôle inconnu : {role} (attendus : {choices})") from error

    actual_sha1 = file_sha1(ipsw)
    if actual_sha1 != requirement.sha1_digest:
        raise FirmwareVerificationError(
            f"{ipsw}: SHA-1 {actual_sha1}, attendu {requirement.sha1_digest}"
        )
    report = inventory(ipsw)
    if report.product_version != requirement.product_version or report.product_build_version != requirement.build:
        raise FirmwareVerificationError(
            f"{ipsw}: version {report.product_version} ({report.product_build_version}), "
            f"attendue {requirement.product_version} ({requirement.build})"
        )
    matching_boards = tuple(
        sorted({identity.board_config for identity in report.identities} & requirement.accepted_boards)
    )
    if not matching_boards:
        accepted = ", ".join(sorted(requirement.accepted_boards))
        raise FirmwareVerificationError(f"{ipsw}: aucune carte attendue ({accepted})")
    return Verification(requirement, report, actual_sha1, matching_boards)
