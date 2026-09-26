import hashlib
import json
from pathlib import Path

import pytest

from test_account_workspace_bootstrap import system, headers


PREFIX = '/api/enterprise/catalog/'


@pytest.fixture
def catalog(system, tmp_path, monkeypatch):
    client, _, _ = system
    workspace = client.post('/api/enterprise/account/workspaces/initialize', headers=headers(), json={}).json()['workspaces'][0]
    root = tmp_path / 'catalog'
    root.mkdir()
    monkeypatch.setenv('MENTORFLOW_CATALOG_DIR', str(root))
    return client, root, headers(workspace=workspace)


@pytest.mark.parametrize('name', ['source-whitelist', 'model-prices'])
def test_catalog_exact_bytes_stable_sha256_etag_and_304(catalog, name):
    client, root, auth = catalog
    raw = '{\n  "version": "v1", "entries": ["中文"]\n}\n'.encode()
    path = root / (name + '.json')
    path.write_bytes(raw)
    first = client.get(PREFIX + name, headers=auth)
    assert first.status_code == 200
    assert first.content == raw
    assert first.headers['content-type'].startswith('application/json')
    etag = '"' + hashlib.sha256(raw).hexdigest() + '"'
    assert first.headers['etag'] == etag
    assert first.headers['cache-control'] == 'private, max-age=300'
    assert client.get(PREFIX + name, headers=auth).headers['etag'] == etag
    for condition in (etag, 'W/' + etag, '"different", W/' + etag, '*'):
        response = client.get(PREFIX + name, headers={**auth, 'If-None-Match': condition})
        assert response.status_code == 304
        assert response.content == b''
        assert response.headers['etag'] == etag
        assert response.headers['cache-control'] == 'private, max-age=300'
    path.write_bytes(raw + b' ')
    changed = client.get(PREFIX + name, headers={**auth, 'If-None-Match': etag})
    assert changed.status_code == 200
    assert changed.headers['etag'] != etag


@pytest.mark.parametrize('name', ['source-whitelist', 'model-prices'])
def test_missing_catalog_404_and_unauthenticated_401(catalog, name):
    client, root, auth = catalog
    response = client.get(PREFIX + name, headers=auth)
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'CATALOG_NOT_CONFIGURED'
    assert response.headers['cache-control'] == 'no-store'
    (root / (name + '.json')).write_text('{"version":"v1"}', encoding='utf-8')
    assert client.get(PREFIX + name, headers={'If-None-Match': '*'}).status_code == 401
    assert client.get(PREFIX + name, headers={'Authorization': 'Bearer invalid'}).status_code == 401


@pytest.mark.parametrize('raw', [b'broken', b'[]', b'{}', b'{"version": NaN}', b'\xff',
                               b'{"version":"v1","value":Infinity}', b' ' * (1024 * 1024 + 1)],
                         ids=['syntax', 'array', 'missing-version', 'nan', 'invalid-encoding', 'infinity', 'oversized'])
def test_invalid_catalog_503_even_for_conditional_request(catalog, raw):
    client, root, auth = catalog
    (root / 'model-prices.json').write_bytes(raw)
    response = client.get(PREFIX + 'model-prices', headers={**auth, 'If-None-Match': '*'})
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'CATALOG_INVALID'
    assert str(root) not in response.text
    assert response.headers['cache-control'] == 'no-store'


def test_one_mib_is_inclusive_and_read_errors_fail_closed(catalog):
    client, root, auth = catalog
    base = b'{"version":"v1"}'
    path = root / 'model-prices.json'
    path.write_bytes(base + b' ' * (1024 * 1024 - len(base)))
    assert client.get(PREFIX + 'model-prices', headers=auth).status_code == 200
    path.unlink()
    path.mkdir()
    response = client.get(PREFIX + 'model-prices', headers=auth)
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'CATALOG_INVALID'


def test_example_catalog_has_verified_period_specific_deepseek_rates():
    path = Path(__file__).resolve().parents[2] / 'deploy/enterprise/staging/rp05/catalog/model-prices.example.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    # The example is revised as prices are re-checked; require a dated version.
    assert isinstance(value['version'], str) and value['version'][:10] >= '2026-09-27'
    required = {'provider', 'model', 'currency', 'input_cache_hit_per_mtok', 'input_cache_miss_per_mtok',
                'output_per_mtok', 'source_url', 'checked_on', 'verified'}
    assert value['models'] and all(required <= row.keys() for row in value['models'])
    rates = {(r['model'], r['price_period']): r for r in value['models']}
    for model, expected in [('deepseek-flash', (0.02, 1, 4)), ('deepseek-v4-pro', (0.15, 4.5, 13.5))]:
        low, peak = rates[model, 'off_peak'], rates[model, 'peak']
        assert low['verified'] is True and peak['verified'] is True
        assert low['source_url'] == 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
        assert low['currency'] == 'CNY'
        for key, amount in zip(('input_cache_hit_per_mtok', 'input_cache_miss_per_mtok', 'output_per_mtok'), expected):
            assert low[key] == amount
            assert peak[key] == 2 * amount
