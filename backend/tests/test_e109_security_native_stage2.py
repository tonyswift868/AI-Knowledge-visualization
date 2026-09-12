"""Real loopback HTTP and native nginx tests with synthetic account fixtures.

SQLite here establishes an existing application account only; it is not RLS
evidence. OIDC authentication uses the existing synthetic fixture. No Keycloak,
SMTP, public issuer, configured database, or external endpoint is contacted.
"""
from __future__ import annotations

from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time

import pytest
from sqlalchemy import text
import uvicorn

from app import account_core_mvp
from test_account_workspace_bootstrap import system, headers, INITIALIZE  # noqa: F401


ROOT = Path(__file__).resolve().parents[2]
EDGE = ROOT / 'deploy/enterprise/auth-email-security/nginx.edge.conf'
CREATE_HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def request(port, path, *, method='GET', payload=None, supplied_headers=None, source='127.0.0.1'):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3,
                                            source_address=(source, 0))
    request_headers = {'X-Request-ID': 'e109-stage2-local', **(supplied_headers or {})}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        request_headers['Content-Type'] = 'application/json'
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        return response.status, response.read(), {
            k.lower(): v for k, v in response.getheaders()
            if k.lower() in {'content-type', 'location', 'set-cookie'}
        }
    finally:
        connection.close()


