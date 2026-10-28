from datetime import datetime, timezone
import json

from refactor.learning.course.mastery import AttemptClosed, Judgement, MasteryBook, event_to_dict
from refactor.learning.course.question_bank import assessment_questions
from refactor.learning.course.store import _wire_id
from tests.refactor.mastery204.test_store204_grants import prepared_store


NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)


def _add_cloze(store, question_id):
    row = dict(store.records('question_bank', 'plan')[0])
    row.update(id=question_id, origin='cloze', subpoint_id='point-s1', target='挖空对象',
               cloze_subject='挖空对象', stem='这是一道 ____ 题？')
    identifier = _wire_id('question_bank', 'plan', row['id'])
    raw = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    store._db.execute('INSERT OR REPLACE INTO _records VALUES(?,?,?,?,0,1,0,1)',
                      ('question_bank', identifier, 'plan', raw))
    store._materialize('question_bank', 'plan', row, 1)
    return row


def test_unanswered_cloze_is_retired_on_read_and_migration_is_idempotent(tmp_path):
    store = prepared_store(tmp_path)
    try:
        cloze = _add_cloze(store, 'cloze-unanswered')
        store.load_mastery('plan')
        retired = next(q for q in store.records('question_bank', 'plan') if q['id'] == cloze['id'])
        assert retired['role'] == 'retired'
        assert retired['origin'] == 'cloze'
        assert retired['retirement']['version'] == 8
        assert cloze['id'] not in {q['id'] for q in assessment_questions(store.records('question_bank', 'plan'))}
        first = retired

        store.require_material_plan('plan')
        assert next(q for q in store.records('question_bank', 'plan') if q['id'] == cloze['id']) == first
    finally:
        store.close()


def test_answered_cloze_history_and_passed_module_survive_retirement(tmp_path):
    store = prepared_store(tmp_path)
    try:
        cloze = _add_cloze(store, 'cloze-answered')
        store.append_question_answer('plan', 'answer-cloze', module_id='module', context='practice',
            attempt_id='attempt', question_id=cloze['id'], point_ids=['point'], angle='fill', correct=False,
            wrong_points=['point'], reason='答错', at=NOW.isoformat(), answer='wrong', question=cloze)
        store.append_judgement('plan', Judgement('cloze-judgement', 1, NOW, 'check', 'module',
            'attempt', (('point', False),), question_id=cloze['id']))

        # The module had independently passed before the new migration.
        passed = next(q for q in store.records('question_bank', 'plan') if q.get('origin') == 'found')
        store.append_judgement('plan', Judgement('pass-judgement', 2, NOW, 'module_test', 'module',
            'pass-attempt', (('point', True),), question_id=passed['id']))
        store.append_attempt_closed('plan', AttemptClosed('pass-close', 3, 'module', 'pass-attempt', 'module_test'),
            details={'assessment_id': 'pass-attempt', 'question_ids': [passed['id']]})
        assert store.load_mastery('plan').module('module').state == 'passed'
        module_before = store._get('modules', 'plan', 'module')
        point_before = store._get('points', 'plan', 'point')

        assert store.load_mastery('plan').module('module').state == 'passed'
        assert store._get('modules', 'plan', 'module') == module_before
        assert store._get('points', 'plan', 'point') == point_before
        assert store.records('question_answers', 'plan')[0]['id'] == 'answer-cloze'
        wrong = store.wrong_answers('plan')
        assert any(answer.get('question_id') == cloze['id'] for group in wrong for answer in group['attempts'])
        events = store.records('judgements', 'plan')
        assert {row['event']['event_id'] for row in events} >= {'cloze-judgement', 'pass-judgement'}
        assert cloze['id'] not in {q['id'] for q in assessment_questions(store.records('question_bank', 'plan'))}
    finally:
        store.close()


def _event_row(event, details=None):
    return dict(id=event.event_id, event=event_to_dict(event), details=details or {})


def test_mixed_frozen_test_drops_cloze_from_mastery_denominator_but_keeps_its_score():
    found = dict(id='found', module_id='m', point_ids=['p'], origin='found', role='bank')
    cloze = dict(id='cloze', module_id='m', point_ids=['p'], origin='cloze', role='retired')
    found_answer = Judgement('found-answer', 1, NOW, 'module_test', 'm', 'a', (('p', True),), question_id='found')
    cloze_answer = Judgement('cloze-answer', 2, NOW, 'module_test', 'm', 'a', (('p', False),), question_id='cloze')
    close = AttemptClosed('close', 3, 'm', 'a', 'module_test')
    book = MasteryBook.from_course_records(
        [{'id': 'p', 'module_id': 'm', 'position': 0}], [{'id': 'm', 'position': 0}],
        [found, cloze], set(), [_event_row(found_answer), _event_row(cloze_answer),
            _event_row(close, {'assessment_id': 'a', 'question_ids': ['found', 'cloze']})])
    assert book.module('m').state == 'passed'
    assert book.stats()['modules']['m']['first_test']['total_questions'] == 2
    assert book.stats()['modules']['m']['first_test']['correct_questions'] == 1
    assert book.question('cloze').module_correct is False
    assert book.question('cloze').ever_wrong is True


