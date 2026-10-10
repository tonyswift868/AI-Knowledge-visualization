"""BUG-45 one-shot Docker IPAM migration. Run ONLY on the server after approval.

Uses Docker CLI, not a daemon or startup-order workaround. No containers, images,
volumes or application data are removed. Each phase is independently executable.
Only stdlib; deployment details are read from the live Docker inventory.
"""
import argparse
import ipaddress
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import time
import uuid

EDGE = 'mentorflow-e1-preview-edge-1'
OLD_EDGE = EDGE + '-r1-before-1db6c0ca'
NETWORKS = {'mentorflow-e1-preview_internal': ('172.18.0.0/16', '172.18.128.0/17', '172.18.0.5'),
            'mentorflow-e1-preview_edge': ('172.19.0.0/16', '172.19.128.0/17', '172.19.0.2')}
URLS = ('https://89.45.45.28/health/live',
        'https://89.45.45.28/mentorflow/updates/app/stable/stable.json',
        'https://auth.clannad.win', 'https://aiteacher.clannad.win')


def run(args):
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f'{shlex.join(args)} failed: {result.stderr.strip()}')
    return result.stdout


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def name(container):
    return container['Name'].lstrip('/')


def endpoints(container):
    if container['HostConfig'].get('NetworkMode', '').startswith('container:'):
        return {}  # shared-namespace containers have no independently attachable endpoints
    return container['NetworkSettings'].get('Networks', {})


def validate_inventory(networks, containers):
    by_name = {name(c): c for c in containers}
    network_ids = {n['Id'] for n in networks if n.get('Id')}
    if any(c['HostConfig'].get('NetworkMode') in network_ids for c in containers):
        raise RuntimeError('NetworkMode stores an old network ID; container recreation would be required. Stop before cutover.')
    if EDGE not in by_name or not by_name[EDGE]['State']['Running']:
        raise RuntimeError('Capture a working gateway, not the already broken outage state.')
    addresses = {}
    for network in networks:
        n = network['Name']; subnet, pool, edge_ip = NETWORKS[n]
        configs = network['IPAM']['Config']
        if (network['Driver'] != 'bridge' or network.get('EnableIPv6') or len(configs) != 1
                or configs[0]['Subnet'] != subnet or configs[0].get('Gateway') != str(ipaddress.ip_network(subnet)[1])
                or network['IPAM'].get('Driver', 'default') != 'default' or network.get('Ingress') or network.get('ConfigOnly')):
            raise RuntimeError(f'Unexpected network configuration: {n}')
        if network.get('Internal', False) != n.endswith('_internal'):
            raise RuntimeError(f'Unexpected internal isolation: {n}')
        if configs[0].get('IPRange', '') not in ('', pool):
            raise RuntimeError(f'Unexpected existing dynamic pool: {n}')
        edge = endpoints(by_name[EDGE]).get(n, {})
        if edge.get('IPAddress') != edge_ip:
            raise RuntimeError(f'Unexpected gateway address: {n}')
        addresses[n] = set()
        for c in containers:
            if name(c) == OLD_EDGE: continue
            endpoint = endpoints(c).get(n)
            if not endpoint: continue
            if endpoint.get('Links') or endpoint.get('GlobalIPv6Address') or endpoint.get('DriverOpts') or endpoint.get('GwPriority', 0):
                raise RuntimeError('Endpoint requires extra Docker options; do not silently drop them.')
            config = endpoint.get('IPAMConfig') or {}
            if config.get('IPv6Address') or config.get('LinkLocalIPs'):
                raise RuntimeError('Unexpected IPv6/link-local endpoint.')
            ip = endpoint.get('IPAddress') or config.get('IPv4Address')
            if ip:
                if ip in addresses[n]: raise RuntimeError(f'Duplicate address in backup: {n} {ip}')
                addresses[n].add(ip)
                if ipaddress.ip_address(ip) not in ipaddress.ip_network(subnet):
                    raise RuntimeError('Address outside preserved subnet.')
    return by_name


