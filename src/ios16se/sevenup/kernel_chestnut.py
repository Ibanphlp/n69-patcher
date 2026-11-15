"""Chestnut display driver transplant helpers.

Ported from SevenUP src/iphone7_port/kernel_chestnut.py
"""
import base64
import collections
import copy
import re
from xml.sax.saxutils import escape

from .core import require
from .kc import KC, plist

NAME = 'com.apple.driver.ApplePinotLCD'
PLATFORM = 'com.apple.driver.AppleARMPlatform'
POOLS = {'__TEXT': '__PRELINK_TEXT', '__TEXT_EXEC': '__PLK_TEXT_EXEC',
         '__DATA': '__PRELINK_DATA', '__DATA_CONST': '__PLK_DATA_CONST'}

def commands(k, va=None):
    off = k.base if va is None else k.offset(va)
    q = off + 32
    for _ in range(struct.unpack_from('<I', k.data, off + 16)[0]):
        cmd, size = struct.unpack_from('<II', k.data, q)
        yield cmd, q, size
        q += size

def image_uuid(k, va):
    return next(k.data[q + 8:q + 24] for cmd, q, size in commands(k, va) if cmd == 27)

def xml_value(v):
    if isinstance(v, (bytes, bytearray)):
        return '<data>' + base64.b64encode(v).decode('ascii') + '</data>'
    if isinstance(v, dict):
        return '<dict>' + ''.join('<key>' + escape(k) + '</key>' + xml_value(x) for k, x in v.items()) + '</dict>'
    if isinstance(v, list):
        return '<array>' + ''.join(xml_value(x) for x in v) + '</array>'
    if isinstance(v, bool):
        return '<true/>' if v else '<false/>'
    if isinstance(v, int):
        return '<integer size="64">0x' + format(v, 'x') + '</integer>'
    if isinstance(v, str):
        return '<string>' + escape(v) + '</string>'
    raise TypeError(type(v))

import struct

def relocations(k):
    d = plist(k.xml)['_PrelinkLinkKASLROffsets']
    words = list(struct.unpack('<' + 'I' * (len(d) // 4), d))
    require(words[0] == len(words) - 1, 'Invalid packed KASLR count')
    require(len(words[1:]) == len(set(words[1:])), 'Duplicate original KASLR slots')
    start = next(s['addr'] for s in k.segments if s['name'] == '__PRELINK_TEXT')
    return start, words[1:]
