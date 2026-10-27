from datetime import datetime, timezone

from refactor.learning.course.mastery import (
    AttemptClosed, Judgement, MasteryBook, PointRef, event_to_dict,
)

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)


def question(qid, mid='m', pid='p'):
    return {'id': qid, 'module_id': mid, 'point_ids': [pid]}


def test_retest_accumulates_against_frozen_48_question_manifest_and_late_question_stays_final():
    rows = [question(f'q{i}') for i in range(49)]
    book = MasteryBook([PointRef('p', 'm')], rows)
    initial_wrong = []
    seq = 0
    for i in range(48):
        seq += 1
        correct = i < 31
        if not correct:
            initial_wrong.append(f'q{i}')
        book.apply(Judgement(f'first-{i}', seq, NOW, 'module_test', 'm', 'first',
                             (('p', correct),), question_id=f'q{i}'))
    seq += 1
    book.apply(AttemptClosed('close-first', seq, 'm', 'first', 'module_test'),
               details={'assessment_id': 'first', 'question_ids': [f'q{i}' for i in range(48)]})
    assert book.module('m').state == 'failed'
    assert book.stats()['modules']['m']['first_test']['total_questions'] == 48

    for i, qid in enumerate(initial_wrong):
        seq += 1
        book.apply(Judgement(f'retest-{i}', seq, NOW, 'retest', 'm', 'retry',
                             (('p', i < 15),), question_id=qid))
    seq += 1
    book.apply(AttemptClosed('close-retry', seq, 'm', 'retry', 'retest'),
               details={'assessment_id': 'first', 'question_ids': initial_wrong})

    assert book.module('m').state == 'passed'  # 31 + 15 = 46 / original 48.
    assert book.stats()['modules']['m']['first_test']['correct_questions'] == 31
    assert book.question('q47').module_correct is False  # The two remaining misses stay visible.
    assert book.question('q48').module_correct is None  # Added later, never in the frozen manifest.
    assert 'q48' in book.next_check_questions()
    reopened=MasteryBook.from_dict(book.to_dict())
    assert reopened.module('m').state=='passed'
    assert reopened.stats()['modules']['m']['first_test']['total_questions']==48


def test_question_id_history_never_falls_back_to_legacy_point_projection():
    point = PointRef('p', 'm')
    row = question('old-q') | {'role': 'retired'}
    judgement = Judgement('old-j', 1, NOW, 'module_test', 'm', 'a', (('p', False),), question_id='old-q')
    close = AttemptClosed('old-c', 2, 'm', 'a', 'module_test')
    book = MasteryBook.from_course_records(
        [{'id': 'p', 'module_id': 'm', 'position': 0}],
        [{'id': 'm', 'position': 0}], [row], set(),
        [{'id': 'old-j', 'event': event_to_dict(judgement)},
         {'id': 'old-c', 'event': event_to_dict(close)}],
    )
    # A qid-bearing event remains a question event, not point-only legacy.
    # It may be outside today's final pool but still contributes to its frozen
    # module test and must not enter the old point reducer.
    assert book.module('m').state == 'failed'
    assert book.stats()['questions']=={}
    assert book.question('old-q').module_correct is False
    assert book._historical_points == {}
    assert book.check_ready() is False


def test_direct_test_retest_keeps_original_manifest_instead_of_later_bank():
    book=MasteryBook([PointRef('p','m')],[question(f'q{i}') for i in range(12)])
    for i in range(10):
        book.apply(Judgement(f'j{i}',i+1,NOW,'skip_test','m','direct',
                             (('p',i<8),),question_id=f'q{i}'))
    book.apply(AttemptClosed('first-close',11,'m','direct','skip_test'),
               details={'assessment_id':'direct','question_ids':[f'q{i}' for i in range(10)]})
    book.apply(Judgement('retry',12,NOW,'retest','m','retry',(('p',True),),question_id='q8'))
    book.apply(AttemptClosed('retry-close',13,'m','retry','retest'),
               details={'assessment_id':'direct','question_ids':['q8']})
    assert book.module('m').state=='skipped'  # 9/10, not 9/12 or 1/1.
    assert book.stats()['modules']['m']['assessment_ids']==[f'q{i}' for i in range(10)]
    assert book.question('q10').module_correct is None
    assert 'q10' in book.next_check_questions()


