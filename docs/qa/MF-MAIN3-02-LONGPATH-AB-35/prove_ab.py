import json,sqlite3
from pathlib import Path
O=Path(__file__).parent;E=O/'evidence/staging'
def read(stage):return json.loads((E/f'server-{stage}.json').read_text(encoding='utf-8'))['entities']
a,b,d=read('Afinal'),read('Bfinal'),read('deleted')
def entity(rows,table,key):
 return next(x for x in rows if x['payload'].get('product_record',{}).get('record_type')==table and x['payload']['product_record']['values'].get('id')==key)
a1=entity(a,'active_plan_tasks','d35-task-1');a2=entity(a,'active_plan_tasks','d35-task-2');b2=entity(b,'active_plan_tasks','d35-task-2')
assert a1['payload']['product_record']['values']['status']=='completed'
assert a2['payload']['product_record']['values']['status']=='planned' and b2['payload']['product_record']['values']['status']=='completed'
assert b2['revision']>a2['revision']
def setting(rows):return next(x for x in rows if x['payload'].get('product_record',{}).get('record_type')=='product_account_settings' and 'D35-' in x['payload']['product_record']['values']['state'])
sa,sb=setting(a),setting(b);assert 'D35-A' in sa['payload']['product_record']['values']['state'] and 'D35-B' in sb['payload']['product_record']['values']['state'];assert sb['revision']>sa['revision']
short='material_7b93b5763ce64676aa4b';deleted=next(x for x in d if x['entity_id']==short);assert deleted['deleted']
for mid in ('material_5ba6b8d36d9a499bbaf6','material_6bb5710b887b45c2a7d5'):
 before=next(x for x in b if x['entity_id']==mid);after=next(x for x in d if x['entity_id']==mid);assert before==after
paths=list(Path('\\\\?\\'+str(O/'devices/B/vault/accounts')).rglob('mentorflow_v2.db'));assert len(paths)==1
with sqlite3.connect(str(paths[0])) as c:
 assert c.execute('SELECT COUNT(*) FROM active_materials WHERE id=?',(short,)).fetchone()[0]==0
 assert c.execute('SELECT stored_path FROM active_materials WHERE id=?',('material_6bb5710b887b45c2a7d5',)).fetchone()[0]==''
 assert c.execute('SELECT COUNT(*) FROM active_chunks').fetchone()[0]==0
report=dict(a_task_1_completed=True,b_task_2_completed=True,task_2_revision=[a2['revision'],b2['revision']],account_setting_revision=[sa['revision'],sb['revision']],short_resource_tombstone_revision=deleted['revision'],b_deleted_resource_absent=True,b_long_payload_absent=True,b_chunks=0,sibling_resources_unchanged=True,scope='FROZEN_GUI_ACTIONS_AND_READ_ONLY_DURABLE_ASSERTIONS')
(E/'ab-core-proof.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
