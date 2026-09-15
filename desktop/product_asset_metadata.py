"""Use the existing Vault transaction and local API for metadata delivery."""
import json


def bind_vault_metadata(vault, api):
    vault._metadata_publisher = lambda key, value: api.request('PUT','/api/product-resources/'+key,
        json=value,headers={'X-MentorFlow-Command-ID':'vault-'+key})
    vault._database.execute('CREATE TABLE IF NOT EXISTS asset_metadata_pending (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    vault._database.commit()
    flush_vault_metadata(vault)


def enqueue_vault_metadata(vault, record):
    if getattr(vault,'_metadata_publisher',None) is None: return
    from pathlib import Path
    from .account_vault import _sha256_file
    digest = record.sha256 or _sha256_file(Path(record.linked_path))
    data = dict(original_filename=record.display_name,media_type=record.mime_type,
        sha256=digest,size_bytes=record.size_bytes,source_kind=record.import_mode.value,classification='USER_FILE')
    vault._database.execute('INSERT INTO asset_metadata_pending VALUES(?,?)',(record.local_asset_id,json.dumps(data)))


def flush_vault_metadata(vault):
    publisher=getattr(vault,'_metadata_publisher',None)
    if publisher is None: return
    for row in vault._database.execute('SELECT * FROM asset_metadata_pending ORDER BY rowid').fetchall():
        publisher(row['id'],json.loads(row['payload']))
        vault._database.execute('DELETE FROM asset_metadata_pending WHERE id=?',(row['id'],))
        vault._database.commit()


def resume_pending_import(vault,source,mode):
    """A retry after local import + lost metadata ACK returns the same asset."""
    if getattr(vault,'_metadata_publisher',None) is None:return None
    from .account_vault import _sha256_file
    candidates=[]
    for row in vault._database.execute('SELECT * FROM asset_metadata_pending').fetchall():
        metadata=json.loads(row['payload'])
        if metadata['source_kind']==mode and metadata['original_filename']==source.name and metadata['size_bytes']==source.stat().st_size:
            candidates.append((row['id'],metadata['sha256']))
    if not candidates:return None
    digest=_sha256_file(source)
    for key,expected in candidates:
        if digest==expected:
            flush_vault_metadata(vault)
            row=vault._database.execute('SELECT * FROM local_assets WHERE local_asset_id=?',(key,)).fetchone()
            return vault._asset(row)
    return None


def resource_rows(vault,lifecycle,api):
    from .local_asset_page import AccountResourceRow
    flush_vault_metadata(vault)
    local={row.local_asset_id:row for row in lifecycle.list_assets()}
    result=[]
    for remote in api.request('GET','/api/product-resources'):
        key=remote['id']
        if key in local:
            result.append(AccountResourceRow.from_local_asset(local.pop(key)))
        else:
            binding=vault.get_resource_binding(key)
            if binding is None:
                binding=lifecycle.register_missing_resource(key,fingerprint_sha256=remote['sha256'])
            binding=lifecycle.resolve_resource_binding(key)
            available=binding.availability.value=='AVAILABLE_LOCAL'
            result.append(AccountResourceRow(key,remote['original_filename'],remote['size_bytes'],
                remote['source_kind'],'AVAILABLE_LOCAL' if available else 'MISSING_ON_THIS_DEVICE',() if available else ('relink',)))
    result.extend(AccountResourceRow.from_local_asset(row) for row in local.values())
    return result
