"""Bootstrap-owned manifest states over real PostgreSQL, observed only by HTTP."""
from datetime import datetime
import json

from sqlalchemy import create_engine, text

from contract_support145 import sync145_backends
from test_sync_http_contract145 import BASE, _headers, _entity_id, _envelope, _put_body


def test_sync_snapshot145_initialization_states_and_generation(sync145_backends):
    subject = 'user-145-manifest'
    issuer = sync145_backends['jwks'].issuer
    token = sync145_backends['jwks'].token(subject=subject)
    headers = _headers(token, 'manifest145')

    def observe(query=''):
        results = []
        for name in ('java',):
            response = sync145_backends[name].get(BASE+'/snapshot'+query, headers=headers)
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload['snapshot_revision'].startswith('pg-snapshot:')
            for state in (payload['learning_state'], payload['observation']['learning_state']):
                state['snapshot_revision'] = '<MVCC>'
                if state['manifest']:
                    for key in ('created_at', 'updated_at'):
                        assert datetime.fromisoformat(state['manifest'][key]).tzinfo
                        state['manifest'][key] = '<DATABASE-TIME>'
            payload['snapshot_revision'] = '<MVCC>'
            provenance = payload['observation']
            assert datetime.fromisoformat(provenance['server_confirmed_at']).tzinfo
            provenance['snapshot_revision'] = '<MVCC>'
            provenance['server_confirmed_at'] = '<DATABASE-TIME>'
            results.append(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        return json.loads(results[0])

    assert observe()['learning_state']['status'] == 'NOT_INITIALIZED'
    # Setup, not an API assertion: mimic the existing bootstrap's committed
    # manifest. The sync API itself cannot initialize an account.
    for name in ('java',):
        engine = create_engine(sync145_backends[name+'_admin_url'])
        try:
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO organizations(id,slug,name,status) VALUES ('org-145','org-145','Test145','active') ON CONFLICT DO NOTHING"))
                connection.execute(text("INSERT INTO workspaces(id,organization_id,slug,name,status) VALUES ('ws-145','org-145','ws-145','Test145','active') ON CONFLICT DO NOTHING"))
                connection.execute(text('''INSERT INTO rehydration_manifests
                  (issuer,subject_id,organization_id,workspace_id,scope_version,initialized,generation)
                  VALUES (:issuer,:subject,'org-145','ws-145','SUPPORTED_REHYDRATION_SCOPE_V1',true,1)'''),
                  dict(issuer=issuer, subject=subject))
        finally:
            engine.dispose()
    empty = observe()
    assert empty['learning_state']['status'] == 'TRUE_EMPTY_SUPPORTED_SCOPE'
    assert empty['learning_state']['known_empty'] is True
    for name in ('java',):
        response = sync145_backends[name].put(BASE+'/entities/learning_run/'+_entity_id('manifest-record'),
            headers=headers, content=_put_body(_envelope('manifest-record'), request_id='manifest-write', revision=0))
        assert response.status_code == 200
    nonempty = observe()
    assert nonempty['learning_state']['status'] == 'NONEMPTY'
    assert nonempty['learning_state']['state_generation'] == 2
    for name in ('java',):
        response = sync145_backends[name].put(BASE+'/entities/learning_run/'+_entity_id('manifest-record'),
            headers=headers, content=_put_body(_envelope('manifest-record'), request_id='manifest-write', revision=0))
        assert response.status_code == 200
    assert observe()['learning_state']['state_generation'] == 2
    assert observe('?required_scope=unsupported')['learning_state']['status'] == 'PARTIAL_UNSUPPORTED'
    assert observe('?scope_version=unknown')['learning_state']['status'] == 'PARTIAL_UNSUPPORTED'
