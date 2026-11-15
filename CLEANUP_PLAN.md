# Project Cleanup Plan for GitHub Release

## Directory Structure After Cleanup

```
iOS-16-SE/
├── .github/
│   └── workflows/
├── firmware/                 # User places IPSWs here (gitignored)
├── n61-patcher/              # Core patcher (bash + python)
│   ├── core/
│   ├── lib/
│   ├── patches/
│   │   ├── kc/n69/           # N69-specific KC patches
│   │   ├── rootfs/           # RootFS patches
│   │   ├── registry_n69_ios16.tsv
│   │   └── registry.tsv      # Original (keep for reference)
│   ├── targets/
│   │   ├── n69-ios16.sh      # Main target
│   │   └── n69_ios16.py      # Python target
│   ├── n61.sh                # Main entry point
│   └── n56patcher.py
├── src/ios16se/              # Python orchestration
│   ├── sevenup/              # Transplant engine
│   ├── build.py
│   ├── cli.py
│   └── ...
├── profiles/
│   └── n69ap_ios16.toml
├── scripts/
│   ├── create_package.sh
│   ├── n69ap_ios16_full_install.sh
│   └── n69ap_mac_setup.sh
├── run_boot.sh
├── run_n69_ios16_boot.py
├── README.md
├── LICENSE
├── .gitignore
└── requirements.txt
```

---

## Files to REMOVE (Not Needed for N69AP iOS 16)

### 1. Upstream Projects (Keep as Submodules or Remove)
```
n56-patcher/          # Entire directory - upstream reference only
SevenUP-main/         # Entire directory - upstream reference only
ipad6-ipados18-master/
ipodtouch6-ios13-main/
kairos-master/
```

### 2. Old/Deprecated Root Scripts
```
boot_ios_16_se.sh
boot_se.py
build.py              # Old, replaced by src/ios16se/build.py
full_boot.sh
kernel_launcher.py
patch_kernelcache.py
capture_logs.sh       # Functionality in run_n69_ios16_boot.py
```

### 3. Duplicate/Old Docs
```
AUDIT_SUMMARY_TABLE.md
N69AP_Installation_Next_Steps.md
PROJECT_AUDIT.md
PROJECT_COMPLETION_TABLE.md
REMAINING_TASKS.md
IMPLEMENTATION_PLAN.md
NEXT_STEPS.md
RAMDISK_EXTRACTION.md
README_BOOT.md
docs/baseline.md
docs/firmware-inputs.md
docs/tethered-boot.md
```

### 4. Old Profile/Config
```
profiles/n69ap.toml
pyproject.toml (root - not used)
```

### 5. Old Target/Registry
```
n61-patcher-master/targets/n69_ios16.py
n61-patcher-master/patches/registry.tsv  (keep only registry_n69_ios16.tsv)
```

### 6. Shell Scripts in Patches Directory (Not Used)
```
n61-patcher-master/patches/rootfs/n69_finish.sh
n61-patcher-master/patches/rootfs/n69_set_roles.sh
n61-patcher-master/patches/rootfs/n69_ios16_install.py
```

### 6. Unused Tools/Directories
```
tools/re_pinot_analyze.py
ramdisk-main/ (except Linux binaries needed)
n61-patcher-master/tools/ (most - keep only extract_ipsw.py, setup.py)
```

### 7. Test Files
```
tests/ (root level)
n61-patcher-master/tests/ (if any)
SevenUP-main/tests/
n56-patcher/tests/
```

### 8. Reports (Generated, Not Source)
```
reports/
artifacts/ (generated)
work/ (generated)
state/ (generated, except IM4M.bin placeholder)
```

---

## Files to KEEP (Essential)

### Core Entry Points
```
run_boot.sh
run_n69_ios16_boot.py
n69ap_ios16_full_install.sh
n69ap_mac_setup.sh
create_mobile_package.sh
```

### Core Python Package
```
src/ios16se/
  ├── __init__.py
  ├── build.py
  ├── cli.py
  ├── boot.py
  ├── device.py
  ├── devicetree.py
  ├── firmware.py
  ├── host.py
  ├── iboot_patch.py
  ├── img4.py
  ├── img4_tool.py
  ├── kc_patch.py
  ├── kc_patch_drivers.py
  ├── macho.py
  ├── manifest.py
  ├── prelink.py
  ├── session.py
  ├── sevenup/ (complete transplant engine)
  └── tests/ (unit tests)
```

