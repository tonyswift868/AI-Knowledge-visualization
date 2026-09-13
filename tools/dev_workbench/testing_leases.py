"""Synthetic certificate and publication helpers for bounded harness tests."""
from datetime import datetime,timezone,timedelta
from functools import lru_cache
import json
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from tools.dev_workbench import runtime_lease as runtime
from tools.dev_workbench.targets import sha256

@lru_cache(maxsize=1)
def certificate():
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'inert harness test CA')])
    now=datetime.now(timezone.utc)
    cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(
        x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1)).add_extension(
        x509.BasicConstraints(ca=True,path_length=0),critical=True).sign(key,hashes.SHA256())
    return cert.public_bytes(serialization.Encoding.PEM),cert.fingerprint(hashes.SHA256()).hex()

def publish(manifest):
    identity=json.loads(Path(manifest['identity_evidence_path']).read_text())
    config=dict(identity.get('test_device_configuration') or dict(contract='MENTORFLOW_TEST_RUNTIME_V1',
        issuer='https://test.invalid',api_base_url='https://test.invalid/api',client_id='mentorflow-desktop',
        audience='mentorflow-api',organization_id='org',workspace_id='workspace',
        tls_ca_path=str(Path(manifest['artifact_path']).parent/'ca.crt')))
    pem,fingerprint=certificate();Path(config['tls_ca_path']).write_bytes(pem)
    config['tls_ca_sha256']=sha256(pem)
    now=datetime.now(timezone.utc)
    value=dict(schema='MENTORFLOW_EPHEMERAL_ACCEPTANCE_RUNTIME_LEASE_V2',runtime_lease_id='TEST-LEASE',
        status='READY_FOR_ACCEPTANCE',test_id=manifest['test_id'],build_source_sha=manifest['source_sha'],
        build_source_tree=manifest['source_tree'],desktop_artifact_sha256=manifest['artifact_sha256'],
        created_at=(now-timedelta(minutes=1)).isoformat(),expires_at=(now+timedelta(hours=1)).isoformat(),
        desktop_runtime_configuration=config,ca_fingerprint=fingerprint)
    write(value);return value

def write(value):
    runtime.AUTHORITY.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(value).encode()
    (runtime.AUTHORITY/'LATEST.json').write_bytes(raw)
    (runtime.AUTHORITY/(value['runtime_lease_id']+'.json')).write_bytes(raw)
