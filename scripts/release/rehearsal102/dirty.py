"""Create small owned fixtures matching a rolled-back user's installation."""
import argparse, json, uuid, hashlib
from pathlib import Path
from common import TASK
parser=argparse.ArgumentParser();parser.add_argument('root',type=Path);args=parser.parse_args()
root=args.root.resolve()
if not root.is_relative_to(TASK.resolve()):raise ValueError('owned root required')
fixtures={}
for name,content in [('notes.txt','学习记录\n第一课已经完成。'.encode()),('fixture.bin',bytes(range(256))),('preferences.json',b'{"fixture":true}')]:
    file=root/'Data/uat-learning'/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(content)
    fixtures[file.relative_to(root).as_posix()]=hashlib.sha256(content).hexdigest()
for prefix in ('staging','retired','failed'):
    path=root/(prefix+'-'+uuid.uuid4().hex);path.mkdir();(path/'tiny.tmp').write_bytes(b'obsolete program bytes')
cache=root.parent/'.MentorFlow-runtime/.mfu/abcdef123456'
stage=cache/'App';stage.mkdir(parents=True,exist_ok=True);(stage/'tiny.tmp').write_bytes(b'old pending')
pending=dict(schema='mentorflow.pending-update.v1',state='PENDING_INSTALL',stage=str(stage),target='2.0.11',release_sha256='0'*64)
# Use the exact existing protocol schema, not a trusted installation bypass.
import sys;sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from refactor.desktop.delivery.per_user_update_core import SCHEMA
pending['schema']=SCHEMA
(root/'pending-update.json').write_text(json.dumps(pending))
path=root/'per-user-transaction.json';journal=json.loads(path.read_bytes());journal['phase']='ROLLED_BACK';path.write_text(json.dumps(journal))
(TASK/'logs/learning-fixtures.json').write_text(json.dumps(fixtures,indent=2))
print(json.dumps({'root':str(root),'fixtures':fixtures,'pending':pending},ensure_ascii=False))
