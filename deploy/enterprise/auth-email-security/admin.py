"""Restricted server-side Keycloak audit. Never print credentials or user data."""
import hashlib
import sys
import json
import os
import pathlib
import subprocess
import urllib.error
import urllib.parse
import urllib.request

BASE = pathlib.Path('/opt/mentorflow/auth-email-security/e109')
os.umask(0o077)
for d in ('backup', 'evidence', 'config', 'secrets'):
    (BASE / d).mkdir(parents=True, exist_ok=True, mode=0o700)

def docker_inspect(name):
    return json.loads(subprocess.check_output(['docker','inspect',name]))[0]

kc = docker_inspect('mentorflow-e1-preview-keycloak-1')
env = dict(s.split('=',1) for s in kc['Config']['Env'])
ip = next(iter(kc['NetworkSettings']['Networks'].values()))['IPAddress']
origin = 'http://' + ip + ':8080'
secret_root = pathlib.Path(next(m['Source'] for m in kc['Mounts'] if m['Destination']=='/run/secrets'))
password = (secret_root/'keycloak_admin_password').read_text().strip()
body = urllib.parse.urlencode({'client_id':'admin-cli','grant_type':'password','username':env['KC_BOOTSTRAP_ADMIN_USERNAME'],'password':password}).encode()
headers = {'Content-Type':'application/x-www-form-urlencoded','X-Forwarded-Proto':'https'}
try:
    token = json.load(urllib.request.urlopen(urllib.request.Request(origin+'/realms/master/protocol/openid-connect/token',data=body,headers=headers),timeout=20))['access_token']
except urllib.error.HTTPError as e:
    raise SystemExit('ADMIN_AUTH_HTTP_' + str(e.code)) from None
del password, body

