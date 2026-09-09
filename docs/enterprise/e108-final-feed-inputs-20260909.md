# E108 final feed binding and build inputs

Continuation of the clean 90591921f2efa893d1e0e65fcaf7619fb69b03b3
integration (tree 87408f95624e6962d30dbfb9dbaba88985ba6d2b).
No delivery is reapplied. This is final build/configuration wiring, not staging
deployment or production release approval.

## Explicit public mount

Signed UPDATE_FEED_URL is exactly
`https://89.45.45.28/mentorflow/updates/e108/`.
The one-click core resolves directory bindings to `stable/stable.json`; legacy
file bindings remain unchanged. It does not GET a directory, probe alternate
URLs, use a browser, or start a local server. Signed metadata aliases agree.

The publisher accepts a validated literal HTTPS path prefix. Its existing
signature/epoch/file-set protocol is unchanged. Local publisher output lives
under `feed-for-04-publication/releases`; immutable objects and version documents
map to public `releases/`. The TARGET envelope is copied to `stable/stable.json`.
Only the generated public allowlist may be uploaded, immutable files first and
the channel pointer atomically last. Internal epoch state and signing material
must never be uploaded. This conforms to 05's immutable `releases/` and no-cache
`stable/` service policy without any server reconfiguration.

## BASE and TARGET

One clean committed source compiles both the real App and native UserRuntime.
No seed App EXE is reused. Only hash-verified dependency files/TOCs are reused;
all packaged project modules and entrypoints are compared with current source.
Compiled public trust and non-fixture configuration are checked too. All native
consumer manifests must be asInvoker.

BASE is 1.4.0. TARGET is 1.4.1 and adds installed release details in About,
available offline, plus a functional Copy version information action. This
copies only version/channel/release prose, never update configuration, account
secrets or local paths. It is actual UI behavior, not a health/test marker.
The common EXE can be identical: signed version resources enable TARGET's new
section; the signed incremental manifest lists every changed file.

Inno 7 compiles the exact signed BASE and remains the only user-facing installer:
`E:/Downloads/MentorFlow-E108-User-Test-20260909/Installer/MentorFlow-Setup.exe`.
Existing parent validation, parent/MentorFlow layout, per-user policy, launcher,
one-click apply/relaunch and next-launch recovery are retained. No App, installer,
UAC or visible window may be launched during this stage.

## Private handoff and evidence

The builder emits `E108_FINAL_RELEASE_INPUT.json`, `E108_PUBLIC_UPLOAD_MANIFEST.json`
and `FINAL_OFFLINE_RECEIPT.json` inside its unique private work directory. They
include absolute App/manifest/installer paths, hashes, counts including the
manifest itself, exact source identity, public keys, a private signing-key
reference, release expiry, commands and the publication mapping. 04 can deploy
the already signed feed or reproduce it without rebuilding either App. Keep
the same key: the BASE trusts that compiled authority, not a newly invented key.

Offline completion is not live HTTPS consumer qualification. The latter remains
04's next deployment gate. No remote push, Actions run, server mutation or
Authenticode signature is claimed. Historical artifacts and storage are preserved.

Pre-build focused verification: 67 passed (new mount/allowlist/UI-copy tests,
one-click integration, release transport boundary, path budgets and next-launch
wiring). Evidence: `E:/Codex/Temp/MF-E108-HTTPS-Final-Tests-01.xml`.
One existing certifi/cryptography certificate-serial deprecation warning; no
verification bypass or dependency mutation. Final artifact verification is
emitted by the build, not inferred from these source-level tests.
