"""Summarize the configured modes against the two archived N69 reference inputs."""

import json

from .build import load_config
from .kc_patch import BOOT_MODES, patches_for_mode


# SPI and its codeless personality share a selection flag, but distinct checks.
COMPONENTS = (
    ("D2255 PMU", "pmu_d2255", "pmu", "pmu", "device PMU initialization"),
    ("Pinot", "display_pinot", "display", "display", "device display output"),
    ("Chestnut", "chestnut", "chestnut", None, "display power initialization"),
    ("Multitouch SPI", "touch_spin69", "spi", "touch", "SPI service attachment"),
    ("SPIN69", "touch_spin69", "spin69", None, "matched personality and touch input"),
    ("SEP", "sep_legacy", "sep", None, "response from a SEP service"),
    ("CS42L71", "audio_cs42l71", None, None, "codec output"),
    ("CS35L19", "audio_cs35l19", None, None, "amplifier output"),
    ("C26", "charger_c26", None, None, "charger response"),
    ("Broadcom Wi-Fi", "wifi_bcm", None, None, "network interface and traffic"),
    ("DRV2604", "vibration_drv2604", None, None, "haptic output"),
)


def coverage(root):
    audit = json.loads((root / "research/n69-compatibility-report.json").read_text())
    metadata = json.loads((root / "research/n69-reference-metadata.json").read_text())
    for key in ("target_kernel_sha256", "se_kernel_sha256"):
        if metadata[key] != audit[key]:
            raise ValueError("Reference metadata and structural audit identify different inputs")
    profile = load_config(root / "profiles/n69ap_ios16.toml")
    modes = {name: patches_for_mode(name, profile.kernelcache_patches) for name in BOOT_MODES}
    rows = [
        f"Reference stock kernels: SE {audit['se_kernel_sha256']}, iPad {audit['target_kernel_sha256']}",
        "Offline reference only. ABI compatibility unknown for every row.",
        "Component | " + " | ".join(modes) + " | Reference trial | Declared dependencies | Missing hardware observation",
    ]
    for name, flag, record, trial, missing in COMPONENTS:
        selection = ["selected" if flag in flags else "off" for flags in modes.values()]
        finding = audit["drivers"].get(trial, {})
        check = finding.get("trial", "not tested")
        if "bindings" in finding:
            check += f" ({finding['bindings']} address mappings)"
        entry = metadata["metadata"].get(record, {})
        dependencies = ", ".join(entry.get("dependencies", [])) or "unexamined"
        if entry.get("kind") == "personality_only":
            check += " (personality only)"
        rows.append(" | ".join([name, *selection, check, dependencies, missing]))
    return "\n".join(rows)
