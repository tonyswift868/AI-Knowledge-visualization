import copy
import hashlib
from pathlib import Path
from unittest.mock import patch
import pytest

from desktop.update_protocol import canonical, SecurityError
from desktop import update_request as request
from desktop.windows_identity import encode


def document():
    return dict(schema=request.SCHEMA, attempt_id='a'*32, nonce='b'*64,
        created_at=100, expires_at=200, app_id='MentorFlow.UAT.MF14',
        install_root=r'D:\MF14I', install_identity=encode(2**64-1,bytes(range(16))),
        base_version='1.1.0', base_manifest_sha256='c'*64, base_receipt_sha256='d'*64,
        target_version='1.1.1', target_manifest_sha256='e'*64, release_sha256='f'*64,
        staged_app=r'D:\MF14W\.u\0123456789ab\App', coordinator_pid=123,coordinator_created_hex='a'*16)


def test_canonical_frozen_bytes():
    raw=canonical(document())
    assert request.read(raw,expected_sha256=hashlib.sha256(raw).hexdigest(),now=150)==document()
    with pytest.raises(SecurityError,match='changed'):
        request.read(raw+b' ',expected_sha256=hashlib.sha256(raw).hexdigest(),now=150)
    with pytest.raises(SecurityError,match='canonical'):
        request.read(raw+b' ',now=150)


@pytest.mark.parametrize('key,value', [('schema','mentorflow.update-attempt.v1'),
    ('nonce','bad'),('created_at',True),('created_at',151),('expires_at',150),('expires_at',1001),
    ('coordinator_pid',True),('coordinator_pid',2**32),('app_id','Other'),
    ('install_identity',[728480046474634900,281474977327604]),
    ('install_root',r'D:\MF14I\..\MentorFlow'),('install_root','D:/MF14I'),
    ('install_root',r'\\server\share\App'),('staged_app',r'D:\MF14I\App'),
    ('staged_app',r'D:\MF14W\App:ads'),('target_version','1.1.0'),('target_version','1.0.0'),
    ('base_receipt_sha256','F'*64)])
def test_invalid_request_rejected(key,value):
    doc=document();doc[key]=value
    with pytest.raises((SecurityError,ValueError)):request.read(canonical(doc),now=150)


def test_duplicate_and_unknown_fields_rejected():
    raw=canonical(document())
    with pytest.raises(SecurityError):request.read(raw.replace(b'{',b'{"schema":"duplicate",',1),now=150)
    with pytest.raises(SecurityError):request.read(canonical(document()|{'command':'arbitrary.exe'}),now=150)


def test_producer_new_attempt_never_refreshes_old_file(tmp_path):
    from types import SimpleNamespace
    from desktop.windows_identity import read_directory
    from scripts.install_receipt_state import INSTALLED_KIND
    from scripts.windows_install_trust import SCHEMA
    import time
    root=tmp_path/'installed';root.mkdir()
    stage=tmp_path/'staging'/'App';stage.mkdir(parents=True)
    output=tmp_path/'requests';output.mkdir()
    receipt=canonical(dict(schema_version=2,record_type=INSTALLED_KIND,state='PENDING_HEALTH',immutable_tree=True,
        binding=dict(schema=SCHEMA,root=str(root),root_identity=read_directory(root),version='1.1.0',
                     source_sha='1'*40,tree_sha='2'*40,manifest_sha256='c'*64),
        inventory={},file_set_identity='4'*64,critical_hashes={},full_verified_at=time.time(),full_verified=True))
    (root/'installation-receipt.json').write_bytes(receipt)
    release=SimpleNamespace(signed={'base_version':'1.1.0'},version='1.1.1',manifest_bytes=b'new',envelope=b'signed fixture')
    params=dict(app_id='MentorFlow.UAT.MF14',install_root=root,staged_app=stage,
        base_receipt=receipt,release=release,coordinator_pid=__import__('os').getpid())
    path,pin=request.produce(output,**params)
    before=path.read_bytes()
    doc=request.read(before,expected_sha256=pin)
    assert doc['install_identity']==read_directory(root)
    next_path,_=request.produce(output,**params)
    assert next_path!=path and path.read_bytes()==before
    assert request.read(next_path.read_bytes())['nonce']!=doc['nonce']
