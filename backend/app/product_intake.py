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


def prepare(snapshot, *, expected_sha256, owner_sha256, confirmed_workspace_ids=(), excluded_workspace_ids=()):
    """Read an immutable backup; retain unidentified rows outside intake."""
    if sha256_file(snapshot)!=expected_sha256: raise ValueError('SOURCE_DIGEST_MISMATCH')
    c=sqlite3.connect(Path(snapshot).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    c.row_factory=sqlite3.Row
    try:
        c.execute('PRAGMA query_only=ON')
        c.execute('PRAGMA trusted_schema=OFF')
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        ownership=list(c.execute('SELECT workspace_id,user_id FROM orm_workspace_ownership'))
        owned={r[0] for r in ownership
               if hashlib.sha256(str(r[1]).encode()).hexdigest()==owner_sha256 }
        if not owned: raise ValueError('NO_PROVEN_OWNER_WORKSPACES')
        # Explicit operator input after Owner confirmation, not a source-provided
        # permission. Admit only named existing roots; never override another owner.
        confirmed=set(confirmed_workspace_ids)
        roots={r[0] for r in c.execute('SELECT id FROM orm_workspaces')}
        if not confirmed<=roots:raise ValueError('CONFIRMED_WORKSPACE_NOT_FOUND')
        if any(r[0] in confirmed and hashlib.sha256(str(r[1]).encode()).hexdigest()!=owner_sha256 for r in ownership):
            raise ValueError('CONFIRMED_WORKSPACE_OTHER_OWNER')
        owned.update(confirmed)
        excluded=set(excluded_workspace_ids)
        if confirmed & excluded:raise ValueError('CONFIRMED_WORKSPACE_EXCLUDED')
        if not excluded<=roots|{r[0] for r in ownership}:raise ValueError('EXCLUDED_WORKSPACE_NOT_FOUND')
        owned.difference_update(excluded)
        rows={t:[dict(r) for r in c.execute('SELECT * FROM "'+t+'"')] for t,s in RECORDS.items()
              if s.get('store','orm')=='orm' and t in tables}
        fks={t:[(r[3],r[2],r[4]) for r in c.execute('PRAGMA foreign_key_list("'+t+'")')] for t in rows}
        accepted={t:{} for t in rows}
        def key(t,r):return encoded([r[k] for k in RECORDS[t]['keys']])
        # The ownership table spans BOTH ORM and active workspace namespaces.
        # Missing orm_workspaces is not missing ownership: active workspaces are
        # represented by active_materials/plans/settings, not synthetic ORM rows.
        # Ownership roots are explicit. Children without workspace derive scope
        # only from actual declared FKs, never from username or row presence.
        def reference_index():
            return {(parent,col):{r[col] for r in accepted.get(parent,{}).values()}
                    for refs in fks.values() for _,parent,col in refs}
        changed=True
        while changed:
            changed=False
            index=reference_index()
            for t,items in rows.items():
                for r in items:
                    k=key(t,r)
                    if k in accepted[t]:continue
                    if t=='orm_workspaces': ok=r['id'] in owned
                    elif 'workspace_id' in r: ok=r['workspace_id'] in owned
                    elif t=='active_learner_profiles' and r['learner_key']=='local_default':
                        # Existing compact-cross-task-v1 producer records origin
                        # workspaces explicitly; it is not an ownerless blob.
                        try:
                            profile=json.loads(r['compact_profile'])
                            refs={profile['current_state']['workspace_id'],
                                  *(point['workspace_id'] for point in profile['learned_points'])}
                            ok=(profile['schema']=='compact-cross-task-v1' and refs<=owned
                                and all(memory['workspace_id'] in owned for memory in rows.get('active_knowledge_memories',[])))
                        except (ValueError,KeyError,TypeError):ok=False
                    else:
                        refs=[(field,parent,col) for field,parent,col in fks[t] if r[field] is not None]
                        ok=bool(refs) and all(r[field] in index.get((parent,col),set()) for field,parent,col in refs)
                    if ok:accepted[t][k]=r;changed=True
        # Any declared dependency outside the admitted group prevents that row
        # (and its dependents) from silently crossing the ownership boundary.
        changed=True
        while changed:
            changed=False
            index=reference_index()
            for t,items in accepted.items():
                for k,r in list(items.items()):
                    if any(r[field] is not None and r[field] not in index.get((parent,col),set()) for field,parent,col in fks[t]):
                        del items[k];changed=True
        records=[]
        for t,items in accepted.items():
            for k,r in items.items():
                payload=project(t,r)
                records.append(dict(record_type=t,row_key=k,kind=RECORDS[t]['kind'],entity_id=record_id(t,r),payload=payload))
        records.sort(key=lambda r:(r['kind']!='resource',r['record_type'],r['row_key']))
        return dict(source_sha256=expected_sha256,owner_sha256=owner_sha256,confirmed_workspace_ids=sorted(confirmed),excluded_workspace_ids=sorted(excluded),records=records,
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


def attach_original_files(prepared, snapshot, *, recovered_originals=None, file_roots=(), recovery_root=None):
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
        for r in c.execute('SELECT id,local_path,source_type FROM orm_sources'):
            if r['id'] not in admitted:continue
            if r['source_type']=='text':continue  # Restored as orm_chunks, not a missing file.
            locator=(recovered_originals or {}).get(r['id']) or r['local_path']
            path=Path(locator) if locator else None
            if path is None or path.suffix.lower() not in allowed or not path.is_file() or path.is_symlink():
                result['missing_originals'].append(r['id']);continue
            values=dict(id=r['id'],original_filename=path.name,media_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',sha256=sha256_file(path),size_bytes=path.stat().st_size,source_kind='OWNER_APPROVED_INTAKE',classification='USER_FILE')
            table='product_asset_metadata';payload=project(table,values);entity=record_id(table,values)
            result['records'].append(dict(record_type=table,row_key=encoded([r['id']]),kind='resource',entity_id=entity,payload=payload))
            result['original_files'].append(dict(source_path=str(path),resource_id=entity,sha256=values['sha256']))
    finally:c.close()
    # Existing active resources already have canonical resource IDs and hashes.
    # Rebind their actual files, never publish a duplicate asset metadata row.
    metadata_count=len(result['original_files'])
    active={r['entity_id']:r['payload'] for r in prepared['records'] if r['record_type']=='active_materials'}
    c=sqlite3.connect(Path(snapshot).resolve().as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
    try:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for row in c.execute('SELECT id,stored_path FROM active_materials') if 'active_materials' in tables else []:
            payload=active.get(row['id'])
            if payload is None:continue
            source=Path(row['stored_path']);candidates=[source]
            parts=source.parts
            for i,part in enumerate(parts):
                if part.lower()=='data':
                    candidates.extend(Path(root).joinpath(*parts[i+1:]) for root in file_roots)
            match=next((p for p in candidates if p.is_file() and not p.is_symlink() and p.stat().st_size==payload['size_bytes'] and sha256_file(p)==payload['sha256']),None)
            # Retained text uploads were converted to CRLF in place. Recover
            # LF bytes only if BOTH authoritative size and SHA256 prove exactness.
            # No original is modified and no fuzzy/text-similarity match is used.
            if match is None and recovery_root is not None and (payload['media_type'].startswith('text/') or payload['media_type']=='application/json'):
                for candidate in candidates:
                    if not candidate.is_file() or candidate.is_symlink() or candidate.stat().st_size>8*1024*1024:continue
                    raw=candidate.read_bytes().replace(b'\r\n',b'\n')
                    if len(raw)!=payload['size_bytes'] or hashlib.sha256(raw).hexdigest()!=payload['sha256']:continue
                    destination=Path(recovery_root)/hashlib.sha256(row['id'].encode()).hexdigest()[:20]/candidate.name
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    if destination.exists():
                        if sha256_file(destination)!=payload['sha256']:raise ValueError('ORIGINAL_RECOVERY_COLLISION')
                    else:
                        with destination.open('xb') as stream:stream.write(raw)
                    match=destination
                    result.setdefault('verified_line_ending_recoveries',[]).append(row['id'])
                    break
            if match is None:result['missing_originals'].append(row['id'])
            else:result['original_files'].append(dict(source_path=str(match),resource_id=row['id'],sha256=payload['sha256']))
    finally:c.close()
    result['admitted']={**prepared['admitted'],'product_asset_metadata':metadata_count}
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
        # Several server resources can share identical bytes/object paths.
        # Preserve the catalog's per-resource local ID; do not collapse them
        # into one raw ZIP member when staging is retried.
        claimed={m.get('local_asset_id') for identity,m in manifests.items() if identity!=item['resource_id']}
        preferred=manifests.get(item['resource_id'],{}).get('local_asset_id')
        candidates=sorted(lifecycle._vault.list_assets(),key=lambda a:a.local_asset_id!=preferred)
        asset=next((a for a in candidates if prior and a.local_asset_id not in claimed and a.sha256==item['sha256'] and a.object_relative_path and lifecycle._vault.managed_object_path(a)==prior.local_path),None)
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


def recover_indexed_archive_text(prepared, snapshot, index_databases, output):
    """Recover exact UTF-8 originals only when the retained ZIP index proves bytes.

    Parsed text alone is NOT an original. Both raw ZIP-member size and SHA256
    must agree. Read-only indexed databases are explicitly supplied, never scanned.
    Returns local original locators for attach_original_files and missing names.
    """
    from pathlib import PurePosixPath
    if sha256_file(snapshot)!=prepared['source_sha256']:raise ValueError('SOURCE_DIGEST_MISMATCH')
    admitted={r['payload']['product_record']['values']['id'] for r in prepared['records'] if r['record_type']=='orm_sources'}
    c=sqlite3.connect(Path(snapshot).resolve().as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
    try:sources=[dict(r) for r in c.execute("SELECT id,archive_preview_id,archive_relative_path,parent_archive_name FROM orm_sources WHERE source_type='archive_file'") if r['id'] in admitted]
    finally:c.close()
    recovered={};proof={}
    for database in index_databases:
        d=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True);d.row_factory=sqlite3.Row
        try:
            d.execute('PRAGMA query_only=ON');d.execute('PRAGMA trusted_schema=OFF')
            tables={r[0] for r in d.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'sources','archive_preview_items'}.issubset(tables):continue
            for source in sources:
                if source['id'] in recovered:continue
                row=d.execute('SELECT * FROM archive_preview_items WHERE preview_id=? AND relative_path=?',(source['archive_preview_id'],source['archive_relative_path'])).fetchone()
                text=d.execute('SELECT text FROM sources WHERE id=?',(source['id'],)).fetchone()
                if row is None or text is None or not isinstance(text[0],str):continue
                raw=text[0].encode('utf-8')
                if len(raw)!=row['size_bytes'] or hashlib.sha256(raw).hexdigest()!=row['content_hash']:continue
                filename=PurePosixPath(source['archive_relative_path']).name
                if not filename or any(ch in filename for ch in '\\/:\x00') or filename in {'.','..'}:raise ValueError('ARCHIVE_FILENAME_INVALID')
                destination=Path(output)/hashlib.sha256(source['id'].encode()).hexdigest()[:20]/filename
                destination.parent.mkdir(parents=True,exist_ok=True)
                if destination.exists():
                    if sha256_file(destination)!=row['content_hash']:raise ValueError('ARCHIVE_RECOVERY_COLLISION')
                else:
                    with destination.open('xb') as stream:stream.write(raw)
                recovered[source['id']]=str(destination)
                proof[source['id']]=dict(index_database=str(database),member=source['archive_relative_path'],sha256=row['content_hash'],size_bytes=len(raw))
        finally:d.close()
    return dict(originals=recovered,proof=proof,missing=[dict(source_id=s['id'],archive=s['parent_archive_name'],member=s['archive_relative_path']) for s in sources if s['id'] not in recovered])
