"""V1.1 presentation acceptance; fixtures never launch the MentorFlow product."""
import copy
import json
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton

from tools.dev_workbench import authority as a, targets as t
from tools.dev_workbench import window as ui
from tools.dev_workbench.presentation import summarize, short_path
from tools.dev_workbench.resources import Resources
from tools.dev_workbench.test_workbench import app, source, candidate, approve, raw


@pytest.fixture
def window(app, source, candidate):
    for name in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf'):
        font = Path('C:/Windows/Fonts') / name
        if font.exists(): QFontDatabase.addApplicationFont(str(font))
    result = ui.Workbench(lambda: source, candidate[1], auto_load=False)
    yield result
    deadline = time.monotonic() + 5
    while result.jobs and time.monotonic() < deadline: QTest.qWait(10)
    assert not result.jobs
    result.close()


def render(window, app, view, width=1100, height=900):
    window.show_view(view); window.resize(width, height); window.show()
    for _ in range(4): app.processEvents()


def primary_text(window):
    return '\n'.join(widget.text() for cls in (QLabel, QPushButton)
                     for widget in window.content.findChildren(cls) if widget.isVisible())


@pytest.mark.parametrize('status,expected', [
    ('RUNNING', '进行中'), ('SILENT', '待命'), ('BLOCKED', '已阻塞'),
    ('USER_ACTION_REQUIRED', '等待你操作'), ('PASS_STAGE', '本阶段已完成'),
    ('WAITING_FOR_ARTIFACT', '等待测试版本'), ('NOT_RUN', '尚未执行'),
    ('LOCKED', '等待前置条件'), ('UNKNOWN', '状态未知'), ('FUTURE_STATE', '状态未知')])
def test_chinese_status_through_authority(source, status, expected):
    board = t.json_bytes(source.files[a.BOARD])
    board['workers']['01'].update(status=status, summary_zh='优化开发工作台')
    source.files[a.BOARD] = raw(board)
    view = a.load_view(source)
    summary = summarize(view)['workers'][1]
    assert summary['text'] == expected
    assert summary['summary'] == '优化开发工作台'
    assert view['workers'][1]['raw_status'] == status


def test_locale_and_missing_resource_fallback():
    missing = Resources('en-US', {'en-US': {'actions': {'refresh': 'Refresh'}}})
    assert missing.text('actions', 'refresh') == 'Refresh'
    assert missing.text('statuses', 'RUNNING') == '进行中'
    assert missing.text('statuses', 'NEW') == '状态未知'
    assert Resources(catalogs={}).text('empty', 'no_action') == '当前无需你操作'
    assert missing.text('labels', 'unregistered') == '暂未提供说明'
    assert missing.text('labels', 'phase') == '暂未提供说明'
    assert missing.text('errors', 'ARTIFACT_HASH_MISMATCH') == '这个文件不是当前指定测试版本。'
    assert missing.text('errors', 'NEW') == '无法完成操作，请查看工程详情。'


def test_evidence_scopes_never_promote_source_or_partial_account_work(source):
    view = a.load_view(source)
    initial = summarize(view)
    assert len(initial['workflow']) == 5
    assert [row['status'] for row in initial['workflow']][:3] == ['NOT_RUN', 'UNKNOWN', 'UNKNOWN']
    queue = t.json_bytes(source.files[a.QUEUE])
    queue['tasks'] += [dict(task_id=key, state=state, workbench_visible=True) for key, state in (
        ('REAL_NATIVE_LOGIN', 'PASS'), ('OLD_DATA_CONTINUITY', 'RUNNING'),
        ('REAL_SMTP_TRANSPORT', 'PASS'), ('RESET_PASSWORD_UI', 'PASS'))]
    source.files[a.QUEUE] = raw(queue)
    view = a.load_view(source)
    rows = summarize(view)['workflow']
    assert [(r['id'], r['status']) for r in rows[:3]] == [
        ('login', 'PASS'), ('continuity', 'RUNNING'), ('account', 'UNKNOWN')]
    assert rows[0]['text'] == '✓ 已完成' and rows[1]['text'] == '● 进行中'
    assert all(r['text'][0] in '✓●!○?' for r in rows)
    view['formal_main3'] = 'NOT_CREATED'
    assert summarize(view)['workflow'][-1]['text'] == '○ 未创建'


