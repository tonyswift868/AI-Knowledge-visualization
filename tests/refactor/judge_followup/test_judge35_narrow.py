"""Third-fifth batch: information follow-up is limited to correct direction plus missing explanation."""
import json

from refactor.learning.course import judge_agent as judge


def _ctx(question, points=None, **extra):
    points = points or [dict(id='p0', title='range', summary='')]
    return dict(mode='test', question=question, points=points, excerpts={'c': 'range 规则'},
                reference=question.get('reference', ''), required_points=question.get('required_points'),
                require_evidence=True, **extra)


def _short(question, statuses, evidence=None, kind='answer', correct=False, wrong=None):
    evidence = evidence or ['[1,3,5]' if s == 'hit' else '' for s in statuses]
    checks = [dict(point_id='p0', point=item['point'], applies=True, status=status,
                   evidence=evidence[i])
              for i, (item, status) in enumerate(zip(question['required_points'], statuses))]
    return dict(kind=kind, correct=correct, wrong_points=wrong or ([] if correct is not False else ['p0']),
                reason='已给出部分结果，但未说明题面要求的理由。', uncertain=False, checks=checks)


def test_range_result_with_missing_step_explanation_is_deferred():
    q = dict(type='short', stem='range(1, 6, 2) 是否包含6？说明步长2怎样决定相邻的数。',
             answer='不包含6，结果为[1,3,5]', reference='不包含6，结果为[1,3,5]；步长2表示每次加2。',
             required_points=[
                 dict(point='结果为[1,3,5]且不包含6', accept=['[1,3,5]'], reject=['包含6']),
                 dict(point='说明步长2决定相邻数每次增加2', accept=['每次加2'], reject=['每次加1'])],
             point_ids=['p0'], chunk_ids=['c'])
    value = _short(q, ['hit', 'miss'])
    verdict = judge.parse_judgement(json.dumps(value, ensure_ascii=False), _ctx(q))
    assert verdict.kind == 'answer' and verdict.correct is False
    assert judge.defer_incomplete(verdict, _ctx(q)).kind == 'insufficient'


def test_missing_explanation_does_not_hide_any_wrong_content():
    q = dict(type='short', stem='说明 range(1, 6, 2) 的结果和步长作用。',
             answer='[1,3,5]，每次加2', reference='[1,3,5]，每次加2。',
             required_points=[dict(point='结果为[1,3,5]', accept=['[1,3,5]'], reject=['[1,3,6]']),
                              dict(point='步长2表示每次加2', accept=['每次加2'], reject=['每次加1'])],
             point_ids=['p0'], chunk_ids=['c'])
    value = _short(q, ['hit', 'wrong'])
    verdict = judge.parse_judgement(json.dumps(value, ensure_ascii=False), _ctx(q))
    assert judge.defer_incomplete(verdict, _ctx(q)).kind == 'answer'


def test_only_saying_statement_is_wrong_has_no_correct_direction_to_follow_up():
    q = dict(type='short', stem='说明 range(1, 6, 2) 的结果和步长作用。',
             answer='[1,3,5]，每次加2', reference='[1,3,5]，每次加2。',
             required_points=[dict(point='结果为[1,3,5]', accept=['[1,3,5]'], reject=[]),
                              dict(point='步长2表示每次加2', accept=['每次加2'], reject=[])],
             point_ids=['p0'], chunk_ids=['c'])
    value = _short(q, ['miss', 'miss'], evidence=['这个说法不对', ''])
    verdict = judge.parse_judgement(json.dumps(value, ensure_ascii=False), _ctx(q))
    assert judge.defer_incomplete(verdict, _ctx(q)).kind == 'answer'


def test_non_explanation_missing_item_is_not_information_follow_up():
    q = dict(type='short', stem='列出两个 range 的关键性质。',
             answer='可迭代；按步长产生整数', reference='可迭代；按步长产生整数。',
             required_points=[dict(point='可迭代', accept=['可以迭代'], reject=[]),
                              dict(point='按步长产生整数', accept=['按步长'], reject=[])],
             point_ids=['p0'], chunk_ids=['c'])
    value = _short(q, ['hit', 'miss'])
    verdict = judge.parse_judgement(json.dumps(value, ensure_ascii=False), _ctx(q))
    assert judge.defer_incomplete(verdict, _ctx(q)).kind == 'answer'


def test_follow_up_is_one_shot_and_second_result_settles_missing():
    q = dict(type='short', stem='说明结果并解释理由。', answer='结果正确，因为规则如此。',
             reference='结果正确，因为规则如此。',
             required_points=[dict(point='结果正确', accept=['结果正确'], reject=[]),
                              dict(point='解释理由', accept=['因为规则如此'], reject=[])],
             point_ids=['p0'], chunk_ids=['c'])
    first = _short(q, ['hit', 'miss'])
    ctx = _ctx(q)
    provisional = judge.parse_judgement(json.dumps(first, ensure_ascii=False), ctx)
    assert judge.defer_incomplete(provisional, ctx).kind == 'insufficient'
    follow_ctx = _ctx(q, information_followed_up=True)
    second = dict(first, kind='answer', correct=False, wrong_points=['p0'],
                  checks=[dict(first['checks'][0]), dict(first['checks'][1], status='miss', evidence='')])
    settled = judge.parse_judgement(json.dumps(second, ensure_ascii=False), follow_ctx)
    assert settled.kind == 'answer' and settled.correct is False
