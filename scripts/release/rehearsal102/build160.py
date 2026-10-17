"""Build isolated 160-MF04 preview packages and local signed feed releases."""
from pathlib import Path
from types import SimpleNamespace
import argparse
import json
import os
import shutil
import sys

if not os.environ.get('MF_UAT_ROOT'):
    raise SystemExit('MF_UAT_ROOT is required for 160 rehearsal; refusing the legacy default directory')
if os.environ.get('MF_UAT_APP_ID') != 'MentorFlow.UAT.gate160':
    raise SystemExit('MF_UAT_APP_ID must be MentorFlow.UAT.gate160')

from common import APP_ID, FEED, ROOT, TASK, environment

environment()
sys.path.insert(0, str(ROOT))
from scripts import build_learning_installer as b
from build import freeze


def stage(label, payload, version, epoch, *, previous=None, mandatory=False, installer=False):
    from refactor.desktop.delivery import update_protocol as protocol

    protocol.APP_ID = APP_ID
    previous_fn = b.previous_release
    # A newly-created local test feed starts at a synthetic old version. A
    # later manifest points at the actual signed local predecessor.
    b.previous_release = lambda *args, **kwargs: SimpleNamespace(
        version='0.0.0', signed={'min_supported_version': '0.0.0', 'mandatory': False}
    ) if not kwargs.get('path') else previous_fn(*args, **kwargs)
    out = TASK / ('release-' + label)
    try:
        b.release(SimpleNamespace(
            payload=payload, feed=FEED, app_id=APP_ID, public_key=TASK/'test-public.json',
            previous_release=previous, output=out, epoch=epoch, valid_days=3,
            notes='160 本机预览验收 ' + label, base_versions=[], mandatory=mandatory,
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


def publish(label):
    source = TASK/('release-'+label)/'stable.json'
    raw = source.read_bytes()
    envelope = json.loads(raw)
    signed = envelope['signed']
    dest = TASK/'webroot/updates/app/stable'
    dest.mkdir(parents=True, exist_ok=True)
    (dest/'stable.json').write_bytes(raw)
    pointer = {
        'version': signed['version'], 'metadata_version': signed['metadata_version'],
        'sha256': __import__('hashlib').sha256(raw).hexdigest(), 'size': len(raw),
    }
    (dest/'latest.json').write_text(json.dumps(pointer, separators=(',', ':')), encoding='utf8')
    return pointer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('label', choices=('X-release160b', 'Y-required-release160', 'Y-optional-release160'))
    parser.add_argument('--source', type=Path, default=ROOT)
    args = parser.parse_args()
    if args.label == 'X-release160b':
        b.ROOT = args.source.resolve()
        payload = freeze(args.label, '9.160.1', args.source.resolve())
        b.ROOT = ROOT
        stage(args.label, payload, '9.160.1',
              99160211, installer=True)
    else:
        required = args.label == 'Y-required-release160'
        payload = TASK/('payload-'+args.label)
        shutil.copytree(TASK/'freeze-X-release160b/app/MentorFlow', payload)
        if (payload/'SELF-CHECK').exists():
            shutil.rmtree(payload/'SELF-CHECK')
        version = '9.160.2' if required else '9.160.3'
        version_file = payload/'mentorflow-version.json'
        data = json.loads(version_file.read_bytes())
        data['version'] = version
        version_file.unlink()
        version_file.write_bytes(b.canonical(data))
        stage(args.label, payload, version, 99160212 if required else 99160213,
              previous=TASK/'release-X-release160b/stable.json', mandatory=required, installer=False)
    print(json.dumps({'label': args.label, 'release': str(TASK/('release-'+args.label)/'stable.json'),
                      'setup': str(TASK/('setup-'+args.label)/'MentorFlow-Setup.exe')
                               if args.label == 'X-release160b' else None}, ensure_ascii=False))


if __name__ == '__main__':
    main()





