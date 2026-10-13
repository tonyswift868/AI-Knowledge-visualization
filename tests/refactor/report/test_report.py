import copy
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.refactor.mastery70.support import seed_assessment
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.mastery import AttemptClosed, Judgement
from refactor.learning.course.report import build_report, SCHEMA
from refactor.learning.course.store import CourseStore, StoreError


DAY = date(2026, 10, 1)
TZ = timezone(timedelta(hours=9))
SCRATCH = Path('D:/MentorFlow/.local/test-tmp/course-report')


class ReportTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.root = Path(self.temp.name)
        self.stores = []
        self.store = self.open()
        self.seq = 0

    def tearDown(self):
        for store in reversed(self.stores):
            store.close()
        self.temp.cleanup()

    def open(self, suffix=''):
        store = CourseStore('account', self.root / suffix)
        self.stores.append(store)
        return store

    def prepare(self, counts=(2,), *, end=2, start=True, record_start=True):
        s = self.store
        s.create_plan('p', goal='学会基础', final_goal='独立实践', level='入门', notes='每天学习',
                      window=PlanWindow(DAY, DAY + timedelta(days=end), 200))
        s.add_sources('p', [dict(id='s', kind='url', url='https://example.invalid',
                                chunks=[dict(chunk_id='c', text='有依据的知识点。')])])
        s.save_outline('p', [dict(id='stage', title='基础阶段')],
                       [dict(id=f'm{i}', stage_id='stage', title=f'模块 {i}') for i in range(len(counts))])
        for i, count in enumerate(counts):
            s.save_generated_module('p', f'm{i}', [dict(id=f'p{i}-{j:02}', summary=f'点 {i}-{j}',
                minutes=10, chunk_ids=['c']) for j in range(count)], check_minutes=10, today=DAY)
        seed_assessment(s, 'p')
        if start:
            s.start_plan('p', **({'today': DAY} if record_start else {}))
        return s

    def pids(self, module='m0'):
        return [p['id'] for p in self.store.records('points', 'p') if p['module_id'] == module]

    def judge(self, context, results, *, module='m0', attempt=None, day=DAY, angle='recall'):
        if len(results)>1:
            for pid,correct in results:
                self.judge(context, [(pid,correct)],module=module,attempt=attempt,day=day,angle=angle)
            return
        self.seq += 1
        event = Judgement(f'j{self.seq:03}', self.seq, datetime.combine(day, time(12), TZ), context,
                          module, attempt, tuple(results), question_id=results[0][0] if context not in ('casual','quick_confirm') else None)
        self.store.append_judgement('p', event)
        return event

    def detect(self, *, module='m0', correct=None, kind='module_test', day=DAY):
        pids = self.pids(module)
        results = [(p, True) for p in pids] if correct is None else correct
        attempt = f'a{self.seq + 1}'
        if results:
            self.judge(kind, results, module=module, attempt=attempt, day=day)
        self.seq += 1
        event = AttemptClosed(f'close{self.seq:03}', self.seq, module, attempt, kind)
        self.store.append_attempt_closed('p', event)
        return event

    def pass_checks(self, day=DAY):
        s = self.store
        if s.get_plan('p')['state'] == 'active':
            s.start_checking('p')
        # Answer every pending question; past errors require two correct answers.
        while not s.load_mastery('p').check_complete():
            book = s.load_mastery('p')
            for module in s.records('modules', 'p'):
                pending = [(pid, True) for pid in self.pids(module['id']) if book.point(pid).level < 3]
                if pending:
                    self.judge('check', pending, module=module['id'], day=day)

    def finish(self, day=DAY, *, credit=True):
        for module in self.store.records('modules', 'p'):
            if self.store.load_mastery('p').module(module['id']).state not in ('passed', 'skipped'):
                self.detect(module=module['id'], day=day)
        self.pass_checks(day)
        if credit:
            remaining = self.store.load_ledger('p').status(day).remaining_confirmed_minutes
            if remaining > 0:
                self.store.add_credit('p', 'finish', 'all', remaining, day, 'confirmed')
        self.store.complete_plan('p', today=day)
        return build_report(self.store, 'p', today=day)

    def dump(self):
        return '\n'.join(self.store._db.iterdump())

    def test_live_points_only_lists_actual_live_teaching(self):
        self.prepare()
        first, second = self.pids()
        self.store.save_teacher_profile('p', {'teaching_points': {first: {'point_id': first, 'chunk_ids': ['c1']}}})
        report = self.finish()
        self.assertEqual([p['point_id'] for p in report['live_points']], [first])
        self.assertTrue(report['live_points'][0]['title'])

    def test_on_time_overview_daily_confirmed_tentative_and_exact_counts(self):
        s = self.prepare()
        s.add_credit('p', 'first', 'first-unit', 10, DAY)
        self.detect(day=DAY + timedelta(days=1))
        s.confirm_credits('p', 'first-unit', today=DAY + timedelta(days=1), event_id='confirm')
        s.add_credit('p', 'extra', 'extra-unit', 5, DAY + timedelta(days=1))
        report = self.finish(DAY + timedelta(days=2))
        overview = report['plan']
        self.assertEqual(report['schema'], SCHEMA)
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual((overview['goal'], overview['final_goal']), ('学会基础', '独立实践'))
        self.assertEqual((overview['start_date'], overview['end_date']), ('2026-10-01', '2026-10-03'))
        self.assertEqual((overview['actual_start_date'], overview['actual_completion_date']), ('2026-10-01', '2026-10-03'))
        self.assertEqual((overview['early_days'], overview['overdue_days']), (0, 0))
        self.assertEqual((overview['planned_minutes'], overview['credited_minutes'], overview['confirmed_minutes'], overview['tentative_minutes']), (30, 35, 30, 5))
        days = report['time']['days']
        self.assertEqual([r['target_minutes'] for r in days], [10, 10, 10])
        self.assertEqual([r['confirmed_minutes'] for r in days], [10, 10, 10])
        self.assertEqual([r['tentative_minutes'] for r in days], [0, 0, 0])
        self.assertEqual([r['credited_confirmed_minutes'] for r in days], [10, 0, 20])
        self.assertEqual([r['credited_tentative_minutes'] for r in days], [0, 5, 0])
        self.assertEqual((report['time']['debt_peak_minutes'], report['time']['ahead_peak_minutes']), (5, 0))
        self.assertEqual((report['time']['learning_days'], report['time']['overflow_minutes']), (3, 5))
        self.assertTrue(report['time']['peak_dates_exact'])
        self.assertIsNone(report['teacher_profile'])
        self.assertEqual(s.records('reports', 'p'), [{'id': 'p', 'document': report}])
        for point in report['points']:
            self.assertEqual((point['check_questions'], point['check_correct'], point['check_accuracy']), (1, 1, 1.0))
            self.assertEqual((point['casual_errors'], point['module_errors'], point['check_errors_before_pass']), (0, 0, 0))

    def test_early_finish_is_stable_across_repeated_generation_reopen_and_later_today(self):
        s = self.prepare()
        first = self.finish()
        self.assertEqual((first['plan']['early_days'], first['plan']['overdue_days']), (2, 0))
        self.assertEqual((first['time']['debt_peak_minutes'], first['time']['ahead_peak_minutes']), (0, 20))
        self.assertEqual(first['time']['learning_days'], 1)
        before = self.dump()
        s.complete_plan('p', today=DAY + timedelta(days=9))
        second = build_report(self.open(), 'p', today=DAY + timedelta(days=9))
        self.assertEqual(first, second)
        self.assertEqual(json.dumps(first, ensure_ascii=False), json.dumps(second, ensure_ascii=False))
        self.assertEqual(self.dump(), before)

    def test_overdue_finish_preserves_late_days_when_ledger_now_says_zero(self):
        self.prepare()
        self.store.add_credit('p', 'first', 'u', 10, DAY, 'confirmed')
        finish = DAY + timedelta(days=4)
        report = self.finish(finish)
        self.assertEqual(self.store.load_ledger('p').status(finish).overdue_days, 0)
        self.assertEqual((report['plan']['early_days'], report['plan']['overdue_days']), (0, 2))
        self.assertEqual(report['time']['debt_peak_minutes'], 20)
        self.assertEqual(report['time']['learning_days'], 2)
        self.assertEqual([r['target_minutes'] for r in report['time']['days']], [10, 10, 10, 0, 0])
        self.assertEqual(report['time']['days'][-1]['credited_confirmed_minutes'], 20)


    def test_direct_test_nine_of_ten_skips_with_weak_point_and_estimated_credit(self):
        self.prepare((10,))
        pids = self.pids()
        self.detect(kind='skip_test', correct=[(p, i < 9) for i, p in enumerate(pids)])
        report = self.finish()
        module = report['modules'][0]
        self.assertEqual((module['state'], module['module_test_count'], module['skip_test_count']), ('skipped', 0, 1))
        self.assertTrue(module['skip_test_passed'])
        self.assertEqual(module['first_test']['mastery_ratio'], 0.9)
        self.assertEqual(report['plan']['credited_minutes'], 110)
        self.assertEqual(report['time']['ahead_peak_minutes'], 73)
        weak = report['points'][-1]
        self.assertTrue(weak['ever_weak'])
        self.assertTrue(weak['ever_red'])
        self.assertEqual((weak['module_errors'], weak['check_questions'], weak['check_accuracy']), (1, 2, 1.0))

    def test_failed_direct_test_ten_of_twelve_keeps_first_ratio_and_errors(self):
        self.prepare((12,))
        pids = self.pids()
        self.detect(kind='skip_test', correct=[(p, i < 10) for i, p in enumerate(pids)])
        self.assertEqual(self.store.load_mastery('p').module('m0').state, 'failed')
        report = self.finish()
        module = report['modules'][0]
        self.assertEqual((module['state'], module['module_test_count'], module['skip_test_count'], module['test_count']), ('passed', 1, 1, 2))
        self.assertEqual(module['first_test']['mastery_ratio'], 10 / 12)
        self.assertFalse(module['skip_test_passed'])
        self.assertEqual([p['module_errors'] for p in report['points']], [0] * 10 + [1, 1])
        self.assertTrue(all(p['ever_red'] for p in report['points'][-2:]))

    def test_weak_history_survives_later_green_and_repeated_detects(self):
        self.prepare((10,))
        pids = self.pids()
        self.detect(correct=[(p, i < 9) for i, p in enumerate(pids)])
        self.detect()
        report = self.finish()
        self.assertEqual(report['modules'][0]['module_test_count'], 2)
        self.assertEqual(report['modules'][0]['first_test']['mastery_ratio'], 0.9)
        self.assertEqual(report['points'][-1]['state'], 'green')
        self.assertTrue(report['points'][-1]['ever_weak'])

    def test_teacher_profile_is_exact_detached_original_without_evaluation(self):
        self.prepare()
        profile = {'原文': '对易错点的原始记录\n用户补充。', '点': ['甲', '乙'], 'nested': {'score': 0.4}}
        self.store.save_teacher_profile('p', profile)
        report = self.finish()
        self.assertEqual(report['teacher_profile'], profile)
        self.assertNotIn('evaluation', report)
        report['teacher_profile']['点'].append('修改返回值')
        self.assertEqual(self.store.records('teacher_profile', 'p')[0]['document'], profile)
        self.assertEqual(self.store.records('reports', 'p')[0]['document']['teacher_profile'], profile)

    def test_replan_reason_historical_revocation_frozen_fill_and_relearning_fifo(self):
        s = self.prepare()
        s.add_credit('p', 'past', 'past-unit', 10, DAY)
        next_day = DAY + timedelta(days=1)
        # Closed historical segments still exist for legacy content operations;
        # user explicit_replan/window_changed deliberately keep their FIFO live.
        s.replan_plan('p', next_day, PlanWindow(DAY, DAY + timedelta(days=4), 200),
                      reason='旧内容重排', cause='module_generated')
        frozen = s.load_ledger('p').to_dict()['history']
        s.revoke_credits('p', 'past-unit', today=next_day, event_id='revoke')
        report = self.finish(next_day)
        self.assertEqual(s.load_ledger('p').to_dict()['history'], frozen)
        self.assertEqual(report['plan']['replan_count'], 2)
        self.assertEqual([r['cause'] for r in report['plan']['replans']], ['module_generated', 'module_generated'])
        self.assertEqual(report['plan']['replans'][-1]['reason'], '旧内容重排')
        self.assertEqual((report['plan']['original_end_date'], report['plan']['end_date']), ('2026-10-03', '2026-10-05'))
        self.assertEqual(report['plan']['early_days'], 3)
        history, current = report['time']['days'][0:2]
        self.assertEqual((history['frozen'], history['target_minutes'], history['tentative_minutes']), (True, 10, 10))
        self.assertEqual((history['submitted_minutes'], history['credited_tentative_minutes']), (10, 0))
        self.assertEqual((current['target_minutes'], current['confirmed_minutes'], current['credited_confirmed_minutes']), (5, 5, 30))
        self.assertEqual(report['time']['segments'][-1]['relearning_minutes'], 10)
        self.assertEqual(report['time']['segments'][-1]['relearning_filled_minutes'], 10)
        self.assertEqual((report['time']['debt_peak_minutes'], report['time']['ahead_peak_minutes'], report['time']['history_debt_minutes']), (10, 15, 0))

    def test_replan_preserves_historical_debt_separately_from_live_peak(self):
        s = self.prepare()
        s.add_credit('p', 'past', 'u', 10, DAY + timedelta(days=1), 'confirmed')
        s.replan_plan('p', DAY + timedelta(days=2), PlanWindow(DAY, DAY + timedelta(days=4), 200),
                      cause='module_generated')
        report = self.finish(DAY + timedelta(days=3))
        self.assertEqual(report['time']['history_debt_minutes'], 10)
        self.assertEqual(report['time']['debt_peak_minutes'], 10)
        self.assertEqual(report['time']['ahead_peak_minutes'], 6)
        self.assertEqual([r['target_minutes'] for r in report['time']['days']], [10, 10, 7, 7, 6])
        self.assertEqual([r['confirmed_minutes'] for r in report['time']['days']], [10, 0, 7, 7, 6])
        self.assertEqual(report['plan']['credited_minutes'], 30)

    def test_historical_confirmation_does_not_rewrite_daily_tentative_snapshot(self):
        s = self.prepare()
        s.add_credit('p', 'past', 'u', 10, DAY)
        next_day = DAY + timedelta(days=1)
        s.replan_plan('p', next_day, cause='module_generated')
        s.confirm_credits('p', 'u', today=next_day)
        report = self.finish(next_day)
        self.assertEqual(report['time']['days'][0]['tentative_minutes'], 10)
        self.assertEqual(report['time']['days'][0]['credited_confirmed_minutes'], 10)
        self.assertEqual(report['plan']['confirmed_minutes'], 30)
        self.assertEqual(report['plan']['tentative_minutes'], 0)

    def test_old_records_report_missing_dates_and_reasons_without_fabricating_them(self):
        s = self.prepare(record_start=False)
        s.add_credit('p', 'past', 'u', 10, DAY)
        s.confirm_credits('p', 'u', event_id='legacy-confirm')
        self.finish()
        records = []
        for record in s.pending_records(999):
            payload = copy.deepcopy(record.payload)
            if record.entity == 'plans':
                payload.pop('completed_on')
            if record.entity == 'ledger_events':
                payload.pop('reason', None)
                payload.pop('cause', None)
            if record.entity != 'reports':
                records.append((replace(record, payload=payload), 1))
        other = self.open('legacy')
        other.apply_remote(list(reversed(records)))
        report = build_report(other, 'p', today=DAY + timedelta(days=3))
        self.assertEqual(report['plan']['actual_start_date'], DAY.isoformat())
        self.assertEqual(report['plan']['start_date_basis'], 'first_activity')
        self.assertIsNone(report['plan']['actual_completion_date'])
        self.assertIsNone(report['plan']['early_days'])
        self.assertIsNone(report['plan']['overdue_days'])
        self.assertIsNone(report['plan']['replans'][0]['reason'])
        self.assertIsNone(report['plan']['replans'][0]['cause'])
        self.assertFalse(report['time']['peak_dates_exact'])
        self.assertEqual(report['time']['undated_operation_ids'], ['legacy-confirm'])

    def test_plan_order_breaks_weakness_ties_and_top_list_has_ten_items(self):
        s = self.prepare((6, 6), start=False)
        s.reorder_modules('p', ['m1', 'm0'], today=DAY)
        s.start_plan('p', today=DAY)
        report = self.finish()
        ordered = self.pids('m1') + self.pids('m0')
        self.assertEqual([p['point_id'] for p in report['points']], ordered)
        self.assertEqual([p['point_id'] for p in report['weakest_points']], ordered[:10])
        self.assertEqual([m['module_id'] for m in report['modules']], ['m1', 'm0'])
        self.assertEqual(report['plan']['replans'][-1]['cause'], 'modules_reordered')

    def test_report_and_optional_metadata_round_trip_through_record_store(self):
        self.prepare()
        report = self.finish()
        other = self.open('device-b')
        other.apply_remote([(r, i + 1) for i, r in enumerate(reversed(self.store.pending_records(999)))])
        self.assertEqual(build_report(other, 'p', today=DAY), report)
        self.assertEqual(other.pending_records(999), [])
        self.store.archive_plan('p')
        self.assertEqual(build_report(self.store, 'p', today=DAY), report)
        self.store.restore_plan('p')
        self.store.delete_plan('p')
        self.assertEqual(self.store.records('reports', 'p'), [])

    def test_backdated_credit_keeps_causal_replay_and_marks_peak_date_limitation(self):
        self.prepare()
        self.store.add_credit('p', 'later', 'u', 10, DAY + timedelta(days=2), 'confirmed')
        self.store.add_credit('p', 'backdated', 'v', 20, DAY, 'confirmed')
        report = self.finish(DAY + timedelta(days=2))
        self.assertEqual(report['time']['debt_peak_minutes'], 20)
        self.assertEqual(report['time']['backdated_operation_ids'], ['backdated'])
        self.assertFalse(report['time']['peak_dates_exact'])
        self.assertEqual(report['time']['learning_days'], 2)

    def test_wrong_answers_without_credits_still_count_as_learning_day(self):
        self.prepare((1,))
        self.judge('quick_confirm', [(self.pids()[0], False)], day=DAY)
        report = self.finish(DAY + timedelta(days=2))
        self.assertEqual(report['time']['learning_days'], 2)
        self.assertEqual(report['time']['activity_dates'], ['2026-10-01', '2026-10-03'])
        self.assertEqual(report['time']['days'][0]['submitted_minutes'], 0)
        self.assertEqual(report['points'][0]['casual_errors'], 1)

    def test_revoked_active_credit_retains_observed_ahead_peak_but_not_live_total(self):
        self.prepare()
        self.store.add_credit('p', 'ahead', 'u', 30, DAY)
        self.store.revoke_credits('p', 'u', today=DAY + timedelta(days=1))
        report = self.finish(DAY + timedelta(days=2))
        self.assertEqual(report['time']['ahead_peak_minutes'], 20)
        self.assertEqual(report['time']['debt_peak_minutes'], 20)
        self.assertEqual(report['plan']['credited_minutes'], 30)
        self.assertEqual(report['time']['days'][0]['submitted_minutes'], 30)
        self.assertEqual(report['time']['days'][0]['credited_tentative_minutes'], 0)
        self.assertTrue(report['time']['peak_dates_exact'])


    def test_zero_remaining_replan_and_idle_weighted_days_are_kept(self):
        s = self.prepare(start=False)
        special = PlanWindow(DAY, DAY + timedelta(days=2), 200, date_weights={DAY + timedelta(days=1): 0})
        s.save_intake('p', window=special, today=DAY, reason='次日休息')
        s.start_plan('p', today=DAY)
        s.add_credit('p', 'all', 'all', 30, DAY, 'confirmed')
        s.replan_plan('p', DAY + timedelta(days=1), cause='module_generated')
        report = self.finish(DAY + timedelta(days=1))
        self.assertEqual([d['target_minutes'] for d in report['time']['days']], [15, 0, 0])
        self.assertEqual(report['time']['ahead_peak_minutes'], 15)
        self.assertEqual(report['time']['history_debt_minutes'], 0)
        self.assertEqual(report['plan']['replans'][1]['reason'], '次日休息')

    def test_missing_structural_sync_page_blocks_report_despite_completed_plan(self):
        self.prepare()
        self.finish()
        other = self.open('partial')
        other.apply_remote([(r, 1) for r in self.store.pending_records(999)
                            if r.entity != 'reports' and not (r.entity == 'points' and r.payload['id'] == 'p0-01')])
        with self.assertRaises(StoreError) as raised:
            build_report(other, 'p', today=DAY)
        self.assertEqual(raised.exception.code, 'PLAN_NOT_COMPLETE')
        self.assertEqual(other.records('reports', 'p'), [])

    def test_same_plan_cannot_generate_report_for_another_account(self):
        self.prepare()
        self.finish()
        with CourseStore('other-account', self.root) as other:
            with self.assertRaises(StoreError) as raised:
                build_report(other, 'p', today=DAY)
            self.assertEqual(raised.exception.code, 'NOT_FOUND')
            self.assertEqual(other.pending_records(999), [])

    def test_added_module_preserves_old_report_until_new_completion_date_is_recorded(self):
        s = self.prepare()
        previous = self.finish()
        next_day = DAY + timedelta(days=1)
        s.save_outline('p', [], [dict(id='m1', stage_id='stage', title='新增内容')])
        self.assertEqual(s.get_plan('p')['state'], 'active')
        self.assertNotIn('completed_on', s.get_plan('p'))
        self.assertEqual(s.load_report('p'), previous)
        s.save_generated_module('p', 'm1', [dict(id='new', summary='新知识点', minutes=10, chunk_ids=['c'])],
                                check_minutes=10, today=next_day)
        seed_assessment(s, 'p')
        s.add_credit('p', 'extension', 'new', 20, next_day, 'confirmed')
        latest = self.finish(next_day, credit=False)
        self.assertEqual(latest['plan']['actual_start_date'], DAY.isoformat())
        self.assertEqual(latest['plan']['actual_completion_date'], next_day.isoformat())
        self.assertEqual(latest['plan']['credited_minutes'], 50)
        self.assertEqual(s.load_report('p'), latest)

    def test_not_complete_and_stale_completed_marker_cannot_save_partial_report(self):
        s = self.prepare()
        before = self.dump()
        with self.assertRaises(StoreError):
            build_report(s, 'p', today=DAY)
        self.assertEqual(self.dump(), before)
        with s.transaction():
            plan = s.get_plan('p')
            plan['state'] = 'completed'
            s._put('plans', 'p', plan)
        before = self.dump()
        with self.assertRaises(StoreError):
            build_report(s, 'p', today=DAY)
        self.assertEqual(self.dump(), before)
        self.assertEqual(s.records('reports', 'p'), [])

    def test_report_save_failure_rolls_back_outbox_and_rows(self):
        self.prepare()
        self.finish()
        self.store.save_teacher_profile('p', {'changed': True})
        before = self.dump()
        put = self.store._put

        def fail(entity, plan_id, payload):
            result = put(entity, plan_id, payload)
            if entity == 'reports':
                raise OSError('write failed')
            return result

        with patch.object(self.store, '_put', side_effect=fail):
            with self.assertRaises(OSError):
                build_report(self.store, 'p', today=DAY)
        self.assertEqual(self.dump(), before)

    def test_large_report_saves_bounded_parts_without_truncating_profile(self):
        self.prepare()
        self.finish()
        profile = {'original': '字' * (85 * 1024)}
        self.store.save_teacher_profile('p', profile)
        report = build_report(self.store, 'p', today=DAY)
        self.assertEqual(report['teacher_profile'], profile)
        self.assertEqual(self.store.load_report('p'), report)
        self.assertGreater(len(self.store.records('reports', 'p')), 1)
        for record in self.store.pending_records(999):
            self.assertLessEqual(len(json.dumps(record.payload, ensure_ascii=False, sort_keys=True,
                                                separators=(',', ':')).encode('utf-8')), 256 * 1024)
        before = self.dump()
        self.assertEqual(build_report(self.store, 'p', today=DAY), report)
        self.assertEqual(self.dump(), before)
        # The manifest may arrive before its immutable-content parts.
        records = self.store.pending_records(999)
        part_records = [r for r in records if r.entity == 'reports' and r.payload['id'] != 'p']
        other = self.open('large-report')
        other.apply_remote([(r, 1) for r in records if r not in part_records])
        with self.assertRaises(StoreError) as raised:
            other.load_report('p')
        self.assertEqual(raised.exception.code, 'REPORT_INCOMPLETE')
        other.apply_remote([(r, 1) for r in reversed(part_records)])
        self.assertEqual(other.load_report('p'), report)
        self.store.save_teacher_profile('p', {'small': True})
        smaller = build_report(self.store, 'p', today=DAY)
        self.assertEqual(self.store.records('reports', 'p'), [dict(id='p', document=smaller)])
        self.assertEqual(self.store.load_report('p'), smaller)
        self.assertTrue(all(any(r.entity_id == old.entity_id and r.deleted for r in self.store.pending_records(999))
                            for old in part_records))

    def test_invalid_date_and_report_before_activity_make_no_changes(self):
        self.prepare()
        report = self.finish()
        before = self.dump()
        for today in (datetime.combine(DAY, time()), DAY - timedelta(days=1), True):
            with self.subTest(today=today), self.assertRaises(StoreError):
                build_report(self.store, 'p', today=today)
            self.assertEqual(self.dump(), before)
        self.assertEqual(build_report(self.store, 'p', today=DAY.isoformat()), report)


if __name__ == '__main__':
    unittest.main()
