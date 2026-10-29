"""Ready-tree optimization retains immutable file and rollback guarantees."""
from contextlib import contextmanager
import json

import pytest

from refactor.desktop.delivery import per_user_install as install
from refactor.desktop.delivery import per_user_update_core as core
from refactor.desktop.delivery import prepared_update as prepared
from refactor.desktop.delivery.update_protocol import SecurityError
from tests.refactor.delivery.test_delivery_per_user_install import case
from tests.refactor.delivery.test_delivery_one_click_update_core import setup


def test_prepared239_one_locked_source_inventory_no_rehash(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    core.prepare_update(root, service=service)
    source = service.staged
    inventory = prepared.inventory
    scanned = []

    @contextmanager
    def measured(app, release):
        scanned.append(app)
        with inventory(app, release) as values:
            yield values

    monkeypatch.setattr(prepared, 'inventory', measured)
    monkeypatch.setattr(prepared, 'ready', lambda *_: pytest.fail('redundant optimistic scan'))
    monkeypatch.setattr(install, '_copy', lambda *a: pytest.fail('ready tree copied'))
    monkeypatch.setattr(install, 'verify_tree', lambda *a, **k: pytest.fail('ready tree rehashed'))
    result = install.transact(root, source, service.release.envelope, prepared=True)
    assert result['status'] == 'HEALTHY'
    assert scanned.count(source) == 1
    assert len(scanned) == 3  # locked source, after move, immediately before activation
    with (root / 'Data/logs/launcher-startup.jsonl').open(encoding='utf8') as stream:
        events = [json.loads(line) for line in stream]
    stages = {e['stage'] for e in events if e['phase'] == 'install_stage_finished'}
    assert {'prepared_inventory_locked', 'moved_inventory', 'activation_identity_recheck',
            'initial_signed_admission', 'locked_signed_admission', 'new_app_health'} <= stages
    assert any(e['phase'] == 'prepared_inventory_result' and e['reused'] for e in events)


def test_prepared239_changed_receipt_fails_hash_and_keeps_current(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    core.prepare_update(root, service=service)
    source = service.staged
    (source / 'MentorFlow.exe').write_bytes(b'changed')
    with pytest.raises(SecurityError):
        install.transact(root, source, service.release.envelope, prepared=True)
    assert install.installed(root).version == '1.4.0'
    assert source.exists()


def test_prepared239_failed_health_restores_previous(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    core.prepare_update(root, service=service)

    def bad_health(*_):
        raise RuntimeError('health unavailable')

    with pytest.raises(RuntimeError, match='health unavailable'):
        install.transact(root, service.staged, service.release.envelope,
                         prepared=True, health=bad_health)
    assert install.installed(root).version == '1.4.0'
    assert json.loads((root / install.JOURNAL).read_bytes())['phase'] == 'ROLLED_BACK'
