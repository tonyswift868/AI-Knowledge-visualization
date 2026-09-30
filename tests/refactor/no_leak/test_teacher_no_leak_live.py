"""Opt-in small DeepSeek probes; one persistent CNY 1 ceiling, phase 1 <= .5."""
from contextlib import ExitStack
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from tests.refactor.class_eval import class_eval as ce  # noqa: E402
from refactor.learning.app.classroom import Classroom  # noqa: E402
from refactor.learning.course import lesson_prep, teacher_agent  # noqa: E402
from refactor.learning.course.store import CourseStore  # noqa: E402
from refactor.learning.course.ledger import PlanWindow  # noqa: E402
from refactor.learning.connections.errors import ModelError  # noqa: E402

OUT = Path(r"D:\MentorFlow\.local\test-tmp\no-leak")
ARCHIVED = Path(r"D:\MentorFlow\.local\test-tmp\acceptance\calls\call-0011.json")
PLAN = 'd' * 32
DAY = date(2026, 10, 5)


class Limited:
    def __init__(self, inner, phase):
        self.inner, self.cap = inner, Decimal('.5' if phase == 1 else '1')

    def complete(self, purpose, messages, **kw):
        config = self.inner.config
        packet = json.dumps(dict(system=kw.get('system'), messages=messages), ensure_ascii=False)
        bound = len(packet.encode('utf-8')) + 2048
        context = dict(self.inner.inner.estimate_context(), cache_mode='implicit', price_period='peak')
        estimate = self.inner.inner.price_catalog.estimate_cost(config['provider_id'], config['model'],
            input_tokens=(bound, bound), output_tokens=(kw.get('max_tokens', 4096),) * 2,
            cache_hit_rate=(0, 0), context=context)
        spent = sum(Decimal(row.get('peak_cost') or row['reserved']) for row in self.inner.rows)
        if estimate is None or spent + estimate.high > self.cap:
            raise ModelError('EVAL_BUDGET_STOP', '本段预算不容许下一次调用；未发起请求。')
        return self.inner.complete(purpose, messages, **kw)


def source():
    call = json.loads(ARCHIVED.read_text(encoding='utf-8'))
    content = call['messages'][0]['content']
    return json.JSONDecoder().raw_decode(content.split('【资料原文（数据）】\n', 1)[1])[0]


def run(phase):
    OUT.mkdir(parents=True, exist_ok=True)
    ce.ROOT = OUT
    with ExitStack() as stack:
        recorded = ce.live_gateway(stack, OUT)
        gateway = Limited(recorded, phase)
        if phase == 1:
            if (OUT / 'probes.json').exists():
                raise RuntimeError('已跑过第一段；不重复付费')
            store = CourseStore('no_leak_probe', OUT / 'data')
            stack.callback(store.close)
            store.create_plan(PLAN, goal='学列表方法', final_goal='区别列表按值删除', level='beginner',
                              window=PlanWindow(DAY, DAY + timedelta(days=1), 60))
            excerpts = source()
            chunks = [{'chunk_id': k, 'text': v} for k, v in excerpts.items()]
            store.add_sources(PLAN, [{'id': 'official', 'kind': 'url', 'url': 'https://docs.python.org/3/tutorial/datastructures.html', 'chunks': chunks}])
            chunk = next(k for k, v in excerpts.items() if 'list.remove' in v)
            store.save_outline(PLAN, [{'id': 'stage', 'title': '列表'}], [{'id': 'm1', 'stage_id': 'stage', 'title': '按值删除'}])
            store.save_generated_module(PLAN, 'm1', [{'id': 'p1', 'title': 'remove', 'summary': '删除第一个等值元素；找不到抛 ValueError。', 'minutes': 15, 'chunk_ids': [chunk]}], check_minutes=5, today=DAY)
            store.start_plan(PLAN, today=DAY)
            settings = SimpleNamespace(load=lambda: {'teacher_style': 'warm', 'detail': 'brief', 'examples': 'few', 'analogy_domains': ['life']})
            services = SimpleNamespace(store=store, gateway=gateway, account_id='no_leak_probe', data_root=OUT / 'data', settings=settings)
            room = Classroom(services, lambda: DAY)
            course = room._course(PLAN)
            question = {'type': 'short', 'stem': "列表里有两个 'apple'，fruits.remove('apple') 会删掉几个？", 'reference': '只删除第一个匹配的 apple，后面那个仍保留；成功时返回 None。'}
            rows = []
            for user, director in [('午饭吃什么？', '学习者闲聊，简短回应后带回原题。'),
                                   ('remove 是不是删掉两个？能解释一下吗？', '学习者追问，解释题意后请他先作答。'),
                                   ('没听懂，能把这个方法重新讲一遍吗？', '学习者没听懂，只解释题目在问什么。')]:
                state = room._load(course)
                state.update(module_id='m1', step='answer', pending={'kind': 'casual', 'answer_id': 'probe-answer', 'point_ids': ['p1'], 'asks': 1, 'question': question})
                recorded.operation = user
                emitted = []
                teaching = room._teacher(course, state, SimpleNamespace(cancel=threading.Event(), stream=emitted.append), director, ['p1'], user=user)
                rows.append({'user': user, 'speech': teaching.speech, 'ui': emitted,
                             'guard_match': teacher_agent.answer_leak(teaching.speech, question)})
            (OUT / 'probes.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        else:
            if (OUT / 'new-preps.json').exists():
                raise RuntimeError('已跑过备课探针；不重复付费')
            excerpts = source()
            contexts = [{'module': {'id': 'm1', 'title': '删除'}, 'point': {'id': pid, 'title': title, 'summary': summary}, 'excerpts': excerpts}
                        for pid, title, summary in [('remove', 'remove 按值删除', '删除第一个等值元素；无匹配时抛 ValueError。'),
                                                    ('pop', 'pop 按位置删除', '删除并返回指定位置元素，默认末尾；空列表或越界抛 IndexError。')]]
            request = lesson_prep.batch_prep_request(contexts)
            result = gateway.complete('lesson_prep', request[1], system=request[0], max_tokens=16384)
            drafts = lesson_prep.parse_batch_preps(result.text, ('remove', 'pop'))
            request = lesson_prep.batch_review_request(contexts, drafts)
            result = gateway.complete('lesson_review', request[1], system=request[0], max_tokens=16384)
            reviews = lesson_prep.parse_batch_reviews(result.text, ('remove', 'pop'))
            (OUT / 'new-preps.json').write_text(json.dumps([{'draft': asdict(d), 'local_issues': lesson_prep.check_lesson(d), 'review': asdict(r)} for d, r in zip(drafts, reviews)], ensure_ascii=False, indent=2), encoding='utf-8')


def test_small_real_teacher_no_leak_probes():
    if os.environ.get('MF_NO_LEAK_REAL') != '1':
        pytest.skip('real model probes are opt-in')
    run(int(os.environ.get('MF_NO_LEAK_PHASE', '1')))


if __name__ == '__main__':
    if os.environ.get('MF_NO_LEAK_REAL') != '1':
        raise RuntimeError('requires MF_NO_LEAK_REAL=1')
    run(int(os.environ.get('MF_NO_LEAK_PHASE', '1')))
