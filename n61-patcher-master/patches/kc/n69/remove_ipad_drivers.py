#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Remove iPad-only drivers from __PRELINK_INFO safely.

Inlines every IDREF globally first (so removing an entry can never
orphan a reference into it), then removes the target entries.

Ported from src/ios16se/kc_patch.py
"""
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from core.patch import Patch


class KcRemoveIpadDrivers(Patch):
    id = "kc.remove_ipad_drivers"
    layer = "kc"
    role = "transform"
    feature = "core"
    scope = "version"
    requires = "kc.j96_clean"
    note = "移除 iPad-only 驱动"

    IPAD_DRIVERS = [
        "AppleMultitouchSPIC1F15",
        "AppleANXDPTX",
        "AppleParadeDP805",
        "AppleParadeDP825",
        "AppleDPDisplayBacklightController",
        "AppleAgileDPDisplay",
        "AppleDPDisplayPowerController",
        "IODisplayPortFamily",
    ]

    def apply(self, ctx, art=None):
        # Find the kernelcache file
        kc_path = ctx.work / "kc.raw"
        if not kc_path.exists():
            # Try to find it from art
            if art and hasattr(art, "kc_raw"):
                kc_path = art.kc_raw
            else:
                # Look in work dir
                for p in ctx.work.glob("*.raw"):
                    if p.stat().st_size > 10000000:
                        kc_path = p
                        break

        if not kc_path.exists():
            ctx.log.die(f"Kernelcache not found: {kc_path}")

        data = bytearray(kc_path.read_bytes())
        prelink_info = self._find_prelink_info(data)
        if prelink_info is None:
            ctx.log.die("__PRELINK_INFO segment not found")

        fileoff, filesize = prelink_info

        xml = bytes(data[fileoff:fileoff + filesize]).rstrip(b'\0')
        xml = self._inline_all_idrefs(xml)

        for driver in self.IPAD_DRIVERS:
            try:
                a, z = self._entry_range(xml, driver)
                ctx.log.log(f"    Removing iPad driver entry: {driver}")
                xml = xml[:a] + xml[z:]
            except Exception:
                # Driver might already be absent
                pass

        if len(xml) > filesize:
            ctx.log.die(f"Prelink XML grew to {len(xml)} > capacity {filesize}")

        data[fileoff:fileoff + filesize] = xml.ljust(filesize, b'\0')

        # Write output
        out_path = ctx.work / f"kc.{self.id}.raw"
        out_path.write_bytes(data)
        ctx.log.log(f"    Output: {out_path}")

        # Update context for next patch
        if hasattr(art, "kc_raw"):
            art.kc_raw = out_path
        return out_path

    def _entry_range(self, xml: bytes, name: str) -> tuple[int, int]:
        prefix = b'<dict><key>_PrelinkInfoDictionary</key><array>'
        if not xml.startswith(prefix):
            raise RuntimeError('Unexpected XML format')
        start = len(prefix)
        depth = 0
        begin = None
        for m in re.finditer(rb'<dict\b[^>]*>|</dict>', xml[start:]):
            tag = m[0]
            off = start + m.start()
            if tag.startswith(b'</'):
                depth -= 1
                if depth == 0 and begin is not None:
                    chunk = xml[begin:start + m.end()]
                    if b'<key>CFBundleIdentifier</key><string>' + name.encode() + b'</string>' in chunk:
                        return begin, start + m.end()
            elif not tag.endswith(b'/>'):
                if depth == 0:
                    begin = off
                depth += 1
        raise RuntimeError(f'Missing entry {name}')

    def _inline_all_idrefs(self, xml: bytes) -> bytes:
        """Inline every IDREF defined inside the removed entries."""
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return xml

        defs = {e.attrib['ID']: e for e in root.iter() if 'ID' in e.attrib}
        if not defs:
            return xml

        def clone_without_id(e: ET.Element) -> ET.Element:
            c = ET.Element(e.tag, {k: v for k, v in e.attrib.items() if k != 'ID'})
            c.text = e.text
            c.tail = None
            for child in e:
                c.append(clone_without_id(child))
            return c

        # Find all IDREFs that point to IDs defined inside removed entries
        refs = [e for e in root.iter() if 'IDREF' in e.attrib]
        for e in refs:
            ident = e.attrib['IDREF']
            target = defs.get(ident)
            if target is None:
                continue
            # Inline the scalar value
            clone = clone_without_id(target)
            if clone.text and not list(clone):
                e.tag = 'value'
                e.attrib.clear()
                e.text = clone.text
                e.tail = None
            else:
                e.tag = clone.tag
                e.attrib = clone.attrib
                e.text = clone.text
                e.tail = clone.tail
                e.clear()
                for child in clone:
                    e.append(child)
            if 'IDREF' in e.attrib:
                del e.attrib['IDREF']

        return ET.tostring(root)

    def _find_prelink_info(self, data: bytes) -> Optional[tuple[int, int]]:
        for i in range(len(data) - 72):
            if data[i:i+4] == b'\x19\x00\x00\x00':
                seg_name = data[i+8:i+24]
                if b'__PRELINK_INFO' in seg_name:
                    vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<4Q', data, i+24)
                    return fileoff, filesize
        return None


if __name__ == "__main__":
    patch = KcRemoveIpadDrivers()
    print(f"Patch: {patch.id}")
    print(f"Layer: {patch.layer}")
    print(f"Role: {patch.role}")
    print(f"Feature: {patch.feature}")