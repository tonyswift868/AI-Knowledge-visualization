"""MF-U11 operator guards, using in-memory commands only; no Docker/IdP calls."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS
import zipfile
import pytest

PACKAGE=Path(__file__).resolve().parents[2]/'deploy/enterprise/staging/rp05/mf-u11'


@pytest.fixture
def modules(monkeypatch):
    def load(name,file):
        spec=importlib.util.spec_from_file_location(name,PACKAGE/file)
        module=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,name,module);spec.loader.exec_module(module);return module
    ops=load('package_ops','package_ops.py');realm=load('realm_admin','realm_admin.py')
    return ops,realm,load('mfu11_release','release.py'),load('mfu11_pending','list_pending_registrations.py')


def args(tmp_path):
    return NS(env_file=tmp_path/'backend.env',keycloak_env_file=tmp_path/'keycloak.env',state_dir=tmp_path/'state',
        origin='https://identity.invalid',ca_file=tmp_path/'ca.pem',token_file=tmp_path/'token')


def test_mfu11_defaults_are_print_only_and_both_image_checks_precede_stop(modules,tmp_path,monkeypatch,capsys):
    ops,realm,release,_=modules
    monkeypatch.setattr(release.subprocess,'run',lambda *a,**k:pytest.fail('dry run executed a command'))
    release.main(['deploy','--env-file','backend.env','--keycloak-env-file','keycloak.env','--state-dir','state',
        '--origin','https://identity.invalid','--ca-file','ca.pem','--token-file','token'])
    assert 'DRY RUN' in capsys.readouterr().out
    plan=release.commands('deploy',args(tmp_path),PACKAGE)
    stop=next(i for i,c in enumerate(plan) if c[-2:]==['stop','backend'])
    assert any('/bundle/image_check.py' in c and 'candidate' in c for c in plan[:stop])
    assert any('win.clannad.mentorflow.check.VerifierLinkCheck' in c for c in plan[:stop])
    assert not any(any('alembic' in v or 'downgrade' in v for v in c) for c in plan)
    assert ops.FILES[0]!=ops.KC_FILES[0] and ops.KC_FILES[-1] in ops.FILES


def test_mfu11_unhealthy_backend_automatically_restores_both_services(modules,tmp_path):
    _,_,release,_=modules;calls=[];failed=False
    def run(command,**kwargs):
        nonlocal failed
        calls.append(command)
        if 'healthy-backend' in command and not failed:
            failed=True;raise subprocess.CalledProcessError(1,command)
    with pytest.raises(RuntimeError,match='rollback is healthy'):
        release.operate('deploy',args(tmp_path),PACKAGE,run=run)
    assert sum('healthy-backend' in c for c in calls)==2
    assert any('restore' in c for c in calls)
    assert any('realm_admin.py' in ' '.join(c) and 'rollback' in c for c in calls)


def test_mfu11_preflight_failure_does_not_touch_services(modules,tmp_path):
    _,_,release,_=modules;calls=[]
    def run(command,**kwargs):calls.append(command);raise ValueError('audit failed')
    with pytest.raises(ValueError):release.operate('deploy',args(tmp_path),PACKAGE,run=run)
    assert len(calls)==1 and not any('stop' in c for c in calls)


def test_mfu11_expired_admin_token_during_rollback_does_not_leave_backend_down(modules,tmp_path):
    _,_,release,_=modules;calls=[]
    def run(command,**kwargs):
        calls.append(command)
        if str(PACKAGE/'realm_admin.py') in command:raise ValueError('admin credential expired')
    with pytest.raises(RuntimeError,match='realm policy rollback'):
        release.operate('rollback',args(tmp_path),PACKAGE,run=run)
    assert calls[-1][2]=='healthy-backend'


def test_mfu11_health_deadline_and_missing_healthcheck(modules,tmp_path):
    _,_,release,_=modules;now=[0]
    def run(command,**kwargs):
        assert 0<kwargs['timeout']<=5
        return NS(stdout='cid\n' if 'compose' in command else 'running starting\n')
    with pytest.raises(TimeoutError):
        release.wait_healthy('backend',tmp_path/'env',run=run,clock=lambda:now[0],sleep=lambda n:now.__setitem__(0,now[0]+n))
    assert now[0]==90
    def missing(command,**kwargs):return NS(stdout='cid\n' if 'compose' in command else 'running missing\n')
    with pytest.raises(RuntimeError,match='missing'):release.wait_healthy('keycloak',tmp_path/'env',run=missing)


def test_mfu11_two_stack_configuration_preserves_unrelated_settings_and_partial_rollback(modules,tmp_path,monkeypatch):
    ops,_,_,_=modules;state=tmp_path/'state';state.mkdir()
    paths={name:tmp_path/(name+'.json') for name in ('backend','keycloak')}
    monkeypatch.setattr(ops,'FILES',('shared',str(paths['keycloak']),str(paths['backend'])))
    monkeypatch.setattr(ops,'KC_FILES',('identity',str(paths['keycloak'])))
    before={'services':{'backend':{'image':ops.BASE,'environment':{'keep':'yes'}},'keycloak':{'image':ops.KC_BASE,'volumes':[{'source':'unchanged','target':'/run/secrets'}]},'postgres':{'image':'keep'}}}
    kc_before=deepcopy(before);kc_before['services']['backend']['image']='old-unused-backend'
    for label,image,config in (('backend',ops.IMAGE,before),('keycloak',ops.KC_IMAGE,kc_before)):
        raw=json.dumps({'services':{label:config['services'][label]}}).encode()
        paths[label].write_bytes(raw);(state/(label+'-override.json')).write_bytes(raw)
        (state/(label+'-patched.json')).write_bytes(ops.override_bytes(raw,label,image))
        (state/(label+'-compose.json')).write_text(json.dumps(config))
    (state/'prepared.json').write_text('{}');(state/'update-password.json').write_text('{"alias":"UPDATE_PASSWORD","enabled":true}')
    def run(command,**kwargs):
        data=deepcopy(kc_before if 'identity' in command else before)
        data['services']['keycloak']['image']=json.loads(paths['keycloak'].read_text())['services']['keycloak']['image']
        if 'identity' not in command:data['services']['backend']['image']=json.loads(paths['backend'].read_text())['services']['backend']['image']
        return NS(stdout=json.dumps(data))
    ops.configuration('patch','backend.env','identity.env',state,run=run)
    ops.configuration('check','backend.env','identity.env',state,run=run)
    # A failed write between the two override switches is recoverable.
    paths['keycloak'].write_bytes((state/'keycloak-override.json').read_bytes())
    ops.configuration('restore','backend.env','identity.env',state,run=run)
    assert paths['backend'].read_bytes()==(state/'backend-override.json').read_bytes()
    paths['backend'].write_text('{"independent":"change"}')
    with pytest.raises(ValueError,match='independently'):ops.configuration('restore','backend.env','identity.env',state,run=run)


def test_mfu11_policy_is_idempotent_scoped_and_reversible(modules,tmp_path):
    _,realm,_,_=modules
    original={'alias':'UPDATE_PASSWORD','enabled':True,'defaultAction':True,'priority':30,'config':{'keep':'yes'}}
    class Client:
        def __init__(self):self.value=deepcopy(original);self.writes=[]
        def call(self,path,method='GET',value=None):
            assert path==realm.ACTION
            if method=='PUT':self.writes.append(value);self.value=deepcopy(value)
            return deepcopy(self.value)
    c=Client();backup=tmp_path/'policy.json'
    realm.policy(c,'capture',backup);realm.policy(c,'check',backup)
    assert not c.writes
    realm.policy(c,'apply',backup);realm.policy(c,'apply',backup)
    assert len(c.writes)==1 and c.value=={**original,'defaultAction':False}
    realm.policy(c,'rollback',backup);realm.policy(c,'rollback',backup)
    assert c.value==original and len(c.writes)==2
    c.value['config']={}
    with pytest.raises(ValueError,match='changed'):realm.policy(c,'apply',backup)


def test_mfu11_jar_audit_rejects_unexpected_class_or_spi_changes(modules,tmp_path,monkeypatch):
    ops,_,_,_=modules;old=tmp_path/'old.jar';new=tmp_path/'new.jar'
    prefix='win/clannad/mentorflow/'
    oldfiles={prefix+'NativeIdentityVerifier.class':b'old',prefix+'AtomicActionTokens.class':b'keep','META-INF/services/spi':b'keep'}
    def write(path,files):
        with zipfile.ZipFile(path,'w') as jar:
            for key,value in files.items():jar.writestr(key,value)
    write(old,oldfiles);monkeypatch.setattr(ops,'KC_JAR_SHA',ops.digest(old.read_bytes()))
    values=oldfiles|{prefix+'NativeIdentityVerifier.class':b'new',prefix+'RegistrationCodes.class':b'new'}
    write(new,values);ops.audit_provider(old,new)
    write(new,values|{'META-INF/services/spi':b'bad'})
    with pytest.raises(ValueError,match='unexpected'):ops.audit_provider(old,new)


def test_mfu11_pending_registration_listing_is_readonly(modules):
    _,_,_,listing=modules
    class Client:
        def call(self,path,method='GET',value=None):
            assert method=='GET' and value is None
            if '/groups?' in path:return [{'id':'g','name':'self-registered-learners-e109'}]
            if '/members?' in path:return [{'id':'legacy'}]
            return [{'id':'new','emailVerified':False,'attributes':{'mf.registration.v1':['pending']}},
                {'id':'done','emailVerified':True,'attributes':{'mf.registration.v1':['verified']}},
                {'id':'legacy','emailVerified':False},{'id':'admin-unverified','emailVerified':False}]
    rows=list(listing.pending(Client()))
    assert [row['id'] for row in rows]==['new','legacy']
    assert 'review' in rows[1]['provenance'] and all(row['action'].startswith('NONE') for row in rows)


def test_mfu11_overlay_tracks_runtime_and_bundle_hashes(modules):
    ops,_,_,_=modules
    manifest=ops.bundle(PACKAGE);root=PACKAGE.parents[4]
    # Git enforces LF in this package. Hashes must survive checkout on Linux.
    for name in [*manifest['artifacts'],'manifest.json']:
        if not name.endswith('.jar'):
            assert b'\r\n' not in (PACKAGE/name).read_bytes(), name
    for name in manifest['sha256']:
        assert (PACKAGE/'overlay'/name).read_bytes()==(root/name).read_bytes().replace(b'\r\n',b'\n')
    assert (PACKAGE/'realm-policy.json').read_bytes()==(root/'deploy/enterprise/auth-email-security/realm-policy.json').read_bytes().replace(b'\r\n',b'\n')


def test_mfu11_compose_literal_matches_running_command(modules):
    ops = modules[0] if isinstance(modules, tuple) else modules.package_ops
    printed = ['KC_X="$$(cat /run/secrets/x)"; test -n "$$KC_X"']
    assert ops.compose_literal(printed) == ['KC_X="$(cat /run/secrets/x)"; test -n "$KC_X"']
    assert ops.compose_literal(['/bin/sh', '-ec']) == ['/bin/sh', '-ec'] and ops.compose_literal(None) is None
