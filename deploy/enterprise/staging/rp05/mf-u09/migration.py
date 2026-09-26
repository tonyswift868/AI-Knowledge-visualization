"""One-off migration using the existing migrate service; default prints only.

The temporary fifth override applies only to `compose run migrate`. It is
removed afterwards and never participates in the live backend Compose config.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import tempfile

from configure import IMAGE, compose, check_bundle


def override(package):
    return {'services': {'migrate': {'image': IMAGE, 'volumes': [
        {'type': 'bind', 'source': str(package.resolve()), 'target': '/opt/mentorflow/mf-u09',
         'read_only': True, 'bind': {'create_host_path': False}}]}}}


def command(action, env_file, override_file):
    return compose(env_file) + ['-f', str(override_file), 'run', '--rm', '--no-deps', '--pull', 'never',
        '--entrypoint', 'python', 'migrate', '/opt/mentorflow/mf-u09/migration_db.py', action, '--execute']


def operate(action, env_file, package):
    check_bundle(package)
    # Existing service retains its network, migrator secrets, UID and tmpfs.
    # No secret values or merged configuration are printed or written here.
    with tempfile.TemporaryDirectory(prefix='mf-u09-migration-') as directory:
        path = Path(directory) / 'migration.override.json'
        path.write_text(json.dumps(override(package)), encoding='utf-8')
        subprocess.run(command(action, env_file, path), check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['upgrade', 'check', 'downgrade'])
    parser.add_argument('--env-file', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    if not args.execute:
        print('DRY RUN: temporary override changes only one-off migrate image and read-only package mount')
        print(shlex.join(command(args.action, args.env_file, '<temporary-migration.override.json>')))
        return
    operate(args.action, args.env_file, Path(__file__).resolve().parent)


if __name__ == '__main__': main()
