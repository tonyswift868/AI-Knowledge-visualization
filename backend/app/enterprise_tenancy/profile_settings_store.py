"""Durable local Profile/Settings store and transactional sync-intent port."""
from __future__ import annotations
import hashlib, json, uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from sqlalchemy import Boolean, Column, Integer, JSON, MetaData, String, Table, create_engine, select, update, func

from .profile_settings_sync import validate, NONSECRET_SETTINGS_ALLOWLIST, ProfileSettingsRejected

metadata = MetaData()
profiles = Table('local_profiles', metadata, Column('account_id', String(160), primary_key=True),
                 Column('organization_id', String(160), primary_key=True), Column('workspace_id', String(160), primary_key=True),
                 Column('revision', Integer, nullable=False), Column('values', JSON, nullable=False))
settings = Table('local_settings', metadata, Column('account_id', String(160), primary_key=True),
                 Column('organization_id', String(160), primary_key=True), Column('workspace_id', String(160), primary_key=True),
                 Column('key', String(80), primary_key=True), Column('value', JSON, nullable=True), Column('revision', Integer, nullable=False))
outbox = Table('structured_sync_outbox', metadata, Column('request_id', String(128), primary_key=True),
    Column('account_id', String(160), nullable=False), Column('organization_id', String(160), nullable=False),
    Column('workspace_id', String(160), nullable=False), Column('kind', String(40), nullable=False),
    Column('entity_id', String(160), nullable=False), Column('operation', String(24), nullable=False),
    Column('expected_revision', Integer, nullable=False), Column('payload', JSON, nullable=False),
    Column('fingerprint', String(64), nullable=False), Column('status', String(16), nullable=False),
    Column('attempt_count', Integer, nullable=False), Column('created_at', String(40), nullable=False), Column('updated_at', String(40), nullable=False))

SCOPE_FIELDS = ('account_id', 'organization_id', 'workspace_id')

