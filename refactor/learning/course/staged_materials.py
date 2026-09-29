"""In-memory material overlay for PLAN-10 previews.

Only confirmed changes are copied into the account store. A cancelled analysis,
failed model call or process exit drops this object and its original bytes.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, is_dataclass
import uuid

from .store import StoreError


class StagedMaterials:
    def __init__(self, store, plan_id):
        self.store, self.plan_id = store, plan_id
        self.account_id, self.root = store.account_id, store.root
        self._sources = {}
        self._chunks = {}
        self._originals = {}

    def get_plan(self, plan_id):
        return self.store.get_plan(plan_id)

    def studied_content(self, plan_id):
        return self.store.studied_content(plan_id)

    def load_ledger(self, plan_id):
        return self.store.load_ledger(plan_id)

    @contextmanager
    def transaction(self):
        before = (deepcopy(self._sources), deepcopy(self._chunks), dict(self._originals))
        try:
            yield self
        except BaseException:
            self._sources, self._chunks, self._originals = before
            raise

    def records(self, entity, plan_id=''):
        rows = self.store.records(entity, plan_id)
        if plan_id != self.plan_id or entity not in ('sources', 'source_chunks'):
            return rows
        overlay = self._sources if entity == 'sources' else self._chunks
        merged = {row['id']: row for row in rows}
        merged.update(deepcopy(overlay))
        return [merged[key] for key in sorted(merged)]

    def add_sources(self, plan_id, sources):
        self.get_plan(plan_id)
        if plan_id != self.plan_id: raise StoreError('INVALID_PLAN')
        ids = []
        for source in sources:
            source = dict(source)
            chunks = source.pop('chunks', [])
            identifier = source.pop('id', None) or uuid.uuid4().hex
            payload = dict(id=identifier, kind='url', url=None, sha256=None, title='', site='', tier='',
                           credibility=None, adopted=False)
            payload.update(source)
            self._sources[identifier] = deepcopy(payload)
            for position, chunk in enumerate(chunks):
                data = asdict(chunk) if is_dataclass(chunk) else dict(chunk)
                chunk_id = data.pop('chunk_id', None) or data.pop('id', None)
                if not chunk_id: raise StoreError('INVALID_ID')
                item = dict(id=chunk_id, source_id=identifier, position=position, text='',
                            page_start=None, page_end=None, section=[])
                item.update(data)
                existing = next((row for row in self.records('source_chunks', plan_id)
                                 if row['id'] == chunk_id), None)
                if existing and existing['source_id'] != identifier: raise StoreError('CHUNK_CONFLICT')
                self._chunks[chunk_id] = deepcopy(item)
            ids.append(identifier)
        return ids

    def set_source_adopted(self, plan_id, source_id, adopted):
        source = next((row for row in self.records('sources', plan_id) if row['id'] == source_id), None)
        if source is None: raise StoreError('NOT_FOUND')
        self._sources[source_id] = dict(source, adopted=bool(adopted))

    def put_original(self, data, digest):
        self._originals[digest] = bytes(data)

    def get_original(self, digest):
        return self._originals.get(digest) or self.store.get_original(digest)

    def commit_into(self, store):
        """Caller holds the account transaction, including the route update."""
        for digest, data in self._originals.items():
            store.put_original(data, digest)
        chunks = list(self._chunks.values())
        for source in self._sources.values():
            attached = [dict(row) for row in chunks if row['source_id'] == source['id']]
            store.add_sources(self.plan_id, [dict(source, chunks=attached)])
