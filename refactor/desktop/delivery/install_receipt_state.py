"""Exact receipt authority and explicit pre-seed state, no elevation or writes.

Namespaces determine authority. A JSON object's shape never promotes an
evidence record into an installed receipt. Legacy records have explicit schema
adapters; existing bootstrap bytes are never migrated in place.
"""
import json
from pathlib import Path
import re
import time

INSTALLED_KIND = 'mentorflow-installed-receipt'
BOOTSTRAP_KIND = 'mentorflow-uat-acl-bootstrap'
TRANSACTION_KIND = 'mentorflow-update-transaction'
HEALTH_KIND = 'mentorflow-health-evidence'
RELEASE_KIND = 'mentorflow-signed-release'
STATES = frozenset({'UNINITIALIZED','ACL_READY_NO_BASE','BASE_INSTALLED_HEALTHY',
    'UPDATE_PREPARED','PENDING_APPLY','PENDING_HEALTH','TARGET_INSTALLED_HEALTHY','ROLLBACK_REQUIRED'})
EDGES = {
    'UNINITIALIZED': {'ACL_READY_NO_BASE'},
    'ACL_READY_NO_BASE': {'PENDING_APPLY'},
    'BASE_INSTALLED_HEALTHY': {'UPDATE_PREPARED'},
    'UPDATE_PREPARED': {'PENDING_APPLY'},
    'PENDING_APPLY': {'PENDING_HEALTH','BASE_INSTALLED_HEALTHY','ROLLBACK_REQUIRED'},
    'PENDING_HEALTH': {'TARGET_INSTALLED_HEALTHY','ROLLBACK_REQUIRED'},
    'TARGET_INSTALLED_HEALTHY': {'UPDATE_PREPARED'},
    'ROLLBACK_REQUIRED': {'BASE_INSTALLED_HEALTHY'},
}


class ReceiptStateError(RuntimeError): pass


def read_document(path, maximum=16*1024**2):
    path=Path(path)
    if path.stat().st_size>maximum: raise ReceiptStateError('oversized metadata')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ReceiptStateError('duplicate metadata field')
            result[key]=value
        return result
    try:
        doc=json.loads(path.read_bytes(),object_pairs_hook=pairs,
            parse_constant=lambda _:(_ for _ in ()).throw(ReceiptStateError('nonfinite metadata')))
        if not isinstance(doc,dict): raise ReceiptStateError('metadata must be object')
        return doc
    except (ValueError,UnicodeError) as error: raise ReceiptStateError('malformed metadata') from error


def validate_installed_record(doc, root):
    """Schema-level validation; caller still verifies ACL, inventory and hashes."""
    if not isinstance(doc,dict): raise ReceiptStateError('invalid installed record')
    typed={'schema_version','record_type','state','immutable_tree'}
    present=typed & set(doc)
    if present and (present!=typed or type(doc['schema_version']) is not int or doc['schema_version'] not in (1,2) or
            doc['record_type']!=INSTALLED_KIND or doc['state'] not in ('HEALTHY','PENDING_HEALTH') or doc['immutable_tree'] is not True):
        raise ReceiptStateError('wrong installed record type/state')
    required={'binding','inventory','file_set_identity','critical_hashes','full_verified_at','full_verified'}
    allowed=required|typed|{'control_hashes','health'}
    if not required<=set(doc) or set(doc)-allowed: raise ReceiptStateError('invalid installed schema')
    binding=doc['binding']
    schema = 'mentorflow.admin-installation.v2' if doc.get('schema_version') == 2 else 'mentorflow.admin-installation.v1'
    if not isinstance(binding,dict) or set(binding)!={'schema','source_sha','tree_sha','manifest_sha256','version','root','root_identity'} or binding['schema']!=schema:
        raise ReceiptStateError('wrong installed binding schema')
    if binding['root']!=str(Path(root)) or not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?',binding.get('version','')):
        raise ReceiptStateError('wrong installed root/version')
    for key,size in [('source_sha',40),('tree_sha',40),('manifest_sha256',64)]:
        if not isinstance(binding[key],str) or not re.fullmatch('[0-9a-f]{%d}'%size,binding[key]):raise ReceiptStateError('wrong installed hash')
    rid=binding['root_identity']
    from refactor.desktop.delivery.windows_identity import decode
    from refactor.desktop.delivery.windows_identity import from_legacy
    from refactor.desktop.delivery.windows_identity import IdentityError
    try:
        decode(rid) if doc.get('schema_version') == 2 else from_legacy(rid)
    except IdentityError as error: raise ReceiptStateError('wrong root identity') from error
    if doc.get('schema_version') == 1 and doc.get('state') != 'HEALTHY':
        raise ReceiptStateError('wrong legacy installed state')
    if 'health' in doc:
        health=doc['health']
        fields={'schema','attempt_id','nonce','version','manifest_sha256','install_identity','process','window','attested_at'}
        if (doc.get('schema_version')!=2 or doc.get('state')!='HEALTHY' or not isinstance(health,dict) or
                set(health)!=fields or health['schema']!='mentorflow.process-window-health.v1' or
                health['version']!=binding['version'] or health['manifest_sha256']!=binding['manifest_sha256'] or
                health['install_identity']!=rid):
            raise ReceiptStateError('invalid installed health binding')
        process=health['process'];window=health['window']
        if (not isinstance(process,dict) or set(process)!={'pid','executable','created_filetime_hex','integrity_rid','elevated','alive'} or
                type(process['pid']) is not int or not 0<process['pid']<2**32 or process['executable']!=str(Path(root)/'App/MentorFlow.exe') or
                not isinstance(process['created_filetime_hex'],str) or re.fullmatch('[0-9a-f]{16}',process['created_filetime_hex']) is None or
                process['integrity_rid']!=8192 or process['elevated'] is not False or process['alive'] is not True or
                not isinstance(window,dict) or set(window)!={'hwnd_hex','window_pid','visible','responsive'} or
                window['window_pid']!=process['pid'] or window['visible'] is not True or window['responsive'] is not True or
                not isinstance(window['hwnd_hex'],str) or re.fullmatch('[0-9a-f]{16}',window['hwnd_hex']) is None or
                not isinstance(health['attempt_id'],str) or re.fullmatch('[0-9a-f]{32}',health['attempt_id']) is None or
                not isinstance(health['nonce'],str) or re.fullmatch('[0-9a-f]{64}',health['nonce']) is None or
                type(health['attested_at']) is not int or not 0<health['attested_at']<=time.time()):
            raise ReceiptStateError('invalid installed process/window attestation')
    elif doc.get('schema_version')==2 and doc.get('state')=='HEALTHY':
        raise ReceiptStateError('healthy receipt requires attested process/window evidence')
    if doc['full_verified'] is not True or type(doc['full_verified_at']) not in (int,float) or not 0<doc['full_verified_at']<=time.time():raise ReceiptStateError('invalid installed verification time')
    if not isinstance(doc['inventory'],dict) or not isinstance(doc['critical_hashes'],dict) or not re.fullmatch('[0-9a-f]{64}',doc['file_set_identity']):raise ReceiptStateError('invalid installed inventory')
    if doc.get('schema_version')==2:
        for row in doc['inventory'].values():
            if (not isinstance(row,list) or len(row)!=7 or row[0] not in ('file','dir') or type(row[1]) is not int or not 0<=row[1]<2**53 or
                    any(not isinstance(row[index],str) or re.fullmatch('[0-9a-f]{%d}'%width,row[index]) is None
                        for index,width in ((2,16),(3,16),(4,16),(5,32),(6,16)))):
                raise ReceiptStateError('invalid lossless v2 inventory')
    # Legacy v1 is accepted only by its exact schema, not receipt-looking keys.
    return doc


