#!/usr/bin/env python3
"""
append_wt_fix.py <orig_im4p> <my_complzss> <out>

Extracts the Watchtower tail from the donor IM4P kernelcache and appends it to
the recompressed complzss stream. Matches n56-patcher's append_watchtower.py.
"""
import sys
import struct
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ios16se.img4_tool import get_img4_tool


def extract_payload_from_im4p(im4p_path: Path) -> bytes:
    """Extract payload bytes from IM4P container using ASN.1 or img4 tool."""
    data = im4p_path.read_bytes()
    if data.startswith(b"\x30"):  # ASN.1 SEQUENCE
        def rdlen(p):
            l = data[p]
            p += 1
            if l & 0x80:
                nb = l & 0x7F
                L = int.from_bytes(data[p:p + nb], "big")
                p += nb
            else:
                L = l
            return L, p

        try:
            L, p = rdlen(1)
            end = p + L
            octs = []

            def walk(a, b):
                pp = a
                while pp < b:
                    tag = data[pp]
                    Ln, q = rdlen(pp + 1)
                    if tag == 0x04:
                        octs.append(data[q:q + Ln])
                    elif tag == 0x30:
                        walk(q, q + Ln)
                    pp = q + Ln

            walk(p, end)
            if octs:
                return max(octs, key=len)
        except Exception:
            pass

    # Fallback to img4 tool extract
    tool = get_img4_tool()
    temp_out = im4p_path.parent / "temp_wt_extract.bin"
    tool.extract(im4p_path, temp_out)
    res = temp_out.read_bytes()
    if temp_out.exists():
        temp_out.unlink()
    return res


def extract_watchtower_tail(payload: bytes) -> bytes:
    """Extract watchtower tail from donor complzss payload."""
    idx = payload.find(b"complzss")
    if idx != -1:
        comp_header = payload[idx:]
        if len(comp_header) >= 20:
            csize = struct.unpack(">I", comp_header[16:20])[0]
            tail_offset = 0x180 + csize
            if len(comp_header) > tail_offset:
                wt = comp_header[tail_offset:]
                return wt

    raise ValueError("Donor has no valid complzss Watchtower tail")


def main():
    if len(sys.argv) < 4:
        print("Usage: append_wt_fix.py <orig_im4p> <my_complzss> <out>")
        sys.exit(1)

    orig_im4p = Path(sys.argv[1])
    mine = Path(sys.argv[2])
    out = Path(sys.argv[3])

    donor_payload = extract_payload_from_im4p(orig_im4p)
    wt = extract_watchtower_tail(donor_payload)

    mydata = mine.read_bytes()
    out.write_bytes(mydata + wt)
    print(f"append_watchtower: +{len(wt)} bytes -> {out} (total {len(mydata) + len(wt)})")


if __name__ == "__main__":
    main()
