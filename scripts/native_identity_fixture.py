"""Disposable real Keycloak 26.7.3 + TLS account-core + native Qt acceptance.

No admin API, token grant, SMTP, existing realm, or production DB is used.
All processes, credentials, databases and certificates belong to a fresh E: lab.
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend'), str(ROOT / 'backend/tests')]


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def certificates(home):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'MentorFlow disposable fixture CA')])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
          .not_valid_after(now+timedelta(hours=3)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
          .sign(ca_key, hashes.SHA256()))
    (home / 'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    pin = None
    for label in ('server', 'backend', 'other'):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, label)]))
                .issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=3))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False)
                .add_extension(x509.KeyUsage(True, False, label == 'server', False, False, False, False, False, False), True)
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if label == 'server' else ExtendedKeyUsageOID.CLIENT_AUTH]), False)
                .sign(ca_key, hashes.SHA256()))
        (home / f'{label}.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (home / f'{label}.key').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        if label == 'backend': pin = cert.fingerprint(hashes.SHA256()).hex()
    return pin


def backend(home, issuer, api, existing_subject):
    import httpx
    import uvicorn
    from alembic import command
    from sqlalchemy import create_engine
    from app import account_core_mvp
    from app.enterprise.contracts_v1 import TenantMode
    from app.enterprise.identity.config import EnterpriseIdentityConfig, KeycloakOidcConfig
    from app.enterprise.identity.keycloak import KeycloakOidcClient
    from app.enterprise.identity.native import NativeSessions, MtlsIdentityVerifier
    from app.enterprise.identity.runtime import EnterpriseIdentityRuntime
    from app.enterprise_tenancy.workspace_bootstrap import AccountWorkspaceBootstrap
    from app.enterprise_tenancy.workspaces import EnterpriseWorkspaceService
    from app.enterprise_tenancy.document_import import EnterpriseDocumentImportService
    import test_account_workspace_bootstrap as storage
    from test_enterprise_workspace_persistence import _config, _SQLiteTransactions, _seed_identity
    storage.ISSUER = issuer  # Test-only SQL membership directory; identity validation remains real.
    path = home / 'account-core.sqlite3'
    command.upgrade(_config(path), 'head')
    engine = create_engine(f'sqlite:///{path.as_posix()}', connect_args={'timeout': 15})
    _seed_identity(engine, organization='existing-org', workspace='existing-workspace', subject=existing_subject)
    context = ssl.create_default_context(cafile=str(home / 'ca.pem'))
    context.load_cert_chain(home / 'backend.pem', home / 'backend.key')
    config = EnterpriseIdentityConfig(mode=TenantMode.ENTERPRISE_SERVER,
        keycloak=KeycloakOidcConfig(issuer=issuer, client_id='mentorflow-desktop', audience='mentorflow-api', redirect_uri=api+'/api/enterprise/auth/callback'),
        browser_allowed_origins=(api,))
    verifier = MtlsIdentityVerifier(issuer+'/mentorflow-native-identity/v1', issuer, context=context)
    sessions = NativeSessions(verifier, issuer)
    # The legacy JWT consumer remains installed; native authentication uses the real SPI.
    legacy = KeycloakOidcClient(config)
    runtime = EnterpriseIdentityRuntime(config, client=legacy, directory=storage.Directory(engine), native_sessions=sessions)
    state = account_core_mvp.app.state
    state.enterprise_identity_runtime = runtime
    state.account_workspace_bootstrap = AccountWorkspaceBootstrap(engine, issuer=issuer)
    transactions = _SQLiteTransactions(engine)
    state.enterprise_workspace_service = EnterpriseWorkspaceService(transaction_manager=transactions)
    state.enterprise_document_import_service = EnterpriseDocumentImportService(transaction_manager=transactions)
    server = uvicorn.Server(uvicorn.Config(account_core_mvp.app, host='127.0.0.1', port=int(api.rsplit(':', 1)[1]),
        ssl_keyfile=str(home / 'server.key'), ssl_certfile=str(home / 'server.pem'), lifespan='off',
        access_log=False, log_level='error', proxy_headers=False))
    worker = threading.Thread(target=server.run, daemon=True)
    worker.start()
    deadline = time.monotonic()+20
    while not server.started and worker.is_alive() and time.monotonic()<deadline:
        time.sleep(.1)
    if not server.started: raise RuntimeError('Isolated account-core TLS startup failed')
    return server, worker, runtime, engine


def desktop_acceptance():
    # Credentials enter this subprocess through a pipe, never argv/env/a saved test config.
    data = json.loads(sys.stdin.read())
    import httpx
    import pytest
    from PySide6.QtWidgets import QWidget
    from PySide6.QtCore import QSettings
    from desktop.account_identity import AccountCredentialVault, AccountPreferenceStore
    from desktop.login_session import LoginSession
    from desktop.enterprise_oidc import DesktopOidcConfig
    from desktop.native_auth import NativeCredentialClient
    from desktop.tests.test_login_smooth import app_context
    from types import SimpleNamespace
    home = Path(data['home'])
    patch = pytest.MonkeyPatch()
    fixture = app_context.__wrapped__(patch, home / 'desktop')
    target, qt, settings = next(fixture)
    vault = AccountCredentialVault()
    actual_settings = QSettings(str(home / 'desktop-preferences.ini'), QSettings.Format.IniFormat)
    target._login_session = LoginSession(AccountPreferenceStore(actual_settings), vault)
    profile = None
    checks = {}
    try:
        for name, value in {'MENTORFLOW_DESKTOP_OIDC_ISSUER': data['issuer'],
                            'MENTORFLOW_ENTERPRISE_API_BASE_URL': data['api'],
                            'SSL_CERT_FILE': str(home / 'ca.pem')}.items():
            patch.setenv(name, value)
        original_send = httpx.AsyncClient.send
        async def observed_send(client, request, **kwargs):
            try:
                return await original_send(client, request, **kwargs)
            except httpx.HTTPError as exc:
                print('MF_FIXTURE_NETWORK=' + type(exc).__name__ + ': ' + str(exc).replace(data['password'], '[REDACTED]'), flush=True)
                raise
        patch.setattr(httpx.AsyncClient, 'send', observed_send)
        from desktop import startup_flow
        original_bootstrap = startup_flow.bootstrap_enterprise_account
        def observed_bootstrap(*a, **kw):
            try: return original_bootstrap(*a, **kw)
            except Exception as exc:
                cause = exc.__cause__
                print('MF_FIXTURE_BOOTSTRAP=' + type(cause).__name__ + ': ' + str(cause), flush=True)
                raise
        patch.setattr(startup_flow, 'bootstrap_enterprise_account', observed_bootstrap)
        def forbidden(*a, **kw): raise AssertionError('External browser attempted')
        patch.setattr('PySide6.QtGui.QDesktopServices.openUrl', forbidden)
        patch.setattr('desktop.in_app_oidc.InAppOidcLoginDialog', forbidden)
        # Business rendering/local inference service are outside this identity acceptance.
        # Exercise the actual _show_main guard with a lightweight QWidget after real bootstrap.
        class Window(QWidget):
            def __init__(self, *a, **kw): super().__init__()
        patch.setitem(sys.modules, 'desktop.main_window', SimpleNamespace(MainWindow=Window))
        def open_main(*a, **kw):
            assert target.bootstrap_result is not None
            target.api = SimpleNamespace(close=lambda: None)
            target.service = SimpleNamespace(origin='http://127.0.0.1:1', stop=lambda: None)
            target._show_main({'username': 'fixture'})
        patch.setattr(target, '_start_enterprise_local_service', open_main)
        patch.setattr(target, '_show_device_migration', open_main)
        target._show_login_gate()
        qt.processEvents()
        target.api = SimpleNamespace(close=lambda: None)
        target._show_main({})
        assert target.main is None
        gate = target.login_gate
        errors = []
        for username in ('wrong', 'missing', 'disabled'):
            gate.username_field.setText(username)
            gate.password_field.setText('incorrect' if username != 'disabled' else data['password'])
            gate.primary_button.click()
            deadline = time.monotonic()+35
            while gate.state.value != 'AUTH_FAILED' and time.monotonic()<deadline:
                qt.processEvents()
                time.sleep(.01)  # Yield the GIL while the real network/import worker runs.
            errors.append(gate.state_detail.text())
            assert target.main is None and target._last_login_result is None
        assert errors == ['账号或密码不正确'] * 3, errors
        checks['native_form_wrong_unknown_disabled_uniform'] = True
        gate.remember_box.setChecked(True)
        gate.username_field.setText('valid')
        gate.password_field.setText(data['password'])
        gate.primary_button.click()
        assert gate.password_field.text() == ''
        deadline = time.monotonic()+35
        while target.main is None and time.monotonic()<deadline:
            qt.processEvents()
            time.sleep(.01)
            if gate.state.value == 'AUTH_FAILED': break
        if target.main is None:
            import faulthandler
            faulthandler.dump_traceback()
            raise AssertionError('Native UI acceptance failed: ' + str(gate.state) + ': ' + gate.state_detail.text())
        result = target._last_login_result
        profile = result.identity.profile()
        checks['native_form_real_tls_verifier'] = True
        checks['workspace_bootstrap'] = target.bootstrap_result.snapshot is not None
        checks['existing_membership_continuity'] = result.identity.workspace_id == 'existing-workspace'
        checks['main_window_gate'] = target.main.isVisible()
        saved = vault.load_oauth_tokens(profile)
        assert saved == (result.access_token, result.refresh_token)
        assert all(data['password'] != item for item in saved)
        actual_settings.sync()
        assert data['password'] not in (home / 'desktop-preferences.ini').read_text(encoding='utf-8')
        checks['windows_credential_locker_session_only'] = True
        async def restore_logout():
            client = NativeCredentialClient(DesktopOidcConfig.from_env())
            try:
                restored = await client.restore(profile, vault)
                assert restored.identity == result.identity and restored.refresh_token != result.refresh_token
                assert await client.end_session(restored.refresh_token)
                try:
                    await client.restore(profile, vault)
                except Exception:
                    return True
                raise AssertionError('Revoked refresh was accepted')
            finally: await client.aclose()
        checks['restore_logout_revocation'] = asyncio.run(restore_logout())
        checks['password_never_persisted'] = True
    finally:
        if profile is None and target._last_login_result is not None:
            profile = target._last_login_result.identity.profile()
        if profile is not None: vault.logout(profile)
        try: next(fixture)
        except StopIteration: pass
        patch.undo()
    print('MF_FIXTURE_DESKTOP_RESULT=' + json.dumps(checks))


def run(args):
    import httpx
    from scripts.build_native_identity_verifier import build
    # Keep Windows SQLite/Vault paths below MAX_PATH, including both identity hashes.
    args.output.mkdir(parents=True, exist_ok=True)
    home = args.output.resolve().parent / ('mf-native-' + uuid.uuid4().hex[:12])
    home.mkdir(parents=True, exist_ok=False)
    receipt = {'keycloak_version': '26.7.3', 'fixture': str(home), 'checks': {}, 'production_data_touched': False,
               'live_keycloak_mutation': 0, 'smtp_send': 0, 'password_grant_used': False}
    checks = receipt['checks']
    kc_process = None
    api_server = api_worker = engine = runtime = None
    log = None
    try:
        with zipfile.ZipFile(args.keycloak_zip) as archive:
            archive.extractall(home)
        kc = home / 'keycloak-26.7.3'
        pin = certificates(home)
        receipt['spi_sha256'] = build(kc, args.jdk, kc / 'providers/mentorflow-native-verifier.jar')
        classes = home / 'fixture-control-classes'
        classes.mkdir()
        subprocess.run([str(args.jdk / 'bin/javac.exe'), '--release', '21', '-cp', str(kc / 'lib/lib/main/*'),
                        '-d', str(classes), str(ROOT / 'scripts/NativeIdentityFixtureControl.java')], check=True)
        with zipfile.ZipFile(kc / 'providers/fixture-control.jar', 'w') as jar:
            for file in classes.rglob('*.class'): jar.write(file, file.relative_to(classes).as_posix())
            jar.writestr('META-INF/services/org.keycloak.services.resource.RealmResourceProviderFactory',
                         'win.clannad.mentorflow.lab.NativeIdentityFixtureControl\n')
            jar.writestr('META-INF/beans.xml', '<beans xmlns="https://jakarta.ee/xml/ns/jakartaee" bean-discovery-mode="all"/>')
        realm = 'mf-native-' + uuid.uuid4().hex[:10]
        kc_port, api_port, management_port = port(), port(), port()
        issuer, api = f'https://127.0.0.1:{kc_port}/realms/{realm}', f'https://127.0.0.1:{api_port}'
        password = secrets.token_urlsafe(26)
        users = []
        for username in ('valid', 'wrong', 'disabled', 'temporary', 'email', 'mfa', 'locked', 'expire', 'revalidate', 'policy-expired', 'configured-mfa'):
            user = {'id': str(uuid.uuid4()), 'username': username, 'enabled': username != 'disabled',
                    'email': username+'@fixture.invalid', 'emailVerified': username != 'email',
                    'firstName': 'Fixture', 'lastName': username,
                    'credentials': [{'type': 'password', 'value': password, 'temporary': username == 'temporary'}],
                    'requiredActions': ['CONFIGURE_TOTP'] if username == 'mfa' else ['UPDATE_PASSWORD'] if username == 'temporary' else []}
            users.append(user)
        payload = {'realm': realm, 'enabled': True, 'sslRequired': 'all', 'verifyEmail': True,
            'registrationAllowed': False, 'resetPasswordAllowed': False, 'bruteForceProtected': True,
            'failureFactor': 2, 'waitIncrementSeconds': 60, 'minimumQuickLoginWaitSeconds': 60,
            'maxFailureWaitSeconds': 300, 'quickLoginCheckMilliSeconds': 1000,
            'passwordPolicy': 'hashAlgorithm(argon2) and forceExpiredPasswordChange(1)', 'smtpServer': {}, 'users': users,
            'clients': [{'clientId': 'mentorflow-desktop', 'publicClient': True,
                         'standardFlowEnabled': True, 'directAccessGrantsEnabled': False,
                         'redirectUris': ['http://127.0.0.1/*']}]}
        import_dir = kc / 'data/import'
        import_dir.mkdir(parents=True, exist_ok=True)
        realm_file = import_dir / 'realm.json'
        realm_file.write_text(json.dumps(payload), encoding='utf-8')
        process_env = {k: v for k, v in os.environ.items() if not k.startswith(('KC_', 'KEYCLOAK_', 'MF_NATIVE_VERIFIER_'))}
        process_env.update({'MF_NATIVE_VERIFIER_ENABLED': '1', 'MF_NATIVE_VERIFIER_REALM': realm,
                           'MF_NATIVE_VERIFIER_ISSUER': issuer, 'MF_NATIVE_VERIFIER_BACKEND_CERT_SHA256': pin,
                           'MF_NATIVE_FIXTURE_CONTROL': '1'})
        java = str(args.jdk / 'bin/java.exe')
        command = [java, '-Xms128m', '-Xmx512m', '-XX:ActiveProcessorCount=2',
            '-Duser.language=en', '-Duser.country=US', '-Dkc.home.dir='+str(kc),
            '-Djboss.server.config.dir='+str(kc / 'conf'),
            '-Djava.util.concurrent.ForkJoinPool.common.threadFactory=io.quarkus.bootstrap.forkjoin.QuarkusForkJoinWorkerThreadFactory',
            '-cp', str(kc / 'lib/quarkus-run.jar'), 'io.quarkus.bootstrap.runner.QuarkusEntryPoint',
            'start-dev', '--db=dev-file', '--cache=local', '--http-enabled=false', '--http-host=127.0.0.1',
            '--http-management-host=127.0.0.1', '--http-management-port='+str(management_port),
            '--https-port='+str(kc_port), '--https-certificate-file='+str(home / 'server.pem'),
            '--https-certificate-key-file='+str(home / 'server.key'), '--https-client-auth=request',
            '--truststore-paths='+str(home / 'ca.pem'), '--import-realm', '--http-access-log-enabled=false', '--log-level=error']
        log = (home / 'keycloak.log').open('wb')
        tls = ssl.create_default_context(cafile=str(home / 'ca.pem'))
        with httpx.Client(verify=tls, trust_env=False, timeout=2) as probe:
            for attempt in range(2):
                kc_process = subprocess.Popen(command, cwd=kc, env=process_env, stdout=log, stderr=subprocess.STDOUT,
                                              creationflags=subprocess.CREATE_NO_WINDOW)
                deadline = time.monotonic()+150
                while time.monotonic()<deadline and kc_process.poll() is None:
                    try:
                        if probe.get(issuer+'/.well-known/openid-configuration').status_code == 200: break
                    except httpx.HTTPError: pass
                    time.sleep(.3)
                if kc_process.poll() is None: break
                if kc_process.returncode != 10: raise RuntimeError('Keycloak startup failed; inspect local log')
                command.insert(1, '-Dkc.config.built=true')
            assert probe.get(issuer+'/.well-known/openid-configuration').status_code == 200
            realm_file.unlink()  # Remove imported plaintext fixture password as soon as consumed.
            checks['real_keycloak_discovery'] = True
            endpoint = issuer+'/mentorflow-native-identity/v1'
            checks['anonymous_verifier_denied'] = probe.post(endpoint, json={'operation': 'authenticate', 'username': 'valid', 'password': password}).status_code == 404
        wrong_tls = ssl.create_default_context(cafile=str(home / 'ca.pem'))
        wrong_tls.load_cert_chain(home / 'other.pem', home / 'other.key')
        with httpx.Client(verify=wrong_tls, trust_env=False) as other:
            checks['unapproved_service_certificate_denied'] = other.post(endpoint, json={}).status_code == 404
        mtls = ssl.create_default_context(cafile=str(home / 'ca.pem'))
        mtls.load_cert_chain(home / 'backend.pem', home / 'backend.key')
        with httpx.Client(verify=mtls, trust_env=False, timeout=10) as identity:
            control = issuer+'/mf-native-fixture-control'
            policy = identity.get(control+'/policy')
            (home / 'policy.json').write_text(json.dumps(policy.json(), indent=2), encoding='utf-8')
            def auth(name, secret=password):
                return identity.post(endpoint, json={'operation': 'authenticate', 'username': name, 'password': secret})
            correct = auth('valid')
            if correct.status_code != 200 or correct.json().get('outcome') != 'authenticated':
                raise AssertionError('Real credential validation did not authenticate: '+correct.text)
            checks['real_password_provider'] = True
            failed = [auth('wrong', 'incorrect'), auth('missing', 'incorrect'), auth('disabled')]
            checks['wrong_unknown_disabled_anti_enumeration'] = all(r.status_code == 401 and r.content == failed[0].content for r in failed)
            for name in ('temporary', 'email', 'mfa'):
                response = auth(name)
                checks[name+'_required_action_closed'] = response.status_code == 200 and response.json().get('outcome') == 'challenge_required'
            assert auth('locked', 'incorrect').status_code == 401
            assert auth('locked', 'incorrect').status_code == 401
            time.sleep(1)
            checks['keycloak_brute_force_locked'] = auth('locked').status_code == 401
            changed = identity.post(control+'/mutate', json={'operation': 'expire', 'username': 'policy-expired'})
            assert changed.status_code == 200
            expired_password = auth('policy-expired')
            checks['identity_password_age_policy_closed'] = expired_password.status_code == 200 and expired_password.json().get('outcome') == 'challenge_required'
            assert identity.post(control+'/mutate', json={'operation': 'otp', 'username': 'configured-mfa'}).status_code == 200
            checks['configured_otp_mfa_closed'] = auth('configured-mfa').json().get('outcome') == 'challenge_required'
            assert identity.post(control+'/mutate', json={'operation': 'require-mfa', 'username': 'configured-mfa'}).status_code == 200
            checks['realm_mandatory_mfa_flow_closed'] = auth('valid').json().get('outcome') == 'challenge_required'
            assert identity.post(control+'/mutate', json={'operation': 'restore-flow', 'username': 'configured-mfa'}).status_code == 200
        api_server, api_worker, runtime, engine = backend(home, issuer, api, correct.json()['subject'])
        with httpx.Client(verify=tls, trust_env=False, timeout=12) as api_probe:
            response = api_probe.post(api+'/api/enterprise/auth/native/login', json={'username': 'expire', 'password': password})
            if response.status_code != 200: raise AssertionError('TLS backend login probe status '+str(response.status_code))
            checks['real_tls_backend_session'] = True
        child_env = {k: v for k, v in os.environ.items() if not k.startswith(('MENTORFLOW_NATIVE_', 'MF_NATIVE_VERIFIER_', 'KC_', 'KEYCLOAK_'))}
        for name in list(child_env):
            if name.upper() in {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY'}:
                child_env.pop(name)
        child_env['NO_PROXY'] = '127.0.0.1,localhost'
        child_env.update({'QT_QPA_PLATFORM': 'offscreen', 'PYTHONIOENCODING': 'utf-8', 'PYTHONPATH': str(ROOT)})
        child = subprocess.run([str(args.desktop_python), str(Path(__file__).resolve()), 'desktop'],
            input=json.dumps({'home': str(home), 'issuer': issuer, 'api': api, 'password': password}),
            text=True, encoding='utf-8', capture_output=True, env=child_env, cwd=ROOT, timeout=150,
            creationflags=subprocess.CREATE_NO_WINDOW)
        # Never print captured request/credential exception representations.
        (home / 'desktop.log').write_text(child.stdout.replace(password, '[REDACTED]')+child.stderr.replace(password, '[REDACTED]'), encoding='utf-8')
        if child.returncode: raise RuntimeError('Native Qt acceptance failed; inspect redacted desktop.log')
        marker = next(line for line in child.stdout.splitlines() if line.startswith('MF_FIXTURE_DESKTOP_RESULT='))
        checks.update(json.loads(marker.split('=', 1)[1]))
        with httpx.Client(verify=tls, trust_env=False, timeout=12) as client:
            prefix = api+'/api/enterprise/auth/native'
            with httpx.Client(verify=mtls, trust_env=False, timeout=10) as identity:
                previous = client.post(prefix+'/login', json={'username': 'revalidate', 'password': password})
                assert previous.status_code == 200
                fresh_password = secrets.token_urlsafe(26)
                assert identity.post(control+'/mutate', json={'operation': 'password', 'username': 'revalidate', 'password': fresh_password}).status_code == 200
                checks['real_password_change_revokes_refresh'] = client.post(prefix+'/refresh', json={'refresh_token': previous.json()['refresh_token']}).status_code == 401
                renewed = client.post(prefix+'/login', json={'username': 'revalidate', 'password': fresh_password})
                assert renewed.status_code == 200
                assert identity.post(control+'/mutate', json={'operation': 'disable', 'username': 'revalidate'}).status_code == 200
                checks['real_disabled_identity_revokes_refresh'] = client.post(prefix+'/refresh', json={'refresh_token': renewed.json()['refresh_token']}).status_code == 401
                fresh_password = None
            pair = client.post(prefix+'/login', json={'username': 'expire', 'password': password})
            assert pair.status_code == 200
            material = pair.json()
            runtime.native_sessions.clock = lambda: time.time()+301
            response = client.get(api+'/api/enterprise/auth/me', headers={'Authorization': 'Bearer '+material['access_token']})
            checks['real_session_access_expiry'] = response.status_code == 401
            runtime.native_sessions.clock = lambda: time.time()+28801
            checks['real_session_refresh_expiry'] = client.post(prefix+'/refresh', json={'refresh_token': material['refresh_token']}).status_code == 401
            runtime.native_sessions.clock = time.time
            original = runtime.native_sessions
            runtime.native_sessions = None
            checks['unconfigured_verifier_closed'] = client.post(prefix+'/login', json={'username': 'valid', 'password': password}).status_code == 503
            runtime.native_sessions = original
            # Real TLS peer that accepts mTLS but never replies: prove the wall-clock deadline.
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            release_stall = threading.Event()
            class Stall(BaseHTTPRequestHandler):
                def log_message(self, *a): pass
                def do_POST(self): release_stall.wait(14)
            stalled = ThreadingHTTPServer(('127.0.0.1', 0), Stall)
            stalled.daemon_threads = True
            stall_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            stall_tls.load_cert_chain(home / 'server.pem', home / 'server.key')
            stall_tls.load_verify_locations(home / 'ca.pem')
            stall_tls.verify_mode = ssl.CERT_REQUIRED
            stalled.socket = stall_tls.wrap_socket(stalled.socket, server_side=True)
            stall_thread = threading.Thread(target=stalled.serve_forever, daemon=True)
            stall_thread.start()
            previous_endpoint = original.verifier.endpoint
            try:
                original.verifier.endpoint = f'https://127.0.0.1:{stalled.server_port}/mentorflow-native-identity/v1'
                before = time.monotonic()
                response = client.post(prefix+'/login', json={'username': 'valid', 'password': password})
                checks['real_mtls_timeout_closed'] = response.status_code == 503 and time.monotonic()-before < 12
            finally:
                original.verifier.endpoint = previous_endpoint
                release_stall.set(); stalled.shutdown(); stall_thread.join(5); stalled.server_close()
            kc_process.terminate(); kc_process.wait(15)
            checks['real_identity_unavailable_closed'] = client.post(prefix+'/login', json={'username': 'valid', 'password': password}).status_code == 503
        receipt['status'] = 'PASS' if all(checks.values()) else 'FAIL'
    except Exception as exc:
        receipt['status'] = 'FAIL'
        receipt['error'] = str(exc) if isinstance(exc, (AssertionError, RuntimeError)) else type(exc).__name__
    finally:
        if api_server is not None:
            api_server.should_exit = True
            api_worker.join(15)
        if runtime is not None:
            # HTTP transports belong to uvicorn's event loop, now closed. Process owns them.
            runtime.native_sessions.families.clear()
        if engine is not None: engine.dispose()
        if kc_process is not None and kc_process.poll() is None:
            kc_process.terminate()
            try: kc_process.wait(15)
            except subprocess.TimeoutExpired: kc_process.kill(); kc_process.wait(10)
        if log is not None: log.close()
        for sensitive in [*home.glob('*.key'), *home.glob('keycloak-*/data/import/*.json')]: sensitive.unlink(missing_ok=True)
        receipt['cleanup'] = {'keycloak_stopped': kc_process is None or kc_process.poll() is not None,
                              'api_stopped': api_worker is None or not api_worker.is_alive(), 'fixture_private_keys_removed': not list(home.glob('*.key'))}
        (home / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        (args.output / (home.name+'-receipt.json')).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        print(json.dumps(receipt, indent=2))
    return 0 if receipt['status'] == 'PASS' else 1


if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1] == 'desktop':
        desktop_acceptance()
    else:
        parser = argparse.ArgumentParser()
        parser.add_argument('--keycloak-zip', type=Path, required=True)
        parser.add_argument('--jdk', type=Path, required=True)
        parser.add_argument('--desktop-python', type=Path, required=True)
        parser.add_argument('--output', type=Path, required=True)
        raise SystemExit(run(parser.parse_args()))
