"""The non-blocking QA fallback must survive the real classroom/store path."""
import copy

from tests.refactor.teacher226.test_classroom_answers226 import lesson
from tests.refactor.conversation37.test_grounded_conversation37 import PLAN


def test_229_source_fallback_is_stored_without_assessment_changes(lesson):
    e = lesson
    before = {entity: copy.deepcopy(e.store.records(entity, PLAN)) for entity in
              ('credits', 'judgements', 'question_answers', 'question_bank', 'teacher_profile')}
    e.response = '【依据】material:c99【/依据】资料指出：没有获奖也可以如实展示项目经历。[c99]'
    e.room.ask(e.room._course(PLAN), e.state, e.ctx, '没有获奖怎么写简历？')
    assert len(e.requests) == 2
    row = e.store.messages(PLAN, 'm0')[-1]
    assert row['kind'] == 'teach_supplement'
    assert row['content'].startswith('讲义里没有，下面是补充')
    assert '如实展示项目经历' in row['content']
    assert '资料指出' not in row['content'] and 'mf-source:' not in row['content']
    assert '[c99]' not in row['content']
    assert {entity: e.store.records(entity, PLAN) for entity in before} == before
    history = e.room._dialogue(e.room._course(PLAN), e.state)
    assert history[-1]['text'].startswith('讲义里没有，下面是补充')
    assert '[c99]' not in history[-1]['text']
