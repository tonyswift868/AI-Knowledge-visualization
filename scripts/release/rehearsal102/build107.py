"""107 gate: three independent freezes, mainline Inno for every installer."""
import argparse
import io
import subprocess
import sys
import tarfile
from pathlib import Path
from common import ROOT, TASK, APP_ID, environment
from build import freeze, stage, b

def main():
    p = argparse.ArgumentParser()
    p.add_argument('label', choices=['O', 'P', 'Q'])
    args = p.parse_args()
    env = environment()
    assert APP_ID == 'MentorFlow.UAT.gate107'
    source = ROOT
    if args.label == 'O':
        source = TASK/'source-O'
        if not source.exists():
            source.mkdir()
            archive = subprocess.check_output(['git','archive','10e625f4'],cwd=ROOT,env=env)
            with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
                stream.extractall(source,filter='data')
    version, epoch = {'O':('2.0.13',99010701), 'P':('9.107.14',99010714),
                      'Q':('9.107.15',99010715)}[args.label]
    payload = freeze(args.label,version,source)
    b.ROOT = ROOT
    stage(args.label,payload,version,epoch,installer=args.label != 'Q')
    command=[sys.executable,str(Path(__file__).with_name('refreeze103.py')),args.label,'--check-only']
    if args.label != 'O': command += ['--source-root',str(ROOT)]
    subprocess.run(command,env=env,check=True)

if __name__ == '__main__': main()
