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

Break-glass request, approval, activation, expiry, revocation, and elevated use
are separate audit actions. Cross-tenant and relationship denials remain
records even when the protected mutation or read returns no business data.

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

## Query and export

`AuditQueryService` authorizes each query, then filters by organization,
workspace, actor, action, resource type, outcome, and inclusive time range.
The result is a minimum public projection: actor ID, tenant IDs, action,
resource identity, outcome, reason, time, and trace. Authentication tokens,
session IDs, request IDs, raw envelopes, and arbitrary payloads are omitted.

Auditors are read-only. They may query and export audit data, because export is
a read operation over the audit resource, but they cannot mutate membership,
workspace, provider, quota, or content resources. `AuditExportService` performs
an independent `audit.export` authorization that is itself appended to the
audit chain. Its JSONL receipt contains only the redacted query projection and
a SHA-256 digest.
