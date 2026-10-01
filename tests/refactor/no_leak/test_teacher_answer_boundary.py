"""Pre-verdict speech is validated before streaming; original questions persist."""
from dataclasses import asdict, replace
import copy
import json

import pytest

from tests.refactor.app.test_app_classroom import room, PLAN, act, ctx  # noqa: F401
from refactor.learning.course import teacher_agent, lesson_prep
from refactor.learning.course.judge_agent import Verdict
from refactor.learning.course.planner_agents import AgentFormatError

QUESTION = {"type": "short", "stem": "列表里有两个 'apple'，fruits.remove('apple') 会删掉几个？",
            "reference": "只删除第一个匹配的 apple，后面那个仍保留；成功时返回 None。"}


@pytest.mark.parametrize('text', ["所以这里只会删掉一个。", "只删除首个匹配项。", "调用会返回 None。",
                                 "只有第一个 apple 被移除。", "成功时没有返回值。"])
def test_reference_points_and_quantity_paraphrases_are_blocked(text):
    assert teacher_agent.answer_leak(text, QUESTION)


def test_safe_restatement_and_question_meaning_are_allowed():
    assert not teacher_agent.answer_leak("午饭稍后聊。" + QUESTION['stem'], QUESTION)
    assert not teacher_agent.answer_leak("题目让你说明这次调用删掉的数量，不是让你写完整代码。", QUESTION)
    choice = {'stem': '选出正确项。', 'answer': '乙说法', 'reference': '乙说法正确'}
    assert teacher_agent.answer_leak('应该选乙说法。', choice)


def test_real_probe_shared_object_name_is_not_an_answer_point():
    # First real probe was unnecessarily rewritten for the phrase "的 apple".
    text = "题目问这次调用后列表里被删掉的 'apple' 的数量，比如 0 个、1 个还是 2 个。请先作答。"
    assert not teacher_agent.answer_leak(text, QUESTION)
    assert teacher_agent.answer_leak("只删除第一个匹配的 apple，后面那个仍保留。", QUESTION)


def test_option_restatement_is_allowed_but_selection_or_confirmation_is_not():
    q = {'stem': '选出正确项。', 'options': ['甲说法', '乙说法', '丙说法', '丁说法'],
         'answer': '乙说法', 'reference': '乙说法正确'}
    full = '选出正确项。A. 甲说法 B. 乙说法 C. 丙说法 D. 丁说法'
    assert not teacher_agent.answer_leak(full, q)
    assert teacher_agent.answer_leak(full + '应该选乙说法。', q)
    assert teacher_agent.answer_leak('选 B 就好了。', q)
    assert teacher_agent.answer_leak('正确的是 B。', q)
    assert teacher_agent.answer_leak('正确的是第二项。', q)
    assert teacher_agent.answer_leak('你说得对，请照这个答案提交。', QUESTION)


def install_pending(classroom, monkeypatch, kind='casual'):
    monkeypatch.setattr(classroom, '_prepare_later', lambda *_: None)
    pending = {'kind': kind, 'answer_id': 'stable-answer', 'point_ids': ['p0'], 'asks': 1,
               'question': copy.deepcopy(QUESTION)}
    classroom.run(PLAN, lambda co, st, cx: st.update(step='answer', pending=pending))
    return pending


@pytest.mark.parametrize('kind,user', [('chat', '午饭吃什么？'), ('question', 'remove 到底删几个？'),
                                      ('question', '没听懂，请重新讲。'), ('unclear', '啊？')])
def test_leaking_interruption_is_rewritten_once_then_only_original_question(room, monkeypatch, kind, user):
    classroom, agents, store = room
    original = install_pending(classroom, monkeypatch)
    monkeypatch.setattr(classroom, '_judge', lambda *_: Verdict(kind, None, (), '用户没有作答。'))
    requests = []
    def complete(purpose, messages, **kw):
        assert purpose == 'teacher'
        requests.append(messages)
        # Attempted early streaming must never see a UI callback.
        assert kw['on_text'] is None
        return type('Reply', (), {'text': '所以这里只会删掉一个，剩下那个 apple 保留。'})()
    monkeypatch.setattr(agents, 'complete', complete)
    emitted = []
    view = act(classroom, 'reply', user, streamed=emitted)
    assert len(requests) == 2
    assert '上次回复未通过' in requests[1][-1]['content']
    assert not any('只会删掉一个' in text for text in emitted)
    assert QUESTION['stem'] in ''.join(emitted)
    assert store.records('judgements', PLAN) == []
    assert classroom._file(PLAN).read({})['pending'] == original
    assert view['step'] == 'answer'


