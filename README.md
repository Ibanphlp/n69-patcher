# N69AP iOS 16 Tethered Boot

This project provides an automated tethered boot pipeline for the iPhone SE 1st Gen (N69AP) on iOS 16.7.16.

## Features
- **Boot Chain Build**: Full automated build of iBSS, iBEC, DT, TC, SEP, and KernelCache.
- **SevenUP Transplant Engine**: Ported for kernel driver displacement on N69AP.
- **N69AP drivers** — The project records structural checks for the PMU, display and touchscreen SPI driver. [Driver coverage](N69AP_BOOT_GUIDE.md#driver-transplants)
- **Installation Pipeline**: Includes scripts for APFS volume creation, rootfs streaming, and finalizing the installation.

## Installation Pipeline

On an Intel Mac, run `N69_EXPERIMENTAL_INSTALL_ACK=1 ./n69ap_ios16_full_install.sh full`. The installer prepares the stock 15.8.8 SSH ramdisk, captures the complete SEP IMG4 and its embedded device ticket from Preboot if needed, verifies them against the connected phone, and proceeds with the build and installation. Follow its DFU prompts; physical interaction is required between boot stages.

If multiple Preboot slots exist, set `N69_PREBOOT_HASH` to the correct 96-character slot hash. The captured device-specific files in `state/` are private; never share them. The captured SEP is copied unchanged, not re-signed from an IPSW.

Recapture the SEP after restoring the stock system or changing devices. Capture verifies the embedded ticket and its Preboot slot; the shell boot path also checks its ECID and CPID against the connected recovery device. Hardware boot success is not yet verified.

## Diagnostics

Use the [one-page capture sheet](FICHE_ESSAI.md) to record, compare and share an attempt.

To check the N69 touchscreen personality in an existing raw kernelcache, run
`python3 research/audit_n69_transplants.py --se-kernel STOCK_SE.raw --compare-spin69 CANDIDATE.raw`.
The report compares the personality label and the typed values of `CFBundleIdentifier`,
`IOClass`, `IOProviderClass` and `IONameMatch`. It records both input hashes and
whether the supplied reference matches the archived SE kernel. Exit status zero
means these fields match the supplied reference. Touch input still requires a device observation.

## Requirements
- Intel-based Mac (for DFU tool compatibility)
- iPhone SE 1st Gen (N69AP)
- Firmware IPSWs (SE 15.8.8, iPad 5 16.7.16)

## License
This project is for educational and repair purposes only. Use at your own risk.
