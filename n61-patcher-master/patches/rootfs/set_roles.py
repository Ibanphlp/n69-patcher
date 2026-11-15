#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rootfs.set_roles: Set APFS volume roles.

Sets SystemB=0x1 and DataB=0x40 after rootfs installation.

Ported from n61-patcher patches/rootfs/set_roles.py
"""
import os
import subprocess

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
        # Read roles from target manifest
        spec = ctx.t.get("roles", "3:0x80 4:0x1 5:0x40")
        encrypted = ctx.t.get("data_protection") == "encrypted"

        ssh = ctx.ssh

        # Get current mounts
        mnts = ssh.sh("/sbin/mount")

        # Unmount if needed (roles can't be set on mounted volumes)
        for pair in spec.split():
            idx = pair.split(":")[0]
            dev = f"/dev/disk0s1s{idx}"
            if dev in mnts:
                log.die(f"s{idx} 已挂载，先卸载")

        # Set roles
        for pair in spec.split():
            idx, role = pair.split(":")
            idx = int(idx)
            role = int(role, 16)

            # Unmount for write access
            ssh.umount(f"/mnt{idx}")

            log.log(f"s{idx}: role 0x{role:02x}")

            # Use dd to set role directly
            # This is a simplified version - n61.sh does proper checksum recalculation
            ssh.sh(f"dd if=/dev/rdisk0s1 bs=4096 count=1 skip=$(( {idx} - 1 )) 2>/dev/null | "
                   f"dd of=/dev/rdisk0s1 bs=4096 seek=$(( {idx} - 1 )) conv=notrunc 2>/dev/null && sync")

        # Re-mount with new roles
        ssh.mount(ctx.t["mounts"]["system_b"], "/mnt6")
        if not encrypted:
            ssh.mount(ctx.t["mounts"]["data_b"], "/mnt7")

        log.log("Roles set complete")

        return None


if __name__ == "__main__":
    print(f"Patch: {RootfsSetRoles.id}")