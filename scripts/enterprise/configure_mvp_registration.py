"""Explicit MVP-only existing-realm merge through an already authenticated kcadm.

No passwords, tokens, user creation, server discovery, login or realm import.
User Profile PUT requires a full GET/merge/PUT; concurrent admin edits must stop.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / 'deploy/enterprise/staging/rp05/account-core-registration-policy.json'
REALM_FIELDS = ('realm', 'registrationAllowed', 'registrationEmailAsUsername', 'verifyEmail',
                'resetPasswordAllowed', 'passwordPolicy', 'bruteForceProtected', 'sslRequired',
                'loginWithEmailAllowed', 'duplicateEmailsAllowed', 'defaultRole', 'defaultGroups',
                'accessTokenLifespan', 'registrationFlow', 'browserFlow')


def canonical(value):
    # Keycloak may omit nullable representation fields on readback.
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [canonical(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_new(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def merge_policy(before, policy):
    if policy != read_json(POLICY):
        raise RuntimeError('Policy does not match this candidate')
    if before['realm'].get('realm') != policy['realm']:
        raise RuntimeError('Realm mismatch')
    after = copy.deepcopy(before)
    after['realm'].update(policy['realm_patch'])
    attrs = after['profile'].get('attributes')
    if not isinstance(attrs, list) or any(not isinstance(a, dict) or not isinstance(a.get('name'), str) for a in attrs):
        raise RuntimeError('Unexpected User Profile configuration')
    names = [a['name'] for a in attrs]
    if len(set(names)) != len(names) or not set(['username', *policy['optional_profile_attributes']]) <= set(names):
        raise RuntimeError('Missing or duplicate standard profile fields; owner review required')
    for attribute in attrs:
        if attribute['name'] in policy['optional_profile_attributes']:
            attribute.pop('required', None)
        elif attribute['name'] != 'username':
            required = attribute.get('required') or {}
            if 'user' in required.get('roles', []) or required.get('scopes'):
                raise RuntimeError('Additional required registration field; do not silently remove it')
    # Preserve enabled, priority, providerId, name, config and VERIFY_PROFILE.
    if after['verify_email'].get('alias') != 'VERIFY_EMAIL':
        raise RuntimeError('VERIFY_EMAIL provider is missing')
    after['verify_email']['defaultAction'] = False
    return canonical(after)


class Kcadm:
    def __init__(self, prefix, config):
        if not isinstance(prefix, list) or not prefix or not all(isinstance(x, str) and x for x in prefix):
            raise RuntimeError('Expected a nonsecret kcadm argv prefix list')
        if any(x.split('=', 1)[0] in {'--password', '--token', '--secret'} for x in prefix):
            raise RuntimeError('Do not put credentials into the command prefix')
        self.prefix, self.config = prefix, config

    def call(self, method, endpoint, payload=None, fields=None):
        # Read/update only explicit realm resources, never create users.
        command = [*self.prefix, method, endpoint, '--config', self.config]
        if not endpoint.startswith('realms/'):
            command += ['-r', 'mentorflow']
        if fields:
            command += ['--fields', ','.join(fields)]
        if payload is not None:
            command += ['-f', '-', '-n']  # send explicit representation, no CLI merge GET
        result = subprocess.run(command, input=None if payload is None else json.dumps(payload),
                                text=True, capture_output=True, timeout=45)
        if result.returncode:
            # Do not echo CLI responses/config, which could contain credentials.
            raise RuntimeError('Keycloak CLI operation failed: ' + method + ' ' + endpoint)
        return json.loads(result.stdout) if method == 'get' else None

    def snapshot(self):
        realm = self.call('get', 'realms/mentorflow', fields=REALM_FIELDS)
        profile = self.call('get', 'users/profile')
        email = self.call('get', 'authentication/required-actions/VERIFY_EMAIL')
        actions = self.call('get', 'authentication/required-actions')
        others = sorted([a for a in actions if a.get('alias') != 'VERIFY_EMAIL'], key=lambda a: a['alias'])
        if not any(a.get('alias') == 'VERIFY_PROFILE' for a in others):
            raise RuntimeError('VERIFY_PROFILE provider is missing; owner review required')
        # Never persist custom required-action config; only fingerprint unchanged actions.
        return canonical({'realm': realm, 'profile': profile, 'verify_email': email,
                          'other_actions_sha256': digest(others)})

    def put(self, resource, value, policy):
        if resource == 'realm':
            self.call('update', 'realms/mentorflow', {k: value[k] for k in policy['realm_patch']})
        elif resource == 'profile':
            self.call('update', 'users/profile', value)
        elif resource == 'verify_email':
            self.call('update', 'authentication/required-actions/VERIFY_EMAIL', value)
        else:
            raise RuntimeError('Forbidden resource')


def execute(client, before, after, policy, *, rollback=False):
    source, target = (after, before) if rollback else (before, after)
    current = client.snapshot()
    if current == target:
        return 'ALREADY_MATCHED'
    # Permit interrupted multi-resource apply/rollback, but reject any drift.
    for key in source:
        if current[key] not in (source[key], target[key]):
            raise RuntimeError('Configuration drift: ' + key + '; do not overwrite admin changes')
    order = ['realm', 'verify_email', 'profile'] if rollback else ['profile', 'verify_email', 'realm']
    for key in order:
        fresh = client.snapshot()
        if fresh != current:
            raise RuntimeError('Concurrent admin change detected; stop and inspect')
        if fresh[key] != target[key]:
            client.put(key, target[key], policy)
            current[key] = copy.deepcopy(target[key])
            if client.snapshot() != current:
                raise RuntimeError('Readback mismatch; preserve plan and use guarded rollback')
    if client.snapshot() != target:
        raise RuntimeError('Final readback failed')
    if not rollback and merge_policy(target, policy) != target:
        raise RuntimeError('Idempotence check failed')
    return 'ROLLED_BACK_VERIFIED' if rollback else 'APPLIED_VERIFIED'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['plan', 'apply', 'verify', 'rollback'])
    parser.add_argument('--profile', required=True, choices=['account-core-mvp'])
    parser.add_argument('--confirm-exclusive-mvp-realm', action='store_true', required=True)
    parser.add_argument('--prefix-file', type=Path, required=True, help='Nonsecret argv array ending with existing kcadm.sh')
    parser.add_argument('--session-config', required=True, help='Existing private kcadm session path, never its contents')
    parser.add_argument('--plan-dir', type=Path, required=True)
    args = parser.parse_args()
    policy = read_json(POLICY)
    prefix = read_json(args.prefix_file)
    client = Kcadm(prefix, args.session_config)
    target_hash = digest([prefix, args.session_config, policy['realm'], args.profile])
    if args.mode == 'plan':
        before = client.snapshot()
        # All four realm booleans must be explicit for unambiguous rollback.
        if any(type(before['realm'].get(k)) is not bool for k in policy['realm_patch']):
            raise RuntimeError('Missing realm booleans; cannot safely freeze rollback')
        after = merge_policy(before, policy)
        args.plan_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        write_new(args.plan_dir / 'before.json', before)
        write_new(args.plan_dir / 'after.json', after)
        write_new(args.plan_dir / 'plan.json', {'schema': policy['schema'], 'policy_hash': digest(policy),
                  'target_hash': target_hash, 'before_hash': digest(before), 'after_hash': digest(after)})
        status = 'PLAN_ONLY_NOT_APPLIED'
    else:
        plan = read_json(args.plan_dir / 'plan.json')
        before, after = read_json(args.plan_dir / 'before.json'), read_json(args.plan_dir / 'after.json')
        if (plan['policy_hash'] != digest(policy) or plan['target_hash'] != target_hash or
            plan['before_hash'] != digest(before) or plan['after_hash'] != digest(after) or
            merge_policy(before, policy) != after):
            raise RuntimeError('Plan binding/allowed delta mismatch')
        if args.mode == 'verify':
            if client.snapshot() != after:
                raise RuntimeError('MVP registration configuration does not match approved plan')
            status = 'CONFIG_READBACK_VERIFIED_NOT_REGISTRATION_TEST'
        else:
            status = execute(client, before, after, policy, rollback=args.mode == 'rollback')
    print(json.dumps({'status': status, 'before_hash': digest(before), 'after_hash': digest(after),
                      'real_registration_test': 'NOT_RUN', 'user_mutations': 0}))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError, KeyError) as error:
        # Avoid emitting a CLI/token response or full config traceback.
        print(json.dumps({'status': 'FAILED_CLOSED', 'error_type': type(error).__name__,
                          'reason': str(error) if isinstance(error, RuntimeError) else 'Input/CLI failure; inspect privately'}))
        raise SystemExit(1)
