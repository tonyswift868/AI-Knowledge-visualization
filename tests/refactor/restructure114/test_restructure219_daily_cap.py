from datetime import date

import pytest

from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.restructure import apply_structure
from refactor.learning.course.store import CourseStore

DAY = date(2026, 10, 28)


@pytest.mark.parametrize('special_weight', [None, 0.5], ids=['ordinary-study-day', 'special-study-day'])
def test_restructure219_preview_uses_plan_window_daily_cap_and_preserves_events(tmp_path, special_weight):
    weights = (1,) * 7
    special = {} if special_weight is None else {DAY: special_weight}
    with CourseStore('isolated', tmp_path) as store:
        store.create_plan('p', goal='isolated preview cap',
                          window=PlanWindow(DAY, DAY, 60, weights, special), total_minutes=0)
        store.add_sources('p', [dict(id='src', kind='url', url='https://example.invalid', title='src',
            site='example.invalid', tier='official', credibility=1, adopted=True,
            chunks=[dict(chunk_id='c', text='source body', page_start=1, page_end=1, section=['chapter'])])])
        store.save_outline('p', [dict(id='stage', title='stage')],
                           [dict(id='module', stage_id='stage', title='module', prerequisites=[])])
        store.save_generated_module('p', 'module', [dict(id='old', title='old', summary='old goal',
            minutes=20, chunk_ids=['c'])], check_minutes=10, today=DAY)
        point = dict(id='new', module_id='module', position=0, title='new', summary='new goal',
            structure='concept_v1', goal='new goal', minutes=100, chunk_ids=['c'],
            subpoints=[dict(id='new-s1', title='sub', minutes=100, chunk_ids=['c'],
                           notes=[dict(text='source body', chunk_id='c')])])
        proposal = dict(plan_id='p', today=DAY.isoformat(), modules=[dict(module_id='module',
            replace_point_ids=['old'], points=[point], navigation=[])])
        history = store.records('ledger_events', 'p')

        report = apply_structure(store, 'p', proposal, dry_run=True)

        assert report['daily_cap']['minutes'] == 60
        assert report['daily_cap']['maximum_daily_minutes'] == 110
        assert report['daily_cap']['fits'] is False
        assert report['daily_cap']['over_cap_days'] == [dict(day=DAY.isoformat(), target=110, excess_minutes=50)]
        assert report['daily_cap']['capacity_minutes'] == 60
        assert store.records('ledger_events', 'p') == history


def test_restructure219_preview_capacity_counts_only_future_study_days(tmp_path):
    start = date(2026, 10, 22)
    window = PlanWindow(start, date(2026, 11, 3), 60, (0, 0, 1, 1, 0, 0, 0),
                        {date(2026, 10, 29): 0})
    with CourseStore('isolated', tmp_path) as store:
        store.create_plan('p', goal='sparse study days', window=window, total_minutes=120)
        store.add_sources('p', [dict(id='src', kind='url', url='https://example.invalid', title='src',
            site='example.invalid', tier='official', credibility=1, adopted=True,
            chunks=[dict(chunk_id='c', text='source body', page_start=1, page_end=1, section=['chapter'])])])
        store.save_outline('p', [dict(id='stage', title='stage')],
                           [dict(id='module', stage_id='stage', title='module', prerequisites=[])])
        store.save_generated_module('p', 'module', [dict(id='old', title='old', summary='old goal',
            minutes=110, chunk_ids=['c'])], check_minutes=10, today=start)
        point = dict(id='new', module_id='module', position=0, title='new', summary='new goal',
            structure='concept_v1', goal='new goal', minutes=110, chunk_ids=['c'],
            subpoints=[dict(id='new-s1', title='sub', minutes=110, chunk_ids=['c'],
                           notes=[dict(text='source body', chunk_id='c')])])
        proposal = dict(plan_id='p', today=DAY.isoformat(), modules=[dict(module_id='module',
            replace_point_ids=['old'], points=[point], navigation=[])])

        before = store.load_ledger('p')
        report = apply_structure(store, 'p', proposal, dry_run=True)

        assert {d.isoformat(): n for d, n in before.targets.items() if n} == {
            '2026-10-22': 60, '2026-10-28': 60}
        assert report['after']['overdue_to_repay'] == 60
        assert report['daily_cap']['outstanding_minutes'] == 120
        assert report['remaining_days'] == 7
        assert report['daily_cap']['capacity_minutes'] == 60
        assert report['daily_cap']['fits'] is False
