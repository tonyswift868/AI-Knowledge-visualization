"""One SQL statement owns the data, MVCC marker and server observation time."""
from sqlalchemy import text
from .models import EnterprisePersistenceRequestError
from .rehydration_manifest import SCOPE_VERSION, SUPPORTED_SCOPE

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
       (SELECT row_to_json(m) FROM (
           SELECT scope_version,initialized,generation,created_at,updated_at
           FROM rehydration_manifests WHERE ''' + _SCOPE + ''') m) AS manifest
''')


def observe(connection, scope, *, requested_scope=None, scope_version=None):
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
    manifest = row['manifest']
    requested = list(SUPPORTED_SCOPE if requested_scope is None else requested_scope)
    version = SCOPE_VERSION if scope_version is None else scope_version
    initialized = bool(manifest and manifest['initialized'] and manifest['scope_version'] == SCOPE_VERSION)
    unsupported = version != SCOPE_VERSION or any(name not in SUPPORTED_SCOPE for name in requested)
    unsupported |= bool(manifest and manifest['scope_version'] != SCOPE_VERSION)
    state = ('PARTIAL_UNSUPPORTED' if unsupported else 'NOT_INITIALIZED' if not initialized
             else 'NONEMPTY' if live else 'TRUE_EMPTY_SUPPORTED_SCOPE')
    learning_state = dict(status=state, empty_state=state,
        known_empty=state == 'TRUE_EMPTY_SUPPORTED_SCOPE', global_account_empty='UNKNOWN',
        account_initialized='UNKNOWN', initialized=initialized, supported_scope_initialized=initialized,
        supported_scope_empty=not live if initialized and not unsupported else None,
        supported_scope=list(SUPPORTED_SCOPE), scope_version=SCOPE_VERSION,
        requested_scope=requested, requested_scope_version=version,
        initialization_authority='COMMITTED_BOOTSTRAP_REHYDRATION_MANIFEST',
        state_generation=manifest['generation'] if manifest else None,
        manifest=manifest, snapshot_revision=marker)
    provenance = dict(source='SERVER_CONFIRMED', snapshot_revision=marker,
                      server_confirmed_at=confirmed, trusted_cache=False)
    return dict(schema='mentorflow.structured-sync.v1', contract_version=3,
        complete=True, completeness_scope='SUPPORTED_ENTITIES_ONLY',
        snapshot_revision=marker, snapshot_authority='POSTGRESQL_SINGLE_STATEMENT_MVCC',
        snapshot_scope=scope, revision_ordering='OPAQUE_OBSERVATION_NOT_INCREMENTAL_CURSOR',
        supported_entity_types=list(SUPPORTED), unsupported_entity_types=list(UNSUPPORTED),
        learning_state=learning_state, entities=entities, resources=resource_projection,
        progress_provenance=[dict(entity_id=e['entity_id'], revision=e['revision'], **provenance)
                             for e in live if e['kind'] == 'progress'],
        observation=dict(schema='mentorflow.sync-observation.v1', **provenance,
            empty_state=state, supported_scope=list(SUPPORTED_SCOPE), scope_version=SCOPE_VERSION,
            initialized=initialized, state_generation=learning_state['state_generation'],
            supported_entity_types=list(SUPPORTED), unsupported_entity_types=list(UNSUPPORTED),
            counts=counts, unsupported_counts={name: None for name in UNSUPPORTED},
            resource_missing_state='NOT_OBSERVED_BY_SERVER', learning_state=learning_state))
