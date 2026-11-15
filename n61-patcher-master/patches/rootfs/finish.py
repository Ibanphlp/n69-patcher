#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rootfs.finish: Final step after rootfs streaming.

Copies var→DataB, replaces fsck/apfs.fs with iOS 14, installs SEP firmware, copies keybags.

Ported from n61-patcher patches/rootfs/finish.py
"""
import os
import subprocess

from core import log
from core.context import STATE_DIR
from core.patch import Patch


class RootfsFinish(Patch):
    id = "rootfs.finish"
    layer = "rootfs"
    role = "finalize"
    feature = "core"
    scope = "version"
    requires = ("rootfs.base_stream",)
    donors = ("apfs14",)
    note = "var→DataB / fsck / SEP固件 / keybags 等收尾"

    def apply(self, ctx, art=None):
        ssh = ctx.ssh
        apfs14 = ctx.donor("apfs14")
        encrypted = ctx.t.get("data_protection") == "encrypted"
        keybags = None if encrypted else os.path.join(STATE_DIR, "keybags.tar")

        # Mount volumes
        ssh.mount(ctx.t["mounts"]["system_b"], "/mnt6")
        ssh.mount(ctx.t["mounts"]["system12"], "/mnt1", ro=True)
        if encrypted:
            # For encrypted DataB, need to ensure SEP is ready for systembag
            log.log("Waiting for SEP readiness for systembag...")
            import time
            time.sleep(2)
        else:
            ssh.mount(ctx.t["mounts"]["data_b"], "/mnt7")

        # 1. Copy var → DataB
        log.log("private/var → DataB…")
        ssh.sh("/bin/cp -a /mnt6/private/var/. /mnt7/")

        # 2. Create xarts/preboot (needed for iOS 14+)
        log.log("xarts / preboot 目录…")
        ssh.sh("mkdir -p /mnt6/private/xarts /mnt6/private/preboot && "
               "chmod 755 /mnt6/private/xarts /mnt6/private/preboot")

        # 3. Replace fsck/apfs.fs with iOS 14 versions
        log.log("换 iOS14 fsck + apfs.fs（原文件改名 *Backup）…")
        ssh.sh("cd /mnt6 && { [ -e sbin/fsckBackup ] || mv sbin/fsck sbin/fsckBackup; } && "
               "{ [ -e System/Library/Filesystems/apfs.fsBackup ] || "
               "mv System/Library/Filesystems/apfs.fs System/Library/Filesystems/apfs.fsBackup; }")

        # Stream iOS 14 fsck and apfs.fs
        src = subprocess.Popen(
            ["/usr/bin/tar", "--uid", "0", "--gid", "0", "--no-xattrs", "-cf", "-",
             "-C", apfs14, "./sbin/fsck", "./System/Library/Filesystems/apfs.fs"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=dict(os.environ, COPYFILE_DISABLE="1"))
        dst = subprocess.Popen(
            ssh.ssh_argv() + ["/usr/bin/tar --numeric-owner -xpf - -C /mnt6 && "
                              "chmod 755 /mnt6/sbin/fsck /mnt6/System/Library/Filesystems/apfs.fs"],
            stdin=src.stdout, stderr=subprocess.PIPE)
        src.stdout.close()
        _, err = dst.communicate()
        src.wait()
        if dst.returncode != 0:
            log.die("fsck/apfs.fs 替换失败\n" + (err or b"").decode(errors="replace"))

        # 4. Copy SEP firmware from SystemA
        log.log("SEP 固件（来自 iOS12 s1）…")
        ssh.sh("/bin/cp -a /mnt1/usr/standalone/firmware/sep-firmware.img4 "
               "/mnt6/usr/standalone/firmware/")

        # 5. Copy keybags
        if encrypted:
            log.log("保留本次为加密 DataB 创建的 systembag…")
            ssh.sh("test -s /mnt7/keybags/systembag.kb")
        else:
            log.log("keybags → DataB…")
            with open(keybags, "rb") as f:
                ssh.deploy_tar_bytes(f.read(), "/mnt7")

        # 6. Verify
        ssh.sh("ls -ln /mnt6/sbin/fsck /mnt6/usr/standalone/firmware/sep-firmware.img4 "
               "/mnt7/keybags/systembag.kb")
        ssh.sync()
        ssh.umount("/mnt1", "/mnt7", "/mnt6")
        log.log("finish 完成，卷已卸载。")

        return None


# Also create a simplified shell version for n61.sh integration
if __name__ == "__main__":
    print(f"Patch: {RootfsFinish.id}")