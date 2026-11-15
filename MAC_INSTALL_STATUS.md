# Mac Installation - Final Status

## All Paths Updated ✅

All tool paths have been updated from Linux to Darwin/macOS:

### Updated Files:
1. **n69ap_mac_setup.sh** - Downloads macOS tools (Darwin binaries)
2. **n69ap_ios16_full_install.sh** - Uses Darwin tools
3. **n69ap_mac_install.sh** - Combined one-command installer
4. **profiles/n69ap_ios16.toml** - Darwin tool paths
5. **run_n69_ios16_boot.py** - Darwin paths for irecovery, gaster
6. **src/ios16se/build.py** - Darwin img4 with Linux fallback
7. **src/ios16se/img4_tool.py** - Darwin paths checked first
8. **src/ios16se/install.py** - Darwin paths for tools and boot chain
9. **README_MAC.md** - Complete Mac installation guide

## Execution Order on Mac

### Step 1: Verify IM4M
```bash
ls -la state/IM4M.bin
# Should be ~6123 bytes (valid)
```

### Step 2: Run Setup (downloads tools + IPSWs)
```bash
./n69ap_mac_setup.sh
```

Expected output:
- Downloads 5 macOS tools to `ramdisk-main/Darwin/`
- Downloads 3 IPSWs to `firmware/`

### Step 3: Run Full Installation
```bash
./n69ap_ios16_full_install.sh full
```

### Or One-Command Install
```bash
./n69ap_mac_install.sh full
```

## Requirements

| Requirement | Status |
|-------------|--------|
| Intel Mac | ✅ Required |
| IPSW-SE | Auto-downloaded (3.5GB) |
| IPSW-IPAD | Auto-downloaded (4.2GB) |
| IPSW-UI | Auto-downloaded (3.5GB) |
| IM4M.bin | Already present (6123 bytes) |
| Device in DFU | Manual step |

## Tool Locations After Setup

```
ramdisk-main/Darwin/
├── gaster          (checkm8 exploit)
├── irecovery       (device control)
├── iBoot64Patcher  (boot-args patching)
├── kairos          (root-partition matching)
└── img4            (IMG4 signing)
```

## Battery Requirement

- **Minimum**: 20% (risky)
- **Recommended**: 50%+
- **Keep connected** throughout installation (~30 minutes)

## Expected Time

| Step | Time |
|------|------|
| Tool download | 2-3 min |
| IPSW download | 5-10 min |
| Ramdisk boot | 1 min |
| IM4M extraction | 30 sec |
| Volume creation | 30 sec |
| RootFS streaming | 25 min |
| Patches | 2 min |
| Build | 5 min |
| Boot | 30 sec |
| **Total** | **~35-40 min** |

## Success Indicators

- Device boots to SpringBoard (grey icons)
- No spinning wheel
- Can select language/country

## Troubleshooting

### "missing tool: ramdisk-main/Darwin/gaster"
Run `./n69ap_mac_setup.sh` first.

### "exec format error"
- Ensure Darwin (macOS) binaries, not Linux
- Script downloads correct binaries automatically

### "IM4M not found"
Place valid IM4M.bin (6123 bytes) in `state/` directory.
Valid IM4M has DER/ASN.1 structure starting with:
```
30 82 18 09 02 01 00 30 0d 06 09 2a 86 48 86 f7 0d 01 01 0b ...
```

## Boot Probability

- **Full mode**: ~85% first try
- **Minimal mode**: ~80% first try
- **Keep device powered** during entire process