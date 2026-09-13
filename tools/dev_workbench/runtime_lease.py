"""Fixed coordination authority for ephemeral A/B transport; never production config."""
from contextlib import ExitStack, contextmanager
import ctypes
from datetime import datetime, timezone
import os
from pathlib import Path
import re

from jsonschema import Draft202012Validator
from cryptography import x509
from desktop.test_device_configuration import CONTRACT, validate
from .targets import Refused, TrustedPaths, json_bytes, pinned_file, sha256

AUTHORITY = Path('E:/Codex/Coordination/MentorFlow/MAIN3/dev-workbench/acceptance-runtime')
MAX_BYTES = 32768
UNAVAILABLE = 'ACCEPTANCE_RUNTIME_UNAVAILABLE'
STRING = {'type': 'string', 'minLength': 1, 'maxLength': 1500}
HEX = {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}
SHA = {'type': 'string', 'pattern': '^[0-9a-f]{40}$'}
CONFIG = dict(type='object', additionalProperties=False,
    required=['contract','issuer','api_base_url','client_id','audience','organization_id','workspace_id','tls_ca_path','tls_ca_sha256'],
    properties={**{k:STRING for k in ('issuer','api_base_url','client_id','audience','organization_id','workspace_id','tls_ca_path')},
                'contract': {'const': CONTRACT}, 'tls_ca_sha256': HEX})
PROPERTIES = {k:STRING for k in (
    'runtime_lease_id','test_id','desktop_artifact_path','backend_build_id','backend_artifact_path',
    'backend_endpoint','identity_endpoint','verifier_endpoint','ca_certificate_reference',
    'backend_client_certificate_reference','runtime_root','runtime_secret_directory',
    'acceptance_credentials_reference','backend_client_private_key_reference','database_identity',
    'database_endpoint','database_name','identity_database_name','database_data_directory',
    'postgresql_version','alembic_head','cleanup_policy','configuration_path','launch_binding',
    'real_ab_rehydration','operator_probe_correction')}
PROPERTIES.update({k:HEX for k in ('desktop_artifact_sha256','backend_digest','ca_fingerprint',
                                  'server_certificate_fingerprint','backend_client_certificate_fingerprint')})
PROPERTIES.update({k:SHA for k in ('build_source_sha','build_source_tree','backend_source_sha','backend_source_tree')})
PROPERTIES.update({k:{'type':'boolean'} for k in ('automatic_cleanup','real_user_data_touched','product_source_changed','private_key_content_in_manifest')})
PROPERTIES['private_key_content_in_manifest'] = {'const': False}
PROPERTIES.update(schema={'enum':['MENTORFLOW_EPHEMERAL_ACCEPTANCE_RUNTIME_LEASE_V1',
                                 'MENTORFLOW_EPHEMERAL_ACCEPTANCE_RUNTIME_LEASE_V2']},
    status={'enum':['READY_FOR_ACCEPTANCE','SUPERSEDED','EXPIRED','STOPPED']},
    created_at=STRING, expires_at=STRING, superseded_by=STRING,
    desktop_runtime_configuration=CONFIG,
    processes={'type':'object','additionalProperties':False,'properties':{
        k:{'type':'integer','minimum':1} for k in ('supervisor_pid','backend_pid','identity_pid','postgres_pid')}},
    checks={'type':'object','additionalProperties':False,'properties':{k:{'type':'boolean'} for k in (
        'certificate_key_match','identity_discovery','mtls_missing_cert_rejected','mtls_wrong_cert_rejected',
        'mtls_valid_accepted','fresh_to_0038','backend_health_schema_preflight','backend_native_auth_me_bootstrap')}},
    old_runtime={'type':'object','additionalProperties':False,'required':['status','endpoint','reason'],
                 'properties':{k:STRING for k in ('status','endpoint','reason')}},
    certificates={'type':'object','additionalProperties':False,'properties':{name:{
        'type':'object','additionalProperties':False,'required':['sha256_fingerprint','subject','expires_at'],
        'properties':{'sha256_fingerprint':HEX,'subject':STRING,'expires_at':STRING,
                      'sans':{'type':'array','maxItems':20,'items':STRING}}}
        for name in ('ca','server','backend','other')}})
SCHEMA = dict(type='object', additionalProperties=False, properties=PROPERTIES, required=[
    'schema','runtime_lease_id','status','test_id','build_source_sha','build_source_tree',
    'desktop_artifact_sha256','created_at','expires_at','ca_fingerprint','desktop_runtime_configuration'])


