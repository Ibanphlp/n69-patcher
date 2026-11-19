"""SevenUP footprint transplants with reviewed cross-version bindings.

The engine is ``iphone7_port.kernel_transplant.apply``. It still rejects a
dependency whose UUID or section layout differs. Those sites are bound only
when one of these reviews succeeds, and the build fails if the recorded
bytes are not still there:

* the same offset in the iOS 16 dependency has the same 16 bytes
* a masked instruction fingerprint is unique in that dependency
* the iPad D2365 PMU has one const-slot neighborhood with the same symbols
"""

from __future__ import annotations

import collections
import struct
import sys
from pathlib import Path

from .touch_audit import SPIN, compare_spin69

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "SevenUP-main" / "src"))

from iphone7_port.core import PortError  # noqa: E402
from iphone7_port.kc import KC  # noqa: E402
from iphone7_port.kernel_chestnut import (  # noqa: E402
    adrp_page, array_end, encode_adrp, relocations, signed, xml_value,
)
from iphone7_port.kernel_transplant import apply as sevenup_apply  # noqa: E402
from iphone7_port.kernel_transplant import owner, owner_ranges  # noqa: E402

PARTS = ("__TEXT", "__TEXT_EXEC", "__DATA", "__DATA_CONST")
POOLS = {
    "__TEXT": "__PRELINK_TEXT",
    "__TEXT_EXEC": "__PLK_TEXT_EXEC",
    "__DATA": "__PRELINK_DATA",
    "__DATA_CONST": "__PLK_DATA_CONST",
}
MARKER = b"AVE ERROR: UnMapYUVInputFromCSID CurrentClient NULL"
DISPLAY_FOOTPRINT = "com.apple.driver.AppleDPDisplay"
PINOT = "com.apple.driver.ApplePinotLCD"
PMU_FOOTPRINT = "com.apple.driver.AppleD2365PMU"
PMU_DONOR = "com.apple.driver.AppleD2255PMU"
SPI = "com.apple.driver.AppleMultitouchSPI"
SPIC = "com.apple.driver.AppleMultitouchSPIC1F15"
CHESTNUT = "com.apple.driver.AppleChestnutDisplayPMU"
DIALOG = "com.apple.driver.AppleDialogPMU"


def _mask(word: int) -> int:
    if word & 0x9F000000 == 0x90000000:
        return word & 0x9F00001F
    if word & 0x7C000000 == 0x14000000:
        return word & 0xFC000000
    if word & 0x9F000000 == 0x10000000:
        return word & 0x9F00001F
    if word & 0x3B000000 == 0x18000000:
        return word & 0xFF00001F
    return word


def _sig(raw: bytes, offset: int, count: int) -> tuple:
    words = struct.unpack_from("<" + "I" * count, raw, offset)
    return tuple(_mask(word) for word in words)


