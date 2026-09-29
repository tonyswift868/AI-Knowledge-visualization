"""The new source entry and its resources work without the retired tree."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_new_runtime_imports_without_legacy_roots():
    source = '''
import importlib, importlib.abc, sys
class BlockRetired(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'desktop', 'backend', 'tools', 'legacy'}:
            raise AssertionError('retired runtime imported: ' + fullname)
sys.meta_path.insert(0, BlockRetired())
for name in (
    'refactor.desktop.packaged_main', 'refactor.desktop.account_shell_main',
    'refactor.desktop.updates', 'refactor.desktop.delivery.per_user_install',
    'refactor.desktop.delivery.per_user_update_core',
    'refactor.desktop.delivery.per_user_launcher',
    'refactor.learning.app.window', 'refactor.learning.app.services',
    'refactor.learning.course.sync_course', 'refactor.learning.course.research',
    'refactor.learning.course.source_catalog_export',
):
    importlib.import_module(name)
'''
    result = subprocess.run([sys.executable, '-B', '-c', source], capture_output=True,
                            text=True, env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, timeout=30)
    assert result.returncode == 0, result.stderr


def test_migrated_teacher_catalog_serves_complete_bundled_model():
    from refactor.learning.app.teacher_assets import TeacherAssetCatalog, PUBLIC_ROOT
    catalog = TeacherAssetCatalog(PUBLIC_ROOT)
    status = catalog.status()
    assert status['ready'], status
    assert status['teacher_id'] == 'tsubaki-live2d'
    assert PUBLIC_ROOT == Path(__file__).resolve().parents[3] / 'refactor/desktop/resources'


def test_source_version_reads_repository_manifest_after_package_move():
    from refactor.desktop.delivery.version import load_version_manifest
    root = Path(__file__).resolve().parents[3]
    expected = json.loads((root / 'mentorflow-version.json').read_text(encoding='utf-8'))
    assert load_version_manifest()['version'] == expected['version']
