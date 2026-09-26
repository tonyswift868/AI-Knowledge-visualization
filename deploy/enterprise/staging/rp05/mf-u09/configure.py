"""Guard the final override and capture/restore an MF-U04 rollback snapshot.

CLI is a dry run unless --execute is present. No remote transport is used.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile

PROJECT = 'mentorflow-e1-preview'
BASE_IMAGE = 'mentorflow-mf-u04-backend:MF-U04'
IMAGE = 'mentorflow-mf-u09-backend:MF-U09'
COMPOSE_FILES = (
    '/opt/mentorflow/releases/21337c1eda64f7e087fcade77c3d4cd7dce4a0af-exact-source/deploy/enterprise/staging/rp05/compose.account-core-mvp.yml',
    '/opt/mentorflow/artifacts/gatea-native-20260913-08/backend.override.json',
    '/opt/mentorflow/artifacts/r2-04-final-20260917/runtime.override.json',
    '/opt/mentorflow/artifacts/mf-d04-20260922/runtime.override.json',
)
LAST_OVERRIDE = Path(COMPOSE_FILES[-1])
ROLLBACK = Path('/opt/mentorflow/state/mentorflow-e1-preview/mf-b03-rollback/mf-u09')
CATALOG = '/opt/mentorflow/state/mentorflow-e1-preview/catalog'
TARGET = '/run/mentorflow/catalog'
SOURCES = ('backend/app/enterprise/identity/native.py', 'backend/app/enterprise_tenancy/structured_sync.py',
           'backend/app/enterprise/catalog_api.py', 'backend/app/account_core_mvp.py',
           'backend/app/enterprise/structured_sync_api.py', 'backend/alembic/versions/0042_structured_sync_changes.py')


def compose(env_file):
    args = ['docker', 'compose', '--env-file', str(env_file), '-p', PROJECT, '--profile', 'account-core-mvp']
    for path in COMPOSE_FILES:
        args.extend(['-f', path])
    return args


def _target(volume):
    if isinstance(volume, dict):
        return volume.get('target')
    if isinstance(volume, str):
        parts = volume.split(':')
        return parts[1] if len(parts) > 1 else parts[0]
    raise ValueError('unsupported Compose volume entry')


def patched_override(value):
    result = deepcopy(value)
    backend = result['services']['backend']
    if backend.get('image') != BASE_IMAGE:
        raise ValueError('final override is no longer MF-U04; stop and review')
    volumes = backend.setdefault('volumes', [])
    if not isinstance(volumes, list) or any(_target(v) == TARGET for v in volumes):
        raise ValueError('catalog mount already exists or volumes are invalid; stop and review')
    backend['image'] = IMAGE
    volumes.append({'type': 'bind', 'source': CATALOG, 'target': TARGET, 'read_only': True,
                    'bind': {'create_host_path': False}})
    return result


def validate_config(before, after, *, upgraded):
    actual = deepcopy(after)
    backend = actual['services']['backend']
    expected = IMAGE if upgraded else BASE_IMAGE
    if backend.get('image') != expected:
        raise ValueError('unexpected backend image')
    env = backend.get('environment', {})
    if any(str(env.get(k)) != '1' for k in ('MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS', 'WEB_CONCURRENCY')):
        raise ValueError('native authentication must remain single process')
    if env.get('MENTORFLOW_CATALOG_DIR', TARGET) != TARGET:
        raise ValueError('catalog directory differs from the mounted default')
    if upgraded:
        mounts = backend.get('volumes', [])
        catalog = [v for v in mounts if _target(v) == TARGET]
        if (len(catalog) != 1 or not isinstance(catalog[0], dict)
                or catalog[0].get('source') != CATALOG or catalog[0].get('type') != 'bind'
                or catalog[0].get('read_only') is not True):
            raise ValueError('catalog must be the exact read-only bind mount')
        backend['volumes'] = [v for v in mounts if _target(v) != TARGET]
        if not backend['volumes'] and 'volumes' not in before['services']['backend']:
            del backend['volumes']
        backend['image'] = BASE_IMAGE
    if actual != before:
        raise ValueError('merged Compose changed outside backend image/catalog mount; stop and review')


def check_bundle(package):
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('baseline') != '2014a74' or set(manifest['sha256']) != set(SOURCES):
        raise ValueError('bundle manifest is invalid')
    files = {p.relative_to(package / 'overlay').as_posix() for p in (package / 'overlay').rglob('*') if p.is_file()}
    if files != set(SOURCES):
        raise ValueError('bundle must overlay exactly the six declared files')
    for source in SOURCES:
        raw = (package / 'overlay' / source).read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest['sha256'][source]:
            raise ValueError('bundle checksum mismatch: ' + source)
        compile(raw, source, 'exec')


def _config(env_file):
    result = subprocess.run(compose(env_file) + ['config', '--format', 'json'], check=True, capture_output=True)
    return json.loads(result.stdout)


def _atomic(path, raw):
    mode = stat.S_IMODE(path.stat().st_mode)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, prefix='.mf-u09-') as stream:
        temp = Path(stream.name)
        stream.write(raw)
    try:
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def operate(action, env_file):
    if action == 'bundle':
        check_bundle(Path(__file__).resolve().parent)
        return
    backup = ROLLBACK / 'override.before.json'
    baseline_file = ROLLBACK / 'compose.before.json'
    if action == 'capture':
        original = LAST_OVERRIDE.read_bytes()
        patched_override(json.loads(original))
        baseline = _config(env_file)
        validate_config(baseline, baseline, upgraded=False)
        ROLLBACK.mkdir(mode=0o700, parents=True, exist_ok=True)
        if backup.exists() or baseline_file.exists():
            raise ValueError('rollback capture already exists; never overwrite it')
        for path, raw in ((backup, original), (baseline_file, json.dumps(baseline).encode())):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
        return
    original = backup.read_bytes()
    baseline = json.loads(baseline_file.read_bytes())
    patch = patched_override(json.loads(original))
    if action == 'patch':
        if LAST_OVERRIDE.read_bytes() != original:
            raise ValueError('final override changed since capture; stop and review')
        validate_config(baseline, _config(env_file), upgraded=False)
        _atomic(LAST_OVERRIDE, (json.dumps(patch, indent=2) + '\n').encode())
    elif action == 'rollback':
        current = LAST_OVERRIDE.read_bytes()
        if current == original:
            return  # Migration failed before the image/config switch.
        if json.loads(current) != patch:
            raise ValueError('final override differs from MF-U09; refuse to overwrite unrelated edits')
        _atomic(LAST_OVERRIDE, original)
    elif action == 'check-rollback':
        current = LAST_OVERRIDE.read_bytes()
        upgraded = current != original
        if upgraded and json.loads(current) != patch:
            raise ValueError('final override differs from both captured and MF-U09 config')
        validate_config(baseline, _config(env_file), upgraded=upgraded)
    elif action in {'check-base', 'check-new'}:
        validate_config(baseline, _config(env_file), upgraded=action == 'check-new')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['bundle', 'capture', 'patch', 'rollback', 'check-base', 'check-new', 'check-rollback'])
    parser.add_argument('--env-file', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    if not args.execute:
        print(shlex.join(['python3', str(Path(__file__).resolve()), args.action,
                          '--env-file', str(args.env_file), '--execute']))
        return 0
    try:
        operate(args.action, args.env_file)
        print(args.action + ': OK')
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        # The captured config can contain secrets; never print it or stderr.
        print(args.action + ': failed; inspect local configuration without exposing secrets', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
