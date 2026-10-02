import json

import pytest

from refactor.learning.course.code_runner import CodeRunError, run_python
from refactor.learning.course.question_agent import parse_questions
from refactor.learning.course.question_review import check_fix_error_consistency, check_question
from refactor.learning.course.question_solver import parse_solution, solver_request, verify
from refactor.learning.course.planner_agents import AgentFormatError


def ctx():
    return {
        'purpose': 'module_test', 'per_point': 1, 'avoid': [],
        'points': [{'id': 'p1', 'title': '方程', 'summary': '解一元方程', 'chunk_ids': ['c1']}],
        'excerpts': {'c1': '等式两边做相同运算仍保持相等。'},
    }


def math_row(**changes):
    row = {
        'id': 'q1', 'type': 'fix_error',
        'stem': '解方程 m+4=-5。指出错误并给出正确解。', 'options': [],
        'answer': '错误做法没有保持等价，正确解为 m=-9，代入 m+4=-5 成立。',
        'reference': '错误原因和正确解均须有方程依据。', 'point_ids': ['p1'], 'chunk_ids': ['c1'],
        'required_points': ['说明错误原因', '给出正确解'],
        'error_cause': '移项时没有变号',
        'wrong_work': ['m+4=-5', 'm=-5+4', 'm=-1'],
        'wrong_result': 'm=-1',
    }
    row.update(changes)
    return row


def response(row):
    return json.dumps({'questions': [row]}, ensure_ascii=False)


def test_new_fields_parse_and_math_checks_standard_solution():
    question = parse_questions(response(math_row()), ctx())[0]
    assert question.error_cause == '移项时没有变号'
    assert question.wrong_work == ('m+4=-5', 'm=-5+4', 'm=-1')
    assert not check_fix_error_consistency(question)
    assert not check_question(question, points=ctx()['points'], excerpts=ctx()['excerpts'], purpose='module_test')


def test_m_plus_four_legacy_contradiction_is_rejected():
    row = {
        'id': 'old', 'type': 'fix_error',
        'stem': '小华解方程 m+4=−5 时，两边同时加4，得到 m=−1。请指出他的错误并给出正确解。',
        'options': [], 'answer': '正确解为 m=-9。',
        'reference': '小华错在两边同时加4，导致 m=-1。',
        'point_ids': ['p1'], 'chunk_ids': ['c1'],
        'required_points': ['指出错误'],
    }
    assert 'fix_error_wrong_work_contradicts_result' in check_question(
        row, points=ctx()['points'], excerpts=ctx()['excerpts'])


def test_new_math_work_must_start_with_equivalent_original_and_correct_answer():
    row = math_row(wrong_work=['m=-1', 'm+4=-5'], wrong_result='m=-1',
                   answer='错误过程不等价，正确解为 m=-9。')
    assert 'fix_error_first_step_not_original' in check_fix_error_consistency(row)
    row = math_row(answer='错误过程，正确解为 m=-1。')
    assert 'fix_error_correct_answer_not_original' in check_fix_error_consistency(row)


def test_partial_new_contract_is_rejected_by_parser():
    with pytest.raises(AgentFormatError):
        parse_questions(response(math_row(wrong_result=None)), ctx())


def test_isolated_runner_output_and_io_guard():
    assert run_python('x=[1,2]\nx.append(3)\nprint(x)').output == '[1, 2, 3]'
    assert run_python('t=(5,)\nprint(type(t))').output == "<class 'tuple'>"
    with pytest.raises(CodeRunError):
        run_python("open('x.txt','w')")


def test_code_fix_error_runs_both_versions_and_rejects_wrong_output():
    row = math_row(
        stem='运行下面的错误代码并指出结果，再给出修正后的代码。',
        answer='修正代码输出 3。',
        error_cause='把索引 1 写成了索引 2',
        wrong_work=['```python\nitems=[1,2,3]\nprint(items[2])\n```'],
        wrong_result='3',
        correct_work='```python\nitems=[1,2,3]\nprint(items[1])\n```',
        correct_result='2',
    )
    # The conceptual stem has no equations, so only the isolated code gate runs.
    assert not check_fix_error_consistency(row)
    row['wrong_result'] = '2'
    assert 'fix_error_code_output_mismatch' in check_fix_error_consistency(row)


def test_code_fix_error_can_read_declared_output_from_answer():
    row = math_row(
        stem='运行错误代码并修正。',
        answer='正确代码如下。\n```python\nprint(2)\n```\n输出：\n```text\n2\n```',
        error_cause='把结果写错', wrong_work=['```python\nprint(3)\n```'], wrong_result='3',
        correct_work='```python\nprint(2)\n```', correct_result=None,
    )
    assert not check_fix_error_consistency(row)


def test_blind_solver_contract_carries_recalculation_fields():
    question = math_row()
    system, messages = solver_request(question, {'c1': '等式两边做相同运算仍保持相等。'})
    assert 'recomputed_wrong_result' in system and 'wrong_work' in messages[-1]['content']
    payload = {'answerable': True, 'answer': '正确解 m=-9', 'viable_options': [], 'reason': '逐步核算',
               'recomputed_wrong_result': 'm=-1', 'wrong_result_matches': True,
               'error_step': '第二步移项未变号', 'error_cause': '移项时没有变号'}
    solution = parse_solution(json.dumps(payload, ensure_ascii=False), question)
    assert solution.wrong_result_matches is True and solution.error_step
    payload['wrong_result_matches'] = False
    # Shape remains valid; verify() uses this value to reject a contradictory
    # question rather than silently discarding the blind recalculation.
    assert parse_solution(json.dumps(payload, ensure_ascii=False), question).wrong_result_matches is False


def test_blind_solver_mismatch_stops_before_judge():
    class Gateway:
        purposes = []
        def complete(self, purpose, messages, **kwargs):
            self.purposes.append(purpose)
            return type('Result', (), {'text': json.dumps({
                'answerable': True, 'answer': 'm=-9', 'viable_options': [], 'reason': '重算',
                'recomputed_wrong_result': 'm=-1', 'wrong_result_matches': True,
                'error_step': '第二步', 'error_cause': '把结果算错了',
            }, ensure_ascii=False)})()

    gateway = Gateway()
    state = {}
    assert verify(gateway, math_row(), points=ctx()['points'], excerpts=ctx()['excerpts'],
                  state=state, save=lambda: None) is False
    assert gateway.purposes == ['question_solve']