class BindingBook:
    """Reviewed donor-VA to target-VA map for one kernel pair."""

    def __init__(self, donor: Path, target: Path):
        self.donor = KC(donor)
        self.target = KC(target)
        self.proved: dict[int, int] = {}
        self.checks: list[tuple[int, int, bytes, bytes]] = []
        self._exec_index: dict[str, tuple] = {}
        self._ranges = owner_ranges(self.donor)
        self._target_ranges = owner_ranges(self.target)
        self._symbols = None

    def symbols(self):
        if self._symbols is None:
            from iphone7_port.kernel_transplant import fast_symbols
            self._symbols = (fast_symbols(self.donor), fast_symbols(self.target))
        return self._symbols

    def remember(self, source: int, dest: int, source_bytes: bytes, dest_bytes: bytes) -> None:
        if source in self.proved and self.proved[source] != dest:
            raise PortError(f"conflicting binding for {source:#x}")
        if self.donor.read(source, len(source_bytes)) != source_bytes:
            raise PortError(f"donor bytes changed at {source:#x}")
        if self.target.read(dest, len(dest_bytes)) != dest_bytes:
            raise PortError(f"binding bytes do not match at {dest:#x}")
        self.proved[source] = dest
        self.checks.append((source, dest, source_bytes, dest_bytes))

    def assert_all(self) -> None:
        for source, dest, source_bytes, dest_bytes in self.checks:
            if self.donor.read(source, len(source_bytes)) != source_bytes:
                raise PortError(f"binding donor bytes do not match at {source:#x}")
            if self.target.read(dest, len(dest_bytes)) != dest_bytes:
                raise PortError(f"binding target bytes do not match at {dest:#x}")

    def _segment(self, kernel: KC, bundle: str, name: str) -> dict:
        return next(segment for segment in kernel.ksegments(bundle) if segment["name"] == name)

    def _last_nonzero(self, kernel: KC, segment: dict) -> int:
        raw = kernel.read(segment["addr"], segment["size"])
        last = -8
        for offset in range(0, len(raw) - 7, 8):
            if struct.unpack_from("<Q", raw, offset)[0]:
                last = offset
        return last

    def _exec(self, bundle: str):
        if bundle not in self._exec_index:
            segment = self._segment(self.target, bundle, "__TEXT_EXEC")
            raw = self.target.read(segment["addr"], segment["size"])
            tables = {}
            for count in (32, 24, 16, 12):
                table = collections.defaultdict(list)
                limit = len(raw) - count * 4
                for offset in range(0, limit + 1, 4):
                    table[_sig(raw, offset, count)].append(offset)
                tables[count] = table
            self._exec_index[bundle] = (segment, raw, tables)
        return self._exec_index[bundle]

    def _imports(self, bundle: str) -> list[tuple[int, str, str]]:
        segments = {segment["name"]: segment for segment in self.donor.ksegments(bundle)}
        base, slots = relocations(self.donor)
        (donor_names, _donor_by_addr), (target_names, _target_by_addr) = self.symbols()
        found = []
        seen = set()
        for slot in slots:
            address = base + slot
            if not any(segment["addr"] <= address < segment["addr"] + segment["size"]
                       for segment in segments.values()):
                continue
            value = struct.unpack("<Q", self.donor.read(address, 8))[0]
            if value in seen:
                continue
            seen.add(value)
            if any(segment["addr"] <= value < segment["addr"] + segment["size"]
                   for segment in segments.values()):
                continue
            names = _donor_by_addr.get(value, [])
            if names and all(name in target_names for name in names) and len({target_names[name] for name in names}) == 1:
                continue
            dependency, segment_name = owner(self._ranges, value)
            if dependency == "XNU":
                continue
            found.append((value, dependency, segment_name))
        return found

    def _same_offset(self, value: int, dependency: str, segment_name: str) -> int | None:
        source = self._segment(self.donor, dependency, segment_name)
        dest = self._segment(self.target, dependency, segment_name)
        offset = value - source["addr"]
        if offset < 0 or offset + 16 > min(source["size"], dest["size"]):
            return None
        source_bytes = self.donor.read(value, 16)
        dest_va = dest["addr"] + offset
        dest_bytes = self.target.read(dest_va, 16)
        if source_bytes != dest_bytes:
            return None
        if source_bytes == bytes(16):
            if self._last_nonzero(self.donor, source) != self._last_nonzero(self.target, dest):
                return None
            if offset <= self._last_nonzero(self.donor, source):
                return None
        return dest_va

    def _fingerprint(self, value: int, dependency: str) -> int | None:
        source = self._segment(self.donor, dependency, "__TEXT_EXEC")
        offset = value - source["addr"]
        raw = self.donor.read(source["addr"], source["size"])
        segment, _host, tables = self._exec(dependency)
        if offset < 0:
            return None
        for count in (32, 24, 16, 12):
            if offset + count * 4 > len(raw):
                continue
            hits = tables[count].get(_sig(raw, offset, count), [])
            if len(hits) == 1:
                return segment["addr"] + hits[0]
        return None

    def _kind(self, ranges, by_addr, value: int, bundle: str) -> str:
        if not value:
            return "0"
        names = by_addr.get(value)
        if names:
            return "X:" + names[0]
        dependency, segment_name = owner(ranges, value)
        if dependency == bundle:
            return "self"
        return f"{str(dependency).rsplit('.', 1)[-1]}:{segment_name}"

    def _sibling(self, value: int, dependency: str, segment_name: str) -> int | None:
        """Match one const pointer through the iPad D2365 PMU's symbol neighborhood."""

        donor_bundle = PMU_DONOR
        target_bundle = PMU_FOOTPRINT
        if donor_bundle not in self.donor.kexts or target_bundle not in self.target.kexts:
            return None
        def words(kernel: KC, bundle: str):
            segment = self._segment(kernel, bundle, "__DATA_CONST")
            raw = kernel.read(segment["addr"], segment["size"])
            count = len(raw) // 8
            return struct.unpack("<" + "Q" * count, raw[:count * 8])

        donor_words = words(self.donor, donor_bundle)
        target_words = words(self.target, target_bundle)
        (_dn, donor_by), (_tn, target_by) = self.symbols()
        donor_kinds = [self._kind(self._ranges, donor_by, item, donor_bundle) for item in donor_words]
        target_kinds = [self._kind(self._target_ranges, target_by, item, target_bundle) for item in target_words]
        positions = [index for index, item in enumerate(donor_words) if item == value]
        if not positions:
            return None
        for width in (2, 3, 4, 5, 6, 8):
            hits = set()
            index = collections.defaultdict(list)
            for cursor in range(width, len(target_kinds) - width):
                signature = tuple(target_kinds[cursor - width:cursor]) + ("*",) + tuple(
                    target_kinds[cursor + 1:cursor + 1 + width])
                index[signature].append(cursor)
            for cursor in positions:
                if cursor < width or cursor + width >= len(donor_kinds):
                    continue
                signature = tuple(donor_kinds[cursor - width:cursor]) + ("*",) + tuple(
                    donor_kinds[cursor + 1:cursor + 1 + width])
                for hit in index.get(signature, []):
                    candidate = target_words[hit]
                    owner_name, owner_segment = owner(self._target_ranges, candidate)
                    if owner_name == dependency and owner_segment == segment_name:
                        hits.add(candidate)
            if len(hits) == 1:
                return next(iter(hits))
        return None

    def _symbol_object(self, value: int, dependency: str, segment_name: str) -> int | None:
        """Match a const object by slots that already have a reviewed address."""

        (_donor_names, donor_by), (target_names, _target_by) = self.symbols()
        try:
            slots = struct.unpack("<8Q", self.donor.read(value, 64))
        except ValueError:
            return None
        constraints = []
        for index, slot in enumerate(slots):
            if slot == 0:
                constraints.append((index, 0))
                continue
            if slot in self.proved:
                constraints.append((index, self.proved[slot]))
                continue
            names = donor_by.get(slot) or []
            if names and names[0] in target_names:
                constraints.append((index, target_names[names[0]]))
                continue
            owner_name, owner_segment = owner(self._ranges, slot)
            if owner_name not in ("XNU", None) and owner_segment:
                dest = self._same_offset(slot, owner_name, owner_segment)
                if dest is not None:
                    constraints.append((index, dest))
        concrete = [item for item in constraints if item[1]]
        if not concrete:
            return None
        segment = self._segment(self.target, dependency, segment_name)
        raw = self.target.read(segment["addr"], segment["size"])
        hits = []
        width = 8 * 8
        for offset in range(0, len(raw) - width + 1, 8):
            words = struct.unpack_from("<8Q", raw, offset)
            if all(words[index] == expected for index, expected in constraints):
                hits.append(segment["addr"] + offset)
                if len(hits) > 1:
                    return None
        if len(hits) == 1:
            return hits[0]
        return None

    def _string_index(self, kernel: KC, dependency: str):
        cache = getattr(self, "_string_cache", None)
        if cache is None:
            cache = self._string_cache = {}
        key = (id(kernel), dependency)
        if key in cache:
            return cache[key]
        from iphone7_port.kernel_chestnut import adrp_page, signed
        segments = {segment["name"]: segment for segment in kernel.ksegments(dependency)}
        text = kernel.read(segments["__TEXT"]["addr"], segments["__TEXT"]["size"])
        raw = kernel.read(segments["__TEXT_EXEC"]["addr"], segments["__TEXT_EXEC"]["size"])
        strings = {}
        cursor = 0
        while cursor < len(text):
            if 32 <= text[cursor] < 127:
                end = cursor
                while end < len(text) and 32 <= text[end] < 127:
                    end += 1
                if end - cursor >= 6 and end < len(text) and text[end] == 0:
                    strings[segments["__TEXT"]["addr"] + cursor] = text[cursor:end].decode()
                cursor = end + 1
            else:
                cursor += 1
        base = segments["__TEXT_EXEC"]["addr"]
        count = len(raw) // 4
        words = struct.unpack("<" + "I" * count, raw[:count * 4])
        starts = {base}
        for index, word in enumerate(words):
            pc = base + index * 4
            if word & 0x7C000000 == 0x14000000:
                target = pc + signed(word & 0x3FFFFFF, 26) * 4
                if base <= target < base + len(raw):
                    starts.add(target)
        starts = sorted(starts)
        refs = collections.defaultdict(list)
        for index, word in enumerate(words):
            if word & 0x9F000000 != 0x90000000 or index + 1 >= count:
                continue
            nxt = words[index + 1]
            if nxt & 0xFFC00000 == 0x91000000 and (nxt >> 5) & 31 == word & 31:
                addr = adrp_page(word, base + index * 4) + ((nxt >> 10) & 0xFFF)
                if addr in strings:
                    func = starts[__import__("bisect").bisect_right(starts, base + index * 4) - 1]
                    refs[func].append(strings[addr])
        cache[key] = (starts, refs, base + len(raw))
        return cache[key]

    def _string_object(self, value: int, dependency: str) -> int | None:
        """Match a const object whose code slot sits in a string-unique function."""

        try:
            slots = struct.unpack("<8Q", self.donor.read(value, 64))
        except ValueError:
            return None
        donor_starts, donor_refs, _end = self._string_index(self.donor, dependency)
        target_starts, target_refs, _target_end = self._string_index(self.target, dependency)
        import bisect
        code_index = None
        signature = None
        segment = self._segment(self.donor, dependency, "__TEXT_EXEC")
        for index, slot in enumerate(slots):
            if not segment["addr"] <= slot < segment["addr"] + segment["size"]:
                continue
            func = donor_starts[bisect.bisect_right(donor_starts, slot) - 1]
            signature = tuple(donor_refs.get(func) or [])
            if signature:
                code_index = index
                break
        if code_index is None or not signature:
            return None
        funcs = [func for func, names in target_refs.items() if tuple(names) == signature]
        if len(funcs) != 1:
            return None
        func = funcs[0]
        position = target_starts.index(func)
        limit = target_starts[position + 1] if position + 1 < len(target_starts) else _target_end
        (_names, donor_by), (target_names, _target_by) = self.symbols()
        anchor = None
        anchor_index = None
        for index, slot in enumerate(slots):
            names = donor_by.get(slot) or []
            if names and names[0] in target_names:
                anchor = target_names[names[0]]
                anchor_index = index
                break
        host = self._segment(self.target, dependency, "__DATA_CONST")
        raw = self.target.read(host["addr"], host["size"])
        hits = []
        for offset in range(0, len(raw) - 63, 8):
            words = struct.unpack_from("<8Q", raw, offset)
            if anchor is not None and words[anchor_index] != anchor:
                continue
            if func <= words[code_index] < limit:
                hits.append(host["addr"] + offset)
                if len(hits) > 1:
                    return None
        if len(hits) == 1:
            return hits[0]
        return None

    def bind_bundle(self, bundle: str) -> dict[int, int]:
        """Return private bindings for every cross-version import of ``bundle``."""

        self.assert_all()
        pending = list(self._imports(bundle))
        missing = []
        progressed = True
        while progressed:
            progressed = False
            missing = []
            for value, dependency, segment_name in pending:
                if value in self.proved:
                    continue
                dest = self._same_offset(value, dependency, segment_name)
                if dest is None and segment_name == "__TEXT_EXEC":
                    dest = self._fingerprint(value, dependency)
                if dest is None:
                    dest = self._sibling(value, dependency, segment_name)
                if dest is None and segment_name in ("__DATA", "__DATA_CONST"):
                    dest = self._symbol_object(value, dependency, segment_name)
                if dest is None and segment_name == "__DATA_CONST":
                    dest = self._string_object(value, dependency)
                if dest is None:
                    missing.append((hex(value), dependency.rsplit(".", 1)[-1], segment_name))
                    continue
                self.remember(value, dest, self.donor.read(value, 16), self.target.read(dest, 16))
                progressed = True
        if missing:
            detail = ", ".join(f"{va} {name} {segment}" for va, name, segment in missing[:12])
            raise PortError(f"{bundle} has {len(missing)} unbound imports: {detail}")
        self.assert_all()
        return {value: self.proved[value] for value, _dep, _seg in self._imports(bundle)}


