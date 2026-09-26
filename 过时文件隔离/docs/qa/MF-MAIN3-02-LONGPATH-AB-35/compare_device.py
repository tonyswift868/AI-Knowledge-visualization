"""Read-only exact record/revision comparison; not a writer or an authority."""
import sqlite3,json,sys,hashlib
from pathlib import Path
O=Path(__file__).parent
sys.path.insert(0,str(O/'build/r4/source'))
from backend.app.product_sync_contract import RECORDS, public_value
stage,device=sys.argv[1:3]
server=json.loads((O/f'evidence/staging/server-{stage}.json').read_text(encoding='utf-8'))['entities']
base=Path('\\\\?\\'+str(O/f'devices/{device}/vault/accounts'))
dbs=list(base.rglob('mentorflow_v2.db'));assert len(dbs)==1
c=sqlite3.connect(str(dbs[0]));c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
scope=json.loads(c.execute('SELECT scope FROM product_sync_binding').fetchone()[0])
results=[]
for e in server:
 if any(e[k]!=scope[k] for k in scope):continue
 record=e['payload'].get('product_record')
 if not record:continue
 name=record['record_type'];spec=RECORDS[name]
 if name.startswith('legacy_'):continue
 values=record['values'];table=spec.get('table',name)
 key=[values[k] for k in spec['keys']];where=' AND '.join('"'+k+'"=?' for k in spec['keys'])
 row=c.execute('SELECT * FROM "'+table+'" WHERE '+where,key).fetchone()
 rev=c.execute('SELECT revision,deleted FROM product_sync_revisions WHERE entity_id=?',(e['entity_id'],)).fetchone()
 results.append(dict(entity_id=e['entity_id'],kind=e['kind'],record_type=name,server_revision=e['revision'],local_revision=rev['revision'] if rev else None,deleted=e['deleted'],values_match=(row is None) if e['deleted'] else bool(row and {k:public_value(row[k]) for k in spec['columns']}==values),revision_match=bool(rev and rev['revision']==e['revision'] and bool(rev['deleted'])==e['deleted'])))
report=dict(device=device,stage=stage,comparison='SERVER_PRODUCT_ALLOWLIST_TO_ACCOUNT_PUBLIC_PROJECTION_READ_ONLY',all_values_match=all(r['values_match'] for r in results),all_revisions_match=all(r['revision_match'] for r in results),records=results)
(O/f'evidence/staging/compare-{stage}-{device}.json').write_text(json.dumps(report,indent=2))
print(json.dumps({k:v for k,v in report.items() if k!='records'}));print('records',len(results),'mismatches',sum(not r['values_match'] or not r['revision_match'] for r in results))
