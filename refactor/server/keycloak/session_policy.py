"""Scoped login lifetime change via Keycloak Admin REST; read-only by default.

26.7.3 has no unlimited regular SSO lifetime: zero restores its defaults.
Use its largest signed-int timeout, about 68 years, and disable refresh reuse
revocation. This does NOT fix the separate native verifier/store lifetime.
"""
import argparse
import json
import os
import subprocess
from pathlib import Path

from .realm_admin import Client

MAX_TIMEOUT = 2_147_483_647
REALM = 'realms/mentorflow'
VALUES = {
    'ssoSessionIdleTimeout': MAX_TIMEOUT,
    'ssoSessionMaxLifespan': MAX_TIMEOUT,
    'ssoSessionIdleTimeoutRememberMe': MAX_TIMEOUT,
    'ssoSessionMaxLifespanRememberMe': MAX_TIMEOUT,
    'offlineSessionIdleTimeout': MAX_TIMEOUT,
    'offlineSessionMaxLifespanEnabled': False,
    'offlineSessionMaxLifespan': MAX_TIMEOUT,
    'clientSessionIdleTimeout': 0,
    'clientSessionMaxLifespan': 0,
    'clientOfflineSessionIdleTimeout': 0,
    'clientOfflineSessionMaxLifespan': 0,
    'revokeRefreshToken': False,
    'refreshTokenMaxReuse': 0,
}
ATTRIBUTES = {
    'client.session.idle.timeout': '0',
    'client.session.max.lifespan': '0',
    'client.offline.session.idle.timeout': '0',
    'client.offline.session.max.lifespan': '0',
}


class KcadmClient:
    """Use Keycloak's authenticated Admin REST CLI inside its existing container."""

    def __init__(self, container):
        self.container = container

    def call(self, path, method='GET', value=None):
        command = ['docker', 'exec', '-i', self.container, '/opt/keycloak/bin/kcadm.sh',
                   'get' if method == 'GET' else 'update']
        if path == REALM:
            command.append(REALM)
        else:
            command.extend([path.removeprefix(REALM + '/'), '-r', 'mentorflow'])
        if method != 'GET':
            command.extend(['-n', '-f', '-'])
        result = subprocess.run(command, input=None if value is None else json.dumps(value),
                                text=True, capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError('Keycloak Admin CLI failed; check container and administrator login')
        return json.loads(result.stdout) if result.stdout.strip() else None


def snapshot(client):
    realm = client.call(REALM)
    rows = client.call(REALM + '/clients')
    clients = []
    for row in rows:
        if (row.get('clientId', '').startswith('mentorflow-')
                and row.get('protocol') == 'openid-connect' and not row.get('bearerOnly')):
            attributes = row.get('attributes') or {}
            clients.append({'id': row['id'], 'clientId': row['clientId'],
                'attributes': {k: attributes[k] for k in ATTRIBUTES if k in attributes}})
    return {'realm': {k: realm.get(k) for k in VALUES}, 'clients': clients}


def target(original):
    return {'realm': dict(VALUES), 'clients': [dict(row, attributes=dict(ATTRIBUTES))
                                               for row in original['clients']]}


def write_settings(client, settings):
    # Realm update is partial; clients merge attributes so other policy survives.
    client.call(REALM, 'PUT', settings['realm'])
    for row in settings['clients']:
        path = REALM + '/clients/' + row['id']
        current = client.call(path)
        attributes = dict(current.get('attributes') or {})
        for key in ATTRIBUTES:
            attributes.pop(key, None)
        attributes.update(row['attributes'])
        client.call(path, 'PUT', dict(current, attributes=attributes))


def policy(client, backup, *, execute=False, rollback=False):
    current = snapshot(client)
    if not execute:
        return {'mode': 'dry-run', 'current': current, 'proposed': target(current),
            'limit': 'Regular SSO uses the 68-year maximum, not mathematical infinity; native login needs a separate fix.'}
    if rollback and not backup.exists():
        raise ValueError('rollback requires the original backup')
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open('x', encoding='utf-8') as stream:
            os.chmod(backup, 0o600)
            json.dump(current, stream, ensure_ascii=False, indent=2)
    original = json.loads(backup.read_text(encoding='utf-8'))
    expected = original if rollback else target(original)
    if {(r['id'], r['clientId']) for r in original['clients']} != {
            (r['id'], r['clientId']) for r in current['clients']}:
        raise ValueError('application clients changed since backup; review before applying')
    # Interrupted apply can have mixed old/new values. Do not overwrite unrelated
    # operator edits; allow either value for each setting for safe repeat/rollback.
    proposed = target(original)
    for key, value in current['realm'].items():
        if value not in (original['realm'][key], proposed['realm'][key]):
            raise ValueError('session setting changed since backup: ' + key)
    originals = {r['id']: r for r in original['clients']}
    for row in current['clients']:
        for key in ATTRIBUTES:
            value = row['attributes'].get(key)
            if value not in (originals[row['id']]['attributes'].get(key), ATTRIBUTES[key]):
                raise ValueError('client session setting changed since backup: ' + row['clientId'])
    if current != expected:
        write_settings(client, expected)
    result = snapshot(client)
    if result != expected:
        raise ValueError('session settings readback mismatch; use --rollback with the saved backup')
    return {'mode': 'rollback' if rollback else 'applied', 'verified': result, 'backup': str(backup)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    transport = p.add_mutually_exclusive_group(required=True)
    transport.add_argument('--origin')
    transport.add_argument('--container', help='existing Keycloak container with authenticated kcadm config')
    p.add_argument('--ca-file', type=Path)
    p.add_argument('--token-file', type=Path)
    p.add_argument('--backup', required=True, type=Path)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--execute', action='store_true')
    p.add_argument('--rollback', action='store_true')
    args = p.parse_args(argv)
    if args.rollback and not args.execute:
        p.error('--rollback requires --execute')
    if args.origin and (args.ca_file is None or args.token_file is None):
        p.error('--origin requires --ca-file and --token-file')
    client = KcadmClient(args.container) if args.container else Client(args.origin, args.ca_file, args.token_file)
    result = policy(client, args.backup,
                    execute=args.execute, rollback=args.rollback)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
