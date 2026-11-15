"""Decoder for the legacy Apple ``complzss`` kernelcache envelope."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import zlib


HEADER_SIZE = 0x180
MAGIC = b"complzss"
RING_SIZE = 4096
MATCH_LIMIT = 18


class ComplzssError(ValueError):
    """The legacy compressed payload is malformed or does not validate."""


@dataclass(frozen=True)
class ComplzssInfo:
    checksum: int
    uncompressed_size: int
    compressed_size: int
    version: int


def _u32_be(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def inspect_bytes(data: bytes) -> ComplzssInfo:
    if len(data) < HEADER_SIZE or data[:8] != MAGIC:
        raise ComplzssError("enveloppe complzss attendue")
    info = ComplzssInfo(
        checksum=_u32_be(data, 8),
        uncompressed_size=_u32_be(data, 12),
        compressed_size=_u32_be(data, 16),
        version=_u32_be(data, 20),
    )
    if info.uncompressed_size == 0 or info.compressed_size == 0:
        raise ComplzssError("tailles complzss invalides")
    if HEADER_SIZE + info.compressed_size > len(data):
        raise ComplzssError("flux complzss tronqué")
    return info


def decompress_bytes(data: bytes) -> tuple[ComplzssInfo, bytes]:
    """Decode one checked LZSS payload with bounded input and output sizes."""

    info = inspect_bytes(data)
    compressed = memoryview(data)[HEADER_SIZE : HEADER_SIZE + info.compressed_size]
    ring = bytearray(RING_SIZE + MATCH_LIMIT - 1)
    ring[: RING_SIZE - MATCH_LIMIT] = b" " * (RING_SIZE - MATCH_LIMIT)
    ring_position = RING_SIZE - MATCH_LIMIT
    output = bytearray()
    input_position = 0
    flags = 0

    def take_byte() -> int:
        nonlocal input_position
        if input_position >= len(compressed):
            raise ComplzssError("fin prématurée du flux complzss")
        value = compressed[input_position]
        input_position += 1
        return value

    while len(output) < info.uncompressed_size:
        flags >>= 1
        if not flags & 0x100:
            flags = take_byte() | 0xFF00
        if flags & 1:
            value = take_byte()
            output.append(value)
            ring[ring_position] = value
            ring_position = (ring_position + 1) & (RING_SIZE - 1)
            continue

        index = take_byte()
        descriptor = take_byte()
        index |= (descriptor & 0xF0) << 4
        count = (descriptor & 0x0F) + 3
        for offset in range(count):
            value = ring[(index + offset) & (RING_SIZE - 1)]
            output.append(value)
            ring[ring_position] = value
            ring_position = (ring_position + 1) & (RING_SIZE - 1)
            if len(output) == info.uncompressed_size:
                break

    if input_position != info.compressed_size:
        raise ComplzssError(
            f"taille de flux incohérente : {input_position}, attendue {info.compressed_size}"
        )
    if zlib.adler32(output) != info.checksum:
        raise ComplzssError("contrôle Adler-32 complzss invalide")
    return info, bytes(output)


def decompress(path: str | Path, output: str | Path) -> ComplzssInfo:
    """Decode a local payload once and allow overwrite if output exists."""

    destination = Path(output)
    if destination.exists():
        destination.unlink()
    info, decoded = decompress_bytes(Path(path).read_bytes())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(decoded)
    return info