def _output_path(work: Path, name: str) -> Path:
    path = work / f"kernel_{name}.raw"
    if (work / "kernelcache.raw").is_file():
        path.write_bytes((work / "kernelcache.raw").read_bytes())
    return path


def transplant_footprint(source: Path, donor: Path, work: Path, old: str, new: str,
                         book: BindingBook) -> Path:
    """Replace ``old`` with ``new`` using SevenUP's engine and reviewed bindings."""

    bindings = book.bind_bundle(new)
    if source.read_bytes().count(MARKER) != 1:
        raise PortError("transplant marker is not unique in the target kernel")
    _blob, report = sevenup_apply(
        source, donor, work, old, new, MARKER, TARGET_MARKER=MARKER,
        private_bindings=bindings,
        private_review={"bindings": len(bindings), "bundle": new},
    )
    output = _output_path(work, new.rsplit(".", 1)[-1])
    validate_transplant(source, donor, output, old, new)
    if report["rebound_pointer_count"] <= 0:
        raise PortError(f"{new} transplant rebound no pointers")
    return output


def validate_transplant(source: Path, donor: Path, output: Path, old: str, new: str) -> None:
    """Reject an unchanged kernel, a missing bundle, or a donor-space relocation."""

    source_bytes = source.read_bytes()
    output_bytes = output.read_bytes()
    if output_bytes == source_bytes:
        raise PortError(f"{new} transplant output is byte-identical to the input")
    if len(output_bytes) != len(source_bytes):
        raise PortError(f"{new} transplant changed the kernel size")
    produced = KC(output)
    if new not in produced.kexts:
        raise PortError(f"prelink is missing transplanted bundle {new}")
    if old != new and old in produced.kexts:
        raise PortError(f"footprint {old} is still present after transplanting {new}")
    donor_kc = KC(donor)
    donor_segments = [segment for segment in donor_kc.ksegments(new) if segment["size"]]
    base, slots = relocations(produced)
    new_segments = [segment for segment in produced.ksegments(new) if segment["size"]]

    def inside(segments, value: int) -> bool:
        return any(segment["addr"] <= value < segment["addr"] + segment["size"] for segment in segments)

    for slot in slots:
        address = base + slot
        if not inside(new_segments, address):
            continue
        value = struct.unpack("<Q", produced.read(address, 8))[0]
        if inside(donor_segments, value) and not inside(new_segments, value):
            raise PortError(f"external relocation still points into the donor address space at {value:#x}")


