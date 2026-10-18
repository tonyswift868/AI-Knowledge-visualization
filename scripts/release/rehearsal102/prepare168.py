"""Create task-local signing and localhost TLS inputs for 168 UAT."""
from pathlib import Path
import base64
import ipaddress
import json
from datetime import datetime, timedelta, timezone

from common import TASK, environment
environment()

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import NameOID

if (TASK/'test-private.bin').exists():
    raise SystemExit('existing UAT signing key preserved; refusing to overwrite')

signing = ed25519.Ed25519PrivateKey.generate()
(TASK/'test-private.bin').write_bytes(signing.private_bytes(
    serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()))
(TASK/'test-public.json').write_text(json.dumps({
    'key_id': 'gate168-test-only',
    'public_key': base64.b64encode(signing.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode('ascii'),
}), encoding='utf8')

tls_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'MentorFlow 168 localhost test only')])
certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
    .public_key(tls_key.public_key()).serial_number(x509.random_serial_number())
    .not_valid_before(datetime.now(timezone.utc)-timedelta(days=1))
    .not_valid_after(datetime.now(timezone.utc)+timedelta(days=2))
    .add_extension(x509.SubjectAlternativeName([
        x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
    ]), critical=False).add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    .sign(tls_key, hashes.SHA256()))
(TASK/'local-api.pem').write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
(TASK/'local-api-key.pem').write_bytes(tls_key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption()))

issuer = 'https://localhost:18764/test-realm'
(TASK/'public-runtime.json').write_text(json.dumps({
    'MENTORFLOW_DESKTOP_OIDC_ISSUER': issuer,
    'MENTORFLOW_DESKTOP_IDENTITY_ISSUER': issuer,
    'MENTORFLOW_DESKTOP_OIDC_CLIENT_ID': 'mentorflow-desktop',
    'MENTORFLOW_DESKTOP_OIDC_AUDIENCE': 'mentorflow-api',
    'MENTORFLOW_ENTERPRISE_API_BASE_URL': 'https://127.0.0.1:18764',
}), encoding='utf8')
print('168 isolated signing input and localhost-only TLS inputs created')
