# One-click per-user update core v1

Dispatch: MF-E108-W07-ONECLICK-UPDATER-CORE-20260909-03.
Base: b6e1d5da34814578d4f59309073a64fcf4711c21 plus I06 repair
8c717eb5cba754cf2bdc0a0394f55ef274feae83. Static feed binding is PENDING_04.

## Public API

`scripts.per_user_update_core` provides blocking worker/coordinator APIs:

- `check_latest(root)` returns UPDATE_AVAILABLE, UP_TO_DATE, FAILED_SECURITY or
  FAILED_RECOVERABLE. Missing feed is explicit and never opens a browser.
- `download_latest(root, progress=None, cancel=None)` checks stable, downloads
  and independently verifies the target, then records UPDATE_DOWNLOADED.
- `apply_and_relaunch(root, pid=None, hwnd_hex=None, reason='normal')` continues
  an already prepared update. In-App calls default PID to the current process;
  capture the App's HWND on the GUI thread and pass its hexadecimal string.
- `update_to_latest(root, progress=None, cancel=None, pid=None, hwnd_hex=None)`
  performs discovery, download, verify and apply/relaunch after **one** consent.
  No second restart button, browser URL, shell script or Setup wizard is needed.

Invoke on the existing UI worker, not the Qt event loop. Progress receives
snapshot dictionaries; cancel is a callable returning bool. Do not manipulate
Qt widgets directly from these callbacks: marshal progress through UI signals.

An in-App caller cannot synchronously terminate itself and finish a transaction.
After validating the installed signed Updater, it starts the ordinary-token
stable UserRuntime and returns APPLYING with coordinator_started/result_path.
This is dispatch evidence, **not health success**. The coordinator re-fetches
signed metadata, verifies cached bytes, closes the exact PID/HWND, applies,
relaunches and verifies target health. External native coordinators can call the
same high-level API with an explicit running App PID/HWND and wait for completion.

UserRuntime also exposes native actions check-latest, download-latest,
update-to-latest and apply-latest for internal callers/qualification. These are
native process arguments, not a user-facing CMD/PowerShell dependency. UI owners
must not direct users to execute them. No installer or browser fallback exists.

## Feed configuration / release boundary

The signed `App/mentorflow-version.json` contains:

```json
{"version":"1.4.0","channel":"stable","update":{"protocol":"mentorflow.signed-file-release.v1","UPDATE_FEED_URL":"https://updates.example.org/mentorflow/stable.json"}}
```

The URL above is documentation only, not a deployed endpoint or default.
04 supplies STAGING_UPDATE_FEED_URL and its exact signed release receipt. The
release builder injects that URL before manifest/signature generation. The older
`update.metadata_url` alias remains valid; if both are present they must match.
Runtime reads this build-provisioned config only after verifying it against the
installed signed release. Environment URL overrides are not accepted. Compiled
Ed25519 public keys remain the authority, not keys in mutable receipts/config.

Missing config returns FAILED_RECOVERABLE with an explicit message. App startup
does not require a reachable feed. Production requires HTTPS; isolated signed
UAT may use a fixed HTTP origin. Credentials/query/fragment URLs, file paths and
localhost endpoints are refused for distributed builds. Assets must remain on
the configured origin. No TLS/signature/hash/antirollback bypass is introduced.
The user-local cache and short reconstruction directory are computed at runtime;
developer absolute staging paths from configuration are ignored by this API.

An explicit compiled ONE_CLICK_TEST_FIXTURE flag is honored **only** in isolated
UAT core test builds to permit loopback protocol regression. It is not runtime
configuration, cannot be enabled by the feed/environment, and the fixture builder
refuses to create an installer in this mode. These artifacts are not distributed
product candidates or proof that 04's static host exists.

## Lifecycle

Shutdown/logoff/unsafe teardown preserves PENDING_INSTALL and does not start a
destructive apply. `resume_pending_on_normal_launch(root)` refreshes signed
metadata, checks that the cached target remains the admitted release, verifies
the cache again and applies/relaunches. UserRuntime `launch` automatically tries
this path before starting App. Offline/changed metadata leaves pending intact
and launches the verified current App; a stale recovery record is not proof of
a currently running process. Withdrawal/change never silently authorizes old
cached bytes. The UI/stable-launcher owner must route ordinary startup through
this native launch action or its equivalent existing lifecycle adapter.

Bad target health restores and automatically relaunches previous. The update
attempt still reports failure; last-recovery.json/current signed identity record
the independently observed recovery, rather than claiming the bad target won.
Unknown errors retain the original exception and pending/recovery state.

Existing signed-file-release.v1, stable, SHA256, Ed25519, incremental/full file
sets, resume/cancel, high-water witnesses, previous retention and transactional
health checks are reused. No formal data, account, installer or server changes.

## Qualification boundary

Focused tests use inert signed files and injected health callbacks. The separate
`scripts/qualify_one_click_core.py` harness uses real packaged App/UserRuntime
on a verified never-activated private desktop with a bounded kill-on-close job.
It checks good and bad one-click updates, normal-token real Qt window health,
offline/cancel, and automatic pending application on next native launch.
Loopback results are labelled fixture-only. Static binding and its one real
HTTP smoke/E2E remain PENDING_04, without a core rewrite.
