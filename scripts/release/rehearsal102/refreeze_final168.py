"""Rebuild only the final App executable from the X analysis/spec inputs."""
from pathlib import Path
import shutil
import sys

from common import ROOT, TASK, environment

env = environment()
sys.path.insert(0, str(ROOT))
from scripts import build_learning_installer as b

source = TASK / 'freeze-X'
target = TASK / 'freeze-X-final'
target.mkdir(parents=True, exist_ok=True)
for name in ('resources', 'web-X', 'runtime', 'component-catalog.json',
             'mentorflow-version.json', 'MentorFlow.ico', 'preview_credentials.py',
             'mentorflow_delivery_authority.py'):
    src = source / name
    dst = target / name
    if dst.exists():
        continue
    if src.is_dir():
        shutil.copytree(src, dst)
    elif src.is_file():
        shutil.copy2(src, dst)
spec = (source / 'MentorFlow.spec').read_text(encoding='utf8')
spec = spec.replace(str(source).replace('\\', '/'), str(target).replace('\\', '/'))
spec_path = target / 'MentorFlow.spec'
spec_path.write_text(spec, encoding='utf8')

original_run = b.run
def task_run(command, work, log):
    command = list(command)
    if any(str(item).endswith('MentorFlow.spec') for item in command):
        command.insert(command.index('PyInstaller') + 1, '--clean')
    original_run(command, work, log)

b.run = task_run
try:
    b.run([sys.executable, '-B', '-m', 'PyInstaller', '--noconfirm',
           '--distpath', target / 'app', '--workpath', target / 'app-build', spec_path],
          target, target / 'app-rebuild.log')
finally:
    b.run = original_run

payload = target / 'app' / 'MentorFlow'
shutil.copytree(target / 'runtime' / 'UserRuntime', payload / 'UserRuntime', dirs_exist_ok=True)
shutil.copy2(target / 'mentorflow-version.json', payload / 'mentorflow-version.json')
authority = (target/'mentorflow_delivery_authority.py').read_text(encoding='utf8')
if "APP_ID='MentorFlow.UAT.gate168'" not in authority or 'gate168-test-only' not in authority:
    raise RuntimeError('generated product authority does not identify only the gate168 test identity')
print(f'final App rebuilt with current source: {payload}')
