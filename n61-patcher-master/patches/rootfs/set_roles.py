#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rootfs.set_roles: Set APFS volume roles.

Sets SystemB=0x1 and DataB=0x40 after rootfs installation.

Ported from n61-patcher patches/rootfs/set_roles.py
"""
from core import log
from core.patch import Patch


class RootfsSetRoles(Patch):
    id = "rootfs.set_roles"
    layer = "rootfs"
    role = "finalize"
    feature = "core"
    scope = "device"
    requires = ("rootfs.finish",)
    note = "s4→System(0x1), s5→Data(0x40)"

    def apply(self, ctx, art=None):
        log.die("The legacy Python APFS role step is disabled. It cannot verify role changes.")


if __name__ == "__main__":
    print(f"Patch: {RootfsSetRoles.id}")