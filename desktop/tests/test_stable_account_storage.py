import os
from pathlib import Path
import pytest

from desktop.account_identity import AccountIdentity
from desktop.account_storage import resolve_account_storage, require_migration_target, durable_path
from desktop.account_vault import AccountVaultManager, VaultLayout
from desktop.local_migration import import_migration_archive, MigrationError
from desktop.account_portability import import_account_package, AccountPortabilityError

ISSUER = 'https://localhost:18080/realms/mentorflow'


def profile(subject='fixture-subject'):
    return AccountIdentity(ISSUER,subject,'Same display name').profile()


def test_default_is_durable_and_legacy_namespace_is_preserved(monkeypatch):
    monkeypatch.delenv('MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY')
    target = resolve_account_storage(profile())
    assert target.root == Path('E:/MentorFlowData/accounts') / profile().issuer_hash / profile().subject_hash
    assert target.target_storage_stable and not target.test_only
    assert target.account_id == profile().account_id
    assert profile('other-subject').account_id != target.account_id
    assert require_migration_target(target.root, profile=profile()).target_storage_stable
    with pytest.raises(ValueError): require_migration_target(target.root, profile=profile('other-subject'))


def test_test_only_process_cannot_claim_real_migration_readiness_on_either_root():
    durable = resolve_account_storage(profile()).root
    with pytest.raises(ValueError, match='TEST_ONLY'):
        require_migration_target(durable, profile=profile())


@pytest.mark.parametrize('root', ['E:/Codex/Temp/fixture', 'E:/installer-staging/app',
    'E:/Codex/Data/worktrees/8831/MentorFlow/data', 'E:/build/candidate', 'E:/artifacts/release'])
def test_disposable_roots_are_not_production_storage(root):
    assert not durable_path(Path(root))
    with pytest.raises(ValueError): resolve_account_storage(profile(), root=Path(root))
    target = resolve_account_storage(profile(), root=Path(root), test_only=True)
    assert target.test_only and not target.target_storage_stable


def test_real_imports_reject_temp_before_read_or_write_even_under_test_environment(tmp_path):
    target = tmp_path/'must-not-exist'/'account'
    with pytest.raises(ValueError, match='TARGET_STORAGE_STABLE=NO'):
        require_migration_target(target)
    with pytest.raises(MigrationError, match='TARGET_STORAGE_STABLE=NO'):
        import_migration_archive(tmp_path/'nonexistent.zip', target)
    with pytest.raises(AccountPortabilityError):
        import_account_package(tmp_path/'nonexistent.pack', target,
            expected_account_identity={'issuer':ISSUER,'subject':'fixture-subject'})
    assert not target.parent.exists()
    assert not require_migration_target(target, test_only=True).target_storage_stable


def test_explicit_test_mode_mount_never_reports_ready(tmp_path):
    with pytest.raises(ValueError):
        AccountVaultManager(VaultLayout(tmp_path, test_only=False)).prepare_profile(profile())
    assert list(tmp_path.iterdir()) == []
    layout = VaultLayout(tmp_path, test_only=True)
    manager = AccountVaultManager(layout)
    assert manager.prepare_profile(profile()) == layout.resolve(profile()).root
    assert not layout.resolve(profile()).target_storage_stable


def test_source_environment_controls_single_resolver(tmp_path):
    env = {'MENTORFLOW_ACCOUNT_VAULT_ROOT':str(tmp_path), 'MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY':'1', 'MENTORFLOW_TEST_SCRATCH_ROOT':os.environ.get('MENTORFLOW_TEST_SCRATCH_ROOT','')}
    layout = VaultLayout.from_env(env)
    assert layout.root_for_profile(profile()) == resolve_account_storage(profile(),root=tmp_path,test_only=True).root
    assert not layout.resolve(profile()).target_storage_stable
    env.pop('MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY')
    with pytest.raises(ValueError): VaultLayout.from_env(env).root_for_profile(profile())


def test_device_root_keeps_existing_accepted_layout_and_marks_fixtures(tmp_path):
    from desktop.account_storage import device_storage_root, prelaunch_storage_environment, DEFAULT_ROOT
    assert device_storage_root({}) == DEFAULT_ROOT
    env = {'LOCALAPPDATA':str(tmp_path), 'MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY':'1', 'MENTORFLOW_TEST_SCRATCH_ROOT':os.environ.get('MENTORFLOW_TEST_SCRATCH_ROOT','')}
    legacy = tmp_path/'MentorFlow'/'Accounts'/'personal'
    assert device_storage_root(env, legacy_profile='personal') == DEFAULT_ROOT
    legacy.mkdir(parents=True)
    assert device_storage_root(env, legacy_profile='personal') == legacy
    bindings = prelaunch_storage_environment(legacy)
    assert bindings['MENTORFLOW_ACCOUNT_VAULT_ROOT'] == str(legacy)
    assert not VaultLayout.from_env({**env, **bindings}).resolve(profile()).target_storage_stable
    env.pop('MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY')
    with pytest.raises(ValueError): device_storage_root(env, legacy_profile='personal')


def test_unc_target_rejected_before_filesystem_access(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail('UNC path must not trigger filesystem access')
    monkeypatch.setattr(Path, 'lstat', forbidden)
    with pytest.raises(ValueError): require_migration_target(Path('//untrusted/share/account'))


def test_desktop_requires_server_account_key_for_public_identity():
    import asyncio
    import httpx
    from desktop.enterprise_oidc import DesktopOidcConfig, EnterpriseOidcClient, EnterpriseOidcError, ValidatedTokenIdentity
    new = 'https://auth.clannad.win/realms/mentorflow'
    config = DesktopOidcConfig(issuer=new, identity_issuer=ISSUER,
        client_id='mentorflow-desktop', audience='mentorflow-api', api_base_url='https://api.example.test')
    identity = AccountIdentity(ISSUER, 'fixture-subject', 'Changed label')
    for key in (identity.account_id, None, profile('wrong-subject').account_id):
        def handler(request):
            assert request.headers['authorization'] == 'Bearer fixture-only'
            if request.url.path.endswith('/workspaces'):
                return httpx.Response(200,json={'workspaces':[{'id':'workspace','organization_id':'org',
                    'title':'Fixture', 'status':'active','initialization_status':'READY'}],
                    'default_workspace_id':'workspace','initialization_required':False})
            return httpx.Response(200,json={'authenticated':True,
                'account':{'subject_id':'fixture-subject','account_id':key},
                'context':{'organization_id':'org','workspace_id':'workspace'}})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                client = EnterpriseOidcClient(config,http_client=http)
                if key == identity.account_id:
                    result, _ = await client._server_state(ValidatedTokenIdentity(new,'fixture-subject','Display'), 'fixture-only')
                    assert result.account_id == identity.account_id
                    assert result.issuer_hash == identity.issuer_hash and result.subject_hash == identity.subject_hash
                else:
                    with pytest.raises(EnterpriseOidcError):
                        await client._server_state(ValidatedTokenIdentity(new,'fixture-subject','Display'), 'fixture-only')
        asyncio.run(run())
