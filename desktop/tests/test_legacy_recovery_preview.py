"""Synthetic preview only: no legacy roots, vaults or servers are opened."""
from copy import deepcopy
from itertools import product
import ast
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QCheckBox

from desktop.legacy_recovery_preview import SCHEMA, READINESS_MESSAGES, RecoveryPreview, RecoverySelection
from desktop.legacy_recovery_wizard import LegacyRecoveryWizard, GUARANTEES


@pytest.fixture
def payload():
    # Only the headline 88/83/330 and path-repair 15 reflect the supplied audit.
    # Workspace splits, identifiers, dates, hashes and other conflicts are synthetic.
    return dict(schema=SCHEMA, revision='fixture-1', sequence=1, target_key='target-fixture',
        target_label='tonyswift555', data_present=True, data_loss_confirmed=False,
        summary=dict(plans=88, progress=83, materials=330),
        sources=[dict(key='primary-id', kind='primary', path='E:/MentorFlowData'),
                 dict(key='dev-id', kind='development', path='E:/aiPlay/AI-Knowledge visualization', has_differences=True),
                 dict(key='older-id', kind='older_local', path='LocalAppData')],
        workspaces=[dict(source_key='primary-id', key='workspace-secret-a', name='英语学习', plans=64,
                         materials=200, last_active='2026-09-01', ownership_proven=True),
                    dict(source_key='primary-id', key='workspace-secret-b', name='阅读计划', plans=24,
                         materials=130, ownership_proven=True)],
        conflicts=dict(identical=10, main_only=2, other_only=3, different=4, path_repair=15),
        all_content_candidates_found=True, line_ending_only=2,
        readiness={key: False for key in READINESS_MESSAGES},
        confirmed_selection=dict(sources=[], workspaces=[]),
        technical=dict(issuer_hash='issuer-fixture-secret', subject_hash='subject-fixture-secret',
                       manifest_hashes='manifest-fixture-secret', ledger_status='0'))


def choose(selection):
    selection.select_source('primary-id', True)
    selection.select_workspace(('primary-id', 'workspace-secret-a'), True)


def ready(payload, selection):
    value = deepcopy(payload)
    value['sequence'] += 1
    value['revision'] = 'fixture-2'
    value['readiness'] = dict.fromkeys(READINESS_MESSAGES, True)
    contract = selection.selection_contract()
    value['confirmed_selection'] = {k: contract[k] for k in ('sources', 'workspaces')}
    return value


@pytest.fixture
def wizard(payload, desktop_qt_session):
    view = LegacyRecoveryWizard(payload)
    view.show()
    desktop_qt_session.processEvents()
    yield view
    view.close()
    view.deleteLater()
    desktop_qt_session.processEvents()


def test_summary_and_unknown_counts(payload, wizard):
    assert wizard.step == 0
    assert wizard.discovery_message.text() == '我们找到了以前的 MentorFlow 学习数据。'
    assert wizard.presence_message.text() == '这些数据仍然存在，目前尚未导入当前账号。'
    assert wizard.loss_message.text() == '尚未发现数据丢失。'
    assert [wizard.summary_values[k].text() for k in ('plans', 'progress', 'materials')] == ['88', '83', '330']
    assert wizard.summary_values['conversations'].text() == '待确认'
    assert not wizard.selection.sources and not wizard.selection.workspaces


def test_details_are_collapsed_and_only_plain_text(wizard):
    assert not wizard.details_toggle.isChecked() and not wizard.details.isVisible()
    visible = '\n'.join(w.text() for w in wizard.findChildren(QLabel) if w.isVisible())
    for secret in ('E:/MentorFlowData', 'workspace-secret-a', 'issuer-fixture-secret', 'ledger'):
        assert secret not in visible
    wizard.details_toggle.click()
    assert wizard.details.isVisible() and wizard.details.isReadOnly()
    assert 'E:/MentorFlowData' in wizard.details.toPlainText()
    assert 'workspace-secret-a' in wizard.details.toPlainText()
    assert 'issuer-fixture-secret' in wizard.details.toPlainText()
    wizard.details_toggle.click()
    assert not wizard.details.isVisible()
    assert all(w.textFormat() == Qt.TextFormat.PlainText for w in wizard.findChildren(QLabel))


