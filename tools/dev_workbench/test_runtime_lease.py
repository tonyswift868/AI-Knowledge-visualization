"""Lease authority is isolated from the real acceptance runtime in every test."""
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from tools.dev_workbench import runtime_lease as r, targets as t
from tools.dev_workbench.test_candidate_identity import publication
from tools.dev_workbench.test_devices import devices
from tools.dev_workbench.test_workbench import candidate
from tools.dev_workbench.testing_leases import write
from desktop import test_device_configuration as config


def lease(): return json.loads((r.AUTHORITY/'LATEST.json').read_text())


def launch(publication, devices, spawn):
    root, value, paths, _, _ = publication
    from tools.dev_workbench.authority import RepoSource
    return devices.launch('A', value, RepoSource(root), paths, spawn=spawn)


def test_latest_config_replaces_immutable_old_binding(publication, devices):
    value=lease(); value['desktop_runtime_configuration']['issuer']='https://rotated.invalid'
    value['desktop_runtime_configuration']['api_base_url']='https://rotated.invalid/api'
    write(value)
    spawn=Mock(return_value=SimpleNamespace(pid=1, poll=lambda:None))
    launch(publication,devices,spawn)
    env=spawn.call_args.kwargs['env']
    assert json.loads(env[config.ENV_KEY]) == value['desktop_runtime_configuration']
    assert env['MENTORFLOW_TEST_RUNTIME_LEASE_ID']==value['runtime_lease_id']
    old=json.loads(Path(publication[1]['identity_evidence_path']).read_text())['test_device_configuration']
    assert old['issuer'] != json.loads(env[config.ENV_KEY])['issuer']
    assert old['tls_ca_sha256'] != json.loads(env[config.ENV_KEY])['tls_ca_sha256']


@pytest.mark.parametrize('field,wrong',[
    ('test_id','wrong'),('build_source_sha','f'*40),('build_source_tree','e'*40),
    ('desktop_artifact_sha256','d'*64),('status','SUPERSEDED'),('status','STOPPED'),
    ('superseded_by','NEWER'),('expires_at','2000-01-01T00:00:00Z'),
    ('created_at','2999-01-01T00:00:00Z'),('expires_at','2026-09-13'),
    ('ca_fingerprint','c'*64),('password','forbidden'),('token','forbidden'),
    ('command','arbitrary'),('environment',{'SSL_CERT_FILE':'bad'}),
    ('runtime_lease_id','../escape'),('private_key_content_in_manifest',True),
    ('cleanup_policy','-----BEGIN PRIVATE KEY----- payload'),
])
def test_bad_leases_never_spawn(publication,devices,field,wrong):
    value=lease(); value[field]=wrong; write(value) if field!='runtime_lease_id' else (r.AUTHORITY/'LATEST.json').write_text(json.dumps(value))
    spawn=Mock()
    with pytest.raises(t.Refused): launch(publication,devices,spawn)
    spawn.assert_not_called()


@pytest.mark.parametrize('field,wrong',[
    ('tls_ca_sha256','a'*64),('tls_ca_path','C:/untrusted/ca.pem'),
    ('issuer','http://insecure.invalid'),('verify_tls',False),('environment',{}),
    ('private_key','-----BEGIN PRIVATE KEY-----'),('storage_root','E:/MentorFlowData'),
])
def test_bad_client_config_never_spawn(publication,devices,field,wrong):
    value=lease(); value['desktop_runtime_configuration'][field]=wrong; write(value)
    spawn=Mock()
    with pytest.raises(t.Refused): launch(publication,devices,spawn)
    spawn.assert_not_called()


@pytest.mark.parametrize('attack',['missing','oversized','pointer_only','named_missing','ca_tampered','owner'])
def test_fixed_publication_and_ca_authority(publication,devices,monkeypatch,attack):
    value=lease(); latest=r.AUTHORITY/'LATEST.json'
    if attack=='missing': latest.unlink()
    elif attack=='oversized': latest.write_bytes(b' '*(r.MAX_BYTES+1))
    elif attack=='pointer_only':
        value['desktop_runtime_configuration']['issuer']='https://forged.invalid'; latest.write_text(json.dumps(value))
    elif attack=='named_missing': (r.AUTHORITY/(value['runtime_lease_id']+'.json')).unlink()
    elif attack=='ca_tampered': Path(value['desktop_runtime_configuration']['tls_ca_path']).write_bytes(b'bad CA')
    else: monkeypatch.setattr(r,'assert_owned',Mock(side_effect=t.Refused(r.UNAVAILABLE)))
    spawn=Mock()
    with pytest.raises(t.Refused): launch(publication,devices,spawn)
    spawn.assert_not_called()


