"""Local operator primitives. Imported without side effects; used only by --execute."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

BASE='mentorflow-mf-u10-backend:MF-U10'
IMAGE='mentorflow-mf-u11-backend:MF-U11'
KC_IMAGE='mentorflow-mf-u11-keycloak:MF-U11'
PROVIDERS='/opt/keycloak/providers'
PROVIDER='mentorflow-native-verifier.jar'
FILES=(
    '/opt/mentorflow/releases/21337c1eda64f7e087fcade77c3d4cd7dce4a0af-exact-source/deploy/enterprise/staging/rp05/compose.account-core-mvp.yml',
    '/opt/mentorflow/artifacts/gatea-native-20260913-08/backend.override.json',
    '/opt/mentorflow/artifacts/r2-04-final-20260917/runtime.override.json',
    '/opt/mentorflow/artifacts/mf-d04-20260922/runtime.override.json')


def compose(env):
    argv=['docker','compose','--env-file',str(env),'-p','mentorflow-e1-preview','--profile','account-core-mvp']
    for path in FILES:argv+=['-f',path]
    return argv


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def digest(raw):return hashlib.sha256(raw).hexdigest()


def atomic(path, raw):
    temp=None
    try:
        with tempfile.NamedTemporaryFile(prefix=path.name+'.mf-u11-',dir=path.parent,delete=False) as stream:
            temp=Path(stream.name)
            os.chmod(temp,0o600);stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        if temp is not None:temp.unlink(missing_ok=True)


def bundle(package):
    m=read(package/'manifest.json')
    for name,sha in m['artifacts'].items():
        if digest((package/name).read_bytes())!=sha:raise ValueError('bundle differs: '+name)
    return m


KC_BASE='mentorflow-r2-keycloak:37f9507'
KC_ID='sha256:a744677a8b4682c2d64064f89af21c9db688fa68baaac616cc0611231c5871a5'
KC_JAR_SHA='367ab6e6724816eb3f06831d334c0fee096ee41e808f13d9d77f383ee34ec46b'
KC_FILES=(
    '/opt/mentorflow/releases/1531c65116063ec606ed1c623acd29fb68e9e8b7-exact-source/deploy/enterprise/staging/rp05/compose.account-core-mvp.yml',
    '/opt/mentorflow/artifacts/gatea-native-20260913-08/keycloak.override.json',
    '/opt/mentorflow/artifacts/gatea-password-policy-20260913-10/keycloak-denylist.override.json',
    '/opt/mentorflow/artifacts/gatea-password-policy-20260913-10/keycloak-native-fix.override.json',
    '/opt/mentorflow/artifacts/r2-04-final-20260917/runtime.override.json')


def kc_compose(env):
    args=['docker','compose','--env-file',str(env),'-p','mentorflow-e1-preview','--profile','account-core-mvp']
    for file in KC_FILES:args+=['-f',file]
    return args


def packaging_only(jar, name):
    if name.endswith('/'):return True
    if name!='META-INF/MANIFEST.MF':return False
    lines=[l for l in jar.read(name).decode('utf-8','replace').splitlines() if l.strip()]
    return all(l.split(':',1)[0] in ('Manifest-Version','Created-By') for l in lines)


def audit_provider(original, candidate):
    if digest(original.read_bytes())!=KC_JAR_SHA:raise ValueError('live provider differs from architect-confirmed SHA256')
    allowed=('win/clannad/mentorflow/NativeIdentityVerifier','win/clannad/mentorflow/EmailCodeReset')
    with zipfile.ZipFile(original) as old, zipfile.ZipFile(candidate) as new:
        if len(set(old.namelist()))!=len(old.namelist()) or len(set(new.namelist()))!=len(new.namelist()):raise ValueError('duplicate JAR entries')
        # Packaging-only entries: directory records and an attribute-free default manifest
        # (the live JAR came from the `jar` tool, the candidate from a zip writer).
        a={n for n in old.namelist() if not packaging_only(old,n)};b={n for n in new.namelist() if not packaging_only(new,n)}
        added=b-a;removed=a-b
        print(json.dumps({'old_classes':sorted(n for n in a if n.endswith('.class')),
                          'new_classes':sorted(n for n in b if n.endswith('.class'))},indent=2))
        if removed or not added or any(not re.fullmatch(r'win/clannad/mentorflow/RegistrationCodes(\$[A-Za-z0-9_]+)?\.class',n) for n in added):
            raise ValueError('unexpected class additions/removals: '+repr(sorted(added|removed)))
        differences={n for n in a&b if old.read(n)!=new.read(n)}
        unexpected={n for n in differences if not any(n==prefix+'.class' or n.startswith(prefix+'$') for prefix in allowed)}
        print(json.dumps({'changed_entries':sorted(differences),'added_entries':sorted(added)},indent=2))
        if unexpected:raise ValueError('unexpected unchanged-class/SPI difference; stop and review: '+repr(sorted(unexpected)))


def override_bytes(original, service, image):
    patched=json.loads(original)
    patched.setdefault('services',{}).setdefault(service,{})['image']=image
    return (json.dumps(patched,indent=2)+'\n').encode()


def compose_literal(value):
    """`docker compose config` prints a literal $ as $$; the running container holds the single $."""
    if isinstance(value, list):
        return [part.replace('$$', '$') if isinstance(part, str) else part for part in value]
    return value.replace('$$', '$') if isinstance(value, str) else value


def capture(env, kc_env, state, package, *, run=subprocess.run):
    bundle(package)
    def command_json(argv):return json.loads(run(argv,check=True,capture_output=True,text=True).stdout)
    before=command_json(compose(env)+['config','--format','json'])
    kc_before=command_json(kc_compose(kc_env)+['config','--format','json'])
    # Compose may pin Keycloak by tag or by the confirmed image ID; line 104 still requires the
    # running container and the tag to resolve to exactly KC_ID.
    if before['services']['backend']['image']!=BASE or kc_before['services']['keycloak']['image'] not in (KC_BASE,KC_ID):
        raise ValueError('base images differ from MF-U10 / confirmed Keycloak')
    settings=before['services']['backend'].get('environment',{})
    if any(str(settings.get(k))!='1' for k in ('WEB_CONCURRENCY','MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS')):raise ValueError('native auth requires single process')
    cid=run(kc_compose(kc_env)+['ps','-q','keycloak'],check=True,capture_output=True,text=True).stdout.strip()
    if not cid or '\n' in cid:raise ValueError('one running Keycloak required')
    container=command_json(['docker','inspect',cid])[0]
    if container['Image']!=KC_ID or command_json(['docker','image','inspect',KC_BASE])[0]['Id']!=KC_ID:
        raise ValueError('Keycloak image ID differs from architect audit')
    if container['State'].get('Health',{}).get('Status')!='healthy':raise ValueError('base Keycloak unhealthy')
    if any(m['Destination']==PROVIDERS or m['Destination'].startswith(PROVIDERS+'/') for m in container['Mounts']):
        raise ValueError('unexpected provider mount; original JAR must be in the image')
    service=kc_before['services']['keycloak']
    if (container['Config']['Entrypoint']!=compose_literal(service.get('entrypoint'))
            or container['Config']['Cmd']!=compose_literal(service.get('command'))):
        raise ValueError('Keycloak Compose does not reproduce its running command')
    state.mkdir(mode=0o700,parents=True,exist_ok=False)
    for label,config,path,service,image in (
        ('backend',before,FILES[-1],'backend',IMAGE),('keycloak',kc_before,KC_FILES[-1],'keycloak',KC_IMAGE)):
        original=Path(path).read_bytes()
        atomic(state/(label+'-compose.json'),json.dumps(config).encode())
        atomic(state/(label+'-override.json'),original)
        atomic(state/(label+'-patched.json'),override_bytes(original,service,image))
    run(['docker','cp',cid+':'+PROVIDERS+'/'+PROVIDER,str(state/'original-provider.jar')],check=True)
    audit_provider(state/'original-provider.jar',package/PROVIDER)
    atomic(state/'prepared.json',json.dumps({'keycloak_image':KC_ID,'provider_sha256':KC_JAR_SHA}).encode())


def configuration(action, env, kc_env, state, *, run=subprocess.run):
    if not (state/'prepared.json').is_file():raise ValueError('prepare/audit has not completed')
    policy=read(state/'update-password.json')
    if policy.get('alias')!='UPDATE_PASSWORD' or policy.get('enabled') is not True:raise ValueError('required-action backup missing or invalid')
    current={}
    for label,path in (('backend',FILES[-1]),('keycloak',KC_FILES[-1])):
        original=(state/(label+'-override.json')).read_bytes();patched=(state/(label+'-patched.json')).read_bytes()
        raw=Path(path).read_bytes()
        if raw not in (original,patched):raise ValueError('override changed independently: '+label)
        current[label]=raw==patched
    for label,command in (('backend',compose),('keycloak',kc_compose)):
        expected=read(state/(label+'-compose.json'))
        if current['keycloak']:expected['services']['keycloak']['image']=KC_IMAGE
        # The backend's final override is absent from the Keycloak file stack.
        if label=='backend' and current['backend']:expected['services']['backend']['image']=IMAGE
        actual=json.loads(run(command(env if label=='backend' else kc_env)+['config','--format','json'],check=True,capture_output=True,text=True).stdout)
        if actual!=expected:raise ValueError('merged configuration changed independently: '+label)
    if action=='check':return
    if action not in ('patch','restore'):raise ValueError('unknown action')
    for label,path in (('backend',FILES[-1]),('keycloak',KC_FILES[-1])):
        source=state/(label+('-patched.json' if action=='patch' else '-override.json'))
        atomic(Path(path),source.read_bytes())
