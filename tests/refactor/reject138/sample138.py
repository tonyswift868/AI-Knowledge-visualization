"""Explicitly invoked paid sampling only; no live model is used by pytest."""
import argparse,copy,json,shutil,sys
from pathlib import Path
from types import SimpleNamespace
REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))
from refactor.learning.app.classroom import Classroom
from refactor.learning.app.services import AppServices
from refactor.learning.course import teacher_agent as agent
from refactor.learning.course.planner_agents import AgentFormatError
from tests.refactor.simulation.run import live_factory
from tests.refactor.simulation.engine import Evidence,RecordedGateway

ACCOUNT='mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932'
PLAN='029b076315034c00bbd6842a5c9c31d9'

def rejection(text,ctx):
    try:
        teaching=agent.parse_teaching(text,ctx);agent.validate_presentation(teaching.speech,ctx)
        return None
    except AgentFormatError as exc:
        reason=getattr(exc,'reason',None)
        if reason:return reason
        message=str(exc)
        return ('HIGHLIGHT_COUNT_OR_QUESTION' if message.startswith('presentation: 用') else
                'TOO_LONG' if message.startswith('presentation: 普通') else
                'ABSOLUTE_WORD' if message.startswith('concept:') else
                'WRAPUP' if message.startswith('concept_wrap:') else
                'REPEATED_ENDING' if message.startswith('concept_ending:') else
                'REPEATED_OPENING' if message.startswith('concept_opening:') else 'PARSE_FAILED')

def prepare(root):
    path=root/'contexts138.json'
    if path.exists():return json.loads(path.read_text(encoding='utf-8'))
    data=root/'context-data';target=data/'learning/accounts'/ACCOUNT/'course.sqlite3'
    target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/'copy.sqlite3',target)
    services=AppServices(account_id=ACCOUNT,data_root=data,session_provider=lambda:None,api_base_url='https://unused.invalid',ca_bundle=None)
    room=Classroom(services,lambda:None)
    try:
        course=room._course(PLAN);saved=services.store.classroom_position(PLAN)['state']
        state=room._unpack_position(course,copy.deepcopy(saved))
        contexts=[]
        for action in ('next','confused'):
            ctx=room._predicted_teacher(course,state,action,())
            if not ctx:raise RuntimeError('Current position has no predicted teacher request')
            ctx.pop('request_page',None)
            contexts.append(dict(action=action,context=ctx))
        cases=[dict(id=f'{i+1:02d}',**copy.deepcopy(contexts[i%2])) for i in range(20)]
        path.write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf-8')
        return cases
    finally:room.cancel_materials();services.close()

def sample(root,phase):
    cases=prepare(root);evidence=Evidence(root/'live');services=SimpleNamespace(data_root=root/'live-data')
    real=live_factory(Path('D:/MentorFlowData/account-shell'),ACCOUNT)(services)
    gateway=RecordedGateway(real,evidence,budget='3',allowed={'teacher'})
    path=root/f'{phase}138.json';rows=[]
    try:
        for case in cases:
            system,messages=agent.teacher_request(case['context'])
            result=gateway.complete('teacher',messages,system=system,plan_id=PLAN,max_tokens=4096)
            reason=rejection(result.text,case['context'])
            rows.append(dict(id=case['id'],action=case['action'],text=result.text,
                reason=reason,metrics=agent.reply_metrics(result.text),call=evidence.calls[-1]))
            path.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(dict(phase=phase,id=case['id'],reason=reason,characters=rows[-1]['metrics']['characters'],spent=str(gateway.spent)),ensure_ascii=False),flush=True)
    finally:gateway.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('phase',choices=['prepare','before','after'])
    args=p.parse_args()
    if args.phase=='prepare':print('contexts',len(prepare(args.root)))
    else:sample(args.root,args.phase)