def test_known_blocker_remains_visible_while_waiting_for_artifact(window, app, source, candidate):
    # Declare this test's waiting state instead of inheriting whichever real
    # CURRENT candidate happens to be registered in the integration checkout.
    waiting = copy.deepcopy(candidate[0])
    waiting['status'] = 'WAITING_FOR_ARTIFACT'
    source.files[a.TARGET] = raw(waiting)
    view = a.load_view(source)
    view['workers'][1]['status'] = 'BLOCKED'
    render(window, app, view)
    assert window.overall.text() == '整体状态：已阻塞'
    assert window.blockers.text() == '当前阻塞：员工 01'
    assert window.blockers.isVisible()
    assert window.target_title.text() == '等待测试版本生成'


@pytest.mark.parametrize('running', [True, False])
def test_no_action_is_explicit_without_inventing_live_work(window, app, source, running):
    view = a.load_view(source)
    if not running:
        for worker in view['workers']: worker['status'] = 'SILENT'
    render(window, app, view)
    assert window.action_text.text() == '当前无需你操作'
    assert not window.action_button.isVisible()
    assert ('员工正在继续处理任务' in window.criteria.text()) is running


def test_unknown_action_is_not_reported_as_no_action(window, app, source):
    source.files[a.BOARD] = b'{broken'
    render(window, app, a.load_view(source))
    assert window.action_text.text() == '暂未确认是否需要你操作'
    assert '当前无需你操作' not in primary_text(window)


@pytest.mark.parametrize('task_id,expected', [
    ('REAL_SMTP_TRANSPORT', '邮件传输已完成'), ('REAL_QQ_DELIVERY', '邮件投递已完成')])
def test_mail_evidence_does_not_complete_account_lifecycle(window, app, source, task_id, expected):
    view = a.load_view(source)
    for row in view['workflow']:
        if row['task_id'] == task_id: row['status'] = 'PASS'
    render(window, app, view)
    assert window.workflow_note.text().startswith(expected)
    assert window.summary['workflow'][2]['status'] == 'UNKNOWN'


@pytest.mark.parametrize('state,expected', [
    ('CURRENT', '可以测试'), ('WAITING_FOR_ARTIFACT', '等待测试版本生成'),
    (None, '当前没有需要测试的版本'), ('SUPERSEDED', '当前没有需要测试的版本')])
def test_three_exclusive_test_target_presentations(window, app, source, candidate, state, expected):
    approve(source, candidate)
    view = a.load_view(source, candidate[1])
    if state is None: view['active_target'] = None
    else: view['active_target']['status'] = state
    if state != 'CURRENT': view['actions'] = []
    render(window, app, view)
    assert window.target_title.text() == expected
    assert window.launch_button.isVisible() is (state == 'CURRENT')
    assert window.launch_button.isEnabled() is (state == 'CURRENT')
    assert window.nav_buttons['directory'].isVisible() is (state == 'CURRENT')
    alternatives = {'可以测试', '等待测试版本生成', '当前没有需要测试的版本'} - {expected}
    assert all(text not in primary_text(window) for text in alternatives)


def test_user_action_is_primary_and_matches_its_launch_target(window, app, source, candidate):
    value = candidate[0]
    value.update(title='请测试真实登录', user_action='使用指定测试账号完成登录。', purpose='确认登录后可以进入工作区。')
    approve(source, candidate)
    view = a.load_view(source, candidate[1])
    view['actions'].insert(0, dict(title='其他验收', instruction='另一个操作'))
    render(window, app, view)
    assert window.action_text.text() == '请测试真实登录'
    assert window.criteria.text() == value['user_action']
    assert window.action_button.isVisible() and window.action_button.objectName() == 'primary'
    assert window.launch_button.objectName() == 'secondary'
    assert window.action_button.text() == '启动测试版本'
    assert len([b for b in window.content.findChildren(QPushButton)
                if b.isVisible() and b.objectName() == 'primary']) == 1
    assert not window.details_dialog.isVisible()


def test_nonlaunch_action_opens_registered_instructions(window, app, source):
    view = a.load_view(source)
    view['actions'] = [dict(title='请确认验收结果', instruction='检查已有回执。', success_criteria='人工确认')]
    render(window, app, view)
    QTest.mouseClick(window.action_button, Qt.MouseButton.LeftButton)
    assert window.details_dialog.isVisible()
    assert '人工确认' in window.details.toPlainText()


