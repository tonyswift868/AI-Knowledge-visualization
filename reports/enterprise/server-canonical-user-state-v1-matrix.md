# Server-canonical user state V1 matrix

Authority: `integration/main3` `ce184db76cb6530f944668d53d08bef1a264ee1c`.
This matrix records the accepted V1 boundary. It does not import or inspect the
legacy 88 plans/83 progress records.

| State | Status | Write path | Read/rehydrate path | Stable ID/revision | Delete/idempotency/conflict |
|---|---|---|---|---|---|
| plans | SERVER_CANONICAL_ALREADY | `PUT /api/enterprise/sync/v1/entities/plan/{id}` | snapshot | entity id / CAS revision | tombstone / request ledger / stale 409 |
| progress/mastery | SERVER_CANONICAL_ALREADY | progress entity write | snapshot | entity id / CAS revision | tombstone / request ledger / stale 409 |
| resource metadata | SERVER_CANONICAL_ALREADY | resource entity write | snapshot; local bytes may be missing | resource id / CAS revision | tombstone / request ledger / stale 409 |
| plan tasks/calendars | MISSING | none | none | none | none |
| review/FSRS | MISSING | none | none | none | none |
| learner/student profile | LOCAL_ONLY | existing account/workspace bootstrap | bootstrap only | account identity seam | account authority; not sync V1 |
| learning runs | MISSING | none | none | none | none |
| supported teaching/chat history | MISSING | none | none | none | none |
| non-secret account/workspace settings | PARTIAL | workspace/account services | bootstrap/settings seams | workspace scope | owner-specific; no sync entity yet |
| model metadata/source/hash | LOCAL_ONLY | model registry/provider seams | model metadata only | provider/model id | no user object bytes |
| stable resource IDs | SERVER_CANONICAL_ALREADY | resource entity references | snapshot inverse plan references | caller-stable validated id | tombstone and CAS |

The server writes are synchronous transactions with `synchronous_commit=on` on
PostgreSQL, scoped by the existing authenticated Actor/Tenant seam and RLS.
The request ledger makes identical retries return the original committed result;
stale revisions are explicit conflicts. Snapshots contain live rows and explicit
tombstones, so an absent row is never interpreted as deletion. Device-local file
availability is a separate observation and cannot hide a server plan/progress row.

## Object-storage boundary

User file bytes belong in an S3-compatible object store: PDF, presentations,
documents, images, ordinary audio/video and attachments. PostgreSQL stores only
resource ID, tenant ownership, filename, MIME, size, SHA-256, object key,
revision and state. Public/re-downloadable model binaries default to no user
object-storage upload; only model metadata/source/hash is retained. No custom
chunk protocol is accepted; boto3/Transfer Manager multipart is the required
adapter. MinIO/S3-compatible disposable tests are the acceptance fixture, with
R2/S3 supplied by configuration for production.

## Acceptance boundary

The existing candidate proves single-writer A commit → A disappears → B snapshot
rehydration for plan, progress and resource metadata, including idempotency,
CAS conflict, tombstone propagation, workspace bootstrap and tenant denial.
It does not prove the rows marked MISSING/PARTIAL, object-storage upload/download,
or real-user migration. Those are explicit follow-up gates rather than inferred
PASS from the presence of unrelated tables.

`NEW_DEVICE_STRUCTURED_REHYDRATION = PASS` for the accepted three-entity V1
scope. `SERVER_SCOPE_MATRIX = COMPLETE_FOR_ACCEPTED_V1` means every requested
category above has an explicit status; it is not a claim that MISSING categories
are implemented.
