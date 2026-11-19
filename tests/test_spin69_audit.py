import contextlib
import io
import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research import audit_n69_transplants as audit
from test_prelink_inspection import kernel


LABEL = "AppleMultitouchSPI - C1,N69"
FIELDS = {
    "CFBundleIdentifier": "com.apple.driver.AppleMultitouchSPI",
    "IOClass": "AppleMultitouchN1SPI",
    "IOProviderClass": "AppleARMSPIDevice",
    "IONameMatch": ["multi-touch,n69"],
}


def personality_xml(personalities):
    entry = {"CFBundleIdentifier": audit.SPIN, "IOKitPersonalities": personalities}
    return plistlib.dumps({"_PrelinkInfoDictionary": [entry]})


class Spin69AuditTests(unittest.TestCase):
    def audit_candidate(self, **changes):
        with tempfile.TemporaryDirectory() as tmp:
            se, candidate = Path(tmp) / "stock", Path(tmp) / "candidate"
            se.write_bytes(kernel(personality_xml({LABEL: FIELDS})))
            candidate.write_bytes(kernel(personality_xml({LABEL: {**FIELDS, **changes}})))
            out = io.StringIO()
            argv = ["audit", "--se-kernel", str(se), "--compare-spin69", str(candidate)]
            with patch("sys.argv", argv), contextlib.redirect_stdout(out):
                status = audit.main()
            return status, json.loads(out.getvalue())

    def test_wrong_provider_with_both_old_search_strings_is_rejected(self):
        status, report = self.audit_candidate(IOProviderClass="FixtureWrongProvider")
        self.assertEqual(status, 1)
        self.assertEqual(report["differences"], {
            "IOProviderClass": {"expected": "AppleARMSPIDevice", "actual": "FixtureWrongProvider"},
        })

    def test_equal_fields_with_extra_unrelated_property(self):
        status, report = self.audit_candidate(FixtureNote="extra property")
        self.assertEqual(status, 0)
        self.assertEqual(report["status"], "match")
        self.assertEqual(report["hardware_initialization"], "unobserved")
        self.assertFalse(report["reference_matches_archived_se"])

    def test_each_typed_field_is_compared_in_place(self):
        for key, value in (("CFBundleIdentifier", "wrong.bundle"), ("IOClass", "OtherClass"),
                           ("IONameMatch", ["multi-touch,j72"]), ("IONameMatch", "multi-touch,n69")):
            with self.subTest(key=key, value=value):
                status, report = self.audit_candidate(**{key: value}, FixtureNote="multi-touch,n69 AppleMultitouchN1SPI")
                self.assertEqual(status, 1)
                self.assertIn(key, report["differences"])

    def test_missing_ambiguous_and_mislabelled_personalities(self):
        reference = kernel(personality_xml({LABEL: FIELDS}))
        incomplete = FIELDS.copy()
        del incomplete["IOProviderClass"]
        for personalities, status in (
            ({LABEL: incomplete}, "unverified"),
            ({LABEL: FIELDS, "Another": FIELDS}, "unverified"),
            ({"Another": FIELDS}, "mismatch"),
        ):
            with self.subTest(personalities=personalities):
                result = audit.compare_spin69(reference, kernel(personality_xml(personalities)))
                self.assertEqual(result["status"], status)

    def test_matching_through_an_xml_reference(self):
        xml = personality_xml({LABEL: FIELDS})
        indirect = xml.replace(b"<string>multi-touch,n69</string>", b'<string IDREF="1"/>')
        indirect = indirect.replace(b"<key>_PrelinkInfoDictionary</key>",
                                    b'<key>NativeName</key><string ID="1">multi-touch,n69</string>'
                                    b'<key>_PrelinkInfoDictionary</key>')
        self.assertEqual(audit.compare_spin69(kernel(xml), kernel(indirect))["status"], "match")
