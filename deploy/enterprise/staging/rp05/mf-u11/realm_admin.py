"""Scoped MF-U11 policy change. Default: print only. No embedded credentials."""
import argparse
import json
import os
from pathlib import Path
import ssl
import urllib.parse
import urllib.request

ACTION = 'realms/mentorflow/authentication/required-actions/UPDATE_PASSWORD'


class Client:
    def __init__(self, origin, ca, token_file):
        parsed = urllib.parse.urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('verified HTTPS identity origin required')
        self.base = origin.rstrip('/') + '/admin/'
        self.tls = ssl.create_default_context(cafile=str(ca))
        self.token = token_file.read_text().strip()
        if not self.token or len(self.token)>16384: raise ValueError('invalid admin token file')

    def call(self, path, method='GET', value=None):
        data = None if value is None else json.dumps(value).encode()
        request = urllib.request.Request(self.base+path, data=data, method=method,
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        # No redirect can forward the administrator credential to another origin.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args): return None
        opener=urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=self.tls))
        with opener.open(request, timeout=15) as response:
            raw=response.read(1024*1024+1)
            if len(raw)>1024*1024: raise ValueError('oversized identity response')
            return json.loads(raw) if raw else None


def changed(action):
    if action.get('alias')!='UPDATE_PASSWORD' or action.get('enabled') is not True:
        raise ValueError('enabled UPDATE_PASSWORD provider required; stop and review')
    return {**action, 'defaultAction': False}


def policy(client, action, backup):
    current=client.call(ACTION)
    if action=='capture':
        changed(current)
        with backup.open('x',encoding='utf-8') as stream:
            os.chmod(backup,0o600); json.dump(current,stream)
        return
    original=json.loads(backup.read_text(encoding='utf-8')); candidate=changed(original)
    if current not in (original,candidate): raise ValueError('required action changed since capture')
    if action=='check':return
    target=candidate if action=='apply' else original
    if current!=target: client.call(ACTION,'PUT',target)
    if client.call(ACTION)!=target: raise ValueError('realm readback mismatch')


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--origin',required=True);p.add_argument('--ca-file',required=True,type=Path)
    p.add_argument('--token-file',required=True,type=Path);p.add_argument('--execute',action='store_true')
    return p


def main():
    p=parser();p.add_argument('action',choices=['capture','check','apply','rollback'])
    p.add_argument('--backup',required=True,type=Path);args=p.parse_args()
    if not args.execute:
        print('DRY RUN: '+args.action+' UPDATE_PASSWORD defaultAction only; user actions untouched');return
    policy(Client(args.origin,args.ca_file,args.token_file),args.action,args.backup)
    print('required-action policy: PASS')


if __name__=='__main__':main()
