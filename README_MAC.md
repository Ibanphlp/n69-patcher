# N69AP iOS 16 - Mac Installation Guide

**For Intel Mac + iPhone SE 1st Gen (N69AP) → iOS 16.7.16**

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
cd /path/to/n69-patcher
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
5. **Volumes**: Creates or validates SystemB at s8. The final role is Recovery (0x04). The OS shares the existing iOS 15 Data volume (role 0x40). A legacy s9 is left in place. Migration can affect data used by iOS 15.
6. **RootFS**: Streams ~9GB rootfs (25 min)
7. **Patches**: Selects the core rootfs resources for every kernel mode and preserves the existing systembag
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
./n69ap_ios16_full_install.sh minimal       # Common baseline
./n69ap_ios16_full_install.sh safe-minimal   # Baseline plus PMU and legacy SEP
```

The N69AP profile enables PMU, display power, LCD, touchscreen and SEP changes. Audio, Wi-Fi, charger and haptic patches are disabled. Touch ID remains unverified. [Driver coverage](N69AP_BOOT_GUIDE.md#driver-transplants)

---

## Troubleshooting

### "Missing tool: ramdisk-main/Darwin/gaster"
Run `./n69ap_mac_setup.sh` first to download macOS tools.

### "No N69AP detected in DFU mode"
- Ensure cable is good (try different USB port)
- DFU mode: BLACK screen, Finder/iTunes shows recovery mode
- Try different USB cable

### "IM4M not found"
Boot the stock SSH ramdisk without SEP and capture the image and its embedded ticket from Preboot:
```bash
N61_RAMDISK_SEP=0 ./n61.sh ramdisk --target n69-ios16
./n61.sh capture-sep --target n69-ios16
```

### "SSH ramdisk not ready"
- Keep device connected to charger
- Wait 30 seconds after gaster reset before proceeding

---

## Evidence from the SE

The last user-reported state was a return to iOS 15. The donor kernel, SpringBoard and hardware initialization still require evidence from the same attempt. Record the mode, host image hashes and the time of any visible change on the SE. [Trial sheet](FICHE_ESSAI.md)

## Portable host audit

The mobile and Mac package scripts each produce a portable archive of the offline audit and its SevenUP Python dependencies. The repository checkout contains the installation tools. After extracting an audit archive, run `python3 research/audit_n69_transplants.py --help` from its root. Device tickets, firmware, boot images and logs stay on their original host.

## Power Before Boot

| Battery | Action |
|---------|--------|
| < 20% | Connect charger first |
| 20-50% | Should work but risky |
| > 50% | Recommended for stable boot |

**Keep charging throughout the entire process!**
