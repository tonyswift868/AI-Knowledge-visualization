"""Offline Docker-command/state tests; never connects to any Docker daemon."""
from copy import deepcopy
import ipaddress
import json
from pathlib import Path
import pytest
import yaml

from refactor.server.ops.network45 import (EDGE, OLD_EDGE, NETWORKS, Migration, affected,
    connect_command, create_command, endpoints, name, ordered, validate_inventory)


INTERNAL, PUBLIC = NETWORKS


def endpoint(ip, alias, *, static=False):
    return dict(IPAddress=ip, IPAMConfig=dict(IPv4Address=ip) if static else None,
                Aliases=[alias], Links=None, GlobalIPv6Address='', DriverOpts=None)


def fixture_inventory(config_file):
    networks=[]
    for n,(subnet,_,_) in NETWORKS.items():
        networks.append(dict(Name=n,Driver='bridge',Internal=n==INTERNAL,Attachable=False,
            EnableIPv6=False,Options={'com.docker.network.driver.mtu':'1500'},
            Labels={'com.docker.compose.project':'mentorflow-e1-preview','com.docker.compose.network':n.rsplit('_',1)[1],
                    'com.docker.compose.config-hash':'old-hash'},
            IPAM=dict(Driver='default',Config=[dict(Subnet=subnet,Gateway=str(ipaddress.ip_network(subnet)[1]))],Options=None)))
    rows=[]
    for service,ip in [('postgres','172.18.0.6'),('keycloak','172.18.0.4'),('backend','172.18.0.3'),
                       ('mailpit','172.18.0.2'),('update-static','172.19.0.3'),('edge','172.18.0.5')]:
        cname=EDGE if service=='edge' else 'mentorflow-e1-preview-'+service+'-1'
        n=PUBLIC if service=='update-static' else INTERNAL
        rows.append(dict(Id='id-'+service,Name='/'+cname,Image='sha256:already-local-nginx',
            State=dict(Running=True,Status='running',Health=dict(Status='healthy')),
            Config=dict(Labels={'com.docker.compose.service':service,'com.docker.compose.project':'mentorflow-e1-preview',
                               'com.docker.compose.project.config_files':str(config_file),
                               'com.docker.compose.project.working_dir':str(config_file.parent)},
                        Env=['KC_PROXY_TRUSTED_ADDRESSES=172.18.0.5'] if service=='keycloak' else []),
            HostConfig=dict(NetworkMode=n,RestartPolicy=dict(Name='unless-stopped',MaximumRetryCount=0)),Mounts=[],
            NetworkSettings=dict(Networks={n:endpoint(ip,service,static=service=='edge')})))
    rows[-1]['NetworkSettings']['Networks'][PUBLIC]=endpoint('172.19.0.2','edge',static=True)
    old=deepcopy(rows[-1]);old.update(Id='old-edge',Name='/'+OLD_EDGE)
    old['State'].update(Running=False,Status='exited')
    for e in old['NetworkSettings']['Networks'].values(): e['IPAddress']=''
    rows.append(old)
    child=deepcopy(rows[2]);child.update(Id='id-proxy',Name='/mentorflow-e1-preview-oidc-loopback-proxy-1')
    child['HostConfig']['NetworkMode']='container:id-backend'
    child['Config']['Labels']['com.docker.compose.service']='oidc-loopback-proxy'
    child['NetworkSettings']['Networks']={};rows.append(child)
    # A stopped helper remains stopped; unrelated smtp-egress membership is untouched.
    helper=deepcopy(rows[2]);helper.update(Id='id-helper',Name='/stopped-helper')
    helper['State'].update(Running=False,Status='exited')
    helper['NetworkSettings']['Networks']={INTERNAL:endpoint('','helper')};rows.append(helper)
    outside=deepcopy(rows[2]);outside.update(Id='id-outside',Name='/unrelated')
    outside['NetworkSettings']['Networks']={'smtp-egress':endpoint('172.20.0.2','outside')};rows.append(outside)
    return networks,rows


