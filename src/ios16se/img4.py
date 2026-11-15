"""Small, read-only IM4P reader for inspecting Apple firmware components.

It deliberately handles only definite-length DER IM4P containers. Signature
verification, decryption and image patching are intentionally outside this
module.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class Im4pError(ValueError):
    """A file is not a well-formed IM4P container supported by this reader."""


@dataclass(frozen=True)
class Im4pInfo:
    image_type: str
    description: str
    payload_offset: int
    payload_length: int
    compression: str


def _length(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise Im4pError("longueur DER tronquée")
    first = data[offset]
    if first < 0x80:
        return first, offset + 1
    size = first & 0x7F
    if size == 0 or size > 4 or offset + 1 + size > len(data):
        raise Im4pError("longueur DER non prise en charge")
    value = int.from_bytes(data[offset + 1 : offset + 1 + size], "big")
    if value < 0x80:
        raise Im4pError("longueur DER non canonique")
    return value, offset + 1 + size


def _element(data: bytes, offset: int, limit: int) -> tuple[int, int, int, int]:
    if offset >= limit:
        raise Im4pError("élément DER manquant")
    tag = data[offset]
    length, content_offset = _length(data, offset + 1)
    end = content_offset + length
    if end > limit:
        raise Im4pError("élément DER hors limites")
    return tag, content_offset, end, end


def inspect_bytes(data: bytes) -> Im4pInfo:
    """Locate the payload octet string in an IM4P container."""

    tag, content_start, content_end, root_end = _element(data, 0, len(data))
    if tag != 0x30 or root_end != len(data):
        raise Im4pError("conteneur DER IM4P attendu")

    elements: list[tuple[int, int, int]] = []
    cursor = content_start
    while cursor < content_end:
        child_tag, child_start, child_end, cursor = _element(data, cursor, content_end)
        elements.append((child_tag, child_start, child_end))
    if len(elements) < 4:
        raise Im4pError("en-tête IM4P incomplet")
    if elements[0][0] != 0x16 or data[elements[0][1] : elements[0][2]] != b"IM4P":
        raise Im4pError("marqueur IM4P absent")
    if elements[1][0] != 0x16 or elements[2][0] != 0x16:
        raise Im4pError("métadonnées IM4P invalides")
    payload_tag, payload_start, payload_end = elements[3]
    if payload_tag != 0x04:
        raise Im4pError("charge utile IM4P absente")
    payload = data[payload_start:payload_end]
    if payload.startswith(b"complzss"):
        compression = "complzss"
    elif payload.startswith(b"bvx2"):
        compression = "lzfse"
    else:
        compression = "none-or-unknown"
    return Im4pInfo(
        image_type=data[elements[1][1] : elements[1][2]].decode("ascii", "replace"),
        description=data[elements[2][1] : elements[2][2]].decode("utf-8", "replace"),
        payload_offset=payload_start,
        payload_length=len(payload),
        compression=compression,
    )


def inspect(path: str | Path) -> Im4pInfo:
    return inspect_bytes(Path(path).read_bytes())


def unpack(path: str | Path, output: str | Path) -> Im4pInfo:
    """Write the opaque IM4P payload once; refuse overwrite by design."""

    source = Path(path)
    destination = Path(output)
    if destination.exists():
        raise Im4pError(f"refus d'écraser la charge utile existante : {destination}")
    data = source.read_bytes()
    info = inspect_bytes(data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as file:
        file.write(data[info.payload_offset : info.payload_offset + info.payload_length])
    return info