def test_sources_workspace_search_and_explicit_choices(wizard):
    wizard.show_step(1)
    assert [b.text() for b in wizard.source_boxes.values()] == ['主要历史数据', '历史开发副本', '更早的本机副本']
    assert wizard.difference_message.isVisible()
    assert '需要先确认合并方式' in wizard.difference_message.text()
    wizard.source_boxes['primary-id'].click()
    wizard.show_step(2)
    assert wizard.workspace_table.rowCount() == 2
    assert not wizard.selection.workspaces
    wizard.search.setText('英语')
    assert wizard.workspace_table.rowCount() == 1
    wizard.select_all.click()
    assert wizard.selection.workspaces == {('primary-id', 'workspace-secret-a')}
    wizard.search.clear()
    wizard.workspace_table.item(1, 0).setCheckState(Qt.CheckState.Checked)
    assert len(wizard.selection.workspaces) == 2
    wizard.workspace_table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert len(wizard.selection.workspaces) == 1
    wizard._clear_workspaces()
    assert not wizard.selection.workspaces
    wizard.select_all.click()
    wizard.source_boxes['primary-id'].setChecked(False)
    assert not wizard.selection.workspaces and wizard.workspace_table.rowCount() == 0


def test_conflicts_and_non_optional_guarantees(wizard):
    wizard.show_step(3)
    assert '15 条资料原路径已经失效，但系统仍找到了对应内容候选' in wizard.path_message.text()
    assert [v.text().split('：')[0] for v in wizard.conflict_values.values()] == [
        '完全相同', '只存在于主要数据', '只存在于其他副本', '同一记录但内容不同', '资料路径需要修复']
    assert any('换行格式差异' in w.text() for w in wizard.findChildren(QLabel))
    wizard.show_step(4)
    assert [v.text() for v in wizard.guarantee_labels] == ['✓ ' + text for text in GUARANTEES]
    assert not wizard.stack.currentWidget().findChildren(QCheckBox)


@pytest.mark.parametrize('flags', list(product((False, True), repeat=4)))
def test_every_readiness_condition_is_required(payload, flags):
    model = RecoverySelection(RecoveryPreview.from_mapping(payload))
    choose(model)
    value = ready(payload, model)
    value['readiness'] = dict(zip(READINESS_MESSAGES, flags))
    model.replace_preview(RecoveryPreview.from_mapping(value))
    assert model.can_start is all(flags)
    assert model.confirm_preview() is all(flags)


@pytest.mark.parametrize('wrong', [None, 1, 'true', [], {}])
def test_readiness_never_coerces_truthy_values(payload, wrong):
    model = RecoverySelection(RecoveryPreview.from_mapping(payload))
    choose(model)
    value = ready(payload, model)
    value['readiness']['TARGET_STORAGE_STABLE'] = wrong
    model.replace_preview(RecoveryPreview.from_mapping(value))
    assert not model.can_start


@pytest.mark.parametrize('field,value', [('summary', {'plans': -1}), ('summary', {'plans': True}),
    ('sources', None), ('workspaces', [{'source_key': 'missing', 'key': 'x'}]),
    ('confirmed_selection', {'sources': ['missing']}), ('sequence', None), ('sequence', True),
    ('target_key', ''), ('technical', {'issuer_hash': {'nested': 'invalid'}})])
def test_malformed_contract_is_controlled(payload, field, value):
    payload[field] = value
    preview = RecoveryPreview.from_mapping(payload)
    assert not preview.valid
    model = RecoverySelection(preview)
    assert not model.can_start and not model.confirm_preview()
    assert '无法读取迁移预览' in model.blockers[0]