def _place(kernel: KC, part: str, _donor_addr: int, size: int, extra: int = 0) -> int:
    """Put a segment in the zero tail of its prelink pool, 16-byte aligned."""

    pool = next(segment for segment in kernel.segments if segment["name"] == POOLS[part])
    occupied = []
    for name, entry in kernel.kexts.items():
        if not entry.get("_PrelinkExecutableLoadAddr"):
            continue
        for segment in kernel.ksegments(name):
            if segment["name"] == part and segment["size"]:
                occupied.append(segment["addr"] + segment["size"])
    end = max(occupied) if occupied else pool["addr"]
    start = (end + 15) & ~15
    limit = pool["addr"] + pool["size"]
    if start + size + extra > limit:
        raise PortError(f"no free prelink space for Chestnut {part}")
    if kernel.read(start, size + extra) != bytes(size + extra):
        raise PortError(f"Chestnut {part} destination is not empty")
    return start


def transplant_chestnut(source: Path, donor: Path, work: Path, book: BindingBook) -> Path:
    """Append AppleChestnutDisplayPMU into empty prelink space, before touch."""

    kernel, donor_kc = KC(source), KC(donor)
    if CHESTNUT in kernel.kexts:
        raise PortError("Chestnut is already present")
    bindings = book.bind_bundle(CHESTNUT)
    donor_segments = {segment["name"]: segment for segment in donor_kc.ksegments(CHESTNUT)}
    plan = {}
    for part in PARTS:
        extra = 512 if part == "__TEXT_EXEC" else 0
        plan[part] = _place(kernel, part, donor_segments[part]["addr"], donor_segments[part]["size"], extra)

    def local(value: int) -> int | None:
        for part, segment in donor_segments.items():
            if segment["addr"] <= value < segment["addr"] + segment["size"]:
                return plan[part] + value - segment["addr"]
        return None

    chunks = {part: bytearray(donor_kc.read(segment["addr"], segment["size"]))
              for part, segment in donor_segments.items() if part in PARTS}
    base, donor_slots = relocations(donor_kc)
    _kbase, existing_slots = relocations(kernel)
    new_slots = []
    for slot in donor_slots:
        address = base + slot
        moved = local(address)
        if moved is None:
            continue
        value = struct.unpack("<Q", donor_kc.read(address, 8))[0]
        rewritten = local(value)
        if rewritten is None:
            if value not in bindings:
                (_names, by_addr), (target_names, _target_by) = book.symbols()
                names = by_addr.get(value, [])
                if not (names and all(name in target_names for name in names)
                        and len({target_names[name] for name in names}) == 1):
                    raise PortError(f"Chestnut import {value:#x} is not bound")
                rewritten = target_names[names[0]]
            else:
                rewritten = bindings[value]
        part = next(name for name, segment in donor_segments.items()
                    if segment["addr"] <= address < segment["addr"] + segment["size"])
        struct.pack_into("<Q", chunks[part], address - donor_segments[part]["addr"], rewritten)
        new_slots.append(moved - _kbase)
    kmod = donor_kc.kexts[CHESTNUT]["_PrelinkKmodInfo"]
    kmod_local = local(kmod)
    if kmod_local is None:
        raise PortError("Chestnut kmod is not inside the transplanted segments")
    part = next(name for name, segment in donor_segments.items()
                if segment["addr"] <= kmod + 156 < segment["addr"] + segment["size"])
    struct.pack_into("<Q", chunks[part], kmod + 156 - donor_segments[part]["addr"], plan["__TEXT"])
    code = chunks["__TEXT_EXEC"]
    old_pc = donor_segments["__TEXT_EXEC"]["addr"]
    new_pc = plan["__TEXT_EXEC"]
    from iphone7_port.kernel_base import branch
    veneer = new_pc + ((len(code) + 3) & ~3)
    veneer_limit = new_pc + len(code) + 512
    for offset in range(0, len(code), 4):
        word = struct.unpack_from("<I", code, offset)[0]
        pc = old_pc + offset
        moved_pc = new_pc + offset
        if word & 0x9F000000 == 0x90000000:
            page = adrp_page(word, pc)
            owners = [name for name, segment in donor_segments.items()
                      if name in plan and (segment["addr"] & ~4095) <= page
                      < ((segment["addr"] + segment["size"] + 4095) & ~4095)]
            if len(owners) != 1:
                raise PortError(f"Chestnut ADRP page {page:#x} is ambiguous")
            target_page = page + plan[owners[0]] - donor_segments[owners[0]]["addr"]
            register = word & 31
            nxt = struct.unpack_from("<I", code, offset + 4)[0] if offset + 4 < donor_segments["__TEXT_EXEC"]["size"] else 0
            if target_page % 4096 == 0:
                struct.pack_into("<I", code, offset, encode_adrp(word, moved_pc, target_page))
            elif nxt & 0xFFC00000 == 0x91000000 and (nxt >> 5) & 31 == register and nxt & 31 == register:
                addend = (nxt >> 10) & 4095
                struct.pack_into("<I", code, offset, encode_adrp(word, moved_pc, (target_page + addend) & ~4095))
                struct.pack_into("<I", code, offset + 4, (nxt & ~(4095 << 10)) | (((target_page + addend) & 4095) << 10))
            else:
                if veneer + 12 > veneer_limit:
                    raise PortError("Chestnut ADRP veneer overflow")
                add = 0x91000000 | ((target_page & 4095) << 10) | (register << 5) | register
                veneer_bytes = struct.pack("<II", encode_adrp(word, veneer, target_page & ~4095), add) + branch(veneer + 8, moved_pc + 4)
                # Veneers live past the copied code, in the reserved tail.
                tail_off = veneer - new_pc
                code.extend(b"\0" * (tail_off + 12 - len(code)))
                code[tail_off:tail_off + 12] = veneer_bytes
                struct.pack_into("<I", code, offset, int.from_bytes(branch(moved_pc, veneer), "little"))
                veneer += 12
        elif word & 0x7C000000 == 0x14000000:
            target = pc + signed(word & 0x3FFFFFF, 26) * 4
            if not old_pc <= target < old_pc + donor_segments["__TEXT_EXEC"]["size"]:
                raise PortError("Chestnut has an external direct branch")
    header = chunks["__TEXT"]
    for cmd, absolute, _size in __import__("iphone7_port.kernel_chestnut", fromlist=["commands"]).commands(
            donor_kc, donor_segments["__TEXT"]["addr"]):
        if cmd != 25:
            continue
        cursor = absolute - donor_kc.offset(donor_segments["__TEXT"]["addr"])
        name = header[cursor + 8:cursor + 24].split(b"\0")[0].decode()
        if name not in plan:
            continue
        value = plan[name]
        seg_size = len(code) if name == "__TEXT_EXEC" else donor_segments[name]["size"]
        struct.pack_into("<QQ", header, cursor + 24, value, seg_size)
        struct.pack_into("<QQ", header, cursor + 40, value - plan["__TEXT"], seg_size)
        count = struct.unpack_from("<I", header, cursor + 64)[0]
        for index in range(count):
            section = cursor + 72 + index * 80
            old = struct.unpack_from("<Q", header, section + 32)[0]
            mapped = local(old)
            if mapped is None:
                raise PortError("Chestnut section is outside the donor")
            struct.pack_into("<Q", header, section + 32, mapped)
    entry = {key: donor_kc.kexts[CHESTNUT][key] for key in (
        "CFBundleIdentifier", "CFBundleName", "CFBundleExecutable", "CFBundleVersion",
        "CFBundlePackageType", "OSBundleLibraries", "IOKitPersonalities", "_PrelinkBundlePath",
        "_PrelinkExecutableSize") if key in donor_kc.kexts[CHESTNUT]}
    entry.update(_PrelinkExecutableLoadAddr=plan["__TEXT"], _PrelinkExecutableSourceAddr=plan["__TEXT"],
                 _PrelinkKmodInfo=kmod_local)
    xml = kernel.xml[:array_end(kernel.xml)] + xml_value(entry).encode() + kernel.xml[array_end(kernel.xml):]
    offsets = existing_slots + new_slots
    if len(offsets) != len(set(offsets)):
        raise PortError("Chestnut slide slots collide with the existing kernel")
    packed = struct.pack("<I", len(offsets)) + struct.pack("<" + "I" * len(offsets), *offsets)
    import re
    import base64
    xml, count = re.subn(
        rb"(<key>_PrelinkLinkKASLROffsets</key><data>)[A-Za-z0-9+/=\s]*(</data>)",
        lambda match: match[1] + base64.b64encode(packed) + match[2], xml)
    if count != 1:
        raise PortError("Chestnut could not update the KASLR offset array")
    info = next(segment for segment in kernel.segments if segment["name"] == "__PRELINK_INFO")
    if len(xml) > info["size"]:
        raise PortError("Chestnut prelink XML does not fit")
    out = bytearray(kernel.data)
    for part, address in plan.items():
        blob = chunks[part]
        out[kernel.offset(address):kernel.offset(address) + len(blob)] = blob
    at = kernel.offset(info["addr"])
    out[at:at + info["size"]] = xml.ljust(info["size"], b"\0")
    output = work / "kernel_chestnut.raw"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(out)
    produced = KC(output)
    if CHESTNUT not in produced.kexts:
        raise PortError("prelink is missing AppleChestnutDisplayPMU")
    if output.read_bytes() == source.read_bytes():
        raise PortError("Chestnut transplant left the kernel unchanged")
    return output


