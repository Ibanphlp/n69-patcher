#!/usr/bin/env python3
"""Read-only SE/iPad kernel driver compatibility audit (no device operations).

The transplant trials run SevenUP's stricter engine in a temporary directory;
passing one is not a runtime or cross-version ABI validation.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'SevenUP-main' / 'src'))
from iphone7_port.kc import KC
from iphone7_port.kernel_transplant import apply

CASES = {
    'pmu': ('com.apple.driver.AppleD2365PMU', 'com.apple.driver.AppleD2255PMU'),
    'display': ('com.apple.driver.AppleANXDPTX', 'com.apple.driver.ApplePinotLCD'),
    'touch': ('com.apple.driver.AppleMultitouchSPIC1F15', 'com.apple.driver.AppleMultitouchSPIN69'),
    'audio_codec': ('com.apple.driver.AppleANXDPTX', 'com.apple.driver.AppleCS42L71Audio'),
    'audio_amplifier': ('com.apple.driver.AppleAE2Audio', 'com.apple.driver.AppleCS35L19Amp'),
    'charger': ('com.apple.driver.AppleHDQGasGaugeControl', 'com.apple.driver.AppleC26Charger'),
}
PARTS = ('__TEXT', '__TEXT_EXEC', '__DATA', '__DATA_CONST')


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def audit(donor_path: Path, target_path: Path, trial: bool = True) -> dict:
    target, donor = KC(target_path), KC(donor_path)
    result = {'target_kernel_sha256': digest(target_path),
              'se_kernel_sha256': digest(donor_path), 'drivers': {},
              'warning': 'Research only; neither a successful transplant nor a build proves SE runtime or SEP compatibility.'}
    for name, (old, new) in CASES.items():
        entry = {'footprint': old, 'se_driver': new,
                 'target_footprint_present': old in target.kexts,
                 'se_driver_present': new in donor.kexts}
        result['drivers'][name] = entry
        if not entry['target_footprint_present'] or not entry['se_driver_present']:
            entry['trial'] = 'missing input driver'
            continue
        deps = donor.kexts[new].get('OSBundleLibraries', {})
        entry['missing_dependencies'] = sorted(x for x in deps if x not in target.kexts)
        try:
            target_segments = {s['name']: s['size'] for s in target.ksegments(old)}
            donor_segments = {s['name']: s['size'] for s in donor.ksegments(new)}
            entry['segments'] = {key: {'target_bytes': target_segments.get(key),
                                       'se_bytes': donor_segments.get(key)} for key in PARTS}
        except (AssertionError, KeyError, ValueError, TypeError) as e:
            entry['segment_error'] = f'{type(e).__name__}: {e}'
        if trial:
            try:
                with tempfile.TemporaryDirectory(prefix='n69-transplant-audit-') as tmp:
                    apply(target_path, donor_path, Path(tmp), old, new, None)
                entry['trial'] = 'structural trial passed; runtime ABI remains unverified'
            except Exception as e:
                entry['trial'] = f'rejected: {type(e).__name__}: {e}'
        else:
            entry['trial'] = 'not run'
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ipad-kernel', type=Path, default=ROOT/'work/build/donor_kc.raw')
    parser.add_argument('--se-kernel', type=Path, default=ROOT/'artifacts/n69ap-15.8.8/KernelCache.raw')
    parser.add_argument('--no-trials', action='store_true', help='inspect inputs only')
    args = parser.parse_args()
    print(json.dumps(audit(args.se_kernel, args.ipad_kernel, not args.no_trials), indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
