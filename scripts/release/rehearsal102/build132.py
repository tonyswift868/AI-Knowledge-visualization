"""132 gate: independent 2.0.14 / P / Q freezes using existing release tools."""
import argparse
import io
import subprocess
import sys
import tarfile
from pathlib import Path
from common import ROOT, TASK, APP_ID, environment
from build import freeze, stage, b


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('label', choices=['O', 'P', 'Q'])
    args = parser.parse_args()
    env = environment()
    assert APP_ID == 'MentorFlow.UAT.gate132'
    source = ROOT
    if args.label == 'O':
        source = TASK/'source-O'
        if not source.exists():
            source.mkdir()
            archive = subprocess.check_output(['git', 'archive', '64fdf076'], cwd=ROOT, env=env)
            with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
                stream.extractall(source, filter='data')
    version, epoch = {'O': ('2.0.14', 99013201), 'P': ('9.132.15', 99013215),
                      'Q': ('9.132.16', 99013216)}[args.label]
    payload = freeze(args.label, version, source)
    b.ROOT = ROOT
    stage(args.label, payload, version, epoch, installer=args.label != 'Q')
    command = [sys.executable, str(Path(__file__).with_name('refreeze103.py')),
               args.label, '--check-only']
    if args.label != 'O':
        command += ['--source-root', str(ROOT)]
    subprocess.run(command, env=env, check=True)


if __name__ == '__main__':
    main()
