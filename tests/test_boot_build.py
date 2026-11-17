import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ios16se.build import build_devicetree, decrypt_iboot, verify_kc_roundtrip, BuildError
from ios16se.kc_patch import RemoveIpadDriversPatch, apply_patches
from ios16se.sevenup.kc import plist
from ios16se.append_wt_fix import extract_watchtower_tail

class BuildTests(unittest.TestCase):
    def test_img4_preflight_stops_before_device_on_ticket_mismatch(self):
        import os
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            boot = root / "boot"
            boot.mkdir()
            for name in ("boot_iBSS", "boot_iBEC", "boot_DT", "boot_TC", "boot_KC", "sep"):
                (boot / f"{name}.img4").write_bytes(b"fixture")
            (root / "IM4M.bin").write_bytes(b"expected ticket")
            fake_img4 = root / "img4"
            fake_img4.write_text("#!/bin/sh\nexit 9\n")
            fake_tool = root / "img4tool"
            fake_tool.write_text(
                "#!/bin/sh\n"
                "while [ $# -gt 0 ]; do\n"
                "  case \"$1\" in\n"
                "    -m) ticket=$2; shift 2 ;;\n"
                "    -p) payload=$2; shift 2 ;;\n"
                "    *) image=$1; shift ;;\n"
                "  esac\n"
                "done\n"
                "printf 'wrong ticket' > \"$ticket\"\n"
                "printf payload > \"$payload\"\n"
            )
            fake_img4.chmod(0o755)
            fake_tool.chmod(0o755)
            env = dict(os.environ, N69_BOOT_DIR=str(boot), N69_IM4M=str(root / "IM4M.bin"),
                       N69_IMG4_BIN=str(fake_img4), N69_IMG4TOOL_BIN=str(fake_tool),
                       N69_LOG_DIR=str(root / "logs"), N69_PREFLIGHT_ONLY="1")
            result = subprocess.run(["bash", str(ROOT / "send_n69_boot.sh")], cwd=root,
                                    env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("IMG4 preflight failed for KernelCache", result.stderr)
            self.assertNotIn("Press Enter", result.stdout)

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

    def test_devicetree_inspects_im4p_and_rejects_invalid_inputs(self):
        def der(tag, data):
            return bytes((tag, len(data))) + data

        def im4p(kind):
            fields = (der(0x16, b'IM4P'), der(0x16, kind),
                      der(0x16, b'device tree'), der(0x04, b'native tree'))
            return der(0x30, b''.join(fields))

        class FakeImg4:
            def extract(self, source, output):
                if Path(source).suffix == '.im4p':
                    Path(output).write_bytes(b'native tree')
                else:
                    Path(output).write_bytes(Path(source).read_bytes())

            def sign(self, source, output, kind, description):
                self_kind_and_description = (kind, description)
                if self_kind_and_description != ('rdtr', 'DeviceTree'):
                    raise AssertionError(self_kind_and_description)
                Path(output).write_bytes(Path(source).read_bytes())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / 'boot' / 'devicetree.img4'
            config = SimpleNamespace(output={'boot_dt': str(output)},
                                     component_sources={'boot_chain': 'target'})
            source_data = [im4p(b'dtre')]
            def extract(_ipsw, _board, _component, dest):
                dest.write_bytes(source_data[0])
            tree = SimpleNamespace(root=object())
            def property_value(_root, name):
                return {'compatible': b'N69AP\0iPhone8,4\0', 'model': b'iPhone8,4\0'}[name]
            with patch('ios16se.build.get_ipsw_path', return_value=root / 'target.ipsw'), \
                 patch('ios16se.build.extract_ipsw_component', side_effect=extract), \
                 patch('ios16se.build.get_img4_tool', return_value=FakeImg4()), \
                 patch('ios16se.build.inspect_devicetree', return_value=tree), \
                 patch('ios16se.devicetree.property_value', side_effect=property_value):
                self.assertEqual(build_devicetree(config, root, root / 'ticket'), output)
                self.assertEqual(output.read_bytes(), b'native tree')
                for bad, message in ((b'invalid', 'not a valid IM4P'),
                                     (im4p(b'krnl'), 'unexpected IM4P type')):
                    source_data[0] = bad
                    with self.assertRaisesRegex(BuildError, message):
                        build_devicetree(config, root, root / 'ticket')
                    self.assertEqual(output.read_bytes(), b'native tree')

    def test_unvalidated_hardware_fails_closed(self):
        with self.assertRaises(RuntimeError):
            apply_patches(Path('x'), ['sep_legacy'], Path('x'))

    def test_watchtower_refuses_guessed_tail(self):
        with self.assertRaises(ValueError): extract_watchtower_tail(b'X'*100000)

    def test_complzss_encoder_roundtrips_apple_lzss_stream(self):
        import random
        import shutil
        import subprocess
        from ios16se.complzss import decompress_bytes

        compiler = shutil.which("cc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("cc/clang is required to exercise the native complzss encoder")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "kernel.raw"
            output = root / "kernel.complzss"
            rng = random.Random(69)
            data = (b"N69 Apple LZSS test pattern " * 800) + bytes(rng.randrange(256) for _ in range(4096))
            source.write_bytes(data)
            executable = root / "mkcomplzss"
            subprocess.run([
                compiler, "-O2", "-std=c99", "-Wall", "-Wextra", "-Werror",
                str(ROOT / "tools/complzss/mkcomplzss.c"),
                str(ROOT / "tools/complzss/lzss.c"), "-o", str(executable),
            ], check=True)
            subprocess.run([str(executable), str(source), str(output)], check=True)
            info, decoded = decompress_bytes(output.read_bytes())
            self.assertEqual(info.uncompressed_size, len(data))
            self.assertEqual(decoded, data)

    def test_roundtrip_checks_full_payload_and_ticket(self):
        def der(tag, data):
            length = len(data)
            if length < 128:
                encoded = bytes([length])
            else:
                raw = length.to_bytes((length.bit_length() + 7) // 8, 'big')
                encoded = bytes([0x80 | len(raw)]) + raw
            return bytes([tag]) + encoded + data
        def string(value): return der(0x16, value)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); original = p/'original'; signed = p/'signed'; ticket = p/'ticket'
            original.write_bytes(b'original payload'); ticket.write_bytes(der(0x30, string(b'IM4M')))
            im4p = der(0x30, string(b'IM4P') + string(b'rkrn') + string(b'kernel') + der(4, original.read_bytes()))
            signed.write_bytes(der(0x30, string(b'IMG4') + im4p + der(0xa0, ticket.read_bytes())))
            self.assertTrue(verify_kc_roundtrip(signed, original, ticket, p/'unused'))
            compressed = p/'kernel.complzss'
            import subprocess
            import shutil
            compiler = shutil.which('cc') or shutil.which('clang')
            if compiler:
                executable = p/'mkcomplzss'
                subprocess.run([compiler, '-O2', '-std=c99', str(ROOT/'tools/complzss/mkcomplzss.c'),
                                str(ROOT/'tools/complzss/lzss.c'), '-o', str(executable)], check=True)
                subprocess.run([str(executable), str(original), str(compressed)], check=True)
                payload = compressed.read_bytes()
                im4p_compressed = der(0x30, string(b'IM4P') + string(b'rkrn') + string(b'kernel') + der(4, payload))
                signed_compressed = p/'signed-compressed'
                signed_compressed.write_bytes(der(0x30, string(b'IMG4') + im4p_compressed + der(0xa0, ticket.read_bytes())))
                self.assertTrue(verify_kc_roundtrip(signed_compressed, original, ticket, p/'unused', compressed))
                compressed.write_bytes(payload + b'corruption')
                self.assertFalse(verify_kc_roundtrip(signed_compressed, original, ticket, p/'unused', compressed))
            original.write_bytes(b'wrong payload')
            self.assertFalse(verify_kc_roundtrip(signed, original, ticket, p/'unused'))
            original.write_bytes(b'original payload'); ticket.write_bytes(b'wrong ticket')
            self.assertFalse(verify_kc_roundtrip(signed, original, ticket, p/'unused'))

if __name__ == '__main__': unittest.main()
