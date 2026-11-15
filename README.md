# N69AP iOS 16 Tethered Boot

This project provides an automated tethered boot pipeline for the iPhone SE 1st Gen (N69AP) on iOS 16.7.16.

## Features
- **Boot Chain Build**: Full automated build of iBSS, iBEC, DT, TC, SEP, and KernelCache.
- **SevenUP Transplant Engine**: Ported for kernel driver displacement on N69AP.
- **Full Patch Pipeline**: Implements PMU, Touch, Display, Audio, WiFi, SEP, and Charger patches.
- **Installation Pipeline**: Includes scripts for APFS volume creation, rootfs streaming, and finalizing the installation.

## Installation Pipeline

1. Boot the stock 15.8.8 SSH ramdisk without SEP: `N61_RAMDISK_SEP=0 ./n61.sh ramdisk --target n69-ios16`.
2. Capture the complete SEP IMG4 from Preboot and its embedded device ticket: `./n61.sh capture-sep --target n69-ios16`. If multiple Preboot slots exist, set `N69_PREBOOT_HASH` to the correct 96-character slot hash. This writes private device-specific files under `state/`; never share them.
3. Return the device to DFU and build boot images: `./run_boot.sh full`. The captured SEP is copied unchanged, not re-signed from an IPSW.
4. Use `n69ap_ios16_full_install.sh` on an Intel Mac for the experimental installation.

Recapture the SEP after restoring the stock system or changing devices. Capture verifies the embedded ticket and its Preboot slot; the shell boot path also checks its ECID and CPID against the connected recovery device. Hardware boot success is not yet verified.

## Requirements
- Intel-based Mac (for DFU tool compatibility)
- iPhone SE 1st Gen (N69AP)
- Firmware IPSWs (SE 15.8.8, iPad 5 16.7.16)

## License
This project is for educational and repair purposes only. Use at your own risk.
