"""Frozen update-attempt data, shared by producer, dry-run and high consumer.

This request is NOT authorization: the helper must separately verify its own
protected installation authority, signed release, process peers and target.
Only producer() creates attempts; expiry creates a NEW file/nonce, never edits.
"""
import hashlib
import os
from pathlib import Path, PureWindowsPath
import re
import time
import uuid

from .update_protocol import canonical, strict_json, SecurityError, version_key
from .windows_identity import decode, read_directory

SCHEMA = 'mentorflow.update-attempt.v2'
FIELDS = {'schema', 'attempt_id', 'nonce', 'created_at', 'expires_at', 'app_id',
          'install_root', 'install_identity', 'base_version', 'base_manifest_sha256',
          'base_receipt_sha256', 'target_version', 'target_manifest_sha256',
          'release_sha256', 'staged_app', 'coordinator_pid', 'coordinator_created_hex'}


def local_path(value):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise SecurityError('invalid update local path')
    path = PureWindowsPath(value)
    if (not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ':'
            or str(path) != value or path == PureWindowsPath(path.anchor)
            or any(p in ('.','..') or p.endswith((' ','.')) for p in path.parts[1:])
            or any(ch in part for part in path.parts[1:] for ch in '<>:"|?*')):
        raise SecurityError('noncanonical/unsafe update local path')
    return path


def validate(doc, *, now=None):
    now = time.time() if now is None else now
    if not isinstance(doc, dict) or set(doc) != FIELDS or doc['schema'] != SCHEMA:
        raise SecurityError('unknown update request schema')
    for key, size in [('attempt_id',32),('nonce',64),('base_manifest_sha256',64),
                      ('base_receipt_sha256',64),('target_manifest_sha256',64),('release_sha256',64)]:
        if not isinstance(doc[key], str) or re.fullmatch('[0-9a-f]{%d}' % size, doc[key]) is None:
            raise SecurityError('invalid request identity: ' + key)
    if (type(doc['created_at']) is not int or type(doc['expires_at']) is not int
            or not 0 < doc['created_at'] <= now < doc['expires_at']
            or not 1 <= doc['expires_at'] - doc['created_at'] <= 900):
        raise SecurityError('expired/invalid update attempt; create a new attempt')
    if not isinstance(doc['app_id'],str) or re.fullmatch(r'MentorFlow(?:\.UAT\.[A-Za-z0-9-]{1,32})?', doc['app_id']) is None:
        raise SecurityError('invalid update app identity')
    if type(doc['coordinator_pid']) is not int or not 0 < doc['coordinator_pid'] < 2**32:
        raise SecurityError('invalid coordinator PID')
    if not isinstance(doc['coordinator_created_hex'],str) or re.fullmatch('[0-9a-f]{16}',doc['coordinator_created_hex']) is None:
        raise SecurityError('invalid coordinator creation identity')
    try: decode(doc['install_identity'])
    except ValueError as error: raise SecurityError('invalid request root identity') from error
    root, staged = local_path(doc['install_root']), local_path(doc['staged_app'])
    if root == staged or root in staged.parents or staged in root.parents or staged.name != 'App':
        raise SecurityError('staging must be a separate App outside the install root')
    if version_key(doc['target_version']) <= version_key(doc['base_version']):
        raise SecurityError('target must be a newer signed version')
    return doc


def read(raw, *, expected_sha256=None, now=None):
    if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise SecurityError('frozen request bytes changed')
    doc = validate(strict_json(raw), now=now)
    if raw != canonical(doc):
        raise SecurityError('request must use canonical JSON encoding')
    return doc


def produce(directory, *, app_id, install_root, staged_app, base_receipt,
            release, coordinator_pid, lifetime=900):
    """Create-only; caller supplies already accepted receipt and signed release.

    Native identity is read here, not copied from potentially rounded metadata.
    Metadata is consumed as Python bytes throughout this producer.
    """
    from .update_tree_security import DirectoryPins, normal
    from scripts.install_receipt_state import read_document, validate_installed_record
    root, stage, directory = map(normal, (install_root, staged_app, directory))
    if directory == root or directory.is_relative_to(root) or root.is_relative_to(directory):
        raise SecurityError('request evidence must remain outside protected installation')
    with DirectoryPins() as pins:
        pins.check(directory,directory,directory=True,missing=False)
        pins.check(stage,stage,directory=True,missing=False)
        # canonical authority path only; don't scan for a receipt-looking JSON.
        receipt_path = root / 'installation-receipt.json'
        pins.check(receipt_path,root,missing=False)
        actual = receipt_path.read_bytes()
        if actual != base_receipt:
            raise SecurityError('installed receipt changed before request freeze')
        receipt = validate_installed_record(read_document(receipt_path), root)
        binding = receipt['binding']
        # The signed protocol supports multiple incremental bases and full
        # fallback. Base authority comes from this receipt, not an invented
        # singular release.base_version field.
        now = int(time.time())
        from desktop.windows_process_peer import ProcessPeer
        with ProcessPeer(coordinator_pid) as peer:
            coordinator_created=peer.inspect()['created_filetime_hex']
        doc = dict(schema=SCHEMA, attempt_id=uuid.uuid4().hex, nonce=os.urandom(32).hex(),
            created_at=now, expires_at=now+lifetime, app_id=app_id,
            install_root=str(root), install_identity=read_directory(root),
            base_version=binding['version'], base_manifest_sha256=binding['manifest_sha256'],
            base_receipt_sha256=hashlib.sha256(actual).hexdigest(),
            target_version=release.version, target_manifest_sha256=hashlib.sha256(release.manifest_bytes).hexdigest(),
            release_sha256=hashlib.sha256(release.envelope).hexdigest(),
            staged_app=str(stage), coordinator_pid=coordinator_pid,coordinator_created_hex=coordinator_created)
        raw = canonical(validate(doc))
        destination = directory / (doc['attempt_id'] + '.request.json')
        pins.check(destination,directory)
        with destination.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        return destination, hashlib.sha256(raw).hexdigest()
