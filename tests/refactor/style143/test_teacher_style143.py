"""Style-only teaching issues keep the first usable draft unchanged."""
from types import SimpleNamespace

import pytest

from refactor.learning.app.classroom import Classroom, RESET
from refactor.learning.connections.errors import ModelError
from refactor.learning.course import teacher_agent as agent
from tests.refactor.teacher_agent.test_class_teacher_agent import context


STYLE_CASES = [
    ('HIGHLIGHT_COUNT', '中文说明没有荧光。', {}),
    ('HIGHLIGHT_COUNT', '==第一句。== ==第二句。== ==第三句。== ==第四句。==', {}),
    ('HIGHLIGHT_QUESTION', '==你怎么看？==', {}),
    ('WRAPUP_TOO_LONG', '==先看结论。==' + '解释材料中的关系，' * 10 + '再回到结论。', {'wrapping': True}),
    ('WRAPUP_SENTENCE_COUNT', '==第一条结论。==第二条结论。第三条结论。', {'wrapping': True}),
    ('WRAPUP_QUESTION', '==先认识压强。==你怎么看？', {'wrapping': True}),
    ('REPEATED_ENDING', '==先认识压强。==这一层先把定义记清。你怎么看？',
     {'first_turn': True, 'previous_intro_ending': 'question'}),
    ('REPEATED_OPENING', '这块可以这样理解。==单位面积上的压力叫压强。==',
     {'recent_styles': [dict(lead='这块可以这样理解。')]}),
]


def teaching_context(**concept_updates):
    concept = dict(
        title='压强',
        notes=[dict(text='单位面积上的压力叫压强。', chunk_id='chunk-a')],
        first_turn=False,
        recent_styles=[],
        previous_intro_ending=None,
        wrapping=False,
    )
    concept.update(concept_updates)
    value = context(concept=concept)
    value['opening'] = False
    return value


@pytest.mark.parametrize('style,text,updates', STYLE_CASES, ids=[row[0] for row in STYLE_CASES])
def test_style143_usable_style_draft_is_kept_after_one_model_call(style, text, updates):
    calls = []

    def complete(*args, **kwargs):
        calls.append(args)
        kwargs['on_text'](text)
        return SimpleNamespace(text=text)

    ctx = teaching_context(**updates)
    room = Classroom(SimpleNamespace(gateway=SimpleNamespace(complete=complete)), lambda: None)
    emitted = []
    result = room._generate_teacher('plan', ctx, agent.teacher_request(ctx), None, emitted.append)

    assert len(calls) == 1
    assert result[0].speech == text
    assert RESET not in emitted
    # In focus mode an unmarked reply must remain available as ordinary text;
    # the renderer's focus fallback is covered by the UI suite.
    if style == 'HIGHLIGHT_COUNT' and '==' not in text:
        assert agent.reply_metrics(text)['highlight_spans'] == 0
        assert result[0].speech in ''.join(emitted)


KEPT_REASONS = [
    ('STATE_CLAIM', '我已经修改计划。'),
    ('CHINESE_REQUIRED', 'A clear explanation.'),
    ('PARSE_FAILED', ''),
    ('TOO_LONG', '==单位面积上的压力叫压强。==' + '对照压力与面积理解这个前提。' * 35),
]
GOOD = '==单位面积上的压力叫压强。==这一层先把定义记清。'


@pytest.mark.parametrize('reason,bad', KEPT_REASONS, ids=[row[0] for row in KEPT_REASONS])
def test_style143_non_style_rejections_still_rewrite_once(reason, bad):
    ctx = teaching_context()
    replies = iter([bad, GOOD])
    calls = []

    def complete(*args, **kwargs):
        calls.append(args)
        text = next(replies)
        kwargs['on_text'](text)
        return SimpleNamespace(text=text)

    room = Classroom(SimpleNamespace(gateway=SimpleNamespace(complete=complete)), lambda: None)
    emitted = []
    result = room._generate_teacher('plan', ctx, agent.teacher_request(ctx), None, emitted.append)

    assert len(calls) == 2
    assert result[0].speech == GOOD
    assert RESET in emitted


def test_style143_second_too_long_reply_is_kept_whole():
    ctx = teaching_context()
    long_reply = '==单位面积上的压力叫压强。==' + '对照压力与面积理解这个前提。' * 35
    calls = []

    def complete(*args, **kwargs):
        calls.append(args)
        kwargs['on_text'](long_reply)
        return SimpleNamespace(text=long_reply)

    room = Classroom(SimpleNamespace(gateway=SimpleNamespace(complete=complete)), lambda: None)
    emitted = []
    result = room._generate_teacher('plan', ctx, agent.teacher_request(ctx), None, emitted.append)

    assert len(calls) == 2
    assert result[0].speech == long_reply
    assert emitted.count(RESET) >= 1


def test_style143_empty_reply_retry_still_uses_bug105_retry():
    # Reuse the established fake gateway so this specifically guards the
    # empty-model-reply retry while style checks are removed.
    from tests.refactor.reject138.test_teacher_empty_retry105 import (
        GOOD as EMPTY_GOOD,
        ctx as empty_context,
        empty,
        room_with,
    )

    room, calls, deferred = room_with([empty(), EMPTY_GOOD])
    teaching_ctx = empty_context()
    result = room._generate_teacher(
        'plan', teaching_ctx, agent.teacher_request(teaching_ctx), None, lambda _: None,
    )

    assert len(calls) == 2 and deferred == [True]
    assert '压强' in result[2]


def test_style143_prompt_keeps_style_as_a_preference_not_a_rejection_contract():
    system, _ = agent.teacher_request(teaching_context())
    assert '超过350字且学生未要求展开' in system
    assert '不因这些样式问题打回重写' in system
    assert '不合要求只重写一次' not in system
