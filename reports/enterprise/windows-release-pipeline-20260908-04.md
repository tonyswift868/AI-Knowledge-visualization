# Windows release feed/signing pipeline — Employee 04

Dispatch: `MF-E108-RELEASE-PIPELINE-20260908-01`

Task: `MENTORFLOW_WINDOWS_RELEASE_FEED_AND_SIGNING_PIPELINE_V1`

Status: implementation and scoped acceptance **PASS**; no deployment performed.

`PRODUCTION_SIGNING = NOT_CONFIGURED`

## Frozen implementation

- Worktree: `E:\Codex\Projects\MentorFlow-release-pipeline-20260908`
- Branch: `codex/windows-release-pipeline-20260908`
- Base: `0b766ca1cc177f9de5bc9512047a05cbf8f8cd8f`
- Implementation commit: `cad7469c7e17a72913dc69f62f14a823cb0ada57`
- Implementation tree: `65a51a9ac18985957bfe9c049bda0fdda659ee54`
- Entry: `scripts/build_release.ps1`; implementation: `scripts/build_release.py`.
- Operator instructions: `docs/windows-release-pipeline.md`.

This is an isolated additive publisher change. Existing updater, installer,
dependency manifests, locks, shared contracts, and other workers' files were not
edited. The original worktree's two existing changes to
`scripts/measure_installed_login_gate.py` and `scripts/test_login_form_probe.py`
were preserved. Its branch advanced independently to
`736c7cc2ac87e245e05ddad5e336f92ed8dc1d4d`; no branch-tip merge was performed.
The release protocol, downloader, and source reader have identical Git content
between that observed commit and this change's base. New per-user consumer
integration is not included in this publisher change; beta publication/download
is verified, not that independent adapter's beta apply behavior.

## Requirement evidence

| Requirement | Authoritative evidence |
| --- | --- |
| One repeatable command; no manual JSON | `test_external_key_and_powershell_single_command` invokes the actual PowerShell command twice with an explicit test-only external PEM; second result `REUSED`, unchanged signed index |
| Packaged target, version, channel, notes | `build_release.ps1` parameters; package/version identity checked before publication; input package is not rewritten |
| Stable and beta | `test_beta_independent_channel`, strict stable prerelease rejection, existing signed beta downloader tests |
| Signed metadata, all required fields | Actual `release.json` is verified by unmodified `desktop.update_protocol.verify_release`; Ed25519 covers app/channel/SemVer/timestamps/minimum versions/mandatory policy/epoch/expiry/hashes/full fallback |
| Manifest, incremental and full assets, checksums, notes | `test_artifacts_and_repeat_noop` independently checks every SHA256SUMS entry and canonical manifest; actual demo tree below |
| Existing file-level incremental, not binary diff | `MatureUpdateService` / `make_plan` / `ObjectCache` unchanged; changed files reference immutable whole-file SHA-256 objects; old manifest auto-selected from verified previous channel |
| BASE → TARGET, delta/full | `test_base_target_static_http_delta_and_full` and `test_published_feed_applies_through_existing_transaction`, both parametrized for two paths; real bytes/hash/copy/swap/receipt, synthetic ACL/registry/shortcut adapters |
| Corrupted asset | `test_corrupted_asset_fails_closed`: `FAILED_SECURITY`, no staged apply target |
| Tampered metadata | `test_tampered_metadata_fails_closed`: invalid signature is rejected |
| Expired metadata | `test_expired_metadata_fails_closed`: genuinely signed expired envelope is rejected |
| Downgrade | Consumer and publisher downgrade tests both reject |
| Stale cached update | `test_stale_cached_update_after_restart` saves a newer witness then replays the old signed feed into a restarted client; rejected |
| Publisher freshness and interruption | Automatic epoch/high-water test; immutable version retained on simulated index-publication failure, exact retry completes; concurrent writer lock refuses |
| Signing hook, no counterfeit production trust | `sign_release.ps1` requires an existing private code-signing certificate, valid system trust chain, verified SHA-256 signatures/timestamps; missing certificate test aborts; mocked hook test proves hash-after-sign and no re-sign on retry only |
| Direct static hosting | Demo served by unmodified `SimpleHTTPRequestHandler`, actual existing updater downloaded delta and full successfully; no source asset-map API |
| No prohibited external operations | No GitHub Actions, push, Release, upload, production deployment, VPS change, dependency installation, or Provider call performed |

