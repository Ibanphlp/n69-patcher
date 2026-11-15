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
    """Transplant driver NAME from donor into footprint of OLD in source kernel."""
    OUT = work
    k, d = KC(source), KC(donor)

    old_segs = k.ksegments(OLD)
    ds_segs = d.ksegments(NAME)
    if not old_segs: raise ValueError(f"Target footprint '{OLD}' not found in source kernel")
    if not ds_segs: raise ValueError(f"Donor driver '{NAME}' not found in donor kernel")

    old = {s['name']: s for s in old_segs}
    ds = {s['name']: s for s in ds_segs}

    require(not any(OLD in e.get('OSBundleLibraries', {}) for e in k.kexts.values()), 'Orion has dependent bundle')
    require(NAME not in k.kexts, 'Amp already present')

    dst = {}
    for n in PARTS:
        pad = (ds[n]['addr'] - old[n]['addr']) % 4096
        dst[n] = old[n]['addr'] + (pad if ds[n]['size'] + pad <= old[n]['size'] else 0)
        if preserve_text_span and n == '__TEXT':
            dst[n] = old[n]['addr']
        require(dst[n] + ds[n]['size'] <= old[n]['addr'] + old[n]['size'], 'Image does not fit ' + n)

    out = bytearray(k.data)
    def write(a, blob):
        require(any(old[n]['addr'] <= a and a + len(blob) <= old[n]['addr'] + old[n]['size'] for n in PARTS),
                'Write outside reclaimed Orion allocation ' + hex(a))
        off = k.offset(a)
        out[off:off+len(blob)] = blob

    for n in PARTS:
        write(old[n]['addr'], bytes(old[n]['size']))
        write(dst[n], d.read(ds[n]['addr'], ds[n]['size']))

    kb, kr = relocations(k)
    db, dr = relocations(d)

    def local(a):
        for n in PARTS:
            s = ds[n]
            if s['addr'] <= a < s['addr'] + s['size']:
                return dst[n] + a - s['addr']
        return None

    newrel = []
    for off in kr:
        va = kb + off
        if not any(old[n]['addr'] <= va < old[n]['addr'] + old[n]['size'] for n in PARTS):
            newrel.append(off)

    for off in dr:
        va = db + off
        if local(va) is not None:
            newrel.append(k.offset(local(va)) - k.offset(kb))

    packed = struct.pack('<I', len(newrel)) + struct.pack('<' + 'I' * len(newrel), *newrel)

    entry = copy.deepcopy(d.kexts[NAME])
    entry.update(_PrelinkExecutableLoadAddr=dst['__TEXT'], _PrelinkExecutableSize=ds['__TEXT']['size'])
    if registration_only:
        entry.pop('IOKitPersonalities', None)
        entry.pop('OSBundleRequired', None)

    xml, inlined = xml_replace_entry(k.xml, OLD, entry)

    xml, count = re.subn(rb'(<key>_PrelinkLinkKASLROffsets</key><data>)[A-Za-z0-9+/=\s]*(</data>)',
                         lambda m: m[1] + base64.b64encode(packed) + m[2], xml)
    require(count == 1, 'Missing KASLR offset array')

    info = next(s for s in k.segments if s['name'] == '__PRELINK_INFO')
    require(len(xml) <= info['size'], 'Prelink XML exceeds existing allocation')
    at = k.offset(info['addr'])
    out[at:at+info['size']] = xml.ljust(info['size'], b'\0')

    if SOURCE_MARKER:
        ma = k.data.find(SOURCE_MARKER)
        if ma != -1:
            out[ma:ma+len(SOURCE_MARKER)] = (TARGET_MARKER or SOURCE_MARKER)

    (OUT / 'kernel_sevenup_transplant.raw').write_bytes(out)
    return OUT / 'kernel_sevenup_transplant.raw'
