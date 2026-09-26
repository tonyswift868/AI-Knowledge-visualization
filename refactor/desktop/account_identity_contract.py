"""Stable account-key projection; never an authentication or tenant credential."""
import hashlib
import re


def account_id_from_hashes(issuer_hash: str, subject_hash: str) -> str:
    if any(not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{64}', v)
           for v in (issuer_hash, subject_hash)):
        raise ValueError('Canonical account hashes required')
    value = 'mentorflow.account.v1:' + issuer_hash + ':' + subject_hash
    return 'mfa1_' + hashlib.sha256(value.encode('ascii')).hexdigest()


def account_id(issuer: str, subject: str) -> str:
    if any(not isinstance(v, str) or not v or v != v.strip() for v in (issuer, subject)):
        raise ValueError('Canonical account anchor required')
    return account_id_from_hashes(*(hashlib.sha256(v.encode('utf-8')).hexdigest()
                                    for v in (issuer, subject)))
