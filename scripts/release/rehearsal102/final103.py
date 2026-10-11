"""Rebundle final web assets into X's independently frozen Python runtime.

Run before scenario103 publishes N. Re-sign the resulting complete payload;
never mutate an installed tree or pretend a copy/version bump is a new build.
"""
import shutil
from pathlib import Path
from common import ROOT,TASK,environment
environment()
from build import b,stage

web=TASK/'web-final'
b.run(['D:/MentorFlow/.local/node/node.exe',
    'D:/MentorFlow/.local/web-deps/node_modules/vite/bin/vite.js','build',
    '--config',ROOT/'refactor/learning/web/vite.config.mjs','--outDir',web],TASK/'temp',TASK/'logs/web-final.log')
payload=TASK/'freeze-X/app/MentorFlow'
target=payload/'_internal/refactor/learning/web/dist'
if not target.is_dir():raise ValueError('expected frozen X web payload missing')
# The new bundle uses content hashes. Obsolete files are not referenced by the
# index and are removed during final task cleanup with the rest of the payload.
shutil.copytree(web,target,dirs_exist_ok=True)
b.ROOT=ROOT
stage('N',payload,'9.103.14',99010314,TASK/'release-O/stable.json',False)
print('final N signed from frozen new Python + freshly built final web')
