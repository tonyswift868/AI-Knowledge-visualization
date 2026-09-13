"""Disposable offline signer; never an operator key or deployable approval."""
import hashlib
import json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from app.enterprise.identity.continuity import SignedIdentityContinuity
from app.enterprise.identity.issuer_migration import OLD_ISSUER, NEW_ISSUER


def signed_mapping(subjects=('account-001',), **changes):
    value = dict(schema='mentorflow.identity-continuity.v1', audit_id='TEST_ONLY-disposable',
                 old_issuer=OLD_ISSUER, new_issuer=NEW_ISSUER, subjects=list(subjects))
    value.update(changes)
    raw = json.dumps(value, separators=(',', ':')).encode()
    key = Ed25519PrivateKey.generate()
    args = (raw, key.sign(raw), key.public_key().public_bytes_raw(), hashlib.sha256(raw).hexdigest())
    return args


def continuity(subjects=('account-001',)):
    return SignedIdentityContinuity(*signed_mapping(subjects))
