"""Explicit owner-approved intake; never binds or edits the legacy source.

Uses existing typed projections, stable IDs, durable producer journal and CAS.
Caller supplies an authenticated ProductSyncRelay and an EMPTY initialized
product database. This module has no credentials, login or deployment capability.
"""
import hashlib
import json
import sqlite3
from pathlib import Path
from .product_sync import ProductSyncProducer, ProductSyncFailure, encoded
from .product_sync_contract import RECORDS, project, record_id


def sha256_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1048576),b''): h.update(block)
    return h.hexdigest()


def prepare(snapshot, *, expected_sha256, owner_sha256):
    """Read an immutable backup; retain unidentified rows outside intake."""
    if sha256_file(snapshot)!=expected_sha256: raise ValueError('SOURCE_DIGEST_MISMATCH')
    c=sqlite3.connect(Path(snapshot).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    c.row_factory=sqlite3.Row
    try:
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA trusted_schema=OFF')
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        workspace_ids={r[0] for r in c.execute('SELECT id FROM orm_workspaces')}
        owned={r[0] for r in c.execute('SELECT workspace_id,user_id FROM orm_workspace_ownership')
               if hashlib.sha256(str(r[1]).encode()).hexdigest()==owner_sha256 and r[0] in workspace_ids}
        if not owned: raise ValueError('NO_PROVEN_OWNER_WORKSPACES')
        rows={t:[dict(r) for r in c.execute('SELECT * FROM "'+t+'"')] for t,s in RECORDS.items()
              if s.get('store','orm')=='orm' and t in tables}
        fks={t:[(r[3],r[2],r[4]) for r in c.execute('PRAGMA foreign_key_list("'+t+'")')] for t in rows}
        accepted={t:{} for t in rows}
        def key(t,r):return encoded([r[k] for k in RECORDS[t]['keys']])
        # Ownership roots are explicit. Children without workspace derive scope
        # only from actual declared FKs, never from username or row presence.
        changed=True
        while changed:
            changed=False
            for t,items in rows.items():
                for r in items:
                    k=key(t,r)
                    if k in accepted[t]:continue
                    if t=='orm_workspaces': ok=r['id'] in owned
                    elif 'workspace_id' in r: ok=r['workspace_id'] in owned
                    else:
                        refs=[(field,parent,col) for field,parent,col in fks[t] if r[field] is not None]
                        ok=bool(refs) and all(parent in accepted and any(p[col]==r[field] for p in accepted[parent].values()) for field,parent,col in refs)
                    if ok:accepted[t][k]=r;changed=True
        # Any declared dependency outside the admitted group prevents that row
        # (and its dependents) from silently crossing the ownership boundary.
        changed=True
        while changed:
            changed=False
            for t,items in accepted.items():
                for k,r in list(items.items()):
                    if any(r[field] is not None and (parent not in accepted or not any(p[col]==r[field] for p in accepted[parent].values())) for field,parent,col in fks[t]):
                        del items[k];changed=True
        records=[]
        for t,items in accepted.items():
            for k,r in items.items():
                payload=project(t,r)
                records.append(dict(record_type=t,row_key=k,kind=RECORDS[t]['kind'],entity_id=record_id(t,r),payload=payload))
        records.sort(key=lambda r:(r['kind']!='resource',r['record_type'],r['row_key']))
        return dict(source_sha256=expected_sha256,owner_sha256=owner_sha256,records=records,
                    admitted={t:len(x) for t,x in accepted.items()},
                    held={t:len(rows[t])-len(x) for t,x in accepted.items() if len(rows[t])!=len(x)})
    finally:c.close()


def execute(prepared, database, relay, *, expected_scope):
    """Retry with SAME disposable journal DB; only final verification means done.

    A pre-write server backup is an external coordinator prerequisite. Snapshot
    and identity authorization happen before queue creation; collision fails
    closed. Source files are never opened for writing by this entry point.
    """
    if relay.scope != expected_scope:
        raise ValueError('INTAKE_TARGET_SCOPE_MISMATCH')
    producer=ProductSyncProducer(database,relay.scope,relay.call)
    producer.install();producer.authorize()
    digest=hashlib.sha256(encoded([prepared['source_sha256'],prepared['owner_sha256'],relay.scope,prepared['records']]).encode()).hexdigest()
    with producer.connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS product_intake_run (singleton INTEGER PRIMARY KEY CHECK(singleton=1), digest TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0)')
        old=c.execute('SELECT digest FROM product_intake_run WHERE singleton=1').fetchone()
        if old and old[0]!=digest:raise ValueError('INTAKE_JOURNAL_MISMATCH')
    snapshot=relay.call({'operation':'snapshot'})
    items,_=producer.check_snapshot(snapshot)
    existing={(r['kind'],r['entity_id']):r for r in items}
    for r in prepared['records']:
        old=existing.get((r['kind'],r['entity_id']))
        if old and (old['deleted'] or old['payload']!=r['payload']):raise ValueError('INTAKE_TARGET_COLLISION')
    with producer.connect() as c:
        c.execute('INSERT OR IGNORE INTO product_intake_run(singleton,digest) VALUES(1,?)',(digest,))
        for r in prepared['records']:
            if (r['kind'],r['entity_id']) in existing:continue
            request_id='intake:'+hashlib.sha256((digest+r['entity_id']).encode()).hexdigest()
            c.execute("INSERT OR IGNORE INTO product_sync_intents(record_type,row_key,operation,request_id,payload,entity_id,base_revision) VALUES(?,?,'WRITE',?,?,?,0)",
                      (r['record_type'],r['row_key'],request_id,encoded(r['payload']),r['entity_id']))
    producer.flush()
    final=relay.call({'operation':'snapshot'})
    items,_=producer.check_snapshot(final)
    final_index={(r['kind'],r['entity_id']):r for r in items}
    if any((r['kind'],r['entity_id']) not in final_index or final_index[(r['kind'],r['entity_id'])]['deleted'] or final_index[(r['kind'],r['entity_id'])]['payload']!=r['payload'] for r in prepared['records']):
        raise ProductSyncFailure('INTAKE_INCOMPLETE')
    producer.restore(final)
    with producer.connect() as c:
        for r in prepared['records']:
            spec=RECORDS[r['record_type']]
            row=c.execute('SELECT * FROM "'+r['record_type']+'" WHERE '+' AND '.join('"'+k+'"=?' for k in spec['keys']),json.loads(r['row_key'])).fetchone()
            if row is None or project(r['record_type'],dict(row))!=r['payload']:raise ProductSyncFailure('INTAKE_LOCAL_VERIFY_FAILED')
        c.execute('UPDATE product_intake_run SET completed=1 WHERE singleton=1')
    return dict(status='VERIFIED_COMPLETE',record_count=len(prepared['records']),admitted=prepared['admitted'],held=prepared['held'])


def attach_original_files(prepared, snapshot):
    """Prepare ordinary files from the admitted source IDs; never guess models.

    File locators stay in this local execution manifest, never sync payloads.
    Missing files are explicit and do not hide structured plans/progress.
    """
    import mimetypes
    if sha256_file(snapshot)!=prepared['source_sha256']:raise ValueError('SOURCE_DIGEST_MISMATCH')
    allowed={'.pdf','.ppt','.pptx','.doc','.docx','.png','.jpg','.jpeg','.gif','.webp','.txt','.md','.mp3','.wav','.mp4','.m4a','.ogg','.webm','.csv','.xlsx'}
    admitted={r['payload']['product_record']['values']['id'] for r in prepared['records'] if r['record_type']=='orm_sources'}
    c=sqlite3.connect(Path(snapshot).resolve().as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
    result={**prepared,'records':list(prepared['records']),'original_files':[],'missing_originals':[]}
    try:
        for r in c.execute('SELECT id,local_path FROM orm_sources'):
            if r['id'] not in admitted:continue
            path=Path(r['local_path']) if r['local_path'] else None
            if path is None or path.suffix.lower() not in allowed or not path.is_file() or path.is_symlink():
                result['missing_originals'].append(r['id']);continue
            values=dict(id=r['id'],original_filename=path.name,media_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',sha256=sha256_file(path),size_bytes=path.stat().st_size,source_kind='OWNER_APPROVED_INTAKE',classification='USER_FILE')
            table='product_asset_metadata';payload=project(table,values);entity=record_id(table,values)
            result['records'].append(dict(record_type=table,row_key=encoded([r['id']]),kind='resource',entity_id=entity,payload=payload))
            result['original_files'].append(dict(source_path=str(path),resource_id=entity,sha256=values['sha256']))
    finally:c.close()
    result['admitted']={**prepared['admitted'],'product_asset_metadata':len(result['original_files'])}
    return result


def stage_original_files(prepared, lifecycle, *, expected_account_id):
    """Use the canonical account Vault before its normal Full Portable Export.

    No personal-local archive relabeling; the standard exporter records the
    mounted account fingerprint and maps server IDs through real bindings.
    """
    if not expected_account_id or lifecycle.account_key!=expected_account_id:
        raise ValueError('INTAKE_VAULT_ACCOUNT_MISMATCH')
    from desktop.account_portability import discover_portability_catalogs
    catalogs=discover_portability_catalogs(lifecycle.vault_root)
    manifests={r['asset_id']:r for r in catalogs['asset_manifests']}
    for item in prepared.get('original_files',[]):
        path=Path(item['source_path'])
        if sha256_file(path)!=item['sha256']:raise ValueError('ORIGINAL_FILE_CHANGED')
        prior=lifecycle._vault.get_resource_binding(item['resource_id'])
        if prior and prior.fingerprint_sha256!=item['sha256']:raise ValueError('ORIGINAL_BINDING_CONFLICT')
        asset=next((a for a in lifecycle._vault.list_assets() if prior and a.sha256==item['sha256'] and a.object_relative_path and lifecycle._vault.managed_object_path(a)==prior.local_path),None)
        if asset is None or lifecycle.resolve_resource_binding(item['resource_id']).availability.value!='AVAILABLE_LOCAL':
            asset=lifecycle._vault.import_managed(path,publish_metadata=False)
            lifecycle.bind_resource_to_asset(item['resource_id'],asset.local_asset_id)
        manifests[item['resource_id']]=dict(asset_id=item['resource_id'],asset_revision_id=item['sha256'],
            local_asset_id=asset.local_asset_id,storage_kind='MANAGED',filename=asset.display_name,
            mime_type=asset.mime_type,size_bytes=asset.size_bytes,sha256=asset.sha256,
            vault_relative_path=asset.object_relative_path)
    catalog=lifecycle.vault_root/'data'/'portability'/'asset-manifests.json'
    catalog.parent.mkdir(parents=True,exist_ok=True)
    temporary=catalog.with_suffix('.intake.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        stream.write(encoded(list(manifests.values())))
        stream.flush()
        import os
        os.fsync(stream.fileno())
    temporary.replace(catalog)
    return dict(staged=len(prepared.get('original_files',[])),missing=len(prepared.get('missing_originals',[])))
