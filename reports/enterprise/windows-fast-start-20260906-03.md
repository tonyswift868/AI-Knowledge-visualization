# E107 Windows fast-start source progress

Dispatch: MF-E107-STARTUP-PERFORMANCE-20260906-03.
Task: MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base: 6495b3a693de2b708b6d8a0058b8b186208c969d.
Result: PARTIAL_RETURNED. Security gate target <=3 seconds is NOT met.
No final package build, push, server configuration change or formal-source import.

## Protected launch gate

The new `launch-gate` command is separate from the unchanged legacy full
`verify` command. The next-release launcher template pins the native helper,
manifest and TLS certificate before invoking it. Its current anchors still
describe the preserved old release; the helper/manifest anchors MUST be rebound
by the existing final packaging workflow. Do not copy this source template over
the old working launcher. No preserved release launcher was changed here.

Manifest payload classification (14,038 payloads; manifest separately pinned):

| Category | Count | Bytes | Policy |
| --- | ---: | ---: | --- |
| CRITICAL_EXECUTABLE | 11,204 | 1,772,685,327 | SHA-256 every invocation |
| IMMUTABLE_RESOURCE | 2,834 | 313,759,933 | SHA-256 every invocation; cache reuse disabled |
| ALLOWED_EXTERNAL_CONFIG | Outside manifest | Not a payload exclusion | Existing strict Runtime/launch.json schema/data_profile only |

All EXE, DLL, PYD, Python source/bootstrap, Qt plugins/QML, JS, archives, active
SVG, configs and unknown formats remain critical by default. The manifest lists
4 EXE, 380 DLL, 267 PYD and 7,490 Python source files. Non-executable resources
use a narrow suffix allowlist. The external TLS certificate is a separately
pinned trust anchor, NOT mutable runtime configuration.

State uses current-user Windows DPAPI authenticated encryption, with no plaintext
PASS flag or stored application secret. It binds schema, exact manifest SHA,
root path and volume/file identity, package version, every file's size/times/
identity, resource NTFS ChangeTime, exact critical hashes, and successful full
verification timestamp. The record is diagnostic only and is never consumed
to authorize hash skipping. All resources are hashed on every invocation.
In-package or aliased state paths are rejected.

Security boundary: DPAPI authenticates data for the Windows logon identity;
it is not process-origin attestation. A synthetic-package regression reproduced
an actual bypass at dc48b28: modify a same-length resource, update its inventory
metadata, and re-seal the state as the same user; the unchanged manifest anchor
was incorrectly accepted as REPEAT/PASS. Raw state-corruption tests were not
sufficient. This repair removes state-based authorization entirely. The same
forgery must now fail with a resource hash mismatch. Existing installer source
explicitly reports unsigned; no independent receipt-signing authority was found
in the examined installer/launch flow. No privileged service or new trust root
was installed. A trusted authority is required before any future cache reuse.
See [Microsoft CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata).

A single directory enumeration reuses normalized paths and stat results. Windows
DirEntry.stat returns zero file identity/link counts, so those entries receive
one real lstat; the zeros are never trusted. Reparse points, hardlinks, unsafe
paths and unexpected/missing files are rejected. An overlapped recursive
ReadDirectoryChangesW subscription starts before enumeration; any notification,
overflow/completion or observer error rejects the run. This replaces a second
walk and detects in-flight changes, not just a static file-set snapshot.
A final root file-identity check rejects replacement of the watched directory
itself; it is a stat/parent check, not another package enumeration.
See [Microsoft ReadDirectoryChangesW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-readdirectorychangesw).

Hash reads retain 1 MiB blocks. The general helper defaults to one reader.
A bounded benchmark on 272 files / 47,361,452 bytes measured serial 0.269/0.132 s,
2 readers 0.117/0.110 s, and 4 readers 0.092/0.090 s. This supports the source
personal-release profile's explicit four-reader option on this host; it is not
a claim that disk concurrency benefits every machine. Whole-package scans were
serialized, not run as competing verifier processes.

## Final-source controlled-copy evidence

### State-origin security repair (after dc48b28)

All-payload hashing restored; no cache state can authorize PASS. Same-user
re-sealing is covered by an ordinary rejection regression, not a skip/xfail.
Focused gate, personal launcher and account launch suites: 56 PASS, 0 SKIP,
0 XFAIL; exit 0. Evidence: E:/Codex/Temp/e107-state-origin-regression.xml.
Pytest data paths were pinned to scratch before collection; the worktree-local
DB before/after SHA-256 guard returned unchanged. The earlier incident below
remains disclosed. Historical four symlink permission skips were not rerun or
converted to PASS.

Current four-reader measurements on the same isolated physical package:

| Invocation | Mode | Hash | Total |
| --- | --- | ---: | ---: |
| First in this run | FULL | 6.191 s | 10.546 s |
| Unchanged repeated launch | FULL | 6.368 s | 11.687 s |

These are source-verifier timings, not packaged launch-click-to-form timings,
and not cold-cache measurements. The security target is still NOT met. No
secure cache speedup is claimed; source LoginGate timings below were not rerun.

One physical copy only:
E:/Codex/Temp/E107-FastStart-20260906-03/MentorFlow.
Copied from the preserved old release using robocopy, without hardlinks.
All mutation scenarios reused this disposable copy and restored their bytes.

