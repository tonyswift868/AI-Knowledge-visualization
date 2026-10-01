"""Continue the same second-stage durable rows, then fixed-input cost controls.

No new cohort, counter reset or third stage. MF_PREP27_CONTINUE_STAGE2=1.
"""
from contextlib import ExitStack
from dataclasses import asdict
from datetime import date
import json
import os
from types import SimpleNamespace

import pytest

from refactor.learning.app.classroom import Classroom
from refactor.learning.app.settings import DEFAULTS
from refactor.learning.course import lesson_prep as lp
from refactor.learning.course.store import CourseStore
from tests.refactor.class_eval import class_eval as ce
from tests.refactor.prep_cost.test_prep27_real_cost import OUT, Gateway, BudgetStop, PLAN


def run():
    marker=OUT/'stage2-controls.json'
    if marker.exists():raise RuntimeError('第二段续做已运行，不重复付费')
    if not (OUT/'phase2-results.json').exists():raise RuntimeError('先完成第二段原运行')
    ce.ROOT=OUT
    controls=[];stop=None
    with ExitStack() as stack:
        recorded=ce.live_gateway(stack,OUT);gateway=Gateway(recorded,2)
        store=stack.enter_context(CourseStore('prep27_2',OUT/'phase2'))
        services=SimpleNamespace(store=store,gateway=gateway,account_id=store.account_id,data_root=OUT/'phase2',
            settings=SimpleNamespace(load=lambda:dict(DEFAULTS)))
        room=Classroom(services,date.today);course=room._course(PLAN);keep=[p['id'] for p in course.points]
        try:
            # Resume only pending manuscripts; already-approved siblings are
            # never submitted again by the actual production retry path.
            for module in course.modules:
                pending=[p for p in course.points_of(module['id']) if room._prep_row(course,p)['status'] not in ('reviewed','needs_live_teaching')]
                if pending:
                    gateway.operation='第二段原操作续核 '+','.join(p['id'] for p in pending)
                    gateway.expectation='保留原稿与已用1次核对计数，正常重开后只用剩余核对；修复参考超范围、独立初值与引申问题，不丢另点已批准稿'
                    room._prepare_batch(course,pending,keep)
            # Isolate batching only: exactly the two history manuscripts and
            # same checklist, source and point contexts as paid batch call 8.
            history=course.points_of(course.modules[0]['id'])
            for point in history:
                draft=lp.parse_prep(json.dumps(room._prep_row(course,point)['lesson'],ensure_ascii=False))
                gateway.operation='合批消融：同一历史稿单点核对 '+point['id']
                gateway.expectation='只把已测两点核对拆为两次，高思考与清单、原文、稿件相同；预期合批总价低于单点总价至少20%，人工分别核对决定不能直接用作课堂状态'
                request=lp.review_request(room._prep_context(course,point),draft)
                result=gateway.complete('lesson_review',request[1],system=request[0],max_tokens=24576)
                controls.append(dict(point_id=point['id'],review=asdict(lp.parse_review(result.text))))
        except BudgetStop as exc:stop=str(exc)
        finally:ce.dump(marker,dict(stop=stop,preps=store.load_lesson_cache(PLAN)['lesson_preps'],controls=controls,
            calls=[r for r in recorded.rows if '续核' in r['operation'] or '消融' in r['operation']]))
    print(json.dumps(dict(stop=stop,controls=len(controls)),ensure_ascii=False))


def test_prep27_paid_stage2_controls():
    if os.environ.get('MF_PREP27_CONTINUE_STAGE2')!='1':pytest.skip('paid second-stage continuation opt-in')
    run()


if __name__=='__main__':
    if os.environ.get('MF_PREP27_CONTINUE_STAGE2')!='1':raise RuntimeError('requires opt-in')
    run()
