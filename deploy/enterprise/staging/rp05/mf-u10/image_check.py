"""Executed inside a disconnected image; never opens database or network."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

MODULES = (
    'app.account_core_mvp', 'app.enterprise.structured_sync_api',
    'app.enterprise.catalog_api', 'app.enterprise.identity.native',
    'app.enterprise_tenancy.structured_sync', 'app.enterprise_tenancy.sync_schema',
    'refactor.server.closure_api',
)


def check(mode, root, package):
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    audit = json.loads((package / 'runtime-audit.json').read_text(encoding='utf-8'))
    expected = {entry['file']: entry['online_sha256'] for entry in audit}
    expected.update({p: entry['sha256'] for p, entry in manifest['originals'].items()})
    if mode == 'candidate':
        expected.update(manifest['sha256'])
    for name, digest in expected.items():
        source = root / name
        if digest is None:
            assert not source.exists(), 'unexpected file in base image: ' + name
        else:
            assert hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest() == digest, (
                'image source differs from audit; extract and review before switching: ' + name)
    # Imports must not depend on a live DB, IdP, object store, or mutable /app.
    os.environ.update(MENTORFLOW_DEPLOYMENT_MODE='PERSONAL_LOCAL',
                      MENTORFLOW_DB='/tmp/mfu10-import-app.db',
                      MENTORFLOW_ORM_DB='/tmp/mfu10-import-orm.db')
    sys.path[:0] = [str(root / 'backend'), str(root)]
    for name in MODULES:
        if mode == 'candidate' or name != 'app.enterprise.catalog_api':
            importlib.import_module(name)
    from app.account_core_mvp import app
    routes = set(app.openapi()['paths'])
    assert not any('/objects/' in route for route in routes), 'original storage must not be wired'
    assert not any('account_object_storage' in name for name in sys.modules)
    if mode == 'candidate':
        for path in ('/api/enterprise/sync/v1/changes', '/api/enterprise/auth/native/session',
                     '/api/enterprise/catalog/model-prices', '/api/enterprise/catalog/source-whitelist'):
            assert path in routes, 'missing assembled route: ' + path
    for module in tuple(sys.modules.values()):
        name = getattr(module, '__name__', '')
        if name == 'app' or name.startswith(('app.', 'refactor.server')):
            file = getattr(module, '__file__', None)
            if file:
                relative = Path(file).resolve().relative_to(root.resolve()).as_posix()
                assert relative in expected, 'runtime module missing from audit: ' + relative
    print(mode + ': source audit, app import, new modules, routes and absence of object storage: PASS')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['base', 'candidate'])
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    if not args.execute:
        print('DRY RUN: import and audit ' + args.mode + ' in a disconnected read-only image')
        return
    check(args.mode, Path('/app'), Path(__file__).resolve().parent)


if __name__ == '__main__':
    main()
