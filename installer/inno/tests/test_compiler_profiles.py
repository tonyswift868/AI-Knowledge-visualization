"""Compile real production sections and >2 GiB space arithmetic; never install."""
import configparser
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

BASE = Path('E:/Codex/Temp/mf-modern-installer-20260908')
CANDIDATE = Path(os.environ.get('MF_INNO_CANDIDATE_DIR', str(BASE / 'candidate')))


class CompilerProfiles(unittest.TestCase):
    def setUp(self):
        self.source = json.loads((CANDIDATE / 'build-receipt.json').read_text())
        self.assertTrue(self.source['fixture'], 'Compile-profile tests require the small fixture receipt, not the real product package')
        self.root = Path(tempfile.mkdtemp(prefix='compile-test-', dir=BASE))

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(BASE.resolve()))
        shutil.rmtree(self.root)

    def compile(self, fixture):
        command = self.source['command'].copy()
        replacements = {'OutputDir': self.root, 'OutputName': 'profile',
                        'FixtureBuild': int(fixture), 'RequiredBytes': 2468435456}
        for key, value in replacements.items():
            command = [f'/D{key}={value}' if arg.startswith(f'/D{key}=') else arg for arg in command]
        result = subprocess.run(command, capture_output=True, text=True, errors='replace', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / 'profile.exe').is_file())

    def test_production_sections_compile(self):
        self.compile(False)

    def test_2_2_gb_payload_plus_reserve_reports_gb_without_overflow(self):
        self.compile(True)
        ini = self.root / 'probe.ini'
        ini.write_text(f'[Probe]\nParent={self.root}\n', encoding='utf-16')
        subprocess.run([str(self.root / 'profile.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES',
                        f'/UXPROBE={ini}', '/UXLOWDISK=1'], timeout=30)
        result = configparser.ConfigParser()
        result.read(ini, encoding='utf-16')
        self.assertEqual(result['Result']['Accepted'], '0')
        self.assertIn('2.3 GB', result['Result']['Error'])
        self.assertIn('1 MB', result['Result']['Error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
