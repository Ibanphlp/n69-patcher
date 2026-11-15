Source: https://github.com/0x7ff/gaster (main downloaded 2026-11-14).
Upstream license is retained in this directory.

Local macOS changes:
- Match IOUSBHostDevice with vendor/product under IOPropertyMatch.
- Report USB service matching and interface/open/configuration failures.

Rebuild in this directory with make macos, then copy gaster to
../../Darwin/gaster. Original binary: ../../Darwin/gaster.legacy.

Validation: builds on the local Intel Mac. The live DFU test reaches
Matched DFU USB service, then still blocks while opening the USB device.
Successful exploitation and ramdisk boot have not been verified.
The shell script limits each gaster pwn/reset operation to 90 seconds.
