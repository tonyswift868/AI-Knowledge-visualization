# Product sync P0 closure — source qualification

Dispatch: `MF-MAIN3-02-PRODUCT-SYNC-P0-FINAL-CLOSURE-20260916-31`.
Base: `0effc3ac7373b60b4c1b2d1e0a8edbea0cadc58e`.

This continues the checkpoint's four MISSING and four PARTIAL groups. The
previous groups are frozen in `previous-gaps.json`. `domain-write-entries.csv`
lists exact business functions and mounted write routes; `entry-counts.json`
contains counts. This is the bounded scope of those existing domains, not a
claim about every route in MentorFlow. Previously WIRED active-teaching entries
are retained, not reclassified as new work.

## Closed paths

| Previous group | Final implementation and boundary |
| --- | --- |
| RESOURCE_METADATA_LEGACY | Existing sources, archive import and deletion transactions produce explicit pathless metadata. Raw text, chunks, local paths, archive scratch state and physical bytes stay local. |
| PLAN_LEGACY | Existing workspace/goal/assessment/plan/day/task/session SQLite remains. The same producer captures its allowlisted records; ORM compatibility mirrors retain their existing readers. Legacy task start/complete now delegate to the existing durable v2 commands. |
| PLAN_PRODUCT_PAGE | Mounted preview, publish, complete, shift preview/confirm and regenerate commands persist typed preview/publication/shift snapshots in the existing ORM SQLite. Rehydration reconstructs state without rerunning commands, solver or Provider. |
| REVIEW_PRODUCT_PAGE | Mounted demo campaign create/start/question/hint/answer/pause/resume/finish writes persist the accepted campaign resume DTO and typed review observations. The question bank remains the explicitly labeled demo bank; this does not fabricate a new production question bank. |
| SUPPORTED_HISTORY | LearningRun state/messages; active teaching sessions/messages/replan history; legacy teaching session payloads. ORM session/message/citation mirrors are auxiliary and excluded from restored-entity counts. |
| NONSECRET_PROFILE_SETTINGS | The accepted eight nonsecret fields have mounted product reads/writes through the existing producer. Existing profile/settings adapter and PostgreSQL regressions remain. |
| NONSECRET_DESKTOP_SETTINGS | Actual normal desktop analogy interests and task-color writes/readbacks use the local account API. No registry enumeration or whole-QSettings export. |
| MODEL_ASSET_METADATA | Actual managed/linked Vault imports record a pathless metadata intent in their existing Vault transaction, then use the same local API/producer. B reads metadata and missing-local-resource views. Explicit MODEL metadata is supported. Shared model installation remains a device cache operation, not an account-domain writer. |

The account producer owns two existing local DBs through one thin facade, not
two protocol engines. The normal child API installs both adapters. Identity
continues to come from the verified parent transport; no token crosses the
private parent/child channel. The unbound-existing-data migration guard remains.
Nothing here migrates an old profile.

## Command and failure contract

Native and frontend clients retain a logical command ID while a request is
pending. The account-bound local command receipt records the request digest and
business response before waiting for server delivery. Request bodies, uploaded
bytes and passwords are not written to that journal. Runtime snapshots and
their command receipt commit together. Existing row triggers retain delivery
intents in the original business transactions; PostgreSQL still owns CAS,
replay responses and revision ACKs.

Lost ACK replay drains the same frozen intent and returns the saved command
result without rerunning the handler. Changed input under the same command ID
is rejected. Multipart transport boundaries and ephemeral loopback ports do
not alter the logical request digest. Auth/workspace denial happens before
business mutation; unavailable delivery remains explicit pending.

Revision conflicts expose pending intent IDs through
`GET /api/product-sync/conflicts`. An explicit `keep_local` resolution must
name the currently observed server revision; a stale revision or server
tombstone is rejected. `POST /api/product-sync/refresh` obtains and validates a
fresh complete scoped snapshot after pending delivery drains. No automatic
last-writer-wins overwrite was introduced.

A process killed after a multi-transaction handler has begun but before its
response receipt is durable is reported as `PRODUCT_SYNC_COMMAND_INTERRUPTED`.
That command is never automatically reexecuted or reported synchronized; the
existing product reads remain available to inspect its durable result. This is
an at-most-once retry fence, not a claim of cross-database distributed atomicity.
The tested timeout gate is loss of the server ACK after durable local effects.

The existing 256 KiB per-record bound remains. Plan history is partitioned by
preview/publication/shift so the normal 36-task page can publish and regenerate
without rewriting an account-sized blob. An oversized or invalid projection
fails explicitly; the payload limit was not raised.

## Settings and unsupported data

`desktop/product_setting_policy.py` enumerates 10 account setting families and
28 device-local families; there are no ambiguous families in this inventory.
Eight account fields are the existing profile/settings allowlist; the two
normal desktop additions are `analogy/keywords` and task colors. Family counts
do not count each dynamic task/workspace ID separately.

Local login identity projections, remember/auto-login choices, model selection,
Provider references/configuration, tutorial position, rendering preferences,
window geometry/navigation and generated recommendation caches remain local.
The existing free-chat prompt UI explicitly promises local-only storage and
keeps that behavior. `account/display_name` is a verified-identity projection,
distinct from editable domain `display_name`.

Unsupported structured-sync payloads remain: physical source text/files, model
binaries, indexes/chunks, provider transport history/credentials, transient
generation jobs, archive/local-path previews and unrelated campaign/game/chat
domains outside the accepted LearningRun/teaching history contract. These are
not silently advertised as synchronized. Resource metadata and structured
learning evidence are distinct from their local physical payloads.

## Evidence

`test-results.json` records final unique pytest identities, frontend counts,
the preserved 141-test checkpoint, exact source hashes and the nested native
PostgreSQL protocol suite. Tests use newly owned disposable roots under
`E:/Codex/Temp/p31`; PostgreSQL is the existing isolated native harness.

Mounted product A → server → fresh B covers plan, progress, resource and account
setting. B completes a task and shifts a published plan; A restores and reads
the exact B state. A and B share no local database or files. Legacy source and
Vault resources restore as metadata with missing local bytes. A default plan
page and a full Review round exercise snapshots beyond small fixtures.

This receipt is SOURCE / native-PostgreSQL / offscreen-fixture qualification.
No GUI control, Frozen build/acceptance, production login, mail, VPS write,
old-data migration, real-user mutation, branch promotion or GitHub Actions ran.
