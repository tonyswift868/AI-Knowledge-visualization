"""Atomically select a task-local 160 signed release for the local feed."""
from pathlib import Path
import argparse
import hashlib
import json
import os

if not os.environ.get('MF_UAT_ROOT'):
    raise SystemExit('MF_UAT_ROOT is required for 160 rehearsal; refusing the legacy default directory')
if os.environ.get('MF_UAT_APP_ID') != 'MentorFlow.UAT.gate160':
    raise SystemExit('MF_UAT_APP_ID must be MentorFlow.UAT.gate160')

from common import TASK, environment

environment()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('label', choices=('X-release160b', 'Y-required-release160', 'Y-optional-release160'))
    args = parser.parse_args()
    source = TASK/('release-'+args.label)/'stable.json'
    raw = source.read_bytes()
    release = json.loads(raw)
    signed = release['signed']
    stable = TASK/'webroot/updates/app/stable'
    stable.mkdir(parents=True, exist_ok=True)
    pointer = {'version': signed['version'], 'metadata_version': signed['metadata_version'],
               'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
    for target, data in ((stable/'stable.json', raw),
                         (stable/'latest.json', json.dumps(pointer, separators=(',', ':')).encode())):
        temporary = target.with_name(target.name+'.new')
        temporary.write_bytes(data)
        os.replace(temporary, target)
    print(json.dumps({'published': args.label, 'version': signed['version'],
                      'mandatory': signed['mandatory'], 'pointer': pointer}, ensure_ascii=False))


if __name__ == '__main__':
    main()
