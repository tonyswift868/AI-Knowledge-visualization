"""Run the compiled Pascal policy against real disposable Windows directories."""
import configparser
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

BASE = Path('E:/Codex/Temp/mf-modern-installer-20260908')
CANDIDATE = Path(os.environ.get('MF_INNO_CANDIDATE_DIR', str(BASE / 'candidate')))
EXE = CANDIDATE / 'MentorFlow-UX-Candidate.exe'


class RuntimePolicy(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='policy-', dir=BASE))

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(BASE.resolve()))
        shutil.rmtree(self.root)

    def probe(self, parent, low_disk=False):
        ini = self.root / 'probe.ini'
        ini.write_text(f'[Probe]\nParent={parent}\n', encoding='utf-16')
        args = [str(EXE), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', f'/UXPROBE={ini}']
        if low_disk:
            args.append('/UXLOWDISK=1')
        subprocess.run(args, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
        data = configparser.ConfigParser()
        # Windows profile APIs require UTF-16 BOM for Unicode INI files.
        raw = ini.read_bytes()
        encoding = 'utf-16' if raw.startswith(b'\xff\xfe') else 'utf-8-sig'
        data.read_string(raw.decode(encoding))
        self.assertIn('Result', data)
        return data['Result']

    def test_existing_parent_creates_nothing(self):
        result = self.probe(self.root)
        self.assertEqual(result['accepted'], '1')
        self.assertEqual(Path(result['target']), self.root / 'MentorFlow')
        self.assertFalse((self.root / 'MentorFlow').exists())
        self.assertEqual([p.name for p in self.root.iterdir()], ['probe.ini'])

    def test_missing_parent_never_creates_ancestors(self):
        result = self.probe(self.root / 'A/B/C')
        self.assertEqual(result['accepted'], '0')
        self.assertFalse((self.root / 'A').exists())
        self.assertIn('不存在', result['error'])

    def test_missing_drive(self):
        letter = next(x for x in 'ZYXWVUTSRQPONMLKJIHGF' if not Path(x + ':\\').exists())
        result = self.probe(letter + ':\\Apps')
        self.assertEqual(result['accepted'], '0')
        self.assertIn('磁盘', result['error'])

    def test_empty_target(self):
        (self.root / 'MentorFlow').mkdir()
        self.assertEqual(self.probe(self.root)['accepted'], '1')

    def test_unknown_target_preserved(self):
        target = self.root / 'MentorFlow'
        target.mkdir()
        (target / 'important.txt').write_text('keep')
        self.assertEqual(self.probe(self.root)['accepted'], '0')
        self.assertEqual((target / 'important.txt').read_text(), 'keep')

    def test_known_target_and_unknown_file(self):
        target = self.root / 'MentorFlow'
        target.mkdir()
        shutil.copy2(CANDIDATE / 'build-input/payload/MentorFlow.exe', target)
        shutil.copy2(CANDIDATE / 'build-input/ownership.ini', target / '.mentorflow-install.ini')
        self.assertEqual(self.probe(self.root)['accepted'], '1')
        (target / 'foreign.txt').write_text('keep')
        self.assertEqual(self.probe(self.root)['accepted'], '0')

    def test_low_disk_human_units(self):
        result = self.probe(self.root, low_disk=True)
        self.assertEqual(result['accepted'], '0')
        self.assertIn('空间不足', result['error'])
        self.assertIn('MB', result['error'])
        self.assertNotIn('bytes', result['error'])

    def test_parent_with_spaces_and_unicode(self):
        parent = self.root / '学习 Apps'
        parent.mkdir()
        self.assertEqual(self.probe(parent)['accepted'], '1')

    def test_target_file_instead_of_directory(self):
        (self.root / 'MentorFlow').write_text('keep')
        self.assertEqual(self.probe(self.root)['accepted'], '0')

    def test_dot_traversal_rejected(self):
        self.assertEqual(self.probe(str(self.root) + '\\..\\elsewhere')['accepted'], '0')

    def test_unknown_empty_subdirectory_is_not_owned(self):
        target = self.root / 'MentorFlow'
        target.mkdir()
        shutil.copy2(CANDIDATE / 'build-input/payload/MentorFlow.exe', target)
        shutil.copy2(CANDIDATE / 'build-input/ownership.ini', target / '.mentorflow-install.ini')
        (target / 'personal-empty-folder').mkdir()
        self.assertEqual(self.probe(self.root)['accepted'], '0')

    def test_forged_or_modified_executable_is_rejected(self):
        target = self.root / 'MentorFlow'
        target.mkdir()
        (target / 'MentorFlow.exe').write_bytes(b'not the recognized executable')
        shutil.copy2(CANDIDATE / 'build-input/ownership.ini', target / '.mentorflow-install.ini')
        self.assertEqual(self.probe(self.root)['accepted'], '0')

    def test_directory_junction_is_rejected(self):
        original = self.root / 'original'
        original.mkdir()
        junction = self.root / 'linked'
        subprocess.run(['powershell', '-NoProfile', '-Command',
                        f"New-Item -ItemType Junction -Path '{junction}' -Target '{original}'"],
                       check=True, capture_output=True)
        try:
            self.assertEqual(self.probe(junction)['accepted'], '0')
            self.assertFalse((original / 'MentorFlow').exists())
        finally:
            # rmdir unlinks only this junction, never recursively touches its target.
            os.rmdir(junction)


if __name__ == '__main__':
    unittest.main(verbosity=2)
