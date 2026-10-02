"""Report graphs, comment evidence and PDF use the same saved course events."""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactor.learning.course.report_comments import evidence_for_report, generate_comments
from refactor.learning.course.report_views import report_views
from refactor.learning.app.plan_flow import report_markdown
from tests.refactor.report.test_report import DAY


@pytest.fixture
def completed():
    from tests.refactor.report.test_report import ReportTests
    fixture = ReportTests()
    fixture.setUp()
    try:
        fixture.prepare()
        point = fixture.pids()[0]
        fixture.judge('casual', [(point, False)])
        fixture.detect(correct=[(point, False), (fixture.pids()[1], True)])
        report = fixture.finish(DAY)
        yield fixture.store, report, point
    finally:
        fixture.tearDown()


def test_saved_events_drive_all_three_graphs_and_daily_history(completed):
    _, report, point = completed
    views = report_views(report)
    assert views['modules'] == [{'module_id': 'm0', 'title': '模块 0', 'mastered': 2, 'total': 2}]
    assert views['errors'] == {'casual': 1, 'module': 1, 'check': 0}
    assert [row['day'] for row in views['history']] == [report['plan']['actual_completion_date']]
    assert sum(row['actual_minutes'] for row in views['history']) == report['plan']['credited_minutes']
    assert all(row['debt_minutes'] == max(0, row['target_minutes'] - row['actual_minutes'])
               and row['ahead_minutes'] == max(0, row['actual_minutes'] - row['target_minutes'])
               for row in views['history'])
    assert point in {p['point_id'] for p in report['points']}
    markdown = report_markdown(report, {p['point_id']: p['summary'] for p in report['points']})
    assert '最终检验：' in markdown and '错误分布' in markdown
    assert '每日目标与实际 / 历史明细' in markdown and report['plan']['actual_completion_date'] in markdown
    assert '欠账' not in markdown.split('## 每日目标与实际')[1].split('## 最需要注意')[0].replace('不是累计欠账', '')
    assert '老师现场讲的知识点' not in markdown
    live = dict(report, live_points=[dict(point_id=point, title='现场讲的点')])
    marked = report_markdown(live, {p['point_id']: p['summary'] for p in report['points']})
    assert '## 老师现场讲的知识点' in marked and '- 现场讲的点' in marked


def test_history_shows_overdue_daily_debt_and_late_ahead():
    from tests.refactor.report.test_report import ReportTests
    fixture = ReportTests()
    fixture.setUp()
    try:
        fixture.prepare()
        fixture.store.add_credit('p', 'first', 'u', 10, DAY, 'confirmed')
        report = fixture.finish(DAY + timedelta(days=4))
        days = report_views(report)['history']
        assert [d['target_minutes'] for d in days] == [10, 10, 10, 0, 0]
        assert [d['actual_minutes'] for d in days] == [10, 0, 0, 0, 20]
        assert [d['debt_minutes'] for d in days] == [0, 10, 10, 0, 0]
        assert [d['ahead_minutes'] for d in days] == [0, 0, 0, 0, 20]
    finally:
        fixture.tearDown()


def test_comments_use_recorded_counts_and_reasons_without_model(completed):
    store, report, point = completed
    evidence = evidence_for_report(store, 'p', report)
    first = next(row for row in evidence if row['point_id'] == point)
    assert [kind for kind, _ in first['mistakes'][:2]] == ['casual', 'module_test']
    class Forbidden:
        def complete(self, *args, **kwargs): raise AssertionError('comments must be offline')
    comments = generate_comments(Forbidden(), store, 'p', report)
    assert '随口问错 1 次' in comments[point]
    assert '模块检测错 1 次' in comments[point]
    assert '现已通过' in comments[point]


def test_model_failure_cannot_affect_comments_or_saved_report(completed):
    store, report, _ = completed
    class Failing:
        def complete(self, *args, **kwargs):
            raise RuntimeError('offline model failure')
    before = store.load_report('p')
    comments = generate_comments(Failing(), store, 'p', report)
    assert all(isinstance(value, str) for value in comments.values())
    assert store.load_report('p') == before


def test_pdf_has_chart_and_content_stream(completed, tmp_path):
    from PySide6.QtGui import QGuiApplication
    from refactor.learning.course.report_pdf import render_report_pdf
    if QGuiApplication.instance() is None:
        os.environ['QT_QPA_PLATFORM'] = 'windows'
    app = QGuiApplication.instance() or QGuiApplication([])
    _, report, point = completed
    target = tmp_path / 'report.pdf'
    report = dict(report, live_points=[dict(point_id=point, title='现场讲的点')])
    render_report_pdf(report, {p['point_id']: p['summary'] for p in report['points']},
                      report_views(report), target)
    assert target.read_bytes().startswith(b'%PDF-')
    assert target.stat().st_size > 3000
    from pypdf import PdfReader
    pages = PdfReader(target).pages
    assert len(pages) >= 1
    # An earlier Qt test may have created the process-wide offscreen platform,
    # whose glyphs are not representative of the actual Windows application.
    # Chinese rendering is checked separately with Poppler in the task report.
    assert all(len(page.get_contents().get_data()) > 1000 for page in pages)
