import json
import os
from types import SimpleNamespace

import pytest

from refactor.desktop.account_closure_store import ClosureStore, KEY
from refactor.desktop.account_identity import AccountProfile, ProfileMode, issuer_hash, subject_hash


class MemorySettings:
    def __init__(self, values):
        self.values = dict(values)

    def sync(self):
        pass

    def allKeys(self):
        return list(self.values)

    def value(self, key, default=None):
        return self.values.get(key, default)


def _record(subject, label):
    issuer = issuer_hash('https://closure219.example.invalid/realm')
    subject_digest = subject_hash(subject)
    profile = AccountProfile(ProfileMode.ENTERPRISE_ACCOUNT, label, issuer, subject_digest)
    return profile.account_id, dict(
        version=1, account_id=profile.account_id, issuer_hash=issuer,
        subject_hash=subject_digest, display_name=label,
        api_base_url='https://api.example.invalid', operation_id='',
        state='PREPARE_UNKNOWN', submitted=False, local_cleaned=False,
    )


def test_records_isolate_bad_entries_and_preserve_valid_recovery_entry():
    valid_id, valid = _record('alice-219', 'Account 219')
    malformed_id, _ = _record('broken-json-219', 'Malformed')
    nested_id, _ = _record('nested-json-219', 'Nested JSON')
    schema_id, _ = _record('broken-schema-219', 'Bad schema')
    mismatch_id, mismatch = _record('mismatch-219', 'Wrong key identity')
    mismatch['subject_hash'] = subject_hash('different-subject-219')
    settings = MemorySettings({
        KEY + malformed_id: '{',
        KEY + nested_id: '[' * 1500 + ']' * 1500,
        KEY + schema_id: json.dumps({'version': 99}),
        KEY + mismatch_id: json.dumps(mismatch),
        KEY + valid_id: json.dumps(valid),
    })

    store = ClosureStore(settings)
    records = store.records()

    assert records == [valid]
    assert store.invalid_record_count == 4
    assert set(settings.values) == {
        KEY + malformed_id, KEY + nested_id, KEY + schema_id, KEY + mismatch_id, KEY + valid_id,
    }


def test_recovery_panel_keeps_good_button_and_reports_bad_entry():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    qtwidgets = pytest.importorskip('PySide6.QtWidgets')
    from refactor.desktop.account_shell_main import AccountShell

    app = qtwidgets.QApplication.instance() or qtwidgets.QApplication([])
    valid_id, valid = _record('panel-219', 'Panel Account 219')
    bad_id, _ = _record('panel-bad-219', 'Bad Panel Record')
    settings = MemorySettings({KEY + bad_id: '{', KEY + valid_id: json.dumps(valid)})
    gate = qtwidgets.QWidget()
    gate_layout = qtwidgets.QVBoxLayout(gate)
    gate.notice_label = qtwidgets.QLabel('', gate)
    gate_layout.addWidget(gate.notice_label)
    shell = SimpleNamespace(
        gate=gate, _closure_recovery=None,
        session=SimpleNamespace(preferences=SimpleNamespace(settings=settings)),
        _closure=None,
    )
    opened = []
    shell._open_closure = opened.append

    AccountShell._refresh_closure_recovery(shell)
    panel = shell._closure_recovery
    buttons = panel.findChildren(qtwidgets.QPushButton)

    assert len(buttons) == 1
    assert buttons[0].text() == '继续注销 / 本机清理：Panel Account 219'
    assert not panel.isHidden()
    assert '部分注销恢复记录无法读取' in gate.notice_label.text()
    buttons[0].click()
    app.processEvents()
    assert len(opened) == 1
    assert opened[0].account_id == valid['account_id']
    assert KEY + bad_id in settings.values and KEY + valid_id in settings.values
