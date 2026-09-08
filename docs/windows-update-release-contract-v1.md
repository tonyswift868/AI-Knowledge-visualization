# MentorFlow signed file-release feed v1

Static hosting contract only; no backend or production feed deployed.
Service entry remains desktop/update_service.py. Normal in-app updates must not
call legacy launch_verified_installer or a first-install NSIS wizard.

## Paths and authenticity

`/stable.json` and `/beta.json` are channel indexes containing the signed release
envelope, served Cache-Control: no-cache/must-revalidate. Versioned envelopes and
`/objects/<sha256>` are immutable. Support Range/206 and accurate Content-Range,
Content-Length; if ignored, return 200 and the full current object only. Never
redirect assets to arbitrary origins; V1 requires the same configured HTTPS
origin, identity encoding, and no embedded credentials/query secrets. Local HTTP
is test constructor injection on literal loopback only, not a production setting.

Envelope has exactly `signed`, `key_id`, `signature` (base64 Ed25519). Signature
bytes cover UTF-8 JSON of `signed`, sorted keys, no whitespace, Unicode unescaped,
finite values only, ending in LF. Duplicate keys are rejected. Client verification
keys come from trusted build configuration, never from a feed/cache/CLI key switch.
Private release keys remain outside repo, client package and logs. Test keys are
ephemeral memory-only keys; test clients receive only public verification material.

## Required signed fields

| Field | Contract |
| --- | --- |
| schema_version | integer 1 |
| metadata_version | monotonically increasing positive integer <= 2^53-1 per app/channel; same epoch cannot change content |
| issued_at, published_at, expires_at | timezone-aware ISO8601; publication <= issue; expiry after issue/now, <=31-day lifetime; issue not >5 minutes future |
| app_id, channel, version | MentorFlow, stable/beta, strict SemVer; stable refuses prerelease; downgrade refused |
| min_supported_version, min_updater_version | supported base/updater SemVer; unknown updater contract fails safely |
| mandatory, mandatory_reason | bool; true requires security/protocol/data_compatibility, never ordinary feature pressure |
| release_notes | plain text <=64KiB, not executable markup |
| key_id, signature_algorithm, hash_algorithm | provisioned ID matching envelope, ed25519, sha256 |
| source_sha, tree_sha | exact 40-lowercase-hex build identities |
| base_versions | list of supported incremental source versions |
| file_manifest | existing mentorflow.windows-package-manifest.v1 exact program inventory; canonical bytes become SELF-CHECK/package-manifest.json |
| full_release | format=file-set-v1, size=sum target files including manifest, assets=[path,size,sha256,url] covering exact target set |
| incremental_assets | target object paths available for incremental reconstruction; new/changed files selected from manifest comparison |
| rollout_percentage, rollout_seed, withdrawn | integer0..100, stable seed1..128 characters, bool |

The entire asset list is signed (`full_release.assets`), not a second unsigned
asset registry. Target paths bind hashes/version/app/channel. Windows traversal,
ADS/device/reserved paths, case duplicates, file/directory conflicts and user-data
namespaces are rejected. A target manifest cannot contain itself; it is generated
canonically from the signed object and participates in final reconstruction hash.

UNCHANGED files are locally hashed and copied (not hardlinked) into independent
staging. NEW/CHANGED files download by immutable object; DELETED files are merely
absent in staging. No live deletion occurs. Missing/corrupt old files can be
re-fetched from the signed full inventory; this is repair reconstruction, not
permission to apply same-version repairs without a separately authorized bridge.

## Selection, fallback, bytes and freshness

Default path FILE_LEVEL_INCREMENTAL, never BLOCK_DELTA. If base unavailable,
unsupported or changed size>=full, select FULL_FALLBACK, downloading the complete
file set with completed-object deduplication. A full set is not Setup.exe, and both
paths use the same transaction. Corrupt signed metadata/asset hash is FAILED_SECURITY;
no fallback may turn it into an accepted install. Transport failures retain partials
and completed objects; retry restarts at most the current object when Range is absent.

FULL_BYTES is target logical payload length; ACTUAL_DOWNLOADED_BYTES counts actual
HTTP body bytes read for assets during the reported operation, excluding metadata,
HTTP/TLS framing and prior attempts. SAVED_BYTES=max(0,FULL_BYTES-actual), percentage
uses that denominator. Reports must separately identify prior attempts/prefetch,
completed-object reuse, synthetic full size and the real shipped full baseline.
Never extrapolate a 5MB fixture's measurement to a measured 2.1GB saving claim.

Highest-seen signed envelope is saved per install/channel and checked before use.
Expired release cannot install, even with a valid signature. Removing/rolling back
the entire user cache is outside the client's network anti-replay proof. The source
helper independently preserves a signed witness in protected
`.install-staging/update-metadata/<channel>.json`; fixture replay checks cover this
separate floor, while real elevated ACL/build/rotation deployment must still qualify.
Withdrawals use a higher epoch;
offline clients learn withdrawal on next check, not magically at publication time.
Future trust rotation uses overlapping build-provisioned IDs and retained old
witness verification keys; no downloaded key becomes trusted automatically.

## Apply, health, cleanup and current qualification boundary

Elevated helper must reverify signed metadata and the exact reconstructed manifest,
disk space, source identity and installed receipt, then use existing Transaction
with retain_previous=True under a lock. Cache is untrusted input. It does not grant
authority to arbitrary copy/execute paths or data migration. User UAC/ordinary-user
restart handoff is not yet provisioned in the production UI.

Previous journal/tree lives under existing `.install-staging/previous/<uuid>`;
this namespace is excluded from active file identity but still ACL/link checked by
the existing trust gate. Installer defaults preserve prior behavior. Update-specific
cleanup must never clean failed E:\MentorFlow while updating another root.

Required future health handshake: started executable hash/version matches signed
target; receipt matches; fresh launch nonce; LoginGate/main-ready signal; no early
fatal exit. pending_update_health remains true until this is independently observed.
Previous is NOT immediately deleted. Explicit elevated rollback_previous is an
executable source recovery path. The source first-launch observer detects a real
fixture process exit/timeout and checks PID/version in a ready signal; it cannot
mark the protected receipt healthy. Automatic product crash rollback, UI-on-exit
handoff and production protected high-water deployment are unqualified, not PASS.

Stale-cache retention policy: remove only journal/object-index-owned expired
partials and nonreferenced assets after current/previous health is known. Unknown
files preserved. This round intentionally retains fixture evidence and does not
implement broad automatic cache deletion. Learning/account/import/upload data
is never a member of either update transaction or updater cleanup.