class FakeDocker:
    def __init__(self,networks,containers):
        self.networks={n['Name']:deepcopy(n) for n in networks}
        self.containers={c['Id']:deepcopy(c) for c in containers}
        self.commands=[];self.fail_create=None;self.http='200'
    def find(self,identifier):
        return next(c for c in self.containers.values() if identifier in (c['Id'],name(c)))
    def __call__(self,args):
        self.commands.append(args)
        if args[0]=='curl':return self.http
        assert args[0]=='docker';a=args[1:]
        if a[0]=='compose':
            last=Path(a[len(a)-1-a[::-1].index('-f')+1])
            value=yaml.safe_load(last.read_text(encoding='utf-8'))
            return json.dumps(value)
        if a[:2]==['ps','-aq']:return '\n'.join(self.containers)
        if a[0]=='inspect':return json.dumps([self.find(i) for i in a[1:]])
        if a[:2]==['network','inspect']:return json.dumps([self.networks[n] for n in a[2:]])
        if a[:2]==['network','ls']:return '\n'.join(self.networks)
        if a[:2]==['network','rm']:
            n=a[-1];assert not any(n in endpoints(c) for c in self.containers.values())
            del self.networks[n];return n
        if a[:2]==['network','create']:
            if self.fail_create==a[-1]: self.fail_create=None;raise RuntimeError('injected create failure')
            n=a[-1]
            def option(flag,default=None):return a[a.index(flag)+1] if flag in a else default
            config=dict(Subnet=option('--subnet'),Gateway=option('--gateway'))
            if '--ip-range' in a:config['IPRange']=option('--ip-range')
            self.networks[n]=dict(Name=n,Driver='bridge',Internal='--internal' in a,EnableIPv6=False,
                IPAM=dict(Driver='default',Config=[config]),Options={},Labels={})
            return n
        if a[:2]==['network','disconnect']:
            n,identifier=a[-2:];self.find(identifier)['NetworkSettings']['Networks'].pop(n,None);return ''
        if a[:2]==['network','connect']:
            n,identifier=a[-2:];c=self.find(identifier)
            ip=a[a.index('--ip')+1] if '--ip' in a else ''
            aliases=[a[i+1] for i,x in enumerate(a) if x=='--alias']
            c['NetworkSettings']['Networks'][n]=dict(IPAddress='',IPAMConfig=dict(IPv4Address=ip) if ip else None,Aliases=aliases)
            return ''
        if a[0]=='update':
            c=self.find(a[-1]);value=a[1].split('=',1)[1]
            c['HostConfig']['RestartPolicy']=dict(Name=value.split(':')[0],MaximumRetryCount=int(value.split(':')[1]) if ':' in value else 0)
            return c['Id']
        if a[0]=='stop':
            self.find(a[-1])['State'].update(Running=False,Status='exited');return ''
        if a[0]=='start':
            c=self.find(a[-1])
            for n,e in endpoints(c).items():
                if n not in self.networks:continue
                config=self.networks[n]['IPAM']['Config'][0]
                ip=(e.get('IPAMConfig') or {}).get('IPv4Address') or e.get('IPAddress')
                used={ep.get('IPAddress') for other in self.containers.values() if other is not c and other['State']['Running']
                      for net,ep in endpoints(other).items() if net==n}
                if not ip:
                    ip=next(str(x) for x in ipaddress.ip_network(config.get('IPRange') or config['Subnet']).hosts()
                            if str(x) not in used and str(x)!=config['Gateway'])
                if ip in used:raise RuntimeError('Address already in use')
                e['IPAddress']=ip
            c['State'].update(Running=True,Status='running');return c['Id']
        if a[0]=='run':
            probe=a[a.index('--name')+1];n=a[a.index('--network')+1]
            c=deepcopy(next(iter(self.containers.values())));c.update(Id=probe,Name='/'+probe)
            c['HostConfig']['NetworkMode']=n;c['NetworkSettings']['Networks']={n:endpoint('',probe)}
            c['State'].update(Running=False,Status='exited');self.containers[probe]=c
            return self(['docker','start',probe])
        if a[0]=='rm':
            assert name(self.find(a[-1])).startswith('mentorflow-bug45-pool-probe-')
            del self.containers[a[-1]];return ''
        raise AssertionError(args)