The implementation also authenticates the operational build receipt separately
and verifies all immutable documents on reuse. Private keys are not included in
output. Default unsigned Windows payloads are never described as Authenticode
trusted just because their release metadata is signed.

## Executed tests

Interpreter: `E:\aiPlay\AI-Knowledge visualization\.venv\Scripts\python.exe`;
existing cryptography `46.0.7`, pytest `8.4.2`. Imports resolved to this isolated
worktree, not another installation. No packages were installed or upgraded.

```text
python -B -m pytest -q scripts/test_build_release.py desktop/tests/test_mature_update.py
  -k "not qt_event_loop and not settings_card and not no_feed_ui"
  --basetemp=E:\Codex\Temp\MF-release-pipeline-regression-20260908-06
  -p no:cacheprovider
  --junitxml=E:\Codex\Temp\MF-release-pipeline-regression-20260908-06.xml

74 passed, 3 deselected in 75.33s
```

All 30 publisher tests ran; 44 existing protocol/download tests ran. The three
deselected tests concern Qt/UI behavior outside this publisher task. Python AST,
both PowerShell parser checks, and Git whitespace checks passed.

JUnit SHA-256:
`B87F33AEC569685DDC9CBADE481E30F00C44D7A04EE571ADE18B825068C584F4`.

Initial test failures were corrected in the new test harness: the full-download
byte assertion originally ignored existing content deduplication, and the
transaction fixture initially used an older shortcut adapter interface. No
updater/transaction production code was changed to make those tests pass.

## Delivered runnable synthetic tree

Created using `python -B -m scripts.make_release_demo --root
E:\Codex\Temp\MF-release-pipeline-demo-20260908`, which invokes the actual
PowerShell publication command. This is a tiny explicitly synthetic package,
**not** a packaged production MentorFlow build and **not** a production key.

Feed root: `E:\Codex\Temp\MF-release-pipeline-demo-20260908\release`

Envelope: `1.1.1\stable\release.json` (identical to `stable.json`)

Envelope SHA-256:
`D176B9B11EFF9574A25BBB7783A88C53FAB730388DAB91D2E0D713617F0D72F0`

The demo includes the full inventory, incremental inventory, all immutable
objects, signed metadata, signed build receipt, notes, checksums, and an
explicitly untrusted development public key. Its private key was memory-only.
The configured demo origin is `http://127.0.0.1:8766`; expiry is seven days from
generation. Use a freshly generated demo after expiry.

A separate post-build run served this exact directory on port 8766 and exercised
the existing downloader. Both reconstructed targets were hash-checked:

| Path | State | Logical full bytes | Actual HTTP asset bytes |
| --- | --- | ---: | ---: |
| FILE_LEVEL_INCREMENTAL | READY_TO_APPLY | 149019 | 1463 |
| FULL_FALLBACK | READY_TO_APPLY | 149019 | 149004 |

The 15-byte difference in full mode is verified duplicate-object reuse, not a
binary delta. These are measured **synthetic** values, not claims about a real
gigabyte-sized release. The local HTTP server was stopped and joined; no test
server/background process remains. Real product executables were not launched.

## Remaining operational prerequisites (not concealed)

Production publishing needs the operator's packaged build and exact build
provenance, a persistent external Ed25519 key already trusted by the packaged
desktop/updater, and the chosen HTTPS origin. Trusted Windows signing additionally
needs a real code-signing certificate and timestamp service. No real certificate
was supplied, created, imported, or used. The mocked hook test does not qualify
real Authenticode trust. These inputs are intentionally not fabricated.

The tool, documented command, static artifacts, safety gates and scoped tests are
complete. Actual production build signing, deployment, integration into another
worker's consumer branch, and end-user installation remain outside this task.
