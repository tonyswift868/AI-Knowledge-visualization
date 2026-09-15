"""Independent processes exercising the existing authenticated server API.

Credentials travel only over stdin; receipts contain synthetic data, never tokens.
No database seeding, filesystem copying, or desktop UI acceptance is claimed.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


def device(request):
    import httpx
    from desktop.structured_sync import StructuredSyncClient
    root = Path(request['root'])
    root.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=request['endpoint'], trust_env=False, timeout=20,
                      headers={'Authorization': 'Bearer ' + request['bearer']}) as http:
        bootstrap = http.post('/api/enterprise/account/workspaces/initialize', json={})
        bootstrap.raise_for_status()
        workspace = bootstrap.json()['workspaces'][0]
        http.headers.update({'X-MentorFlow-Organization-ID': workspace['organization_id'],
                             'X-MentorFlow-Workspace-ID': workspace['id']})
        me = http.get('/api/enterprise/auth/me'); me.raise_for_status()
        assert me.json()['authenticated'] is True
        local = root / 'local-only-model.bin'
        client = StructuredSyncClient(http, resource_is_present=lambda item:
            local.is_file() and hashlib.sha256(local.read_bytes()).hexdigest() == item['payload']['sha256'])
        writes = []
        if request['action'] == 'create':
            assert not local.exists()
            local.write_bytes(b'owned disposable model bytes\n' * 4096)
            payloads = {
                'resource': {'filename':local.name,'media_type':'application/octet-stream',
                    'sha256':hashlib.sha256(local.read_bytes()).hexdigest(),
                    'size_bytes':local.stat().st_size,'product_classification':'MODEL'},
                'plan': {'title':'Learn statistics','resource_ids':['resource-seal']},
                'progress': {'plan_id':'plan-seal','mastery':0.25,'completed_tasks':1},
                'learner_profile': {'display_name':'Disposable learner'},
                'setting': {'theme':'dark','locale':'zh-CN'},
            }
            for kind, payload in payloads.items():
                writes.append(client.write(kind,kind+'-seal',request_id='create-'+kind,
                    expected_revision=0,payload=payload))
            writes.append(client.write('plan','plan-seal',request_id='edit-a-plan',
                expected_revision=1,payload={'title':'Learn statistics A revised','resource_ids':['resource-seal']}))
        before = client.rehydrate()
        if request['action'] == 'update':
            assert not local.exists()
            for kind, key, payload in (
                ('plan','plans',{'title':'Learn statistics B revised','resource_ids':['resource-seal']}),
                ('progress','progress',{'plan_id':'plan-seal','mastery':0.75,'completed_tasks':3})):
                row=before[key][0]
                writes.append(client.write(kind,row['entity_id'],request_id='edit-b-'+kind,
                    expected_revision=row['revision'],payload=payload))
        result = {'workspace':workspace,'before':before,'after':client.rehydrate(),
                  'writes':writes,'physical_file_exists':local.exists(),'pid':os.getpid()}
        if request['action'] == 'isolation':
            other=request['other_workspace']
            denied=http.get(client.PREFIX+'/snapshot',headers={
                'X-MentorFlow-Organization-ID':other['organization_id'],
                'X-MentorFlow-Workspace-ID':other['id']})
            assert denied.status_code == 403, 'SERVER_MUST_REJECT_OTHER_ACCOUNT_SCOPE'
            result['denial_status']=denied.status_code
            result['denial_code']=denied.json().get('code')
        return result


def prove_continuity(source, endpoint, bearer, scratch):
    base=Path('E:/Codex/Temp/MF04-FOUNDATION-20260915/devices-2')
    run='seal-'+uuid.uuid4().hex
    roots={d:base/d/run for d in ('A','B')}
    assert all(not root.exists() for root in roots.values())
    def invoke(which, action, *, subject='sealing-account', other=None):
        args={'endpoint':endpoint,'root':str(roots[which]),'bearer':bearer(subject),'action':action}
        if other: args['other_workspace']=other
        env=dict(os.environ, PYTHONPATH=str(source)+os.pathsep+str(source/'backend'),
            MENTORFLOW_USER_DATA_ROOT=str(roots[which]), MENTORFLOW_ORM_DB=str(roots[which]/'orm.sqlite'))
        p=subprocess.run([sys.executable,'-B',str(source/'scripts/sync_device_continuity.py')],
            input=json.dumps(args),capture_output=True,text=True,env=env,cwd=source,timeout=55,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if p.returncode: raise AssertionError('DEVICE_PROCESS_FAILED:'+p.stderr[-1500:])
        return json.loads(p.stdout)
    a=invoke('A','create') # process is terminated and transport closed before B starts
    b=invoke('B','update')
    for key in ('plans','progress','learner_profile','setting'):
        assert a['after'][key] == b['before'][key], key
    assert a['workspace']['id']==b['workspace']['id']
    assert a['after']['resources'][0]['payload']==b['before']['resources'][0]['payload']
    assert b['before']['resources'][0]['device_availability']=='MISSING_ON_THIS_DEVICE'
    assert not b['physical_file_exists']
    a_restart=invoke('A','read')
    b_restart=invoke('B','read')
    for key in ('plans','progress','learner_profile','setting'):
        assert b['after'][key]==a_restart['after'][key]==b_restart['after'][key], key
    isolation=invoke('B','isolation',subject='sealing-other-account',other=a['workspace'])
    assert not isolation['after']['plans']
    evidence={'roots':{k:str(v) for k,v in roots.items()},'filesystem_copy_a_to_b':0,
        'a':a,'b':b,'a_restart':a_restart,'b_restart':b_restart,
        'isolation':{'status':isolation['denial_status'],'code':isolation['denial_code']},
        'auth':'EPHEMERAL_RS256_SERVER_VERIFIED', 'authority':'EXISTING_STRUCTURED_SYNC_HTTP_API',
        'desktop_ui_uat':'NOT_RUN'}
    (scratch/'device-continuity.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    return evidence

if __name__=='__main__':
    print(json.dumps(device(json.load(sys.stdin))))
