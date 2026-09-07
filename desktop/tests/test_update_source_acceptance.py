"""Fast acceptance is identity admission, never a target content verdict."""
import copy
import json
from pathlib import Path
import pytest
from desktop.tests.test_mature_update import feed
from desktop.update_protocol import MANIFEST_PATH, digest, SecurityError, canonical
from desktop.update_tree_security import AcceptedApp
from scripts.windows_install_trust import SCHEMA


def inputs(feed):
    raw=(feed.app/MANIFEST_PATH).read_bytes()
    rows={r['path']:r for r in json.loads(raw)['files']}
    rows[MANIFEST_PATH]=dict(path=MANIFEST_PATH,size=len(raw),sha256=digest(raw))
    root=feed.install.stat(); app=feed.app.stat()
    receipt=dict(full_verified=True,binding=dict(schema=SCHEMA,root=str(feed.install),
        version='1.1.0',manifest_sha256=digest(raw),root_identity=[root.st_dev,root.st_ino]),
        inventory={'App':['dir',0,app.st_mtime_ns,app.st_ctime_ns,app.st_dev,app.st_ino,0]})
    return rows,digest(raw),receipt


@pytest.mark.parametrize('field',['manifest_sha256','root_identity','root','version','schema','app_identity','full_verified'])
def test_wrong_receipt_rejects_before_materialization(feed,field):
    rows,pin,receipt=inputs(feed)
    if field=='app_identity': receipt['inventory']['App'][5]+=1
    elif field=='full_verified': receipt[field]=False
    else: receipt['binding'][field]='wrong'
    with pytest.raises(SecurityError,match='receipt'):
        AcceptedApp(feed.app,rows,pin,receipt=receipt)
    assert not (feed.install/'.u').exists()


def test_corrupted_current_unchanged_receipt_final_hash_fails_no_commit(feed):
    rows,pin,receipt=inputs(feed)
    original_receipt=canonical(receipt)
    item=feed.app/'MentorFlow.exe'
    item.write_bytes(b'X'*len(feed.old_files['MentorFlow.exe']))
    before={p.relative_to(feed.app).as_posix():digest(p.read_bytes()) for p in feed.app.rglob('*') if p.is_file()}
    with AcceptedApp(feed.app,rows,pin,receipt=receipt) as accepted:
        assert accepted.profile['SOURCE_PAYLOAD_HASH_COUNT']==0
        service=feed.service(); service.cache.accepted_app=accepted
        assert service.check()['state']=='UPDATE_AVAILABLE'
        result=service.download()
        assert result['state']=='FAILED_SECURITY'
        assert service.staged is None
        assert service.cache._hardlinks  # failure occurs after actual hardlinking
    after={p.relative_to(feed.app).as_posix():digest(p.read_bytes()) for p in feed.app.rglob('*') if p.is_file()}
    assert before==after and canonical(receipt)==original_receipt


def test_tampered_manifest_rejects(feed):
    rows,pin,receipt=inputs(feed)
    path=feed.app/MANIFEST_PATH; path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(SecurityError,match='manifest pin'):
        AcceptedApp(feed.app,rows,pin,receipt=receipt)


@pytest.mark.parametrize('path',['../outside','/absolute','C:relative','//host/share','safe/../outside'])
def test_lexical_paths_fail_before_file_access(feed,path):
    rows,pin,receipt=inputs(feed)
    manifest=json.loads((feed.app/MANIFEST_PATH).read_bytes())
    manifest['files'][0]['path']=path
    raw=canonical(manifest); (feed.app/MANIFEST_PATH).write_bytes(raw)
    rows={r['path']:r for r in manifest['files']}
    rows[MANIFEST_PATH]=dict(path=MANIFEST_PATH,size=len(raw),sha256=digest(raw))
    with pytest.raises(SecurityError): AcceptedApp(feed.app,rows,digest(raw))


def test_protected_factory_uses_existing_receipt_without_payload_prehash(feed,monkeypatch):
    # ACL validation is mocked only here: production ACLs cannot be established
    # without elevation. Native volume/FileId and write-denial remain real.
    from scripts import windows_install_trust as trust
    rows,pin,receipt=inputs(feed)
    (feed.install/trust.RECEIPT).write_bytes(canonical(receipt))
    monkeypatch.setattr(trust,'assert_protected',lambda p:None)
    monkeypatch.setattr(trust,'assert_safe_parents',lambda p:None)
    monkeypatch.setattr(trust,'_check_entry_acls',lambda p:None)
    with AcceptedApp.protected(feed.install) as accepted:
        assert accepted.lease['receipt_schema']==SCHEMA
        assert accepted.lease['installed_manifest_sha256']==pin
        assert accepted.lease['protected_contract']
        with pytest.raises(OSError): (feed.app/'MentorFlow.exe').write_bytes(b'bad')
