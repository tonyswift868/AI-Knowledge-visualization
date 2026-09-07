"""Signed desktop release contract; no I/O, private keys or installer execution."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import PurePosixPath

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

APP_ID = 'MentorFlow'
UPDATER_VERSION = '1.0.0'
MANIFEST_PATH = 'SELF-CHECK/package-manifest.json'
MAX_METADATA = 16 * 1024**2
HASH = re.compile(r'[0-9a-f]{64}')
SEMVER = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?')


class SecurityError(RuntimeError):
    """Untrusted metadata/content; no downgrade or full fallback is allowed."""


class RecoverableError(RuntimeError):
    """Old installed version remains usable; user can retry later."""


class DownloadCancelled(RecoverableError):
    pass


def canonical(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def version_key(value: str):
    match = SEMVER.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise SecurityError('invalid SemVer')
    major, minor, patch, pre, _build = match.groups()
    identifiers = []
    for item in pre.split('.') if pre else []:
        if item.isdigit():
            if len(item) > 1 and item.startswith('0'):
                raise SecurityError('leading zero in prerelease')
            identifiers.append((0, int(item)))
        else:
            identifiers.append((1, item))
    return int(major), int(minor), int(patch), not bool(pre), tuple(identifiers)


def safe_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 220:
        raise SecurityError('invalid target path')
    parts = value.split('/')
    reserved = re.compile(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)')
    if any(not p or p in ('.', '..') or p.endswith(('.', ' ')) or reserved.match(p)
           or any(ord(c) < 32 or c in '\\:<>"|?*' for c in p) for p in parts):
        raise SecurityError('unsafe Windows target path')
    if parts[0].casefold() in {'data', 'mentorflowdata', 'workspace', 'account', 'uploads',
                               'plans', 'progress', 'logs', 'cache', '.install-staging', '.update-previous'}:
        raise SecurityError('user/control data is not a binary asset')
    return value


def strict_json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SecurityError('duplicate JSON key')
            result[key] = value
        return result
    if len(raw) > MAX_METADATA:
        raise SecurityError('metadata too large')
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(SecurityError('nonfinite JSON')))
    except (ValueError, UnicodeError) as exc:
        raise SecurityError('invalid JSON') from exc


def integer(value, maximum=8 * 1024**3):
    if type(value) is not int or not 0 <= value <= maximum:
        raise SecurityError('invalid asset size')
    return value


@dataclass(frozen=True)
class Release:
    envelope: bytes
    signed: dict
    rows: dict
    assets: dict
    manifest_bytes: bytes

    @property
    def version(self):
        return self.signed['version']


def verify_release(raw: bytes, public_keys: dict[str, bytes], *, current: str,
                   channel: str = 'stable', now=None) -> Release:
    """Trust comes only from caller-provisioned public keys, never the envelope."""
    doc = strict_json(raw)
    try:
        if not isinstance(doc, dict) or set(doc) != {'signed', 'key_id', 'signature'}:
            raise SecurityError('invalid envelope')
        signed = doc['signed']
        key = public_keys.get(doc['key_id'])
        if key is None:
            raise SecurityError('release key is not trusted')
        signature = base64.b64decode(doc['signature'], validate=True)
        Ed25519PublicKey.from_public_bytes(key).verify(signature, canonical(signed))
        required = {'schema_version', 'app_id', 'channel', 'version', 'published_at', 'expires_at',
                    'min_supported_version', 'min_updater_version', 'mandatory', 'mandatory_reason',
                    'release_notes', 'full_release', 'incremental_assets', 'file_manifest',
                    'hash_algorithm', 'signature_algorithm', 'source_sha', 'tree_sha', 'base_versions'}
        if not isinstance(signed, dict) or set(signed) != required:
            raise SecurityError('unsupported release fields')
        if signed['schema_version'] != 1 or signed['app_id'] != APP_ID:
            raise SecurityError('wrong release identity/schema')
        if channel not in ('stable', 'beta') or signed['channel'] != channel:
            raise SecurityError('wrong update channel')
        if signed['hash_algorithm'] != 'sha256' or signed['signature_algorithm'] != 'ed25519':
            raise SecurityError('unsupported release algorithms')
        target = version_key(signed['version'])
        if channel == 'stable' and not target[3]:
            raise SecurityError('prerelease on stable channel')
        if target < version_key(current):
            raise SecurityError('downgrade refused')
        if version_key(UPDATER_VERSION) < version_key(signed['min_updater_version']):
            raise RecoverableError('updater version is too old; controlled upgrade required')
        version_key(signed['min_supported_version'])
        if type(signed['mandatory']) is not bool or signed['mandatory_reason'] not in ('', 'security', 'protocol', 'data_compatibility'):
            raise SecurityError('invalid mandatory policy')
        if signed['mandatory'] and not signed['mandatory_reason']:
            raise SecurityError('mandatory feature-only release refused')
        if not isinstance(signed['release_notes'], str) or len(signed['release_notes']) > 65536:
            raise SecurityError('invalid release notes')
        now = now or datetime.now(timezone.utc)
        published = datetime.fromisoformat(signed['published_at'].replace('Z', '+00:00'))
        expires = datetime.fromisoformat(signed['expires_at'].replace('Z', '+00:00'))
        if published.tzinfo is None or expires.tzinfo is None or published > now + timedelta(minutes=5) or expires <= now or expires <= published:
            raise SecurityError('expired/future release')
        for name in ('source_sha', 'tree_sha'):
            if not re.fullmatch('[0-9a-f]{40}', signed[name]):
                raise SecurityError('invalid source identity')
        if not isinstance(signed['base_versions'], list) or len(signed['base_versions']) > 1000:
            raise SecurityError('invalid bases')
        for base in signed['base_versions']:
            version_key(base)
        manifest = signed['file_manifest']
        if manifest['schema'] != 'mentorflow.windows-package-manifest.v1' or manifest['product'] != APP_ID or manifest['version'] != signed['version']:
            raise SecurityError('manifest identity mismatch')
        if not 1 <= len(manifest['files']) <= 50000:
            raise SecurityError('invalid file count')
        rows, seen = {}, set()
        for row in manifest['files']:
            if set(row) != {'path', 'size', 'sha256'}:
                raise SecurityError('invalid manifest row')
            path = safe_path(row['path'])
            if path.casefold() in seen or path.casefold() == MANIFEST_PATH.casefold():
                raise SecurityError('duplicate/self-referential manifest path')
            seen.add(path.casefold())
            integer(row['size'])
            if not HASH.fullmatch(row['sha256']):
                raise SecurityError('invalid asset hash')
            rows[path] = dict(row)
        # File/directory conflicts must be rejected before creating a tree.
        for path in rows:
            if any(str(p).casefold() in seen for p in PurePosixPath(path).parents if str(p) != '.'):
                raise SecurityError('file/directory conflict')
        manifest_bytes = canonical(manifest)
        rows[MANIFEST_PATH] = dict(path=MANIFEST_PATH, size=len(manifest_bytes), sha256=digest(manifest_bytes))
        full = signed['full_release']
        if set(full) != {'format', 'size', 'assets'} or full['format'] != 'file-set-v1':
            raise SecurityError('unsupported full format')
        assets = {}
        for asset in full['assets']:
            if set(asset) != {'path', 'size', 'sha256', 'url'} or asset['path'] in assets:
                raise SecurityError('invalid/duplicate asset')
            row = rows.get(asset['path'])
            if row is None or any(asset[k] != row[k] for k in ('size', 'sha256')):
                raise SecurityError('asset does not bind target manifest')
            if not isinstance(asset['url'], str) or len(asset['url']) > 4096:
                raise SecurityError('invalid asset URL')
            assets[asset['path']] = dict(asset)
        if set(assets) != set(rows) or integer(full['size'], 512 * 1024**3) != sum(r['size'] for r in rows.values()):
            raise SecurityError('full inventory mismatch')
        if not isinstance(signed['incremental_assets'], list) or len(set(signed['incremental_assets'])) != len(signed['incremental_assets']) or set(signed['incremental_assets']) - set(rows):
            raise SecurityError('invalid incremental asset list')
        return Release(raw, signed, rows, assets, manifest_bytes)
    except SecurityError:
        raise
    except (KeyError, TypeError, ValueError, InvalidSignature, AttributeError, OverflowError) as exc:
        raise SecurityError('release authenticity/contract rejected') from exc


def make_plan(release: Release, local_rows: dict | None, current: str) -> dict:
    full = release.signed['full_release']['size']
    changed = [path for path, row in release.rows.items()
               if not local_rows or local_rows.get(path, {}).get('sha256') != row['sha256']
               or local_rows.get(path, {}).get('size') != row['size']]
    size = sum(release.rows[p]['size'] for p in changed)
    supported = current in release.signed['base_versions'] and version_key(current) >= version_key(release.signed['min_supported_version'])
    fallback = not local_rows or not supported or size >= full
    paths = list(release.rows) if fallback else changed
    return dict(update=version_key(release.version) > version_key(current), current=current,
                latest=release.version, channel=release.signed['channel'],
                release_notes=release.signed['release_notes'],
                update_path='FULL_FALLBACK' if fallback else 'FILE_LEVEL_INCREMENTAL',
                full_bytes=full, expected_download_bytes=sum(release.rows[p]['size'] for p in paths),
                download_paths=paths, deleted=sorted(set(local_rows or {}) - set(release.rows)))
