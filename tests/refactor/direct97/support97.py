"""Small real-store/classroom fixture; only teacher prose and preparation are fake."""
from datetime import date,datetime,timedelta,timezone
import json,random
from types import SimpleNamespace

from refactor.learning.app.classroom import Classroom
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore
from tests.refactor.integration38.support import Agents,bank_question,block,context

PLAN='d'*32
ACCOUNT='direct97-offline'
DAY=date(2026,10,10)
SETTINGS=dict(teacher_style='warm',detail='brief',examples='few',analogy_domains=['life'],refresh_time='04:00')

class Teacher(Agents):
    def complete(self,purpose,messages,**kw):
        if purpose=='teacher':
            task=block([m for m in reversed(messages) if m['content'].startswith('【本步任务】')][:1],'【本步任务】')
            if '这个模块没有检测题' in task['director']:
                self.calls.append(purpose);self.requests.append(dict(purpose=purpose,messages=messages,system=kw.get('system')))
                return SimpleNamespace(text='你觉得'+task['current_points'][0]['title']+'里的先后顺序是什么意思？')
        return super().complete(purpose,messages,**kw)

def make(directory,*,questions=10,points=4):
    store=CourseStore(ACCOUNT,directory)
    store.create_plan(PLAN,goal='队列与栈',final_goal='理解处理顺序',level='beginner',window=PlanWindow(DAY,DAY+timedelta(days=7),500))
    store.add_sources(PLAN,[dict(id='s',url='https://docs.python.org/3/tutorial/datastructures.html',title='队列与栈原文',adopted=True,
        chunks=[dict(chunk_id='k1',text='队列采用先进先出的顺序，先加入的先取出。'),dict(chunk_id='k2',text='栈采用后进先出的顺序。')])])
    store.save_outline(PLAN,[dict(id='st',title='处理顺序')],[dict(id='m0',stage_id='st',title='理解队列'),dict(id='m1',stage_id='st',title='理解栈')])
    store.save_generated_module(PLAN,'m0',[dict(id=f'p{i}',title=f'队列概念{i+1}',summary='先进先出的处理顺序',minutes=10,chunk_ids=['k1']) for i in range(points)],
        check_minutes=questions*2,final_check_minutes=questions*2,check_question_minutes=2,today=DAY)
    store.save_generated_module(PLAN,'m1',[dict(id='other',title='栈',summary='后进先出',minutes=10,chunk_ids=['k2'])],
        check_minutes=0,final_check_minutes=0,check_question_minutes=2,today=DAY)
    if questions:
        from refactor.learning.course.point_materials import _selection_passages
        ref=next(r for r in _selection_passages(store,PLAN) if r['id']=='k1')['source']
        stems=['队列取出顺序遵循____。','队列的先加入元素应当____取出。','队列使用____原则。','排队的数据顺序称作____。','队列顺序缩写所指的是____。',
            '队列先放入的元素按____规则处理。','从队列头取出体现____。','在队列尾加入再从头取出是____。','队列不是后进先出而是____。','队列的原文关键词为____。']
        rows=[]
        for i in range(questions):
            q=bank_question(f'p{i%points}','m0',index=i,kind='fill')
            q.update(stem=stems[i%len(stems)],answer=[['先进先出']],source=ref,answer_source=ref)
            rows.append(q)
        store.add_bank_questions(PLAN,'m0',rows)
        assert len(store.records('question_bank',PLAN))==questions
    for mid in ('m0','m1'):store.save_module_preparation(PLAN,mid,dict(state='done'))
    store.start_plan(PLAN,today=DAY)
    teacher=Teacher()
    services=SimpleNamespace(store=store,gateway=teacher,account_id=ACCOUNT,data_root=directory,settings=SimpleNamespace(load=lambda:dict(SETTINGS)))
    e=SimpleNamespace(store=store,teacher=teacher,services=services)
    e.room=fresh(e)
    return e

def fresh(e):
    room=Classroom(e.services,lambda:DAY,now=lambda:datetime(2026,10,10,10,tzinfo=timezone.utc),rng=random.Random(97))
    # This task exercises prepared modules only; do not start unrelated workers.
    room._prepare_next_module=lambda *a,**kw:None
    return room

def act(e,action,*args,request_id=None):
    result=e.room.perform(PLAN,action,context(),args,request_id=request_id)
    assert not result.get('failure'),result.get('failure')
    return result

def position(e,count=0,step='teaching'):
    co=e.room._course(PLAN);state=e.room._rebuild(co)
    for i in range(count):e.store.add_credit(PLAN,f'learn:p{i}',f'p{i}',10,DAY,'tentative')
    e.store.save_teacher_profile(PLAN,dict(teaching_points={f'p{i}':dict(point_id=f'p{i}',title=f'队列概念{i+1}') for i in range(count)}))
    state.update(index=count,step=step,day=DAY.isoformat(),in_session=True,conversation=dict(point_id=f'p{count}',options=[],interrupted=True))
    e.room._save(e.room._course(PLAN),state)
    return state

def finish_review(e):
    view=None
    while True:
        view=act(e,'reply','我觉得是按加入先后处理。')
        assert view['direct_review']['answered'] and view['direct_review']['sources']
        if view['step']=='direct_choice':return view
        view=act(e,'direct_next')

def close(e):
    e.room.cancel_materials();e.store.close()
