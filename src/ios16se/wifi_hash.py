"""Rewrite Wi-Fi hash-store records to this phone's own firmware.

The iPad 16.7.16 kernel approves ``C-4355__s-C0/kristoff.{trx,clmb,txcb}``.
Those digests are replaced with the hashes of the stock SE files
``C-43452__s-A2/hans.{trx,clmb,txcb}``. Verification stays enabled: this
module never rewrites ``mov w0, #0; ret`` into ``mov w0, #1; ret``.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

SE_FIRMWARE = {
    "trx": "C-43452__s-A2/hans.trx",
    "clmb": "C-43452__s-A2/hans.clmb",
    "txcb": "C-43452__s-A2/hans.txcb",
}
IPAD_RECORDS = {
    "trx": "C-4355__s-C0/kristoff.trx",
    "clmb": "C-4355__s-C0/kristoff.clmb",
    "txcb": "C-4355__s-C0/kristoff.txcb",
}
HASH_MAGIC = 0x68617368
MOV_W0_0_RET = bytes.fromhex("00008052c0035fd6")
MOV_W0_1_RET = bytes.fromhex("20008052c0035fd6")


class WifiHashError(RuntimeError):
    """The hash store could not be rewritten without disabling verification."""


def path_key(name: str) -> bytes:
    return hashlib.sha256(name.encode("utf-8")).digest()


def _sha(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


class _KernelView:
    def __init__(self, path: Path):
        import sys
        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / "SevenUP-main" / "src"))
        from iphone7_port.kc import KC

        self.kc = KC(path)
        self.data = bytearray(self.kc.data)

    def read(self, va: int, size: int) -> bytes:
        return bytes(self.data[self.kc.offset(va):self.kc.offset(va) + size])

    def header_va(self) -> int:
        magic = struct.pack("<Q", HASH_MAGIC)
        found = []
        start = 0
        while True:
            index = self.kc.data.find(magic, start)
            if index < 0:
                break
            found.append(index)
            start = index + 1
        if len(found) != 1:
            raise WifiHashError(f"hash-store magic count is {len(found)}")
        fileoff = found[0]
        for segment in self.kc.segments:
            base = self.kc.base + segment["offset"]
            if base <= fileoff < base + segment["size"]:
                return segment["addr"] + (fileoff - base) - 0x10
        raise WifiHashError("hash-store magic is not inside a kernel segment")


def read_index(view: _KernelView, header: int | None = None) -> dict[bytes, dict]:
    header = view.header_va() if header is None else header
    if struct.unpack("<Q", view.read(header + 0x10, 8))[0] != HASH_MAGIC:
        raise WifiHashError("hash-store magic mismatch")
    count, array = struct.unpack("<QQ", view.read(header + 0x20, 16))
    if count <= 0 or count > 4096:
        raise WifiHashError(f"unexpected hash-store count {count}")
    result = {}
    for index in range(count):
        entry = struct.unpack("<Q", view.read(array + index * 8, 8))[0]
        key_blob, value = struct.unpack("<QQ", view.read(entry, 16))
        blob, flags, length, aux = struct.unpack("<QIIQ", view.read(value, 24))
        if struct.unpack("<I", view.read(key_blob, 4))[0] != 32:
            raise WifiHashError("filename digest blob is not 32 bytes")
        if struct.unpack("<I", view.read(blob, 4))[0] != 32:
            raise WifiHashError("firmware digest blob is not 32 bytes")
        key = view.read(key_blob + 4, 32)
        if key in result:
            raise WifiHashError("duplicate filename key")
        result[key] = {
            "value": value,
            "blob": blob,
            "flags": flags,
            "length": length,
            "aux": aux,
            "digest": view.read(blob + 4, 32),
        }
    return result


def load_firmware(directory: Path) -> dict[str, bytes]:
    files = {}
    for kind, name in SE_FIRMWARE.items():
        path = directory / Path(name).name
        if not path.is_file():
            path = directory / name
        if not path.is_file():
            raise WifiHashError(f"stock SE Wi-Fi file missing: {name}")
        files[kind] = path.read_bytes()
    return files


def rewrite(kernel_path: Path, firmware: dict[str, bytes], output: Path) -> dict:
    """Point the three iPad firmware records at ``firmware`` bytes."""

    view = _KernelView(kernel_path)
    before_bytes = bytes(view.data)
    if MOV_W0_1_RET in before_bytes and MOV_W0_0_RET not in before_bytes:
        raise WifiHashError("kernel already contains a forced-success return")
    header = view.header_va()
    index = read_index(view, header)
    edits = []
    for kind, ipad_name in IPAD_RECORDS.items():
        key = path_key(ipad_name)
        if key not in index:
            raise WifiHashError(f"hash store has no record for {ipad_name}")
        record = index[key]
        digest = _sha(firmware[kind])
        blob = record["blob"]
        value = record["value"]
        struct.pack_into("<I", view.data, view.kc.offset(blob), 32)
        view.data[view.kc.offset(blob + 4):view.kc.offset(blob + 36)] = digest
        struct.pack_into("<I", view.data, view.kc.offset(value + 12), len(firmware[kind]))
        edits.append({
            "record": ipad_name,
            "se_file": SE_FIRMWARE[kind],
            "old_digest": record["digest"].hex(),
            "new_digest": digest.hex(),
            "length": len(firmware[kind]),
        })
    if view.data.count(MOV_W0_1_RET) > before_bytes.count(MOV_W0_1_RET):
        raise WifiHashError("rewrite introduced mov w0, #1; ret")
    # The forbidden transformation is specifically 0 -> 1 at a former failure site.
    for offset in range(len(view.data) - 7):
        if before_bytes[offset:offset + 8] == MOV_W0_0_RET and view.data[offset:offset + 8] == MOV_W0_1_RET:
            raise WifiHashError("mov w0, #0; ret was rewritten to return 1")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(view.data)
    after = read_index(_KernelView(output), header)
    for kind, ipad_name in IPAD_RECORDS.items():
        record = after[path_key(ipad_name)]
        if record["digest"] != _sha(firmware[kind]) or record["length"] != len(firmware[kind]):
            raise WifiHashError(f"record {ipad_name} does not match the SE firmware")
    return {"header": hex(header), "edits": edits, "verification_left_enabled": True}


def apply_wifi(ctx, firmware_dir: Path) -> Path:
    files = load_firmware(firmware_dir)
    output = ctx.work_dir / "kernel_wifi_hash.raw"
    rewrite(ctx.kernel_path, files, output)
    if output.read_bytes() == ctx.kernel_path.read_bytes():
        raise WifiHashError("Wi-Fi hash rewrite left the kernel unchanged")
    return output
