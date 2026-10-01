"""Six archived-source probes, opt-in; persistent phase-one peak CNY .5 cap.

No configuration/credential writes. Full prompts use synthetic course IDs;
the administrator key remains exclusively inside the existing gateway.
"""
from contextlib import ExitStack
from dataclasses import asdict
from decimal import Decimal
import json
import os
from pathlib import Path
import time

import pytest

from tests.refactor.class_eval import class_eval as ce
from refactor.learning.course import lesson_prep
from refactor.learning.connections.errors import ModelError
from refactor.learning.course.planner_agents import AgentFormatError

OUT = Path(r'D:\MentorFlow\.local\test-tmp\guided-lesson')


class PhaseOne:
    def __init__(self, recorded): self.recorded = recorded

    def complete(self, purpose, request, *, operation):
        inner = self.recorded.inner
        config = self.recorded.config
        bound = len(json.dumps(dict(system=request[0], messages=request[1]), ensure_ascii=False).encode('utf-8')) + 2048
        context = dict(inner.estimate_context(), cache_mode='implicit', price_period='peak')
        quote = inner.price_catalog.estimate_cost(config['provider_id'], config['model'],
            input_tokens=(bound, bound), output_tokens=(16384, 16384), cache_hit_rate=(0, 0), context=context)
        spent = sum(Decimal(r.get('peak_cost') or r['reserved']) for r in self.recorded.rows)
        if quote is None or quote.currency != 'CNY' or spent + quote.high > Decimal('.5'):
            raise ModelError('EVAL_BUDGET_STOP', '第一段预算不足以覆盖下一请求；没有发送。')
        # Record expected improvement before EVERY paid request, including reviews.
        with (OUT / 'report.md').open('a', encoding='utf-8') as report:
            report.write(f'\n付费前预期：{operation}；分步格式和允许直接确认讲过的内容，预计一次核对通过率由约40%升至至少80%，单点写稿+核对高峰价≤¥0.08。此次最大预留¥{quote.high}，已用¥{spent}。\n')
        self.recorded.case = 'guided-six-fixed-points'
        self.recorded.operation = operation
        return self.recorded.complete(purpose, request[1], system=request[0], max_tokens=16384)


def run():
    if (OUT / 'real-results.json').exists() or (OUT / 'budget.json').exists():
        raise RuntimeError('本轮已运行过，不自动重复付费')
    samples = json.loads((OUT / 'samples.json').read_text(encoding='utf-8'))
    assert len(samples)==6 and [s['cohort'] for s in samples].count('acceptance')==3
    ce.ROOT = OUT
    rows = []; stop = None
    with ExitStack() as stack:
        recorded = ce.live_gateway(stack, OUT)
        gateway = PhaseOne(recorded)
        for begin in (0, 2, 3, 5):
            contexts = samples[begin:begin+(2 if begin in (0,3) else 1)]
            ids = tuple(c['point']['id'] for c in contexts)
            started = time.perf_counter()
            try:
                request = lesson_prep.batch_prep_request(contexts) if len(contexts)==2 else lesson_prep.prep_request(contexts[0])
                response = gateway.complete('lesson_prep', request, operation='写稿 '+','.join(ids))
                generation_call = recorded.rows[-1]['id']
                drafts = lesson_prep.parse_batch_preps(response.text, ids) if len(contexts)==2 else (lesson_prep.parse_prep(response.text, chunk_ids=contexts[0]['point']['chunk_ids']),)
            except (ModelError, AgentFormatError) as exc:
                rows.extend(dict(point_id=pid, stage='generation_failed', error=str(exc)) for pid in ids)
                stop = str(exc)
                break
            generation_seconds = time.perf_counter()-started
            for context, draft in zip(contexts, drafts):
                row = dict(point_id=context['point']['id'], title=context['point']['title'], cohort=context['cohort'],
                    draft=asdict(draft), generation_call=generation_call, writer_share=len(contexts),
                    generation_seconds=generation_seconds, local_issues=lesson_prep.check_lesson(draft,chunk_ids=context['point']['chunk_ids']))
                rows.append(row)
                try:
                    response = gateway.complete('lesson_review', lesson_prep.review_request(context,draft), operation='独立核对 '+row['point_id'])
                    row['review_call']=recorded.rows[-1]['id']
                    row['review']=asdict(lesson_prep.parse_review(response.text))
                    row['stage']='reviewed'
                except (ModelError, AgentFormatError) as exc:
                    row.update(stage='review_failed',error=str(exc));stop=str(exc)
                    break
            ce.dump(OUT / 'real-results.json',dict(rows=rows,stop=stop))
            if stop:break
        ce.dump(OUT / 'real-results.json',dict(rows=rows,stop=stop))


def test_six_real_guided_lessons():
    if os.environ.get('MF_GUIDED_REAL')!='1':pytest.skip('real probes require MF_GUIDED_REAL=1')
    run()


if __name__=='__main__':
    if os.environ.get('MF_GUIDED_REAL')!='1':raise RuntimeError('requires MF_GUIDED_REAL=1')
    run()
