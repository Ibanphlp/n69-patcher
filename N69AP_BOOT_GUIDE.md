# N69AP iOS 16 Tethered Boot Pipeline

Tethered boot path for iPhone SE 1st gen (N69AP and N69uAP) on iOS 16.7.16.
SpringBoard is unproven until a serial log shows the SE PMU probe, the Pinot
framebuffer, and launchd. A green host test is not that log.

## Overview

This pipeline boots iOS 16 on N69AP (iPhone SE 1st Gen, A9) by:
1. Using the SE's own iOS 15.8.8 kernelcache for hardware drivers
2. Porting those drivers into the iPad 5 iOS 16 kernelbase via SevenUP's footprint-displacement engine
3. Building a complete boot chain with IM4M-signed images

## Boot Sequence

```
DFU Mode → gaster pwn → iBSS → iBEC → DeviceTree → TrustCache → SEP → KernelCache → bootx
```

## Prerequisites

### Firmware Files (place in `firmware/`)
- `iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw` (N69AP SE 15.8.8)
- `iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw` (J96 iPad 5 16.7.16)

### SEP and IM4M
Boot the stock SSH ramdisk without SEP, then capture the complete image and its embedded ticket from Preboot:
```bash
N61_RAMDISK_SEP=0 ./n61.sh ramdisk --target n69-ios16
./n61.sh capture-sep --target n69-ios16
```
If multiple slots are present, set `N69_PREBOOT_HASH` to the desired 96-character Preboot slot hash.

## Usage

### Quick Start
```bash
# safe-minimal builds the PMU transplant and the reviewed SEP request shim.
# full adds Chestnut, Pinot and the SPIN69 personality.
# Neither mode has been shown to reach SpringBoard. A host-side structural
# pass is not a serial log of the SE PMU, the Pinot framebuffer, or launchd.
./run_boot.sh safe-minimal
./run_boot.sh full

# Build and boot device (device must be in DFU mode)
./run_boot.sh full --boot
```

### Direct Python Usage
```python
# Build only
python run_n69_ios16_boot.py --im4m state/IM4M.bin --mode full

# Build and boot
python run_n69_ios16_boot.py --im4m state/IM4M.bin --mode full --boot
```

## Build Modes

| Mode | Patches Applied | Use Case |
|------|----------------|----------|
| `safe-minimal` | remove_ipad_drivers, generic_security, pmu_d2255, sep_legacy | Safest for unknown hardware |
| `minimal` | remove_ipad_drivers, generic_security | Basic boot without driver ports |
| `full` | All patches | Full driver functionality (PMU, Display, Audio, WiFi, etc.) |

## Driver Transplants

| Driver | Footprint | New Driver | Purpose |
|--------|-----------|------------|---------|
| `AppleD2365PMU` | 4285 bytes | `AppleD2255PMU` | Power management |
| `AppleDPDisplay` | 66280 bytes | `ApplePinotLCD` | MIPI-DSIM display |
| `AppleCS42L81Audio` | 108605 bytes | `AppleCS42L71Audio` | Audio codec |
| `AppleAE2Audio` | ~8708 bytes | `AppleCS35L19Amp` | Amplifier |
| `AppleHDQGasGaugeControl` | 8708 bytes | `AppleC26Charger` | Charging |

## Output Artifacts

All boot images are in `artifacts/boot/`:

| File | Description |
|------|-------------|
| `boot_iBSS.img4` | Initial Boot ROM stage (signed as ibss) |
| `boot_iBEC.img4` | Second stage bootloader with boot-args |
| `boot_DT.img4` | Native N69AP DeviceTree |
| `boot_TC.img4` | TrustCache (rtsc) |
| `boot_KC.img4` | Patched KernelCache with transplanted drivers |
| `sep.img4` | SEP firmware (signed as rsep) |

## Architecture

```
src/ios16se/
├── build.py                  # Build orchestrator
├── cli.py                    # CLI interface
├── build.py                  # Boot flow execution
├── kc_patch.py               # Patch registry & RemoveIpadDrivers
├── kc_patch_drivers.py       # TransplantedDriverPatch base class
├── kc_patch_n69.py           # N69AP-specific patches (PMU, SEP, WiFi)
└── sevenup/                  # SevenUP transplant engine
    ├── kernel_transplant.py  # Footprint-displacement engine
    ├── kc.py                 # KernelCache loader
    └── kernel_chestnut.py    # XML utilities

profiles/
└── n69ap_ios16.toml          # Build configuration

run_n69_ios16_boot.py         # Master orchestration script
run_boot.sh                    # Shell wrapper
```

## Development Status

- [x] iBSS/iBEC decryption via gaster
- [x] boot_args injection via iBoot64Patcher
- [x] root-partition selection via kairos
- [x] iPad 5 iOS 16 kernelbase extraction
- [x] iPad driver removal from prelink XML
- [x] PMU D2255 driver transplant
- [x] Display Pinot driver transplant
- [x] Audio CS42L71/CS35L19 driver transplants
- [x] SEP legacy compatibility patches
- [x] WiFi BCM hash bypass
- [x] Watchtower append
- [x] KernelCache recompress
- [x] IM4M signing for all components

## Testing

Before attempting to boot, run preflight check:
```bash
python run_n69_ios16_boot.py --im4m state/IM4M.bin --mode safe-minimal --build-only
```

This verifies:
- IM4M exists and is valid
- All IPSWs are present
- All tools are executable
- All boot artifacts are built

## Troubleshooting

### Device reboots to recovery mode
- Check boot-args: `rd=disk0s1s8` must match SystemB. `disk0s1s4` is Update, not the root.
- Verify IM4M signing: `img4 -i boot_KC.img4 -M state/IM4M.bin` should work

### Kernel panics
- Try `--mode safe-minimal` first
- Check serial console for panic messages
- Verify KASLR table rebuild in `append_wt_fix.py`

### No serial output
- Ensure debug flags: `debug=0x2014e` in boot-args
- Check cable connection and USB debugging enabled

## References

- [SevenUP Main](https://github.com/tihmstar/SevenUP) - Transplant engine base
- [n56-patcher](https://github.com/n Edwin/n61-patcher) - Reference implementation
- [checkra1n](https://checkra.in) - DFU exploitation tool

## License

This project is for educational and repair purposes only. Use at your own risk.
