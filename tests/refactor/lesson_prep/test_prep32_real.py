"""One opt-in paid point, read-only existing preset, persistent peak budget <= CNY .5."""
from contextlib import ExitStack
from datetime import date, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.refactor.class_eval import class_eval as ce
from refactor.learning.connections import gateway as gm
from refactor.learning.app.classroom import Classroom
from refactor.learning.course.extract import extract_html
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore


class BudgetStop(RuntimeError):
    pass


class SmallGateway(ce.RecordedGateway):
    def __getattr__(self, name):
        return getattr(self.inner, name)

    def complete(self, purpose, messages, **kwargs):
        kwargs['max_tokens'] = max(kwargs.get('max_tokens', 4096), gm.PURPOSE_MIN_OUTPUT.get(purpose, 0))
        packet = json.dumps(dict(system=kwargs.get('system'), messages=messages), ensure_ascii=False)
        bound = len(packet.encode('utf-8')) + 2048
        context = dict(self.inner.estimate_context(), cache_mode='implicit', price_period='peak')
        quote = self.inner.price_catalog.estimate_cost(self.config['provider_id'], self.config['model'],
            input_tokens=(bound, bound), output_tokens=(kwargs['max_tokens'], kwargs['max_tokens']),
            cache_hit_rate=(0, 0), context=context)
        with self.lock:
            spent = sum(Decimal(r.get('peak_cost') or r['reserved']) for r in self.rows)
            if quote is None or quote.currency != 'CNY' or spent + quote.high > Decimal('.5'):
                raise BudgetStop('不发起超出¥0.5保守预算的请求。')
            with (self.output/'real-expectations.md').open('a', encoding='utf-8') as out:
                out.write(f'\n调用前：{purpose}，预计缺稿桥调用≤1秒，后台核实后通过事件出第一步；'
                          f'本次最高预留¥{quote.high}，已有¥{spent}。\n')
            # Parent reserves before send, settles real usage at peak, records
            # prompts/results only, and never touches credentials.
            return super().complete(purpose, messages, **kwargs)


@pytest.mark.skipif(os.environ.get('MF32_REAL') != '1', reason='paid admin preset probe opt-in')
def test_prep32_paid_one_point_returns_before_model_and_notifies():
    root = Path(os.environ['MF32_ARTIFACTS'])
    ce.ROOT = root
    source = Path('D:/MentorFlow/.local/test-tmp/class-eval/sources/python-datastructures.html')
    doc = extract_html(source.read_bytes(), ce.SOURCE_URL)
    index = next(i for i, b in enumerate(doc.blocks) if 'list.append' in b.text)
    excerpt = '\n\n'.join(b.text for b in doc.blocks[index:index+2])
    (root/'real-source.txt').write_text(excerpt, encoding='utf-8')
    with ExitStack() as stack:
        stack.enter_context(patch.object(ce, 'RecordedGateway', SmallGateway))
        gateway = ce.live_gateway(stack, root)
        store = CourseStore('prep32_real', root/'real-account')
        stack.callback(store.close)
        plan, day = 'd'*32, date.today()
        store.create_plan(plan, goal='理解列表追加', window=PlanWindow(day, day+timedelta(days=1), 30))
        store.add_sources(plan, [dict(id='s', kind='url', url=ce.SOURCE_URL, title='Python官方教程本地快照',
                                     chunks=[dict(chunk_id='append', text=excerpt)])])
        store.save_outline(plan, [dict(id='s', title='列表')], [dict(id='m', stage_id='s', title='追加元素')])
        store.save_generated_module(plan, 'm', [dict(id='append', title='list.append(x)',
            summary='列表末尾追加一个元素；可追加整个列表作为一个元素。', minutes=15, chunk_ids=['append'])],
            check_minutes=2, today=day)
        store.start_plan(plan, today=day)
        prefs = dict(refresh_time='04:00', teacher_style='warm', detail='brief', examples='few', analogy_domains=['life'])
        services = SimpleNamespace(store=store, gateway=gateway, settings=SimpleNamespace(load=lambda: prefs),
                                   account_id='prep32_real', data_root=root/'real-account')
        room = Classroom(services, lambda: day)
        room._prepare_later = lambda *a: None
        room._schedule_idle_prep = lambda *a: None
        room._prefetch_banks = lambda *a, **kw: None
        event = threading.Event()
        room.lesson_event = lambda data: event.set()
        room.run(plan, room.enter)
        started = time.perf_counter()
        view = room.run(plan, room.next)
        bridge_seconds = time.perf_counter()-started
        assert bridge_seconds < 1 and view['step'] == 'preparing_lesson'
        assert event.wait(280), 'preparation notification timeout'
        while room._lesson_wait_jobs: time.sleep(.01)
        view = room.run(plan, lambda *a: None)
        row = store.load_lesson_cache(plan)['lesson_preps']['append']
        ce.dump(root/'real-result.json', dict(bridge_seconds=bridge_seconds,
            ready_seconds=time.perf_counter()-started, step=view['step'], wait=view['lesson_wait'],
            lesson=view['lesson'], row=row, messages=view['messages']))
        assert view['step'] == 'lesson', view['lesson_wait']