def test_failed_all_cloze_test_does_not_block_taught_module_after_retirement():
    cloze = dict(id='cloze', module_id='m', point_ids=['p'], origin='cloze', role='retired')
    answer = Judgement('cloze-answer', 1, NOW, 'module_test', 'm', 'a', (('p', False),), question_id='cloze')
    close = AttemptClosed('close', 2, 'm', 'a', 'module_test')
    book = MasteryBook.from_course_records(
        [{'id': 'p', 'module_id': 'm', 'position': 0}], [{'id': 'm', 'position': 0}],
        [cloze], {'p'}, [_event_row(answer),
            _event_row(close, {'assessment_id': 'a', 'question_ids': ['cloze']})])
    assert book.module('m').state == 'passed'
    assert book.check_ready()
    assert book.stats()['modules']['m']['first_test']['total_questions'] == 1
    assert book.stats()['modules']['m']['first_test']['correct_questions'] == 0
    assert book.question('cloze').ever_wrong is True


def test_historically_passed_mixed_manifest_remains_passed_after_cloze_filter():
    found = dict(id='found', module_id='m', point_ids=['p'], origin='found', role='bank')
    clozes = [dict(id=f'cloze-{i}', module_id='m', point_ids=['p'], origin='cloze', role='retired')
              for i in range(9)]
    ids = [q['id'] for q in [found, *clozes]]
    events = [_event_row(Judgement('found-answer', 1, NOW, 'module_test', 'm', 'a',
                    (('p', False),), question_id='found'))]
    events.extend(_event_row(Judgement(f'cloze-answer-{i}', i+2, NOW, 'module_test', 'm', 'a',
                        (('p', True),), question_id=f'cloze-{i}')) for i in range(9))
    events.append(_event_row(AttemptClosed('close', 11, 'm', 'a', 'module_test'),
                             {'assessment_id': 'a', 'question_ids': ids}))
    book = MasteryBook.from_course_records(
        [{'id': 'p', 'module_id': 'm', 'position': 0}], [{'id': 'm', 'position': 0}],
        [found, *clozes], set(), events)
    assert book.module('m').state == 'passed'
    assert book.stats()['modules']['m']['first_test']['total_questions'] == 10
    assert book.stats()['modules']['m']['first_test']['correct_questions'] == 9


def test_failed_all_cloze_manifest_before_learning_does_not_block_teaching():
    cloze = dict(id='cloze', module_id='m', point_ids=['p'], origin='cloze', role='retired')
    answer = Judgement('cloze-answer', 1, NOW, 'module_test', 'm', 'a', (('p', False),), question_id='cloze')
    close = AttemptClosed('close', 2, 'm', 'a', 'module_test')
    book = MasteryBook.from_course_records(
        [{'id': 'p', 'module_id': 'm', 'position': 0}], [{'id': 'm', 'position': 0}],
        [cloze], set(), [_event_row(answer),
            _event_row(close, {'assessment_id': 'a', 'question_ids': ['cloze']})])
    assert book.module('m').state == 'untested'
    assert book.stats()['modules']['m']['first_test']['total_questions'] == 1
    assert book.question('cloze').module_correct is False


def test_cloze_migration_does_not_retire_legacy_found_choice_question(tmp_path):
    from refactor.learning.course.question_bank import assessment_questions
    from tests.refactor.integration38.support import bank_question

    store = prepared_store(tmp_path)
    try:
        source_question = bank_question('point', 'module', kind='choice', chunk='chunk')
        store.add_bank_questions('plan', 'module', [source_question])
        cloze = _add_cloze(store, 'cloze-unanswered')
        store.load_mastery('plan')

        bank = store.records('question_bank', 'plan')
        assert next(q for q in bank if q['id'] == source_question['id'])['role'] == 'bank'
        assert source_question['id'] in {q['id'] for q in assessment_questions(bank, 'module')}
        assert next(q for q in bank if q['id'] == cloze['id'])['role'] == 'retired'
    finally:
        store.close()


def test_cloze_only_retest_cannot_revoke_historical_passed_module():
    cloze = dict(id='cloze', module_id='m', point_ids=['p'], origin='cloze', role='retired')
    first_answer = Judgement('first-answer', 1, NOW, 'module_test', 'm', 'first', (('p', True),), question_id='cloze')
    first_close = AttemptClosed('first-close', 2, 'm', 'first', 'module_test')
    retry_answer = Judgement('retry-answer', 3, NOW, 'retest', 'm', 'retry', (('p', False),), question_id='cloze')
    retry_close = AttemptClosed('retry-close', 4, 'm', 'retry', 'retest')
    book = MasteryBook.from_course_records(
        [{'id': 'p', 'module_id': 'm', 'position': 0}], [{'id': 'm', 'position': 0}], [cloze], set(),
        [_event_row(first_answer), _event_row(first_close, {'assessment_id': 'first', 'question_ids': ['cloze']}),
         _event_row(retry_answer), _event_row(retry_close, {'assessment_id': 'first', 'question_ids': ['cloze']})])
    assert book.module('m').state == 'passed'
