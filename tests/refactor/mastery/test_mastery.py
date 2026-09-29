import json
import random
import unittest
from datetime import datetime, timezone
from dataclasses import replace

from refactor.learning.course.mastery import (
    ANGLES, AttemptClosed, Judgement, MasteryBook, MasteryError, PointRef,
)

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def judgement(seq, results, context='casual', attempt_id=None, angle=None, module='m'):
    return Judgement(str(seq), seq, NOW, context, module, attempt_id, tuple(results), angle)


def book(count=10):
    return MasteryBook([PointRef(str(i), 'm') for i in range(count)])


def close(b, count, correct, *, kind='module_test', seq=100, attempt='a'):
    b.apply(judgement(seq, [(str(i), i < correct) for i in range(count)], kind, attempt))
    b.apply(AttemptClosed(str(seq + 1), seq + 1, 'm', attempt, kind))


class MasteryTests(unittest.TestCase):
    def test_multi_point_question_counts_only_its_errors(self):
        b = book(3)
        b.apply(judgement(1, [('0', True), ('1', False)]))
        b.apply(judgement(2, [('2', False)], 'quick_confirm'))
        self.assertEqual([b.point(str(i)).casual_errors for i in range(3)], [0, 1, 1])
        self.assertEqual(b.point('1').module_errors, 0)

    def test_ninety_percent_boundaries(self):
        for total, mastered, passed in [(10, 9, True), (12, 10, False), (10, 8, False)]:
            with self.subTest(total=total, mastered=mastered):
                b = book(total); close(b, total, mastered)
                self.assertEqual(b.module('m').state, 'passed' if passed else 'failed')
                self.assertEqual(b.point(str(total - 1)).state, 'weak' if passed else 'red')
                self.assertEqual(b.point('0').state, 'green')

    def test_untested_points_not_mastered_and_all_results_must_be_correct(self):
        b = book(10)
        b.apply(judgement(1, [(str(i), True) for i in range(9)], 'module_test', 'a'))
        b.apply(judgement(2, [('0', False)], 'module_test', 'a'))
        self.assertEqual(b.module('m').state, 'untested')
        b.apply(AttemptClosed('3', 3, 'm', 'a', 'module_test'))
        self.assertEqual(b.module('m').state, 'failed')
        self.assertEqual(b.point('0').state, 'red')
        self.assertEqual(b.point('9').state, 'red')

    def test_retest_clears_only_red_and_keeps_errors(self):
        b = book(3); close(b, 3, 1)
        b.apply(judgement(102, [('1', False), ('2', True)], 'retest'))
        self.assertEqual(b.module('m').state, 'failed')
        self.assertEqual(b.point('1').module_errors, 2)
        b.apply(judgement(103, [('1', True)], 'retest'))
        self.assertEqual(b.module('m').state, 'passed')
        b.apply(judgement(104, [('0', False)], 'retest'))
        self.assertEqual(b.point('0').state, 'green')

    def test_skip_pass_and_fail(self):
        b = book(); close(b, 10, 9, kind='skip_test')
        self.assertEqual(b.module('m').state, 'skipped')
        self.assertEqual(b.point('9').state, 'weak')
        self.assertTrue(b.check_ready())
        b = book(); close(b, 10, 8, kind='skip_test')
        self.assertEqual(b.module('m').state, 'untested')
        self.assertTrue(all(b.point(str(i)).state == 'untested' for i in range(10)))
        self.assertEqual(b.point('9').module_errors, 1)
        self.assertFalse(b.check_ready())

    def test_all_modules_required_and_empty_book_is_not_complete(self):
        b = MasteryBook([PointRef('0', 'm'), PointRef('1', 'other')])
        close(b, 1, 1)
        self.assertFalse(b.check_ready()); self.assertEqual(b.next_check_items(), [])
        b.apply(judgement(102, [('1', True)], 'skip_test', 's', module='other'))
        b.apply(AttemptClosed('103', 103, 'other', 's', 'skip_test'))
        self.assertTrue(b.check_ready())
        self.assertFalse(MasteryBook([]).check_complete())

    def test_initial_levels_and_check_bounds(self):
        b = book(3)
        b.apply(judgement(1, [('0', False), ('1', False)]))
        close(b, 3, 0)
        b.apply(judgement(102, [(str(i), True) for i in range(3)], 'retest'))
        self.assertEqual([b.point(str(i)).level for i in range(3)], [1, 1, 1])
        for seq in range(103, 107):
            b.apply(judgement(seq, [('0', False)], 'check', angle='recall'))
        self.assertEqual(b.point('0').level, 1)
        for seq in range(107, 110):
            b.apply(judgement(seq, [('0', True)], 'check', angle='apply'))
        self.assertEqual(b.point('0').level, 3)
        b.apply(judgement(110, [('0', False)], 'check', angle='contrast'))
        self.assertEqual(b.point('0').level, 3)
        self.assertNotIn('0', [pid for pid, _ in b.next_check_items(3)])

    def test_alternating_answers_do_not_pass(self):
        b = book(1); close(b, 1, 1)
        for seq in range(102, 110):
            b.apply(judgement(seq, [('0', seq % 2 == 1)], 'check', angle='recall'))
        self.assertEqual(b.point('0').level, 2)
        self.assertFalse(b.check_complete())
        b.apply(judgement(110, [('0', True)], 'check', angle='apply'))
        self.assertTrue(b.check_complete())

    def test_coverage_priority_and_query_is_read_only(self):
        b = book(); b.apply(judgement(1, [('3', False)])); close(b, 10, 9)
        original = b.to_dict()
        self.assertEqual(b.next_check_items(3), [('9', 'recall'), ('3', 'recall'), ('0', 'recall')])
        self.assertEqual(b.to_dict(), original)
        b.apply(judgement(102, [('3', False)], 'check', angle='recall'))
        self.assertEqual(b.next_check_items()[0][0], '9')

    def test_gap_counts_questions_not_number_of_points(self):
        b = book(5); close(b, 5, 5)
        b.apply(judgement(102, [('0', False)], 'check', angle='recall'))
        b.apply(judgement(103, [('1', False), ('2', False), ('3', False)], 'check', angle='recall'))
        self.assertEqual(b.stats()['check_questions'], 2)
        self.assertEqual(b.next_check_items(min_gap=3)[0][0], '4')
        b.apply(judgement(104, [('4', False)], 'check', angle='recall'))
        # All candidates gap-blocked: a deterministic fallback avoids deadlock.
        self.assertEqual(b.next_check_items(min_gap=3)[0][0], '0')
        b.apply(judgement(105, [('4', False)], 'check', angle='apply'))
        self.assertEqual(b.next_check_items(min_gap=3)[0][0], '0')

    def test_angle_rotation_and_oldest_reuse(self):
        b = book(1); close(b, 1, 1)
        for i, angle in enumerate(ANGLES):
            self.assertEqual(b.next_check_items(), [('0', angle)])
            b.apply(judgement(102 + i, [('0', False)], 'check', angle=angle))
        self.assertEqual(b.next_check_items(), [('0', 'recall')])
        b.apply(judgement(108, [('0', False)], 'check', angle='recall'))
        self.assertEqual(b.next_check_items(), [('0', 'apply')])

    def test_order_independence_and_round_trip(self):
        events = [judgement(1, [('0', False)]), judgement(2, [('0', False)], 'module_test', 'a'),
                  AttemptClosed('3', 3, 'm', 'a', 'module_test'), judgement(4, [('0', True)], 'retest'),
                  judgement(5, [('0', True)], 'check', angle='recall')]
        reference = book(1)
        for event in events: reference.apply(event)
        rng = random.Random(3)
        for _ in range(20):
            rng.shuffle(events); b = book(1)
            for event in events: b.apply(event)
            restored = MasteryBook.from_dict(json.loads(json.dumps(b.to_dict())))
            self.assertEqual(restored.point('0'), reference.point('0'))
            self.assertEqual(restored.stats(), reference.stats())
            self.assertEqual(restored.to_dict(), reference.to_dict())

    def test_idempotency_conflict_and_invalid_event_are_atomic(self):
        b = book(1); event = judgement(1, [('0', False)])
        b.apply(event); b.apply(event)
        self.assertEqual(b.point('0').casual_errors, 1)
        before = b.to_dict()
        for invalid in [replace(event, results=(('0', True),)), judgement(2, [('missing', True)])]:
            with self.assertRaises(MasteryError): b.apply(invalid)
            self.assertEqual(b.to_dict(), before)
        with self.assertRaises(MasteryError): replace(event, at=NOW.replace(tzinfo=None))
        with self.assertRaises(MasteryError): replace(event, results=(('0', 1),))

    def test_stats_report_error_counts_question_accuracy_and_attempts(self):
        b = book(1); close(b, 1, 0, kind='skip_test')
        close(b, 1, 1, seq=200, attempt='b')
        b.apply(judgement(202, [('0', False)], 'check', angle='recall'))
        b.apply(judgement(203, [('0', True)], 'check', angle='apply'))
        stats = b.stats()
        self.assertEqual(stats['points']['0']['check_accuracy'], 0.5)
        self.assertEqual(stats['points']['0']['module_errors'], 1)
        self.assertEqual(stats['modules']['m'], dict(module_test_count=1, skip_test_count=1, test_count=2))

    def test_optional_context_attempt_id_is_not_a_test_attempt(self):
        b=book(1)
        b.apply(judgement(1,[('0',True)],'casual','a'))
        close(b,1,1,attempt='a')
        self.assertTrue(b.check_ready())
        with self.assertRaises(MasteryError):
            b.apply(AttemptClosed('other-close',200,'m','a','module_test'))

    def test_report_evidence_replays_out_of_order_and_queries_are_detached(self):
        b = book(1)
        events = [judgement(1, [('0', False)], 'check', angle='recall'),
                  judgement(2, [('0', False)], 'module_test', 'a'),
                  AttemptClosed('closed', 3, 'm', 'a', 'module_test'),
                  judgement(4, [('0', True)], 'retest'),
                  judgement(5, [('0', False)], 'check', angle='apply'),
                  judgement(6, [('0', True)], 'check', angle='contrast'),
                  judgement(7, [('0', True)], 'check', angle='transfer'),
                  judgement(8, [('0', False)], 'check', angle='fix_error')]
        for event in reversed(events):
            b.apply(event)
        evidence = b.report_stats()
        self.assertTrue(evidence['points']['0']['ever_red'])
        self.assertEqual(evidence['points']['0']['check_errors_before_pass'], 1)
        self.assertEqual(evidence['points']['0']['check_questions'], 4)
        self.assertEqual(evidence['modules']['m']['retest_count'], 1)
        self.assertEqual(evidence['modules']['m']['first_test']['mastery_ratio'], 0.0)
        expected = MasteryBook.from_dict(b.to_dict()).report_stats()
        self.assertEqual(expected, evidence)
        evidence['modules']['m']['first_test']['mastery_ratio'] = 1.0
        self.assertEqual(b.report_stats(), expected)


if __name__ == '__main__': unittest.main()
