"""Account-scoped structured state: synchronous commit, CAS, replay and tombstones.

This is an explicit V1 subset, not an exporter of the legacy learning databases.
Identity is supplied exclusively by the existing authenticated Actor/Tenant seam.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (MetaData, Table, Column, String, Integer, Boolean, JSON,
                        CheckConstraint, select, insert, update, and_, text)

from .context import TenantTransactionContext
from .models import EnterprisePersistenceRequestError
from .sync_registry import SYNC_KINDS, SYNC_ITEM_REGISTRY


metadata = MetaData()
SCOPE = ('issuer', 'subject_id', 'organization_id', 'workspace_id')
def scope_columns():
    return [Column(n, String(512 if n == 'issuer' else 128), primary_key=True) for n in SCOPE]

entities = Table('structured_sync_entities', metadata, *scope_columns(),
    Column('kind', String(24), primary_key=True), Column('entity_id', String(160), primary_key=True),
    Column('revision', Integer, nullable=False), Column('deleted', Boolean, nullable=False),
    Column('payload', JSON, nullable=False),
    CheckConstraint('revision > 0', name='ck_sync_revision'),
    CheckConstraint("kind IN ('plan','progress','resource','task','calendar','review','learner_profile','learning_run','history','setting')", name='ck_sync_kind'))
requests = Table('structured_sync_requests', metadata, *scope_columns(),
    Column('request_id', String(128), primary_key=True),
    Column('digest', String(64), nullable=False), Column('response', JSON, nullable=False))

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)

ID_PATTERN = r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
class Plan(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    resource_ids: list[str] = Field(default_factory=list, max_length=100)

class Progress(StrictModel):
    plan_id: str = Field(pattern=ID_PATTERN)
    mastery: float = Field(ge=0, le=1)
    completed_tasks: int = Field(ge=0)

class Resource(StrictModel):
    product_classification: Literal["USER_FILE", "MODEL", "OTHER", "UNKNOWN"] = "UNKNOWN"
    filename: str = Field(min_length=1, max_length=300, pattern=r'^[^/\\\x00-\x1f]+$')
    media_type: str = Field(min_length=1, max_length=120, pattern=r'^[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+$')
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    size_bytes: int = Field(ge=0, le=9_223_372_036_854_775_807)

class Write(StrictModel):
    request_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$')
    expected_revision: int = Field(ge=0, le=2_147_483_646)
    payload: dict

class Delete(StrictModel):
    request_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$')
    expected_revision: int = Field(ge=1, le=2_147_483_646)

def error(code, status=409):
    return EnterprisePersistenceRequestError(code, code, status=status)

def scope_of(context):
    return dict(issuer=context.actor.issuer, subject_id=context.actor.subject_id,
                organization_id=context.tenant.organization_id, workspace_id=context.tenant.workspace_id)

def predicate(table, scope):
    return and_(*(table.c[n] == scope[n] for n in SCOPE))

class StructuredSyncService:
    def __init__(self, transaction_manager):
        self.transactions = transaction_manager

    @staticmethod
    def _bind(tx, scope, *, writing=False):
        c = tx.connection
        if c.dialect.name == 'postgresql':
            c.execute(text("SELECT set_config('mentorflow.actor_issuer', :issuer, true)"), scope)
            if writing:
                # Serialize one account/workspace, including references and first insert.
                from .rehydration_manifest import lock_learning_scope
                lock_learning_scope(c, scope)
                # Success requires WAL flush, even if a session default disables it.
                c.execute(text('SET LOCAL synchronous_commit = on'))

    def write(self, actor, tenant, kind, entity_id, body, *, deleting=False):
        import re
        from pydantic import ValidationError
        if kind not in SYNC_KINDS or not re.fullmatch(ID_PATTERN, entity_id):
            raise error('SYNC_ENTITY_INVALID', 422)
        payload = {}
        if not deleting:
            try:
                model = {'plan': Plan, 'progress': Progress, 'resource': Resource}.get(kind)
                payload = (model.model_validate(body.payload).model_dump() if model else SYNC_ITEM_REGISTRY[kind].project(body.payload))
            except ValidationError as exc:
                raise error('SYNC_PAYLOAD_INVALID', 422) from exc
            # Preserve pre-contract request fingerprints when the optional field is absent.
            if kind == 'resource' and 'product_classification' not in body.payload:
                payload.pop('product_classification', None)
            if kind == 'plan' and (len(set(payload['resource_ids'])) != len(payload['resource_ids']) or
                    any(not re.fullmatch(ID_PATTERN, r) for r in payload['resource_ids'])):
                raise error('SYNC_RESOURCE_IDS_INVALID', 422)
        context = TenantTransactionContext(tenant=tenant, actor=actor)
        scope = scope_of(context)
        fingerprint = hashlib.sha256(json.dumps([kind, entity_id, body.expected_revision, deleting, payload],
            sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        with self.transactions.transaction(context) as tx:
            self._bind(tx, scope, writing=True)
            c = tx.connection
            replay = c.execute(select(requests).where(predicate(requests, scope), requests.c.request_id == body.request_id)).mappings().first()
            if replay:
                if replay['digest'] != fingerprint:
                    raise error('SYNC_IDEMPOTENCY_KEY_REUSED')
                result = replay['response']
            else:
                match = and_(predicate(entities, scope), entities.c.kind == kind, entities.c.entity_id == entity_id)
                current = c.execute(select(entities).where(match)).mappings().first()
                revision = current['revision'] if current else 0
                if deleting and not current:
                    raise error('SYNC_ENTITY_NOT_FOUND', 404)
                if revision != body.expected_revision:
                    raise error('SYNC_STALE_REVISION')
                if current and current['deleted']:
                    raise error('SYNC_ENTITY_TOMBSTONED', 410)
                refs = [('resource', r) for r in payload.get('resource_ids', [])]
                if kind == 'progress' and not deleting:
                    refs.append(('plan', payload['plan_id']))
                for ref_kind, ref_id in refs:
                    found = c.execute(select(entities.c.entity_id).where(predicate(entities, scope),
                        entities.c.kind == ref_kind, entities.c.entity_id == ref_id, entities.c.deleted.is_(False))).first()
                    if not found:
                        raise error('SYNC_REFERENCE_NOT_IN_CURRENT_SCOPE', 422)
                result = dict(kind=kind, entity_id=entity_id, revision=revision + 1, deleted=deleting, payload=payload)
                if current:
                    c.execute(update(entities).where(match, entities.c.revision == revision).values(
                        revision=result['revision'], deleted=deleting, payload=payload))
                else:
                    c.execute(insert(entities).values(**scope, **result))
                c.execute(insert(requests).values(**scope, request_id=body.request_id, digest=fingerprint, response=result))
                from .rehydration_manifest import advance_learning_generation
                advance_learning_generation(c, scope)
        # Context exits (and COMMIT succeeds) before any HTTP success is possible.
        return result

    def snapshot(self, actor, tenant, *, requested_scope=None, scope_version=None):
        from .sync_snapshot import observe
        context = TenantTransactionContext(tenant=tenant, actor=actor)
        scope = scope_of(context)
        with self.transactions.transaction(context) as tx:
            self._bind(tx, scope)
            result = observe(tx.connection, scope, requested_scope=requested_scope, scope_version=scope_version)
        return result