def test_safe_rewrite_is_released_and_hidden_prep_answer_is_not_sent(room, monkeypatch):
    classroom, agents, store = room
    original = install_pending(classroom, monkeypatch)
    monkeypatch.setattr(classroom, '_judge', lambda *_: Verdict('question', None, (), '先问题意。'))
    replies = iter(['只会删掉一个。', '题目只问删除的数量，请先按你的理解作答，判定后再讲原因。'])
    requests = []
    def complete(purpose, messages, **kw):
        requests.append(messages)
        return type('Reply', (), {'text': next(replies)})()
    monkeypatch.setattr(agents, 'complete', complete)
    emitted = []
    act(classroom, 'reply', '没听懂', streamed=emitted)
    assert len(emitted) == 1 and '只会删掉一个' not in emitted[0]
    task = json.loads(requests[0][-1]['content'].split('\n', 1)[1])
    assert task['pending_question']['stem'] == QUESTION['stem']
    assert 'prepared_lesson' not in task and 'reference' not in task['pending_question']
    assert classroom._file(PLAN).read({})['pending'] == original


def test_shared_boundary_protects_final_check_and_rejects_replacement_question(room, monkeypatch):
    classroom, agents, _ = room
    install_pending(classroom, monkeypatch, 'check')
    replies = []
    def complete(*_args, **kw):
        replies.append(kw)
        return type('Reply', (), {'text': '先答题。\n【随口问】新的简单题是什么？【/随口问】'})()
    monkeypatch.setattr(agents, 'complete', complete)
    emitted = []
    classroom.run(PLAN, lambda co, st, cx: classroom._teacher(co, st, cx, '解释题意', ['p0']), ctx(emitted))
    assert len(replies) == 2 and QUESTION['stem'] in ''.join(emitted)
    assert '新的简单题' not in ''.join(emitted)


def test_saved_verdict_allows_answer_explanation_and_streaming(room, monkeypatch):
    classroom, agents, _ = room
    install_pending(classroom, monkeypatch)
    def work(co, st, cx):
        classroom._record(co, st, 'casual', st['pending'], Verdict('answer', False, ('p0',), '删除数量不对。'), '两个')
        return classroom._teacher(co, st, cx, '判定后解释', ['p0'], short=True)
    def complete(*_args, **kw):
        text = '这里只删除第一个匹配项，所以只会删掉一个。'
        kw['on_text'](text)
        return type('Reply', (), {'text': text})()
    monkeypatch.setattr(agents, 'complete', complete)
    emitted = []
    classroom.run(PLAN, work, ctx(emitted))
    assert '只会删掉一个' in ''.join(emitted)


def test_new_rule_allows_confirming_the_taught_example():
    from tests.refactor.guided_lessons.fixtures import draft
    assert lesson_prep.check_lesson(draft()) == ()
    assert lesson_prep.VERSION == 4
    assert not hasattr(lesson_prep, 'casual_repeats_example')


def test_live_teacher_can_confirm_the_taught_example_after_new_decision():
    from tests.refactor.teacher_agent.test_class_teacher_agent import context
    text = "先看这个例子。\n```python\nfruits = ['apple', 'banana', 'apple']\nfruits.remove('apple')\nprint(fruits)\n```\n【随口问】" + QUESTION['stem'] + "【/随口问】"
    assert teacher_agent.parse_teaching(text, context()).casual_question == QUESTION['stem']


def test_failed_requests_fall_back_without_reference_and_cancel_is_preserved(room, monkeypatch):
    from refactor.learning.connections.errors import ModelError, ModelCancelled
    classroom, agents, _ = room
    install_pending(classroom, monkeypatch)
    calls = []
    def failed(*_args, **kw):
        calls.append(kw)
        raise ModelError('NETWORK_ERROR', '失败')
    monkeypatch.setattr(agents, 'complete', failed)
    emitted = []
    classroom.run(PLAN, lambda co, st, cx: classroom._teacher(co, st, cx, '解释题意', ['p0']), ctx(emitted))
    assert len(calls) == 2 and not teacher_agent.answer_leak(''.join(emitted), QUESTION)
    def cancelled(*_args, **_kw):
        raise ModelCancelled()
    monkeypatch.setattr(agents, 'complete', cancelled)
    emitted.clear()
    from refactor.learning.app.bridge import BridgeError
    with pytest.raises(BridgeError, match='已停止') as exc:
        classroom.run(PLAN, lambda co, st, cx: classroom._teacher(co, st, cx, '解释题意', ['p0']), ctx(emitted))
    assert exc.value.code == 'CANCELLED'
    assert not emitted
