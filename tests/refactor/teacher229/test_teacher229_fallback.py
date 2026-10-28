"""BUG-183 round three: source mistakes never leave an active QA unanswered."""
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from refactor.learning.app.classroom import Classroom, RESET, SUPPLEMENT_PREFIX
from refactor.learning.course import teacher_agent as agent
from refactor.learning.connections.errors import ModelCancelled, ModelError
from tests.refactor.teacher_agent.test_class_teacher_agent import context


ROOT = Path(__file__).resolve().parents[3]
TEACHER228 = ROOT / 'tests' / 'refactor' / 'teacher228'


def _read(path):
    with path.open(encoding='utf-8') as stream:
        return json.load(stream)


def _teacher_context(*, question, evidence, chunk='source'):
    return context(qa=True, question_only=True, user=question, excerpts={chunk: evidence}, dialogue=[])


class _Gateway:
    def __init__(self, responses=(), error=None):
        self.responses = list(responses)
        self.error = error
        self.calls = []

    def complete(self, purpose, messages, *, system=None, on_text=None, **kwargs):
        self.calls.append((purpose, messages, system))
        if self.error:
            raise self.error
        text = self.responses.pop(0) if self.responses else self.last
        self.last = text
        on_text(text)
        return SimpleNamespace(text=text)


def _run(ctx, responses=(), *, cancel=None, error=None):
    gateway = _Gateway(responses, error=error)
    room = object.__new__(Classroom)
    room.services = SimpleNamespace(gateway=gateway)
    streamed = []
    result = room._generate_teacher('synthetic-plan', ctx, agent.teacher_request(ctx), cancel or threading.Event(), streamed.append)
    return result, gateway, streamed


def _saved_228():
    return _read(TEACHER228 / 'live_responses228.json')['responses']


def _samples_226():
    return _read(TEACHER228 / 'responses228.json')['cases']


@pytest.mark.parametrize('row', _saved_228(), ids=lambda row: f"call-{row['call_index']}")
def test_229_all_eight_saved_228_real_responses_return_an_answer(row):
    """Replay saved model output through the current parser/rewrite/fallback path."""
    from tests.refactor.teacher226.live_compare226 import SAMPLES, _context_for
    sample = next(sample for sample in SAMPLES if sample[0] == row['sample_id'])
    ctx = _context_for(sample, current=True)
    result, _, _ = _run(ctx, [row['response'], row['response']])
    teaching, _, accepted = result
    assert teaching.speech.strip()
    assert accepted.strip()
    assert teaching.answer_basis in {'material', 'supplement'}


@pytest.mark.parametrize('row', _samples_226(), ids=lambda row: row['id'])
def test_229_all_four_226_retained_excerpts_return_an_answer(row):
    ctx = _teacher_context(question=row['question'], evidence=row['evidence'])
    raw = '【依据】material:c1【/依据】\n' + row['quoted_answer_excerpt']
    result, _, _ = _run(ctx, [raw, raw])
    teaching, _, accepted = result
    assert teaching.speech.strip()
    assert accepted.strip()


def test_229_common_resume_advice_and_unsupported_absolute_extension_are_not_blocked():
    evidence = '讲义只说技术面试会围绕经历提问。'
    ctx = _teacher_context(question='我没有获奖经历，简历怎么写？', evidence=evidence)
    raw = ('【依据】supplement【/依据】\n'
           '招聘方看的是你能否胜任，奖项只是佐证之一，不是必需项。')
    result = agent.parse_teaching(raw, ctx)
    assert '奖项只是佐证之一，不是必需项' in result.speech
    assert result.answer_basis == 'supplement' and result.cited_chunks == ()
    # No absolute-word truth gate: the model may explain an ordinary general
    # recommendation even when the supplied source does not discuss it.


