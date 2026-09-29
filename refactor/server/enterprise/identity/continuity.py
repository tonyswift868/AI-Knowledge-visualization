"""Operator-authenticated, per-subject E109 continuity. No implicit aliasing.

Verification key and exact manifest digest are deployment trust anchors, outside
the signed document. This module neither accepts tokens nor widens issuer policy.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .issuer_migration import OLD_ISSUER, NEW_ISSUER


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate continuity field')
        result[key] = value
    return result


@dataclass(frozen=True, slots=True, init=False)
class SignedIdentityContinuity:
    subjects: frozenset[str]
    audit_id: str
    manifest_sha256: str

    def __init__(self, manifest: bytes, signature: bytes, public_key: bytes, expected_sha256: str):
        if not 1 <= len(manifest) <= 1024 * 1024:
            raise ValueError('Invalid continuity manifest size')
        digest = hashlib.sha256(manifest).hexdigest()
        if not re.fullmatch(r'[0-9a-f]{64}', expected_sha256) or digest != expected_sha256:
            raise ValueError('Continuity manifest digest mismatch')
        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(signature, manifest)
            value = json.loads(manifest.decode('utf-8'), object_pairs_hook=_unique_object)
        except Exception:
            raise ValueError('Continuity signature or document invalid') from None
        if (not isinstance(value, dict)
                or set(value) != {'schema', 'audit_id', 'old_issuer', 'new_issuer', 'subjects'}
                or value['schema'] != 'mentorflow.identity-continuity.v1'
                or value['old_issuer'] != OLD_ISSUER or value['new_issuer'] != NEW_ISSUER
                or not isinstance(value['audit_id'], str)
                or not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', value['audit_id'])
                or not isinstance(value['subjects'], list) or not value['subjects']):
            raise ValueError('Invalid continuity contract')
        subjects = value['subjects']
        if any(not isinstance(s, str) or not s or len(s) > 128 or s != s.strip()
               or any(ord(c) < 32 or ord(c) == 127 for c in s) for s in subjects):
            raise ValueError('Invalid continuity subject')
        if len(set(subjects)) != len(subjects):
            raise ValueError('Duplicate continuity alias')
        object.__setattr__(self, 'subjects', frozenset(subjects))
        object.__setattr__(self, 'audit_id', value['audit_id'])
        object.__setattr__(self, 'manifest_sha256', digest)

    def resolve(self, external_issuer: str, subject: str, canonical_issuer: str) -> str:
        if (canonical_issuer != OLD_ISSUER or external_issuer not in (OLD_ISSUER, NEW_ISSUER)
                or subject not in self.subjects):
            raise ValueError('Account continuity is not authorized')
        return OLD_ISSUER


def canonical_principal_issuer(external_issuer, subject, canonical_issuer, continuity=None):
    # Same-issuer authentication does not need a migration assertion.
    if external_issuer == canonical_issuer:
        return canonical_issuer
    if not isinstance(continuity, SignedIdentityContinuity):
        raise ValueError('Authenticated account continuity required')
    return continuity.resolve(external_issuer, subject, canonical_issuer)


def from_env(env):
    names = ('MENTORFLOW_IDENTITY_CONTINUITY_FILE', 'MENTORFLOW_IDENTITY_CONTINUITY_SIGNATURE_HEX',
             'MENTORFLOW_IDENTITY_CONTINUITY_PUBLIC_KEY_HEX', 'MENTORFLOW_IDENTITY_CONTINUITY_SHA256')
    values = [env.get(name, '') for name in names]
    if not any(values):
        return None
    if not all(values):
        raise ValueError('All continuity trust anchors are required')
    with Path(values[0]).open('rb') as stream:
        manifest = stream.read(1024 * 1024 + 1)
    return SignedIdentityContinuity(manifest, bytes.fromhex(values[1]),
                                    bytes.fromhex(values[2]), values[3])
