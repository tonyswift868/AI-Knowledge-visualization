"""Remote identity, cache integrity and failure feedback, without user data."""
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock
import pytest
from tools.dev_workbench.branch_launch import BranchLauncher, LaunchError


def test_remote_default_is_not_integration_or_local_head(tmp_path):
    b=BranchLauncher(cache=tmp_path)
    b.git=Mock(return_value='ref: refs/heads/main2\tHEAD\n'+'a'*40+'\tHEAD\n'+'b'*40+'\trefs/heads/control/main3-current')
    assert b.resolve('release') == dict(channel='release',branch='main2',sha='a'*40)
    assert b.resolve('development')['sha']=='b'*40
    b.git.return_value='ref: refs/heads/main4\tHEAD\n'+'c'*40+'\tHEAD'
    assert b.resolve('release')['branch']=='main4'
    with pytest.raises(LaunchError):b.resolve('development')


def test_no_network_no_silent_fallback(tmp_path):
    b=BranchLauncher(cache=tmp_path);b.git=Mock(side_effect=LaunchError('Git unavailable'))
    with pytest.raises(LaunchError,match='unavailable'):b.resolve('release')


def test_source_cache_missing_or_modified_is_not_reused(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    def git(*a):return subprocess.check_output(['git','-C',str(repo),*a]).decode().strip()
    git('init');git('config','user.name','test');git('config','user.email','test@example.invalid')
    (repo/'entry.py').write_text('print(1)\n');git('add','.');git('commit','-m','source')
    sha=git('rev-parse','HEAD');b=BranchLauncher(root=repo,cache=tmp_path)
    source=tmp_path/'source';source.mkdir();(source/'entry.py').write_bytes(git('show',sha+':entry.py').encode()+b'\n')
    assert b.source_matches(source,sha)
    (source/'entry.py').write_text('print(2)\n');assert not b.source_matches(source,sha)
    (source/'entry.py').unlink();assert not b.source_matches(source,sha)


def test_stable_profiles_isolate_data_and_preserve_service_config(tmp_path,monkeypatch):
    monkeypatch.setenv('MENTORFLOW_USER_DATA_ROOT','E:/MentorFlowData')
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_ISSUER','https://service.example.invalid/realm')
    b=BranchLauncher(cache=tmp_path)
    a=b.environment(tmp_path/'development');r=b.environment(tmp_path/'release')
    assert a['MENTORFLOW_USER_DATA_ROOT']!=r['MENTORFLOW_USER_DATA_ROOT']
    assert a['MENTORFLOW_DESKTOP_OIDC_ISSUER'].startswith('https://service.')
    marker=tmp_path/'release/data/keep';marker.write_text('retained')
    assert b.environment(tmp_path/'release')==r and marker.read_text()=='retained'
    assert 'MENTORFLOW_TEST_RUNTIME_CONFIGURATION' not in a


def test_failed_child_reports_selected_version_not_success(tmp_path,monkeypatch):
    b=BranchLauncher(cache=tmp_path,profile_base=tmp_path/'profiles')
    b.resolve=Mock(return_value=dict(channel='release',branch='main2',sha='a'*40))
    source=tmp_path/'source';source.mkdir();b.prepare=Mock(return_value=source)
    child=Mock(pid=123,returncode=7);child.poll.return_value=7
    monkeypatch.setattr('tools.dev_workbench.branch_launch.subprocess.Popen',Mock(return_value=child))
    with pytest.raises(LaunchError,match='启动退出（7）.*未回退'):
        b.launch('release')
    assert b.resolve.call_count==1 and b.prepare.call_count==1

def test_old_branch_does_not_render_new_account_page(tmp_path):
    b=BranchLauncher(cache=tmp_path)
    source=tmp_path/'source';source.mkdir()
    b.run=Mock();b.prepare_lifecycle(source,lambda _:None)
    b.run.assert_not_called()


def test_missing_renderer_dependencies_have_actionable_error(tmp_path,monkeypatch):
    b=BranchLauncher(cache=tmp_path);source=tmp_path/'source'
    (source/'scripts').mkdir(parents=True);(source/'scripts/render_account_lifecycle_ui.py').write_text('')
    monkeypatch.setenv('MENTORFLOW_WORKBENCH_KEYCLOAK',str(tmp_path/'missing'))
    monkeypatch.setenv('MENTORFLOW_WORKBENCH_JDK',str(tmp_path/'missing'))
    with pytest.raises(LaunchError,match='Keycloak 26.7.3.*JDK'):
        b.prepare_lifecycle(source,lambda _:None)


def test_normal_launch_has_canonical_identity_and_verified_tls(tmp_path, monkeypatch):
    monkeypatch.setenv('MENTORFLOW_DESKTOP_ISSUER_MIGRATION_JSON', '{"stale":true}')
    monkeypatch.setenv('MENTORFLOW_ACCOUNT_MVP_TEST', '1')
    for key in ('MENTORFLOW_DESKTOP_OIDC_ISSUER','MENTORFLOW_DESKTOP_IDENTITY_ISSUER'):
        monkeypatch.delenv(key, raising=False)
    env = BranchLauncher(cache=tmp_path).environment(tmp_path/'profile')
    assert env['MENTORFLOW_DESKTOP_IDENTITY_ISSUER']=='https://localhost:18080/realms/mentorflow'
    assert env['MENTORFLOW_DESKTOP_OIDC_ISSUER']=='https://auth.clannad.win/realms/mentorflow'
    assert env['MENTORFLOW_ENTERPRISE_API_BASE_URL']=='https://89.45.45.28'
    assert 'MENTORFLOW_DESKTOP_ISSUER_MIGRATION_JSON' not in env
    assert 'MENTORFLOW_ACCOUNT_MVP_TEST' not in env
    import ssl
    ssl.create_default_context(cafile=env['SSL_CERT_FILE'])