def affected(containers):
    selected = {c['Id'] for c in containers if set(endpoints(c)) & NETWORKS.keys()}
    changed = True
    while changed:
        changed = False
        identifiers = selected | {name(c) for c in containers if c['Id'] in selected}
        for c in containers:
            mode = c['HostConfig'].get('NetworkMode', '')
            if mode.startswith('container:') and mode[10:] in identifiers and c['Id'] not in selected:
                selected.add(c['Id']); changed = True
    return [c for c in containers if c['Id'] in selected or name(c) == OLD_EDGE]


def ordered(containers):
    ranks = {'postgres': 0, 'keycloak': 2, 'backend': 3, 'worker': 4,
             'oidc-loopback-proxy': 4, 'frontend': 5, 'edge': 9}
    result = sorted(containers, key=lambda c: (ranks.get((c['Config'].get('Labels') or {}).get('com.docker.compose.service'), 1), name(c)))
    # Shared network namespaces must start after their owner and stop before it.
    output = []
    while result:
        identifiers = {c['Id'] for c in output} | {name(c) for c in output}
        ready = next((c for c in result if not c['HostConfig'].get('NetworkMode', '').startswith('container:')
                      or c['HostConfig']['NetworkMode'][10:] in identifiers), None)
        if ready is None: raise RuntimeError('Unresolved shared network namespace owner.')
        output.append(ready); result.remove(ready)
    return output


def create_command(network, *, fixed):
    args = ['docker', 'network', 'create', '--driver', 'bridge', '--subnet', network['IPAM']['Config'][0]['Subnet'],
            '--gateway', network['IPAM']['Config'][0]['Gateway']]
    config = network['IPAM']['Config'][0]
    pool = NETWORKS[network['Name']][1] if fixed else config.get('IPRange')
    if pool: args += ['--ip-range', pool]
    if network.get('Internal'): args += ['--internal']
    if network.get('Attachable'): args += ['--attachable']
    for flag, values in (('--opt', network.get('Options')), ('--ipam-opt', network['IPAM'].get('Options')),
                         ('--aux-address', config.get('AuxiliaryAddresses')), ('--label', network.get('Labels'))):
        for key, value in (values or {}).items():
            # A stale Compose config hash would ask Compose to rebuild a correct network.
            if fixed and key == 'com.docker.compose.config-hash': continue
            args += [flag, f'{key}={value}']
    return args + [network['Name']]


def connect_command(container, network):
    endpoint = endpoints(container)[network]
    args = ['docker', 'network', 'connect']
    # Preserve ALL current addresses to avoid breaking any unrecorded literal-IP
    # consumer. Formerly dynamic low addresses become explicit reservations.
    ip = endpoint.get('IPAddress') or (endpoint.get('IPAMConfig') or {}).get('IPv4Address')
    if ip: args += ['--ip', ip]
    for alias in endpoint.get('Aliases') or []: args += ['--alias', alias]
    return args + [network, container['Id']]


