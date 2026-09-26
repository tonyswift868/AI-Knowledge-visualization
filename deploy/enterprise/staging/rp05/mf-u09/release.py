"""MF-U09 local operator commands. Default: print only. No SSH or uploads."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

from configure import BASE_IMAGE, IMAGE, CATALOG, compose


def commands(action, env_file, package):
    helper = ['python3', str(package / 'configure.py')]
    def configure(step):
        return helper + [step, '--env-file', str(env_file), '--execute']
    docker = compose(env_file)
    if action == 'prepare':
        return [configure('bundle'), ['docker', 'image', 'inspect', BASE_IMAGE, '--format', '{{.Id}}'],
                docker + ['config', '--quiet'], configure('capture'),
                ['install', '-d', '-m', '0755', CATALOG]]
    if action == 'build':
        manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
        # Inspect sources inside the new image without running app startup.
        program = ('import hashlib,pathlib; expected=' + repr(manifest['sha256']) + '; '
                   'assert all(hashlib.sha256((pathlib.Path("/app")/p).read_bytes()).hexdigest()==h '
                   'for p,h in expected.items()); print("four overlay hashes: OK")')
        return [configure('bundle'), ['docker', 'image', 'inspect', BASE_IMAGE, '--format', '{{.Id}}'],
                ['docker', 'build', '--network=none', '--pull=false', '-t', IMAGE, '-f', str(package / 'Dockerfile'), str(package)],
                ['docker', 'run', '--rm', '--network=none', '--read-only', '--entrypoint', 'python', IMAGE, '-c', program],
                ['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}']]
    if action == 'deploy':
        return [['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'],
                configure('check-base'), configure('patch'), docker + ['config', '--quiet'], configure('check-new'),
                docker + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate', 'backend'],
                docker + ['ps', 'backend']]
    if action == 'rollback':
        return [['docker', 'image', 'inspect', BASE_IMAGE, '--format', '{{.Id}}'], configure('rollback'),
                docker + ['config', '--quiet'], configure('check-base'),
                docker + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate', 'backend'],
                docker + ['ps', 'backend']]
    raise ValueError('unknown action')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'build', 'deploy', 'rollback'])
    parser.add_argument('--env-file', required=True, type=Path, help='Existing operator-managed Compose env file')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    for command in commands(args.action, args.env_file, Path(__file__).resolve().parent):
        print(shlex.join(command), flush=True)
        if args.execute:
            subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
