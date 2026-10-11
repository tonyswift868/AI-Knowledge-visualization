"""105: independently freeze legacy O, prepared-update P and target X."""
import argparse
import io
import subprocess
import sys
import tarfile
from common import ROOT, TASK, environment
from build import freeze, stage, b

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('label', choices=['O', 'P', 'X'])
    args = parser.parse_args()
    env = environment()
    source = ROOT
    if args.label == 'O':
        source = TASK/'source-O'
        if not source.exists():
            source.mkdir()
            archive = subprocess.check_output(['git', 'archive', '10e625f4'], cwd=ROOT, env=env)
            with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
                stream.extractall(source, filter='data')
    version, epoch = {'O': ('2.0.13', 99010501), 'P': ('9.105.1', 99010502), 'X': ('9.105.2', 99010503)}[args.label]
    payload = freeze(args.label, version, source)
    b.ROOT = ROOT
    stage(args.label, payload, version, epoch, installer=args.label != 'X')
    subprocess.run([sys.executable, str(__import__('pathlib').Path(__file__).with_name('refreeze103.py')),
                    args.label, '--check-only'], env=env, check=True)

if __name__ == '__main__': main()
