"""SoC identity for the two iPhone SE (A9) boards.

Samsung N69AP is CPID 0x8000. TSMC N69uAP is CPID 0x8003. The model token
from irecovery is matched in full: ``n69uap`` is not an ``n69ap`` device.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class SocMismatch(ValueError):
    """The connected phone and the artifact set are different A9 variants."""


@dataclass(frozen=True)
class BoardIdentity:
    model: str
    cpid: int
    se_board: str
    donor_board: str

    @property
    def cpid_text(self) -> str:
        return f"0x{self.cpid:04x}"


SAMSUNG = BoardIdentity("n69ap", 0x8000, "n69ap", "j71sap")
TSMC = BoardIdentity("n69uap", 0x8003, "n69uap", "j71tap")
IDENTITIES = (SAMSUNG, TSMC)
A9_CPIDS = frozenset({item.cpid for item in IDENTITIES})
A10_CPIDS = frozenset({0x8010, 0x8011, 0x8012, 0x8015})

_MODEL = re.compile(r"(?im)^\s*MODEL:\s*(\S+)\s*$")
_CPID = re.compile(r"(?im)^\s*CPID:\s*(0x[0-9a-fA-F]+|[0-9]+)\s*$")


def parse_probe(text: str) -> tuple[str, int]:
    """Return the exact MODEL token and CPID. Substring matches are rejected."""

    models = _MODEL.findall(text or "")
    cpids = _CPID.findall(text or "")
    if len(models) != 1 or len(cpids) != 1:
        raise SocMismatch("irecovery probe must contain exactly one MODEL and one CPID")
    model = models[0].lower()
    raw = cpids[0].lower()
    cpid = int(raw, 16) if raw.startswith("0x") else int(raw, 10)
    return model, cpid


def identity_for(model: str, cpid: int) -> BoardIdentity:
    """Require the model token and CPID to name the same board."""

    found = [item for item in IDENTITIES if item.model == model.lower() and item.cpid == cpid]
    if not found:
        raise SocMismatch(
            f"MODEL {model} and CPID 0x{cpid:04x} are not a matched N69AP/N69uAP pair"
        )
    return found[0]


def identity_from_probe(text: str) -> BoardIdentity:
    model, cpid = parse_probe(text)
    try:
        return identity_for(model, cpid)
    except SocMismatch:
        # This TSMC SE prints MODEL n69ap while CPID is 0x8003. The CPID is the
        # silicon; the donor is j71tap, not the Samsung j71sap set.
        if model == SAMSUNG.model and cpid == TSMC.cpid:
            return TSMC
        raise


def require_artifact_identity(artifact_model: str, probe_text: str) -> BoardIdentity:
    """Refuse a Samsung artifact set on a TSMC phone, and the reverse."""

    probe = identity_from_probe(probe_text)
    if artifact_model.lower() != probe.model:
        raise SocMismatch(
            f"artifact set is {artifact_model}, probe is {probe.model} ({probe.cpid_text})"
        )
    return probe


def ibec_needs_go(cpid: int) -> bool:
    """iBEC runs on load on A9. ``go`` is only sent for A10 and later."""

    if cpid in A9_CPIDS:
        return False
    if cpid in A10_CPIDS:
        return True
    raise SocMismatch(f"CPID 0x{cpid:04x} is not an A9 or A10 boot target")


def allow_seputil_load(cpid: int) -> bool:
    """A9 panics if seputil --load runs inside the ramdisk."""

    return cpid not in A9_CPIDS


def allow_ramdisk_sep(cpid: int, data_protection: str) -> bool:
    """Unencrypted DataB copies stock keybags and must boot the ramdisk SEP-off."""

    if data_protection == "none" and cpid in A9_CPIDS:
        return False
    return True


def devicetree_matches(payload: bytes, identity: BoardIdentity) -> bool:
    """The extracted tree must name this board and iPhone8,4, with a trailing NUL."""

    board = identity.se_board.upper().encode() + b"\0"
    # N69uAP contains the letters N69AP; the NUL keeps the tokens distinct.
    other = (TSMC if identity is SAMSUNG else SAMSUNG).se_board.upper().encode() + b"\0"
    return board in payload and b"iPhone8,4\0" in payload and other not in payload


def stock_data_slot(volumes: list[tuple[int, int, str]], configured: int | None, data_b: int) -> int:
    """Pick the existing Data volume by APFS role 0x40.

    ``volumes`` is ``(slot, role, name)``. A configured slot is used only when
    that slot itself has the Data role. Otherwise the single other Data-role
    volume that is not the new DataB is selected.
    """

    by_slot = {slot: (role, name) for slot, role, name in volumes}
    if configured is not None and configured in by_slot and by_slot[configured][0] == 0x40:
        if configured != data_b:
            return configured
    found = [slot for slot, (role, _name) in by_slot.items() if role == 0x40 and slot != data_b]
    if len(found) != 1:
        raise SocMismatch(f"expected one stock Data volume, found slots {found}")
    return found[0]