@pytest.mark.parametrize('width,height', [(1100, 900), (640, 600), (320, 700), (320, 480)])
def test_narrow_layout_hides_engineering_noise_and_preserves_details(window, app, source, candidate, width, height):
    approve(source, candidate)
    view = a.load_view(source, candidate[1])
    task_id = 'MF_MAIN3_LONG_TASK_' + 'EXACT_ID_' * 40
    full_path = 'E:/Codex/Temp/' + 'long-folder/' * 30 + 'Candidate.exe'
    view['workers'][1].update(task_id=task_id, objective='准备测试版本，任务标识：' + task_id)
    view['active_target'].update(artifact_path=full_path, launch_target=full_path)
    before = copy.deepcopy(view)
    render(window, app, view, width, height)
    assert window.width() == width and window.height() == height
    assert len(window.sections) == 5
    assert window.scroll.horizontalScrollBar().maximum() == 0
    assert all(card.width() <= window.scroll.viewport().width() for card in window.sections)
    assert window.integration.width() >= 140
    if width == 1100:
        assert window.integration.height() <= 40
        # The accepted two-authority UI adds a qualified-candidate row. Its
        # lower status cards may scroll; both launch entries must stay visible.
        for control in (window.launch_button, window.qualified_button):
            position = control.mapTo(window.scroll.viewport(), QPoint())
            assert position.y() >= 0
            assert position.y() + control.height() <= window.scroll.viewport().height()
    text = primary_text(window)
    for hidden in (task_id, full_path, view['integration_sha'], view['integration_tree'],
                   'UNKNOWN', 'TASK_ID', 'source_sha', '0036_account_import_ledger', 'secret://fixture/login'):
        assert hidden not in text
    assert text.count(view['integration_sha'][:8]) == 1
    assert not window.details_dialog.isVisible()
    QTest.mouseClick(window.engineering_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert window.details_dialog.isVisible()
    details = json.loads(window.details.toPlainText())
    assert details['integration_sha'] == view['integration_sha']
    assert details['source_schema'] == '0036_account_import_ledger'
    assert details['active_target']['artifact_path'] == full_path
    assert details['workers'][1]['task_id'] == task_id
    assert window.details_dialog.width() <= width
    close = next(b for b in window.details_dialog.findChildren(QPushButton) if b.text() == '收起详情')
    QTest.mouseClick(close, Qt.MouseButton.LeftButton)
    assert not window.details_dialog.isVisible()
    window.worker_rows[1][0].click()
    assert json.loads(window.details.toPlainText())['task_id'] == task_id
    assert view == before
    window.copy_integration()
    assert app.clipboard().text() == view['integration_sha']
    assert short_path(full_path) == 'E:\\…\\Candidate.exe'


@pytest.mark.parametrize('button_name', ['action_button', 'launch_button'])
@pytest.mark.parametrize('tampered', [False, True])
def test_both_launch_buttons_reverify_fresh_target_and_hash(window, app, source, candidate, monkeypatch, button_name, tampered):
    approve(source, candidate)
    render(window, app, a.load_view(source, candidate[1]))
    spawn = Mock(return_value=SimpleNamespace(pid=777))
    real_launch = t.launch_exact
    verify = Mock(side_effect=lambda *args, **kwargs: real_launch(*args, **kwargs, spawn=spawn))
    monkeypatch.setattr(ui, 'launch_exact', verify)
    if tampered: Path(candidate[0]['artifact_path']).write_bytes(b'MZ altered after rendering')
    QTest.mouseClick(getattr(window, button_name), Qt.MouseButton.LeftButton)
    deadline = time.monotonic() + 5
    while window.jobs and time.monotonic() < deadline: QTest.qWait(10)
    assert not window.jobs
    assert verify.call_count == 1
    if tampered:
        spawn.assert_not_called()
        assert window.notice.text() == '这个文件不是当前指定测试版本。'
        assert window.last_error == 'ARTIFACT_HASH_MISMATCH'
    else:
        spawn.assert_called_once()
        assert window.notice.text() == '测试版本已启动，请按测试说明完成验收。'


def test_error_localization_preserves_code_only_in_details(window, app, source):
    render(window, app, a.load_view(source))
    window.failure('FUTURE_INTERNAL_ERROR')
    assert 'FUTURE_INTERNAL_ERROR' not in primary_text(window)
    window.engineering_details()
    assert json.loads(window.details.toPlainText())['last_error'] == 'FUTURE_INTERNAL_ERROR'


def test_canonical_target_details_keep_redaction_without_mutating_identity(window, app, source, candidate):
    secret = 'fixture-private-canary'
    candidate[0]['purpose'] = '测试说明 password=' + secret
    source.files[a.TARGET] = raw(candidate[0])
    view = a.load_view(source, candidate[1])
    before = copy.deepcopy(view)
    render(window, app, view)
    window.version_details()
    assert secret not in window.details.toPlainText()
    details = json.loads(window.details.toPlainText())
    assert details['artifact_sha256'] == candidate[0]['artifact_sha256']
    assert details['secret_ref'] == 'secret://fixture/login'
    assert view == before
