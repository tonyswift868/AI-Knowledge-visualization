# MF-R1-04-DATA-REPAIR

Base: `0536acb33a3a6694f71aa0dcd7074060b28e7f98`, branch `worker/main3/04-data-r1`.
Old `bf8c359` audit was consulted and its read-only projector reused on new snapshots;
no historical dispatch resumed or whole commit cherry-picked.

## Reproduced and repaired

1. `product_command_boundary.invoke` flushed the complete upload queue before and
   after GET. An unrelated conflicting/invalid item therefore prevented reading
   committed local learning state. GET also re-journaled plan runtime state.
   Read-only methods now authorize, verify the local account/workspace binding,
   read without flushing/re-journaling, and report the actual pending/conflict
   status in the response header. Writes and explicit refresh retain their
   existing durable-commit and pending-change protections. This does not claim
   that failed writes are saved to the server or silently resolve conflicts.
2. The existing V1 package executor restored an existing device-local asset ID;
   an empty new device has only the server resource ID. Added
   `LocalAssetLifecycle.restore_resource_from_package(resource_id, package,
   fingerprint_sha256=...)`, using the existing archive verifier, account
   fingerprint check, managed-object store and local resource binding. It
   restores only the exact matching original file, preserving the server ID and
   hash, without applying packaged SQLite/settings or replacing server state.
   Wrong account/hash and conflicting local revisions are rejected.
3. Recovery through `import_managed` would register a new device-local ID as a
   second cloud USER_FILE when the metadata publisher was active. Recovery now
   uses an explicit local-only import; ordinary new uploads keep their existing
   default publication/retry behavior. The existing verified-HTTPS reacquire
   path receives the same correction, including for local model resources.
4. The existing recovery page can now bind that current-account lifecycle and
   execute its per-file package button. The authenticated server projection
   supplies the resource SHA256. Account/generation changes while the native
   file picker is open discard its result. Global Portable Pack V2 remains
   unchanged; this is the existing V1 original-file recovery path.

## Composition handoff to 03 / main dispatcher

`desktop/main.py` and authentication were not edited. After the recovery page's
existing `authenticated(canonical_account_id)` call and current Vault mount,
the composition root needs this call with its existing lifecycle instance:

```python
rehydration_page.bind_resource_recovery(current_account_asset_lifecycle)
```

The lifecycle's Vault account key must match the page account. Do not bind a
personal-local or previous-account instance. The existing `present()` publication
supplies the SHA256 and file classification; no second sync request is needed.
The package button remains disabled until the lifecycle is bound. The original
Full Portable Export in `system_page.py` supplies `.mentorflowpack` files; a
Quick export without raw bytes cannot restore them. The helper does not turn a
personal-local package into an enterprise package by changing its owner.

Source/fixture verification is complete for this seam. Packaged/main composition
and real user migration are not claimed complete. Large archive recovery uses
the existing synchronous local recovery pattern; background UI orchestration can
reuse the composition root's existing data-action runner.

## Current data and exact migration boundaries

`MIGRATION_INPUTS.json` binds current read-only snapshots, counts, hashes, source
paths and the Owner-authorized stable target supplied by 01. PRIMARY currently
still has 530 workspaces / 88 plans / 330 source resources. Its explicit local
owner group has 529 workspaces / 83 plans / 330 resources / 83 progress records.
The 1 unowned workspace / 5 plans / 797 execution tasks and 55 dangling ownership
references remain separately retained. Development/test accounts were not
automatically merged into the canonical administrator.

Reuse the existing personal enterprise organization/workspace as transport scope;
preserve ORM business workspace/plan/task/resource IDs inside typed product records.
Do not confuse the 529 ORM workspaces with 529 enterprise identity memberships.
SECONDARY/LEGACY are comparison sources, not a second append batch.

The current source was tested through the existing projector across 46 registered
ORM record types. All declared SQLite foreign keys and seven explicit core joins
closed. 56 rows still fail `PRODUCT_SYNC_RECORD_TOO_LARGE`: 17 calendars, 35 replan
histories and 4 activation records. Compact UTF-8 serialization leaves the same
56 failures (largest about 4.9 MB), so this is not just ASCII/Chinese escaping.
No limit was raised, record truncated or opaque payload routed around validation.
The exact rejected row-key hashes and field-count evidence are retained in the
protected D scratch `projection-audit` directory. The prior normal-source
`PRODUCT_SYNC_EXISTING_DATA_REQUIRES_MIGRATION` safeguard is retained.

The real server's read-only observation was schema `0036_account_import_ledger`
at the time in the manifest; this branch expects 0040. **01 alone owns that
upgrade.** No schema/identity/server data writes were executed by 04.
The rename to `tonyswift` must preserve the supplied subject/account key;
the data target no longer requires another Owner confirmation of that candidate.

To continue the concrete existing intake: use the pinned PRIMARY clone and its
primary owner allowlist, retain business keys, apply current typed projectors to
the existing canonical scope, and keep source/backups untouched. The oversized
historical aggregates require an explicit lossless domain representation before
that intake can fully pass; this delivery supplies the rejected set rather than
inventing a new importer. Actual writes remain with the coordinating executor.

## Model boundary

`LocalAssetLifecycle.reacquire_resource` already verifies HTTPS download bytes
against the supplied hash and now cannot publish a duplicate USER_FILE. A
synthetic model download verified that the server stable ID remains unchanged
and no cloud metadata is published. No real model was downloaded/uploaded.
The generic recovery projection still has no trusted model URL/catalog binding,
and the existing bounded file downloader is not a general multi-GB model
installer (2 GiB limit). A TTS-specific build download script is not a general
product model manager. Model relinking remains available in the existing local
resource page; no fake working model-download button or new training platform
was introduced.

## Validation

- Read boundary: 5 unit cases, including bad queue, authorization rejection,
  stale local account binding, and write protection; the defect was red before
  the fix (3 errors plus double-flush failure).
- Native disposable PostgreSQL: 4 focused cases passed (actual conflicting
  SQLite queue/read, independent B restore, lost ACK retry, populated-source
  migration refusal). The two affected read/rehydrate cases were rerun after
  the final local-binding check. No real server was used for these tests.
- Local asset lifecycle: 11 passed, including A closed/original removed -> empty
  B package recovery, stable ID/hash, replay, wrong-account/hash refusal, and
  model reacquire with a publisher that rejects any cloud write.
- Recovery UX/transport: 63 passed, including account switch during file picker.
- Existing new-upload metadata/retry behavior: 4 passed. An unrelated model-label
  test needs unavailable `qtbot`; it was not claimed passed.
- `git diff --check` clean. No build, GitHub Actions, real migration or secret
  values in output. Tests and SQLite copies reside under
  `D:/CodexScratch/MentorFlow/MF-R1-04-DATA-REPAIR/04`; temporary PostgreSQL stopped
  and removed. Current read-only snapshots are retained with restricted ACL for
  coordinator continuation.

## Residual dispatch decisions

Main composition hookup, the 01 server upgrade, and trusted generic model
catalog/installer selection remain with their owners. Primary identity has been
resolved and is not re-gated here.

**待调度答复：** 对无本地归属记录的 1 workspace / 5 plans / 797 execution tasks，
本轮继续单独保留；若需一并恢复，请主调度明确这组确属用户资料还是开发 fixture。
其余 primary group 不需要重复 Owner 归属确认。56 条超大历史聚合的工程修复
仍须按上述精确记录集合继续，不能用“仅差 Owner 确认”掩盖。
