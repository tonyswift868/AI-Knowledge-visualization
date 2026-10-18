"""Publish one signed 168 release and its paired four-field pointer/policy locally."""
from pathlib import Path
import argparse
import hashlib
import json
import os

if not os.environ.get('MF_UAT_ROOT'):
    raise SystemExit('MF_UAT_ROOT is required for 168 rehearsal')
if os.environ.get('MF_UAT_APP_ID') != 'MentorFlow.UAT.gate168':
    raise SystemExit('MF_UAT_APP_ID must be MentorFlow.UAT.gate168')

from common import TASK, environment

environment()


def atomic_write(target, content):
    temporary = target.with_name(target.name+'.new')
    temporary.write_bytes(content)
    os.replace(temporary, target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('label', choices=('X-final3', 'Y-optional-final3', 'Y-required-final3'))
    parser.add_argument('--hint-mandatory', choices=('true', 'false'))
    parser.add_argument('--hint-floor')
    args = parser.parse_args()

    raw = (TASK/('release-'+args.label)/'stable.json').read_bytes()
    envelope = json.loads(raw)
    signed = envelope['signed']
    mandatory = signed['mandatory'] if args.hint_mandatory is None else args.hint_mandatory == 'true'
    floor = args.hint_floor or signed['min_supported_version']
    digest = hashlib.sha256(raw).hexdigest()
    pointer_fields = {
        'version': signed['version'],
        'metadata_version': signed['metadata_version'],
        'sha256': digest,
        'size': len(raw),
    }
    policy = {**pointer_fields, 'mandatory': mandatory, 'mandatory_floor': floor}
    if set(pointer_fields) != {'version', 'metadata_version', 'sha256', 'size'}:
        raise AssertionError('latest.json must retain the exact four-field legacy schema')
    if any(policy[key] != pointer_fields[key]
           for key in ('version', 'metadata_version', 'sha256', 'size')):
        raise AssertionError('login-policy.json does not pair with latest.json')
    stable = TASK/'webroot/updates/app/stable'
    stable.mkdir(parents=True, exist_ok=True)
    # Keep the four-field latest pointer and the signed-manifest bytes unchanged.
    # Write the matching policy first and latest last, so a reader cannot observe
    # a new pointer paired with the preceding policy during this local rehearsal.
    atomic_write(stable/'stable.json', raw)
    atomic_write(stable/'login-policy.json', json.dumps(policy, separators=(',', ':')).encode())
    atomic_write(stable/'latest.json', json.dumps(pointer_fields, separators=(',', ':')).encode())
    print(json.dumps({'published': args.label, 'version': signed['version'],
                      'manifest_mandatory': signed['mandatory'], 'hint_mandatory': mandatory,
                      'pointer': pointer_fields, 'policy': policy}, ensure_ascii=False))


if __name__ == '__main__':
    main()