def test_arbitrary_unapproved_file_not_used(publication,devices,tmp_path,monkeypatch):
    other=tmp_path/'rogue.json'; other.write_text(json.dumps(lease()))
    monkeypatch.setenv('MENTORFLOW_ACCEPTANCE_RUNTIME_LEASE',str(other))
    monkeypatch.setenv(config.ENV_KEY,json.dumps(lease()['desktop_runtime_configuration']))
    (r.AUTHORITY/'LATEST.json').unlink()
    with pytest.raises(t.Refused): launch(publication,devices,Mock())


@pytest.mark.skipif(os.name!='nt',reason='Windows reparse authority test')
def test_reparse_authority_refused(publication,devices,tmp_path,monkeypatch):
    original=r.AUTHORITY; junction=tmp_path/'alias'
    result=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(junction),str(original)],capture_output=True)
    assert result.returncode==0
    monkeypatch.setattr(r,'AUTHORITY',junction)
    try:
        with pytest.raises(t.Refused,match='UNTRUSTED_PATH'): launch(publication,devices,Mock())
    finally: junction.rmdir()


def test_expiry_rechecked_at_spawn(publication,devices,monkeypatch):
    value=lease(); now=datetime.now(timezone.utc); calls=0
    value['expires_at']=(now+timedelta(seconds=1)).isoformat(); write(value)
    class Clock:
        @staticmethod
        def now(_):
            nonlocal calls
            calls+=1
            return now if calls<3 else now+timedelta(seconds=2)
        fromisoformat=datetime.fromisoformat
    monkeypatch.setattr(r,'datetime',Clock)
    spawn=Mock()
    with pytest.raises(t.Refused): launch(publication,devices,spawn)
    spawn.assert_not_called()


def test_non_device_launch_never_reads_runtime(candidate,monkeypatch):
    manifest,paths=candidate
    monkeypatch.setattr(r,'verified_runtime',Mock(side_effect=AssertionError('production read lease')))
    spawn=Mock(return_value=SimpleNamespace(pid=1))
    t.launch_exact(manifest,manifest,lambda _:manifest['source_tree'],paths,spawn=spawn)
    assert 'env' not in spawn.call_args.kwargs


def test_new_contract_only_identity_without_pinned_endpoints(publication,devices):
    from tools.dev_workbench.test_candidate_identity import commit
    from tools.dev_workbench.authority import TARGET
    root,manifest,_,_,_=publication
    path=Path(manifest['identity_evidence_path']); identity=json.loads(path.read_text())
    identity.pop('test_device_configuration'); identity['runtime_contract']=config.CONTRACT
    identity['test_id']=manifest['test_id']; path.write_text(json.dumps(identity))
    manifest['identity_evidence_sha256']=t.sha256(path.read_bytes())
    (root/TARGET).write_text(json.dumps(manifest)); commit(root,'Publish contract-only identity')
    spawn=Mock(return_value=SimpleNamespace(pid=1,poll=lambda:None))
    launch(publication,devices,spawn)
    assert spawn.called


def test_public_ca_must_not_bundle_private_key(publication,devices):
    value=lease(); config=value['desktop_runtime_configuration']; path=Path(config['tls_ca_path'])
    content=path.read_bytes()+b'\n-----BEGIN PRIVATE KEY-----\ninert forbidden payload\n'
    path.write_bytes(content); config['tls_ca_sha256']=t.sha256(content); write(value)
    with pytest.raises(t.Refused,match='CA_MISMATCH'): launch(publication,devices,Mock())


@pytest.mark.skipif(os.name!='nt',reason='Windows deny-write/delete leases')
def test_publication_and_ca_pinned_through_spawn(publication,devices):
    value=lease()
    def spawn(*args,**kwargs):
        for path in (r.AUTHORITY/'LATEST.json',r.AUTHORITY/(value['runtime_lease_id']+'.json'),
                     Path(value['desktop_runtime_configuration']['tls_ca_path'])):
            with pytest.raises(OSError): path.write_bytes(b'attempted replacement')
            with pytest.raises(OSError): path.unlink()
        return SimpleNamespace(pid=1,poll=lambda:None)
    launch(publication,devices,spawn)


@pytest.mark.parametrize('raw',[
    b'{"schema":1,"schema":2}', b'{"schema":NaN}', b'['*2000+b']'*2000,
])
def test_malformed_bounded_json_refused(publication,raw):
    (r.AUTHORITY/'LATEST.json').write_bytes(raw)
    with pytest.raises(t.Refused):
        with r.verified_runtime(publication[1],publication[2]): pytest.fail('bad JSON admitted')
