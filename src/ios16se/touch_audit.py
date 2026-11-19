"""Read-only comparison of the N69 tactile personality with a supplied SE kernel."""

import hashlib

from .prelink import MATCH_FIELDS, PrelinkError, personality_match_fields


SPIN = "com.apple.driver.AppleMultitouchSPIN69"


def compare_spin69(reference: bytes, candidate: bytes) -> dict:
    report = {
        "bundle": SPIN,
        "reference_sha256": hashlib.sha256(reference).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "hardware_initialization": "unobserved",
    }
    try:
        label, expected = personality_match_fields(reference, SPIN)
        if (expected["CFBundleIdentifier"] != "com.apple.driver.AppleMultitouchSPI"
                or expected["IOClass"] != "AppleMultitouchN1SPI"
                or expected["IONameMatch"] != ["multi-touch,n69"]):
            raise PrelinkError("la référence ne déclare pas la personnalité tactile N69 attendue")
    except PrelinkError as error:
        return {**report, "status": "invalid_reference", "reason": str(error)}
    try:
        actual_label, actual = personality_match_fields(candidate, SPIN)
    except PrelinkError as error:
        return {**report, "status": "unverified", "reason": str(error)}
    differences = {
        key: {"expected": expected[key], "actual": actual[key]}
        for key in MATCH_FIELDS if expected[key] != actual[key]
    }
    if label != actual_label:
        differences["personality_label"] = {"expected": label, "actual": actual_label}
    return {
        **report, "status": "mismatch" if differences else "match",
        "personality_label": label, "reference_fields": expected, "differences": differences,
    }