def api(path, method='GET', data=None):
    h = {'Authorization':'Bearer '+token, 'X-Forwarded-Proto':'https'}
    payload = None
    if data is not None:
        payload = json.dumps(data).encode()
        h['Content-Type'] = 'application/json'
    try:
        with urllib.request.urlopen(urllib.request.Request(origin+'/admin/'+path,data=payload,headers=h,method=method),timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise RuntimeError('ADMIN_API_HTTP_' + str(e.code) + ' path=' + path.split('?')[0]) from None

def private_save(name, value):
    p = BASE/'backup'/name
    if not p.exists():
        p.write_text(json.dumps(value,indent=2))
        p.chmod(0o600)

def audit():
    realm = api('realms/mentorflow')
    flows = api('realms/mentorflow/authentication/flows')
    executions = {f['alias']:api('realms/mentorflow/authentication/flows/'+urllib.parse.quote(f['alias'],safe='')+'/executions') for f in flows}
    roles = api('realms/mentorflow/roles')
    actions = api('realms/mentorflow/authentication/required-actions')
    profile = api('realms/mentorflow/users/profile')
    clients = api('realms/mentorflow/clients')
    users = api('realms/mentorflow/users?max=1000&briefRepresentation=false')
    user_snapshot = {u['id']:{'user':u,'roles':api('realms/mentorflow/users/'+u['id']+'/role-mappings'),'credentials':api('realms/mentorflow/users/'+u['id']+'/credentials')} for u in users}
    for name,value in [('realm.json',realm),('flows.json',flows),('executions.json',executions),('roles.json',roles),('required-actions.json',actions),('profile.json',profile),('clients.json',clients),('users.json',user_snapshot)]:
        private_save(name,value)
    for config_id in {e['authenticationConfig'] for rows in executions.values() for e in rows if e.get('authenticationConfig')}:
        private_save('auth-config-'+config_id+'.json',api('realms/mentorflow/authentication/config/'+config_id))
    default_id = realm['defaultRole']['id']
    default_roles = api('realms/mentorflow/roles-by-id/'+default_id+'/composites')
    private_save('default-composites.json',default_roles)
    edge = docker_inspect('mentorflow-e1-preview-edge-1')
    edge_path = pathlib.Path(next(m['Source'] for m in edge['Mounts'] if m['Destination']=='/etc/nginx/nginx.conf'))
    backup = BASE/'backup/nginx.conf'
    if not backup.exists():
        backup.write_bytes(edge_path.read_bytes())
    private_save('containers.json',{n:{'id':v['Id'],'started':v['State']['StartedAt'],'restarts':v['RestartCount']} for n in ['mentorflow-e1-preview-keycloak-1','mentorflow-e1-preview-edge-1','mentorflow-e1-preview-backend-1','mentorflow-e1-preview-postgres-1'] for v in [docker_inspect(n)]})
    allowed = ['registrationAllowed','registrationEmailAsUsername','verifyEmail','resetPasswordAllowed','loginWithEmailAllowed','duplicateEmailsAllowed','bruteForceProtected','permanentLockout','failureFactor','maxFailureWaitSeconds','minimumQuickLoginWaitSeconds','waitIncrementSeconds','quickLoginCheckMilliSeconds','maxDeltaTimeSeconds','passwordPolicy','browserFlow','registrationFlow','resetCredentialsFlow','loginTheme','actionTokenGeneratedByUserLifespan','attributes']
    safe = {k:realm.get(k) for k in allowed}
    safe['smtp_configured'] = bool(realm.get('smtpServer',{}).get('host'))
    safe['smtp_fields_present'] = list(realm.get('smtpServer',{}))
    safe['proxy_config'] = {k:v for k,v in env.items() if k.startswith('KC_PROXY') or k in ['KC_HOSTNAME','KC_HOSTNAME_STRICT','KC_FEATURES','KC_HTTP_ENABLED']}
    safe['network'] = {n:{'ip':v['IPAddress']} for n,v in kc['NetworkSettings']['Networks'].items()}
    safe['default_roles'] = [{'name':r['name'],'clientRole':r['clientRole']} for r in default_roles]
    safe['required_actions'] = [{k:a.get(k) for k in ['alias','enabled','defaultAction','priority','providerId']} for a in actions]
    safe['flows'] = executions
    safe['clients'] = [{k:c.get(k) for k in ['clientId','publicClient','directAccessGrantsEnabled','redirectUris','authenticationFlowBindingOverrides']} for c in clients]
    safe['existing_users_count'] = len(users)
    safe['master_clients'] = [c['clientId'] for c in api('realms/master/clients')]
    safe['master_users_count'] = api('realms/master/users/count')
    safe['authenticators'] = api('realms/mentorflow/authentication/authenticator-providers')
    safe['edge_path'] = str(edge_path)
    (BASE/'evidence/latest-config-audit.json').write_text(json.dumps(safe,indent=2))
    print(json.dumps({'backup_complete':True,'realm':'mentorflow','users_count':len(users),'smtp_configured':safe['smtp_configured']}))

def apply_policy(policy_path):
    policy = json.loads(pathlib.Path(policy_path).read_text())
    assert policy['schema_version'] == 'mentorflow.auth-email-security.v1'
    assert policy['realm'] == 'mentorflow'
    assert policy['realm_settings']['registrationAllowed'] is False
    # Always retain a rollback baseline before administrative writes.
    audit()
    original_users = json.loads((BASE/'backup/users.json').read_text())
    if policy['realm_settings'].get('verifyEmail'):
        assert all(not item['user'].get('email') or item['user'].get('emailVerified') for item in original_users.values()), 'LEGACY_UNVERIFIED_EMAIL_REQUIRES_REVIEW'
    root = 'realms/mentorflow'
    api(root, 'PUT', {'registrationAllowed': False})
    aliases = {f['alias'] for f in api(root+'/authentication/flows')}
    for key in ['registration_flow', 'reset_flow']:
        flow = policy[key]
        if flow['alias'] not in aliases:
            source = urllib.parse.quote(flow['source'], safe='')
            api(root+'/authentication/flows/'+source+'/copy', 'POST', {'newName': flow['alias']})
    for alias, values in policy['required_actions'].items():
        action = api(root+'/authentication/required-actions/'+alias)
        action.update(values)
        api(root+'/authentication/required-actions/'+alias, 'PUT', action)
    api(root+'/authentication/required-actions/UPDATE_EMAIL/config', 'PUT', {'config': policy['email_update']})
    profile = api(root+'/users/profile')
    profile.pop('unmanagedAttributePolicy', None)
    for attr in profile['attributes']:
        if attr['name'] == 'email':
            attr['required'] = {'roles': policy['email_required_roles']}
        elif attr['name'] in ['firstName', 'lastName']:
            attr.pop('required', None)
    api(root+'/users/profile', 'PUT', profile)
    registration_path = root+'/authentication/flows/'+policy['registration_flow']['alias']+'/executions'
    for execution in api(registration_path):
        if execution.get('providerId') == 'registration-password-action':
            execution['requirement'] = 'REQUIRED'
            api(registration_path, 'PUT', execution)
    reset_path = root+'/authentication/flows/'+policy['reset_flow']['alias']+'/executions'
    for execution in api(reset_path):
        if execution.get('providerId') == 'reset-otp' or (execution.get('authenticationFlow') and 'Conditional OTP' in execution['displayName']):
            execution['requirement'] = 'DISABLED'
            api(reset_path, 'PUT', execution)
        if execution.get('providerId') == 'reset-credential-email':
            config = {'alias': 'e109-reset-requires-login', 'config': {'force-login': 'true'}}
            if execution.get('authenticationConfig'):
                api(root+'/authentication/config/'+execution['authenticationConfig'], 'PUT', config)
            else:
                api(root+'/authentication/executions/'+execution['id']+'/config', 'POST', config)
    name = policy['default_new_user_group']
    groups = api(root+'/groups?search='+urllib.parse.quote(name)+'&exact=true')
    if not groups:
        api(root+'/groups', 'POST', {'name': name})
        groups = api(root+'/groups?search='+urllib.parse.quote(name)+'&exact=true')
    group = next(g for g in groups if g['name'] == name)
    learner = api(root+'/roles/'+policy['default_new_user_role'])
    assert learner['name'] == 'LEARNER' and not learner['composite']
    api(root+'/groups/'+group['id']+'/role-mappings/realm', 'POST', [learner])
    api(root+'/default-groups/'+group['id'], 'PUT')
    settings = dict(policy['realm_settings'])
    settings.update(registrationFlow=policy['registration_flow']['alias'], resetCredentialsFlow=policy['reset_flow']['alias'])
    api(root, 'PUT', settings)
    for locale in set(api(root+'/localization')) | {'en', 'zh-CN'}:
        old = api(root+'/localization/'+locale)
        private_save('localization-'+locale+'.json', old)
        generic = old.get('invalidUserMessage') or ('账号或密码不正确，请重试。' if locale.startswith('zh') else 'Invalid username or password.')
        keys = ['invalidUserMessage', 'invalidUsernameMessage', 'accountDisabledMessage', 'accountTemporarilyDisabledMessage', 'accountPermanentlyDisabledMessage']
        values = {key: generic for key in keys}
        values['invalidPasswordRegexPatternMessage'] = '该密码过于常见，请换一个不同的密码。' if locale.startswith('zh') else 'This password is too common. Choose a different password.'
        api(root+'/localization/'+locale, 'POST', values)
    print('POLICY_APPLIED_PUBLIC_REGISTRATION_OFF_NO_USER_WRITES')

def apply_smtp(config_path):
    config = json.loads(pathlib.Path(config_path).read_text())
    assert config['SMTP_MODE'] in ['MAILPIT_UAT_ONLY', 'REAL_RELAY']
    assert config['SMTP_HOST'] and config['SMTP_FROM'] and 1 <= int(config['SMTP_PORT']) <= 65535
    if config['SMTP_MODE'] == 'REAL_RELAY':
        assert config.get('SMTP_STARTTLS') or config.get('SMTP_SSL'), 'REAL_SMTP_REQUIRES_TLS'
    else:
        assert config['SMTP_HOST'] == 'mentorflow-e109-mailpit'
    credential = os.environ.get(config.get('SMTP_SECRET_ENV', 'SMTP_SECRET'))
    if not credential:
        path = pathlib.Path(config['SMTP_SECRET_FILE']).resolve()
        assert path.is_relative_to((BASE/'secrets').resolve()), 'SECRET_STORE_PATH_REQUIRED'
        assert path.stat().st_mode & 0o077 == 0, 'SECRET_FILE_MUST_BE_PRIVATE'
        credential = path.read_text().strip()
    assert credential and config['SMTP_USER']
    smtp = {'host': config['SMTP_HOST'], 'port': str(config['SMTP_PORT']), 'user': config['SMTP_USER'], 'password': credential, 'from': config['SMTP_FROM'], 'auth': 'true', 'starttls': str(bool(config.get('SMTP_STARTTLS'))).lower(), 'ssl': str(bool(config.get('SMTP_SSL'))).lower()}
    api('realms/mentorflow', 'PUT', {'smtpServer': smtp, 'registrationAllowed': False})
    print('SMTP_APPLIED_SECRET_NOT_OUTPUT_PUBLIC_REGISTRATION_OFF')

if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['backup']:
            audit()
        elif len(sys.argv) == 3 and sys.argv[1] == 'apply-policy':
            apply_policy(sys.argv[2])
        elif len(sys.argv) == 3 and sys.argv[1] == 'apply-smtp':
            apply_smtp(sys.argv[2])
        else:
            raise SystemExit('Usage: admin.py backup | apply-policy FILE | apply-smtp FILE')
    except (OSError, RuntimeError, AssertionError, KeyError, ValueError) as exc:
        # Deliberately do not print HTTP bodies, environment or configuration.
        raise SystemExit('ADMIN_OPERATION_FAILED: '+type(exc).__name__) from None