def test_229_persistent_bad_quote_is_delivered_as_supplement_without_source_marks():
    ctx = _teacher_context(question='解释这份讲义。', evidence='讲义介绍模块边界用于明确组件职责。')
    invalid = ('【依据】material:c1【/依据】\n'
               '资料写道：“组件边界由魔法产生。”[c1] 这是一条中文解释。')
    result, gateway, streamed = _run(ctx, [invalid, invalid])
    teaching, _, accepted = result
    assert len(gateway.calls) == 2
    assert teaching.answer_basis == 'supplement'
    assert teaching.speech.startswith(SUPPLEMENT_PREFIX)
    assert '组件边界由魔法产生' in teaching.speech
    assert '原话是' not in teaching.speech
    assert '[c1]' not in teaching.speech and 'mf-source:' not in teaching.speech
    assert accepted.startswith('【依据】supplement')
    assert RESET in streamed
    assert streamed[-1] == teaching.speech
    assert '资料写道' not in accepted and '原文说' not in accepted
    assert '[c1]' not in accepted and 'mf-source:' not in accepted


def test_229_material_quote_mislabeled_supplement_is_rewritten_then_delivered():
    evidence = '系统把模块按职责拆分。'
    ctx = _teacher_context(question='讲义对模块怎么说？', evidence=evidence)
    misplaced = '【依据】supplement【/依据】\n原文说：“' + evidence + '”[c1]'
    result, gateway, _ = _run(ctx, [misplaced, misplaced])
    teaching, _, accepted = result
    assert len(gateway.calls) == 2
    assert teaching.answer_basis == 'supplement'
    assert evidence in teaching.speech
    assert '[c1]' not in teaching.speech and '原文说' not in teaching.speech
    assert accepted.startswith('【依据】supplement')


def test_229_explicit_external_supplement_can_compare_with_a_source_quote():
    evidence = '系统把模块按职责拆分。'
    ctx = _teacher_context(question='系统为什么这样拆分？', evidence=evidence)
    raw = ('【依据】supplement【/依据】\n以下是资料外补充，原文仅作背景：'
           '资料写道：“' + evidence + '”[c1] 再用比方说明它的用处。')
    result = agent.parse_teaching(raw, ctx)
    assert result.answer_basis == 'supplement' and not result.cited_chunks


def test_229_invented_source_quote_in_supplement_falls_back_to_an_answer():
    evidence = '系统把模块按职责拆分。'
    ctx = _teacher_context(question='解释这句话。', evidence=evidence)
    invalid = '【依据】supplement【/依据】\n原文说：“模块都由魔法连接。”[c1] 这是一条中文解释。'
    result, gateway, _ = _run(ctx, [invalid, invalid])
    teaching, _, accepted = result
    assert len(gateway.calls) == 2
    assert teaching.answer_basis == 'supplement'
    assert '都由魔法连接' in teaching.speech
    assert '原文说' not in teaching.speech and '[c1]' not in teaching.speech
    assert accepted.startswith('【依据】supplement')


def test_229_fallback_removes_quote_attribution_when_verb_is_omitted():
    evidence = '系统把模块按职责拆分。'
    ctx = _teacher_context(question='解释这句话。', evidence=evidence)
    invalid = '【依据】supplement【/依据】\n原文：“这句话是伪的。”[c1]仍作为补充显示。'
    result, gateway, _ = _run(ctx, [invalid, invalid])
    teaching, _, accepted = result
    assert len(gateway.calls) == 2
    assert '原文：“' not in teaching.speech and '原文：“' not in accepted
    assert '原文说：“' not in teaching.speech and '[c1]' not in teaching.speech
    assert '这句话是伪的' in teaching.speech and teaching.answer_basis == 'supplement'


def test_229_fallback_keeps_state_change_claim_guard():
    ctx = _teacher_context(question='这份计划现在是什么状态？', evidence='计划状态显示 active。')
    invalid = ('【依据】material:c1【/依据】\n'
               '我已经把计划改成 completed 状态。[c1]')
    with pytest.raises(agent.TeachingFormatError, match='修改了计划'):
        _run(ctx, [invalid, invalid])


