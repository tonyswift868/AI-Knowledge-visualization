"""Assemble audited MF-U04 sources in disposable test directories only."""
import io
import os
from pathlib import Path
import subprocess
import tarfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import ipaddress
import socket
import ssl
import sys
import time

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[2]
AUDIT = Path(os.environ.get('MFU04_AUDIT_DIR', 'D:/MentorFlow/.local/deploy-audit/mfu04'))
PACKAGE = ROOT / 'deploy/enterprise/staging/rp05/mf-u10'


def assemble(destination, *, overlay=False):
    destination.mkdir(parents=True)
    archive = subprocess.run(['git', 'archive', '37f9507', 'backend/app', 'backend/config'], cwd=ROOT,
                             check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source.getmembers():
            if not member.isfile():
                continue
            target = destination / member.name
            assert target.resolve().is_relative_to(destination.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.extractfile(member).read())
    assert (AUDIT / 'backend/app/orm.py').is_file(), 'MF-U04 evidence files required'
    for source in AUDIT.rglob('*'):
        if source.is_file():
            target = destination / source.relative_to(AUDIT)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    if overlay:
        for source in (PACKAGE / 'overlay').rglob('*'):
            if source.is_file():
                target = destination / source.relative_to(PACKAGE / 'overlay')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
    return destination


def environment(root, lab):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('MENTORFLOW_', 'MFU10_')) and k != 'PYTHONPATH'}
    env.update(PYTHONPATH=os.pathsep.join((str(root / 'backend'), str(root))),
               PYTHONDONTWRITEBYTECODE='1',
               MENTORFLOW_DEPLOYMENT_MODE='ENTERPRISE_SERVER',
               MENTORFLOW_DATABASE_URL=lab.url('mentorflow_app'),
               MENTORFLOW_ORM_DB=str(root / 'scratch-orm.db'),
               MENTORFLOW_DB=str(root / 'scratch-app.db'),
               MENTORFLOW_KEYCLOAK_ISSUER='https://mf07-isolated.invalid/realm',
               MENTORFLOW_KEYCLOAK_REDIRECT_URI='https://127.0.0.1/callback',
               MENTORFLOW_KEYCLOAK_CLIENT_ID='mentorflow-desktop',
               MENTORFLOW_KEYCLOAK_AUDIENCE='mentorflow-api')
    return env


def certificate(root):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'MF-U10 loopback rehearsal')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
        .sign(key, hashes.SHA256()))
    cert_path, key_path = root / 'local-cert.pem', root / 'local-key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path


@contextmanager
def server(root, lab, *, catalog=None):
    cert, key = certificate(root)
    runner = root / 'rehearsal_app.py'
    runner.write_bytes((ROOT / 'backend/tests/mfu10_rehearsal_app.py').read_bytes())
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = environment(root, lab)
    trace = root / 'runtime-trace.json'
    env.update(MFU10_PORT=str(port), MFU10_CERT=str(cert), MFU10_KEY=str(key), MFU10_TRACE=str(trace),
               MENTORFLOW_CATALOG_DIR=str(catalog or root / 'catalog'))
    with (root / 'uvicorn.log').open('wb') as log:
        process = subprocess.Popen([sys.executable, '-B', str(runner)], cwd=root, env=env,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            with httpx.Client(base_url=f'https://127.0.0.1:{port}',
                verify=ssl.create_default_context(cafile=str(cert)), trust_env=False, timeout=2) as client:
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise AssertionError((root / 'uvicorn.log').read_text(errors='replace'))
                    try:
                        if client.get('/health/startup').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(.1)
                else:
                    raise AssertionError('local application startup deadline exceeded')
                yield client
        finally:
            # Terminate only this owned local process. Runtime module trace is
            # also exposed below before shutdown so Windows termination is safe.
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
