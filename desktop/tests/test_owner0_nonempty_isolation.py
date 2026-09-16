"""Synthetic nonempty data qualification. No live server or formal data access.

The former history failure is a mandatory ordinary regression.
"""
import copy
import hashlib
import json
from pathlib import Path
from scripts.test_storage_policy import configured_test_temp_root
import tempfile
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QListWidget

from desktop.account_identity import AccountIdentity, AccountPreferenceStore
from desktop.account_metadata_sync import AccountMetadataSyncError, AccountMetadataSyncService
from desktop.account_vault import AccountVaultManager, VaultLayout
from desktop.local_asset_lifecycle import LocalAssetLifecycle
from desktop.main_window import MainWindow
from desktop.startup_flow import AccountBootstrapResult, classify_device
from desktop.tests.test_login_smooth import app_context  # shared isolated Qt fixture

ISSUER = 'https://identity.example.test/realms/mentorflow'


class MetadataApi:
    """Only the network port is synthetic; real Vault and binding code are used."""
    def __init__(self, payload):
        self.payload = payload
        self.writes = []
        self.closed = False

    def account_metadata(self):
        assert not self.closed
        return copy.deepcopy(self.payload)

    def put_account_progress(self, payload):
        assert not self.closed
        self.writes.append(copy.deepcopy(payload))
        return copy.deepcopy(payload)

    def close(self):
        self.closed = True


def metadata(identity, source):
    name = identity.subject
    return {
        'schema_version': 'mentorflow.account-core-metadata.v1',
        'context_contract': 'enterprise-context.v1',
        'account': {'issuer': identity.issuer, 'subject_id': name},
        'tenant': {'organization_id': identity.organization_id, 'workspace_id': identity.workspace_id},
        'workspace': {'workspace_id': identity.workspace_id, 'title': 'Workspace ' + name},
        'plans': [{'plan_id': 'plan-' + name, 'title': 'Only ' + name, 'archived': name == 'b', 'status': 'archived' if name == 'b' else 'active', 'resource_ids': ['asset-' + name]}],
        'progress': [{'snapshot_id': 'progress-' + name, 'plan_id': 'plan-' + name, 'resource_id': 'asset-' + name, 'mastery': .2 if name == 'a' else .9}],
        'learning_metadata': {'resource_id': 'asset-' + name, 'evidence_revision': 'rev-' + name},
        'resource_manifest': {'schema_version': 'mentorflow.asset-manifest.read.v1', 'assets': [{
            'asset_id': 'asset-' + name, 'asset_revision_id': 'revision-' + name,
            'filename': name + '.txt', 'mime_type': 'text/plain', 'size_bytes': source.stat().st_size,
            'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'import_mode': 'LINKED',
            'portability': 'NONPORTABLE', 'availability': 'AVAILABLE',
        }]},
    }


def test_nonempty_a_b_a_restart_real_vault_sync_and_bindings(tmp_path):
    application = QApplication.instance() or QApplication([])
    scratch = configured_test_temp_root()
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='o0-', dir=scratch) as temporary:
        root = Path(temporary)
        identities = {s: AccountIdentity(ISSUER, s, s, 'org-' + s, 'ws-' + s) for s in ('a', 'b')}
        manager = AccountVaultManager(VaultLayout(root / 'device'))
        previous_vault = None
        api_by_subject = {}
        remembered_settings = QSettings(str(root / 'preferences.ini'), QSettings.Format.IniFormat)
        preferences = AccountPreferenceStore(remembered_settings)
        try:
            for subject in ('a', 'b', 'a'):
                identity = identities[subject]
                source = root / (subject + '.txt')
                source.write_bytes(('synthetic only ' + subject).encode())
                vault = manager.mount(identity.profile())
                if previous_vault is not None:
                    assert previous_vault.closed
                previous_vault = vault
                lifecycle = LocalAssetLifecycle(vault, account_identity={'issuer': ISSUER, 'subject': subject})
                api = MetadataApi(metadata(identity, source))
                api_by_subject[subject] = api
                service = AccountMetadataSyncService(api, lifecycle, account_identity=identity)
                snapshot = service.sync()
                assert snapshot.plans[0]['plan_id'] == 'plan-' + subject
                assert snapshot.plans[0]['archived'] is (subject == 'b')
                assert snapshot.progress[0]['mastery'] == (.2 if subject == 'a' else .9)
                service.relink(resource_id='asset-' + subject,
                    expected_fingerprint_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), selected_file=source)
                binding = lifecycle.resolve_resource_binding('asset-' + subject)
                assert binding.local_path == source.absolute()
                assert ('personal-local' not in vault.root.parts)
                service.push_progress(snapshot.progress[0])
                assert api.writes == [snapshot.progress[0]]
                # Real presentation method, with a synthetic server snapshot only.
                target = QListWidget()
                view = SimpleNamespace(bootstrap_snapshot=snapshot,
                    include_archived_plans=SimpleNamespace(isChecked=lambda: True))
                MainWindow._show_account_plan_metadata(view, target)
                assert target.count() == 1 and ('Only ' + subject) in target.item(0).text()
                assert '进度记录 1 项' in target.item(0).text()
                assert ('Only ' + ('b' if subject == 'a' else 'a')) not in target.item(0).text()
                view.include_archived_plans.isChecked = lambda: False
                MainWindow._show_account_plan_metadata(view, target)
                assert ('没有可显示' in target.item(0).text()) is (subject == 'b')
                assert str(root) not in json.dumps(snapshot.server_metadata)
                other = identities['b' if subject == 'a' else 'a']
                api.payload = metadata(other, source)
                with pytest.raises(AccountMetadataSyncError):
                    service.sync()
                preferences.save_target(identity.profile(), remember_me=True, auto_login=True)
                assert preferences.load_unvalidated() == identity.profile()
                assert preferences.login_options() == (True, True)
                api.close()
                target.deleteLater()
            manager.unmount()
            restarted = AccountVaultManager(VaultLayout(root / 'device'))
            try:
                profile = AccountPreferenceStore(QSettings(str(root/'preferences.ini'), QSettings.Format.IniFormat)).load_unvalidated()
                restored = restarted.mount(profile)
                lifecycle = LocalAssetLifecycle(restored, account_identity={'issuer': ISSUER, 'subject': 'a'})
                assert lifecycle.resolve_resource_binding('asset-a').local_path == (root/'a.txt').absolute()
                assert all(api.closed for api in api_by_subject.values())
            finally:
                restarted.unmount()
            assert not (root / 'device' / 'personal-local').exists()
        finally:
            manager.unmount()