def test_229_fallback_keeps_chinese_answer_when_original_body_is_english():
    ctx = _teacher_context(question='解释这份讲义。', evidence='系统采用模块边界划分职责。')
    invalid = '【依据】material:c99【/依据】\nThe boundary separates each responsibility.'
    result, _, _ = _run(ctx, [invalid, invalid])
    teaching, _, accepted = result
    assert teaching.answer_basis == 'supplement'
    assert teaching.speech.startswith(SUPPLEMENT_PREFIX)
    assert 'responsibility' in teaching.speech
    assert accepted.startswith('【依据】supplement')
    assert '[c99]' not in teaching.speech and '[c99]' not in accepted


@pytest.mark.parametrize('attribution', ['资料明确指出', '原文说明'])
def test_229_fallback_removes_unquoted_material_attribution_too(attribution):
    ctx = _teacher_context(question='解释这份资料。', evidence='材料介绍系统模块。')
    invalid = f'【依据】material:c99【/依据】\n{attribution}：业务校验可以作为一个独立步骤。[c99]'
    (teaching, _, accepted), gateway, _ = _run(ctx, [invalid, invalid])
    assert len(gateway.calls) == 2
    assert '业务校验可以作为一个独立步骤' in teaching.speech
    assert attribution not in teaching.speech and '[c99]' not in teaching.speech
    assert '补充说明：业务校验' in teaching.speech
    assert attribution not in accepted


def test_229_qa_prompt_preserves_general_advice_and_bounds_technical_guarantees():
    qa_system, _ = agent.teacher_request(context(qa=True))
    lesson_system, _ = agent.teacher_request(context())
    assert '常见的一般建议按通常场景解释即可' in qa_system
    assert '保证的对象、成立条件和时间范围' in qa_system
    assert '传播或收敛保证' in qa_system
    assert '【答疑准确性】' not in lesson_system


def test_229_material_pronoun_keeps_its_previously_cited_source():
    evidence = '业务规则校验由应用服务层负责；数据库约束作为最后一道完整性防线。'
    ctx = _teacher_context(question='数据库约束的作用？', evidence=evidence)
    raw = ('【依据】material:c1【/依据】\n业务规则校验由应用服务层负责，数据库约束作为最后一道完整性防线。[c1]'
           '\n\n两者是先后两道，职责不同[c1]。')
    (teaching, _, _), gateway, _ = _run(ctx, [raw])
    assert len(gateway.calls) == 1
    assert teaching.answer_basis == 'material' and teaching.cited_chunks == ('source',)
    with pytest.raises(agent.QAParseError):
        agent.parse_teaching('【依据】material:c1【/依据】\n两者是先后两道，职责不同[c1]。', ctx)


def test_229_cancel_before_generation_does_not_send_or_fallback():
    ctx = _teacher_context(question='解释这份讲义。', evidence='系统采用模块边界划分职责。')
    cancel = threading.Event()
    cancel.set()
    room = object.__new__(Classroom)
    gateway = _Gateway(['some answer'])
    room.services = SimpleNamespace(gateway=gateway)
    with pytest.raises(ModelCancelled):
        room._generate_teacher('synthetic-plan', ctx, agent.teacher_request(ctx), cancel, lambda _: None)
    assert gateway.calls == []


def test_229_gateway_failure_is_not_swallowed_by_provenance_fallback():
    ctx = _teacher_context(question='解释这份讲义。', evidence='系统采用模块边界划分职责。')
    room = object.__new__(Classroom)
    room.services = SimpleNamespace(gateway=_Gateway(error=ModelError('MODEL_CONNECTION_FAILED', 'temporary model failure')))
    with pytest.raises(ModelError, match='temporary model failure'):
        room._generate_teacher('synthetic-plan', ctx, agent.teacher_request(ctx), threading.Event(), lambda _: None)
