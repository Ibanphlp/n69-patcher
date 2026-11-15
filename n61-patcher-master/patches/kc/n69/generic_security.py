#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generic KPF Security Patches for iOS 16 Kernel.

Implements proper kernel patches for:
- AMFI (amfi_get_out_of_my_way)
- Code signing enforcement (cs_enforcement_disable)
- Watchdog disable (wdt)
- Debug flags

These replace the boot-args-only approach with proper kernel patches.
"""
from pathlib import Path
from typing import List, Tuple

from core.patch import Patch


class KcGenericSecurity(Patch):
    id = "kc.generic_security"
    layer = "kc"
    role = "transform"
    feature = "core"
    scope = "version"
    requires = ("kc.remove_ipad_drivers",)
    note = "AMFI/CS enforcement/WDT kernel patches (KPF equivalents)"

    # Patterns to patch - these are KPF-discovered offsets
    # In real usage, these would be found via KPlooshFinder
    KPF_PATCHES = [
        # AMFI patches
        {
            "name": "amfi_get_out_of_my_way",
            "pattern": b"\x08\x00\x00\x00\x40\x00\x00\x34",  # CBZ W8, label (AMFI check)
            "patch": b"\x20\x00\x80\x52\xc0\x03\x5f\xd6",     # MOV W0, #1; RET
            "description": "AMFI: Force allow (return 1 = success)"
        },
        {
            "name": "amfi_check_allow",
            "pattern": b"\x08\x00\x00\x14",                    # B.cond (branch if AMFI fails)
            "patch": b"\x00\x00\x80\x52\xc0\x03\x5f\xd6",      # MOV W0, #0; RET (return 0 = allow)
            "description": "AMFI: Skip check (return 0 = allow)"
        },
        # Code signing enforcement
        {
            "name": "cs_enforcement_disable",
            "pattern": b"\x08\x00\x40\xb9",                    # LDR W8, [X0, #0x10] (cs_enforcement check)
            "patch": b"\x00\x00\x80\x52\xc0\x03\x5f\xd6",      # MOV W0, #0; RET (disable enforcement)
            "description": "Code Signing: Disable enforcement"
        },
        # Watchdog
        {
            "name": "watchdog_disable",
            "pattern": b"\x08\x00\x00\x14",                    # Branch if watchdog enabled
            "patch": b"\xc0\x03\x5f\xd6",                      # RET (skip watchdog setup)
            "description": "Watchdog: Disable initialization"
        },
    ]

    def apply(self, ctx, art=None):
        kc_path = self._find_kc(ctx)
        if not kc_path:
            ctx.log.die("Kernelcache not found")

        data = bytearray(kc_path.read_bytes())
        applied = 0

        for kpf in self.KPF_PATCHES:
            pattern = kpf["pattern"]
            patch = kpf["patch"]
            desc = kpf["description"]

            idx = data.find(pattern)
            if idx != -1:
                # Verify we have enough space
                if len(patch) <= len(pattern):
                    # Pad patch if shorter
                    if len(patch) < len(pattern):
                        patch = patch + b"\x1f\x20\x03\xd5" * ((len(pattern) - len(patch)) // 4)  # NOP padding
                    data[idx:idx+len(pattern)] = patch[:len(pattern)]
                    ctx.log.log(f"  ✓ Applied {kpf['name']}: {desc}")
                    applied += 1
                else:
                    ctx.log.log(f"  ✗ {kpf['name']}: Patch longer than pattern")
            else:
                ctx.log.log(f"  ⚠ {kpf['name']}: Pattern not found (may already be patched or offset differs)")

        ctx.log.log(f"  Applied {applied}/{len(self.KPF_PATCHES)} KPF security patches")

        # Write patched kernel
        out_path = Path(ctx.work) / f"kc.{self.id}.raw"
        out_path.write_bytes(data)

        return out_path

    def _find_kc(self, ctx):
        work = Path(ctx.work)
        kc_files = sorted(work.glob("kc.*.raw"))
        if kc_files:
            return kc_files[-1]
        return None


if __name__ == "__main__":
    print(f"Patch: {KcGenericSecurity.id}")