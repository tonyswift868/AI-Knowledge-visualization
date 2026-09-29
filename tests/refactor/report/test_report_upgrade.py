"""Report graphs, comment evidence and PDF use the same saved course events."""
from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactor.learning.course.report_comments import (
    CommentFormatError, build_request, evidence_for_report, generate_comments, parse_comments,
)
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


def test_comment_request_uses_original_excerpt_and_judgement_and_parser_rejects_extra_claim(completed):
    store, report, point = completed
    evidence = evidence_for_report(store, 'p', report)
    first = next(row for row in evidence if row['point_id'] == point)
    assert first['source_excerpts'] == ['有依据的知识点。']
    assert [(j['context'], j['correct']) for j in first['judgements'][:2]] == [('casual', False), ('module_test', False)]
    system, messages = build_request(evidence)
    assert '只返回 JSON' in system and '有依据的知识点。' in messages[0]['content']
    answer = json.dumps({'comments': [{'point_id': row['point_id'], 'action': 'review'} for row in evidence]})
    comments = parse_comments(answer, evidence)
    assert '随口问曾错 1 次' in comments[point]
    assert '模块检测曾错 1 次' in comments[point]
    assert '现已通过' in comments[point]
    for bad in ('{"comments":[]}', answer.replace('"review"', '"发明错误"', 1),
                answer.replace('"action": "review"', '"action": "review", "claim": "曾答错"', 1)):
        with pytest.raises(CommentFormatError):
            parse_comments(bad, evidence)


def test_model_failure_leaves_comment_unavailable_without_changing_report(completed):
    store, report, _ = completed
    class Failing:
        def complete(self, *args, **kwargs):
            raise RuntimeError('offline model failure')
    before = store.load_report('p')
    comments = generate_comments(Failing(), store, 'p', report)
    assert all(value is None for value in comments.values())
    assert store.load_report('p') == before


def test_pdf_has_chart_and_content_stream(completed, tmp_path):
    from PySide6.QtGui import QGuiApplication
    from refactor.learning.course.report_pdf import render_report_pdf
    if QGuiApplication.instance() is None:
        os.environ['QT_QPA_PLATFORM'] = 'windows'
    app = QGuiApplication.instance() or QGuiApplication([])
    _, report, _ = completed
    target = tmp_path / 'report.pdf'
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
