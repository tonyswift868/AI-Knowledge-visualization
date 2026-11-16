"""FastAPI/Pydantic wire errors, exercised through both HTTP implementations."""
import json

import pytest

from contract_support145 import sync145_backends, ORG, WORKSPACE


CASES145 = [
    ('GET', '/changes?since=-1', None),
    ('GET', '/changes?since=9223372036854775808', None),
    ('GET', '/changes?since=wat', None),
    ('GET', '/changes?since=1.0', None),
    ('GET', '/changes?limit=0', None),
    ('GET', '/changes?limit=501', None),
    ('GET', '/changes?kind=unknown', None),
    ('GET', '/changes?since=-1&kind=unknown&limit=0', None),
    ('GET', '/snapshot?scope_version='+'x'*65, None),
    ('PUT', '/entities/unknown/a', {}),
    ('PUT', '/entities/task/a', {}),
    ('PUT', '/entities/task/a', {'request_id': '', 'expected_revision': 0, 'payload': {}}),
    ('PUT', '/entities/task/a', {'request_id': 'a'*129, 'expected_revision': 0, 'payload': {}}),
    ('PUT', '/entities/task/a', {'request_id': 'bad space', 'expected_revision': 0, 'payload': {}}),
    ('PUT', '/entities/task/a', {'request_id': 1, 'expected_revision': True, 'payload': []}),
    ('PUT', '/entities/task/a', {'request_id': 'v', 'expected_revision': 1.0, 'payload': {}}),
    ('PUT', '/entities/task/a', {'request_id': 'v', 'expected_revision': -1, 'payload': {}, 'extra': '未知'}),
    ('PUT', '/entities/task/a', {'request_id': 'v', 'expected_revision': 2147483647, 'payload': {}}),
    ('POST', '/entities/task/a/delete', {'request_id': 'v', 'expected_revision': 0, 'payload': {}}),
    ('POST', '/entities/task/a/tombstone', {'request_id': 'v', 'expected_revision': -1}),
    ('POST', '/entities/task/a/tombstone', {'request_id': 'v', 'expected_revision': 0, 'payload': {}}),
    ('POST', '/entities/bulk', {'records': []}),
    ('POST', '/entities/bulk', {'records': [1]}),
    ('POST', '/entities/bulk', {'records': 'bad', 'extra': 1}),
    ('POST', '/entities/bulk', {'records': [{}]*501}),
    ('POST', '/entities/bulk', []),
    ('PUT', '/entities/task/a', None),
]


@pytest.mark.parametrize('method,path,body', CASES145)
def test_sync_validation145_exact_http_errors(sync145_backends, method, path, body):
    headers = {'Authorization': 'Bearer '+sync145_backends['jwks'].token(subject='user-145-validation'),
               'X-MentorFlow-Organization-ID': ORG,
               'X-MentorFlow-Workspace-ID': WORKSPACE, 'X-Request-ID': 'validation145'}
    response = sync145_backends["java"].request(method, '/api/enterprise/sync/v1'+path,
                                               headers=headers, json=body)
    assert response.status_code == (200 if path == '/changes?since=1.0' else 422), response.text
    assert response.headers['content-type'].startswith('application/json')
    assert response.headers['x-request-id'] == 'validation145'



@pytest.mark.parametrize('body,content_type', [
    (b'{', 'application/json'),
    (b'{"request_id":', 'application/json'),
    (b'{"x":1,}', 'application/json'),
    (b'{"x":1', 'application/json'),
    (b'{"x" 1}', 'application/json'),
    (b'{"x":1 "y":2}', 'application/json'),
    (b'{"x":"'+bytes([92])+b'q"}', 'application/json'),
    (b'{}{}', 'application/json'),
    ('{"文字😀":1 "y":2}'.encode('utf8'), 'application/json'),
    (b'[]', 'application/json'),
    (b'not json', 'text/plain'),
    (bytes([255]), 'application/json'),
    (bytes([239,187,191])+b'{}', 'application/json'),
])
def test_sync_validation145_raw_body_errors(sync145_backends, body, content_type):
    headers = {'Authorization': 'Bearer '+sync145_backends['jwks'].token(subject='user-145-validation'),
               'X-MentorFlow-Organization-ID': ORG,
               'X-MentorFlow-Workspace-ID': WORKSPACE, 'X-Request-ID': 'raw145',
               'Content-Type': content_type}
    response = sync145_backends["java"].put('/api/enterprise/sync/v1/entities/task/a',
                                           content=body, headers=headers)
    assert response.status_code in (400, 422), response.text
    assert response.headers['content-type'].startswith('application/json')
    assert response.headers['x-request-id'] == 'raw145'
