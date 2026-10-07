from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course import source_quality
from refactor.learning.course.store import CourseStore
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.mastery import Judgement, AttemptClosed, MasteryBook
from tests.refactor.integration38.support import bank_question


def test_detail81_question_projection_and_source_classification_once(tmp_path, monkeypatch):
    today = date(2026, 10, 8)
    with CourseStore('detail81', tmp_path) as store:
        store.create_plan('p', goal='概念', window=PlanWindow(today, today+timedelta(days=5), 100))
        store.add_sources('p', [dict(id='user', title='用户资料', url='https://example.invalid/user', adopted=True, chunks=[dict(id='c', text='正文内容')])])
        store.save_outline('p',[dict(id='s',title='阶段')],[dict(id='m',stage_id='s',title='有题模块'),dict(id='empty',stage_id='s',title='无题模块')])
        store.save_generated_module('p','m',[dict(id=f'p{i}',title=f'点{i}',summary='能理解',minutes=8,chunk_ids=['c']) for i in range(3)],check_minutes=2,today=today)
        store.save_generated_module('p','empty',[dict(id='noq',title='只学',summary='理解',minutes=8,chunk_ids=['c'])],check_minutes=0,today=today)
        for i in range(3):
            q=bank_question(f'p{i}','m',kind='fill',answer='依据正文',chunk='c')
            q['id']=f'p{i}'
            store.add_bank_questions('p','m',[q])
        store.start_plan('p',today=today)
        now=datetime(2026,10,8,tzinfo=timezone.utc)
        for i in range(3):
            store.append_judgement('p',Judgement(f'j{i}',i,now,'module_test','m','a',((f'p{i}',True),),question_id=f'p{i}'))
        store.append_attempt_closed('p',AttemptClosed('closed',4,'m','a','module_test'))
        for i,q in enumerate(store.records('question_bank','p')):
            assert q['module_id']=='m'
        def forbidden(*a,**k): raise AssertionError('详情不应逐点计算掌握等级或重算用户资料指纹')
        monkeypatch.setattr(MasteryBook,'point',forbidden)
        monkeypatch.setattr(source_quality,'assess_stored',forbidden)
        calls=[]
        flow=PlanFlow(SimpleNamespace(store=store),lambda *a:None,lambda:today)
        flow._lists=lambda:calls.append(1) or {}
        detail=flow.detail('p')
        modules=detail['stages'][0]['modules']
        assert [(m['question_count'],m['passed_questions']) for m in modules]==[(3,0),(0,0)]
        assert modules[0]['state']=='passed'
        assert len(calls)==1
        assert all('level' not in p and 'state' not in p for m in modules for p in m['points'])
        assert not any(p['source_general'] for m in modules for p in m['points'])
        # Non-adopted stored sources retain the old label, but are classified once.
        original=store.records
        def records(entity,plan_id=''):
            rows=original(entity,plan_id)
            return [dict(r,adopted=False) for r in rows] if entity=='sources' else rows
        monkeypatch.setattr(store,'records',records)
        classifications=[]
        monkeypatch.setattr(source_quality,'assess_stored',lambda *a,**k: classifications.append(a[0]['id']) or {'credibility':3})
        detail=flow.detail('p')
        assert classifications==['user']
        assert all(p['source_general'] for m in detail['stages'][0]['modules'] for p in m['points'])
