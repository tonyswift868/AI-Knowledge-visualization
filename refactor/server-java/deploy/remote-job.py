#!/usr/bin/env python3
"""Submit/poll a server job; a broken SSH connection is never job failure."""
import argparse
import shlex
import subprocess
import time


def ssh_command(args, action, command=()):
    remote = ['sudo', '-n', 'sh', args.runner, action, args.job_dir, *command]
    return ['ssh', '-F', args.ssh_config, '-o', 'ServerAliveInterval=15',
            '-o', 'ServerAliveCountMax=6', args.host, shlex.join(remote)]


def query(args, action, command=()):
    try:
        result = subprocess.run(ssh_command(args, action, command),
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    lines = result.stdout.strip().splitlines()
    return lines[-1] if lines else None


def wait(args, initial=None):
    until = time.monotonic() + args.timeout
    state = initial
    previous = object()
    while True:
        if state != previous:
            print(state or 'SSH unavailable; outcome unknown, reconnecting', flush=True)
            previous = state
        if state == 'SUCCEEDED 0':
            return 0
        if state and state.startswith('FAILED '):
            code = state.removeprefix('FAILED ')
            if code.isdecimal() and int(code) > 0:
                return 1
        if time.monotonic() >= until:
            print('Outcome unresolved; reconnect with wait using this same job directory. No rollback requested.', flush=True)
            return 2
        time.sleep(args.interval)
        state = query(args, 'status')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-config', required=True)
    parser.add_argument('--host', default='mentorflow')
    parser.add_argument('--runner', required=True)
    parser.add_argument('--job-dir', required=True)
    parser.add_argument('--timeout', type=float, default=1800)
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('action', choices=['start', 'wait'])
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.timeout < 0 or args.interval <= 0:
        parser.error('timeout must be nonnegative and interval must be positive')
    if args.command[:1] == ['--']:
        args.command = args.command[1:]
    if args.action == 'start' and not args.command:
        parser.error('start requires a command')
    if args.action == 'wait' and args.command:
        parser.error('wait does not take a command')
    state = query(args, 'start', args.command) if args.action == 'start' else query(args, 'status')
    return wait(args, state)


if __name__ == '__main__':
    raise SystemExit(main())
