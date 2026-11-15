"""SevenUP-style stock SEP handling.

The SEP ROM accepts the complete IMG4 captured from the installed system's
Preboot volume: the original IM4P payload plus the IM4M ticket embedded in the
same container.  Re-wrapping an IPSW ``SEP`` IM4P with a host-extracted ticket
is deliberately not supported here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Iterable


MAX_SEP_SIZE = 16 * 1024 * 1024
VALID_IMAGE_TYPES = ("sepi", "rsep")
_HASH_RE = re.compile(r"(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{96})")


class SepError(ValueError):
    """A captured SEP image is incomplete or does not belong to this device."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SepError(message)


def _tlv(data: bytes, offset: int = 0) -> tuple[int, int, int]:
    _require(0 <= offset and offset + 2 <= len(data), "Truncated DER header")
    tag, length = data[offset : offset + 2]
    _require(tag & 31 != 31, "High-tag DER is not accepted at the IMG4 container level")
    head = offset + 2
    if length & 128:
        count = length & 127
        _require(1 <= count <= 8 and head + count <= len(data), "Invalid DER length")
        length = int.from_bytes(data[head : head + count], "big")
        _require(length >= 128, "Non-canonical DER length")
        head += count
    _require(head + length <= len(data), "Truncated DER body")
    return tag, head, head + length


def _children(data: bytes) -> list[tuple[int, bytes, bytes]]:
    tag, position, end = _tlv(data)
    _require(tag in (0x30, 0xA0) and end == len(data), "Expected a complete DER container")
    result: list[tuple[int, bytes, bytes]] = []
    while position < end:
        child_tag, body, tail = _tlv(data, position)
        result.append((child_tag, data[body:tail], data[position:tail]))
        position = tail
    return result


def split_image(image: bytes) -> tuple[bytes, bytes]:
    """Return the IM4P container and the embedded IM4M ticket bytes."""

    top = _children(image)
    _require(
        len(top) >= 3 and top[0][1] == b"IMG4" and top[1][0] == 0x30,
        "Expected an IMG4 containing IM4P and IM4M",
    )
    payload = top[1][2]
    payload_rows = _children(payload)
    _require(bool(payload_rows) and payload_rows[0][1] == b"IM4P", "Missing IM4P")
    tickets = [item for item in top if item[0] == 0xA0]
    _require(len(tickets) == 1, "Expected exactly one IM4M wrapper")
    ticket = tickets[0][1]
    ticket_rows = _children(ticket)
    _require(bool(ticket_rows) and ticket_rows[0][1] == b"IM4M", "Missing IM4M")
    return payload, ticket


def im4p_type(payload: bytes) -> str:
    rows = _children(payload)
    _require(len(rows) >= 2 and rows[0][1] == b"IM4P" and len(rows[1][1]) == 4, "Invalid IM4P type")
    return rows[1][1].decode("ascii")


def ticket_hash(ticket: bytes) -> str:
    return hashlib.sha384(ticket).hexdigest().upper()


@dataclass(frozen=True)
class SepImage:
    """A validated complete SEP IMG4 and its embedded ticket."""

    payload: bytes
    ticket: bytes
    image_type: str
    image_sha256: str
    image_sha384: str

    @property
    def preboot_hash(self) -> str:
        return ticket_hash(self.ticket)


def validate_image(image: bytes, *, expected_ticket_hash: str | None = None) -> SepImage:
    """Validate the complete container without changing any payload bytes."""

    _require(0 < len(image) < MAX_SEP_SIZE, "Unexpected SEP image size")
    payload, ticket = split_image(image)
    image_type = im4p_type(payload)
    _require(image_type in VALID_IMAGE_TYPES, f"Unexpected SEP IM4P type {image_type!r}")
    if expected_ticket_hash is not None:
        _require(bool(_HASH_RE.fullmatch(expected_ticket_hash)), "Invalid Preboot ticket hash")
        actual_hash = (
            hashlib.sha1(ticket).hexdigest().upper()
            if len(expected_ticket_hash) == 40
            else ticket_hash(ticket)
        )
        _require(
            actual_hash == expected_ticket_hash.upper(),
            "The SEP ticket hash does not match its Preboot directory",
        )
    return SepImage(
        payload=payload,
        ticket=ticket,
        image_type=image_type,
        image_sha256=hashlib.sha256(image).hexdigest(),
        image_sha384=hashlib.sha384(image).hexdigest(),
    )


def preboot_hash_from_path(path: str, *, mount: str = "/mnt8") -> str:
    """Extract a 40- or 96-hex slot name from an exact Preboot SEP path."""

    pattern = re.compile(
        rf"^{re.escape(mount)}/([0-9A-Fa-f]{{40}}|[0-9A-Fa-f]{{96}})/usr/standalone/firmware/sep-firmware\.img4$"
    )
    match = pattern.fullmatch(path.strip())
    _require(match is not None, "SEP source is not a Preboot slot firmware image")
    return match.group(1).upper()


