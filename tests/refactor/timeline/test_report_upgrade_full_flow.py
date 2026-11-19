"""Fixed offline plan → classroom → final check → report graph/PDF path."""
from __future__ import annotations

import os
import json
from pathlib import Path
from types import SimpleNamespace

from test_final_check_time_full_flow import flow_env, prepare, skip_all, check_answer, join_profiles, PLAN
from refactor.learning.course.report_views import report_views
from refactor.learning.course.report_pdf import render_report_pdf
from refactor.learning.course.report_comments import evidence_for_report


REPORT_ROOT = Path(os.environ.get('REPORT_UPGRADE_ROOT', 'D:/MentorFlow/.local/test-tmp/report-upgrade'))
ARTIFACT = REPORT_ROOT / 'report.pdf'


def test_full_flow_report_charts_match_saved_events_and_export_pdf(flow_env, monkeypatch):
    e = flow_env
    prepare(e)
    skip_all(e)
    check_answer(e, wrong=True)
    while not e.store.load_mastery(PLAN).check_complete():
        check_answer(e)
    join_profiles()
    report = e.flow.report(PLAN)['report']
    assert e.store.load_report(PLAN) == report
    views = report_views(report)
    assert [d['day'] for d in views['history']] == [report['plan']['actual_completion_date']]
    assert sum(d['actual_minutes'] for d in views['history']) == report['plan']['credited_minutes']
    assert all(m['mastered'] == m['total'] for m in views['modules'])
    assert views['errors']['casual'] == sum(p['casual_errors'] for p in report['points'])
    assert views['errors']['module'] == sum(p['module_errors'] for p in report['points'])
    assert views['errors']['check'] == sum(p['check_questions'] - p['check_correct'] for p in report['points'])
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.instance() is None:
        monkeypatch.setenv('QT_QPA_PLATFORM', 'windows')
    app = QGuiApplication.instance() or QGuiApplication([])
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    render_report_pdf(report, {p['id']: p.get('title') or p['summary'] for p in e.store.records('points', PLAN)},
                      views, ARTIFACT)
    (ARTIFACT.parent / 'sample.json').write_text(json.dumps({
        'report': report,
        'views': views,
        'titles': {p['id']: p.get('title') or p['summary'] for p in e.store.records('points', PLAN)},
    }, ensure_ascii=False), encoding='utf-8')
    (ARTIFACT.parent / 'evidence.json').write_text(json.dumps(
        evidence_for_report(e.store, PLAN, report), ensure_ascii=False), encoding='utf-8')
    assert ARTIFACT.read_bytes().startswith(b'%PDF-')


def test_report_comment_batch_saves_once_without_changing_course_state(flow_env):
    e = flow_env
    prepare(e)
    skip_all(e)
    check_answer(e, wrong=True)
    while not e.store.load_mastery(PLAN).check_complete():
        check_answer(e)
    join_profiles()

    class FakeGateway:
        calls = 0
        def complete(self, purpose, messages, **kwargs):
            self.calls += 1
            raise AssertionError('报告评语不得调用模型')

    fake = FakeGateway()
    e.flow.services.gateway = fake
    before = (e.store.get_plan(PLAN), e.store.records('judgements', PLAN),
              e.store.records('credits', PLAN))
    first = e.flow.report(PLAN)['report']
    assert fake.calls == 0
    assert e.flow.report(PLAN)['report'] == first
    assert fake.calls == 0
    assert e.store.load_report(PLAN) == first
    assert len(first['point_comments']) == len(first['points'])
    assert any('最终检验错 1 次' in text for text in first['point_comments'].values())
    assert (e.store.get_plan(PLAN), e.store.records('judgements', PLAN),
            e.store.records('credits', PLAN)) == before
