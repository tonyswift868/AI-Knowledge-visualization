"""Rebuild affected frozen code from retained specs, never copy-version a package."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from common import TASK, ROOT, environment
from build import b, stage

p=argparse.ArgumentParser();p.add_argument('label',choices=['P','X'])
p.add_argument('--stage-only',action='store_true',help='Resume signing/compilation after the final freezes already succeeded')
args=p.parse_args()
env=environment();work=TASK/('freeze-'+args.label)
for spec,dest,cache in [('UserRuntime.spec','runtime','runtime-build'),('MentorFlow.spec','app','app-build')]:
    if args.stage_only: continue
    b.run([sys.executable,'-B','-m','PyInstaller','--clean','--noconfirm','--distpath',work/dest,
           '--workpath',work/cache,work/spec],work,work/(dest+'-final.log'))
payload=work/'app/MentorFlow'
shutil.copytree(work/'runtime/UserRuntime',payload/'UserRuntime',dirs_exist_ok=True)
shutil.copyfile(work/'mentorflow-version.json',payload/'mentorflow-version.json')
b.ROOT=ROOT
version,epoch={'P':('9.105.1',99010502),'X':('9.105.2',99010503)}[args.label]
stage(args.label,payload,version,epoch,installer=args.label=='P')
subprocess.run([sys.executable,str(Path(__file__).with_name('refreeze103.py')),args.label,
               '--check-only','--source-root',str(ROOT)],env=env,check=True)
