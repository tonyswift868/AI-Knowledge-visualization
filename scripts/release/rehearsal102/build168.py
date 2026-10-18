"""Build isolated 168-MF04 preview packages and signed local feed releases."""
from pathlib import Path
from types import SimpleNamespace
import argparse
import hashlib
import json
import os
import shutil
import sys
import subprocess

if not os.environ.get('MF_UAT_ROOT'):
    raise SystemExit('MF_UAT_ROOT is required for 168 rehearsal')
if os.environ.get('MF_UAT_APP_ID') != 'MentorFlow.UAT.gate168':
    raise SystemExit('MF_UAT_APP_ID must be MentorFlow.UAT.gate168')

from common import APP_ID, FEED, ROOT, TASK, environment

environment()
sys.path.insert(0, str(ROOT))
from scripts import build_learning_installer as b
from build import freeze


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.digest()


def stage(label, payload, version, epoch, *, previous=None, mandatory=False, installer=False):
    from refactor.desktop.delivery import update_protocol as protocol

    protocol.APP_ID = APP_ID
    embedded_version = json.loads((Path(payload)/'mentorflow-version.json').read_bytes()).get('version')
    if embedded_version != version:
        raise ValueError(f'payload version {embedded_version!r} does not match release version {version!r}')
    previous_fn = b.previous_release
    b.previous_release = lambda *args, **kwargs: SimpleNamespace(
        version='0.0.0', signed={'min_supported_version': '0.0.0', 'mandatory': False}
    ) if not kwargs.get('path') else previous_fn(*args, **kwargs)
    out = TASK / ('release-' + label)
    try:
        b.release(SimpleNamespace(
            payload=payload, feed=FEED, app_id=APP_ID, public_key=TASK/'test-public.json',
            previous_release=previous, output=out, epoch=epoch, valid_days=3,
            notes='168 本机预览验收 ' + label, base_versions=[], mandatory=mandatory,
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


def build_variant(label, *, mandatory, version, epoch):
    payload = TASK/('payload-'+label)
    shutil.copytree(TASK/'freeze-X-final/app/MentorFlow', payload)
    self_check = payload/'SELF-CHECK'
    if self_check.exists():
        shutil.rmtree(self_check)
    version_file = payload/'mentorflow-version.json'
    data = json.loads(version_file.read_bytes())
    data['version'] = version
    version_file.unlink()
    version_file.write_bytes(b.canonical(data))
    # The only Y payload changes are its signed version metadata and signature.
    for relative in ('MentorFlow.exe', 'UserRuntime/UserRuntime.exe'):
        if file_hash(payload/relative) != file_hash(TASK/'freeze-X-final/app/MentorFlow'/relative):
            raise AssertionError('policy variant unexpectedly changed frozen product code: '+relative)
    stage(label, payload, version, epoch,
          previous=TASK/'release-X-final3/stable.json', mandatory=mandatory, installer=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('label', choices=('X-final3', 'Y-optional-final3', 'Y-required-final3'))
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--reuse-frozen', action='store_true',
                        help='reuse X payload already frozen in this task directory')
    args = parser.parse_args()
    if args.label == 'X-final3':
        b.ROOT = args.source.resolve()
        payload = TASK/'freeze-X-final/app/MentorFlow' if args.reuse_frozen else freeze(
            'X-final', '9.168.11', args.source.resolve())
        b.ROOT = ROOT
        if not payload.is_dir():
            raise SystemExit('no existing X freeze to reuse')
        version_path = payload/'mentorflow-version.json'
        version_data = json.loads(version_path.read_bytes())
        version_data['version'] = '9.168.11'
        version_path.write_bytes(b.canonical(version_data))
        (TASK/'freeze-X-final/mentorflow-version.json').write_bytes(b.canonical(version_data))
        check = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('refreeze103.py')),
            'X-final3', '--check-only', '--source-root', str(args.source.resolve())],
            env=environment(), cwd=ROOT, capture_output=True, text=True, encoding='utf8', timeout=180)
        if check.returncode:
            raise RuntimeError('refreeze103 X check failed: '+check.stdout+'\n'+check.stderr)
        stage('X-final3', payload, '9.168.11', 99168211, installer=False)
    elif args.label == 'Y-optional-final3':
        build_variant('Y-optional-final3', mandatory=False, version='9.168.12', epoch=99168212)
    else:
        build_variant('Y-required-final3', mandatory=True, version='9.168.13', epoch=99168213)
    print(json.dumps({'label': args.label, 'release': str(TASK/('release-'+args.label)/'stable.json'),
                      'setup': str(TASK/'setup-X/MentorFlow-Setup.exe') if args.label == 'X' else None},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
