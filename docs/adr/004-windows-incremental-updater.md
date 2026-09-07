# ADR 004 — Windows desktop file-level incremental updater

Date: 2026-09-07. Dispatch: MF-E107-DESKTOP-MATURE-UPDATER-V1-20260907-04.
Decision: extend the existing desktop/update_service.py and its configured update
cache, with signed file-level releases and the existing installer transaction.
Production activation is conditional on qualified protected apply/health handling
and provisioned release trust. This is NOT a claim of mature self-update acceptance.

## Actual baseline (not the stale dispatch reference)

HEAD 4c6cf8ccc5d96dbbf97675c71b08faed7474da98;
tree d389fbb08a54a2c6dec1a3386db94cc7f3199021. Preserve the subsequent installer
recovery and 0-KiB display repairs relative to dispatch reference 9a1a643d.
Two unrelated dirty login-observation files must remain unchanged.

Existing UI: SystemPage software-update card, explicit async executor check,
download button, GitHub release link, then full Setup launch confirmation.
Existing engine: desktop/update_service.py delegates to verify_windows_package
stage_update. Version config selects GitHub, stable, E:\MentorFlowUpdates.
Existing release feed: configured GitHub release API/asset digest, not a signed
desktop release-metadata contract. Delta, download cancellation/resume, metadata
signature, previous-version health/rollback, background throttle: absent.
Existing installer Transaction does staged copy/hash/ACL/receipt commit and
interruption rollback; its successful cleanup deletes the prior program. That
default MUST NOT be called an update-health rollback implementation.
Frontend does not implement another desktop-binary updater; official_feed is
content/news, and voice release signatures are a different product namespace.

## Open-source-first qualification

| Option | Verified capabilities | Constraint / decision |
| --- | --- | --- |
| Velopack 1.2.0 (latest release observed) | Windows Python SDK, UpdateManager, channels, binary delta, restart; PyInstaller onedir supported. Current docs provide --installto, MSI PerMachine and VELOPACK_INSTALLDIR. | Do NOT claim custom paths or per-machine installs are unsupported. Its current/Update.exe/stub/package layout, lifecycle hooks and package ownership differ from App/installation-receipt.json plus existing ACL/NSIS trust. In-place coexistence and ordinary-user apply into this protected layout are NOT runtime qualified. Adopting its installer/layout now would change the preserved product contract. Defer migration, not a general rejection of Velopack. |
| MSIX/AppInstaller | OS-managed signed packages, block-oriented deployment, update/repair settings, Windows 10/11 support subject to OS/version settings. | Changes package identity, registration, location/virtualization and deployment ownership. PackageVolume is configurable, so not a claim that all MSIX must live on C:. Does not preserve the present custom NSIS transaction/receipt/shortcut contract without migration; whole-product migration is explicitly out of scope. |
| Existing NSIS + exact manifest | Already owns selectable protected App root, staging/verify/commit/recovery and program-only boundaries. | Choose FILE_LEVEL_INCREMENTAL, not block delta. Reuse unchanged local files after hashing, fetch only new/changed objects. Reconstruct then invoke a narrowly qualified apply bridge; never call the first-install wizard for updates. More maintenance/security work; release only after missing apply/health/UI seams qualify. |

Official sources inspected (2026-09-07; live docs are not pinned SDK runtime tests):
- https://github.com/velopack/velopack/releases/tag/1.2.0
- https://docs.velopack.io/getting-started/python
- https://docs.velopack.io/packaging/operating-systems/windows
- https://docs.velopack.io/packaging/installer
- https://docs.velopack.io/packaging/deltas
- https://docs.velopack.io/packaging/channels
- https://learn.microsoft.com/en-us/windows/msix/app-installer/auto-update-and-repair--overview
- https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes

No Velopack/.NET/MSIX tools or dependencies installed; no lockfiles changed.
Cryptography 46.0.7 and HTTPX 0.28.1 are already locked and available. Ed25519 uses
the existing cryptography library, never a custom cryptographic implementation.

Explicit qualification: VELOPACK_PYTHON_SUPPORTED=YES;
VELOPACK_CUSTOM_INSTALL_LOCATION_SUPPORTED=YES;
VELOPACK_PER_MACHINE_MSI_SUPPORTED=YES.
VELOPACK=QUALIFIED_MATURE_REFERENCE_BUT_NOT_ADOPTED_IN_V1.
Concrete migration cost: replace native entry/stub pins, change App-relative
resource loading to current, migrate receipt/shortcut identities and pending
transaction recovery, adopt package feed/retention and retest custom NSIS
directory/cancel/UAC/ACL semantics. No in-place interoperability test proves
those changes safe in the current layout; capability documentation is not that test.

## Download transport qualification (continuation, same dispatch)

V1_TRANSPORT=HTTP_RANGE, behind UpdateTransport Protocol (metadata fetch, bounded
stream headers/bytes, origin validation, received-body byte accounting). Core
signature/manifest/cache/transaction logic does not depend on HTTPX internals.

