# E107 bootstrap payload preparation and compiled pins — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base d0360e0e579ef1b57696432eaa562c5769274fd9.

prepare_bootstrap_payload.py accepts an explicitly pinned manifest/source/tree,
requires the protected native gate in the payload, performs read-only package
verification with data/startup checks disabled, and prepares a new output directory
outside the payload on E:. Existing output directories are refused.

During archive construction, each actual streamed file is hashed against the
pinned manifest, including the manifest itself. Exact file-set checks and a
directory mutation watcher remain active during archiving. Explicit directories
are preserved. Entry/byte limits match the native extractor. Only after success
does preparation generate ReleaseIdentity.cs containing fixed archive SHA-256,
manifest SHA-256, source SHA, tree SHA and version constants. On error, partial
new scratch output may remain, but no identity source is emitted.

Source/tree values are supplied release identities, not independently attested
Git provenance. The final release build still owns that provenance check and
compiler invocation. Generated C# is build input, not a runtime JSON/sidecar trust
file. Ordinary runtime configuration cannot override these compiled pins.

Four new tests and seven preceding installer-component tests PASS: eleven tests
in 4.224 seconds. New checks prove:

- archive byte-for-byte equivalence to synthetic source files, then compilation
  of generated pins into a native .NET test host and protected extraction;
- modifying the archive after preparation and embedding that modified archive
  still fails the compiled original hash before any staging directory is created;
- mutation after initial package verification is caught while archiving, so no
  ReleaseIdentity.cs is emitted;
- invalid identity literals or manifest pin fail before output creation.

Only small synthetic test hosts were compiled. PreparedPayloadChecks.cs is not
a production installer entry; its only argument is an isolated parent, never a
hash override. No staged product code was executed. Scratch artifacts remain
under E:/Codex/Temp/e107-prepare-bootstrap-* and protected synthetic destinations
under E:/MentorFlow-TrustTest-20260906.

Still incomplete: production bootstrap build command, visible elevated installer
UI, verified native install invocation, destination/shortcut/update handling and
complete-chain long-path qualification. The legacy installer refusal stays active.
No final product package, new startup timing, dependency/lock change, push, server
mutation, formal data write or retained release change. Prior 58-case runtime
tests and historical four WinError 1314 skips were not rerun/reclassified.
<=3s startup stability and <=8s form-interactive acceptance remain unqualified.