def test_true_point_only_legacy_events_still_project_as_legacy():
    judgement = Judgement('legacy-j', 1, NOW, 'module_test', 'm', 'a', (('p', True),))
    close = AttemptClosed('legacy-c', 2, 'm', 'a', 'module_test')
    book = MasteryBook.from_course_records(
        [{'id': 'p', 'module_id': 'm', 'position': 0}], [{'id': 'm', 'position': 0}],
        [question('current-q')], set(),
        [{'id': 'legacy-j', 'event': event_to_dict(judgement)},
         {'id': 'legacy-c', 'event': event_to_dict(close)}],
    )
    assert book.module('m').state == 'passed'
    assert book._historical_points['p']['state'] == 'green'


def test_retired_bank_rows_do_not_block_completed_point_only_module():
    book=MasteryBook.from_course_records(
        [{'id':'p','module_id':'m','position':0}], [{'id':'m','position':0}],
        [question('retired-q') | {'role':'retired'}], {'p'}, [],
    )
    assert book.check_ready()
    assert book.module('m').state=='passed'
    assert book.next_check_questions()==[]  # Retired rows never enter the final pool.
    assert book.module('m').state == 'passed'


def test_explicit_grant_is_not_counted_as_a_learner_answer_or_check():
    book = MasteryBook([PointRef('p', 'm')], [question('q'), question('q-unasked')])
    wrong = Judgement('wrong', 1, NOW, 'module_test', 'm', 'attempt', (('p', False),), question_id='q')
    close = AttemptClosed('closed', 2, 'm', 'attempt', 'module_test')
    book.apply(wrong)
    book.apply(close, details={'assessment_id': 'attempt', 'question_ids': ['q']})
    grant = Judgement('manual:grant:q', 3, NOW, 'check', 'm', results=(('p', True),), question_id='q')
    details = {'user_override': True, 'override_kind': 'question_passed', 'grant_id': 'grant', 'module_id': 'm'}
    book.apply(grant, details=details)

    assert book.module('m').state == 'passed'
    assert book.question('q').module_correct is True
    assert book.stats()['questions']['q']['module_errors'] == 1  # Historical error remains.
    assert book.question('q').ever_wrong is True
    assert book.question('q').check_questions == 0
    assert book.stats()['check_questions'] == 0
    unasked_grant = Judgement('manual:grant:unasked', 4, NOW, 'check', 'm',
                              results=(('p', True),), question_id='q-unasked')
    book.apply(unasked_grant, details={'user_override': True, 'override_kind': 'question_passed',
                                       'grant_id': 'grant-unasked', 'module_id': 'm'})
    assert book.question('q-unasked').passed is True
    assert book.question('q-unasked').module_correct is None  # No fictitious detection result.

    # The replay-position grant unlocks later genuine check answers. It must
    # not count the grant itself as an answer or erase the prior wrong result.
    real_check = Judgement('real-check-after-grant', 5, NOW, 'check', 'm',
                           results=(('p', True),), question_id='q')
    book.apply(real_check)
    assert book.question('q').check_questions == 1
    assert book.question('q').check_correct == 1
    assert book.stats()['check_questions'] == 1
    real_check_again = Judgement('real-check-after-grant-2', 6, NOW, 'check', 'm',
                                 results=(('p', True),), question_id='q')
    book.apply(real_check_again)
    assert book.question('q').check_questions == 2
    assert book.question('q').passed is True

    final = Judgement('manual:final:q-unasked', 7, NOW, 'check', 'm', results=(('p', True),), question_id='q-unasked')
    book.apply(final, details={'user_override': True, 'override_kind': 'final_check_passed',
                               'grant_id': 'final', 'module_id': 'm'})
    assert book.check_complete()
    assert book.question('q').check_questions == 2
    assert len([e for e in book.to_dict()['events'] if e['type'] == 'judgement']) == 6

