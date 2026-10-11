"""Summarize actual process/transaction timings, not synthetic health proofs."""
import json
from datetime import datetime
from common import TASK

def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf8').splitlines() if line.strip()]

all_stages=rows(TASK/'logs/stages.jsonl')
summary={}
for label in ('A','B','NEXT'):
    result=json.loads((TASK/f'logs/result105-{label}.json').read_bytes())
    if label=='NEXT':
        summary[label]=result
        continue
    click=result['click_at']
    events=json.loads((TASK/f'logs/{label}-observations.json').read_bytes())
    times={e['event']:round((e.get('file_time') or
        (datetime.fromisoformat(e['at']).timestamp() if 'at' in e else e['observed']))-click,4) for e in events}
    last=max(e['observed'] for e in events)+1
    phases=[r for r in all_stages if click<=r['at']<=last]
    summary[label]=dict(result=result,click_offsets=times,functions=phases)
    starts=rows(TASK/f'logs/{label}-first-startup.jsonl')
    summary[label]['moves']=[r for r in starts if r['phase']=='prepared_tree_moved']
    old_pid=next(e['app_pid'] for e in events if e['event']=='ack')
    telemetry=TASK/f'logs/reconstruction-{old_pid}.json'
    if telemetry.exists():summary[label]['reconstruction']=json.loads(telemetry.read_bytes())
(TASK/'logs/metrics105.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
for label,data in summary.items():
    print(label, data.get('click_offsets', {'next_start_seconds':data.get('next_start_seconds')}))
