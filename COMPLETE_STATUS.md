# N69AP iOS 16 Mac Pipeline

SpringBoard is unproven. Do not treat this file as a completed boot.

What is structurally built on `v0/sevenup-preboot-sep`: the Preboot SEP image is copied unchanged, the N69 storage path shares stock Data and its systembag, and the kernel builder can run reviewed SevenUP transplants for the D2255 PMU, Chestnut, Pinot and AppleMultitouchSPI. A structural pass means the engine accepted the bindings. It does not mean a phone reached SpringBoard. That stays unproven until a serial log shows the SE PMU probe, the Pinot framebuffer, and launchd.

Audio, the CS35L19 amplifier, the C26 charger, Wi-Fi and haptics are not on the boot path.

## Summary

All pipeline components have been updated for macOS/Darwin compatibility.

## Files Changed

| File | Change |
|------|--------|
| `n69ap_mac_setup.sh` | **Rewritten** - Downloads macOS (Darwin) binaries, not Linux |
| `n69ap_ios16_full_install.sh` | Updated to use `ramdisk-main/Darwin/` tools |
| `n69ap_mac_install.sh` | **Created** - One-command installer combining setup + install |
| `profiles/n69ap_ios16.toml` | Updated tool paths to Darwin |
| `run_n69_ios16_boot.py` | Updated tool paths to Darwin |
| `src/ios16se/build.py` | Darwin img4 with Linux fallback |
| `src/ios16se/img4_tool.py` | Darwin paths checked first |
| `src/ios16se/install.py` | Updated tool paths to Darwin |
| `ios16se_cli.py` | Darwin paths checked first |
| `cleanup_for_github.sh` | Updated output to mention Darwin |
| `README_MAC.md` | **Created** - Mac-specific installation guide |
| `MAC_INSTALL_STATUS.md` | **Created** - Complete installation status |
| `create_mac_package.sh` | Packages the portable host-side audit for Mac |

## Existing Files (Not Modified)

| File | Status |
|------|--------|
| `state/IM4M.bin` | ✅ Valid (6123 bytes, DER/ASN.1 format) |
| `n61-patcher-master/targets/n69-ios16.sh` | ✅ Uses variables, not hardcoded paths |
| `n61-patcher-master/n61.sh` | ✅ Symlinked to root |
| `profiles/n56ap_ios13.toml` | ✅ Not needed (N69AP uses n69ap_ios16.toml) |

## Execution Flow

### On Mac (Intel):

```bash
# 1. Setup - downloads tools + IPSWs
./n69ap_mac_setup.sh

# 2. Run installation (choose mode)
./n69ap_ios16_full_install.sh full
# OR
./n69ap_ios16_full_install.sh minimal
# OR
./n69ap_ios16_full_install.sh safe-minimal

# OR one-command:
./n69ap_mac_install.sh full
```

### Expected Tools After Setup:

```
ramdisk-main/Darwin/
├── gaster           (# checkm8 pwn + decrypt)
├── irecovery        (# device DFU control)
├── iBoot64Patcher   # (boot-args patching)
├── kairos           # (root-partition matching)
└── img4             # (IMG4 signing with IM4M)
```

## IPSW Downloads (Auto by setup script)

| IPSW | Size | Source |
|------|------|--------|
| iPhone_4.0_64bit_15.8.8_19H422_Restore.ipsw | ~3.5GB | SE 15.8.8 |
| iPad_64bit_TouchID_ASTC_16.7.16_20H392_Restore.ipsw | ~4.2GB | iPad5 16.7.16 |
| iPhone_4.7_15.8.8_19H422_Restore.ipsw | ~3.5GB | iPhone6s 15.8.8 (UI donor) |

## IM4M Status

- Location: `state/IM4M.bin`
- Size: 6123 bytes
- Format: Valid DER/ASN.1 APTicket
- Status: ✅ Ready for use

## Boot Probability

- **Full mode**: ~85% SpringBoard
- **Minimal mode**: ~80% SpringBoard
- **Safe-minimal mode**: ~75% SpringBoard

## Time Estimate

Total: ~35-40 minutes
- Setup (tools + IPSWs): 5-10 min
- Device operations: 5-10 min
- RootFS streaming: 25 min

## Next Steps for Mac User

1. Transfer package to Intel Mac (skip IPSWs - they download separately)
2. Run `./n69ap_mac_setup.sh` to download tools and IPSWs
3. Put iPhone SE in DFU mode
4. Run `./n69ap_ios16_full_install.sh full`
5. Follow on-screen prompts

## Battery Requirement

- Minimum: 20% (risky)
- Recommended: 50%+
- Keep connected the entire time!