class LocalProfileSettingsStore:
    def __init__(self, path: str | Path = ':memory:', *, scope: Mapping[str, str] | None = None):
        url = 'sqlite+pysqlite:///:memory:' if str(path) == ':memory:' else 'sqlite+pysqlite:///' + str(Path(path))
        self.engine = create_engine(url, future=True)
        metadata.create_all(self.engine)
        self.bound_scope = dict(scope) if scope is not None else None

    @contextmanager
    def transaction(self):
        with self.engine.begin() as connection:
            yield connection

    def _scope(self, account_id, organization_id, workspace_id):
        scope = dict(account_id=account_id, organization_id=organization_id, workspace_id=workspace_id)
        if any(not isinstance(v, str) or not v for v in scope.values()):
            raise ProfileSettingsRejected('scope_required')
        if self.bound_scope is not None and scope != self.bound_scope:
            raise ProfileSettingsRejected('wrong_scope')
        return scope

    def read_profile(self, *, account_id, organization_id, workspace_id):
        scope = self._scope(account_id, organization_id, workspace_id)
        with self.engine.connect() as c:
            row = c.execute(select(profiles).filter_by(**scope)).mappings().first()
        return dict(row['values']) if row else {}

    def read_settings(self, *, account_id, organization_id, workspace_id):
        scope = self._scope(account_id, organization_id, workspace_id)
        with self.engine.connect() as c:
            rows = c.execute(select(settings.c.key, settings.c.value).filter_by(**scope)).all()
        return {key: value for key, value in rows}

    def mutate(self, *, account_id, organization_id, workspace_id, profile=None, setting_updates=None, unset=(), request_id=None, emit_outbox=True, server_revision=None):
        scope = self._scope(account_id, organization_id, workspace_id)
        profile = dict(profile or {})
        profile = validate(profile)
        updates = validate(dict(setting_updates or {}))
        unset = tuple(unset)
        if any(key not in NONSECRET_SETTINGS_ALLOWLIST for key in unset):
            raise ProfileSettingsRejected('unknown_or_sensitive_field')
        request_id = request_id or 'local-' + uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        def work(c):
            old = c.execute(select(profiles).filter_by(**scope)).mappings().first()
            revision = int(old['revision']) if old else 0
            new_revision = int(server_revision) if server_revision is not None else revision + 1
            merged = validate({**(old['values'] if old else {}), **profile})
            c.execute(profiles.delete().filter_by(**scope))
            c.execute(profiles.insert().values(**scope, revision=new_revision, values=merged))
            existing = {key: value for key, value in c.execute(
                select(settings.c.key, settings.c.value).filter_by(**scope)).all()}
            for key in unset: existing.pop(key, None)
            existing.update(updates)
            c.execute(settings.delete().filter_by(**scope))
            for key, value in existing.items():
                c.execute(settings.insert().values(**scope, key=key, value=value, revision=new_revision))
            if request_id:
                for kind, entity, payload in (('learner_profile', account_id, merged), ('setting', account_id, existing)):
                    digest = hashlib.sha256(json.dumps([kind, entity, new_revision, payload], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                    existing_intent = c.execute(select(outbox).where(outbox.c.request_id == request_id + ':' + kind)).mappings().first()
                    if existing_intent and existing_intent['fingerprint'] != digest:
                        raise ProfileSettingsRejected('request_id_payload_conflict')
            if not emit_outbox:
                return new_revision
            for kind, entity, payload in (('learner_profile', account_id, merged), ('setting', account_id, existing)):
                digest = hashlib.sha256(json.dumps([kind, entity, new_revision, payload], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                c.execute(outbox.insert().prefix_with('OR IGNORE').values(request_id=request_id + ':' + kind, **scope, kind=kind, entity_id=entity,
                    operation='upsert', expected_revision=revision, payload=payload, fingerprint=digest, status='PENDING',
                    attempt_count=0, created_at=now, updated_at=now))
            return new_revision
        active = getattr(self, '_active_connection', None)
        return work(active) if active is not None else self._run_transaction(work)

    def _run_transaction(self, work):
        with self.transaction() as c:
            return work(c)

    def export_snapshot(self, *, account_id, organization_id, workspace_id):
        scope = self._scope(account_id, organization_id, workspace_id)
        with self.engine.connect() as c:
            pr = c.execute(select(profiles.c.revision).filter_by(**scope)).scalar_one_or_none() or 0
            sr = c.execute(select(func.max(settings.c.revision)).filter_by(**scope)).scalar_one_or_none() or 0
        result=[]
        if pr: result.append({'kind': 'learner_profile', 'entity_id': account_id, 'revision': pr, 'deleted': False, 'payload': self.read_profile(**scope)})
        if sr: result.append({'kind': 'setting', 'entity_id': account_id, 'revision': sr, 'deleted': False, 'payload': self.read_settings(**scope)})
        return result

    def apply_snapshot(self, items, *, account_id, organization_id, workspace_id):
        """Apply a local snapshot through the shared fail-closed dispatcher."""
        from .rehydration_dispatcher import DispatchContext, RehydrationDispatcher
        context = DispatchContext('local', account_id, organization_id, workspace_id, 'local-snapshot')
        wrapped = {'complete': True, 'snapshot_revision': context.snapshot_revision,
                   'snapshot_scope': {'issuer': 'local', 'subject_id': account_id,
                                      'organization_id': organization_id, 'workspace_id': workspace_id},
                   'entities': list(items)}
        adapter = type('Adapter', (), {'apply': lambda _, item, *, scope, store: store.apply_item(item, scope={
            'account_id': scope['subject_id'], 'organization_id': scope['organization_id'], 'workspace_id': scope['workspace_id']})})()
        with self.transaction() as c:
            self._active_connection = c
            try: return RehydrationDispatcher({'learner_profile': adapter, 'setting': adapter}).apply(wrapped, context=context, store=self)
            finally: self._active_connection = None

    def apply_item(self, item, *, scope):
        if set(scope) != set(SCOPE_FIELDS) or any(not isinstance(scope[k], str) or not scope[k] for k in SCOPE_FIELDS):
            raise ProfileSettingsRejected('wrong_scope')
        payload = validate(item.get('payload', {}))
        if type(item.get('revision')) is not int or item['revision'] <= 0:
            raise ProfileSettingsRejected('revision_required')
        with self.engine.connect() as c:
            if item['kind'] == 'learner_profile':
                current = c.execute(select(profiles.c.revision).filter_by(**scope)).scalar_one_or_none() or 0
                current_payload = c.execute(select(profiles.c['values']).filter_by(**scope)).scalar_one_or_none() or {}
            else:
                current = c.execute(select(func.max(settings.c.revision)).filter_by(**scope)).scalar_one_or_none() or 0
                current_payload = {key: value for key, value in c.execute(select(settings.c.key, settings.c.value).filter_by(**scope)).all()}
        if item['revision'] < current:
            if current_payload == payload:
                return 'DUPLICATE_IGNORED'
            raise ProfileSettingsRejected('stale_revision')
        if item['revision'] == current and current > 0 and current_payload == payload:
            return 'DUPLICATE_IGNORED'
        if item['revision'] == current and current > 0:
            raise ProfileSettingsRejected('stale_revision')
        if item.get('deleted') is True:
            with self.transaction() as c:
                if item['kind'] == 'learner_profile': c.execute(profiles.delete().filter_by(**scope))
                else: c.execute(settings.delete().filter_by(**scope))
            return 'DELETED'
        existing_keys = tuple(current_payload) if item['kind'] == 'setting' else ()
        if item['kind'] == 'setting':
            conn = getattr(self, '_active_connection', None)
            if conn is None:
                with self.transaction() as conn:
                    conn.execute(settings.delete().filter_by(**scope))
                    for key, value in payload.items():
                        conn.execute(settings.insert().values(**scope, key=key, value=value, revision=item['revision']))
            else:
                conn.execute(settings.delete().filter_by(**scope))
                for key, value in payload.items():
                    conn.execute(settings.insert().values(**scope, key=key, value=value, revision=item['revision']))
            return 'APPLIED'
        self.mutate(account_id=scope['account_id'], organization_id=scope['organization_id'], workspace_id=scope['workspace_id'],
                    profile=payload if item['kind'] == 'learner_profile' else None,
                    setting_updates=payload if item['kind'] == 'setting' else None,
                    unset=existing_keys, request_id='rehydrate-' + item['kind'] + '-' + item['entity_id'], emit_outbox=False,
                    server_revision=item['revision'])
        return 'APPLIED'

    def pending(self):
        with self.engine.connect() as c:
            return tuple(dict(row) for row in c.execute(select(outbox).where(outbox.c.status == 'PENDING').order_by(outbox.c.created_at)).mappings())

    def ack(self, request_id):
        with self.transaction() as c:
            result = c.execute(update(outbox).where(outbox.c.request_id == request_id, outbox.c.status == 'PENDING').values(status='ACKED')).rowcount
        return bool(result)

    def retry(self, request_id):
        with self.transaction() as c:
            result = c.execute(update(outbox).where(outbox.c.request_id == request_id, outbox.c.status == 'PENDING').values(attempt_count=outbox.c.attempt_count + 1)).rowcount
        return bool(result)

    def relay_pending(self, send):
        """Deliver pending intents, ACKing only after the server accepts them."""
        delivered = 0
        for intent in self.pending():
            try:
                send(intent)
            except Exception:
                self.retry(intent['request_id'])
                continue
            self.ack(intent['request_id'])
            delivered += 1
        return delivered
