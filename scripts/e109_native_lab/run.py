"""One-shot native Windows lab. Explicit paths; loopback; no SMTP; owned cleanup.

Run with the repository's PostgreSQL/SQLAlchemy/httpx Python test environment.
Raw subprocess output is confined to the disposable directory and deleted.
Only allowlisted status/count evidence survives. Never deploy NativeTokenFixture.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
import zipfile

import httpx
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'scripts')]
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Lab:
    def __init__(self, args):
        self.args = args
        args.scratch.mkdir(parents=True,exist_ok=True)
        if shutil.disk_usage(args.scratch).free < 2 * 1024**3:
            raise RuntimeError('BLOCKED_FIXTURE_CAPACITY_DISK')
        self.identity = 'mf-e109-sec04-' + secrets.token_hex(6)
        self.home = args.scratch.resolve() / self.identity
        self.home.mkdir(parents=True, exist_ok=False)
        self.pgdata = self.home / 'postgres-data'
        self.kc = self.home / 'keycloak'
        self.pgport, self.kcport, self.tlsport = port(), port(), port()
        self.base = f'https://127.0.0.1:{self.tlsport}'
        self.password = secrets.token_urlsafe(32)
        self.lease = secrets.token_urlsafe(32)
        self.process = None
        self.pg_started = False
        self.logs = []
        self.report = dict(fixture=self.identity, fixture_type='NATIVE_WINDOWS_POSTGRES_KEYCLOAK',
            isolated=True, production_data=False, ports={'postgres': self.pgport, 'keycloak_http': self.kcport,'keycloak_https':self.tlsport},
            limits={'postgres_shared_buffers_mb': 64, 'postgres_max_connections': 30,
                    'keycloak_heap_mb': 512, 'keycloak_active_processors': 2},
            smtp_send=0, production_mutation=0, secret_values_emitted=0,
            checks={}, cleaned_up=False)
        self.report['source_sha256'] = {str(p.relative_to(ROOT)).replace('\\','/'):
            hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [*Path(__file__).parent.glob('*.py'),*Path(__file__).parent.glob('*.java'),
             ROOT/'backend/tests/test_e109_security_native_stage2.py',
             ROOT/'release/account-capabilities.json',
             ROOT/'deploy/enterprise/auth-email-security/realm-policy.json',
             ROOT/'deploy/enterprise/auth-email-security/nginx.edge.conf']}
        self.report['binary_sha256'] = {name:hashlib.sha256(path.read_bytes()).hexdigest()
            for name,path in {'postgres':args.pg_bin/'postgres.exe','java':args.java_home/'bin/java.exe',
                              'nginx':args.nginx_exe,'keycloak_runner':args.keycloak/'lib/quarkus-run.jar'}.items()}
        self.manifest = args.scratch.resolve() / (self.identity + '-manifest.json')
        self.manifest.write_text(json.dumps(dict(identity=self.identity, owned_directory=str(self.home),
            ports=self.report['ports']), indent=2), encoding='utf-8')

    def command(self, args, *, timeout=120, env=None):
        output = self.home / 'last-command.log'
        # Windows daemon grandchildren can inherit pipe handles and keep
        # communicate() blocked after pg_ctl exits; regular files avoid that.
        with output.open('wb') as log:
            result = subprocess.run([str(a) for a in args], cwd=self.home, env=env,
                stdout=log, stderr=subprocess.STDOUT, timeout=timeout, creationflags=HIDDEN)
        if result.returncode:
            # No raw log is printed: command output may contain credentials/tokens.
            raise RuntimeError('NATIVE_COMMAND_FAILED_' + Path(str(args[0])).stem)
        return output.read_text(encoding='utf-8', errors='replace')

    def pg(self, user='postgres', database='postgres', **kwargs):
        return psycopg.connect(host='127.0.0.1', port=self.pgport, user=user,
            password=self.password, dbname=database, **kwargs)

    def dburl(self, user, database):
        from sqlalchemy.engine import URL
        return URL.create('postgresql+psycopg', username=user, password=self.password,
            host='127.0.0.1', port=self.pgport, database=database).render_as_string(hide_password=False)

    def start_postgres(self):
        pwfile = self.home / 'init-password'
        pwfile.write_text(self.password, encoding='utf-8')
        try:
            self.command([self.args.pg_bin / 'initdb.exe', '-D', self.pgdata, '-U', 'postgres',
                '--auth=scram-sha-256', '--pwfile=' + str(pwfile), '--encoding=UTF8', '--locale=C'])
        finally:
            pwfile.unlink(missing_ok=True)
        with (self.pgdata / 'postgresql.conf').open('a', encoding='utf-8') as out:
            out.write(f"\nlisten_addresses='127.0.0.1'\nport={self.pgport}\nshared_buffers='64MB'\n"
                      "max_connections=30\nwork_mem='2MB'\nlog_statement='none'\nlog_min_error_statement=panic\n")
        self.pg_started = True
        self.command([self.args.pg_bin / 'pg_ctl.exe', '-D', self.pgdata,
                      '-l', self.home / 'postgres.log', '-w', 'start'])
        self.report['postgres_pid'] = int((self.pgdata/'postmaster.pid').read_text().splitlines()[0])
        with self.pg(autocommit=True) as conn:
            self.report['postgres_version'] = conn.execute('SHOW server_version').fetchone()[0]
            for role in ('mentorflow_app', 'mentorflow_migrator', 'mf_e109_sec04_keycloak'):
                conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}').format(sql.Identifier(role), sql.Literal(self.password)))
            for db, owner in (('mf_e109_sec04_app', 'mentorflow_migrator'),
                              ('mf_e109_sec04_keycloak', 'mf_e109_sec04_keycloak')):
                conn.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(db), sql.Identifier(owner)))

    def start_keycloak(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        from datetime import datetime, timedelta, timezone
        from ipaddress import ip_address
        key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,self.identity)])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=2))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address('127.0.0.1'))]),critical=False)
            .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True).sign(key,hashes.SHA256()))
        cert_path, key_path = self.home/'tls-cert.pem', self.home/'tls-key.pem'
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        self.tls_context = ssl.create_default_context(cafile=str(cert_path))
        self.report['tls_certificate_sha256'] = cert.fingerprint(hashes.SHA256()).hex()
        shutil.copytree(self.args.keycloak, self.kc)
        classes = self.home / 'classes'
        classes.mkdir()
        self.command([self.args.java_home / 'bin/javac.exe', '-cp', str(self.kc / 'lib/lib/main/*'),
            '-d', classes, Path(__file__).with_name('NativeTokenFixture.java')])
        jar = self.kc / 'providers/mf-e109-sec04-fixture.jar'
        with zipfile.ZipFile(jar, 'w') as archive:
            for item in classes.rglob('*.class'):
                archive.write(item, item.relative_to(classes).as_posix())
            archive.writestr('META-INF/services/org.keycloak.services.resource.RealmResourceProviderFactory',
                            'lab.NativeTokenFixture\n')
            archive.writestr('META-INF/beans.xml', '<beans xmlns="https://jakarta.ee/xml/ns/jakartaee" bean-discovery-mode="all"/>')
        env = {k:v for k,v in os.environ.items() if not k.startswith(('KC_', 'KEYCLOAK_', 'JAVA_'))}
        env.update(KC_BOOTSTRAP_ADMIN_USERNAME='mf-e109-sec04-admin', KC_BOOTSTRAP_ADMIN_PASSWORD=self.password,
            E109_NATIVE_LAB_LEASE=self.lease, KC_DB_PASSWORD=self.password)
        args = [self.args.java_home / 'bin/java.exe', '-Xms128m', '-Xmx512m', '-XX:ActiveProcessorCount=2',
            '-Duser.language=en', '-Duser.country=US', '-Dkc.home.dir=' + str(self.kc),
            '-Djboss.server.config.dir=' + str(self.kc / 'conf'),
            '-Djava.util.concurrent.ForkJoinPool.common.threadFactory=io.quarkus.bootstrap.forkjoin.QuarkusForkJoinWorkerThreadFactory',
            '-cp', self.kc / 'lib/quarkus-run.jar', 'io.quarkus.bootstrap.runner.QuarkusEntryPoint',
            'start-dev', '--http-host=127.0.0.1', f'--http-port={self.kcport}', '--http-management-host=127.0.0.1',
            f'--https-port={self.tlsport}', '--https-certificate-file='+str(cert_path), '--https-certificate-key-file='+str(key_path),
            '--db=postgres', '--db-url=' + f'jdbc:postgresql://127.0.0.1:{self.pgport}/mf_e109_sec04_keycloak',
            '--db-username=mf_e109_sec04_keycloak', '--db-pool-initial-size=1', '--db-pool-min-size=1',
            '--db-pool-max-size=5', '--cache=local', '--http-access-log-enabled=false', '--log-level=error']
        log = (self.home / 'keycloak.log').open('wb')
        self.logs.append(log)
        for attempt in range(2):
            self.process = subprocess.Popen([str(a) for a in args], cwd=self.kc, env=env,
                stdout=log, stderr=subprocess.STDOUT, creationflags=HIDDEN)
            self.report['keycloak_pid'] = self.process.pid
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    break
                try:
                    if httpx.get(self.base + '/realms/master', timeout=2,verify=self.tls_context,trust_env=False).status_code == 200:
                        self.report['keycloak_version'] = '26.7.3'
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(.5)
            if self.process.poll() != 10:
                raise RuntimeError('NATIVE_KEYCLOAK_START_FAILED')
            args.insert(1, '-Dkc.config.built=true')
        raise RuntimeError('NATIVE_KEYCLOAK_REAUGMENT_FAILED')

    def configure_realm(self):
        self.http = httpx.Client(base_url=self.base, timeout=15, follow_redirects=False, trust_env=False,verify=self.tls_context)
        login = self.http.post('/realms/master/protocol/openid-connect/token', data=dict(
            grant_type='password', client_id='admin-cli', username='mf-e109-sec04-admin', password=self.password))
        assert login.status_code == 200, 'LAB_ADMIN_LOGIN_FAILED'
        self.admin_headers = {'Authorization': 'Bearer ' + login.json()['access_token']}
        actual_version = self.api('/admin/serverinfo')['systemInfo']['version']
        assert actual_version == '26.7.3', 'UNEXPECTED_KEYCLOAK_VERSION'
        self.report['keycloak_version'] = actual_version
        policy = json.loads((ROOT / 'deploy/enterprise/auth-email-security/realm-policy.json').read_text())
        self.realm_path = '/admin/realms/' + self.identity
        self.api('/admin/realms', 'POST', dict(realm=self.identity, enabled=True, sslRequired='none',
            **policy['realm_settings'], smtpServer={}, eventsEnabled=True,
            enabledEventTypes=['VERIFY_EMAIL', 'VERIFY_EMAIL_ERROR', 'UPDATE_PASSWORD', 'UPDATE_PASSWORD_ERROR', 'RESET_PASSWORD', 'RESET_PASSWORD_ERROR'],
            clients=[dict(clientId='mf-e109-sec04-client', enabled=True, publicClient=True,
                standardFlowEnabled=True, directAccessGrantsEnabled=True, redirectUris=['http://127.0.0.1/unused-lab-callback'])]))
        self.api(self.realm_path + '/roles', 'POST', {'name':'LEARNER'})
        self.api(self.realm_path+'/groups','POST',{'name':policy['default_new_user_group']})
        group = self.api(self.realm_path+'/groups')[0]
        learner = self.api(self.realm_path+'/roles/LEARNER')
        self.api(self.realm_path+'/groups/'+group['id']+'/role-mappings/realm','POST',[learner])
        self.api(self.realm_path+'/default-groups/'+group['id'],'PUT')
        alias = policy['reset_flow']['alias']
        self.api(self.realm_path + '/authentication/flows/reset%20credentials/copy', 'POST', {'newName':alias})
        ep = self.realm_path + '/authentication/flows/' + alias + '/executions'
        for ex in self.api(ep):
            if ex.get('providerId') == 'reset-otp' or (ex.get('authenticationFlow') and 'Conditional OTP' in ex['displayName']):
                ex['requirement'] = 'DISABLED'
                self.api(ep, 'PUT', ex)
            if ex.get('providerId') == 'reset-credential-email':
                self.api(self.realm_path + '/authentication/executions/' + ex['id'] + '/config', 'POST',
                    {'alias':'mf-e109-sec04-force-login', 'config':{'force-login':'true'}})
        self.api(self.realm_path, 'PUT', {'resetCredentialsFlow':alias})
        for name, values in policy['required_actions'].items():
            self.api(self.realm_path + '/authentication/required-actions/' + name, 'PUT',
                     {'alias':name, 'providerId':name, 'name':name, **values})
        # Use the repository's common external login error strings.
        for key in ('invalidUserMessage','invalidUsernameMessage','accountDisabledMessage',
                    'accountTemporarilyDisabledMessage','accountPermanentlyDisabledMessage'):
            response = self.http.put(self.realm_path + '/localization/en/' + key,
                content='Invalid username or password.', headers={**self.admin_headers, 'Content-Type':'text/plain'})
            assert response.status_code in (200,204), 'LOCALIZATION_POLICY_FAILED'
        realm = self.api(self.realm_path)
        assert realm['registrationAllowed'] is False and not realm['smtpServer']
        self.report['realm_policy'] = {'registrationAllowed':False, 'resetPasswordAllowed':realm['resetPasswordAllowed'],
            'actionTokenGeneratedByUserLifespan':realm['actionTokenGeneratedByUserLifespan'], 'smtp_configured':False}

    def api(self, path, method='GET', body=None):
        response = self.http.request(method, path, headers=self.admin_headers, json=body)
        assert response.status_code in (200,201,204), 'LAB_ADMIN_OPERATION_FAILED_' + str(response.status_code)
        return response.json() if response.content else None

    def close(self):
        if hasattr(self, 'http'):
            self.http.close()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(20)
        if self.pg_started:
            self.command([self.args.pg_bin / 'pg_ctl.exe', '-D', self.pgdata, '-m', 'fast', '-w', 'stop'])
        for log in self.logs:
            log.close()
        native_log = self.home / 'keycloak.log'
        if native_log.exists():
            content = native_log.read_text(encoding='utf-8',errors='replace')
            # Class/method/line only: no exception messages, tokens, SQL values,
            # URLs, users, passwords, or other unbounded diagnostic payloads.
            self.report['keycloak_exception_classes'] = sorted(set(re.findall(
                r'\b((?:java|jakarta|org)\.[\w.$]*(?:Exception|Error))\b', content)))
            self.report['keycloak_stack_locations'] = list(dict.fromkeys(re.findall(
                r'\bat (org\.keycloak\.[\w.$]+\([\w.]+:\d+\))', content)))[:12]
        manifest = json.loads(self.manifest.read_text())
        target = self.home.resolve()
        assert target.parent == self.args.scratch.resolve() and target.name == manifest['identity']
        assert str(target) == manifest['owned_directory'] and target.name.startswith('mf-e109-sec04-')
        shutil.rmtree(target)
        closed_ports = []
        for number in self.report['ports'].values():
            with socket.socket() as probe:
                probe.settimeout(.5)
                closed_ports.append(probe.connect_ex(('127.0.0.1',number)) != 0)
        self.report['ports_closed'] = all(closed_ports)
        self.report['cleaned_up'] = not target.exists() and all(closed_ports) and (self.process is None or self.process.poll() is not None)
        self.password = self.lease = None
        self.manifest.write_text(json.dumps({**manifest, 'cleaned_up':self.report['cleaned_up']}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('pg-bin','java-home','keycloak','nginx-exe'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--scratch', type=Path, default=Path('E:/Codex/Temp'))
    parser.add_argument('--only', choices=('postgres','keycloak','all'), default='all')
    args = parser.parse_args()
    lab = Lab(args)
    for name in ('MENTORFLOW_DATABASE_URL','MENTORFLOW_MIGRATION_DATABASE_URL','MENTORFLOW_RLS_DSN',
                 'E1_WORKSPACE_BOOTSTRAP_TEST_DATABASE_URL'):
        os.environ.pop(name,None)
    os.environ['MENTORFLOW_DEPLOYMENT_MODE'] = 'PERSONAL_LOCAL'
    os.environ['MENTORFLOW_ORM_DB'] = str(lab.home / 'unused-local.db')
    try:
        lab.start_postgres()
        print('POSTGRES_STARTED', flush=True)
        if args.only in ('postgres','all'):
            from e109_native_lab.postgres_checks import run as postgres_checks
            postgres_checks(lab)
            print('POSTGRES_CHECKS_COMPLETE', flush=True)
        if args.only in ('keycloak','all'):
            lab.start_keycloak()
            lab.configure_realm()
            print('KEYCLOAK_STARTED', flush=True)
            from e109_native_lab.keycloak_checks import run as keycloak_checks
            keycloak_checks(lab)
            print('KEYCLOAK_CHECKS_COMPLETE', flush=True)
    except BaseException as exc:
        # Safe diagnostics: type + source location, never values from exception.
        import traceback
        frames = traceback.extract_tb(exc.__traceback__)
        lab.report['error'] = {'type':type(exc).__name__, 'locations':[
            {'file':Path(f.filename).name, 'line':f.lineno} for f in frames[-6:]]}
        if isinstance(exc, AssertionError) and exc.args and str(exc.args[0]).isupper():
            lab.report['error']['code'] = str(exc.args[0])
    finally:
        lab.close()
        lab.report['failed_checks'] = [k for k,v in lab.report['checks'].items() if v=='FAIL']
        receipt = args.scratch / (lab.identity + '-result.json')
        receipt.write_text(json.dumps(lab.report, indent=2), encoding='utf-8')
        print(json.dumps(lab.report, indent=2), flush=True)
        print('RECEIPT=' + str(receipt), flush=True)
    return 1 if 'error' in lab.report or lab.report['failed_checks'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
