# Enterprise audit policy

Enterprise audit records are serialized only through the frozen
`AuditEnvelope` contract. The envelope carries actor context (including
organization, workspace, and roles), action, resource, outcome, reason,
request ID, trace ID, and a timezone-aware timestamp.

## Required event vocabulary

The governance API supports login, invite, disable, credential revoke, role
change, document upload/delete, plan publish, Provider configuration change,
export, and access-denied events. Authorization decisions are appended before
their result is returned to a caller.

## Redaction

Secrets, credential material, authorization headers, keys, and sensitive
reason fragments are redacted before canonical JSON is stored. Metadata is
flat and bounded by the frozen contract. Logs, traces, exceptions, and public
read models must never include a Provider credential reference or plaintext.

## Integrity seam

The SQLite foundation is append-only at the table boundary and links entries
with a sequence and SHA-256 hash chain. A separately maintained chain-state
anchor records the expected entry count and final hash so tail deletion is
detectable. Verification fails on sequence gaps, changed payloads, changed
links, changed hashes, or count/final-hash mismatch.

This is a tamper-detection seam, **not** an immutable legal ledger. Production
operations must additionally protect database access, export checkpoints, and
retention policy outside this module.
