from pathlib import Path
import os, sys, json, base64, ipaddress
from datetime import datetime,timedelta,timezone
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ed25519
from common import TASK as B
for name in ('temp','local','screenshots','webroot/updates/app/stable','logs'): (B/name).mkdir(parents=True,exist_ok=True)
k=ed25519.Ed25519PrivateKey.generate()
(B/'test-private.bin').write_bytes(k.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption()))
(B/'test-public.json').write_text(json.dumps({'key_id':'inapp102c-test-only','public_key':base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()}))
r=rsa.generate_private_key(public_exponent=65537,key_size=2048)
n=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'MentorFlow local test only')])
c=(x509.CertificateBuilder().subject_name(n).issuer_name(n).public_key(r.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc)-timedelta(days=1)).not_valid_after(datetime.now(timezone.utc)+timedelta(days=2)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False).add_extension(x509.BasicConstraints(ca=True,path_length=None),True).sign(r,hashes.SHA256()))
(B/'local-api.pem').write_bytes(c.public_bytes(serialization.Encoding.PEM))
(B/'local-api-key.pem').write_bytes(r.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption()))
issuer='https://localhost:18764/test-realm'
(B/'public-runtime.json').write_text(json.dumps({'MENTORFLOW_DESKTOP_OIDC_ISSUER':issuer,'MENTORFLOW_DESKTOP_IDENTITY_ISSUER':issuer,'MENTORFLOW_DESKTOP_OIDC_CLIENT_ID':'mentorflow-desktop','MENTORFLOW_DESKTOP_OIDC_AUDIENCE':'mentorflow-api','MENTORFLOW_ENTERPRISE_API_BASE_URL':'https://127.0.0.1:18764'}))
print('isolated TLS and test signing inputs ready')
