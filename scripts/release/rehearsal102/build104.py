"""Independent O/new freezes for BUG-79; never launch before local-only checks."""
import io
import subprocess
import sys
import tarfile
from common import ROOT, TASK, environment

env = environment()
here = __import__('pathlib').Path(__file__).parent
source = TASK / 'source-O'
source.mkdir(exist_ok=True)
archive = subprocess.check_output(['git', 'archive', '10e625f4'], cwd=ROOT, env=env)
with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
    stream.extractall(source, filter='data')
for label, tree in (('O', source), ('X', ROOT)):
    subprocess.run([sys.executable, str(here/'build.py'), label, '--source', str(tree)], env=env, check=True)
    subprocess.run([sys.executable, str(here/'refreeze103.py'), label, '--check-only'], env=env, check=True)
print('104 independent builds and local-only archive checks complete')