def bootstrap_record(doc, root):
    """Explicit immutable legacy bootstrap adapter, not receipt recognition."""
    required={'DISPATCH_ID','UAT_ROOT_CREATED','UAT_ROOT','OWNER','DACL_SDDL',
        'ELEVATED_TOKEN_INTEGRITY','ELEVATED_WRITE_TEST','BOOTSTRAP_EXIT_CODE','attempt_nonce',
        'completed_unix','OWNER_CORRECTIONS_IF_NEEDED','OWNER_SUBMITTED_AT_CREATE','INHERITANCE',
        'NORMAL_USER_WRITABLE_AREAS','EXPECTED_PRODUCTION_UPDATE_UAC_COUNT','UPDATE_UAC_PROMPT_COUNT',
        'SENTINEL_TEXT','FORMAL_USER_DATA_WRITES','SERVER_MUTATIONS'}
    if not isinstance(doc,dict) or set(doc)!=required or doc['DISPATCH_ID']!='MF-E107-UAT-PROTECTED-ROOT-BOOTSTRAP-20260907-10':raise ReceiptStateError('unknown bootstrap evidence schema')
    if doc['UAT_ROOT']!=str(Path(root)) or doc['UAT_ROOT_CREATED'] is not True or doc['BOOTSTRAP_EXIT_CODE']!=0 or doc['OWNER'] not in ('S-1-5-32-544','S-1-5-18'):raise ReceiptStateError('invalid bootstrap root/result')
    if doc['ELEVATED_TOKEN_INTEGRITY']!={'elevated':True,'integrity_rid':12288,'integrity':'HIGH'} or doc['ELEVATED_WRITE_TEST']!='PASS_REAL_WINDOWS':raise ReceiptStateError('invalid bootstrap token')
    if not re.fullmatch('[0-9a-f]{32}',doc['attempt_nonce']) or type(doc['completed_unix']) is not int or not 0<doc['completed_unix']<=time.time():raise ReceiptStateError('invalid bootstrap nonce/time')
    if doc['SENTINEL_TEXT']!='MentorFlow UAT elevated controlled write PASS\n':raise ReceiptStateError('unrecognised bootstrap sentinel')
    return dict(schema_version=1,record_type=BOOTSTRAP_KIND,evidence=doc)


def transition(current, event_record):
    if (not isinstance(event_record,dict) or set(event_record)!={'schema_version','record_type','from_state','to_state','attempt_id','nonce'} or
            event_record.get('schema_version')!=1 or event_record.get('record_type')!=TRANSACTION_KIND or event_record.get('from_state')!=current or
            event_record.get('to_state') not in EDGES.get(current,set()) or
            not re.fullmatch('[0-9a-f]{32}',event_record.get('attempt_id','')) or not re.fullmatch('[0-9a-f]{64}',event_record.get('nonce',''))):
        raise ReceiptStateError('invalid transaction transition')
    return event_record['to_state']


def validate_evidence_namespace(root):
    """Separate non-executable evidence namespace, never installed authority."""
    base=Path(root)/'.uat-control'
    if not base.exists():return
    for path in base.iterdir():
        if path.is_dir():raise ReceiptStateError('unexpected nested UAT evidence')
        doc=read_document(path,1024**2)
        if path.name=='acl-bootstrap-result.json':
            bootstrap_record(doc,root)
        elif (doc.get('schema_version')!=1 or doc.get('record_type') not in {BOOTSTRAP_KIND,HEALTH_KIND,TRANSACTION_KIND}):
            raise ReceiptStateError('invalid evidence namespace record type')