| Qualification point | Current HTTP Range | Windows BITS candidate |
| --- | --- | --- |
| Bandwidth / cost | Bounded streamed objects; no automatic idle-bandwidth or metered policy in V1; manual download only. | OS congestion/idle-bandwidth and cost-aware transfer policies. |
| Disconnect / application exit | Partial/completed objects persist; retry continues next invocation. No network job continues after App exits. | Service-owned jobs can outlive the creating App and resume interruptions/reboots, subject to owner logon/network policy. |
| Reboot | Cache resume after next App launch; not continuous background service. | Durable job state managed by BITS; qualification must test job-owner logon and cost policy. |
| Python / COM / frozen Qt | Existing locked HTTPX; no Qt blocking. | Requires per-thread COM initialization, job-GUID persistence/reconciliation, cancel/Complete semantics and PyInstaller COM packaging; never call COM synchronously on Qt main thread. |
| Privilege | Ordinary-user cache only; apply separately elevated. | Create user-owned jobs to user-accessible cache; RunAs alone is not interactive logon. BITS service presence is not authority to write protected App; apply remains separate. |

BITS_QUALIFICATION=PRODUCTION_CANDIDATE/FOLLOW_UP, documentation only, not runtime
tested, no BITS jobs created. Do not rewrite Range for this bounded qualification.
Official references:
- https://learn.microsoft.com/en-us/windows/win32/bits/background-intelligent-transfer-service-portal
- https://learn.microsoft.com/en-us/windows/win32/bits/best-practices-when-using-bits
- https://learn.microsoft.com/en-us/windows/win32/bits/users-and-network-connections

## Anti-replay and release operations

Signed metadata_version is monotonic per app/channel; issued_at/expires_at bound
freshness (maximum signed lifetime 31 days). The client stores a signed high-water
witness before returning a release, rejects older epochs and rejects changed
signed content with the same epoch. It reloads this witness on download/apply and
across service restart. This addresses hostile/stale network feed replay, not
malicious local-user deletion/rollback of their whole update-cache profile.
The source helper additionally stores an independently signed high-water witness
under protected .install-staging/update-metadata/<channel>.json. Fixture tests
prove that deleting the ordinary cache does not reset this helper floor. Actual
elevated production ACL/build/rotation deployment remains NOT_RUN; production
apply stays disabled without build-provisioned trust. No whole TUF hierarchy added.

Key rotation: build-provisioned overlapping public-key IDs; envelope key_id must
match signed key_id. Never accept a key embedded in the feed. Old witness public
verification material must remain available during rollover. Revocation/recovery
requires reviewed trust-bundle deployment; no unsigned reset switch exists.

Signed rollout_percentage/rollout_seed/version produce a deterministic per-install
cohort locally, without transmitting account/workspace IDs. withdrawn prevents
download/apply from the observed metadata; a higher epoch must be used for any
withdraw/reissue/cohort change. Offline clients cannot know about a newly issued
withdrawal until recheck; expiry bounds that window, not instantaneous revocation.

## Architecture and release boundary

One existing update-service facade/cache root. Per-install subdirectories and OS
locks, signed stable/beta envelope, SHA-addressed partial/completed objects. Local
HTTP fixture is constructor-injected only; deployed URLs must use HTTPS, pinned
origin, no credentials/redirect downgrade. Public trust is build-provisioned, not
accepted from the downloaded release or user-writable cache. No production key or
endpoint is invented. Signature failure forbids any full fallback.

The file-set-v1 full release is the complete set of immutable target objects, not
Setup.exe. Incremental uses the subset missing from the valid local base. Full
fallback fetches the full set but follows identical staging/verification, never
uninstall/reinstall. Deletions are omissions in the new staging tree only.
Cache checks/download/reconstruction are unprivileged and never mutate live App.
Same-volume protected staging/apply and final signature validation must happen
in a separately qualified elevated helper after explicit restart consent.

Updater objects/check/high-water state stay in the update cache, outside the
binary tree and learning/account data. Exceptions use the existing Python logger;
this change does not configure a new persistent log sink. Production durable log
routing/rotation remains unqualified. Existing desktop preferences use QSettings;
this round does not migrate or certify every legacy App storage path.
Mandatory releases require security/protocol/data-compatibility reason; they do
not introduce a new login/startup blocking gate here. Signed SemVer prevents
production downgrade. Stable UI only, beta via build-provisioned advanced config.

## Migration risk / rollback plan

No installer or existing installed payload is replaced this round. Source UI must
not silently fall back to the legacy Setup workflow when signed feed is absent.
Keep legacy explicit checker API for source compatibility, not default UI action.
Production feed activation requires review of pinned keys, helper independent
reverification, UAC/App-close handoff, retained previous known-good, installed
receipt/shortcut metadata, first-launch health and restart under ordinary identity.
Any partial bridge remains disabled instead of pretending a staging success is
an installed update. Restore the previous source entry if integration regresses;
do not modify user data, overwrite unknown files, or reset unrelated work.
Keep all existing installer evidence immutable. This ADR can be amended by
qualification evidence, not by weakening E:\MentorFlow ACL or root requirements.