def replace_personality(source: Path, donor: Path, work: Path) -> Path:
    """Install the SE SPIN69 personality only after AppleMultitouchSPI is linked."""

    kernel, donor_kc = KC(source), KC(donor)
    if SPI not in kernel.kexts:
        raise PortError("SPIN69 personality cannot link: AppleMultitouchSPI is missing")
    if SPIN in kernel.kexts:
        raise PortError("SPIN69 personality is already present")
    entry = dict(donor_kc.kexts[SPIN])
    from iphone7_port.kernel_transplant import xml_replace_entry
    xml, _inlined = xml_replace_entry(kernel, SPIC, entry)
    info = next(segment for segment in kernel.segments if segment["name"] == "__PRELINK_INFO")
    if len(xml) > info["size"]:
        raise PortError("SPIN69 personality does not fit in the prelink segment")
    out = bytearray(kernel.data)
    at = kernel.offset(info["addr"])
    out[at:at + info["size"]] = xml.ljust(info["size"], b"\0")
    output = work / "kernel_touch_personality.raw"
    output.write_bytes(out)
    produced = KC(output)
    if SPIN not in produced.kexts or SPIC in produced.kexts:
        raise PortError("iPad SPI personality was removed before the SE personality linked")
    check = compare_spin69(donor_kc.data[donor_kc.base:], produced.data[produced.base:])
    if check["status"] != "match":
        reason = check.get("reason") or ", ".join(check["differences"])
        raise PortError(f"SPIN69 personality check failed: {reason}")
    return output


LEFTOVER_DP = (
    "com.apple.driver.AppleANXDPTX",
    "com.apple.driver.AppleParadeDP805",
    "com.apple.driver.AppleParadeDP825",
    "com.apple.driver.AppleDPDisplayBacklightController",
    "com.apple.driver.AppleAgileDPDisplay",
    "com.apple.driver.AppleDPDisplayPowerController",
    "com.apple.iokit.IODisplayPortFamily",
)


def protected_footprints(applied: list[str]) -> set[str]:
    """Bundles that are still the destination of a transplant must not be deleted."""

    protected = set()
    if "display_pinot" not in applied:
        protected.add(DISPLAY_FOOTPRINT)
    if "touch_spin69" not in applied:
        protected.add(SPIC)
    protected.add(PMU_FOOTPRINT)  # replaced in place; the name is already D2255 or still the footprint
    return protected
