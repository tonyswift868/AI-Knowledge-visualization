"""Read-only post-release HTTP checks; default prints requests, --execute sends."""
import argparse
import hashlib
import json
from pathlib import Path
import ssl
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPSHandler, HTTPRedirectHandler, ProxyHandler, build_opener

HEALTH = ('/health/live', '/health/startup', '/health/ready')
CATALOGS = ('/api/enterprise/catalog/source-whitelist', '/api/enterprise/catalog/model-prices')
SESSION = '/api/enterprise/auth/native/session'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def auth_headers(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    token = value['access_token']
    if not isinstance(token, str) or not token or '\n' in token or '\r' in token:
        raise ValueError('invalid test access token')
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
    for field, header in (('organization_id', 'X-MentorFlow-Organization-ID'),
                          ('workspace_id', 'X-MentorFlow-Workspace-ID')):
        if value.get(field):
            headers[header] = value[field]
    return headers


def error_code(content):
    value = json.loads(content)
    return value.get('error', value).get('code')


def verify(args):
    ssl_context = ssl.create_default_context(cafile=str(args.ca_file))
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl_context), NoRedirect())
    def get(path, headers=None):
        try:
            response = opener.open(Request(args.url.rstrip('/') + path, headers=headers or {}), timeout=15)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read(1024 * 1024 + 1)
            assert len(raw) <= 1024 * 1024, 'response exceeds catalog size limit'
            return response.status, response.headers, raw
    for path in HEALTH:
        assert get(path)[0] == 200, path + ' is not healthy'
        print('200 ' + path)
    if args.mode == 'health':
        return
    headers = auth_headers(args.auth_file)
    if args.mode in {'empty', 'catalogs'}:
        assert 'X-MentorFlow-Organization-ID' in headers and 'X-MentorFlow-Workspace-ID' in headers
        for path in CATALOGS:
            assert get(path)[0] == 401, 'anonymous catalog request must return 401'
            status, response_headers, raw = get(path, headers)
            if args.mode == 'empty':
                assert status == 404 and error_code(raw) == 'CATALOG_NOT_CONFIGURED', path
                print('404 CATALOG_NOT_CONFIGURED ' + path)
                continue
            assert status == 200, path + ' must return 200'
            value = json.loads(raw)
            assert isinstance(value, dict) and 'version' in value
            if path.endswith('source-whitelist'):
                assert set(value) == {'version', 'updated', 'whitelist', 'github_orgs', 'low', 'blocked', 'blocked_keywords'}
            expected_etag = '"' + hashlib.sha256(raw).hexdigest() + '"'
            assert response_headers['ETag'] == expected_etag
            assert response_headers['Cache-Control'] == 'private, max-age=300'
            status, cached_headers, cached = get(path, dict(headers, **{'If-None-Match': expected_etag}))
            assert status == 304 and cached == b'' and cached_headers['ETag'] == expected_etag
            status, repeated_headers, repeated = get(path, headers)
            assert status == 200 and repeated == raw and repeated_headers['ETag'] == expected_etag
            print('200 / 304 / stable SHA256 ETag ' + path)
    if args.mode == 'session':
        status, _, raw = get(SESSION, headers)
        assert status == 200 and json.loads(raw) == {'state': 'active'}
        assert get(SESSION)[0] == 401
        print('session active 200 / anonymous 401')
        if args.superseded_auth_file:
            status, _, raw = get(SESSION, auth_headers(args.superseded_auth_file))
            assert status == 401 and error_code(raw) == 'NATIVE_SESSION_SUPERSEDED'
            print('old session 401 NATIVE_SESSION_SUPERSEDED')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['health', 'empty', 'catalogs', 'session'], required=True)
    parser.add_argument('--url', required=True)
    parser.add_argument('--ca-file', type=Path, required=True)
    parser.add_argument('--auth-file', type=Path)
    parser.add_argument('--superseded-auth-file', type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    parsed = urlsplit(args.url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        parser.error('--url must be the configured HTTPS origin')
    if args.mode != 'health' and args.auth_file is None:
        parser.error('--auth-file is required for authenticated checks')
    if not args.execute:
        for path in HEALTH:
            print('GET ' + args.url.rstrip('/') + path + ' -> expect 200')
        if args.mode in {'empty', 'catalogs'}:
            for path in CATALOGS:
                print('GET ' + args.url.rstrip('/') + path + ' [current Bearer + workspace headers from auth file]')
                print('expect ' + ('404 CATALOG_NOT_CONFIGURED' if args.mode == 'empty' else '200 + SHA256 ETag; conditional 304'))
        if args.mode == 'session':
            print('GET ' + args.url.rstrip('/') + SESSION + ' [current Bearer] -> active 200; anonymous -> 401')
            if args.superseded_auth_file:
                print('GET ' + args.url.rstrip('/') + SESSION + ' [old Bearer] -> 401 NATIVE_SESSION_SUPERSEDED')
        print('DRY RUN: no files read, no requests sent; add --execute to run checks')
        return
    try:
        verify(args)
    except Exception as exc:
        # Never print response bodies, tokens, request headers or provider errors.
        parser.exit(1, 'Verification failed (' + type(exc).__name__ + '); inspect the selected step locally.\n')


if __name__ == '__main__':
    main()