@pytest.fixture
def case(tmp_path):
    config=tmp_path/'current.yml';config.write_text('services: {}\n',encoding='utf-8')
    networks,containers=fixture_inventory(config)
    docker=FakeDocker(networks,containers)
    migration=Migration(tmp_path/'state',docker)
    migration.backup()
    overlay=Path(__file__).resolve().parents[3]/'refactor/server/compose.network45.yml'
    return migration,docker,overlay


def cutover(migration,overlay):
    migration.check_compose(overlay)
    migration.quiesce();migration.detach();migration.rebuild();migration.attach();migration.start();migration.verify()


def test_network45_complete_migration_preserves_addresses_aliases_and_stopped_helpers(case):
    migration,docker,overlay=case;old=deepcopy(docker.containers)
    cutover(migration,overlay)
    for identifier,c in old.items():
        current=docker.find(identifier)
        if name(c)==OLD_EDGE:
            assert not endpoints(current) and not current['State']['Running']
            assert current['HostConfig']['RestartPolicy']['Name']=='no';continue
        assert current['State']['Running']==c['State']['Running']
        if identifier=='id-outside':assert current==c
        for n,e in endpoints(c).items():
            if e['IPAddress']:assert endpoints(current)[n]['IPAddress']==e['IPAddress']
            assert endpoints(current)[n]['Aliases']==e['Aliases']
        assert current['HostConfig']['RestartPolicy']==c['HostConfig']['RestartPolicy']
    assert all('down' not in cmd and 'prune' not in cmd for cmd in docker.commands)
    assert not any(cmd[:2]==['docker','rm'] for cmd in docker.commands)


def test_network45_worst_order_other_containers_and_new_dynamic_allocations_before_edge(case):
    migration,docker,overlay=case;cutover(migration,overlay)
    start=len(docker.commands);migration.rehearse();migration.verify()
    result=json.loads((migration.state/'rehearsal.json').read_text())
    assert len(result)==2
    for row in result:assert ipaddress.ip_address(row['probe_ip']) in ipaddress.ip_network(NETWORKS[row['network']][1])
    calls=docker.commands[start:]
    last_start=next(i for i,cmd in enumerate(calls) if cmd==['docker','start','id-edge'])
    assert all(calls.index(['docker','start',identifier])<last_start for identifier in ('id-postgres','id-backend','id-keycloak'))
    assert len([c for c in calls if c[:2]==['docker','run']])==2
    assert not any(name(c).startswith('mentorflow-bug45-pool-probe-') for c in docker.containers.values())


def test_network45_partial_cutover_can_rollback_without_removing_any_application_container(case):
    migration,docker,overlay=case
    migration.check_compose(overlay);migration.quiesce();migration.detach()
    docker.fail_create=PUBLIC
    with pytest.raises(RuntimeError,match='injected'):migration.rebuild()
    migration.rollback()
    assert len(docker.containers)==10
    for n in docker.networks.values():assert not n['IPAM']['Config'][0].get('IPRange')
    assert docker.find(EDGE)['State']['Running']
    assert not endpoints(docker.find(OLD_EDGE))


def test_network45_declarative_compose_pool_and_gateway_agree_with_script(case):
    migration,docker,overlay=case;data=yaml.safe_load(overlay.read_text())
    for n,(subnet,pool,ip) in NETWORKS.items():
        key=n.rsplit('_',1)[1]
        assert data['networks'][key]['ipam']['config'][0]['subnet']==subnet
        assert data['networks'][key]['ipam']['config'][0]['ip_range']==pool
        assert data['services']['edge']['networks'][key]['ipv4_address']==ip
        assert ipaddress.ip_address(ip) not in ipaddress.ip_network(pool)
    assert data['services']['keycloak']['environment']['KC_PROXY_TRUSTED_ADDRESSES']=='172.18.0.5'
    command=create_command(migration.inventory()['networks'][0],fixed=True)
    assert '--internal' in command and 'com.docker.network.driver.mtu=1500' in command
    assert not any('config-hash' in x for x in command)
    assert any('config-hash' in x for x in create_command(migration.inventory()['networks'][0],fixed=False))


