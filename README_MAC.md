# N69AP iOS 16 - Mac Installation Guide

**For Intel Mac + iPhone SE 1st Gen (N69AP) → iOS 16.7.16**

The installation is experimental. On-device boot and compatibility with the secondary encrypted data volume remain unverified.

## Quick Start

### 1. Prepare Your Mac
- **Mac must be Intel-based** (NOT Apple Silicon)
- Connect iPhone SE to Mac via USB cable
- Put device in **DFU mode**:
  - Hold Power + Home for 8 seconds
  - Release Power, keep holding Home for 15 seconds
  - Screen stays BLACK = DFU mode

### 2. Run Setup Script
```bash
cd /path/to/n69ap-ios16-install
./n69ap_mac_setup.sh
```

This downloads:
- macOS/Darwin tools (gaster, irecovery, iBoot64Patcher, kairos, img4)
- 3 IPSWs (~11GB total):
  - iPhone SE 15.8.8 (~3.5GB)
  - iPad 5 16.7.16 (~4.2GB) - **primary donor**
  - iPhone 6s 15.8.8 (~3.5GB) - optional UI donor

### 3. Install
```bash
./n69ap_ios16_full_install.sh full
```

**Battery**: Keep iPhone connected to charger during installation (~30 min)

---

## What the Scripts Do

### `n69ap_mac_setup.sh`
- Downloads macOS-compatible binaries from palera1n releases
- Downloads IPSWs from ipsw.me
- Creates `firmware/` and `ramdisk-main/Darwin/` directories

### `n69ap_ios16_full_install.sh`
1. **Preflight**: Verifies Mac, IPSWs, IM4M, tools
2. **Device Check**: Confirms N69AP in DFU mode
3. **Ramdisk**: Exploits checkm8 → SSH ramdisk on port 2222
4. **IM4M**: Extracts device ticket (fully automated via SSH)
5. **Volumes**: Expects SystemB at s8 and DataB at s9
6. **RootFS**: Streams ~9GB rootfs (25 min)
7. **Patches**: Applies driver, keybag, UI resource patches
8. **Build**: Creates boot images with TrustCache
9. **Boot**: Sends iBSS → iBEC → DT → TC → SEP → KC → bootx

---

## Required Files (Auto-downloaded)

| File | Path | Description |
|------|------|-------------|
| IPSW-SE | `firmware/iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw` | SE 15.8.8 for base |
| IPSW-IPAD | `firmware/iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw` | iPad 5 16.7.16 (driver donor) |
| IPSW-UI | `firmware/iPhone_4.7_15.8.8_19H422_Restore.ipsw` | iPhone 6s UI donor (optional) |
| IM4M | `state/IM4M.bin` | Device-specific ticket (extracted via ramdisk) |

---

## Build Modes

```bash
./n69ap_ios16_full_install.sh full
./n69ap_ios16_full_install.sh minimal
./n69ap_ios16_full_install.sh safe-minimal
```

All modes leave the unvalidated N69AP hardware and SEP patches disabled.

---

## Troubleshooting

### "Missing tool: ramdisk-main/Darwin/gaster"
Run `./n69ap_mac_setup.sh` first to download macOS tools.

### "No N69AP detected in DFU mode"
- Ensure cable is good (try different USB port)
- DFU mode: BLACK screen, Finder/iTunes shows recovery mode
- Try different USB cable

### "IM4M not found"
After setup, put device in DFU and run gaster extraction:
```bash
./ramdisk-main/Darwin/gaster pwn
./ramdisk-main/Darwin/gaster reset
sleep 3
dd if=/dev/disk2 bs=256 count=16384 2>/dev/null | ./ramdisk-main/Darwin/img4 -i - -m state/IM4M.bin
```

### "SSH ramdisk not ready"
- Keep device connected to charger
- Wait 30 seconds after gaster reset before proceeding

---

## Expected Results

The installer reports when the boot chain was sent. Verify the OS version on the device to confirm the boot.

The N69AP profile expects `SystemB` and `DataB` to start at role `0x00` before finalization. Recovery (`0x04`) is the role SevenUP uses for `Xystem` on its iPhone 7 target. The N69AP finalization stops when the original iOS 15 volumes already hold System (`0x01`) or Data (`0x40`).

If an APFS write is interrupted after it starts, the original metadata blocks are retained in the logged `apfs-role-backup.*` directory. The write may have applied partially, so obtain a fresh status report before making further changes.

---

## Files in This Package

```
├── LICENSE                    # MIT License
├── README.md                  # This file
├── N69AP_BOOT_GUIDE.md        # Detailed technical guide
├── FINAL_AUDIT_TABLE.md       # Patch verification table
├── requirements.txt           # Python dependencies
├── run_boot.sh                # Main orchestrator (macOS)
├── run_n69_ios16_boot.py      # Python build script
├── n69ap_ios16_full_install.sh # Full automated install
├── n69ap_mac_setup.sh         # Auto-download tools + IPSWs
├── n69ap_mac_install.sh       # Combined setup + install
├── profiles/                  # Build configurations
├── src/                       # Build scripts, SevenUP engine
├── patches/                   # Driver transplant patches
├── artifacts/                 # Boot images, kernels
├── ramdisk-main/Darwin/       # macOS binaries (downloaded)
├── ramdisk-main/Linux/        # Linux binaries (reference)
├── firmware/                  # IPSW files (downloaded)
├── tools/                     # Support tools (mkcomplzss)
└── state/                     # IM4M.bin, device state
```

---

## Power Before Boot

| Battery | Action |
|---------|--------|
| < 20% | Connect charger first |
| 20-50% | Should work but risky |
| > 50% | Recommended for stable boot |

**Keep charging throughout the entire process!**