"""Account-bound product commit adapter; journal is delivery state, not domain truth.

Triggers capture only stable row keys inside existing business transactions.
Known domain projections are frozen before the first network attempt. ACKs own
server revisions. Rehydration writes the existing product stores, never files.
"""
from __future__ import annotations
import json
import sqlite3
import threading
import re
from pathlib import Path
from contextlib import contextmanager
from .product_sync_contract import RECORDS, project, validate, record_id, ProductSyncRejected


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


class ProductSyncFailure(RuntimeError):
    def __init__(self, code='PRODUCT_SYNC_PENDING'):
        self.code = code
        super().__init__(code)


class ProductSyncProducer:
    def __init__(self, database, scope, send, *, store='orm'):
        self.database = Path(database)
        if store not in {'orm','legacy'}:
            raise ProductSyncFailure('PRODUCT_SYNC_STORE_REQUIRED')
        self.scope = dict(scope)
        if set(scope) != {'issuer','subject_id','organization_id','workspace_id'} or any(not v for v in scope.values()):
            raise ProductSyncFailure('PRODUCT_SYNC_SCOPE_REQUIRED')
        self.send = send
        self.lock = threading.RLock()
        self.records = {key: spec for key, spec in RECORDS.items() if spec.get('store', 'orm') == store}
        self.store = store

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.database, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            with c:
                yield c
        finally:
            c.close()

    def install(self):
        with self.connect() as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            old = (c.execute('SELECT scope FROM product_sync_binding WHERE singleton=1').fetchone()
                   if 'product_sync_binding' in tables else None)
            if old and old[0] != encoded(self.scope):
                raise ProductSyncFailure('PRODUCT_SYNC_SCOPE_MISMATCH')
            if not old and any(table in tables and c.execute('SELECT 1 FROM "'+table+'" LIMIT 1').fetchone()
                               for table in (spec.get('table', key) for key, spec in self.records.items())):
                raise ProductSyncFailure('PRODUCT_SYNC_EXISTING_DATA_REQUIRES_MIGRATION')
            if self.store == 'orm':
                from .product_sync_extensions import install_aggregate_tables
                install_aggregate_tables(c)
                tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            c.executescript("""
            CREATE TABLE IF NOT EXISTS product_sync_binding (singleton INTEGER PRIMARY KEY CHECK(singleton=1), scope TEXT NOT NULL, hydrating INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS product_sync_intents (seq INTEGER PRIMARY KEY AUTOINCREMENT, record_type TEXT NOT NULL, row_key TEXT NOT NULL, operation TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE, payload TEXT, entity_id TEXT, base_revision INTEGER, status TEXT NOT NULL DEFAULT 'PENDING', error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS product_sync_revisions (record_type TEXT NOT NULL, row_key TEXT NOT NULL, entity_id TEXT NOT NULL, revision INTEGER NOT NULL, deleted INTEGER NOT NULL, PRIMARY KEY(record_type,row_key));
            CREATE TABLE IF NOT EXISTS product_sync_commands (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, status TEXT NOT NULL, response TEXT, status_code INTEGER);
            """)
            c.execute('INSERT OR IGNORE INTO product_sync_binding(singleton,scope) VALUES(1,?)', (encoded(self.scope),))
            for record, spec in self.records.items():
                table = spec.get('table', record)
                if table not in tables:
                    raise ProductSyncFailure('PRODUCT_SYNC_SCHEMA_MISSING:' + table)
                cols = {r[1] for r in c.execute('PRAGMA table_info("'+table+'")')}
                if not set(spec['columns']).issubset(cols):
                    raise ProductSyncFailure('PRODUCT_SYNC_SCHEMA_DRIFT:' + table)
                for operation, ref in [('INSERT','NEW'),('UPDATE','NEW'),('DELETE','OLD')]:
                    keys = ','.join(ref+'."'+key+'"' for key in spec['keys'])
                    name = 'product_sync_' + table + '_' + operation.lower()
                    c.execute(f"""CREATE TRIGGER IF NOT EXISTS "{name}" AFTER {operation} ON "{table}"
                        WHEN (SELECT hydrating FROM product_sync_binding WHERE singleton=1)=0
                        BEGIN INSERT INTO product_sync_intents(record_type,row_key,operation,request_id)
                        VALUES('{record}',json_array({keys}),'{operation}',lower(hex(randomblob(16)))); END""")

    def _scope_check(self, c):
        r = c.execute('SELECT scope FROM product_sync_binding WHERE singleton=1').fetchone()
        if not r or r[0] != encoded(self.scope):
            raise ProductSyncFailure('PRODUCT_SYNC_SCOPE_MISMATCH')

    def start(self):
        self.install()
        self.flush()
        self.restore(self.send({'operation':'snapshot'}))

    def authorize(self):
        try:
            result = self.send({'operation': 'authorize'})
            if result != {'authorized': True}:
                raise ProductSyncFailure('PRODUCT_SYNC_DENIED')
        except Exception as exc:
            code = 'PRODUCT_SYNC_DENIED' if getattr(exc,'code',None) == 'PRODUCT_SYNC_DENIED' else 'PRODUCT_SYNC_PENDING'
            raise ProductSyncFailure(code) from None

    def status(self):
        with self.connect() as c:
            self._scope_check(c)
            pending = c.execute("SELECT COUNT(*) FROM product_sync_intents WHERE status!='ACKED'").fetchone()[0]
            error = c.execute("SELECT error FROM product_sync_intents WHERE status!='ACKED' ORDER BY seq LIMIT 1").fetchone()
            interrupted = c.execute("SELECT COUNT(*) FROM product_sync_commands WHERE status='RUNNING'").fetchone()[0]
            return {'scope': 'SUPPORTED_PRODUCT_RECORDS', 'pending': pending, 'interrupted_commands':interrupted,
                    'status': (error[0] or 'PRODUCT_SYNC_PENDING') if error else
                    ('PRODUCT_SYNC_COMMAND_INTERRUPTED' if interrupted else 'SERVER_ACKNOWLEDGED')}

    def flush(self):
        with self.lock:
            while True:
                with self.connect() as c:
                    self._scope_check(c)
                    row = c.execute("SELECT * FROM product_sync_intents WHERE status!='ACKED' ORDER BY seq LIMIT 1").fetchone()
                    if row is None:
                        return
                    intent = dict(row)
                    table, key = intent['record_type'], intent['row_key']
                    spec = RECORDS[table]
                    rev = c.execute('SELECT * FROM product_sync_revisions WHERE record_type=? AND row_key=?',(table,key)).fetchone()
                    if intent['payload'] is None:
                        where = ' AND '.join('"'+k+'"=?' for k in spec['keys'])
                        domain = c.execute('SELECT * FROM "'+spec.get('table', table)+'" WHERE '+where, json.loads(key)).fetchone()
                        deleting = domain is None
                        if deleting and not rev:
                            c.execute("UPDATE product_sync_intents SET status='ACKED' WHERE seq=?",(intent['seq'],))
                            continue
                        try:
                            payload = {} if deleting else project(table, dict(domain))
                        except (ValueError, TypeError, KeyError, OverflowError):
                            c.execute('UPDATE product_sync_intents SET error=? WHERE seq=?',
                                      ('PRODUCT_SYNC_INVALID_PROJECTION', intent['seq']))
                            c.commit()
                            raise ProductSyncFailure('PRODUCT_SYNC_INVALID_PROJECTION') from None
                        entity = rev['entity_id'] if deleting else record_id(table, dict(domain))
                        base = rev['revision'] if rev else 0
                        intent.update(payload=encoded(payload),entity_id=entity,base_revision=base,operation='DELETE' if deleting else 'WRITE')
                        c.execute('UPDATE product_sync_intents SET payload=?,entity_id=?,base_revision=?,operation=? WHERE seq=?',
                                  (intent['payload'],entity,base,intent['operation'],intent['seq']))
                message = dict(operation=intent['operation'].lower(),kind=spec['kind'],entity_id=intent['entity_id'],
                               request_id=intent['request_id'],expected_revision=intent['base_revision'],payload=json.loads(intent['payload']))
                try:
                    result = self.send(message)
                    if (result.get('kind') != spec['kind'] or result.get('entity_id') != intent['entity_id']
                        or result.get('revision') != intent['base_revision']+1
                        or result.get('deleted') is not (intent['operation']=='DELETE')
                        or result.get('payload') != message['payload']):
                        raise ProductSyncFailure('PRODUCT_SYNC_INVALID_ACK')
                    with self.connect() as c:
                        self._scope_check(c)
                        c.execute('INSERT INTO product_sync_revisions VALUES(?,?,?,?,?) ON CONFLICT(record_type,row_key) DO UPDATE SET entity_id=excluded.entity_id,revision=excluded.revision,deleted=excluded.deleted',
                                  (table,key,intent['entity_id'],result['revision'],int(result['deleted'])))
                        c.execute("UPDATE product_sync_intents SET status='ACKED',error='' WHERE seq=?",(intent['seq'],))
                except Exception as exc:
                    code = getattr(exc,'code','PRODUCT_SYNC_PENDING')
                    if code not in {'PRODUCT_SYNC_CONFLICT','PRODUCT_SYNC_DENIED','PRODUCT_SYNC_INVALID_ACK','PRODUCT_SYNC_SCOPE_MISMATCH','PRODUCT_SYNC_CONTRACT_REJECTED'}:
                        code = 'PRODUCT_SYNC_PENDING'
                    with self.connect() as c:
                        c.execute('UPDATE product_sync_intents SET error=? WHERE seq=?',(code,intent['seq']))
                    raise ProductSyncFailure(code) from None

    def check_snapshot(self, snapshot):
        if snapshot.get('complete') is not True or snapshot.get('snapshot_scope') != self.scope:
            raise ProductSyncFailure('PRODUCT_SYNC_SCOPE_MISMATCH')
        if (snapshot.get('schema') != 'mentorflow.structured-sync.v1'
            or snapshot.get('snapshot_authority') != 'POSTGRESQL_SINGLE_STATEMENT_MVCC'
            or not re.fullmatch(r'pg-snapshot:\d+:\d+:(?:\d+(?:,\d+)*)?', snapshot.get('snapshot_revision',''))):
            raise ProductSyncFailure('PRODUCT_SYNC_SNAPSHOT_REQUIRED')
        items = snapshot.get('entities')
        if not isinstance(items,list):
            raise ProductSyncFailure('PRODUCT_SYNC_SNAPSHOT_REQUIRED')
        seen = set()
        for item in items:
            identity = (item.get('kind'), item.get('entity_id'))
            if (identity in seen or type(item.get('revision')) is not int or item['revision'] < 1
                or type(item.get('deleted')) is not bool or not isinstance(item.get('payload'),dict)):
                raise ProductSyncFailure('PRODUCT_SYNC_INVALID_SNAPSHOT')
            seen.add(identity)
            validate(item['kind'], item['entity_id'], item['payload'])
        return items, seen

    def restore(self, snapshot):
        items, seen = self.check_snapshot(snapshot)
        with self.lock, self.connect() as c:
            self._scope_check(c)
            if c.execute("SELECT 1 FROM product_sync_intents WHERE status!='ACKED'").fetchone():
                raise ProductSyncFailure('PRODUCT_SYNC_PENDING')
            known = c.execute('SELECT record_type,entity_id FROM product_sync_revisions').fetchall()
            if any((RECORDS[row['record_type']]['kind'],row['entity_id']) not in seen for row in known):
                raise ProductSyncFailure('PRODUCT_SYNC_INCOMPLETE_SNAPSHOT')
            c.execute('PRAGMA defer_foreign_keys=ON')
            c.execute('UPDATE product_sync_binding SET hydrating=1 WHERE singleton=1')
            for item in items:
                if item['deleted']:
                    candidates = c.execute('SELECT record_type,row_key,revision FROM product_sync_revisions WHERE entity_id=?',(item['entity_id'],)).fetchall()
                    prior = next((r for r in candidates if RECORDS[r['record_type']]['kind']==item['kind']),None)
                    if prior:
                        if prior['revision'] > item['revision']:
                            raise ProductSyncFailure('PRODUCT_SYNC_STALE_SNAPSHOT')
                        spec = RECORDS[prior['record_type']]
                        where = ' AND '.join('"'+k+'"=?' for k in spec['keys'])
                        c.execute('DELETE FROM "'+spec.get('table', prior['record_type'])+'" WHERE '+where,json.loads(prior['row_key']))
                        c.execute('UPDATE product_sync_revisions SET revision=?,deleted=1 WHERE record_type=? AND row_key=?',
                                  (item['revision'],prior['record_type'],prior['row_key']))
                    continue
                payload = item['payload']
                if 'product_record' not in payload:
                    continue  # Existing non-product protocol items keep their existing consumers.
                validate(item['kind'],item['entity_id'],payload)
                record = payload['product_record']; table=record['record_type'];spec=RECORDS[table]
                if table not in self.records:
                    continue
                values = record['values']; key=encoded([values[k] for k in spec['keys']])
                prior = c.execute('SELECT revision FROM product_sync_revisions WHERE record_type=? AND row_key=?',(table,key)).fetchone()
                if prior and prior[0] > item['revision']:
                    raise ProductSyncFailure('PRODUCT_SYNC_STALE_SNAPSHOT')
                columns=list(values); inserting={**spec['local_defaults'],**values}
                names=','.join('"'+k+'"' for k in inserting);params=','.join('?' for _ in inserting)
                keys=','.join('"'+k+'"' for k in spec['keys'])
                updates=','.join('"'+k+'"=excluded."'+k+'"' for k in columns if k not in spec['keys'])
                local_table = spec.get('table', table)
                c.execute(f'INSERT INTO "{local_table}" ({names}) VALUES({params}) ON CONFLICT({keys}) DO UPDATE SET {updates}',tuple(inserting.values()))
                c.execute('INSERT INTO product_sync_revisions VALUES(?,?,?,?,0) ON CONFLICT(record_type,row_key) DO UPDATE SET entity_id=excluded.entity_id,revision=excluded.revision,deleted=0',(table,key,item['entity_id'],item['revision']))
            c.execute('UPDATE product_sync_binding SET hydrating=0 WHERE singleton=1')

    def begin_command(self, command_id, fingerprint):
        with self.connect() as c:
            self._scope_check(c)
            old = c.execute('SELECT * FROM product_sync_commands WHERE id=?', (command_id,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise ProductSyncFailure('PRODUCT_SYNC_COMMAND_REUSED')
                if old['status'] != 'COMPLETED':
                    # Never rerun an ambiguous partially executed business command.
                    raise ProductSyncFailure('PRODUCT_SYNC_COMMAND_INTERRUPTED')
                return dict(old)
            c.execute("INSERT INTO product_sync_commands(id,fingerprint,status) VALUES(?,?,'RUNNING')", (command_id,fingerprint))
        return None

    def retry_conflict(self, request_id, expected_server_revision):
        """Explicit keep-local resolution, fenced against a newly changed server."""
        from uuid import uuid4
        snapshot = self.send({'operation':'snapshot'})
        items, _ = self.check_snapshot(snapshot)
        with self.lock, self.connect() as c:
            self._scope_check(c)
            intent=c.execute("SELECT * FROM product_sync_intents WHERE request_id=? AND status!='ACKED'",(request_id,)).fetchone()
            if not intent or intent['error']!='PRODUCT_SYNC_CONFLICT':
                raise ProductSyncFailure('PRODUCT_SYNC_CONFLICT_REQUIRED')
            kind=RECORDS[intent['record_type']]['kind']
            remote=next((v for v in items if (v['kind'],v['entity_id'])==(kind,intent['entity_id'])),None)
            if not remote or remote['deleted'] or type(expected_server_revision) is not int or remote['revision']!=expected_server_revision:
                raise ProductSyncFailure('PRODUCT_SYNC_CONFLICT')
            c.execute("UPDATE product_sync_intents SET request_id=?,base_revision=?,error='' WHERE seq=?",
                (uuid4().hex,remote['revision'],intent['seq']))
        self.flush()

    def conflicts(self):
        with self.connect() as c:
            self._scope_check(c)
            return [dict(r) for r in c.execute("SELECT request_id,record_type,entity_id,base_revision,error FROM product_sync_intents WHERE status!='ACKED' AND error='PRODUCT_SYNC_CONFLICT'")]

    def complete_command(self, command_id, response, status_code, states):
        with self.lock, self.connect() as c:
            self._scope_check(c)
            for table, key, state in states:
                if table not in {'product_plan_state', 'product_review_state', 'product_account_settings'}:
                    raise ProductSyncFailure('PRODUCT_SYNC_CONTRACT_REJECTED')
                data = encoded(state)
                # Validate before commit so an oversized state cannot silently
                # become a durable but undeliverable business result.
                try:
                    project(table, {'id':key, 'state':data})
                except ProductSyncRejected:
                    raise ProductSyncFailure('PRODUCT_SYNC_INVALID_PROJECTION') from None
                c.execute(f'INSERT INTO "{table}" VALUES(?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state WHERE state!=excluded.state', (key,data))
            if command_id:
                c.execute("UPDATE product_sync_commands SET status='COMPLETED',response=?,status_code=? WHERE id=?",
                          (response,status_code,command_id))

    def runtime_states(self, table):
        if table not in {'product_plan_state','product_review_state','product_account_settings'}:
            raise ProductSyncFailure('PRODUCT_SYNC_CONTRACT_REJECTED')
        with self.connect() as c:
            self._scope_check(c)
            return {r['id']: json.loads(r['state']) for r in c.execute(f'SELECT * FROM "{table}"')}


class ProductSyncStores:
    """One producer contract across the two existing compatibility stores."""
    def __init__(self, database, legacy_database, scope, send):
        self.primary = ProductSyncProducer(database, scope, send)
        self.legacy = ProductSyncProducer(legacy_database, scope, send, store='legacy')
        self.scope = self.primary.scope

    def __getattr__(self, name):
        return getattr(self.primary, name)

    def start(self):
        self.primary.install()
        self.legacy.install()
        self.flush()
        self.restore(self.primary.send({'operation':'snapshot'}))

    def flush(self):
        self.primary.flush()
        self.legacy.flush()

    def restore(self, snapshot):
        self.primary.restore(snapshot)
        self.legacy.restore(snapshot)

    def status(self):
        states = [p.status() for p in (self.primary,self.legacy)]
        return dict(scope='SUPPORTED_PRODUCT_RECORDS', pending=sum(s['pending'] for s in states),
                    interrupted_commands=sum(s['interrupted_commands'] for s in states),
                    status=next((s['status'] for s in states if s['status']!='SERVER_ACKNOWLEDGED'), 'SERVER_ACKNOWLEDGED'))

    def conflicts(self):
        return self.primary.conflicts()+self.legacy.conflicts()

    def retry_conflict(self, request_id, expected_server_revision):
        for producer in (self.primary,self.legacy):
            if any(row['request_id']==request_id for row in producer.conflicts()):
                return producer.retry_conflict(request_id,expected_server_revision)
        raise ProductSyncFailure('PRODUCT_SYNC_CONFLICT_REQUIRED')
