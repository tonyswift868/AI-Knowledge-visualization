"""Re-freeze a changed UAT runtime hook without reusing a signed payload."""
from common import environment, TASK, ROOT
from pathlib import Path
environment()
import sys, shutil, argparse
sys.path.insert(0,str(ROOT))
from scripts import build_learning_installer as b
from build import stage
parser=argparse.ArgumentParser();parser.add_argument('label');parser.add_argument('--old',action='store_true');parser.add_argument('--output',type=Path);args=parser.parse_args()
work=TASK/('freeze-O' if args.old else 'freeze-X')
output=args.output or TASK/('final-old-app' if args.old else 'final-app')
if args.old:
    b.ROOT=TASK/'old-source'
    spec=work/'MentorFlow.spec';text=spec.read_text();text=text.replace(repr(str(Path(__file__).with_name('timing_hook.py')))+', ','');spec.write_text(text)
b.run([sys.executable,'-B','-m','PyInstaller','--noconfirm','--distpath',output,
       '--workpath',work/'app-build',work/'MentorFlow.spec'],work,work/'app-final-build.log')
payload=output/'MentorFlow'
shutil.copytree(work/'runtime/UserRuntime',payload/'UserRuntime')
shutil.copy2(work/'mentorflow-version.json',payload/'mentorflow-version.json')
stage(args.label,payload,'2.0.12' if args.old else '9.102.5',99010260 if args.old else 99010261)
