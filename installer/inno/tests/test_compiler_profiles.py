"""Real Inno 7.1 compiler checks with output disabled. Never run an installer."""
import contextlib
import importlib.util
import io
import argparse
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('compile_builder', ROOT / 'scripts/build_inno_installer.py')
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
BASE = Path('E:/Codex/Temp/mf-installer-source-20260909')


class CompilerProfiles(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='compile-test-', dir=BASE))

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(BASE.resolve()))
        shutil.rmtree(self.root)

    def compile(self, required):
        args = argparse.Namespace(check_source=True, output=None, payload=None,
            work_dir=str(self.root), compiler=str(BUILDER.COMPILER), check_required_bytes=required)
        with contextlib.redirect_stdout(io.StringIO()):
            receipt = BUILDER.build(args)
        self.assertTrue(receipt['check_source'])
        self.assertIsNone(receipt['artifact'])
        self.assertFalse(receipt['installer_exe_emitted'])
        self.assertIn('--output=no', receipt['command'])
        self.assertFalse(list(self.root.rglob('MentorFlow-Setup.exe')))
        log = (Path(receipt['evidence_dir']) / 'compile.log').read_text(encoding='utf-8')
        self.assertIn('Successful compile', log)
        self.assertNotIn('Warning:', log)
        self.assertIn('Compiling [Code] section', log)
        return receipt

    def test_all_production_sections_compile_without_emitting_installer(self):
        self.compile(268435456)

    def test_required_space_over_2gb_compiles_without_overflow(self):
        receipt = self.compile(2468435456)
        self.assertEqual(receipt['required_bytes'], 2468435456)

    def test_required_space_over_4gb_uses_int64(self):
        self.compile(5368709120)


if __name__ == '__main__':
    unittest.main(verbosity=2)
