"""Opt-in six-source real preparation through production durable retry code.

Default skipped. MF_PREP26_REAL=1 and MF_PREP26_PHASE=1/2 select a paid run;
phase two additionally needs a concrete diagnosis/change recorded in report.
Only existing administrator credentials are read; files/usage are isolated.
"""
from contextlib import ExitStack
from datetime import date, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.refactor.class_eval import class_eval as ce
from refactor.learning.app.classroom import Classroom
from refactor.learning.course.store import CourseStore
from refactor.learning.course.ledger import PlanWindow

OUT=Path('D:/MentorFlow/.local/test-tmp/prep-retest')
PLAN='e'*32


class BudgetStop(RuntimeError): pass


class BoundedGateway:
    def __init__(self,recorded,phase):
        self.recorded=recorded;self.phase=phase
        self.price_catalog=recorded.inner.price_catalog
    def get_configuration(self):return self.recorded.inner.get_configuration()
    def complete(self,purpose,messages,**kwargs):
        assert purpose in ('lesson_prep','lesson_review')
        # Gateway currently floors both purposes to 24576. Reserve this
        # actual cap, not the smaller 16384 asked by Classroom.
        kwargs['max_tokens']=max(kwargs.get('max_tokens',0),24576)
        inner=self.recorded.inner;config=self.recorded.config
        bound=len(json.dumps(dict(system=kwargs.get('system'),messages=messages),ensure_ascii=False).encode('utf-8'))+2048
        context=dict(inner.estimate_context(),cache_mode='implicit',price_period='peak')
        quote=self.price_catalog.estimate_cost(config['provider_id'],config['model'],input_tokens=(bound,bound),
            output_tokens=(kwargs['max_tokens'],kwargs['max_tokens']),cache_hit_rate=(0,0),context=context)
        spent=sum(Decimal(r.get('peak_cost') or r['reserved']) for r in self.recorded.rows)
        stage=sum(Decimal(r.get('peak_cost') or r['reserved']) for r in self.recorded.rows if r['case']==f'prep26-phase{self.phase}')
        if quote is None or quote.currency!='CNY' or spent+quote.high>Decimal('1.2') or (self.phase==1 and stage+quote.high>Decimal('.6')):
            raise BudgetStop('预算不能覆盖下一请求，尚未发送；保留已付费稿与核对记录。')
        task=json.loads(messages[-1]['content'].split('\n',1)[1])
        ids=[p['id'] for p in task['points']] if 'points' in task else [task['point']['id']] if 'point' in task else [task['id']]
        self.recorded.case=f'prep26-phase{self.phase}';self.recorded.operation=purpose+' '+','.join(ids)
        with (OUT/'report.md').open('a',encoding='utf-8') as f:
            f.write(f'\n付费前预期（第{self.phase}段）：{self.recorded.operation}。合并版已补引导题明示条件、别名边界与正文引申要求，并启用高思考24576输出和独立核对；预计六点首核通过≥5/6、事实及选项回应错误0、每点高峰写稿+核对≤¥0.08。最大预留¥{quote.high}，本段已用¥{stage}，累计¥{spent}。\n')
        return self.recorded.complete(purpose,messages,**kwargs)


def seed(store,samples):
    day=date.today()
    store.create_plan(PLAN,goal='六点讲稿固定复测',window=PlanWindow(day,day+timedelta(days=2),120))
    modules=[]
    for start in (0,2,4):
        pair=samples[start:start+2];module=f'm{start//2}'
        modules.append(dict(id=module,stage_id='st',title=pair[0]['module']['title']))
        excerpts={k:v for s in pair for k,v in s['excerpts'].items()}
        store.add_sources(PLAN,[dict(id=f's{start}',kind='url',title=pair[0]['cohort'],
            url=pair[0].get('source',{}).get('url','https://example.invalid/archived-authoritative'),
            chunks=[dict(chunk_id=k,text=v) for k,v in excerpts.items()])])
    store.save_outline(PLAN,[dict(id='st',title='跨学科核对')],modules)
    for start in (0,2,4):
        store.save_generated_module(PLAN,f'm{start//2}',[dict(s['point'],minutes=15) for s in samples[start:start+2]],check_minutes=8,today=day)
    store.start_plan(PLAN,today=day)


def run(phase):
    assert phase in (1,2)
    marker=OUT/f'phase{phase}-results.json'
    if marker.exists():raise RuntimeError('本段已经运行，禁止自动重复付费')
    if phase==2 and '第二段具体改动与预期' not in (OUT/'report.md').read_text(encoding='utf-8'):
        raise RuntimeError('第二段需要先记录真实失败证据、具体修正和预期')
    samples=json.loads((OUT/'samples.json').read_text(encoding='utf-8'))
    ce.ROOT=OUT
    with ExitStack() as stack:
        recorded=ce.live_gateway(stack,OUT)
        gateway=BoundedGateway(recorded,phase)
        store=stack.enter_context(CourseStore(f'prep26_phase{phase}',OUT/f'phase{phase}'))
        seed(store,samples)
        services=SimpleNamespace(store=store,gateway=gateway,account_id=store.account_id,data_root=OUT/f'phase{phase}',
            settings=SimpleNamespace(load=lambda:dict(samples[0]['prefs'],refresh_time='04:00')))
        room=Classroom(services,date.today)
        course=room._course(PLAN);points=course.points
        keep=[p['id'] for p in points]
        stop=None
        try:
            for module in course.modules:
                room._prepare_batch(course,course.points_of(module['id']),keep)
        except BudgetStop as exc:stop=str(exc)
        finally:
            ce.dump(marker,dict(phase=phase,stop=stop,preps=store.load_lesson_cache(PLAN)['lesson_preps'],
                calls=[r for r in recorded.rows if r['case']==f'prep26-phase{phase}']))
        print(json.dumps(dict(phase=phase,stop=stop,states={pid:r['status'] for pid,r in store.load_lesson_cache(PLAN)['lesson_preps'].items()},
            spent=str(sum(Decimal(r.get('peak_cost') or r['reserved']) for r in recorded.rows))),ensure_ascii=False))


def test_prep26_live_six_points():
    if os.environ.get('MF_PREP26_REAL')!='1':pytest.skip('paid probes opt-in MF_PREP26_REAL=1')
    run(int(os.environ.get('MF_PREP26_PHASE','1')))


if __name__=='__main__':
    if os.environ.get('MF_PREP26_REAL')!='1':raise RuntimeError('real run is opt-in')
    run(int(os.environ.get('MF_PREP26_PHASE','1')))
