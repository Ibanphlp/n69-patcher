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
# safe-minimal removes leftover iPad drivers, then adds PMU + SEP shims.
# Use --board n69uap for TSMC devices (CPID 0x8003); run_boot.sh defaults to n69ap.
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
| `safe-minimal` | generic_security, remove_ipad_drivers, pmu_d2255, sep_legacy, panic_overlay | Minimal boot with reviewed PMU/SEP shims |
| `minimal` | generic_security, remove_ipad_drivers, panic_overlay | Basic boot without driver ports |
| `full` | Adds Chestnut, Pinot and SPIN69 to the PMU and SEP changes | Hardware operation remains unverified |

## Driver Transplants

The committed [compatibility report](research/n69-compatibility-report.json) identifies the two kernel images by hash. Its three passing results cover linking on those images. Device initialization and peripheral operation remain unverified.

| Component | N69AP profile | Evidence and limits |
|-----------|---------------|---------------------|
| D2255 PMU | Enabled | The report records 68 bindings. Power management on the SE remains unverified. |
| Pinot LCD | Enabled | The report records five bindings. It contains no framebuffer observation. |
| Touchscreen | Enabled | The report records one binding for `AppleMultitouchSPI`. Its test ends before the separate SPIN69 personality step. Touch input remains unverified. |
| Chestnut display power | Enabled | Implemented separately from Pinot. The report contains no Chestnut result. |
| SEP compatibility | Enabled | The tests check firmware and ticket handling and a synthetic code change. Device SEP services and Touch ID remain unverified. |
| CS42L71 audio codec | Disabled | The implementation refuses the transplant and reports insufficient space. |
| CS35L19 amplifier | Disabled | The implementation refuses the transplant. |
| C26 charger | Disabled | The implementation refuses the transplant and reports insufficient space. Charging behavior on hardware remains unverified. |
| Broadcom Wi-Fi | Disabled | Firmware hash handling exists. The report contains no Wi-Fi association or traffic test. |
| DRV2604 vibration | Disabled | The implementation refuses the transplant and reports a missing driver class. |

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

### Device automatically reboots to stock iOS after bootx
- If the ramdisk logo disappears, iBoot likely attempted kernel handoff, but that alone does not identify a panic.
- The latest attempt used `minimal` with `remove_ipad_drivers` and still returned to iOS 15 after about 60 seconds. That makes driver cleanup an unconfirmed hypothesis, not a fix.
- The current embedded iBEC arguments omit panic-hold and verbose flags. With the phone in DFU, rebuild the complete chain (including iBEC) for CPID 0x8003: `python3 run_n69_ios16_boot.py --board n69uap --mode minimal --build-only`. Do not use `--kernel-only` for this diagnostic; it would leave the old iBEC in place. The configured arguments include `panic-wait-forever=1`, `-v`, `keepsyms=1`, and `serial=3` so a panic may remain visible. This is diagnostic, not a confirmed fix.
- Run `N69_PREFLIGHT_ONLY=1 ./send_n69_boot.sh` before retrying. If it still restarts, note whether the verbose log shows panic text or the screen stays frozen; also check iOS 15 Analytics Data for a matching `panic-full`/`panic-base` report.

### Device reboots to recovery mode
- Check boot-args: `rd=disk0s1s8` must match SystemB. `disk0s1s4` is Update, not the root.
- `./send_n69_boot.sh` runs an IM4M preflight for KernelCache, DeviceTree, TrustCache and SEP before entering DFU. An explicit `IMG4 preflight failed` points to an artifact/ticket mismatch, not a kernel panic.
- Verify IM4M signing manually with the vendored tools: `ramdisk-main/Darwin/img4 -i artifacts/boot/boot_DT.img4 -M state/IM4M.bin`; the large KernelCache is parsed with `ramdisk-main/Darwin/img4tool`.
- The script displays the iBEC boot logo after loading iBEC. If it appears, iBEC accepted the logo commands; if it never appears, investigate pwn/iBSS/iBEC before attributing the black screen to the kernel. The script's `logs/boot-*.log` records irecovery output and per-command exit codes.

### Kernel panics
- Try `--mode safe-minimal` first.
- The panic overlay draws only if Pinot/PE_Video has initialized. It also writes marker `0x4e363901` (panic entry), `0x4e363902` (formatter), then `0x4e363903` (stack capture) into its reserved kernel panic-state word. This is a RAM diagnostic marker, not persistent storage; collect it from a kernel memory dump/debugger or correlate it with serial diagnostics. No haptic call is made from panic context.
- The configured iBEC arguments include `panic-wait-forever=1 -v keepsyms=1 serial=3`. Rebuild iBEC (not only the KernelCache) before relying on them. A visible panic or a persistent frozen screen is diagnostic evidence; a black screen alone is not.
- Check the serial console for panic messages and the last reached boot stage.
- Verify KASLR table rebuild in `append_wt_fix.py`.

### Serial/UART checklist (iPhone SE 1st gen)
- Boot arguments in `profiles/n69ap_ios16.toml` include `panic-wait-forever=1 -v debug=0x2014e keepsyms=1 serial=3`; connect a 3.3 V TTL UART adapter through an iPhone SE (1st gen / Lightning) UART breakout/dock-flex accessory exposing the device's serial TX, RX and GND contacts. Lightning pin assignments are accessory/authentication-dependent: use a breakout with documented UART pin mapping for this exact SE revision; do not guess Lightning pin numbers or connect RS-232 voltage levels. Use a common ground and begin with the adapter's RX connected to device TX at 115200 baud, 8 data bits, no parity, 1 stop bit; TX is only needed if bidirectional input is required.
- On macOS, find the adapter as `/dev/tty.usbserial-XXXX` (or `/dev/cu.usbserial-XXXX`) with `ls /dev/{tty,cu}.usbserial*`, then start capture before boot: `screen /dev/tty.usbserial-XXXX 115200`.
- Keep the serial terminal open while running `./send_n69_boot.sh`. Save the transcript, the matching `logs/boot-*.log`, whether/when the Apple logo appeared, and whether the phone stayed connected or returned to Recovery. A message through `launchd` is the evidence needed to establish a late successful kernel boot.
- Without this cable/breakout, none of the observations above can by itself confirm a complete boot through `launchd`; they only narrow the possible causes (iBEC response, signature/artifact rejection, or a panic captured before display initialization).

### No serial output
- Ensure `panic-wait-forever=1 -v debug=0x2014e keepsyms=1 serial=3` are in the **rebuilt iBEC's** boot-args. A kernel-only rebuild does not update them.
- Check the adapter's UART mapping/voltage level, common ground, TX-to-RX direction and baud rate; a standard charge/sync Lightning cable does not expose UART.

## References

- [SevenUP Main](https://github.com/tihmstar/SevenUP) - Transplant engine base
- [n56-patcher](https://github.com/n Edwin/n61-patcher) - Reference implementation
- [checkra1n](https://checkra.in) - DFU exploitation tool

## License

This project is for educational and repair purposes only. Use at your own risk.
