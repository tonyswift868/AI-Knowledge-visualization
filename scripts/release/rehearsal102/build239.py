"""Build final-code 239-MF04 local-only packages and signed policy feeds."""
from pathlib import Path
from types import SimpleNamespace
import argparse
import json
import os
import shutil
import sys

TASK_ROOT = Path('D:/MentorFlow/.local/test-tmp/fix239u').resolve()
if Path(os.environ.get('MF_UAT_ROOT', '')).resolve() != TASK_ROOT:
    raise SystemExit('MF_UAT_ROOT must be D:/MentorFlow/.local/test-tmp/fix239u')
if os.environ.get('MF_UAT_APP_ID') != 'MentorFlow.UAT.fix239u':
    raise SystemExit('MF_UAT_APP_ID must be MentorFlow.UAT.fix239u')

from common import APP_ID, FEED, ROOT, TASK, environment
environment()
BUILD_PYTHON = Path('D:/MentorFlow/.venv/Scripts/python.exe').resolve()
if Path(sys.executable).resolve() != BUILD_PYTHON:
    raise SystemExit(f'build239 must run with task-approved interpreter: {BUILD_PYTHON}')
import PyInstaller
import PySide6
if PyInstaller.__version__ != '6.16.0':
    raise SystemExit(f'build239 requires locked PyInstaller 6.16.0, got {PyInstaller.__version__}')
print(f'build preflight passed: python={sys.executable}; PyInstaller={PyInstaller.__version__}; PySide6={PySide6.__file__}')
sys.path.insert(0, str(ROOT))
from scripts import build_learning_installer as b
from build import freeze

X_LABEL = 'X239-lock-final'
OPTIONAL_LABEL = 'Y-optional239-lock-final'
REQUIRED_LABEL = 'Y-required239-lock-final'
VERSION_X = '9.239.1'
VERSION_Y = '9.239.2'
BASE_LABEL = 'X-baseline239'
BASE_Y_LABEL = 'Y-baseline239'
BASE_VERSION_X = '9.238.0'
BASE_VERSION_Y = '9.238.1'


def stage(label, payload, version, epoch, *, previous=None, mandatory=False, installer=False):
    from refactor.desktop.delivery import update_protocol as protocol

    protocol.APP_ID = APP_ID
    previous_fn = b.previous_release
    source_root = TASK / 'source-final239'
    b.ROOT = source_root
    # The first preview release has a synthetic predecessor. Subsequent policy
    # feeds are both signed against the actual X release.
    b.previous_release = lambda *args, **kwargs: SimpleNamespace(
        version='0.0.0', signed={'min_supported_version': '0.0.0', 'mandatory': False}
    ) if not kwargs.get('path') else previous_fn(*args, **kwargs)
    out = TASK / ('release-' + label)
    try:
        b.release(SimpleNamespace(
            payload=payload, feed=FEED, app_id=APP_ID, public_key=TASK/'test-public.json',
            previous_release=previous, output=out, epoch=epoch, valid_days=3,
            notes='239-MF04 本机预览验证 ' + label, base_versions=[], mandatory=mandatory,
            mandatory_reason='protocol' if mandatory else '', minimum_version=None,
        ))
    finally:
        b.previous_release = previous_fn
    b.sign(SimpleNamespace(public_key=TASK/'test-public.json', private_key=TASK/'test-private.bin',
                           unsigned=out/'unsigned-release.json', output=out/'stable.json'))

    objects = TASK/'webroot/updates/app/objects'
    objects.mkdir(parents=True, exist_ok=True)
    for item in (out/'objects').iterdir():
        target = objects/item.name
        if not target.exists():
            try:
                target.hardlink_to(item)
            except OSError:
                shutil.copy2(item, target)
    if installer:
        b.installer(SimpleNamespace(payload=payload, release=out/'stable.json',
            work=TASK/('inno-'+label), output=TASK/('setup-'+label),
            compiler=Path('E:/Codex/App/InnoSetup-7.1.0/ISCC.exe')))
    return out/'stable.json'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('label', choices=('X', 'Y-optional', 'Y-required', 'X-baseline', 'Y-baseline'))
    parser.add_argument('--source', type=Path, default=ROOT)
    args = parser.parse_args()
    if args.label in ('X', 'X-baseline'):
        source = args.source.resolve()
        b.ROOT = source
        freeze_label = BASE_LABEL if args.label == 'X-baseline' else X_LABEL
        version = BASE_VERSION_X if args.label == 'X-baseline' else VERSION_X
        payload = freeze(freeze_label, version, source)
        b.ROOT = TASK / 'source-final239'
        release = stage(freeze_label, payload, version, 99239001 if args.label == 'X-baseline' else 99239101,
                        installer=True)
        result = {'label': freeze_label, 'version': version, 'release': str(release),
                  'setup': str(TASK/('setup-'+freeze_label)/'MentorFlow-Setup.exe')}
    elif args.label == 'Y-baseline':
        label = BASE_Y_LABEL
        payload = TASK/('payload-'+label)
        if payload.exists():
            raise SystemExit(f'refusing to overwrite prepared payload: {payload}')
        shutil.copytree(TASK/('freeze-'+BASE_LABEL)/'app/MentorFlow', payload)
        (payload/'SELF-CHECK').unlink(missing_ok=True)
        version_file = payload/'mentorflow-version.json'
        version = json.loads(version_file.read_bytes())
        version['version'] = BASE_VERSION_Y
        version_file.unlink()
        version_file.write_bytes(b.canonical(version))
        release = stage(label, payload, BASE_VERSION_Y, 99239002,
            previous=TASK/('release-'+BASE_LABEL)/'stable.json', mandatory=False)
        result = {'label': label, 'version': BASE_VERSION_Y, 'mandatory': False,
                  'release': str(release)}
    else:
        required = args.label == 'Y-required'
        label = REQUIRED_LABEL if required else OPTIONAL_LABEL
        payload = TASK/('payload-'+label)
        if payload.exists():
            raise SystemExit(f'refusing to overwrite prepared payload: {payload}')
        shutil.copytree(TASK/('freeze-'+X_LABEL)/'app/MentorFlow', payload,
                        ignore=shutil.ignore_patterns('SELF-CHECK'))
        version_file = payload/'mentorflow-version.json'
        version = json.loads(version_file.read_bytes())
        version['version'] = VERSION_Y
        version_file.unlink()
        version_file.write_bytes(b.canonical(version))
        release = stage(label, payload, VERSION_Y, 99239103 if required else 99239102,
            previous=TASK/('release-'+X_LABEL)/'stable.json', mandatory=required)
        result = {'label': label, 'version': VERSION_Y, 'mandatory': required,
                  'release': str(release)}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
