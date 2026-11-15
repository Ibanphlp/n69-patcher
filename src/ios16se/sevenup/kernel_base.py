"""ARM64 instruction helpers for SevenUp transplant engine.

Ported from SevenUP src/iphone7_port/kernel_base.py
"""
import struct

def word(k, address):
    """Read 4-byte word from kernel at VA."""
    o = k.offset(address)
    return struct.unpack_from('<I', k.data, o)[0]

def signed(value, bits):
    """Sign-extend a bitfield."""
    mask = 1 << (bits - 1)
    return (value & ((1 << bits) - 1)) - (value & mask)

def branch(pc, target):
    """Generate B or BL instruction word."""
    delta = target - pc
    if -0x2000000 <= delta < 0x2000000:
        return 0x94000000 | ((delta >> 2) & 0x3ffffff)  # B
    raise ValueError(f'branch {delta:#x} out of range')


def symbols(k):
    """Extract symbol table from kernel."""
    names, addresses = {}, {}
    for seg in k.segments:
        if seg['name'] not in ('__TEXT', '__TEXT_EXEC'):
            continue
        data = k.data[k.base+seg['offset']:k.base+seg['offset']+seg['size']]
        off, count, strings, length = struct.unpack_from('<IIII', data, 8)
        st = data[strings:strings+length]
        for i in range(count):
            idx, typ, sect, desc, va = struct.unpack_from('<IBBHQ', data, off + i*16)
            if not va or (typ & 0xe0):
                continue
            name = st[idx:st.find(b'\0', idx)].decode()
            names[name] = va
            addresses.setdefault(va, []).append(name)
    return names, addresses
