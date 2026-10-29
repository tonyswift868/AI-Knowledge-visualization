"""Check each 239-MF04 executable payload before any real app launch."""
from pathlib import Path
import argparse
import json
import marshal
import os
import sys

if Path(os.environ.get('MF_UAT_ROOT', '')).resolve() != Path('D:/MentorFlow/.local/test-tmp/fix239u').resolve():
    raise SystemExit('MF_UAT_ROOT must be D:/MentorFlow/.local/test-tmp/fix239u')
if os.environ.get('MF_UAT_APP_ID') != 'MentorFlow.UAT.fix239u':
    raise SystemExit('MF_UAT_APP_ID must be MentorFlow.UAT.fix239u')

from common import APP_ID, TASK, ROOT, environment
environment()
sys.path.insert(0, str(ROOT))
from PyInstaller.archive.readers import CArchiveReader

LABELS = {
    'X': ('X239-lock-final', 'release-X239-lock-final', '9.239.1', False),
    'Y-optional': ('Y-optional239-lock-final', 'release-Y-optional239-lock-final', '9.239.2', False),
    'Y-required': ('Y-required239-lock-final', 'release-Y-required239-lock-final', '9.239.2', True),
    'X-baseline': ('X-baseline239', 'release-X-baseline239', '9.238.0', False),
    'Y-baseline': ('Y-baseline239', 'release-Y-baseline239', '9.238.1', False),
}
parser = argparse.ArgumentParser()
parser.add_argument('label', choices=tuple(LABELS))
parser.add_argument('--check-only', action='store_true', required=True)
parser.add_argument('--source-root', type=Path, help='Compare frozen Python modules to this exact source tree')
args = parser.parse_args()
freeze_label, release_label, version, mandatory = LABELS[args.label]
payload = (TASK/('freeze-'+freeze_label)/'app/MentorFlow'
           if args.label in ('X', 'X-baseline')
           else TASK/('payload-'+freeze_label))
release_path = TASK/release_label/'stable.json'

release = json.loads(release_path.read_bytes())['signed']
assert release['version'] == version, release['version']
assert release['mandatory'] is mandatory, release['mandatory']
config = json.loads((payload/'_internal/refactor/desktop/resources/public-runtime.json').read_bytes())
assert config['MENTORFLOW_ENTERPRISE_API_BASE_URL'] == 'https://127.0.0.1:18764'
assert config['MENTORFLOW_DESKTOP_OIDC_ISSUER'] == 'https://localhost:18764/test-realm'
for exe in (payload/'MentorFlow.exe', payload/'UserRuntime/UserRuntime.exe'):
    archive = CArchiveReader(str(exe))
    hook = archive.extract('timing_hook')
    assert b'UAT_LOCAL_ONLY' in hook and b'127.0.0.1' in hook, exe
    if args.source_root:
        if exe.name == 'UserRuntime.exe':
            entry = marshal.loads(archive.extract('per_user_install'))
            source = args.source_root/'refactor/desktop/delivery/per_user_install.py'
            assert entry == compile(source.read_bytes(), entry.co_filename, 'exec',
                                    dont_inherit=True, optimize=0), 'stale frozen entry script'
        pyz = archive.open_embedded_archive('PYZ.pyz')
        names = ['refactor.desktop.delivery.per_user_install',
                 'refactor.desktop.delivery.per_user_launcher',
                 'refactor.desktop.delivery.per_user_update_core',
                 'refactor.desktop.delivery.startup_window',
                 'refactor.desktop.delivery.update_download',
                 'refactor.desktop.delivery.update_protocol',
                 'refactor.desktop.delivery.prepared_update',
                 'refactor.desktop.delivery.update_service']
        if exe.name == 'MentorFlow.exe':
            names.extend(['refactor.desktop.updates', 'refactor.desktop.update_dialog',
                          'refactor.desktop.account_shell_main', 'refactor.learning.app.handlers'])
        for name in names:
            code = pyz.extract(name)
            source = args.source_root/(name.replace('.', '/')+'.py')
            expected = compile(source.read_bytes(), code.co_filename, 'exec',
                               dont_inherit=True, optimize=0)
            assert code == expected, ('frozen code is not final source', exe, name)
        authority = pyz.extract('mentorflow_delivery_authority')
        assert APP_ID in authority.co_consts, 'wrong frozen app identity'
        key_id = json.loads((TASK/'test-public.json').read_bytes())['key_id']
        assert repr(key_id) in repr(authority.co_consts), 'wrong frozen preview key identity'
print(json.dumps({'label': args.label, 'version': version, 'mandatory': mandatory,
                  'payload': str(payload), 'release': str(release_path),
                  'checked': True, 'source_root': str(args.source_root) if args.source_root else None},
                 ensure_ascii=False))