def test_recent_history_must_not_cross_accounts(tmp_path):
    # Isolated real QSettings INI: never write the user's global registry settings.
    settings = QSettings(str(tmp_path / 'shared-shape.ini'), QSettings.Format.IniFormat)
    a = AccountIdentity(ISSUER, 'a', 'a').profile()
    b = AccountIdentity(ISSUER, 'b', 'b').profile()
    preferences = AccountPreferenceStore(settings)
    preferences.save_target(a)
    settings.setValue('analogy/recent_cards', json.dumps([{'summary': 'PRIVATE-A-RECENT', 'concept_key': 'concept-a'}]))
    settings.sync()
    preferences.save_target(b)
    from desktop.runtime import record_enterprise_account_session, clear_enterprise_account_session
    record_enterprise_account_session(b, {'authenticated': True, 'context': {'organization_id': 'org-b', 'workspace_id': 'ws-b'}})
    try:
        loaded = MainWindow._load_recent_analogy_cards(SimpleNamespace(settings=settings, account_profile=b))
        assert loaded == [], 'A recent-history content was loaded in B context; stop this acceptance chain'
        assert 'PRIVATE-A-RECENT' in settings.value('analogy/recent_cards')
    finally:
        clear_enterprise_account_session()


def test_late_nonempty_a_bootstrap_closes_only_a_while_b_remains_mounted(app_context, monkeypatch):
    target, qt, _ = app_context
    bound = []
    monkeypatch.setattr('desktop.runtime.bind_desktop_profile_data_root', lambda profile: bound.append(profile))
    with tempfile.TemporaryDirectory(prefix='o0late-', dir=configured_test_temp_root()) as temporary:
        root = Path(temporary)
        results = {}
        try:
            for subject in ('a', 'b'):
                identity = AccountIdentity(ISSUER, subject, subject, 'org-' + subject, 'ws-' + subject)
                manager = AccountVaultManager(VaultLayout(root / 'device'))
                vault = manager.mount(identity.profile())
                source = root / (subject + '.txt')
                source.write_bytes(('late synthetic ' + subject).encode())
                api = MetadataApi(metadata(identity, source))
                lifecycle = LocalAssetLifecycle(vault, account_identity={'issuer': ISSUER, 'subject': subject})
                sync = AccountMetadataSyncService(api, lifecycle, account_identity=identity)
                snapshot = sync.sync()
                results[subject] = AccountBootstrapResult(identity, identity.profile(), {}, snapshot,
                    manager, lifecycle, sync, api, classify_device(snapshot))
            stale, current = results['a'], results['b']
            a_vault, b_vault = stale.vault_manager.current, current.vault_manager.current
            target.bootstrap_result = current
            target._background_succeeded(f'{target._login_generation - 1}:bootstrap', stale)
            assert stale.metadata_api.closed and a_vault.closed
            assert not current.metadata_api.closed and not b_vault.closed
            assert target.bootstrap_result is current
            assert current.snapshot.plans[0]['plan_id'] == 'plan-b'
            assert current.snapshot.progress[0]['mastery'] == .9
            assert current.metadata_api.writes == [] and stale.metadata_api.writes == []
            assert bound == [] and target.main is None
            assert not (root / 'device' / 'personal-local').exists()
        finally:
            target.bootstrap_result = None
            for result in results.values():
                result.close()
