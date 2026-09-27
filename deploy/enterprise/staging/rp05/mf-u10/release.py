"""MF-U10 operator commands. Print only unless --execute is explicitly given."""
import argparse
from pathlib import Path
import shlex
import subprocess
import time

from configure import BASE_IMAGE, IMAGE, CATALOG, compose


def image_check(mode, package):
    return ['docker', 'run', '--rm', '--network=none', '--read-only', '--tmpfs', '/tmp',
            '--mount', 'type=bind,src=' + str(package.resolve()) + ',dst=/bundle,readonly',
            '--entrypoint', 'python', BASE_IMAGE if mode == 'base' else IMAGE,
            '/bundle/image_check.py', mode, '--execute']


def commands(action, env_file, package):
    def configure(step):
        return ['python3', str(package / 'configure.py'), step, '--env-file', str(env_file), '--execute']
    def migration(step):
        return ['python3', str(package / 'migration.py'), step, '--env-file', str(env_file), '--execute']
    docker = compose(env_file)
    up = docker + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate', 'backend']
    if action == 'prepare':
        return [configure('bundle'), image_check('base', package), docker + ['config', '--quiet'],
                configure('capture'), ['install', '-d', '-m', '0755', CATALOG]]
    if action == 'build':
        return [configure('bundle'), image_check('base', package),
                ['docker', 'build', '--network=none', '--pull=false', '-t', IMAGE,
                 '-f', str(package / 'Dockerfile'), str(package)],
                image_check('candidate', package)]
    if action == 'deploy':
        return [configure('bundle'), image_check('base', package), image_check('candidate', package),
                configure('check-base'), docker + ['stop', 'backend'],
                migration('upgrade'), migration('check'), configure('patch'),
                docker + ['config', '--quiet'], configure('check-new'), up]
    if action == 'rollback':
        # Proven MF-U04 admission rejects 0042. A data-preserving downgrade is
        # required before its original, unchanged image can become healthy.
        return [configure('check-rollback'), docker + ['stop', 'backend'],
                migration('downgrade'), configure('rollback'), docker + ['config', '--quiet'],
                configure('check-base'), up]
    raise ValueError('unknown action')


def wait_healthy(env_file, *, timeout=90, clock=time.monotonic, sleep=time.sleep, run=subprocess.run):
    deadline = clock() + timeout
    def query(command):
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError('backend did not become healthy within 90 seconds')
        return run(command, check=True, capture_output=True, text=True,
                   timeout=min(5, remaining)).stdout.strip()
    while clock() < deadline:
        identifiers = query(compose(env_file) + ['ps', '-a', '-q', 'backend']).splitlines()
        if len(identifiers) > 1:
            raise RuntimeError('native backend must remain single process/single container')
        if identifiers:
            state = query(['docker', 'inspect', '--format',
                '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}',
                identifiers[0]])
            if state == 'running healthy':
                return
            if state.split()[0] in ('exited', 'dead', 'restarting') or state.endswith(' missing'):
                raise RuntimeError('backend exited/restarted or lacks a healthcheck')
        sleep(min(2, max(0, deadline - clock())))
    raise TimeoutError('backend did not become healthy within 90 seconds')


def operate(action, env_file, package, *, run=subprocess.run, wait=wait_healthy):
    stopped = False
    try:
        for command in commands(action, env_file, package):
            print(shlex.join(command), flush=True)
            # Even a partly failing stop must restore the original backend.
            if action == 'deploy' and command[-2:] == ['stop', 'backend']:
                stopped = True
            run(command, check=True)
        if action in ('deploy', 'rollback'):
            wait(env_file)
    except Exception as failure:
        if action == 'deploy' and stopped:
            print('MF-U10 failed; restoring captured MF-U04 configuration, schema 0041 and image.', flush=True)
            try:
                operate('rollback', env_file, package, run=run, wait=wait)
            except Exception as rollback_error:
                raise RuntimeError('MF-U10 failed and automatic rollback failed; keep incident open') from rollback_error
            raise RuntimeError('MF-U10 failed; MF-U04 rollback is healthy') from failure
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'build', 'deploy', 'rollback'])
    parser.add_argument('--env-file', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    package = Path(__file__).resolve().parent
    if not args.execute:
        for command in commands(args.action, args.env_file, package):
            print(shlex.join(command))
        if args.action in ('deploy', 'rollback'):
            print('DRY RUN: wait at most 90 seconds for backend healthy.')
        if args.action == 'deploy':
            print('DRY RUN: on any failure after stop, run rollback (0041 + captured MF-U04) and await healthy.')
        return
    operate(args.action, args.env_file, package)


if __name__ == '__main__':
    main()
