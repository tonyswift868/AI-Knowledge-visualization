"""ONE authorized Keycloak replacement, exact inspect-based rollback."""
import copy
import http.client
import json
import os
import pathlib
import shutil
import socket
import subprocess
import time

BASE=pathlib.Path('/opt/mentorflow/auth-email-security/e109')
os.umask(0o077)
NAME='mentorflow-e1-preview-keycloak-1'
OLD=NAME+'-e109-rollback'
marker=BASE/'backup/proxy-restart-attempt.json'
if marker.exists():
    raise SystemExit('RESTART_ALREADY_ATTEMPTED_NO_RETRY')

def inspect(name):
    return json.loads(subprocess.check_output(['docker','inspect',name]))[0]

class DockerConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(40)
        self.sock.connect('/var/run/docker.sock')

def docker(method,path,data=None):
    c=DockerConnection('localhost')
    payload=json.dumps(data) if data is not None else None
    c.request(method,'/v1.47'+path,body=payload,headers={'Content-Type':'application/json'})
    r=c.getresponse(); raw=r.read(); c.close()
    if r.status>=400:
        raise RuntimeError('DOCKER_'+method+'_HTTP_'+str(r.status))
    return json.loads(raw) if raw else None

original=inspect(NAME)
edge=inspect('mentorflow-e1-preview-edge-1')
env=dict(s.split('=',1) for s in original['Config']['Env'])
assert env['KC_PROXY_HEADERS']=='xforwarded'
common=set(original['NetworkSettings']['Networks'])&set(edge['NetworkSettings']['Networks'])
assert len(common)==1
network=common.pop()
proxy_ip=edge['NetworkSettings']['Networks'][network]['IPAddress']
assert proxy_ip and '/' not in proxy_ip and proxy_ip!='0.0.0.0'
endpoint=original['NetworkSettings']['Networks'][network]
keycloak_ip=endpoint['IPAddress']
aliases=endpoint.get('Aliases') or [NAME,'keycloak']
edge_conf=pathlib.Path(next(m['Source'] for m in edge['Mounts'] if m['Destination']=='/etc/nginx/nginx.conf')).read_text()
for required in ['proxy_set_header X-Forwarded-For $remote_addr;', 'proxy_set_header Forwarded "";', 'proxy_set_header X-Forwarded-Proto https;', 'proxy_set_header X-Forwarded-Port 18080;']:
    assert required in edge_conf
(BASE/'backup/keycloak-container-inspect.json').write_text(json.dumps(original,indent=2))
(BASE/'backup/edge-container-inspect.json').write_text(json.dumps(edge,indent=2))
labels=original['Config'].get('Labels',{})
compose_files=labels.get('com.docker.compose.project.config_files','').split(',')
for index,value in enumerate(compose_files):
    p=pathlib.Path(value)
    if p.is_file():
        shutil.copyfile(p,BASE/'backup'/('compose-'+str(index)+'.yml'))
        if (p.parent/'.env').is_file():
            shutil.copyfile(p.parent/'.env',BASE/'backup'/('compose-'+str(index)+'.env'))
config=copy.deepcopy(original['Config'])
config.pop('OnBuild',None)
config['Env']=[s for s in config['Env'] if not s.startswith('KC_PROXY_TRUSTED_ADDRESSES=')]+['KC_PROXY_TRUSTED_ADDRESSES='+proxy_ip]
config['HostConfig']=copy.deepcopy(original['HostConfig'])
config['NetworkingConfig']={'EndpointsConfig':{network:{'IPAMConfig':{'IPv4Address':keycloak_ip},'Aliases':aliases}}}
(BASE/'backup/keycloak-replacement-payload.json').write_text(json.dumps(config,indent=2))
marker.write_text(json.dumps({'state':'starting','original_id':original['Id'],'proxy_ip':proxy_ip,'keycloak_ip':keycloak_ip}))
new_id=None
renamed=False
disconnected=False
start=time.monotonic()
try:
    docker('POST','/containers/'+original['Id']+'/stop?t=20')
    docker('POST','/networks/'+network+'/disconnect',{'Container':original['Id'],'Force':False})
    disconnected=True
    docker('POST','/containers/'+original['Id']+'/rename?name='+OLD)
    renamed=True
    new_id=docker('POST','/containers/create?name='+NAME,config)['Id']
    docker('POST','/containers/'+new_id+'/start')
    deadline=time.monotonic()+150
    while time.monotonic()<deadline:
        state=inspect(NAME)['State']
        if state.get('Health',{}).get('Status')=='healthy':
            break
        if state['Status'] in ['exited','dead']:
            raise RuntimeError('NEW_KEYCLOAK_NOT_RUNNING')
        time.sleep(3)
    else:
        raise RuntimeError('KEYCLOAK_HEALTH_TIMEOUT')
    now=inspect(NAME)
    expected=copy.deepcopy(original['Config'])
    for k,v in expected.items():
        if k in ['Env','OnBuild']: continue
        assert now['Config'].get(k)==v, 'UNEXPECTED_CONFIG_CHANGE_'+k
    report={'state':'success','keycloak_running':True,'keycloak_ready':True,'proxy_headers':'xforwarded','trusted_address':proxy_ip,'old_id':original['Id'],'new_id':new_id,'window_seconds':round(time.monotonic()-start,1),'only_environment_change':'KC_PROXY_TRUSTED_ADDRESSES','rollback_container':OLD}
    marker.write_text(json.dumps(report,indent=2))
    (BASE/'evidence/proxy-restart.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report),flush=True)
except BaseException as exc:
    if new_id:
        docker('DELETE','/containers/'+new_id+'?force=true')
    if renamed:
        docker('POST','/containers/'+original['Id']+'/rename?name='+NAME)
    if disconnected:
        docker('POST','/networks/'+network+'/connect',{'Container':original['Id'],'EndpointConfig':{'IPAMConfig':{'IPv4Address':keycloak_ip},'Aliases':aliases}})
    docker('POST','/containers/'+original['Id']+'/start')
    marker.write_text(json.dumps({'state':'rolled_back','error_type':type(exc).__name__,'original_id':original['Id']}))
    raise SystemExit('KEYCLOAK_ROLLED_BACK_NO_RETRY: '+type(exc).__name__) from None
