#!/usr/bin/env python3
"""Structural SE/iPad transplant audit.

A pass means SevenUP's engine accepted reviewed bindings for that driver.
It does not mean the phone reached SpringBoard.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "SevenUP-main" / "src"))

from ios16se.driver_transplant import (  # noqa: E402
    CHESTNUT, DISPLAY_FOOTPRINT, PINOT, PMU_DONOR, PMU_FOOTPRINT, SPI, SPIN, BindingBook,
    transplant_footprint,
)

from ios16se.prelink import inspect_record, PrelinkError  # noqa: E402

CASES = (
    ("pmu", PMU_FOOTPRINT, PMU_DONOR),
    ("display", DISPLAY_FOOTPRINT, PINOT),
    ("touch", SPI, SPI),
)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def metadata_evidence(se_kernel: Path, ipad_kernel: Path, bundle: str) -> dict:
    try:
        record = inspect_record(se_kernel, bundle)
    except (OSError, PrelinkError) as error:
        return {"status": "unavailable", "reason": str(error)}
    target_dependencies = {}
    for dependency in record.dependencies:
        try:
            target_record = inspect_record(ipad_kernel, dependency)
            target_dependencies[dependency] = {
                "status": "declared",
                "executable_size": target_record.executable_size,
            }
        except (OSError, PrelinkError) as error:
            target_dependencies[dependency] = {"status": "unavailable", "reason": str(error)}
    return {
        "status": "read",
        "bundle": bundle,
        "kind": "personality_only" if record.executable_size is None else "executable",
        "executable_size": record.executable_size,
        "dependencies": list(record.dependencies),
        "personalities": list(record.personalities),
        "target_dependencies": target_dependencies,
        "abi_compatibility": "unknown",
        "hardware_initialization": "unobserved",
        "limit": "Metadata presence does not establish call arguments, object layout or initialization order.",
    }


def audit(se_kernel: Path, ipad_kernel: Path, *, inspect_only: bool = False) -> dict:
    result = {
        "schema_version": 2,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "boot_mode": None,
        "full_pipeline_tested": False,
        "scope": "Ordered footprint trials, not an execution of a boot mode",
        "target_kernel_sha256": digest(ipad_kernel),
        "se_kernel_sha256": digest(se_kernel),
        "springboard_boot": "unproven",
        "note": (
            "A structural pass means the SevenUP engine accepted the reviewed "
            "bindings. The touch trial covers the SPI driver only. "
            "Physical PMU, display and touchscreen operation remain unverified."
        ),
        "binding_count_definition": (
            "Distinct private donor-address to target-address mappings returned by bind_bundle; "
            "not all calls, all relocations or verified ABI contracts."
        ),
        "not_tested": {
            "chestnut": "No Chestnut transplant in these footprint trials",
            "spin69": "SPI trial does not install the SPIN69 personality",
            "sep": "No firmware or service trial in this audit",
        },
        "metadata": {
            name: metadata_evidence(se_kernel, ipad_kernel, bundle)
            for name, bundle in (
                ("pmu", PMU_DONOR), ("display", PINOT), ("spi", SPI),
                ("spin69", SPIN), ("chestnut", CHESTNUT),
                ("sep", "com.apple.driver.AppleSEPManager"),
            )
        },
        "drivers": {},
    }
    if inspect_only:
        result["scope"] = "Metadata inspection only; no transplant trials executed"
        result["structural_pass"] = None
        return result
    book = BindingBook(se_kernel, ipad_kernel)
    with tempfile.TemporaryDirectory(prefix="n69-audit-") as scratch:
        current = ipad_kernel
        failed = None
        for name, footprint, donor in CASES:
            entry = {
                "footprint": footprint, "se_driver": donor,
                "abi_compatibility": "unknown", "hardware_initialization": "unobserved",
            }
            result["drivers"][name] = entry
            if failed is not None:
                entry["trial"] = f"not run after {failed} failed"
                continue
            entry["input_sha256"] = digest(current)
            try:
                work = Path(scratch) / name
                work.mkdir()
                produced = transplant_footprint(current, se_kernel, work, footprint, donor, book)
                entry["bindings"] = len(book.bind_bundle(donor))
                entry["output_sha256"] = digest(produced)
                entry["trial"] = "structural pass; SevenUP accepted the reviewed bindings"
                current = produced
            except Exception as error:
                entry["trial"] = f"rejected: {type(error).__name__}: {error}"
                failed = name
    passed = [name for name, info in result["drivers"].items() if info["trial"].startswith("structural pass")]
    result["structural_pass"] = len(passed) == len(CASES)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ipad-kernel", type=Path, default=ROOT / "work/build/donor_kc.raw")
    parser.add_argument("--se-kernel", type=Path, default=ROOT / "artifacts/n69ap-15.8.8/KernelCache.raw")
    parser.add_argument("--write", type=Path, default=None, help="also write the JSON report here")
    parser.add_argument("--inspect-only", action="store_true",
                        help="read metadata and input hashes without running transplant trials")
    args = parser.parse_args()
    if not args.ipad_kernel.is_file() or not args.se_kernel.is_file():
        print(f"kernels missing: {args.ipad_kernel} {args.se_kernel}", file=sys.stderr)
        return 2
    if args.write and args.write.exists() and any(
        args.write.samefile(kernel) for kernel in (args.se_kernel, args.ipad_kernel)
    ):
        print("Report destination refers to an input kernel.", file=sys.stderr)
        return 2
    report = audit(args.se_kernel, args.ipad_kernel, inspect_only=args.inspect_only)
    text = json.dumps(report, indent=2)
    print(text)
    if args.write:
        args.write.write_text(text + "\n")
    return 0 if args.inspect_only or report["structural_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