### n61-patcher Core
```
n61-patcher-master/
  ├── n61.sh
  ├── n56patcher.py
  ├── core/
  ├── lib/
  ├── patches/
  │   ├── kc/n69/ (7 files)
  │   ├── rootfs/ (18 .py files - the actual patches)
  │   ├── registry_n69_ios16.tsv
  │   └── registry.tsv (reference)
  ├── targets/
  │   ├── n69-ios16.sh
  │   └── n69_ios16.py
  ├── n56patcher.py
  └── tools/extract_ipsw.py
```

### Configuration
```
profiles/n69ap_ios16.toml
profiles/n69ap_ios16.toml
```

### Documentation (Consolidated)
```
README.md
N69AP_BOOT_GUIDE.md
FINAL_AUDIT_TABLE.md
LICENSE
requirements.txt
```

### Scripts
```
scripts/
  ├── create_mobile_package.sh
  ├── n69ap_ios16_full_install.sh
  └── n69ap_mac_setup.sh
```

### Runtime (gitignored)
```
firmware/ (user provides IPSWs)
artifacts/ (generated)
work/ (generated)
state/IM4M.bin (placeholder)
```

---

## .gitignore

```
# User-provided firmware
firmware/*.ipsw
firmware/*.dmg

# Generated artifacts
artifacts/
work/
state/IM4M.bin

# Python
__pycache__/
*.pyc
*.pyo
.pytest_cache/

# OS
.DS_Store
Thumbs.db

# IDE
.idea/
.vscode/
*.swp
*.swo

# Logs
*.log
pwn.log
```

---

## Cleanup Commands

```bash
# Remove upstream projects (keep as submodules if needed)
rm -rf n56-patcher/ SevenUP-main/ ipad6-ipados18-master/ ipodtouch6-ios13-main/ kairos-master/

# Remove old root scripts
rm -f boot_ios_16_se.sh boot_se.py build.py full_boot.sh kernel_launcher.py patch_kernelcache.py capture_logs.sh

# Remove old docs
rm -f AUDIT_SUMMARY_TABLE.md N69AP_Installation_Next_Steps.md PROJECT_AUDIT.md PROJECT_COMPLETION_TABLE.md REMAINING_TASKS.md IMPLEMENTATION_PLAN.md NEXT_STEPS.md RAMDISK_EXTRACTION.md README_BOOT.md
rm -rf docs/

# Remove old configs
rm -f profiles/n69ap.toml pyproject.toml

# Remove duplicate patches/scripts
rm -f n61-patcher-master/targets/n69_ios16.py
rm -f n61-patcher-master/patches/registry.tsv
rm -f n61-patcher-master/patches/rootfs/n69_finish.sh
rm -f n61-patcher-master/patches/rootfs/n69_set_roles.sh
rm -f n61-patcher-master/patches/rootfs/n69_ios16_install.py

# Remove unused tools
rm -f tools/re_pinot_analyze.py
rm -rf ramdisk-main/Darwin/ ramdisk-main/Windows/ (keep only Linux/)

# Remove test directories
rm -rf tests/ n61-patcher-master/tests/ SevenUP-main/tests/ n56-patcher/tests/

# Remove reports
rm -rf reports/

# Remove generated dirs (will be recreated)
rm -rf artifacts/ work/ state/
mkdir -p state firmware
touch state/.gitkeep firmware/.gitkeep

# Move scripts to scripts/
mkdir -p scripts
mv create_mobile_package.sh n69ap_ios16_full_install.sh n69ap_mac_setup.sh scripts/

# Move docs to docs/
mkdir -p docs
mv N69AP_BOOT_GUIDE.md FINAL_AUDIT_TABLE.md docs/
```

---

## Final Repo Size Estimate

| Component | Size |
|-----------|------|
| Source code | ~2 MB |
| n61-patcher | ~3 MB |
| SevenUP (transplant engine) | ~1 MB |
| Docs | ~200 KB |
| Scripts | ~50 KB |
| **Total** | **~6-7 MB** (vs 100+ MB currently) |

---

## GitHub Release Checklist

- [ ] Run cleanup commands
- [ ] Create .gitignore
- [ ] Create LICENSE (MIT or similar)
- [ ] Create requirements.txt
- [ ] Write comprehensive README.md
- [ ] Add .github/workflows/ci.yml (optional)
- [ ] Create release tags
- [ ] Write release notes