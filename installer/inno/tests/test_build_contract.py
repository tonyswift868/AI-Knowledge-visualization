"""Independent build boundary checks: no product runtime, GUI or dependencies."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('inno_builder', ROOT / 'scripts/build_inno_installer.py')
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
BASE = Path('E:/Codex/Temp/mf-modern-installer-20260908')


class BuildContract(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='build-test-', dir=BASE))
        self.payload = self.root / 'payload'
        self.payload.mkdir()
        (self.payload / 'MentorFlow.exe').write_bytes(b'inert test')
        (self.payload / 'mentorflow-version.json').write_text('{"version":"9.8.7"}')
        (self.payload / 'SELF-CHECK').mkdir()
        (self.payload / 'SELF-CHECK/package-manifest.json').write_text('{"fixture":true}')
        self.output = self.root / 'output'
        self.args = argparse.Namespace(output=str(self.output), payload=str(self.payload),
                                       fixture=False, appearance='dynamic', compiler='ISCC.exe')

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(BASE.resolve()))
        shutil.rmtree(self.root)

    def test_output_inside_payload_rejected_before_write(self):
        forbidden = self.payload / 'do-not-create/output'
        self.args.output = str(forbidden)
        with self.assertRaisesRegex(ValueError, 'outside'):
            BUILDER.build(self.args)
        self.assertFalse((self.payload / 'do-not-create').exists())

    def test_production_input_version_and_no_runtime_launch(self):
        def fake_run(command, **kwargs):
            if command[0] == 'ISCC.exe':
                (self.output / 'MentorFlow-Setup.exe').write_bytes(b'fixture compiler output')
            return subprocess.CompletedProcess(command, 0, stdout='', stderr='')
        with patch.object(BUILDER.subprocess, 'run', side_effect=fake_run) as run:
            BUILDER.build(self.args)
        verify_command = run.call_args_list[0].args[0]
        self.assertIn('--no-smoke', verify_command)
        self.assertIn('--no-data-check', verify_command)
        compiler_command = run.call_args_list[1].args[0]
        self.assertIn('/DProductVersion=9.8.7', compiler_command)
        self.assertIn('/DFixtureBuild=0', compiler_command)
        self.assertIn('/DAppearance=dynamic', compiler_command)
        self.assertTrue((self.output / 'build-input/ownership.ini').read_bytes().startswith(b'\xff\xfe'))
        receipt = json.loads((self.output / 'build-receipt.json').read_text())
        self.assertEqual(receipt['file_count'], 3)
        self.assertFalse(receipt['fixture'])
        self.assertEqual(len(receipt['package_manifest_sha256']), 64)
        self.assertGreaterEqual(len(receipt['source_sha256']), 5)

    def test_failed_package_verification_never_compiles(self):
        with patch.object(BUILDER.subprocess, 'run', side_effect=subprocess.CalledProcessError(2, 'verify')) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                BUILDER.build(self.args)
        self.assertEqual(run.call_count, 1)
        self.assertFalse(any(self.output.glob('*.exe')))

    def test_changed_source_during_compile_cannot_receive_receipt(self):
        real_digest = BUILDER.digest
        calls = 0
        def changing_digest(path):
            nonlocal calls
            if Path(path).resolve() == ROOT / 'installer/inno/MentorFlow.iss':
                calls += 1
                if calls > 1:
                    return '0' * 64
            return real_digest(path)
        with patch.object(BUILDER, 'digest', side_effect=changing_digest), \
             patch.object(BUILDER.subprocess, 'run', return_value=subprocess.CompletedProcess('test', 0, '', '')):
            with self.assertRaisesRegex(RuntimeError, 'source changed'):
                BUILDER.build(self.args)
        self.assertFalse((self.output / 'build-receipt.json').exists())

    def test_changed_payload_manifest_cannot_receive_receipt(self):
        real_digest = BUILDER.digest
        calls = 0
        def changing_digest(path):
            nonlocal calls
            if Path(path).resolve() == self.payload / 'SELF-CHECK/package-manifest.json':
                calls += 1
                if calls > 1:
                    return '0' * 64
            return real_digest(path)
        with patch.object(BUILDER, 'digest', side_effect=changing_digest), \
             patch.object(BUILDER.subprocess, 'run', return_value=subprocess.CompletedProcess('test', 0, '', '')):
            with self.assertRaisesRegex(RuntimeError, 'manifest changed'):
                BUILDER.build(self.args)
        self.assertFalse((self.output / 'build-receipt.json').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
