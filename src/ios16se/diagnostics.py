"""Host-side evidence from an N69 capture. Raw logs stay in the capture directory."""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .firmware import IPAD5_16_7_16, TARGET_15_8_8

SCHEMA_VERSION = 2
MODES = ("minimal", "safe-minimal", "full")
IMAGE_NAMES = {
    "iBSS": "boot_iBSS.img4", "iBEC": "boot_iBEC.img4", "DeviceTree": "boot_DT.img4",
    "TrustCache": "boot_TC.img4", "SEP": "sep.img4", "KernelCache": "boot_KC.img4",
}
BUILD = r"[0-9]{1,3}[A-Z][0-9]{1,5}[a-z]?"
MAX_REPORT_BYTES = 4 * 1024 * 1024


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def timestamp(raw):
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00").replace(" +", "+").replace(" -", "-"))
        return value.astimezone(timezone.utc) if value.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def capture_context(root, image_dir, mode, scope, images=None):
    try:
        manifest = json.loads((image_dir / "boot.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("expected an object")
        built_mode = manifest.get("kernelcache_mode")
        if built_mode not in MODES:
            status = "invalid_mode"
        elif mode is None:
            status = "requested_mode_unknown"
        else:
            status = "match" if mode == built_mode else "mismatch"
    except (OSError, ValueError):
        built_mode, status, manifest = None, "unavailable", {}
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, check=True, timeout=3,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    hashes = {}
    paths = images if images is not None else {key: image_dir / name for key, name in IMAGE_NAMES.items()}
    for key, path in paths.items():
        try:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            hashes[key] = digest.hexdigest()
        except OSError:
            hashes[key] = None
    return {
        "attempt_id": str(uuid4()), "snapshot_at_utc": utc_now(), "artifact_scope": scope,
        "requested_mode": mode, "manifest_kernelcache_mode": built_mode, "mode_check": status,
        "manifest_target": manifest.get("target"), "commit": commit, "image_sha256": hashes,
        "expected_builds": {"stock": TARGET_15_8_8.build, "donor": IPAD5_16_7_16.build},
    }


def describe_modes(context):
    return ("Requested mode: {} | Manifest mode: {} | Mode check: {}".format(
        context.get("requested_mode") or "unknown", context.get("manifest_kernelcache_mode") or "unknown",
        context.get("mode_check", "unknown")))


def read_report(path, collected_at, start=None):
    """Read Apple IPS header/body or legacy text. Keep incomplete input explicitly partial."""
    result = {
        "file": path.name, "collected_at_utc": collected_at, "attribution": "unassigned",
        "reported_timestamp": None, "reported_os": None, "reported_build": None,
        "panic_string": None, "watchdog_service": None, "time_relation": "unknown",
        "parse_status": "unrecognized",
    }
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_REPORT_BYTES + 1)
    except OSError:
        result["parse_status"] = "unreadable"
        return result
    text = raw[:MAX_REPORT_BYTES].decode("utf-8", "replace").strip()
    docs = {}
    decoder = json.JSONDecoder()
    remainder = text
    while remainder and len(raw) <= MAX_REPORT_BYTES:
        try:
            obj, end = decoder.raw_decode(remainder)
        except (ValueError, RecursionError):
            break
        if isinstance(obj, dict):
            docs.update(obj)
        remainder = remainder[end:].lstrip()
    if len(raw) > MAX_REPORT_BYTES:
        # Many IPS files still have a complete one-line header.
        try:
            obj = json.loads(text.split("\n", 1)[0])
            if isinstance(obj, dict):
                docs.update(obj)
        except (ValueError, RecursionError):
            pass
        result["parse_status"] = "partial"
    elif docs:
        result["parse_status"] = "partial" if remainder else "parsed"
    when = docs.get("timestamp") or docs.get("date")
    operating_system = docs.get("os_version") or docs.get("build")
    panic = docs.get("panicString")
    if not docs and not text.startswith(("{", "[")):
        def legacy(label):
            match = re.search(r"^" + label + r":[ \t]*(.+)$", text, re.MULTILINE | re.IGNORECASE)
            return match.group(1).strip() if match else None
        when, operating_system = legacy("Date/Time"), legacy("OS Version")
        panic = legacy("panicString") or legacy("Panic Reason")
        if any((when, operating_system, panic)) and len(raw) <= MAX_REPORT_BYTES:
            result["parse_status"] = "parsed"
    for key, value in (("reported_timestamp", when), ("reported_os", operating_system), ("panic_string", panic)):
        if isinstance(value, str):
            result[key] = value[:4096]
    build = re.search(r"\b(" + BUILD + r")\b", result["reported_os"] or "")
    result["reported_build"] = build.group(1) if build else None
    service = re.search(r"(?:no successful checkins from|no checkins from|service returned not alive.*?service\s*:)\s*([\w.-]+)",
                        result["panic_string"] or "", re.IGNORECASE)
    result["watchdog_service"] = service.group(1) if service else None
    event, lower, upper = timestamp(when), timestamp(start), timestamp(collected_at)
    if event and lower and upper and lower <= upper:
        result["time_relation"] = "before" if event < lower else "after" if event > upper else "within"
    return result


def write_evidence(out_dir, connected, info, matched, host_context, *, collected_at=None):
    fields = {}
    for line in (info or "").splitlines():
        key, sep, value = line.partition(": ")
        if sep and key in ("ProductType", "ProductVersion", "BuildVersion"):
            fields[key] = value.strip()
    context = host_context or {}
    crash = out_dir / "crash"
    reports = []
    if crash.is_dir():
        for path in sorted(crash.rglob("*")):
            if path.is_file() and not path.is_symlink():
                report = read_report(path, collected_at, context.get("bootx_sent_at_utc"))
                report["file"] = path.relative_to(out_dir).as_posix()
                reports.append(report)
    evidence = {
        "schema_version": SCHEMA_VERSION, "attempt_id": context.get("attempt_id") or str(uuid4()),
        "completed_at_utc": utc_now(), "host_context": host_context,
        "donor_kernel_entry": "unproven",
        "donor_kernel_entry_reason": "The SSH endpoint and stored reports are not tied to the current donor kernel.",
        "ssh_connected": connected,
        "device_observation": {**fields, "matches_pre_boot_ecid": matched} if fields else None,
        "stored_reports": reports,
    }
    (out_dir / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return evidence


def load_capture(directory):
    report = json.loads((directory / "evidence.json").read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("schema_version", 1) not in (1, SCHEMA_VERSION):
        raise ValueError("unsupported evidence schema")
    if "donor_kernel_entry" not in report or not isinstance(report.get("stored_reports"), list):
        raise ValueError("incomplete evidence file")
    for key in ("host_context", "device_observation"):
        if report.get(key) is not None and not isinstance(report[key], dict):
            raise ValueError("invalid " + key)
    if any(not isinstance(item, dict) for item in report["stored_reports"]):
        raise ValueError("invalid stored report")
    return report


def summary(report):
    context = report.get("host_context") or {}
    device = report.get("device_observation") or {}
    if device:
        identity = {True: "pre-boot ECID matched", False: "identity mismatch"}.get(
            device.get("matches_pre_boot_ecid"), "identity unverified")
        observed = "{} {} ({})".format(device.get("ProductType", "?"), device.get("BuildVersion", "?"), identity)
    else:
        observed = "no OS identity recorded"
    last_host = ("bootx command returned at " + context["bootx_sent_at_utc"]
                 if context.get("bootx_sent_at_utc") else "no handoff timestamp recorded")
    return "\n".join([
        "Capture: " + (report.get("attempt_id") or "unknown (legacy)"),
        describe_modes(context),
        "Last recorded device OS response: " + observed,
        "Host handoff: " + last_host,
        "Host capture completed: " + (report.get("completed_at_utc") or "unknown"),
        "SSH probe replied: " + str(report.get("ssh_connected")),
        "First unconfirmed boot stage: donor kernel entry on this SE",
        f"Stored reports awaiting attribution: {len(report['stored_reports'])}",
    ])


def comparison(left, right):
    rows = [f"First capture\n{summary(left)}", f"Second capture\n{summary(right)}"]
    a, b = left.get("host_context") or {}, right.get("host_context") or {}
    rows.append(f"Host commit: {a.get('commit') or 'unknown'} -> {b.get('commit') or 'unknown'}")
    for key in IMAGE_NAMES:
        x = (a.get("image_sha256") or {}).get(key)
        y = (b.get("image_sha256") or {}).get(key)
        change = "unknown" if not x or not y else "same" if x == y else "different"
        rows.append(f"{key}: {change}")
    return "\n".join(rows)


def public_evidence(report):
    """Project only typed fields. Free-form logs, report names and panic text remain local."""
    def token(value, pattern):
        return value if isinstance(value, str) and re.fullmatch(pattern, value) else None

    def instant(raw):
        value = timestamp(raw)
        return value.isoformat() if value else None

    context = report.get("host_context") or {}
    device = report.get("device_observation") or {}
    safe_context = {
        "requested_mode": token(context.get("requested_mode"), "|".join(MODES)),
        "manifest_kernelcache_mode": token(context.get("manifest_kernelcache_mode"), "|".join(MODES)),
        "mode_check": token(context.get("mode_check"), "match|mismatch|unavailable|invalid_mode|requested_mode_unknown"),
        "artifact_scope": token(context.get("artifact_scope"), "pre_send_snapshot|standalone_snapshot"),
        "commit": token(context.get("commit"), "[a-f0-9]{40}"),
        "bootx_sent_at_utc": instant(context.get("bootx_sent_at_utc")),
        "snapshot_at_utc": instant(context.get("snapshot_at_utc")),
        "image_sha256": {key: token((context.get("image_sha256") or {}).get(key), "[a-f0-9]{64}")
                         for key in IMAGE_NAMES},
        "expected_builds": {k: token((context.get("expected_builds") or {}).get(k), BUILD) for k in ("stock", "donor")},
    }
    matched = device.get("matches_pre_boot_ecid")
    return {
        "schema_version": SCHEMA_VERSION,
        "attempt_id": token(report.get("attempt_id"), "[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}"),
        "completed_at_utc": instant(report.get("completed_at_utc")),
        "host_context": safe_context,
        "ssh_connected": report.get("ssh_connected") is True,
        "donor_kernel_entry": "unproven",
        "device_observation": {
            "ProductType": token(device.get("ProductType"), r"iPhone[0-9]{1,2},[0-9]{1,2}"),
            "BuildVersion": token(device.get("BuildVersion"), BUILD),
            "matches_pre_boot_ecid": matched if isinstance(matched, bool) else None,
        } if device else None,
        "stored_reports": [{
            "reported_timestamp": instant(item.get("reported_timestamp")),
            "reported_build": token(item.get("reported_build"), BUILD),
            "collected_at_utc": instant(item.get("collected_at_utc")),
            "time_relation": token(item.get("time_relation"), "before|within|after|unknown"),
            "attribution": "unassigned",
        } for item in report["stored_reports"]],
    }


def public_timeline(directory):
    source = directory / "timeline.log"
    if not source.is_file() or source.is_symlink():
        return ""
    pattern = re.compile(
        r"(?:(?:idevice-id(?:-after(?:-\d+)?)?|device-info(?:-after)?|crash-report|irecovery|"
        r"system-profiler-usb|lsusb|pairing)\.txt (?:started|exit -?\d+|timeout|unavailable)"
        r"|SSH (?:collection started|collection completed|unavailable|skipped: multiple visible devices)"
        r"|usbmux observation: \d+ visible device\(s\)|device-info-after\.txt matches pre-boot ECID"
        r"|host returned from bootx; capture started|standalone capture started)"
    )
    lines = []
    with source.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            raw, _, event = line.rstrip("\n").partition(" ")
            when = timestamp(raw)
            if when and pattern.fullmatch(event):
                lines.append(f"{when.isoformat()} {event}\n")
    return "".join(lines)


def share_capture(directory, output):
    sanitized = public_evidence(load_capture(directory))
    contents = {
        "evidence.json": json.dumps(sanitized, indent=2),
        "summary.txt": summary(sanitized),
        "timeline.log": public_timeline(directory),
    }
    with output.open("xb") as handle:
        try:
            with tarfile.open(fileobj=handle, mode="w:gz") as archive:
                for name, content in contents.items():
                    data = content.encode("utf-8")
                    entry = tarfile.TarInfo(name)
                    entry.size = len(data)
                    entry.mode = 0o600
                    archive.addfile(entry, io.BytesIO(data))
        except BaseException:
            output.unlink(missing_ok=True)
            raise