@pytest.fixture
def local_http(system):
    _, _, engine = system
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    config = uvicorn.Config(account_core_mvp.app, lifespan='off', access_log=False,
                            log_level='critical', proxy_headers=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 8
    try:
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started, 'LOCAL_ASGI_START_FAILED'
        yield port, engine
    finally:
        server.should_exit = True
        thread.join(8)
        listener.close()
        assert not thread.is_alive(), 'LOCAL_ASGI_CLEANUP_FAILED'


@pytest.mark.parametrize('authenticated', [False, True])
@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_disabled_account_actions_do_not_enumerate_over_real_http(local_http, authenticated, method):
    port, engine = local_http
    status, body, _ = request(port, INITIALIZE, method='POST', payload={},
                              supplied_headers=headers('a'))
    assert status == 200
    workspace = json.loads(body)['workspaces'][0]
    # Prove one account exists and the other does not in this isolated fixture.
    with engine.connect() as connection:
        subjects = set(connection.execute(text('SELECT subject_id FROM organization_memberships')).scalars())
        assert 'user-a' in subjects and 'user-missing' not in subjects
    account_headers = headers('a', workspace) if authenticated else {}
    for action in ('register', 'forgot_password', 'forgot-password', 'reset-password', 'verify-email'):
        results = [request(port, '/api/enterprise/auth/' + action, method=method,
                    payload={'username': subject}, supplied_headers=account_headers)
                   for subject in ('user-a', 'user-missing')]
        assert results[0] == results[1], 'ACCOUNT_EXISTENCE_RESPONSE_DIFFERENCE'
        status, body, response_headers = results[0]
        assert status == (404 if authenticated else 401)
        assert 'location' not in response_headers and 'set-cookie' not in response_headers
        assert b'user-a' not in body and b'user-missing' not in body
        assert json.loads(body).get('error', {}).get('code') == (
            None if authenticated else 'ENTERPRISE_AUTH_REQUIRED')


def rate_contract():
    source = EDGE.read_text(encoding='utf-8')
    maps = re.findall(r'    map "\$request_method:[^\n]+\{.*?^    \}', source, re.M | re.S)
    zones = re.findall(r'^\s*(limit_req_zone \$e109_\w+_key zone=(e109_auth_(\w+)):1m rate=(\d+)r/m;)', source, re.M)
    limits = re.findall(r'^\s*(limit_req zone=(e109_auth_(\w+)) burst=(\d+) nodelay;)', source, re.M)
    assert len(maps) == len(zones) == len(limits) == 3
    assert {z[1] for z in zones} == {z[1] for z in limits}
    status = re.findall(r'^\s*(limit_req_status (\d+);)', source, re.M)
    assert len(status) == 1
    bounds = {kind: (int(rate), next(int(burst) for _, _, label, burst in limits if label == kind))
              for _, _, kind, rate in zones}
    return maps, [z[0] for z in zones], [v[0] for v in limits], status[0], bounds


@contextmanager
def native_nginx(tmp_path, upstream_port):
    configured = os.environ.get('E109_TEST_NGINX_EXE', '')
    if not configured:
        pytest.skip('NATIVE_NGINX_NOT_CONFIGURED')
    executable = Path(configured).resolve(strict=True)
    assert executable.name in {'nginx.exe', 'nginx'} and executable.is_file()
    maps, zones, limits, status, bounds = rate_contract()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    prefix = tmp_path / 'nginx'
    (prefix / 'logs').mkdir(parents=True)
    (prefix / 'temp').mkdir()
    config = prefix / 'local.conf'
    config.write_text('\n'.join([
        'worker_processes 1;', 'pid logs/nginx.pid;', 'error_log logs/error.log crit;',
        'events { worker_connections 64; }', 'http {', 'access_log off;',
        *maps, *zones, 'server {', f'listen 127.0.0.1:{port};',
        'location / {', *limits, status[0],
        'proxy_set_header X-Forwarded-For $remote_addr;',
        f'proxy_pass http://127.0.0.1:{upstream_port};', '}', '}', '}',
    ]), encoding='utf-8')
    args = [str(executable), '-p', prefix.as_posix() + '/', '-c', config.as_posix()]
    checked = subprocess.run([*args, '-t'], cwd=prefix, capture_output=True, timeout=8,
                             creationflags=CREATE_HIDDEN)
    assert checked.returncode == 0, 'LOCAL_NGINX_CONFIG_REJECTED'
    process = subprocess.Popen(args, cwd=prefix, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=CREATE_HIDDEN)
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                if request(port, '/health/live')[0] == 200:
                    break
            except OSError:
                time.sleep(.02)
        else:
            pytest.fail('LOCAL_NGINX_START_FAILED')
        yield port, int(status[1]), bounds
    finally:
        subprocess.run([*args, '-s', 'quit'], cwd=prefix, capture_output=True,
                       timeout=8, creationflags=CREATE_HIDDEN)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            subprocess.run([*args, '-s', 'stop'], cwd=prefix, capture_output=True,
                           timeout=8, creationflags=CREATE_HIDDEN)
            process.wait(timeout=8)
        assert not (prefix / 'logs/nginx.pid').exists(), 'LOCAL_NGINX_CLEANUP_FAILED'


@pytest.mark.parametrize('kind,path', [
    ('token', '/realms/mentorflow/protocol/openid-connect/token'),
    ('login', '/realms/mentorflow/login-actions/authenticate'),
    ('email', '/realms/mentorflow/login-actions/reset-credentials'),
])
def test_native_nginx_threshold_key_variation_and_recovery(local_http, tmp_path, record_property, kind, path):
    upstream_port, _ = local_http
    assert request(upstream_port, INITIALIZE, method='POST', payload={},
                   supplied_headers=headers('a'))[0] == 200
    with native_nginx(tmp_path, upstream_port) as (port, rejected, bounds):
        rate, burst = bounds[kind]
        spacing = 60 / rate
        assert 0 < spacing <= 60 and burst <= 30, 'RATE_CONTRACT_OUTSIDE_BOUNDED_TEST'
        started = time.monotonic()
        # First normal request plus the configured burst; actual application
        # path returns 404 because email actions remain disabled in Account Core.
        statuses = [request(port, path, method='POST', payload={'username': 'user-missing'})[0]
                    for _ in range(burst + 2)]
        assert time.monotonic() - started < spacing, 'BURST_TIMING_NOT_PROVEN'
        # nginx's initial bucket accounting can include the initial request in
        # the burst (notably at Windows process startup). Bound both cases by
        # the configured burst, without changing its rate or waiting for refill.
        assert statuses[:burst] == [404] * burst
        assert statuses[-1] == rejected
        assert statuses == sorted(statuses)
        assert set(statuses) == {404, rejected}
        record_property('admitted_before_rejection', statuses.count(404))
        record_property('configured_burst', burst)
        record_property('configured_rate_per_minute', rate)
        for username, forged in [('user-a', '198.51.100.1'), ('user-missing', '203.0.113.8')]:
            assert request(port, path + '?client_id=' + username, method='POST',
                payload={'username': username}, supplied_headers={
                    'X-Forwarded-For': forged, 'X-Real-IP': forged,
                    'Forwarded': 'for=' + forged})[0] == rejected
        if kind == 'email':
            for query in ('execution=VERIFY_EMAIL', 'execution=%56ERIFY_EMAIL',
                          'execution=OTHER&execution=VERIFY_EMAIL', 'execution=UPDATE_EMAIL',
                          'execution=UPDATE_PASSWORD'):
                assert request(port, '/realms/mentorflow/login-actions/required-action?' + query,
                               method='POST', payload={})[0] == rejected, 'EMAIL_KEY_VARIATION_BYPASS'
        # The existing contract keys by real source IP, not account/tenant.
        assert request(port, path, method='POST', payload={'username': 'user-a'},
                       source='127.0.0.2')[0] == 404
        assert request(port, '/health/live')[0] == 200
        assert request(port, path)[0] == 404  # auth GET is not throttled
        time.sleep(spacing + .3)
        assert request(port, path, method='POST', payload={'username': 'user-a'})[0] == 404
