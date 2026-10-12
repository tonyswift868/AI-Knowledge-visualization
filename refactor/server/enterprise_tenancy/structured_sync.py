"""Account-scoped structured state: synchronous commit, CAS, replay and tombstones.

This is an explicit V1 subset, not an exporter of the legacy learning databases.
Identity is supplied exclusively by the existing authenticated Actor/Tenant seam.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (MetaData, Table, Column, String, Integer, Boolean, JSON,
                        CheckConstraint, select, insert, update, and_, text)

from .context import TenantTransactionContext
from .models import EnterprisePersistenceRequestError
from .sync_registry import SYNC_KINDS, SYNC_ITEM_REGISTRY
from ..product_sync_contract import validate as validate_product_record, ProductSyncRejected

_SECRET_KEYS = frozenset({'password', 'access_token', 'refresh_token', 'token', 'credential',
                          'api_key', 'provider_secret', 'private_key', 'signing_key', 'secret'})

def _reject_secret(value):
    if isinstance(value, dict):
        if any(str(k).lower() in _SECRET_KEYS or any(part in str(k).lower() for part in ('password', 'token', 'secret', 'credential', 'private_key')) for k in value):
            raise error('SYNC_SECRET_FIELD_REJECTED', 422)
        for child in value.values():
            _reject_secret(child)
    elif isinstance(value, list):
        for child in value:
            _reject_secret(child)


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
_REFACTOR_KIND_BY_RECORD = {
    'plan': 'learning_run',
    'task': 'task',
    'progress': 'task',
    'turn': 'history',
    'note': 'history',
    'receipt': 'history',
    'resource': 'resource',
    'plan_revision': 'learning_run',
    'learning_contract': 'learning_run',
    'evidence': 'history',
    'error': 'history',
    'verification': 'history',
    'tutor_checkpoint': 'learning_run',
    'daily_mission': 'learning_run',
    'draft_plan': 'learning_run',
    'course_record': 'learning_run',
    'setting': 'setting',
    # Account-owned tools, free-chat sessions and researched sources are
    # durable learning history; they are not legacy product records.
    'tool_activity': 'history',
    'chat_session': 'history',
    'research_source': 'history',
}
_REFACTOR_FIELDS = {
    'plan': {'key', 'plan_id', 'workspace_id', 'title', 'workspace_title',
             'status', 'source', 'resources', 'issues', 'details', 'task_ids'},
    'task': {'plan_key', 'task'},
    'progress': {'plan_key', 'task_id', 'payload'},
    'turn': {'id', 'plan_key', 'task_id', 'action', 'question', 'answer',
             'state', 'created_at'},
    'note': {'plan_key', 'task_id', 'text', 'revision'},
    'receipt': {'id', 'plan_key', 'task_id', 'payload', 'created_at'},
    'resource': {'material_id', 'filename', 'media_type', 'size', 'sha256',
                 'product_classification', 'text'},
    'plan_revision': {'plan_key', 'plan_id', 'revision', 'status', 'snapshot', 'created_at'},
    'learning_contract': {'plan_key', 'contract_id', 'plan_revision', 'milestone_id',
                          'competency_ids', 'title', 'purpose', 'target_depth',
                          'visible_objectives', 'required_evidence', 'resource_refs',
                          'estimated_minutes', 'created_at'},
    'evidence': {'plan_key', 'evidence_id', 'contract_id', 'objective_ids',
                 'competency_ids', 'type', 'source', 'summary', 'independence',
                 'evaluator_result', 'confidence', 'created_at'},
    'error': {'plan_key', 'error_id', 'contract_id', 'evidence_id', 'competency_id',
              'error_class', 'description', 'context', 'hint_level',
              'resolved_in_session', 'misconception_candidate', 'created_at'},
    'verification': {'plan_key', 'verification_id', 'contract_id', 'verdict',
                     'objective_results', 'observed_errors', 'recommended_action',
                     'created_at'},
    'tutor_checkpoint': {'plan_key', 'checkpoint_id', 'plan_id', 'contract_id',
                         'achieved_objectives', 'pending_objectives',
                         'stable_competencies', 'attention_competencies',
                         'active_misconception_ids', 'recent_evidence_ids',
                         'suggested_next_step', 'summary', 'created_at'},
    'daily_mission': {'schema', 'plan_key', 'mission_id', 'plan_revision', 'day_date', 'timezone',
                      'status', 'task_ids', 'objectives', 'available_minutes',
                      'used_minutes', 'remaining_minutes', 'reflowed_task_ids', 'notice',
                      'started_at', 'updated_at', 'idempotency_key'},
    'draft_plan': {'plan_key', 'draft_id', 'revision', 'status', 'snapshot', 'confirmed',
                   'updated_at', 'idempotency_key'},
    'course_record': {'schema', 'plan_key', 'entity', 'entity_id', 'payload'},
    'setting': {'key', 'values'},
    'tool_activity': {'kind', 'activity_id', 'payload'},
    'chat_session': {'session_id', 'payload'},
    'research_source': {'source_id', 'payload'},
}
_REFACTOR_FIELD_VARIANTS = {
    # A pending verification item and a completed evaluator result share the
    # same durable history kind.  Accept both strict shapes so a device can
    # sync the queue before an evaluator has produced a result.
    'verification': (
        _REFACTOR_FIELDS['verification'],
        {'plan_key', 'verification_id', 'contract_id', 'objective_id',
         'priority', 'reason', 'status', 'source_error_ids', 'created_at'},
        {'plan_key', 'verification_id', 'contract_id', 'objective_id',
         'priority', 'reason', 'status', 'source_error_ids', 'reason_codes',
         'source_evidence_ids', 'created_at'},
    ),
    'evidence': (
        _REFACTOR_FIELDS['evidence'],
        _REFACTOR_FIELDS['evidence'] | {'session_id', 'artifact_refs'},
    ),
    'note': (
        {'plan_key', 'task_id', 'text'},
        _REFACTOR_FIELDS['note'],
    ),
}
class Plan(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    resource_ids: list[str] = Field(default_factory=list, max_length=100)
    product_record: dict | None = None

class Progress(StrictModel):
    plan_id: str | None = Field(pattern=ID_PATTERN)
    mastery: float = Field(ge=0, le=1)
    completed_tasks: int = Field(ge=0)
    product_record: dict | None = None

class Resource(StrictModel):
    product_record: dict | None = None
    product_classification: Literal["USER_FILE", "MODEL", "OTHER", "UNKNOWN"] = "UNKNOWN"
    filename: str = Field(min_length=1, max_length=300, pattern=r'^[^/\\\x00-\x1f]+$')
    media_type: str = Field(min_length=1, max_length=120, pattern=r'^[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+$')
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    size_bytes: int = Field(ge=0, le=9_223_372_036_854_775_807)
    # Refactor materials may carry a bounded text excerpt.  Original bytes
    # stay device-local; this is only the recoverable body fragment used by a
    # plan on another device.
    text: str | None = Field(default=None, max_length=8_000)
    # Refactor learning records use a namespaced envelope.  Keeping the
    # stable material identity beside the metadata lets a new device rebuild
    # the envelope from a server snapshot while legacy resource records keep
    # using the same canonical fields.
    refactor_record_id: str | None = Field(default=None, pattern=ID_PATTERN)

class Write(StrictModel):
    request_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$')
    expected_revision: int = Field(ge=0, le=2_147_483_646)
    payload: dict

class Delete(StrictModel):
    request_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$')
    expected_revision: int = Field(ge=1, le=2_147_483_646)

class BulkWrite(StrictModel):
    # Individual validation errors belong to their row, not the whole batch.
    records: list[dict] = Field(min_length=1, max_length=500)

def error(code, status=409):
    return EnterprisePersistenceRequestError(code, code, status=status)


def _refactor_resource_payload(entity_id: str, payload: object) -> dict | None:
    """Validate and project a refactor resource envelope for storage.

    The refactor client deliberately sends one generic envelope for every
    record type.  ``resource`` is the one structured-sync kind with a strict
    Pydantic shape, so bridge it here into the canonical resource metadata
    shape.  Raw bytes, previews and local paths never enter this projection.
    ``None`` means this is a legacy/product resource payload and the normal
    validation path should handle it.
    """
    if not isinstance(payload, dict) or payload.get("schema") != "mentorflow.refactor-sync.v1":
        return None
    from pydantic import ValidationError
    if set(payload) != {"schema", "version", "record_type", "record_id", "values"}:
        raise error("SYNC_PAYLOAD_INVALID", 422)
    if type(payload.get("version")) is not int or payload.get("version") != 1 or payload.get("record_type") != "resource":
        raise error("SYNC_PAYLOAD_INVALID", 422)
    record_id = payload.get("record_id")
    if not isinstance(record_id, str) or not record_id or not re.fullmatch(ID_PATTERN, record_id):
        raise error("SYNC_PAYLOAD_INVALID", 422)
    expected_entity = "refactor.v1:resource:" + hashlib.sha256(record_id.encode("utf-8")).hexdigest()
    if entity_id != expected_entity:
        raise error("SYNC_ENTITY_INVALID", 422)
    values = payload.get("values")
    resource_fields = {"material_id", "filename", "media_type", "size", "sha256",
                       "product_classification"}
    if not isinstance(values, dict) or set(values) not in (resource_fields, resource_fields | {"text"}) or values.get("material_id") != record_id:
        raise error("SYNC_PAYLOAD_INVALID", 422)
    if type(values.get("size")) is not int or values["size"] < 0:
        raise error("SYNC_PAYLOAD_INVALID", 422)
    canonical = {
        "filename": values.get("filename"),
        "media_type": values.get("media_type"),
        "sha256": values.get("sha256"),
        "size_bytes": values.get("size"),
        "product_classification": values.get("product_classification"),
        "text": values.get("text", ""),
        "refactor_record_id": record_id,
    }
    try:
        projected = Resource.model_validate(canonical).model_dump()
    except ValidationError as exc:
        raise error("SYNC_PAYLOAD_INVALID", 422) from exc
    projected.pop("product_record", None)
    return projected


def _validate_course_record(values):
    if not isinstance(values, dict) or set(values) != _REFACTOR_FIELDS['course_record']:
        raise error('SYNC_PAYLOAD_INVALID', 422)
    schema = values['schema']
    match = re.fullmatch(r'mentorflow\.course\.([a-z_]{1,40})\.v[0-9]{1,3}', schema) if isinstance(schema, str) else None
    if (match is None or values['entity'] != match.group(1)
            or not isinstance(values['entity_id'], str) or not re.fullmatch(ID_PATTERN, values['entity_id'])
            or not isinstance(values['plan_key'], str)
            or values['plan_key'] != '' and not re.fullmatch(ID_PATTERN, values['plan_key'])
            or not isinstance(values['payload'], dict)):
        raise error('SYNC_PAYLOAD_INVALID', 422)
    # Count the payload's root object as depth 1; only object/array nesting
    # adds depth. Check iteratively before the existing recursive secret scan.
    pending = [(values['payload'], 1)]
    while pending:
        value, depth = pending.pop()
        if isinstance(value, (dict, list)):
            if depth > 32:
                raise error('SYNC_PAYLOAD_INVALID', 422)
            if isinstance(value, dict) and any(not isinstance(key, str) for key in value):
                raise error('SYNC_PAYLOAD_INVALID', 422)
            pending.extend((child, depth + 1) for child in (value.values() if isinstance(value, dict) else value))
        elif value is not None and type(value) not in (str, int, float, bool):
            raise error('SYNC_PAYLOAD_INVALID', 422)
    try:
        encoded = json.dumps(values['payload'], ensure_ascii=False, allow_nan=False,
                             sort_keys=True, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
        raise error('SYNC_PAYLOAD_INVALID', 422) from exc
    if len(encoded) > 256 * 1024:
        raise error('SYNC_PAYLOAD_TOO_LARGE', 422)


def _refactor_sync_payload(kind: str, entity_id: str, payload: object) -> dict | None:
    """Validate the generic refactor envelope before storing it in the ledger.

    The structured-sync endpoint also serves older product projections.  New
    learning records deliberately use a namespaced generic envelope so their
    local schemas do not need to masquerade as the legacy ``Plan``/``Task``
    models.  Resource metadata is projected to the existing canonical shape;
    the other record types remain envelopes so the desktop adapter can rebuild
    its exact local records on another device.
    """
    if not isinstance(payload, dict) or payload.get('schema') != 'mentorflow.refactor-sync.v1':
        return None
    if set(payload) != {'schema', 'version', 'record_type', 'record_id', 'values'}:
        raise error('SYNC_PAYLOAD_INVALID', 422)
    if type(payload.get('version')) is not int or payload['version'] != 1:
        raise error('SYNC_PAYLOAD_INVALID', 422)
    record_type = payload.get('record_type')
    if record_type not in _REFACTOR_KIND_BY_RECORD or _REFACTOR_KIND_BY_RECORD[record_type] != kind:
        raise error('SYNC_PAYLOAD_INVALID', 422)
    record_id = payload.get('record_id')
    if (not isinstance(record_id, str) or not record_id or len(record_id) > 512
            or any(ord(char) < 32 for char in record_id)):
        raise error('SYNC_PAYLOAD_INVALID', 422)
    expected_entity = 'refactor.v1:' + record_type + ':' + hashlib.sha256(record_id.encode('utf-8')).hexdigest()
    if entity_id != expected_entity:
        raise error('SYNC_ENTITY_INVALID', 422)
    values = payload.get('values')
    if not isinstance(values, dict):
        raise error('SYNC_PAYLOAD_INVALID', 422)
    if record_type == 'course_record':
        _validate_course_record(values)
    # Oversized account history is stored as a separately authenticated blob;
    # the CAS entity keeps only this bounded manifest.
    if record_type != 'course_record' and set(values) == {'blob_ref', 'record_type', 'record_id', 'summary'}:
        ref = values.get('blob_ref')
        expected_manifest_entity = ('refactor.v1:' + record_type + ':'
                                    + hashlib.sha256(str(values.get('record_id', '')).encode('utf-8')).hexdigest())
        if (values.get('record_type') != record_type
                or not isinstance(values.get('record_id'), str)
                or expected_manifest_entity != entity_id
                or not isinstance(values.get('summary'), dict)
                or not isinstance(ref, dict)
                or ref.get('schema') != 'mentorflow.history-blob.v1'
                or not isinstance(ref.get('sha256'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', ref['sha256'])
                or type(ref.get('size')) is not int or ref['size'] < 0
                or ref['size'] > 8 * 1024 * 1024
                or ref.get('content_type') != 'application/json'):
            raise error('SYNC_PAYLOAD_INVALID', 422)
        return {'schema': payload['schema'], 'version': payload['version'],
                'record_type': record_type, 'record_id': values['record_id'],
                'values': values}
    allowed_fields = _REFACTOR_FIELDS[record_type]
    if record_type == 'resource':
        allowed = (allowed_fields, allowed_fields | {'text'})
        if set(values) not in allowed:
            raise error('SYNC_PAYLOAD_INVALID', 422)
    elif record_type in _REFACTOR_FIELD_VARIANTS:
        if set(values) not in _REFACTOR_FIELD_VARIANTS[record_type]:
            raise error('SYNC_PAYLOAD_INVALID', 422)
        if record_type == 'note' and set(values) == {'plan_key', 'task_id', 'text'}:
            values = dict(values, revision=1)
    elif set(values) != allowed_fields:
        raise error('SYNC_PAYLOAD_INVALID', 422)
    if record_type == 'resource':
        return _refactor_resource_payload(entity_id, payload)
    if record_type == 'draft_plan':
        if (not isinstance(values.get('revision'), int) or values['revision'] < 1
                or values.get('status') not in {'NEEDS_INPUT', 'PLAN_READY'}
                or not isinstance(values.get('snapshot'), dict)
                or type(values.get('confirmed')) is not bool):
            raise error('SYNC_PAYLOAD_INVALID', 422)
        confirmation = values['snapshot'].get('draft_confirmation')
        if values['confirmed']:
            required = {'schema', 'draft_id', 'revision', 'confirmed_at', 'confirmed_by', 'idempotency_key'}
            if (not isinstance(confirmation, dict) or set(confirmation) != required
                    or confirmation.get('schema') != 'mentorflow.draft-confirmation.v1'
                    or confirmation.get('draft_id') != values.get('draft_id')
                    or confirmation.get('revision') != values.get('revision')):
                raise error('SYNC_PAYLOAD_INVALID', 422)
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                             sort_keys=True, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, OverflowError) as exc:
        raise error('SYNC_PAYLOAD_INVALID', 422) from exc
    if record_type != 'course_record' and len(encoded) >= 256 * 1024:
        raise error('SYNC_PAYLOAD_TOO_LARGE', 422)
    return {
        'schema': payload['schema'],
        'version': payload['version'],
        'record_type': record_type,
        'record_id': record_id,
        'values': values,
    }

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

    def _payload(self, kind, entity_id, body, *, deleting=False):
        from pydantic import ValidationError
        if not isinstance(kind, str) or kind not in SYNC_KINDS or not isinstance(entity_id, str) or not re.fullmatch(ID_PATTERN, entity_id):
            raise error('SYNC_ENTITY_INVALID', 422)
        payload = {}
        if not deleting:
            try:
                course_record = (body.payload.get('schema') == 'mentorflow.refactor-sync.v1'
                                 and body.payload.get('record_type') == 'course_record')
                if course_record:
                    refactor_payload = _refactor_sync_payload(kind, entity_id, body.payload)
                _reject_secret(body.payload)
                if not course_record:
                    refactor_payload = _refactor_sync_payload(kind, entity_id, body.payload)
                if refactor_payload is not None:
                    payload = refactor_payload
                else:
                    validate_product_record(kind, entity_id, body.payload)
                    if kind == 'progress' and body.payload.get('plan_id') is None and not body.payload.get('product_record'):
                        raise error('SYNC_PAYLOAD_INVALID', 422)
                    model = {'plan': Plan, 'progress': Progress, 'resource': Resource}.get(kind)
                    payload = (model.model_validate(body.payload).model_dump() if model else SYNC_ITEM_REGISTRY[kind].project(body.payload))
                    if payload.get('product_record') is None:
                        payload.pop('product_record', None)
                    if payload.get('refactor_record_id') is None:
                        payload.pop('refactor_record_id', None)
                    if kind == 'resource' and payload.get('text') is None:
                        payload.pop('text', None)
            except ProductSyncRejected as exc:
                raise error(str(exc), 422) from exc
            except ValidationError as exc:
                raise error('SYNC_PAYLOAD_INVALID', 422) from exc
            # Preserve pre-contract request fingerprints when the optional field is absent.
            if kind == 'resource' and refactor_payload is None and 'product_classification' not in body.payload:
                payload.pop('product_classification', None)
            if kind == 'plan' and refactor_payload is None and (len(set(payload['resource_ids'])) != len(payload['resource_ids']) or
                    any(not re.fullmatch(ID_PATTERN, r) for r in payload['resource_ids'])):
                raise error('SYNC_RESOURCE_IDS_INVALID', 422)
        return payload

    def _write_row(self, c, scope, kind, entity_id, body, payload, deleting):
        fingerprint = hashlib.sha256(json.dumps([kind, entity_id, body.expected_revision, deleting, payload],
            sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
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
            if kind == 'progress' and not deleting and payload.get('plan_id'):
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
        return result

    def write(self, actor, tenant, kind, entity_id, body, *, deleting=False):
        payload = self._payload(kind, entity_id, body, deleting=deleting)
        context = TenantTransactionContext(tenant=tenant, actor=actor)
        scope = scope_of(context)
        with self.transactions.transaction(context) as tx:
            self._bind(tx, scope, writing=True)
            result = self._write_row(tx.connection, scope, kind, entity_id, body, payload, deleting)
        return result

    def write_many(self, actor, tenant, body):
        from pydantic import ValidationError
        context = TenantTransactionContext(tenant=tenant, actor=actor)
        scope = scope_of(context)
        results = []
        with self.transactions.transaction(context) as tx:
            self._bind(tx, scope, writing=True)
            for row in body.records:
                identity = {k: row.get(k) for k in ('kind', 'entity_id')}
                try:
                    with tx.connection.begin_nested():
                        if set(row) - {'kind', 'entity_id', 'request_id', 'expected_revision', 'payload', 'deleted'}:
                            raise error('SYNC_RECORD_INVALID', 422)
                        deleting = row.get('deleted', False)
                        if type(deleting) is not bool:
                            raise error('SYNC_RECORD_INVALID', 422)
                        model = Delete if deleting else Write
                        data = {k: v for k, v in row.items() if k not in {'kind', 'entity_id', 'deleted'}}
                        operation = model.model_validate(data)
                        payload = self._payload(identity['kind'], identity['entity_id'], operation, deleting=deleting)
                        result = self._write_row(tx.connection, scope, identity['kind'], identity['entity_id'], operation, payload, deleting)
                    results.append(dict(identity, status=200, result=result))
                except ValidationError:
                    results.append(dict(identity, status=422, code='SYNC_RECORD_INVALID'))
                except EnterprisePersistenceRequestError as exc:
                    results.append(dict(identity, status=exc.status, code=exc.code))
        # No success escapes until the complete outer transaction commits.
        return dict(schema='mentorflow.structured-sync-bulk.v1', results=results)

    def snapshot(self, actor, tenant, *, requested_scope=None, scope_version=None):
        from .sync_snapshot import observe
        context = TenantTransactionContext(tenant=tenant, actor=actor)
        scope = scope_of(context)
        with self.transactions.transaction(context) as tx:
            self._bind(tx, scope)
            result = observe(tx.connection, scope, requested_scope=requested_scope, scope_version=scope_version)
        return result

    def changes(self, actor, tenant, *, since=0, kind='learning_run', limit=100):
        """Latest state of changed entities; soft deletions remain in this feed.

        Allocation is serialized through commit within the scope by migration
        0042's trigger. Sequence gaps/other tenants never advance this cursor.
        A row edited between pages may move forward and be returned again;
        clients apply its per-record revision idempotently.
        """
        if (type(since) is not int or not 0 <= since <= 9_223_372_036_854_775_807
                or kind not in SYNC_KINDS or type(limit) is not int or not 1 <= limit <= 500):
            raise error('SYNC_CHANGES_QUERY_INVALID', 422)
        context = TenantTransactionContext(tenant=tenant, actor=actor)
        scope = scope_of(context)
        with self.transactions.transaction(context) as tx:
            self._bind(tx, scope)
            rows = tx.connection.execute(text('''
                SELECT kind,entity_id,revision,deleted,payload,change_seq
                FROM structured_sync_entities
                WHERE issuer=:issuer AND subject_id=:subject_id
                  AND organization_id=:organization_id AND workspace_id=:workspace_id
                  AND kind=:kind AND change_seq>:since
                ORDER BY change_seq LIMIT :page_size
            '''), dict(scope, kind=kind, since=since, page_size=limit + 1)).mappings().all()
        items = []
        for row in rows[:limit]:
            value = dict(row)
            if isinstance(value['payload'], str):
                value['payload'] = json.loads(value['payload'])
            value['deleted'] = bool(value['deleted'])
            items.append(value)
        return dict(schema='mentorflow.structured-sync-changes.v1', contract_version=1,
                    scope=scope, kind=kind, since=str(since),
                    next_cursor=str(items[-1]['change_seq'] if items else since),
                    has_more=len(rows) > limit, entities=items)