def select_sep(
    candidates: Iterable[str],
    active: str,
    requested: str | None = None,
    *,
    mount: str = "/mnt8",
) -> str:
    """Select exactly one active Preboot SEP image, as SevenUP does."""

    pattern = re.compile(
        rf"^{re.escape(mount)}/([0-9A-Fa-f]{{40}}|[0-9A-Fa-f]{{96}})/usr/standalone/firmware/sep-firmware\.img4$"
    )
    valid: list[tuple[str, str]] = []
    for candidate in candidates:
        path = candidate.strip().replace("\r", "")
        match = pattern.fullmatch(path)
        if match:
            valid.append((match.group(1).upper(), path))

    active = active.strip().replace("\r", "")
    if requested:
        _require(bool(_HASH_RE.fullmatch(requested)), "Preboot hash must contain 40 or 96 hexadecimal characters")
        valid = [row for row in valid if row[0] == requested.upper()]
    elif _HASH_RE.fullmatch(active):
        valid = [row for row in valid if row[0] == active.upper()]

    _require(
        len(valid) == 1,
        "Cannot uniquely identify the active SEP image. Inspect Preboot, then use N69_PREBOOT_HASH explicitly.",
    )
    return valid[0][1]


def parse_img4tool_identity(text: str) -> tuple[int, int]:
    """Read ECID and CPID from ``img4tool -a`` output."""

    ecid = re.search(r"\bECID:\s+ECID:\s+(0[xX][0-9A-Fa-f]+|\d+)", text)
    chip = re.search(r"\bCHIP:\s+CHIP:\s+(0[xX][0-9A-Fa-f]+|\d+)", text)
    _require(ecid is not None and chip is not None, "Ticket identity output is missing ECID or CHIP")
    return int(ecid.group(1), 0), int(chip.group(1), 0)


def parse_irecovery_identity(text: str) -> tuple[int, int]:
    """Read the connected recovery device's ECID and CPID."""

    ecid = re.search(r"(?im)^\s*ECID:\s*(0[xX][0-9A-Fa-f]+|\d+)\b", text)
    chip = re.search(r"(?im)^\s*CPID:\s*(0[xX][0-9A-Fa-f]+|\d+)\b", text)
    _require(ecid is not None and chip is not None, "Recovery device identity is missing ECID or CPID")
    return int(ecid.group(1), 0), int(chip.group(1), 0)


def ticket_identity(ticket: bytes, img4tool: str | Path) -> tuple[int, int]:
    """Validate a ticket with img4tool and return its ECID and CPID."""

    with tempfile.TemporaryDirectory(prefix=".sep-ticket-") as temporary:
        path = Path(temporary) / "ticket.im4m"
        path.write_bytes(ticket)
        path.chmod(0o600)
        result = subprocess.run(
            [str(img4tool), "-a", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    output = (result.stdout or "") + (result.stderr or "")
    _require(result.returncode == 0, f"img4tool could not read the SEP ticket: {output.strip()}")
    return parse_img4tool_identity(output)


def validate_identity(
    ticket: bytes,
    *,
    expected_ecid: int | None = None,
    expected_cpid: int | None = None,
    img4tool: str | Path | None = None,
) -> tuple[int, int] | None:
    """Check the ticket against the live device identity when requested."""

    if expected_ecid is None and expected_cpid is None and img4tool is None:
        return None
    _require(img4tool is not None, "img4tool is required to verify the SEP ticket identity")
    actual_ecid, actual_cpid = ticket_identity(ticket, img4tool)
    if expected_ecid is not None:
        _require(actual_ecid == expected_ecid, "Captured SEP ticket ECID does not match the device")
    if expected_cpid is not None:
        _require(actual_cpid == expected_cpid, "Captured SEP ticket CPID does not match the device")
    return actual_ecid, actual_cpid


def validate_file(
    path: str | Path,
    *,
    expected_ticket_hash: str | None = None,
    expected_ticket: bytes | None = None,
) -> SepImage:
    data = Path(path).read_bytes()
    image = validate_image(data, expected_ticket_hash=expected_ticket_hash)
    if expected_ticket is not None:
        _require(image.ticket == expected_ticket, "Captured SEP ticket does not match state/IM4M.bin")
    return image


def atomic_write(path: str | Path, data: bytes, mode: int = 0o600) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + f".tmp.{os.getpid()}")
    try:
        with temporary.open("wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        temporary.chmod(mode)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def receipt(
    image: SepImage,
    *,
    source: str,
    preboot_hash: str | None = None,
    expected_ecid: int | None = None,
    expected_cpid: int | None = None,
    actual_identity: tuple[int, int] | None = None,
) -> dict[str, object]:
    ecid = expected_ecid if expected_ecid is not None else (actual_identity[0] if actual_identity else None)
    cpid = expected_cpid if expected_cpid is not None else (actual_identity[1] if actual_identity else None)
    return {
        "schema": 1,
        "method": "sevenup-preboot-img4",
        "source": source,
        "preboot_hash": preboot_hash or image.preboot_hash,
        "image_type": image.image_type,
        "image_sha256": image.image_sha256,
        "image_sha384": image.image_sha384,
        "ticket_sha384": image.preboot_hash,
        "ecid": str(ecid) if ecid is not None else None,
        "cpid": str(cpid) if cpid is not None else None,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def write_receipt(path: str | Path, value: dict[str, object]) -> Path:
    return atomic_write(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))
