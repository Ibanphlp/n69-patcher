"""Source donor transplant engine with bounded allocation and ABI hooks.

Ported from SevenUP src/iphone7_port/kernel_transplant.py
"""
import base64
import bisect
import collections
import copy
import re
import struct
import xml.etree.ElementTree as ET

from .core import require
from .kc import KC, macho, plist
from .kernel_chestnut import commands, relocations, xml_value
from .kernel_base import branch

PARTS = ['__TEXT', '__TEXT_EXEC', '__DATA', '__DATA_CONST']

def entry_range(xml, name):
    prefix = b'<dict><key>_PrelinkInfoDictionary</key><array>'; require(xml.startswith(prefix), 'Unexpected XML')
    start = len(prefix); depth = 0; begin = None
    for m in re.finditer(rb'<dict\b[^>]*>|</dict>', xml[start:]):
        tag = m[0]; off = start + m.start()
        if tag.startswith(b'</'):
            depth -= 1
            if depth == 0 and begin is not None:
                chunk = xml[begin:start+m.end()]
                if b'<key>CFBundleIdentifier</key><string>' + name.encode() + b'</string>' in chunk:
                    return begin, start+m.end()
        elif not tag.endswith(b'/>'):
            if depth == 0:
                begin = off
            depth += 1
    raise RuntimeError('Missing entry ' + name)

def xml_replace_entry(xml, name, entry):
    try:
        a, z = entry_range(xml, name)
        removed = xml[a:z]
        new_xml = xml[:a] + xml_value(entry).encode() + xml[z:]
        ids = set(re.findall(rb'\bID="([^"]+)"', removed))
    except Exception:
        arr_tag = b'<dict><key>_PrelinkInfoDictionary</key><array>'
        idx = xml.find(arr_tag)
        if idx == -1:
            raise RuntimeError("Could not find _PrelinkInfoDictionary array")
        insert_pos = idx + len(arr_tag)
        new_entry_xml = xml_value(entry).encode()
        new_xml = xml[:insert_pos] + new_entry_xml + xml[insert_pos:]
        return new_xml, 0

    # Inline IDREFs by cloning the definition element (minus ID attribute)
    root = ET.fromstring(new_xml)
    defs = {e.attrib['ID']: e for e in root.iter() if 'ID' in e.attrib}

    def clone_without_id(e):
        c = ET.Element(e.tag, {k: v for k, v in e.attrib.items() if k != 'ID'})
        c.text = e.text
        c.tail = None
        for child in e:
            c.append(clone_without_id(child))
        return c

    inlined = 0
    for ident in ids:
        pattern = rb'<\w+\b[^>]*\bIDREF="' + re.escape(ident) + rb'"[^>]*/>'
        target = defs.get(ident)
        if target is None:
            continue
        clone = clone_without_id(target)
        # Serialize the clone
        replacement = ET.tostring(clone)
        new_xml, count = re.subn(pattern, lambda m: replacement, new_xml)
        inlined += count
    require(not ids & set(re.findall(rb'\bIDREF="([^"]+)"', new_xml)), 'Dangling removed XML definition')
    return new_xml, inlined

def fast_symbols(k):
    names, addresses = {}, collections.defaultdict(list)
    for cmd, q, _ in commands(k):
        if cmd != 2:
            continue
        off, count, strings, length = struct.unpack_from('<IIII', k.data, q + 8)
        st = k.data[k.base + strings:k.base + strings + length]
        for i in range(count):
            idx, typ, sect, desc, va = struct.unpack_from('<IBBHQ', k.data, k.base + off + i * 16)
            if not va or (typ & 0xe0):
                continue
            name = st[idx:st.find(b'\0', idx)].decode()
            names[name] = va
            addresses[va].append(name)
    return names, addresses

def apply(source, donor, work, OLD, NAME, SOURCE_MARKER=None, TARGET_MARKER=None,
          preserve_text_span=True, registration_only=False, full_donor_metadata=True):
    """The stripped copier is not a transplant. Use SevenUP's engine."""
    raise RuntimeError(
        "src/ios16se/sevenup/kernel_transplant.apply copies segments without rebinding "
        "relocations or ADRP. Call ios16se.driver_transplant.transplant_footprint, "
        "which uses SevenUP's kernel_transplant.apply and reviewed bindings."
    )
