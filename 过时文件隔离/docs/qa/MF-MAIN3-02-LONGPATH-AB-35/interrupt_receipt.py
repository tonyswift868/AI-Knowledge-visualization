"""One reversible SQLite abort at the existing command receipt boundary, TEST A only."""
import sqlite3,json,re,sys
from pathlib import Path
O=Path(__file__).parent;action=sys.argv[1];campaign=sys.argv[2] if len(sys.argv)>2 else ''
root=O/'devices/A';assert json.loads((root/'.workbench-owner.json').read_text())['root']==str(root)
paths=list(Path('\\\\?\\'+str(root/'vault/accounts')).rglob('mentorflow_v2.db'));assert len(paths)==1
c=sqlite3.connect(str(paths[0]));c.row_factory=sqlite3.Row
U=Path((O/'uat-root.txt').read_text());f=json.loads((U/'fixture-credentials.json').read_text());scope=json.loads(c.execute('SELECT scope FROM product_sync_binding').fetchone()[0]);assert scope['subject_id']==f['subjects']['uat33-member'];f.clear()
if action=='install':
 assert re.fullmatch(r'review-demo-[0-9a-f]{16}',campaign)
 assert c.execute('SELECT 1 FROM product_review_state WHERE id=?',(campaign,)).fetchone()
 before=[r[0] for r in c.execute("SELECT id FROM product_sync_commands WHERE status='RUNNING'")]
 with c:
  c.execute("CREATE TRIGGER mf35_interrupt_receipt BEFORE UPDATE OF status ON product_sync_commands WHEN NEW.status='COMPLETED' AND json_extract(NEW.response,'$.campaign_id')='"+campaign+"' AND json_extract(NEW.response,'$.state')='paused' BEGIN SELECT RAISE(ABORT,'MF35_TEST_RECEIPT_INTERRUPTION'); END")
 (O/'evidence/staging/interruption-seam.json').write_text(json.dumps(dict(scope='OWNED_SYNTHETIC_A_ONLY',campaign_id=campaign,method='SQLITE_ABORT_BEFORE_RECEIPT_COMMIT',running_before=before),indent=2))
 print('One campaign pause receipt interruption armed; no business or sync engine replaced')
elif action=='remove':
 with c:c.execute('DROP TRIGGER IF EXISTS mf35_interrupt_receipt')
 report=json.loads((O/'evidence/staging/interruption-seam.json').read_text());running=[r[0] for r in c.execute("SELECT id FROM product_sync_commands WHERE status='RUNNING'")];report['new_running_ids']=list(set(running)-set(report['running_before']));report['trigger_removed']=True
 assert len(report['new_running_ids'])==1
 (O/'evidence/staging/interruption-seam.json').write_text(json.dumps(report,indent=2));print('Trigger removed; exactly one interrupted command remains for same-ID GUI retry')
else:raise ValueError('UNKNOWN_ACTION')
