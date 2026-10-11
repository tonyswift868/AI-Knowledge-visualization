"""Inspect real transaction files and compare retained learning fixtures."""
import argparse, json, hashlib
from pathlib import Path
from common import TASK
parser=argparse.ArgumentParser();parser.add_argument('root',type=Path);parser.add_argument('--version');parser.add_argument('--fixtures',action='store_true');args=parser.parse_args()
root=args.root.resolve()
if not root.is_relative_to(TASK.resolve()):raise ValueError('owned root required')
version=json.loads((root/'current-release.json').read_bytes())['signed']['version']
updater=json.loads((root/'updater-release.json').read_bytes())['signed']['version']
journal=json.loads((root/'per-user-transaction.json').read_bytes())
result=dict(root=str(root),version=version,updater=updater,phase=journal['phase'],pending=(root/'pending-update.json').exists(),
    junk=[p.name for p in root.iterdir() if p.name.startswith(('staging-','retired-','failed-'))])
if args.version:
    assert version==args.version and updater==args.version and journal['phase']=='HEALTHY',result
    assert not result['pending'],result
if args.fixtures:
    fixtures=json.loads((TASK/'logs/learning-fixtures.json').read_bytes())
    result['learning_unchanged']=all(hashlib.sha256((root/name).read_bytes()).hexdigest()==sha for name,sha in fixtures.items())
    assert result['learning_unchanged'],result
    assert not result['junk'],result
print(json.dumps(result,ensure_ascii=False))