def test_network45_no_cutover_without_compose_validation_or_after_inventory_drift(case):
    migration,docker,overlay=case
    with pytest.raises(RuntimeError,match='check-compose'):migration.quiesce()
    assert not any(c[:2]==['docker','update'] for c in docker.commands)
    migration.check_compose(overlay)
    extra=deepcopy(docker.find('id-helper'));extra.update(Id='new-container',Name='/new')
    docker.containers[extra['Id']]=extra
    with pytest.raises(RuntimeError,match='Inventory changed'):migration.quiesce()
    assert docker.find(EDGE)['State']['Running']


@pytest.mark.parametrize('fault',['subnet','ipv6','wrong_edge','driver_opt','duplicate','trust','old_network_id'])
def test_network45_preflight_stops_on_unsupported_or_conflicting_live_state(case,fault):
    migration,docker,overlay=case
    if fault=='subnet':docker.networks[INTERNAL]['IPAM']['Config'][0]['Subnet']='10.0.0.0/24'
    elif fault=='ipv6':docker.networks[INTERNAL]['EnableIPv6']=True
    elif fault=='wrong_edge':docker.find(EDGE)['NetworkSettings']['Networks'][INTERNAL]['IPAddress']='172.18.0.7'
    elif fault=='driver_opt':docker.find('id-backend')['NetworkSettings']['Networks'][INTERNAL]['DriverOpts']={'x':'y'}
    elif fault=='duplicate':docker.find('id-backend')['NetworkSettings']['Networks'][INTERNAL]['IPAddress']='172.18.0.5'
    elif fault=='old_network_id':
        docker.networks[INTERNAL]['Id']='network-id-before-rebuild'
        docker.find('id-backend')['HostConfig']['NetworkMode']='network-id-before-rebuild'
    else:docker.find('id-keycloak')['Config']['Env']=[]
    with pytest.raises(RuntimeError):migration.preflight()
    assert not any(c[:2]==['docker','update'] for c in docker.commands)


def test_network45_http_failure_stops_instead_of_reporting_success(case):
    migration,docker,overlay=case;cutover(migration,overlay);docker.http='503'
    start=len(docker.commands)
    with pytest.raises(RuntimeError,match='HTTP check failed'):migration.verify()
    assert len([c for c in docker.commands[start:] if c[0]=='curl'])==1


def test_network45_probe_failure_cleans_only_its_own_containers(case,monkeypatch):
    migration,docker,overlay=case;cutover(migration,overlay)
    original=migration.command
    def fail(args):
        if args[:2]==['docker','run'] and args[args.index('--network')+1]==PUBLIC:
            raise RuntimeError('probe failure')
        return original(args)
    migration.command=fail
    with pytest.raises(RuntimeError,match='probe failure'):migration.rehearse()
    assert len(docker.containers)==10
    assert not docker.find(EDGE)['State']['Running']
    # Explicit attach/start recovers a rehearsal interrupted after disconnecting edge.
    migration.attach();migration.start();migration.verify()


def test_network45_frontend_health_dependency_and_worker_start_after_backend(case):
    migration, _, _ = case
    containers = migration.inventory()['containers']
    backend = next(c for c in containers if c['Id'] == 'id-backend')
    for service in ('frontend', 'worker'):
        extra = deepcopy(backend)
        extra.update(Id='id-'+service, Name='/mentorflow-e1-preview-'+service+'-1')
        extra['Config']['Labels']['com.docker.compose.service'] = service
        containers.append(extra)
    ids = [c['Id'] for c in ordered(containers)]
    assert ids.index('id-keycloak') < ids.index('id-backend') < ids.index('id-worker')
    assert ids.index('id-backend') < ids.index('id-frontend') < ids.index('id-edge')
