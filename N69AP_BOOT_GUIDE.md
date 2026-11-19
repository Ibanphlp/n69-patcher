# N69AP iOS 16 Tethered Boot Pipeline

Experimental iOS 16.7.16 tethered boot work for the iPhone SE 1st Gen (N69AP). A successful boot remains unverified.

## Overview

The build uses an iPad 5 kernel donor. The N69AP hardware and SEP adaptations remain disabled.

## Boot Sequence

```
DFU Mode → gaster pwn → iBSS → iBEC → DeviceTree → TrustCache → SEP → KernelCache → bootx
```

## Prerequisites

### Firmware Files (place in `firmware/`)
- `iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw` (N69AP SE 15.8.8)
- `iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw` (J96 iPad 5 16.7.16)

### IM4M Ticket
Extract from NOR via SSH ramdisk:
```bash
# After entering ramdisk via gaster:
dd if=/dev/disk2 bs=256 count=16384 | ./ramdisk-main/Linux/img4 -i - -m state/IM4M.bin
```

## Usage

### Quick Start
```bash
# Build with the default mode
./run_boot.sh

# Build with the full mode
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

The `safe-minimal`, `minimal`, and `full` modes all leave unvalidated N69AP driver and SEP patches disabled.

## Candidate Driver Transplants

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
| `boot_KC.img4` | Patched donor KernelCache |
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
- [ ] Validate N69AP PMU, display, audio, Wi-Fi, and SEP adaptations on the target
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
- The target profile expects `SystemB` on `disk0s1s8`. Use the APFS status report to verify the name, role, and checksum.
- Verify IM4M signing: `img4 -i boot_KC.img4 -M state/IM4M.bin` should work

### Kernel panics
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