"""Exact local test targets. No shell text, network fetch, secrets or product engine."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'docs/governance/TEST_TARGET_MANIFEST.schema.json'
LIMIT = 2 * 1024 * 1024
IDENTITY_FIELDS = ('source_sha', 'source_tree', 'product_version', 'build_id',
                   'artifact_type', 'artifact_sha256', 'environment', 'deployment_identity')
SCRIPT_IDS = frozenset({'WORKBENCH_SELF_CHECK'})


class Refused(RuntimeError):
    """Only static error codes cross the UI; never echo rejected input."""


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise Refused('MALFORMED_JSON')
            result[key] = value
        return result
    if len(raw) > LIMIT: raise Refused('INPUT_TOO_LARGE')
    try:
        return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Refused('MALFORMED_JSON')))
    except (ValueError, UnicodeError):
        raise Refused('MALFORMED_JSON') from None


def validate_manifest(value):
    validator = Draft202012Validator(json_bytes(SCHEMA.read_bytes()), format_checker=FormatChecker())
    if not validator.is_valid(value): raise Refused('INVALID_MANIFEST')
    # jsonschema's date-time checker is optional. Admission must stay strict in
    # the existing minimal desktop runtime even without its format extra.
    stamp = value['created_at']
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})', stamp):
        raise Refused('INVALID_MANIFEST')
    try:
        datetime.fromisoformat(stamp.upper().replace('Z', '+00:00'))
    except ValueError:
        raise Refused('INVALID_MANIFEST') from None
    if value['status'] == 'CURRENT' and any(value[key].upper() in {'UNKNOWN', 'NONE', 'NOT_RUN', 'PENDING'}
            for key in ('product_version', 'build_id', 'deployment_identity')):
        raise Refused('INVALID_MANIFEST')
    return value


class TrustedPaths:
    def __init__(self, roots=None):
        self.roots = tuple(Path(p).absolute() for p in (roots if roots is not None else (
            'E:/Codex/Temp', 'E:/Codex/Artifacts',
            'E:/Codex/Projects/MentorFlow/artifacts', 'E:/Codex/Projects/MentorFlow/release',
            ROOT / 'artifacts', ROOT / 'release')))

    def check(self, value, *, directory=False):
        if not isinstance(value, str) or not value or value.startswith(('\\\\', '//')):
            raise Refused('UNTRUSTED_PATH')
        path = Path(value)
        if not path.is_absolute() or any(c in value for c in ('\x00', '\n', '\r')):
            raise Refused('UNTRUSTED_PATH')
        for part in path.parts[1:]:
            if part in ('.', '..') or part.endswith((' ', '.')) or ':' in part or re.match(
                    r'(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', part):
                raise Refused('UNTRUSTED_PATH')
        if not any(path.is_relative_to(root) for root in self.roots):
            raise Refused('UNTRUSTED_PATH')
        try:
            for parent in reversed((path, *path.parents)):
                info = parent.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise Refused('UNTRUSTED_PATH')
            if path.is_dir() != directory or not (path.is_dir() if directory else path.is_file()):
                raise Refused('ARTIFACT_MISSING')
            if not any(path.resolve().is_relative_to(root.resolve()) for root in self.roots):
                raise Refused('UNTRUSTED_PATH')
        except OSError:
            raise Refused('ARTIFACT_MISSING') from None
        return path


@contextmanager
def pinned_file(path):
    """Windows deny-write/delete lease held through CreateProcess admission."""
    if os.name != 'nt':
        with path.open('rb') as stream: yield stream
        return
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    handles = []
    try:
        # Deny parent renames while the final executable is checked and launched.
        for parent in reversed(path.parents):
            handle = create(str(parent), 0x80, 3, None, 3, 0x02200000, None)
            if handle == wintypes.HANDLE(-1).value: raise Refused('ARTIFACT_BUSY')
            handles.append(handle)
        handle = create(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
        if handle == wintypes.HANDLE(-1).value: raise Refused('ARTIFACT_BUSY')
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        with os.fdopen(fd, 'rb') as stream: yield stream
    finally:
        for handle in reversed(handles): close(handle)


def read_checked(path, paths):
    path = paths.check(str(path))
    with pinned_file(path) as stream:
        return stream.read(LIMIT + 1)


def navigation_path(manifest, kind, paths):
    if kind == 'directory':
        artifact = paths.check(manifest.get('artifact_path'))
        return paths.check(str(artifact.parent), directory=True)
    key = {'receipt': 'receipt_path', 'log': 'log_path'}.get(kind)
    if key is None: raise Refused('UNKNOWN_ACTION')
    path = paths.check(manifest.get(key))
    if path.suffix.lower() not in {'.json', '.jsonl', '.md', '.txt', '.log', '.xml', '.pdf', '.png'}:
        raise Refused('UNSAFE_DOCUMENT_TYPE')
    if manifest.get('secret_ref') and not manifest['secret_ref'].startswith('secret://') and path == Path(manifest['secret_ref']):
        raise Refused('SECRET_REFERENCE_ONLY')
    return path


def launch_exact(manifest, approved, source_tree, paths, *, spawn=subprocess.Popen, cancelled=lambda: False,
                 device_environment=None, device_contract=None):
    """Caller reloads repo approval at click time; cache/selection never authorizes."""
    validate_manifest(manifest)
    if manifest != approved: raise Refused('TARGET_CHANGED')
    if manifest['status'] != 'CURRENT': raise Refused('OLD_OR_UNREADY_TARGET')
    if source_tree(manifest['source_sha']) != manifest['source_tree']:
        raise Refused('SOURCE_IDENTITY_MISMATCH')
    artifact = paths.check(manifest['artifact_path'])
    evidence = paths.check(manifest['identity_evidence_path'])
    with ExitStack() as stack:
        stream = stack.enter_context(pinned_file(artifact))
        identity_stream = stack.enter_context(pinned_file(evidence))
        # Recheck aliases after acquiring the leases.
        paths.check(str(artifact)); paths.check(str(evidence))
        hasher = hashlib.sha256()
        while block := stream.read(1024 * 1024):
            if cancelled(): raise Refused('CANCELLED')
            hasher.update(block)
        digest = hasher.hexdigest()
        if digest != manifest['artifact_sha256']: raise Refused('ARTIFACT_HASH_MISMATCH')
        raw = identity_stream.read(LIMIT + 1)
        if len(raw) > LIMIT or sha256(raw) != manifest['identity_evidence_sha256']:
            raise Refused('BUILD_IDENTITY_MISMATCH')
        identity = json_bytes(raw)
        if not isinstance(identity, dict) or identity.get('schema_version') != 1 or any(
                identity.get(key) != manifest[key] for key in IDENTITY_FIELDS):
            raise Refused('BUILD_IDENTITY_MISMATCH')
        kind = manifest['launch_type']
        if device_environment is not None:
            from desktop.test_device_profile import PROFILE_CONTRACT
            if (device_contract != PROFILE_CONTRACT or kind != 'DESKTOP_EXE' or
                    identity.get('device_profile_contract') != PROFILE_CONTRACT):
                raise Refused('TEST_DEVICE_CONTRACT_REQUIRED')
        if kind in ('DESKTOP_EXE', 'INSTALLER'):
            if manifest['artifact_type'] != kind or manifest['launch_target'] != manifest['artifact_path']:
                raise Refused('LAUNCH_TARGET_MISMATCH')
            stream.seek(0)
            if artifact.suffix.lower() != '.exe' or stream.read(2) != b'MZ':
                raise Refused('INVALID_EXECUTABLE')
            argv = [str(artifact)]
        elif kind == 'SCRIPT_ID' and manifest['launch_target'] in SCRIPT_IDS:
            if manifest['artifact_type'] != 'SCRIPT_INPUT': raise Refused('LAUNCH_TARGET_MISMATCH')
            # Fixed repo-owned module; JSON supplies only a schema-checked opaque ID.
            argv = [sys.executable, '-I', str(ROOT / 'tools/dev_workbench/self_check.py'), manifest['test_id']]
        else:
            raise Refused('UNKNOWN_SCRIPT_ID')
        if cancelled(): raise Refused('CANCELLED')
        try:
            options = dict(cwd=str(ROOT if kind == 'SCRIPT_ID' else artifact.parent), shell=False, close_fds=True)
            if device_environment is not None: options['env'] = device_environment
            return spawn(argv, **options)
        except OSError:
            raise Refused('LAUNCH_FAILED') from None
