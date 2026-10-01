"""Opt-in budgeted production preparation and blinded review capability test.

MF_PREP27_REAL=1 MF_PREP27_PHASE=1/2; never repeats a completed stage.
Exam expected labels stay outside model requests. No credentials are saved.
"""
from contextlib import ExitStack
from dataclasses import asdict
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactor.learning.app.classroom import Classroom
from refactor.learning.app.settings import DEFAULTS
from refactor.learning.course import lesson_prep as lp
from refactor.learning.course.planner_agents import AgentFormatError
from refactor.learning.course.store import CourseStore
from tests.refactor.class_eval import class_eval as ce
from tests.refactor.prep_retest.test_prep26_live_pipeline import seed, PLAN
from tests.refactor.prep_cost.prep27_cases import archived_samples, exam

OUT=Path('D:/MentorFlow/.local/test-tmp/prep-cost')


class BudgetStop(RuntimeError):pass


class Gateway:
    def __init__(self,recorded,phase):
        self.recorded=recorded;self.phase=phase;self.price_catalog=recorded.inner.price_catalog
        self.operation='';self.expectation=''
    def get_configuration(self):return self.recorded.inner.get_configuration()
    def complete(self,purpose,messages,**kwargs):
        assert purpose in ('lesson_prep','lesson_review')
        # Actual provider floor including high reasoning is not reduced.
        kwargs['max_tokens']=max(24576,kwargs.get('max_tokens',0))
        context=dict(self.recorded.inner.estimate_context(),cache_mode='implicit',price_period='peak')
        bound=len(json.dumps(dict(system=kwargs.get('system'),messages=messages),ensure_ascii=False).encode('utf-8'))+2048
        config=self.recorded.config
        quote=self.price_catalog.estimate_cost(config['provider_id'],config['model'],input_tokens=(bound,bound),
            output_tokens=(kwargs['max_tokens'],kwargs['max_tokens']),cache_hit_rate=(0,0),context=context)
        spent=sum(Decimal(r.get('peak_cost') or r['reserved']) for r in self.recorded.rows)
        stage=sum(Decimal(r.get('peak_cost') or r['reserved']) for r in self.recorded.rows if r['case']==f'prep27-phase{self.phase}')
        if quote is None or quote.currency!='CNY' or spent+quote.high>Decimal('2') or (self.phase==1 and stage+quote.high>Decimal('.7')):
            raise BudgetStop('下一次调用最大预留无法纳入预算，未发送。')
        self.recorded.case=f'prep27-phase{self.phase}'
        self.recorded.operation=self.operation or purpose
        with (OUT/'report.md').open('a',encoding='utf-8') as f:
            f.write(f'\n付费前：第{self.phase}段 {self.recorded.operation}。预计改善：{self.expectation}。保留高思考、实际24576上限；最大预留¥{quote.high}，本段已用¥{stage}，累计¥{spent}。\n')
        return self.recorded.complete(purpose,messages,**kwargs)


def capability(gateway,samples,phase):
    rows=exam(samples)
    ce.dump(OUT/'exam-fixtures.json',rows)
    excerpts={k:v for s in samples for k,v in s['excerpts'].items()}
    # Blinded: categories, expected findings and human evidence are omitted.
    items=[dict(point=dict(r['context']['point'],id=r['id']),draft=r['draft']) for r in rows]
    system=lp.REVIEW_SYSTEM+'\n本次只检测、不修稿：任何问题用 live 并写明确理由，无问题 approve；revised 一律 null。按顺序输出 {"reviews":[{"point_id":"...","verdict":"approve|live","issues":[],"revised":null},...]}。每份稿独立判断，不因其他稿有错而误拦。'
    gateway.operation='18份盲测核对清单'
    gateway.expectation='事实/数值4份100%拦下，表述8份至少90%，无错6份误拦≤1；检验新增六项清单，不由模型给自己打分'
    response=gateway.complete('lesson_review',[dict(role='user',content='【当前模块原文】\n'+json.dumps(excerpts,ensure_ascii=False)),
        dict(role='user',content='【待核讲稿】\n'+json.dumps(items,ensure_ascii=False))],system=system,max_tokens=24576)
    parsed=lp.parse_batch_review_results(response.text,tuple(r['id'] for r in rows))
    results=[dict(id=r['id'],category=r['category'],evidence=r['evidence'],
        result=asdict(p) if not isinstance(p,AgentFormatError) else dict(error=str(p))) for r,p in zip(rows,parsed)]
    ce.dump(OUT/f'exam-phase{phase}.json',results)
    return results


def run(phase):
    assert phase in (1,2)
    marker=OUT/f'phase{phase}-results.json'
    if marker.exists():raise RuntimeError('该阶段已运行，禁止自动重复付费')
    if phase==2 and '第二段具体改动与预期' not in (OUT/'report.md').read_text(encoding='utf-8'):
        raise RuntimeError('先记录真实证据、离线修改与第二段预期')
    samples=archived_samples()
    OUT.mkdir(parents=True,exist_ok=True);ce.ROOT=OUT
    ce.dump(OUT/'samples.json',samples)
    with ExitStack() as stack:
        recorded=ce.live_gateway(stack,OUT);gateway=Gateway(recorded,phase)
        store=stack.enter_context(CourseStore(f'prep27_{phase}',OUT/f'phase{phase}'))
        seed(store,samples)
        prefs=dict(DEFAULTS)
        services=SimpleNamespace(store=store,gateway=gateway,settings=SimpleNamespace(load=lambda:prefs),
            account_id=store.account_id,data_root=OUT/f'phase{phase}')
        room=Classroom(services,date.today);course=room._course(PLAN);keep=[p['id'] for p in course.points]
        stop=None
        try:
            capability(gateway,samples,phase)
            # Stage 1 finishes the previous unreviewed mathematics pair first.
            modules=[course.modules[2]] if phase==1 else course.modules
            for module in modules:
                gateway.operation='完整写稿与核对 '+module['title']
                gateway.expectation='默认三步+机械前检+两点核对，预计每点≤¥0.08、首核≥80%、人工事实/回应/表述问题0'
                room._prepare_batch(course,course.points_of(module['id']),keep)
        except (BudgetStop,AgentFormatError) as exc:stop=str(exc)
        finally:
            ce.dump(marker,dict(phase=phase,stop=stop,preps=store.load_lesson_cache(PLAN)['lesson_preps'],
                calls=[r for r in recorded.rows if r['case']==f'prep27-phase{phase}']))
        print(json.dumps(dict(phase=phase,stop=stop,spent=str(sum(Decimal(r.get('peak_cost') or r['reserved']) for r in recorded.rows))),ensure_ascii=False))


def test_prep27_live_capability_and_cost():
    if os.environ.get('MF_PREP27_REAL')!='1':pytest.skip('paid opt-in MF_PREP27_REAL=1')
    run(int(os.environ.get('MF_PREP27_PHASE','1')))


if __name__=='__main__':
    if os.environ.get('MF_PREP27_REAL')!='1':raise RuntimeError('paid run requires opt-in')
    run(int(os.environ.get('MF_PREP27_PHASE','1')))