def test_ownership_selection_and_target_changes_fail_closed(payload):
    payload['workspaces'][0]['ownership_proven'] = False
    model = RecoverySelection(RecoveryPreview.from_mapping(payload))
    assert not model.workspaces
    choose(model)
    value = ready(payload, model)
    model.replace_preview(RecoveryPreview.from_mapping(value))
    assert not model.can_start and any('归属' in s for s in model.blockers)
    value['sequence'] += 1
    value['workspaces'][0]['ownership_proven'] = True
    model.replace_preview(RecoveryPreview.from_mapping(value))
    assert model.can_start
    model.select_workspace(('primary-id', 'workspace-secret-b'), True)
    assert not model.can_start
    value['target_key'] = 'another-target'
    model.replace_preview(RecoveryPreview.from_mapping(value))
    assert not model.sources and not model.workspaces and not model.can_start


def test_fresh_preview_confirmation_has_no_execution(payload, wizard):
    original = deepcopy(payload)
    requests = []
    wizard.preview_requested.connect(requests.append)
    wizard.source_boxes['primary-id'].setChecked(True)
    wizard.workspace_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    wizard.refresh_preview.click()
    assert requests == [wizard.selection.selection_contract()]
    wizard.show_step(5)
    assert not wizard.start_button.isEnabled()
    assert '测试数据目录' in wizard.blocker_message.text()
    assert '账号身份地址' in wizard.blocker_message.text()
    value = ready(payload, wizard.selection)
    assert wizard.apply_preview(value)
    assert wizard.start_button.isEnabled()
    wizard.start_button.click()
    assert wizard.selection.preview_confirmed
    assert '尚未导入任何数据' in wizard.execution_message.text()
    assert payload == original
    assert not wizard.apply_preview(payload)  # Late response must not replace newer projection.
    assert wizard.preview.sequence == 2
    assert wizard.apply_preview(None)
    assert not wizard.start_button.isEnabled()
    assert '无法读取迁移预览' in wizard.blocker_message.text()


def test_close_exits_preview_without_confirmation(wizard):
    wizard.close()
    assert not wizard.isVisible()
    assert not wizard.selection.preview_confirmed


@pytest.mark.parametrize('field,value', [('data_present', False), ('data_present', 'true'),
    ('data_loss_confirmed', True), ('data_loss_confirmed', None), ('data_loss_confirmed', 0)])
def test_uncertain_data_integrity_cannot_be_ready(payload, field, value):
    model = RecoverySelection(RecoveryPreview.from_mapping(payload))
    choose(model)
    update = ready(payload, model)
    update[field] = value
    model.replace_preview(RecoveryPreview.from_mapping(update))
    assert not model.can_start


def test_synchronous_producer_response_clears_waiting_message(payload, wizard):
    wizard.source_boxes['primary-id'].setChecked(True)
    wizard.workspace_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    wizard.preview_requested.connect(lambda _: wizard.apply_preview(ready(payload, wizard.selection)))
    wizard.refresh_preview.click()
    assert wizard.preview.sequence == 2
    assert wizard.preview_request_status.text() == ''


def test_missing_candidate_proof_does_not_claim_candidates(payload, wizard):
    payload['sequence'] = 2
    payload['all_content_candidates_found'] = False
    wizard.apply_preview(payload)
    assert '内容候选仍待确认' in wizard.path_message.text()
    assert '仍找到了' not in wizard.path_message.text()


def test_source_boundary_and_no_hardcoded_audit():
    root = Path(__file__).parents[1]
    allowed = {'dataclasses', 'typing', 'PySide6.QtCore', 'PySide6.QtWidgets', 'legacy_recovery_preview', 'style'}
    for filename in ('legacy_recovery_preview.py', 'legacy_recovery_wizard.py'):
        source = (root / filename).read_text(encoding='utf-8')
        tree = ast.parse(source)
        imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert set(imports) <= allowed
        assert not any(isinstance(n, ast.Import) for n in ast.walk(tree))
        assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and
                       n.func.id in {'open', 'eval', 'exec', '__import__'} for n in ast.walk(tree))
        assert 'tonyswift555' not in source and 'E:/MentorFlowData' not in source
        assert not any(isinstance(n, ast.Constant) and type(n.value) is int and n.value in (88, 83, 330)
                       for n in ast.walk(tree))
