import pytest

from refactor.learning.course import teacher_agent as agent
from tests.refactor.teacher_agent.test_class_teacher_agent import context


def test_226_only_explicit_qa_context_selects_qa_mode():
    assert agent.teaching_mode(context()) == 'lesson'
    assert agent.teaching_mode(context(qa=True, allow_plan=True)) == 'qa'
    # The caller emits qa=True only on its allow_plan path; this flag is the contract.
    assert agent.teaching_mode(context(qa=True)) == 'qa'


@pytest.mark.parametrize(('protocol', 'basis'), [
    ('lesson', 'lesson'),
    ('material:c1,c2', 'material'),
    ('supplement', 'supplement'),
])
def test_226_qa_protocol_is_removed_and_basis_recorded(protocol, basis):
    ctx = context(qa=True, allow_plan=True)
    body = '答案正文[c1]' if basis != 'material' else '传感器接收环境信息，执行器改变环境。'
    result = agent.parse_teaching(f'【依据】{protocol}【/依据】\n{body}', ctx)
    assert result.answer_basis == basis
    assert result.speech == body if basis != 'supplement' else result.speech == '答案正文'
    assert result.cited_chunks == (('chunk-a', 'chunk-z') if basis == 'material' else
                                   ('chunk-a',) if basis == 'lesson' else ())
    assert result.citation_aliases == (('c1', 'chunk-a'), ('c2', 'chunk-z'))


@pytest.mark.parametrize('protocol', [
    'unknown', 'material:c99', 'material:', 'material:c1,c99', 'supplement,c1',
])
def test_226_missing_or_invalid_qa_protocol_is_rejected(protocol):
    ctx = context(qa=True, allow_plan=True)
    with pytest.raises(agent.TeachingFormatError):
        agent.parse_teaching(f'【依据】{protocol}【/依据】\n答案正文[c1]', ctx)


def test_226_lesson_mode_keeps_legacy_unmarked_replies():
    result = agent.parse_teaching('答案正文[c1]', context())
    assert result.speech == '答案正文[c1]'
    assert result.answer_basis == 'lesson'


def test_226_qa_prompt_marks_qa_and_allows_supplement_without_weakening_lesson_prompt():
    lesson_system, lesson_messages = agent.teacher_request(context())
    qa_system, qa_messages = agent.teacher_request(context(qa=True, allow_plan=True))
    assert '不能把模型常识、合理引申或想象当作依据' in lesson_system
    assert '允许用模型知识直接回答' in qa_system
    qa_task = qa_messages[-1]['content']
    assert '"qa":true' in qa_task
    assert '"qa"' not in lesson_messages[-1]['content']
    assert '凡是主动提问，不论问题是否属于当前知识点，都只能选择 material 或 supplement' in qa_system
    assert '当前知识点内容时，首行输出【依据】lesson' not in qa_system
    assert '属于课外补充，不能作为本点讲课、考核或已讲事实' in lesson_system


def test_226_qa_concept_keeps_current_notes_in_task_without_lesson_scope_limits():
    concept = dict(goal='区分输入与输出', notes=[dict(chunk_id='chunk-a', fact='传感器接收环境信息。')], first_turn=True)
    ctx = context(qa=True, concept=concept)
    system, messages = agent.teacher_request(ctx)
    task = messages[-1]['content']
    assert '区分输入与输出' in task
    assert '传感器接收环境信息。' in task
    assert '当前大点分成若干小要点' not in system
    assert 'notes 是这一步要讲到的事实' not in system


def test_226_question_only_context_forbids_lesson_protocol_and_prompt_says_so():
    ctx = context(qa=True, question_only=True)
    with pytest.raises(agent.QAParseError):
        agent.parse_teaching('【依据】lesson【/依据】\n已讲内容。', ctx)
    result = agent.parse_teaching('【依据】material:c1【/依据】\n传感器接收环境信息。', ctx)
    assert result.answer_basis == 'material'
    system, _ = agent.teacher_request(ctx)
    assert '显式主动提问' in system
    assert '不能选择 lesson' in system


def test_226_unrestricted_qa_still_accepts_lesson_for_reply_classification():
    result = agent.parse_teaching('【依据】lesson【/依据】\n回应当前讲解。', context(qa=True))
    assert result.answer_basis == 'lesson'


def test_226_material_prompt_limits_claims_to_evidence_and_does_not_pad_short_answers():
    system, _ = agent.teacher_request(context(qa=True))
    assert '允许多说一点正确的解释和合理延伸' in system
    assert '引用必须支持紧邻的这句话' in system
    assert '200～300字不是最低长度' in system
    assert '原文短就短答' in system and '不为凑长度补例子' in system
    assert '只有问题确实需要资料之外的新知识' in system
    assert '使用自身知识直接回答' in system


def test_226_stream_header_parser_waits_for_complete_header():
    ctx = context(qa=True, allow_plan=True)
    assert agent.qa_response_header('【依据】material:c1', ctx) is None
    assert agent.qa_response_header('【依据】material:c1,c2【/依据】\n', ctx) == (
        'material', (('c1', 'chunk-a'), ('c2', 'chunk-z')))


@pytest.mark.parametrize('basis', ['material:c1', 'supplement'])
def test_226_live_missing_closing_marker_keeps_explicit_first_line_basis(basis):
    ctx = context(qa=True, question_only=True)
    raw = f'【依据】{basis}\n\n传感器接收环境信息。[c1]'
    result = agent.parse_teaching(raw, ctx)
    assert result.answer_basis == basis.split(':')[0]
    assert '【依据】' not in result.speech
    assert agent.qa_response_header(f'【依据】{basis}', ctx) is None
    assert agent.qa_response_header(f'【依据】{basis}\n', ctx)[0] == result.answer_basis


def test_226_supplement_removes_material_citations_and_links():
    ctx = context(qa=True, allow_plan=True)
    result = agent.parse_teaching('【依据】supplement【/依据】\n补充说明[c1] [来源](mf-source:chunk-a)', ctx)
    assert result.speech == '补充说明'
    assert result.cited_chunks == ()
