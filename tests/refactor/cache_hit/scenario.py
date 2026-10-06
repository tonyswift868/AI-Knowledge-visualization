"""Offline requests from the integrated original-material classroom, not model-quality evidence."""
from datetime import datetime,timezone
import hashlib,json
from refactor.learning.app.classroom import Classroom
from refactor.learning.connections.gateway import PURPOSE_EFFORT
from tests.refactor.integration38.support import context,join_workers
from tests.refactor.integration38.test_offline_integrated38_flow import build_plan,DAY,BODY

def run_scenario(root,*,extended=False):
    services,flow,model,pid=build_plan(root,[])
    room=Classroom(services,lambda:DAY,now=lambda:datetime(2026,10,3,10,tzinfo=timezone.utc))
    try:
        def act(name,*args):
            pending=room._file(pid).read({}).get('pending') or {}
            q=pending.get('question') or {}
            if name=='reply' and q:
                if q['kind']=='recall':
                    room.perform(pid,'recall_reveal',context(),(q['id'],'学生自己的回答'))
                    name,args='recall_submit',(q['id'],list(range(len(q['key_points']))))
                else:
                    from refactor.learning.course.question_bank import grading_question
                    name,args='answer',(grading_question(q)['answer'],)
            v=room.perform(pid,name,context(),args)
            assert not v['failure'],v['failure']
            return v
        v=act('enter')
        for _ in range(60):
            step=v['step']
            if step=='completed':break
            if step in ('teach','teaching','closing'):v=act('next')
            elif step=='opening':v=act('choose',0)
            elif step in ('answer','testing','retesting','checking') and v['question']:v=act('reply','先进先出')
            elif step=='test_offer':v=act('start_test','module_test')
            elif step=='test_done':v=act('next_module')
            elif step in ('check_offer','checking'):v=act('start_check')
            else:raise AssertionError(step)
        assert v['step']=='completed'
        start=len(model.requests) if extended else None
        if extended:
            act('start_practice',None,None,2);act('reply','先进先出');v=act('reply','先进先出')
            assert v['step']=='completed'
        act('leave');join_workers()
        rows=[dict(row,index=i,model='offline-scripted',options=dict(reasoning_effort=PURPOSE_EFFORT.get(row['purpose'],'high')))
              for i,row in enumerate(model.requests)]
        return dict(fixture_sha256=hashlib.sha256(BODY).hexdigest(),seed=0,date=DAY.isoformat(),status='completed',requests=rows,
            practice_start=start,practice_reply_lost_pending=False)
    finally:
        room.cancel_materials();join_workers();services._closed=True;services.store.close()