class Migration:
    def __init__(self, state, command=run):
        self.state, self.command = Path(state), command

    def docker(self, *args):
        return self.command(['docker', *args])

    def inspect(self, identifier):
        return json.loads(self.docker('inspect', identifier))[0]

    def backup(self, configs=(), env_files=()):
        self.state.mkdir(parents=True, exist_ok=False)
        networks = json.loads(self.docker('network', 'inspect', *NETWORKS))
        ids = self.docker('ps', '-aq').split()
        containers = json.loads(self.docker('inspect', *ids))
        validate_inventory(networks, containers)
        selected = affected(containers); ordered(selected)
        save(self.state/'inventory.json', dict(networks=networks, containers=selected))
        sources = set(configs) | set(env_files)
        compose = []
        missing = []  # Compose provenance whose release directory was removed later; recorded, not fatal.
        for c in selected:
            labels = c['Config'].get('Labels') or {}
            files = labels.get('com.docker.compose.project.config_files', '').split(',')
            wd = labels.get('com.docker.compose.project.working_dir', '')
            if files != ['']:
                paths = [str((Path(wd)/f).resolve()) for f in files]
                gone = [f for f in paths if not Path(f).is_file()]
                if gone:
                    missing.append(dict(container=c['Name'], files=gone))
                    continue
                sources.update(paths)
                envs = [f for f in labels.get('com.docker.compose.project.environment_file', '').split(',') if f]
                envs = list(dict.fromkeys([*envs, *env_files])); sources.update(envs)
                args = ['docker', 'compose', '--project-directory', wd, '-p', labels['com.docker.compose.project']]
                for e in envs: args += ['--env-file', e]
                for f in paths: args += ['-f', f]
                if args not in compose: compose.append(args)
            for mount in c.get('Mounts', []):
                if mount.get('Type') == 'bind' and mount.get('Destination') == '/etc/nginx/nginx.conf':
                    sources.add(mount['Source'])
        copies = []
        for i, source in enumerate(sorted(sources)):
            path = Path(source)
            if not path.is_file() or path.suffix.lower() in ('.key', '.pem', '.p12', '.pfx'):
                raise RuntimeError(f'Not a configuration text file: {source}')
            target = self.state/f'config-{i:03d}-{path.name}'
            shutil.copy2(path, target); copies.append(dict(source=source, backup=target.name))
        save(self.state/'config-files.json', copies)
        save(self.state/'compose.json', compose)
        save(self.state/'missing-compose-sources.json', missing)
        self.preflight()

    def check_compose(self, overlay):
        target = self.state/'network45.override.yml'
        shutil.copy2(overlay, target)
        compose = json.loads((self.state/'compose.json').read_text(encoding='utf-8'))
        if not compose: raise RuntimeError('No Compose provenance; review the actual launch configuration first.')
        commands = ['#!/usr/bin/env bash', 'set -euo pipefail', '# Read-only config validation. Append this override to ALL future current Compose invocations.']
        future=[]
        for i, args in enumerate(compose):
            command = args+['-f', str(target)]
            config = json.loads(self.command(command+['config','--format','json']))
            if 'edge' not in config.get('services', {}):
                # e.g. the worker-only Compose set: nothing here owns the fixed addresses.
                save(self.state/f'compose-effective-{i}.json', config)
                commands.append(shlex.join(command+['config','--quiet'])); future.append(command)
                continue
            for n,(_,pool,ip) in NETWORKS.items():
                logical = n.rsplit('_',1)[1]
                if (config['services']['edge']['networks'][logical]['ipv4_address'] != ip
                        or config['networks'][logical]['ipam']['config'][0]['ip_range'] != pool):
                    raise RuntimeError('Compose override did not win; do not cut over.')
            if 'keycloak' in config['services'] and config['services']['keycloak']['environment'].get('KC_PROXY_TRUSTED_ADDRESSES') != '172.18.0.5':
                raise RuntimeError('Compose proxy trust changed; do not cut over.')
            save(self.state/f'compose-effective-{i}.json', config)
            commands.append(shlex.join(command+['config','--quiet']))
            future.append(command)
        (self.state/'compose-check.sh').write_text('\n'.join(commands)+'\n',encoding='utf-8')
        save(self.state/'future-compose.json',future)
        (self.state/'compose-checked').write_text('ok\n',encoding='utf-8')

    def inventory(self):
        return json.loads((self.state/'inventory.json').read_text(encoding='utf-8'))

    def preflight(self):
        old = self.inventory()
        live = [self.inspect(c['Id']) for c in old['containers']]
        networks = json.loads(self.docker('network', 'inspect', *NETWORKS))
        validate_inventory(networks, live)
        # New endpoints between backup and cutover must not be disconnected blindly.
        current = json.loads(self.docker('inspect', *self.docker('ps', '-aq').split()))
        if {c['Id'] for c in affected(current)} != {c['Id'] for c in old['containers']}:
            raise RuntimeError('Inventory changed; take a new backup before cutover.')
        policies = [c for c in live if (c['Config'].get('Labels') or {}).get('com.docker.compose.service') == 'keycloak']
        if not policies or not any('KC_PROXY_TRUSTED_ADDRESSES=172.18.0.5' in c['Config'].get('Env', []) for c in policies):
            raise RuntimeError('Live Keycloak trust differs from the dispatch; investigate before cutover.')
        save(self.state/'preflight.json', dict(endpoints={name(c): endpoints(c) for c in live},
                                               preserved_addresses=True, dynamic_pools={n: v[1] for n,v in NETWORKS.items()}))
        print('Preflight OK: actual endpoint/IPAMConfig saved; fixed-address provenance must be reviewed in config copies.')

    def quiesce(self):
        if not (self.state/'compose-checked').is_file(): raise RuntimeError('Run check-compose first.')
        self.preflight()
        for c in self.inventory()['containers']: self.docker('update', '--restart=no', c['Id'])
        for c in reversed(ordered(self.inventory()['containers'])):
            if self.inspect(c['Id'])['State']['Running']: self.docker('stop', '--time', '30', c['Id'])

    def detach(self):
        for c in self.inventory()['containers']:
            if self.inspect(c['Id'])['State']['Running']: raise RuntimeError('Run quiesce first.')
            for n in NETWORKS:
                if n in endpoints(self.inspect(c['Id'])): self.docker('network', 'disconnect', '--force', n, c['Id'])

    def rebuild(self, *, fixed=True):
        for n in self.inventory()['networks']:
            live = self.docker('network', 'ls', '--format', '{{.Name}}').split()
            if n['Name'] in live: self.docker('network', 'rm', n['Name'])
            self.command(create_command(n, fixed=fixed))

    def attach(self):
        for c in self.inventory()['containers']:
            if name(c) == OLD_EDGE: continue  # retained, disconnected, restart=no
            for n in NETWORKS:
                if n in endpoints(c) and n not in endpoints(self.inspect(c['Id'])):
                    self.command(connect_command(c, n))

    def wait(self, identifier, timeout=180):
        until = time.monotonic()+timeout
        while time.monotonic() < until:
            state = self.inspect(identifier)['State']
            if state.get('Running') and state.get('Health', {}).get('Status', 'healthy') == 'healthy': return
            if state.get('Status') in ('exited', 'dead'): raise RuntimeError(f'Container exited: {identifier}')
            time.sleep(2)
        raise RuntimeError(f'Container not healthy after {timeout}s: {identifier}')

    def start(self):
        for c in ordered(self.inventory()['containers']):
            if name(c) == OLD_EDGE: continue
            if c['State']['Running']:
                self.docker('start', c['Id']); self.wait(c['Id'])
            policy = c['HostConfig']['RestartPolicy']
            value = policy['Name'] or 'no'
            if value == 'on-failure' and policy.get('MaximumRetryCount'): value += ':'+str(policy['MaximumRetryCount'])
            self.docker('update', '--restart='+value, c['Id'])
        # Even on rollback leave the retired duplicate gateway quarantined. Restoring
        # its conflicting addresses/restart policy would recreate the same outage.

    def verify(self, *, ca=None, fixed=True):
        networks = json.loads(self.docker('network', 'inspect', *NETWORKS))
        live = [self.inspect(c['Id']) for c in self.inventory()['containers']]
        validate_inventory(networks, live)
        for n in networks:
            expected = NETWORKS[n['Name']][1] if fixed else next(old for old in self.inventory()['networks'] if old['Name']==n['Name'])['IPAM']['Config'][0].get('IPRange','')
            if n['IPAM']['Config'][0].get('IPRange','') != expected:
                raise RuntimeError('Dynamic pool not installed.')
        for original,c in zip(self.inventory()['containers'],live):
            if name(c) == OLD_EDGE:
                if c['State']['Running'] or set(endpoints(c)) & NETWORKS.keys(): raise RuntimeError('Retired gateway not quarantined.')
                continue
            if original['State']['Running']: self.wait(c['Id'])
            for n in NETWORKS:
                if n in endpoints(original):
                    expected = endpoints(original)[n].get('IPAddress') or (endpoints(original)[n].get('IPAMConfig') or {}).get('IPv4Address')
                    current = endpoints(c).get(n,{})
                    actual = current.get('IPAddress') or (current.get('IPAMConfig') or {}).get('IPv4Address')
                    if expected and actual != expected: raise RuntimeError('Existing container address changed.')
                    if not set(endpoints(original)[n].get('Aliases') or []) <= set(current.get('Aliases') or []):
                        raise RuntimeError('DNS alias was lost.')
        checks = []
        for url in URLS:
            args = ['curl','--silent','--show-error','--location','--connect-timeout','15','--max-time','60','--output','/dev/null','--write-out','%{http_code}']
            if ca and url.startswith('https://89.45.45.28/'): args += ['--cacert', str(ca)]
            status = self.command(args+[url]).strip()
            checks.append(dict(url=url, status=status))
            if status != '200': raise RuntimeError(f'HTTP check failed: {url} -> {status}')
        save(self.state/'http-checks.json', checks)
        print('Four HTTP endpoints: 200. Client login must now be checked by the architect/user.')

    def rehearse(self):
        old = self.inventory(); edge = next(c for c in old['containers'] if name(c) == EDGE)
        # Run verify first, including HTTP, before taking the gateway down again.
        self.docker('stop','--time','30',edge['Id'])
        for n in NETWORKS: self.docker('network','disconnect','--force',n,edge['Id'])
        probes = []
        try:
            for c in reversed(ordered(old['containers'])):
                if name(c) not in (EDGE,OLD_EDGE) and c['State']['Running']:
                    self.docker('stop','--time','30',c['Id'])
            for c in ordered(old['containers']):
                if name(c) not in (EDGE,OLD_EDGE) and c['State']['Running']:
                    self.docker('start',c['Id']); self.wait(c['Id'])
            results = []
            for i,(n,(_,pool,_)) in enumerate(NETWORKS.items()):
                probe = 'mentorflow-bug45-pool-probe-'+uuid.uuid4().hex[:12]
                self.docker('run','-d','--name',probe,'--restart=no','--network',n,
                            '--entrypoint','/bin/sh',edge['Image'],'-c','sleep 600')
                probes.append(probe)
                ip = endpoints(self.inspect(probe))[n]['IPAddress']
                if ipaddress.ip_address(ip) not in ipaddress.ip_network(pool): raise RuntimeError('Dynamic allocation escaped pool.')
                results.append(dict(network=n,probe_ip=ip))
            for n in NETWORKS: self.command(connect_command(edge,n))
            self.docker('start',edge['Id']); self.wait(edge['Id'])
            save(self.state/'rehearsal.json',results)
        finally:
            for probe in probes: self.docker('rm','-f',probe)  # only disposable probe containers; never volumes

    def rollback(self, *, ca=None):
        # Valid even after a partially completed cutover: operate on backed-up IDs.
        for c in self.inventory()['containers']: self.docker('update','--restart=no',c['Id'])
        for c in reversed(ordered(self.inventory()['containers'])):
            if self.inspect(c['Id'])['State']['Running']: self.docker('stop','--time','30',c['Id'])
        self.detach(); self.rebuild(fixed=False); self.attach(); self.start()
        self.verify(ca=ca,fixed=False)
        print('Original network settings and container IPs restored; retired duplicate gateway remains stopped/disconnected.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('step', choices=('backup','preflight','check-compose','quiesce','detach','rebuild','attach','start','verify','rehearse','rollback'))
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--config',action='append',default=[])
    parser.add_argument('--env-file',action='append',default=[])
    parser.add_argument('--ca',type=Path)
    parser.add_argument('--overlay',type=Path,default=Path(__file__).resolve().parents[1]/'compose.network45.yml')
    args=parser.parse_args(); migration=Migration(args.state)
    if args.step=='backup': migration.backup(args.config,args.env_file)
    elif args.step=='check-compose': migration.check_compose(args.overlay)
    elif args.step=='verify': migration.verify(ca=args.ca)
    elif args.step=='rehearse': migration.verify(ca=args.ca); migration.rehearse(); migration.verify(ca=args.ca)
    elif args.step=='rollback': migration.rollback(ca=args.ca)
    else: getattr(migration,args.step)()


if __name__=='__main__':
    try: main()
    except (RuntimeError, OSError, KeyError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
