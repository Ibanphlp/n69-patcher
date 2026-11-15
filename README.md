# N69AP iOS 16 Tethered Boot

This project provides an automated tethered boot pipeline for the iPhone SE 1st Gen (N69AP) on iOS 16.7.16.

## Features
- **Boot Chain Build**: Full automated build of iBSS, iBEC, DT, TC, SEP, and KernelCache.
- **SevenUP Transplant Engine**: Ported for kernel driver displacement on N69AP.
- **Full Patch Pipeline**: Implements PMU, Touch, Display, Audio, WiFi, SEP, and Charger patches.
- **Installation Pipeline**: Includes scripts for APFS volume creation, rootfs streaming, and finalizing the installation.

## Installation Pipeline

1. **Build Boot Images**: `./run_boot.sh full`
2. **Setup Device**: Connect SE 1st Gen in DFU mode.
3. **Full Install**: Use the `n69ap_ios16_full_install.sh` script on an Intel Mac.

## Requirements
- Intel-based Mac (for DFU tool compatibility)
- iPhone SE 1st Gen (N69AP)
- Firmware IPSWs (SE 15.8.8, iPad 5 16.7.16)

## License
This project is for educational and repair purposes only. Use at your own risk.
