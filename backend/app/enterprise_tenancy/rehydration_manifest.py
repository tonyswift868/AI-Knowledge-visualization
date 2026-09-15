"""Bootstrap-owned durable state; no client assertion can initialize it."""
from sqlalchemy import text
import hashlib
import json

SCOPE_VERSION = 'SUPPORTED_REHYDRATION_SCOPE_V1'
# All structured domains have a server snapshot/apply path.  Binary resources
# remain metadata-only and are intentionally outside this scope.
SUPPORTED_SCOPE = ('plans', 'progress', 'resource_metadata', 'plan_tasks', 'calendars',
                   'review_fsrs', 'learner_profile', 'learning_runs', 'teaching_chat_history', 'nonsecret_settings')
WHERE_SCOPE = 'issuer=:issuer AND subject_id=:subject_id AND organization_id=:organization_id AND workspace_id=:workspace_id'


def lock_learning_scope(connection, scope):
    digest = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).digest()
    key = int.from_bytes(digest[:8], 'big', signed=True)
    connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})


def initialize_learning_state(connection, scope):
    # Called only after membership and profile initialization in the same commit.
    # Existing initialized state/generation is retained on repeated bootstrap.
    lock_learning_scope(connection, scope)
    connection.execute(text('''
        INSERT INTO rehydration_manifests
          (issuer,subject_id,organization_id,workspace_id,scope_version,initialized,generation)
        VALUES (:issuer,:subject_id,:organization_id,:workspace_id,:version,true,1)
        ON CONFLICT (issuer,subject_id,organization_id,workspace_id) DO UPDATE
          SET initialized=true, generation=rehydration_manifests.generation+1, updated_at=CURRENT_TIMESTAMP
          WHERE NOT rehydration_manifests.initialized AND rehydration_manifests.scope_version=:version
    '''), dict(scope, version=SCOPE_VERSION))


def advance_learning_generation(connection, scope):
    connection.execute(text('''UPDATE rehydration_manifests
        SET generation=generation+1, updated_at=CURRENT_TIMESTAMP
        WHERE ''' + WHERE_SCOPE + ' AND initialized AND scope_version=:version'), dict(scope, version=SCOPE_VERSION))


def learning_state_initialized(connection, scope):
    return bool(connection.execute(text('SELECT initialized FROM rehydration_manifests WHERE '+WHERE_SCOPE+
        ' AND scope_version=:version'), dict(scope, version=SCOPE_VERSION)).scalar_one_or_none())
