# GitHub Actions: no automatic test compute

Authority: `MF-E107-GITHUB-ACTIONS-AUTO-TEST-KILL-20260907-01` /
`MENTORFLOW_GITHUB_ACTIONS_ZERO_AUTO_TEST_GUARD_V1`.

GitHub is a source/history and future release-asset host, not the default test
machine. Run tests/security/dependency review locally or on owned infrastructure.
This source change is LOCAL ONLY: it does not disable already-published workflows,
cancel runs, change repository settings, register a runner or deploy anything.

## Local / A integration / VPS preflight

From the repository root, using an existing Python with PyYAML:

```text
python scripts/enterprise/verify_actions_compute_policy.py
python -m pytest tests/policy/test_actions_compute_policy.py -q -p no:cacheprovider
```

Run this local guard before accepting workflow changes or a release preflight.
It must NOT be installed as an automatically triggered GitHub-hosted Action.
Exit 0 means the current source workflows satisfy the static policy; exit 1
means fail closed. It performs no network calls, tests, installs or dispatches.
`--ref <exact commit>` audits historical YAML read-only against current policy.

All workflows default to heavy, including wrappers and prerequisite jobs. The
policy file `.github/actions-compute-policy.json` has no lightweight exceptions.
Future exceptions need a reviewed reason; test/scanner/delegating content cannot
be hidden behind a lightweight name. Guard coverage includes scalar/list/mapping
`on`, duplicate/merge-key rejection, unknown events, hosted versioned labels,
dynamic runner expressions, unresolved reusable workflows and call cycles.
Local `workflow_call` is retained to avoid breaking the manual CI wrappers;
every entry workflow must remain manual. Scheduled Dependabot updates are disabled.

All 34 directly scheduled job definitions now explicitly select the existing
`[self-hosted, Linux, X64, mentorflow-e1, turkey-ci]` labels. This identifies the
read-only API-observed `mentorflow-e1-turkey-01`; it is not a promise that all its
tooling, scanner leases, deployment access or prerequisites are ready.

Windows-only jobs `backend-full-windows`, `backend-desktop`, and
`shadow-repair-contract` retain their steps but use `if: ${{ false }}`. There is
no verified authorized Windows self-hosted runner. They must be executed locally
or reviewed again if an authorized runner becomes available; Linux is not Windows
coverage. Mixed OS matrices retain Linux only. PR-only dependency review is also
disabled because manual dispatch has no PR base/head payload. Historical exact
branch/source gates remain intact and can still refuse a manual run. Full release
or Shadow gates may therefore fail closed; no passing full release is claimed.

Security CodeQL now depends on the scanner lease. Previously its missing `needs`
allowed Python and JS/TS jobs to start despite the scanner-lease job failing.
No security scanner steps, test commands or services were deleted/rewritten.

## Audit results and attribution

See `reports/enterprise/actions-compute-audit-20260907.json` for the full per-file
name/triggers/runners/jobs/CodeQL/hosted/self-hosted/schedule/workflow_run/status
inventory, before/after counts, and remote observations.

There are 8 local workflows. Auto entry event pairs: 6 → 0. Hosted heavy job
definitions: 29 → 0. Existing self-hosted definitions: 5 → 34 after rerouting.
These counts do not multiply strategy matrices or count reusable call wrappers
as runner allocations. No `workflow_run` or `repository_dispatch` entry was found.
No `.github/codeql/*` configuration exists in this source tree. Custom CodeQL
analysis lives in `.github/workflows/enterprise-security.yml`; container release
also uses CodeQL's SARIF upload action.

Git log --follow and blame show the file and its push/pull_request/schedule
triggers entered the integration history together in
`c9704f0cf628f96b73cf9cb06d9491ddce41c9c9`:

- Author: Codex Recovery; author date 2026-08-27 01:15:27 +08:00.
- Commit date: 2026-08-27 01:24:07 +08:00.
- Subject: `feat(enterprise): 重建自包含安全负载与预发布资产`.
- Cherry-pick trailer: `4d61b3547a8d0865a53634abd549be004c904ce1`.
- Tracked `enterprise_e1/INTEGRATION_QUEUE.json` binds those exact source/integration
  commits to E1-07 delivery `DLV-W07-20260826T172028Z-07`.
- Original dispatch/task IDs: UNKNOWN. The original source object and external
  ready receipt are unavailable in this checkout/current disk; no guess is made.
- No later trigger modification was found before this repair in available history.

The old cron is `23 3 * * 1` (Monday 03:23 UTC / 11:23 China time). It is a
potential auto entry, NOT proof of why the screenshot's 16:35 mail appeared.

## Remote evidence and required user action

Read-only GitHub queries found no runs in 2026-09-07 07:30–10:00 UTC (15:30–18:00
China time), nor any run returned for the entire UTC day. Therefore the 16:35
run ID/event/actor/SHA/runner remain UNKNOWN, not schedule or push by inference.
Provide the run URL if that exact execution needs attribution.

Latest returned Enterprise Security run is 33273443300 on 2026-08-29 20:25:15 UTC:
push, core-v2/integration, SHA 895632425889363631cd0e38c952ea94b0ffdcb6,
actor tonyswift868, attempt 1. Its Python/JS CodeQL and scanner lease jobs were
allocated GitHub Actions runners with ubuntu-latest labels; all failed. The
separate self-hosted scanner job was skipped with no runner allocated. It is
explicitly NOT the requested September 7 run.

GET `/repos/tonyswift868/AI-Knowledge-visualization/code-scanning/default-setup`
returned HTTP 403: "Code scanning is not enabled for this repository. Please
enable code scanning in the repository settings." No state field was returned:
Default Setup remains AUTHORITY_UNAVAILABLE / unverified, not falsely Disabled.

Required remote actions, OUTSIDE this local-only dispatch:

1. An authorized owner must publish the reviewed source to the default branch.
   Until then, old remote workflows remain capable of running. No push was made.
2. Repository → Settings → Security → Code security (or Code security and analysis)
   → Code scanning → CodeQL analysis: inspect Default setup and disable it if
   configured. It is independent of the custom YAML.
3. On the same repository security settings page, inspect Dependency graph →
   automatic dependency submission and Dependabot security updates. The Actions
   API lists an active `Dependency Graph` entry at `dynamic/dependabot/update-graph`,
   which is not a local YAML and is not disabled by this source guard. Its event
   and cost cannot be proven from the registration entry alone.
4. Actions also retains an active historical `E1 Turkey Runner First Real Smoke`
   registration absent from this source tree. Review historical/default-branch
   workflow availability before claiming repository-wide zero remote compute.

Do not run/rerun/dispatch Actions as a verification step. No new login, credential
mutation, scanner, server/database operation, installer or updater change is needed.
