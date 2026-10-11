"""Summarize preserved real observations, not synthesized timing estimates."""
import json
from common import TASK

def load(path):
    return json.loads(path.read_bytes())

def main():
    observations = load(TASK/'logs/scenario-observations.json')
    clicks = [json.loads(x) for x in (TASK/'logs/scenarios.jsonl').read_text(encoding='utf8').splitlines()]
    click = next(x['at'] for x in reversed(clicks) if x['action']=='update-click-begin')
    events = {x['event']: x for x in observations}
    ack = events['ack']
    timings = {name: round(row['observed']-click,3) for name,row in events.items()}
    stages = [json.loads(x) for x in (TASK/'logs/stages.jsonl').read_text(encoding='utf8').splitlines()]
    stages = [x for x in stages if x['at']>=click and x['pid'] in (ack['app_pid'],ack['coordinator_pid'],events['new_window_input_ready']['pid'])]
    result = {'click_to_event_seconds': timings,
              'ack_to_app_exit_seconds': round(events['old_app_exit']['observed']-ack['file_time'],3),
              'stages': stages,
              'reconstruction': load(TASK/'logs'/('reconstruction-'+str(ack['app_pid'])+'.json'))}
    (TASK/'logs/metrics104.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'click_to_event_seconds':timings,'ack_to_app_exit_seconds':result['ack_to_app_exit_seconds'],
        'calls': [{'pid':x['pid'],'stage':x['stage'],'seconds':x['seconds']} for x in stages]},ensure_ascii=False))

if __name__=='__main__': main()
