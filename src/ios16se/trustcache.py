"""Merge a static trust cache with cdhashes of binaries that were actually written."""

from __future__ import annotations

import struct
from pathlib import Path


class TrustCacheError(ValueError):
    """The trust cache does not describe the binaries that were installed."""


# The N69 core install copies plists, an activation ticket and the SEP image.
# It does not replace a Mach-O. The static iPad cache still describes the
# streamed rootfs and cryptex, so the boot may send that cache under this review.
REVIEW_SHARED_DATA = "shared-data-no-macho"


def mark_shared_data(manifest: dict) -> dict:
    """Record that this boot uses the stock cache because no Mach-O was replaced."""

    updated = dict(manifest)
    updated["trustcache_is_stock"] = False
    updated["trustcache_review"] = REVIEW_SHARED_DATA
    return updated


def boot_trustcache_allowed(manifest: dict) -> bool:
    """True only for the reviewed shared-data install, not for an unmarked stock cache."""

    return (
        manifest.get("trustcache_review") == REVIEW_SHARED_DATA
        and manifest.get("trustcache_is_stock") is False
    )


def parse_v1(data: bytes) -> list[bytes]:
    if len(data) < 8:
        raise TrustCacheError("trust cache is truncated")
    version, count = struct.unpack_from("<II", data, 0)
    if version != 1:
        raise TrustCacheError(f"unsupported trust cache version {version}")
    need = 8 + count * 22
    if len(data) < need:
        raise TrustCacheError("trust cache entries are truncated")
    hashes = []
    for index in range(count):
        entry = data[8 + index * 22:8 + (index + 1) * 22]
        hashes.append(entry[:20])
    return hashes


def build_v1(cdhashes: list[bytes]) -> bytes:
    unique = sorted(set(cdhashes))
    for item in unique:
        if len(item) != 20:
            raise TrustCacheError("cdhash must be 20 bytes")
    out = bytearray(struct.pack("<II", 1, len(unique)))
    for item in unique:
        out += item + bytes((2, 0))  # hash type sha256, flags 0
    return bytes(out)


def seal(stock: bytes, extra: list[bytes], *, require_patched: bool) -> bytes:
    """Return a cache that contains the stock entries and ``extra``.

    Refusing an empty extra list is what stops the installer from sending the
    stock iPad cache that was sealed before patch-rootfs and the cryptex.
    """

    if require_patched and not extra:
        raise TrustCacheError("refusing to seal the stock trust cache; patched binaries have no cdhashes")
    merged = parse_v1(stock) + list(extra)
    sealed = build_v1(merged)
    if require_patched and sealed == stock:
        raise TrustCacheError("sealed trust cache is still the stock image")
    return sealed


def cdhash_from_file(path: Path) -> bytes:
    """SHA-256 prefix of a Mach-O code directory, or of the file if unsigned."""

    data = path.read_bytes()
    # Mach-O 64-bit little endian.
    if data[:4] == b"\xcf\xfa\xed\xfe" and len(data) >= 32:
        ncmds = struct.unpack_from("<I", data, 16)[0]
        offset = 32
        for _ in range(ncmds):
            if offset + 8 > len(data):
                break
            cmd, size = struct.unpack_from("<II", data, offset)
            if cmd == 0x1D and size >= 16:  # LC_CODE_SIGNATURE
                dataoff, datasize = struct.unpack_from("<II", data, offset + 8)
                blob = data[dataoff:dataoff + datasize]
                if len(blob) >= 12 and blob[:4] == b"\xfa\xde\x0c\xc0":
                    count = struct.unpack_from(">I", blob, 8)[0]
                    for index in range(count):
                        kind, rel = struct.unpack_from(">II", blob, 12 + index * 8)
                        if kind == 0:  # CSMAGIC code directory slot
                            directory = blob[rel:]
                            import hashlib
                            return hashlib.sha256(directory).digest()[:20]
            offset += size
    import hashlib
    return hashlib.sha256(data).digest()[:20]
