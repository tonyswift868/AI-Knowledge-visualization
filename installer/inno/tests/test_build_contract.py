"""Build boundary regressions. Compiler/product verifier mocked; no installer runs."""
import argparse
import contextlib
import importlib.util
import io
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
BASE = Path('E:/Codex/Temp/mf-installer-source-20260909')


class BuildContract(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='build-test-', dir=BASE))
        self.payload = self.root / 'payload'
        self.payload.mkdir()
        (self.payload / 'MentorFlow.exe').write_bytes(b'inert non-PE test data')
        (self.payload / 'mentorflow-version.json').write_text('{"version":"9.8.7"}')
        (self.payload / 'SELF-CHECK').mkdir()
        (self.payload / 'SELF-CHECK/package-manifest.json').write_text('{"test":true}')
        self.output = self.root / 'output'
        self.work = self.root / 'private'
        self.args = argparse.Namespace(output=str(self.output), payload=str(self.payload),
            release=str(self.root / 'base-release.json'),
            work_dir=str(self.work), check_source=False, compiler='ISCC.exe', check_required_bytes=2468435456)

        Path(self.args.release).write_text('{"mock_signed_release":true}')
        verifier = patch.object(BUILDER, 'verify_core_payload', return_value='MentorFlow.UAT.BuildTest')
        verifier.start()
        self.addCleanup(verifier.stop)

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(BASE.resolve()))
        shutil.rmtree(self.root)

    def fake_run(self, command, **kwargs):
        if command[0] == 'ISCC.exe' and '--output=no' not in command:
            stage = Path(next(x.split('=', 1)[1] for x in command if x.startswith('/DOutputDir=')))
            (stage / 'MentorFlow-Setup.exe').write_bytes(b'non-executable fake compiler output')
        return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    def build(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return BUILDER.build(self.args)

    def test_output_inside_payload_rejected_before_write(self):
        self.args.output = str(self.payload / 'do-not-create/output')
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.build()
        self.assertFalse((self.payload / 'do-not-create').exists())
        self.assertFalse(self.work.exists())

    def test_work_inputs_cannot_leak_into_user_output_or_payload(self):
        for path in (self.output / 'private', self.payload / 'private', self.root):
            with self.subTest(path=path):
                self.args.work_dir = str(path)
                with self.assertRaisesRegex(ValueError, 'disjoint'):
                    self.build()
        self.assertFalse(self.output.exists())

    def test_alternate_or_old_output_is_rejected_not_deleted(self):
        self.output.mkdir()
        old = self.output / 'Old-Setup.exe'
        old.write_bytes(b'preserve')
        with patch.object(BUILDER.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'empty'):
                self.build()
        run.assert_not_called()
        self.assertEqual(old.read_bytes(), b'preserve')

    def test_production_emits_exactly_one_file_with_no_runtime_or_data_access(self):
        with patch.object(BUILDER.subprocess, 'run', side_effect=self.fake_run) as run:
            receipt = self.build()
        self.assertEqual(len(run.call_args_list), 2)
        verify_command = run.call_args_list[0].args[0]
        self.assertIn('--no-smoke', verify_command)
        self.assertIn('--no-data-check', verify_command)
        command = run.call_args_list[1].args[0]
        self.assertIn('/DProductVersion=9.8.7', command)
        self.assertIn('/DPayloadExeSHA256=' + BUILDER.digest(self.payload / 'MentorFlow.exe'), command)
        self.assertNotIn('--output=no', command)
        self.assertFalse(any('Fixture' in x or 'Appearance' in x for x in command))
        self.assertEqual([x.name for x in self.output.iterdir()], ['MentorFlow-Setup.exe'])
        stage = Path(receipt['evidence_dir'])
        record = json.loads((stage / 'payload-inventory.json').read_text(encoding='utf-8'))
        self.assertEqual(record['version'], '9.8.7')
        self.assertEqual(record['executable_sha256'], BUILDER.digest(self.payload / 'MentorFlow.exe'))
        self.assertIn('Launcher=Updater\\UserRuntime.exe launch', (stage / 'shell.ini').read_text(encoding='utf-16'))
        self.assertFalse((stage / 'ownership.ini').exists())
        self.assertEqual(receipt['file_count'], 3)
        self.assertEqual(len(receipt['package_manifest_sha256']), 64)
        self.assertGreaterEqual(len(receipt['source_sha256']), 5)
        self.assertEqual(receipt['installer_launches'], 0)
        self.assertTrue(receipt['installer_exe_emitted'])

    def test_check_source_needs_no_product_and_emits_no_installer(self):
        self.args.check_source = True
        self.args.payload = None
        self.args.output = None
        with patch.object(BUILDER.subprocess, 'run', side_effect=self.fake_run) as run:
            receipt = self.build()
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        self.assertIn('--output=no', command)
        self.assertIn('/DRequiredBytes=2468435456', command)
        self.assertFalse(receipt['installer_exe_emitted'])
        self.assertIsNone(receipt['artifact'])
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.work.rglob('MentorFlow-Setup.exe')))

    def test_failed_package_verification_never_compiles_or_creates_output(self):
        with patch.object(BUILDER.subprocess, 'run', side_effect=subprocess.CalledProcessError(2, 'verify')) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                self.build()
        self.assertEqual(run.call_count, 1)
        self.assertFalse(self.output.exists())

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
             patch.object(BUILDER.subprocess, 'run', side_effect=self.fake_run):
            with self.assertRaisesRegex(RuntimeError, 'source changed'):
                self.build()
        self.assertFalse(list(self.work.rglob('build-receipt.json')))
        self.assertFalse(self.output.exists())

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
             patch.object(BUILDER.subprocess, 'run', side_effect=self.fake_run):
            with self.assertRaisesRegex(RuntimeError, 'manifest changed'):
                self.build()
        self.assertFalse(list(self.work.rglob('build-receipt.json')))
        self.assertFalse(self.output.exists())

    def test_nested_installer_candidate_is_not_packed(self):
        for relative in ('Installer/Old.exe', 'MentorFlow-Setup.exe', 'MentorFlow-UX-Candidate.exe'):
            path = self.payload / relative
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'not a PE')
            with patch.object(BUILDER.subprocess, 'run', side_effect=self.fake_run):
                with self.assertRaisesRegex(ValueError, 'installer candidates'):
                    self.build()
            path.unlink()
        self.assertFalse(self.output.exists())

    def test_output_changed_during_compile_refuses_delivery(self):
        def competing_run(command, **kwargs):
            result = self.fake_run(command, **kwargs)
            if command[0] == 'ISCC.exe':
                self.output.mkdir()
                (self.output / 'keep.txt').write_text('external')
            return result
        with patch.object(BUILDER.subprocess, 'run', side_effect=competing_run):
            with self.assertRaisesRegex(RuntimeError, 'Output changed'):
                self.build()
        self.assertEqual([x.name for x in self.output.iterdir()], ['keep.txt'])

    def test_cli_has_no_implicit_payload_or_alternate_candidate_modes(self):
        cases = [[], ['--fixture'], ['--check-source', '--output', str(self.output)],
                 ['--payload', str(self.payload)], ['--check-source', '--appearance', 'dark'],
                 ['--check-source', '--payload', str(self.payload)]]
        for arguments in cases:
            with self.subTest(arguments=arguments), \
                 patch.object(BUILDER.sys, 'argv', ['builder', '--work-dir', str(self.work), *arguments]), \
                 patch.object(BUILDER, 'build') as build, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    BUILDER.main()
                self.assertEqual(error.exception.code, 2)
                build.assert_not_called()

    def test_prerelease_and_build_metadata_are_preserved(self):
        (self.payload / 'mentorflow-version.json').write_text('{"version":"9.8.7-rc.1+source.abc"}')
        with patch.object(BUILDER.subprocess, 'run', side_effect=self.fake_run) as run:
            self.build()
        self.assertIn('/DProductVersion=9.8.7-rc.1+source.abc', run.call_args_list[1].args[0])

    def test_changed_signed_release_cannot_receive_receipt(self):
        real_digest = BUILDER.digest
        calls = 0
        def changing_digest(path):
            nonlocal calls
            if Path(path).resolve() == Path(self.args.release).resolve():
                calls += 1
                if calls > 1: return '0' * 64
            return real_digest(path)
        with patch.object(BUILDER, 'digest', side_effect=changing_digest), \
             patch.object(BUILDER.subprocess, 'run', return_value=subprocess.CompletedProcess('test', 0, '', '')):
            with self.assertRaisesRegex(RuntimeError, 'Signed release changed'):
                BUILDER.build(self.args)
        self.assertFalse((self.output / 'build-receipt.json').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
