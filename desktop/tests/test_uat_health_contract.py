import base64
import copy
from pathlib import Path
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from desktop.update_protocol import SecurityError, canonical
from desktop.uat_health_contract import verify_request, validate_health


def fixture():
    private = Ed25519PrivateKey.generate()
    keys = {'fixture': private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)}
    request = dict(schema_version=1, app_id='MentorFlow', attempt_id='a'*32, nonce='b'*64,
        purpose='boot-health', created_at=100, expires_at=200,
        expected_app_root='D:\\Relocated\\App', root_identity=[123, 456],
        runtime_version='1.1.0', target_manifest_sha256='c'*64, executable_sha256='d'*64,
        signed_release_identity='e'*64, receipt_sha256=None,
        health_endpoint='D:\\Health\\'+'a'*32+'\\health.json', fixture_root=None, cross_volume_root=None)
    def sign(doc):
        return canonical(dict(signed=doc, key_id='fixture', signature=base64.b64encode(private.sign(canonical(doc))).decode()))
    health = {k: request[k] for k in ('schema_version','app_id','attempt_id','nonce','runtime_version','target_manifest_sha256','signed_release_identity','receipt_sha256','root_identity')}
    health.update(process_id=42, timestamp=150, qt_ready=True,
        actual_app_root=request['expected_app_root'], installed_health_authority=False,
        token=dict(integrity='MEDIUM', elevated=False, integrity_rid=8192),
        marks=dict(PYTHON_ENTRY=0, QT_IMPORT=100, QAPPLICATION=200, QT_EVENT_LOOP_READY=300))
    return request, health, keys, sign


def test_valid_signed_attempt_and_health():
    request, health, keys, sign = fixture()
    assert verify_request(sign(request), keys, now=150) == request
    assert validate_health(health, request, expected_pid=42, now=151)


@pytest.mark.parametrize('field,value', [
    ('nonce','0'*64), ('attempt_id','0'*32), ('runtime_version','9.9.9'),
    ('target_manifest_sha256','0'*64), ('signed_release_identity','0'*64),
    ('receipt_sha256','0'*64), ('root_identity',[0,0]), ('timestamp',99),
    ('timestamp',201), ('process_id',41), ('qt_ready',False),
    ('installed_health_authority',True), ('actual_app_root','E:\\Other\\App'),
    ('token',dict(integrity='HIGH', elevated=True, integrity_rid=12288)),
    ('marks',dict(PYTHON_ENTRY=0))])
def test_health_reject(field, value):
    request, health, _, _ = fixture()
    health[field] = value
    with pytest.raises(SecurityError): validate_health(health, request, expected_pid=42, now=151)


@pytest.mark.parametrize('field,value', [
    ('schema_version',2), ('app_id','Other'), ('nonce','bad'), ('expires_at',500),
    ('created_at',160), ('expires_at',149), ('purpose','commit'),
    ('expected_app_root','D:\\x\\..\\App'), ('root_identity',[True,2]),
    ('health_endpoint','D:\\Relocated\\App\\health.json'),
    ('health_endpoint','D:\\Health\\wrong\\health.json'), ('unknown',1)])
def test_request_reject(field, value):
    request, _, keys, sign = fixture()
    request[field] = value
    with pytest.raises(SecurityError): verify_request(sign(request), keys, now=150)


def test_signature_and_key_reject():
    request, _, keys, sign = fixture()
    raw = sign(request)
    with pytest.raises(SecurityError): verify_request(raw.replace(b'1.1.0',b'1.1.1'), keys, now=150)
    with pytest.raises(SecurityError): verify_request(raw, {}, now=150)


def test_old_marker_new_attempt_and_expiry_reject():
    request, health, _, _ = fixture()
    request['attempt_id'] = 'f'*32
    with pytest.raises(SecurityError): validate_health(health,request,expected_pid=42,now=151)
    request['attempt_id'] = health['attempt_id']
    with pytest.raises(SecurityError): validate_health(health,request,expected_pid=42,now=201)


def test_no_build_path_authority_or_production_override():
    root = Path(__file__).parents[2]
    for name in ('desktop/uat_health_contract.py', 'desktop/packaged_update_uat.py', 'desktop_update_uat_entry.py'):
        source = (root/name).read_text(encoding='utf-8')
        for prefix in ('D:/MFU8','D:/MFUAC','D:/CodexScratch','E:/Codex'):
            assert prefix not in source and prefix.replace('/', '\\\\') not in source
    assert 'MENTORFLOW_UAT_ATTEMPT' not in (root/'desktop_gui_entry.py').read_text(encoding='utf-8')
    assert not (root/'mentorflow_uat_build_authority.py').exists()


def test_path_budget_preserved():
    from scripts.runtime_path_budget import budget
    assert budget('E:\\MentorFlow', ['_internal/QtCore.pyd'])
    with pytest.raises(ValueError): budget('D:\\'+'x'*240, ['_internal/QtCore.pyd'])
