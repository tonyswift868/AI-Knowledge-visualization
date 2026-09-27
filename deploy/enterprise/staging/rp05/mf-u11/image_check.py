"""MF-U11 disconnected image source + import check; no live DB/identity access."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys


def check(mode, root, package):
    manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
    expected=dict(manifest['base_runtime_sha256'])
    if mode=='candidate':expected.update(manifest['sha256'])
    for name,digest in expected.items():
        if digest is None:
            assert not (root/name).exists(), 'unexpected base module: '+name
        else:
            assert hashlib.sha256((root/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest, 'source differs from audit: '+name
    os.environ.update(MENTORFLOW_DEPLOYMENT_MODE='PERSONAL_LOCAL',MENTORFLOW_DB='/tmp/mfu11-app.db',MENTORFLOW_ORM_DB='/tmp/mfu11-orm.db')
    sys.path[:0]=[str(root/'backend'),str(root)]
    for name in ('app.account_core_mvp','app.enterprise.identity.lifecycle_api','app.enterprise.identity.native',
                 'app.enterprise.catalog_api','app.enterprise.structured_sync_api'):
        importlib.import_module(name)
    from app.account_core_mvp import app
    routes=app.openapi()['paths']
    if mode=='candidate':
        for path in ('register','register/verify','register/resend','reset-code/request','reset-code/verify','reset-code/complete'):
            assert '/api/enterprise/auth/lifecycle/'+path in routes
    for module in tuple(sys.modules.values()):
        name=getattr(module,'__name__','')
        if name=='app' or name.startswith(('app.','refactor.server')):
            file=getattr(module,'__file__',None)
            if file:assert Path(file).resolve().relative_to(root.resolve()).as_posix() in expected, 'unaudited runtime module: '+name
    print(mode+': source audit and application imports PASS')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['base','candidate']);p.add_argument('--execute',action='store_true')
    args=p.parse_args()
    if args.execute:check(args.mode,Path('/app'),Path(__file__).resolve().parent)
    else:print('DRY RUN: disconnected image audit and application imports')
