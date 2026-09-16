"""Source-release inclusion checks; no Desktop build or live Keycloak claims."""
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
THEME = 'deploy/enterprise/themes/mentorflow-desktop-v1'
COMPOSE = 'deploy/enterprise/staging/rp05/compose.account-core-mvp.yml'
REALM = 'deploy/enterprise/config/keycloak-realm.json'
READY = 'deploy/enterprise/staging/rp05/keycloak-ready.sh'


def test_exact_source_release_contains_complete_mounted_login_theme():
    # Test the staged candidate tree during integration, and the identical index
    # after commit. Archive only these release inputs, never credentials/data.
    tree = subprocess.check_output(['git', 'write-tree'], cwd=ROOT).decode().strip()
    # Exact-source release export must not inherit the reviewing Windows
    # checkout's core.autocrlf conversion (the Linux source uses Git blobs).
    data = subprocess.check_output(['git', '-c', 'core.autocrlf=false', 'archive', '--format=tar', tree, '--',
                                    THEME, COMPOSE, REALM, READY], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        files = {member.name: archive.extractfile(member).read()
                 for member in archive.getmembers() if member.isfile()}
    tracked = subprocess.check_output(['git', 'ls-files', '--', THEME], cwd=ROOT).decode().splitlines()
    assert tracked and set(tracked) <= files.keys()
    for name in tracked:
        exact_blob = subprocess.check_output(['git', 'show', ':' + name], cwd=ROOT)
        assert files[name] == exact_blob
    required = ['login/theme.properties', 'login/login.ftl', 'login/resources/css/mentorflow.css',
                'login/messages/messages_en.properties', 'login/messages/messages_zh_CN.properties']
    assert all(THEME + '/' + name in files for name in required)
    compose = yaml.safe_load(files[COMPOSE])
    mount = next(item for item in compose['services']['keycloak']['volumes']
                 if ':/opt/keycloak/themes/mentorflow-desktop-v1:' in item)
    source, target, mode = mount.split(':')
    assert (ROOT / PurePosixPath(COMPOSE).parent / source).resolve() == (ROOT / THEME).resolve()
    assert mode == 'ro'
    assert target == '/opt/keycloak/themes/mentorflow-desktop-v1'
    realm = json.loads(files[REALM])
    desktop = next(client for client in realm['clients'] if client['clientId'] == 'mentorflow-desktop')
    assert desktop['attributes']['login_theme'] == PurePosixPath(THEME).name
    assert compose['services']['keycloak-ready']['environment']['MENTORFLOW_DESKTOP_LOGIN_THEME'] == PurePosixPath(THEME).name
    assert b'login_theme' in files[READY]


def test_single_migration_head_is_workspace_bootstrap_without_connecting_database():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    config = Config()
    config.set_main_option('script_location', str(ROOT / 'backend/alembic'))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ['0040_admin_overview_aggregate']
    assert '0035_account_workspace_bootstrap' in {r.revision for r in scripts.walk_revisions()}
