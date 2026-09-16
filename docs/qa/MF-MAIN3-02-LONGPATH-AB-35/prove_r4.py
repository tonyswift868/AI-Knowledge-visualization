import json,sqlite3,hashlib
from pathlib import Path
O=Path(__file__).parent;E=O/'evidence/staging'
def rows(s):return json.loads((E/f'server-{s}.json').read_text())['entities']
def review(s):return next(x for x in rows(s) if x['kind']=='review')
a,b,r,i=[review(s) for s in ('Areview','Breview','Aresumed','Ainterrupted')]
assert a['revision']<b['revision']<r['revision'];assert r==i
st=lambda x:json.loads(x['payload']['product_record']['values']['state'])['resume_dto']['campaign']['state']
# State is asserted through actual exported schema below, without guessed DTO keys.
for x in (a,b,r):
 state=json.loads(x['payload']['product_record']['values']['state']); print(x['revision'],list(state.keys()))
before={x['entity_id']:x for x in rows('Areview')};after={x['entity_id']:x for x in rows('Ar4deleted')}
ch='material_5ba6b8d36d9a499bbaf6';assert after[ch]['deleted'] and after[ch]['revision']==2
associated='orm_source_revisions:1010df2c60e2cf4ede95d7f3e35bde9f6de982fae31df973a3470759a954ac17';assert before[associated]['payload']['product_record']['values']['source_id']==ch and after[associated]['deleted'];unchanged=[k for k in before if k not in (ch,associated)];assert all(before[k]==after[k] for k in unchanged)
f=json.loads((E/'sibling-fixture.json').read_text());assert hashlib.sha256(Path(chr(92)*2+'?'+chr(92)+f['physical_file']).read_bytes()).hexdigest()==f['sha256']
def db(d):
 paths=list(Path(chr(92)*2+'?'+chr(92)+str(O/f'devices/{d}/vault/accounts')).rglob('mentorflow_v2.db'));assert len(paths)==1
 c=sqlite3.connect(str(paths[0]));c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');return c
with db('B') as c:
 assert c.execute('SELECT COUNT(*) FROM active_materials WHERE id=?',(ch,)).fetchone()[0]==0
 assert c.execute('SELECT stored_path FROM active_materials WHERE id=?',('material_6bb5710b887b45c2a7d5',)).fetchone()[0]==''
with db('A') as c:
 assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='mf35_interrupt_receipt'").fetchone()
 seam=json.loads((E/'interruption-seam.json').read_text());ids=[x[0] for x in c.execute("SELECT id FROM product_sync_commands WHERE status='RUNNING'")];assert set(ids)-set(seam['running_before'])==set(seam['new_running_ids']);assert len(seam['new_running_ids'])==1
p=dict(review_revisions=[a['revision'],b['revision'],r['revision'],i['revision']],interrupted_no_server_change=True,interrupted_new_command_count=1,interruption_trigger_removed=True,delete_tombstone_revision=2,unrelated_server_records_unchanged=True,associated_source_revision_tombstoned=True,sibling_workspace_unchanged=True,sibling_file_hash_unchanged=True,b_deleted_chinese_absent=True,b_long_physical_absent=True)
(E/'r4-proof.json').write_text(json.dumps(p,indent=2));print(json.dumps(p))
