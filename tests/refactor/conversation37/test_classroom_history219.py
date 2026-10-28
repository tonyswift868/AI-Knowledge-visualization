"""Real host/store regressions for BUG-149/151; no model or user data."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from refactor.learning.course.request_layout import dialogue_tail
from tests.refactor.conversation37.test_grounded_conversation37 import (
    PLAN, act, env, pending,
)


def test_pending_ask219_explains_default_ticks_and_student_self_verdict(env, monkeypatch):
    pending(env)
    captured = []
    complete = env.gateway.complete

    def capture(purpose, messages, **kwargs):
        captured.extend(m['content'] for m in messages if m['content'].startswith('【本步任务】'))
        return complete(purpose, messages, **kwargs)

    monkeypatch.setattr(env.gateway, 'complete', capture)
    act(env, 'ask', '问答题的要点应该怎么勾，谁来决定对错？')
    task = '\n'.join(captured)
    assert '默认全部勾上' in task
    assert '没答到的点成叉' in task
    assert '算我答对' in task and '算我答错' in task
    assert '与勾选数量无关' in task
    assert '全部勾到才算对' not in task
    assert not env.store.records('judgements', PLAN)
    assert not env.store.records('question_answers', PLAN)


def seed_history(env, *, current=1, other=2000):
    at = datetime(2026, 10, 3, tzinfo=timezone.utc)
    with env.store.transaction():
        for index in range(other):
            env.store.append_message(PLAN, 'm1', 'assistant', '其他模块的历史',
                                     message_id=f'other-{index:04}', at=at)
        for index in range(current):
            env.store.append_message(PLAN, 'm0', 'assistant', f'本模块历史{index}',
                                     message_id=f'current-{index:04}', at=at+timedelta(seconds=index))
    course = env.room._course(PLAN)
    state = env.room._load(course)
    return course, state


def track_decoded_messages(monkeypatch):
    decoded = []
    loads = json.loads

    def count(value, *args, **kwargs):
        result = loads(value, *args, **kwargs)
        if isinstance(result, dict) and {'role', 'content', 'at', 'compressed'} <= result.keys():
            decoded.append(result['id'])
        return result

    monkeypatch.setattr(json, 'loads', count)
    return decoded


def test_say219_reads_only_latest_message_and_preserves_idempotence(env, monkeypatch):
    course, state = seed_history(env)
    decoded = track_decoded_messages(monkeypatch)
    env.room._say(course, state, '新消息', message_id='stable-219')
    assert not any(identifier.startswith('other-') for identifier in decoded)
    assert len(decoded) <= 3, decoded[:10]
    revisions = [(r.entity, r.entity_id, r.local_revision) for r in env.store.pending_records(5000)]
    decoded.clear()
    assert env.room._say(course, state, '不应覆盖已有消息', message_id='stable-219') == 'stable-219'
    assert decoded == ['stable-219']
    assert revisions == [(r.entity, r.entity_id, r.local_revision) for r in env.store.pending_records(5000)]


def test_dialogue219_filters_other_modules_without_changing_page_boundaries(env, monkeypatch):
    course, state = seed_history(env, current=83)
    expected = dialogue_tail([{'role': 'assistant', 'text': f'本模块历史{i}'} for i in range(83)],
                             max_rows=40, max_chars=60000)
    decoded = track_decoded_messages(monkeypatch)
    assert env.room._dialogue(course, state) == expected
    assert len(decoded) == 83
    assert not any(identifier.startswith('other-') for identifier in decoded)


def test_say219_keeps_order_when_clock_moves_back(env):
    course, state = seed_history(env, current=2, other=0)
    previous = env.store.messages(PLAN, 'm0')[-1]
    env.room.now = lambda: datetime(2000, 1, 1, tzinfo=timezone.utc)
    env.room._say(course, state, '墙钟回拨后仍追加', message_id='later-219')
    rows = env.store.messages(PLAN, 'm0')
    assert rows[-1]['id'] == 'later-219'
    assert datetime.fromisoformat(rows[-1]['at']) == datetime.fromisoformat(previous['at'])+timedelta(microseconds=1)


def test_message_query219_retains_null_channel_compression_and_tie_order(env):
    at = datetime(2026, 10, 3, tzinfo=timezone.utc)
    for identifier in ('b', 'a', 'c'):
        env.store.append_message(PLAN, None, 'assistant', identifier, message_id=identifier, at=at)
    env.store.compress_messages(PLAN, None, ['b'], '摘要', summary_id='summary', at=at)
    assert [r['id'] for r in env.store.messages(PLAN)] == ['a', 'c', 'summary']
    assert [r['id'] for r in env.store.messages(PLAN, include_compressed=True)] == ['a', 'b', 'c', 'summary']
    assert [r['id'] for r in env.store.messages(PLAN, limit=2)] == ['c', 'summary']
    assert env.store.messages(PLAN, limit=0) == []
    with pytest.raises(ValueError):
        env.store.messages(PLAN, limit=-1)