Historical dc48b28 measurements, four hash readers (superseded for acceptance):

| Measurement | Inventory/state | Hash | Total |
| --- | ---: | ---: | ---: |
| First FULL with new protected state | 2.134 s | 10.022 s | 18.969 s |
| Unchanged REPEAT | 2.964 s | 4.887 s | 7.860 s |

Target security gate <=3 s: **NOT MET**. Earlier prototype measurements excluded
the corrected Windows identity/link-count handling and are not final acceptance
numbers. No strict cold-cache or idle-machine claim. Hashing ~1.77 GB of critical
payload remains material work; it was not reclassified as resource data merely
to meet the latency target.
The historical 7.860-second REPEAT result used the now-rejected cache trust
assumption and is NOT secure acceptance evidence. Current repeated launches
perform FULL verification; native-helper startup and the final packaged
click-to-form path remain unmeasured.

Final-source isolated full-copy EXE, Qt6Core DLL, PNG resource and manifest byte
tampering all rejected; unknown.cfg addition rejected. Unit tests additionally
cover restored mtime, malformed/editable cache, cache expiry, root replay, missing
files, cache path traversal, no ChangeTime fallback, late additions, real NTFS
hardlinks, single enumeration and four-reader rejection.

Evidence:
- E:/Codex/Temp/E107-FastStart-20260906-03/final-gate-results.json
- E:/Codex/Temp/E107-FastStart-20260906-03/final-tamper-results.json
- E:/Codex/Temp/E107-FastStart-20260906-03/hash-worker-benchmark.json

The retained release EXE remains
4235AB853C4EB45F848AD92F8F87943C064B0B3BE280EA2556700C6E9D98C496.

## Login source behavior and distinct clocks

MainWindow stays deferred after authentication. Enterprise frontend/business
asset preparation now runs in the post-login service-start worker, before that
service starts; Personal Local's existing order is unchanged. uvicorn is imported
only when starting the Personal Local business server, not to display LoginGate.

Real Windows Qt/WebEngine source runs, empty isolated INI settings and private
scratch data roots, fixed existing issuer/CA/SPKI policy, no credentials entered:

| Source run | LOGIN_WINDOW_VISIBLE | FORM_INTERACTIVE | Exit |
| --- | ---: | ---: | ---: |
| First observed fresh source process | 3.054 s | 9.203 s | 0 |
| Repeat fresh source process | 1.740 s | 6.129 s | 0 |

These clocks start at the Python measurement script's entry, NOT LAUNCH_CLICK.
The owned SSH tunnel was prepared separately, so SECURITY_GATE_DONE/connection
preparation are NOT included. Do not add these observations together and call
them a measured packaged launch. Editable username/password/submit DOM elements
were checked at the allowed HTTPS OIDC origin. MainWindow was absent; module
inspection found no MainWindow, app.main, uvicorn, torch or Docling before login.
Both windows exited normally; the owned SSH tunnel PID 52280 was closed.

Repeat source form <=8 s: observed PASS. Full click-to-form <=8 s: NOT QUALIFIED.
LAUNCH_CLICK and final packaged SECURITY_GATE_DONE: NOT_MEASURED this dispatch.
The next-release launcher provides a nonblank trusted OS WinForms security-status
window before helper execution; this is feedback, not an editable LoginGate.
Its source order and PowerShell syntax were checked, but the revised launcher
has not been run with a newly frozen helper (no package build was authorized).
Existing SSH/TLS preparation overlap, issuer check and trust pins are preserved.

## Regression and test isolation incident

Final focused run: **64 PASS, 4 SKIP, 0 XFAIL**, exit 0, 123.67 seconds.
Only the four historical Windows symlink cases skipped with WinError 1314.
EXE/DLL/manifest/config gates, actual hardlinks, LoginGate-first, original recent
history failure node and deferred service-generation handling remain ordinary
PASS. The 169-test login suite was not rerun.
Raw result: E:/Codex/Temp/e107-fast-final-all.xml.
Subsequent successful-full timestamp and root-replacement refinements received
3 PASS and 2 PASS focused checks respectively; these overlap the larger run and
are not added as five independent new cases.

An earlier supplemental run displayed 23 assertion passes but exited 1 because
the isolation guard detected a change to the worktree-local ignored database:
E:/Codex/Worktrees/MF-e1-account-core-e107-final/data/mentorflow_v2.db.
It is NOT an all-green result. The file has no hardlink alias to the named formal
source. No original rows were rescanned and no rollback/overwrite was attempted.
Cause: the service test changed environment variables after another test's
collection had already imported the backend's cached ORM engine. The service
test now explicitly rebinds that cached engine/legacy path to its temporary DB
and restores the prior process-local binding on cleanup. Final pytest also starts
with all data roots pinned to scratch before collection. Its before/after SHA
guard confirmed OFFICE_DB_UNCHANGED_IN_FINAL_RUN=true. This does not erase the
earlier side effect or claim the worktree-local database was never touched.

The named formal E:/MentorFlowData sources, old UAT root and backups were not
opened for migration/import/testing. No server deployment, membership, permission
or infrastructure mutation; no push; no final UAT build. This source receipt is
partial progress, not final product or performance acceptance.
