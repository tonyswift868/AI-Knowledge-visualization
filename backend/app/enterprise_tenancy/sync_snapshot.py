"""One SQL statement owns the data, MVCC marker and server observation time."""
from sqlalchemy import text
from .models import EnterprisePersistenceRequestError

SUPPORTED = ('plans', 'progress', 'resources')
UNSUPPORTED = ('tasks_calendar', 'review', 'learner_model', 'learning_runs',
               'messages', 'teaching_sessions', 'nonsecret_settings', 'model_metadata')
_SCOPE = 'issuer=:issuer AND subject_id=:subject_id AND organization_id=:organization_id AND workspace_id=:workspace_id'
OBSERVE = text('''
WITH scoped AS MATERIALIZED (
 SELECT kind, entity_id, revision, deleted, payload FROM structured_sync_entities
 WHERE ''' + _SCOPE + ''' ORDER BY kind, entity_id LIMIT 1001
)
SELECT pg_current_snapshot()::text AS revision,
       statement_timestamp() AS observed_at,
       COALESCE((SELECT json_agg(scoped ORDER BY kind, entity_id) FROM scoped), '[]'::json) AS entities,
       EXISTS(SELECT 1 FROM structured_sync_requests WHERE ''' + _SCOPE + ''') AS initialized
''')


def observe(connection, scope):
    if connection.dialect.name != 'postgresql':
        raise EnterprisePersistenceRequestError('SYNC_QUERY_FAILED', 'PostgreSQL required', status=503)
    row = connection.execute(OBSERVE, scope).mappings().one()
    entities = row['entities']
    if len(entities) > 1000:
        raise EnterprisePersistenceRequestError('SYNC_SNAPSHOT_LIMIT_EXCEEDED', 'Snapshot limit exceeded', status=413)
    marker = 'pg-snapshot:' + row['revision']
    confirmed = row['observed_at'].isoformat()
    live = [e for e in entities if not e['deleted']]
    references = {}
    for e in live:
        if e['kind'] == 'plan':
            for resource in e['payload'].get('resource_ids', []):
                references.setdefault(resource, []).append(e['entity_id'])
    resource_projection = [dict(
        resource_id=e['entity_id'], revision=e['revision'],
        product_classification=e['payload'].get('product_classification', 'UNKNOWN'),
        classification_authority=('AUTHENTICATED_RESOURCE_METADATA' if
            'product_classification' in e['payload'] else 'UNCLASSIFIED_LEGACY_METADATA'),
        plan_ids=sorted(references.get(e['entity_id'], [])),
        device_availability='NOT_OBSERVED_BY_SERVER',
    ) for e in live if e['kind'] == 'resource']
    counts = {name: sum(e['kind'] == kind for e in live) for name, kind in
              (('plans', 'plan'), ('progress', 'progress'), ('resources', 'resource'))}
    counts['tombstones'] = len(entities) - len(live)
    # There is no full-account initialization/empty authority in the accepted schema.
    # Unknown unsupported/local history must never be converted into an empty fact.
    learning_state = dict(status='PARTIAL_UNSUPPORTED', known_empty=False,
        account_initialized='UNKNOWN', supported_scope_initialized=bool(row['initialized']),
        supported_scope_empty=not live, authority='COMMITTED_V1_STATE_AND_REQUEST_LEDGER',
        reason='FULL_ACCOUNT_HISTORY_AUTHORITY_UNAVAILABLE')
    provenance = dict(source='SERVER_CONFIRMED', snapshot_revision=marker,
                      server_confirmed_at=confirmed, trusted_cache=False)
    return dict(schema='mentorflow.structured-sync.v1', contract_version=2,
        complete=True, completeness_scope='SUPPORTED_ENTITIES_ONLY',
        snapshot_revision=marker, snapshot_authority='POSTGRESQL_SINGLE_STATEMENT_MVCC',
        snapshot_scope=scope, revision_ordering='OPAQUE_OBSERVATION_NOT_INCREMENTAL_CURSOR',
        supported_entity_types=list(SUPPORTED), unsupported_entity_types=list(UNSUPPORTED),
        learning_state=learning_state, entities=entities, resources=resource_projection,
        progress_provenance=[dict(entity_id=e['entity_id'], revision=e['revision'], **provenance)
                             for e in live if e['kind'] == 'progress'],
        observation=dict(schema='mentorflow.sync-observation.v1', **provenance,
            supported_entity_types=list(SUPPORTED), unsupported_entity_types=list(UNSUPPORTED),
            counts=counts, unsupported_counts={name: None for name in UNSUPPORTED},
            resource_missing_state='NOT_OBSERVED_BY_SERVER', learning_state=learning_state))