def stamp(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', value):
        raise Refused(UNAVAILABLE)
    try: return datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError: raise Refused(UNAVAILABLE) from None


def assert_owned(path):
    """Coordination files must be owned by this Windows account, SYSTEM or admins.

    Same-account A/02 coordination is the authority boundary, as with local Git.
    Private keys and resolver references are metadata only and are never opened.
    """
    if os.name != 'nt':
        if path.stat().st_uid != os.getuid(): raise Refused(UNAVAILABLE)
        return
    from ctypes import wintypes as w
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    owner, descriptor, token = ctypes.c_void_p(), ctypes.c_void_p(), w.HANDLE()
    adv.GetNamedSecurityInfoW.argtypes = [w.LPWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p]
    adv.GetNamedSecurityInfoW.restype = w.DWORD
    adv.OpenProcessToken.argtypes = [w.HANDLE,w.DWORD,ctypes.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes = [w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]
    adv.EqualSid.argtypes = [ctypes.c_void_p,ctypes.c_void_p]
    adv.IsWellKnownSid.argtypes = [ctypes.c_void_p,ctypes.c_int]
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    try:
        if adv.GetNamedSecurityInfoW(str(path),1,1,ctypes.byref(owner),None,None,None,ctypes.byref(descriptor)):
            raise Refused(UNAVAILABLE)
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(),8,ctypes.byref(token)): raise Refused(UNAVAILABLE)
        size = w.DWORD()
        adv.GetTokenInformation(token,1,None,0,ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token,1,buffer,size,ctypes.byref(size)): raise Refused(UNAVAILABLE)
        user = ctypes.cast(buffer,ctypes.POINTER(ctypes.c_void_p))[0]
        if not (adv.EqualSid(owner,user) or adv.IsWellKnownSid(owner,22) or adv.IsWellKnownSid(owner,26)):
            raise Refused(UNAVAILABLE)
    finally:
        if token: kernel.CloseHandle(token)
        if descriptor: kernel.LocalFree(descriptor)


def validate_lease(value, manifest, now):
    if not Draft202012Validator(SCHEMA).is_valid(value): raise Refused(UNAVAILABLE)
    # No secret payload can hide inside otherwise permitted descriptive metadata.
    if re.search(r'-----BEGIN [^-]*PRIVATE KEY-----', str(value), re.I): raise Refused(UNAVAILABLE)
    if (value['status'] != 'READY_FOR_ACCEPTANCE' or 'superseded_by' in value or
            not stamp(value['created_at']) <= now < stamp(value['expires_at']) or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,119}',value['runtime_lease_id'])):
        raise Refused(UNAVAILABLE)
    for key, field in (('test_id','test_id'),('build_source_sha','source_sha'),
                       ('build_source_tree','source_tree'),('desktop_artifact_sha256','artifact_sha256')):
        if value[key] != manifest[field]: raise Refused('ACCEPTANCE_RUNTIME_CANDIDATE_MISMATCH')
    try: config = validate(value['desktop_runtime_configuration'])
    except ValueError: raise Refused(UNAVAILABLE) from None
    for key, field in (('backend_endpoint','api_base_url'),('identity_endpoint','issuer'),
                       ('ca_certificate_reference','tls_ca_path')):
        if key in value and value[key] != config[field]: raise Refused(UNAVAILABLE)
    return config


@contextmanager
def verified_runtime(manifest, paths):
    """Acquire fixed LATEST + named publication + CA leases through CreateProcess."""
    if manifest.get('environment') not in ('TEST','STAGING') or manifest.get('status') != 'CURRENT':
        raise Refused(UNAVAILABLE)
    try:
        with ExitStack() as stack:
            authority_paths = TrustedPaths([AUTHORITY])
            def read(path):
                path = authority_paths.check(str(path))
                stream = stack.enter_context(pinned_file(path))
                authority_paths.check(str(path))
                assert_owned(path); assert_owned(path.parent)
                raw = stream.read(MAX_BYTES+1)
                if len(raw)>MAX_BYTES: raise Refused(UNAVAILABLE)
                return raw
            raw = read(AUTHORITY/'LATEST.json')
            lease = json_bytes(raw)
            config = validate_lease(lease,manifest,datetime.now(timezone.utc))
            # A mutable pointer alone cannot invent a publication; named record
            # must independently exist in the same controlled directory.
            if read(AUTHORITY/(lease['runtime_lease_id']+'.json')) != raw: raise Refused(UNAVAILABLE)
            ca = paths.check(config['tls_ca_path'])
            stream = stack.enter_context(pinned_file(ca)); paths.check(str(ca))
            cert_bytes = stream.read(1024*1024+1)
            if len(cert_bytes)>1024*1024 or sha256(cert_bytes)!=config['tls_ca_sha256']:
                raise Refused('ACCEPTANCE_RUNTIME_CA_MISMATCH')
            if re.search(rb'-----BEGIN [^-]*PRIVATE KEY-----', cert_bytes, re.I):
                raise Refused('ACCEPTANCE_RUNTIME_CA_MISMATCH')
            from cryptography.hazmat.primitives import hashes
            certificate = x509.load_pem_x509_certificate(cert_bytes)
            if (certificate.fingerprint(hashes.SHA256()).hex()!=lease['ca_fingerprint'] or
                    not certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca):
                raise Refused('ACCEPTANCE_RUNTIME_CA_MISMATCH')
            def current():
                now=datetime.now(timezone.utc)
                validate_lease(lease,manifest,now)
                if not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc:
                    raise Refused(UNAVAILABLE)
            current()
            yield config, lease['runtime_lease_id'], current
    except Refused: raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError, x509.ExtensionNotFound):
        raise Refused(UNAVAILABLE) from None
