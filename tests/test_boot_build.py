import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ios16se.build import decrypt_iboot, verify_kc_roundtrip, BuildError
from ios16se.kc_patch import RemoveIpadDriversPatch, apply_patches
from ios16se.sevenup.kc import plist
from ios16se.append_wt_fix import extract_watchtower_tail

class BuildTests(unittest.TestCase):
    def test_prelink_removed_definitions_keep_types(self):
        xml = (b'<dict><key>_PrelinkInfoDictionary</key><array>'
               b'<dict><key>CFBundleIdentifier</key><string>com.test.Old</string>'
               b'<key>num</key><integer ID="1">123</integer>'
               b'<key>word</key><string ID="2">hello</string></dict>'
               b'<dict><key>CFBundleIdentifier</key><string>com.test.Keep</string>'
               b'<key>num</key><integer IDREF="1"/><key>word</key><string IDREF="2"/></dict>'
               b'</array></dict>')
        from ios16se.sevenup.kernel_transplant import entry_range
        out = RemoveIpadDriversPatch._remove_entries(xml, [entry_range(xml, 'com.test.Old')])
        kept = plist(out)['_PrelinkInfoDictionary'][0]
        self.assertEqual((kept['num'], kept['word']), (123, 'hello'))
        self.assertNotIn(b'<value>', out)

    def test_decrypt_uses_distinct_inputs_and_rejects_legacy_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); ibss = p/'ibss.im4p'; ibec = p/'ibec.im4p'
            ibss.write_bytes(b'ibss source'); ibec.write_bytes(b'ibec source')
            tool = p/'tool'; tool.write_bytes(b'patcher')
            work = p/'decrypt'; work.mkdir(); (work/'iBSS.patched').write_bytes(b'old')
            (work/'iBEC.dec').write_bytes(b'old')
            calls = []
            def gaster(_tool, args):
                calls.append(args[1]); Path(args[2]).write_bytes(Path(args[1]).read_bytes())
            def patch_ibss(cmd, _label): Path(cmd[2]).write_bytes(Path(cmd[1]).read_bytes()+b' patched')
            with patch('ios16se.build.check_device_pwned'), patch('ios16se.build.run_gaster', side_effect=gaster), patch('ios16se.build.run_checked', side_effect=patch_ibss):
                a,b = decrypt_iboot(ibss, ibec, work, tool, tool, 'n69ap', tool)
                self.assertEqual(calls, [str(ibss),str(ibec)])
                self.assertNotEqual(a.read_bytes(), b.read_bytes())
                decrypt_iboot(ibss, ibec, work, tool, tool, 'n69ap', tool)
                self.assertEqual(len(calls), 2)

    def test_unvalidated_hardware_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, 'Unvalidated'):
            apply_patches(Path('x'), ['sep_legacy'], Path('x'))

    def test_watchtower_refuses_guessed_tail(self):
        with self.assertRaises(ValueError): extract_watchtower_tail(b'X'*100000)

    def test_roundtrip_checks_full_payload_and_ticket(self):
        def der(tag, data):
            length = len(data)
            encoded = bytes([length]) if length < 128 else bytes([0x81, length])
            return bytes([tag]) + encoded + data
        def string(value): return der(0x16, value)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); original = p/'original'; signed = p/'signed'; ticket = p/'ticket'
            original.write_bytes(b'original payload'); ticket.write_bytes(der(0x30, string(b'IM4M')))
            im4p = der(0x30, string(b'IM4P') + string(b'rkrn') + string(b'kernel') + der(4, original.read_bytes()))
            signed.write_bytes(der(0x30, string(b'IMG4') + im4p + der(0xa0, ticket.read_bytes())))
            self.assertTrue(verify_kc_roundtrip(signed, original, ticket, p/'unused'))
            original.write_bytes(b'wrong payload')
            self.assertFalse(verify_kc_roundtrip(signed, original, ticket, p/'unused'))
            original.write_bytes(b'original payload'); ticket.write_bytes(b'wrong ticket')
            self.assertFalse(verify_kc_roundtrip(signed, original, ticket, p/'unused'))

if __name__ == '__main__': unittest.main()
