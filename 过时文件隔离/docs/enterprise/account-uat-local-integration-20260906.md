# Local account UAT integration — source candidate only

Dispatch: `MF-A-ACCOUNT-UAT-INTEGRATION-20260906-01`.
Task: `MENTORFLOW_ACCOUNT_USER_TEST_LOCAL_INTEGRATION_V1`.
Office/branch: existing `MF-e1-account-core-r1-a`, `codex/e1-account-core-r1-a`.
No push, server mutation, package build, release/tag, worktree creation or cleanup.

## Exact source mapping

| Contribution | Exact commit | Exact tree | Integration |
| --- | --- | --- | --- |
| Accepted checkpoint | 1531c65116063ec606ed1c623acd29fb68e9e8b7 | c441dbfd48fa4bedcac1ee7212866728bc8ac03c | Common ancestor |
| Registration / starting HEAD | 6505fc82f9efdc71baffc86d818aec3141fe8c76 | 4dfb8af912b1e13ff4ad856ddf3ae3c0f1d48e38 | Already included; never reapplied |
| E107 launcher/preparation | a9a75ec6871a2291dca78747653a5afd2d6026f2 | 31dd2b5569f29694b0c25949afcb764bec3698d3 | Exact parent is checkpoint |
| E107 history repair | 3259dd6d219bf7dbdd116c93a0c637daf5744566 | 38402e5597c1e24950eea09fc4a6d3f1ffbd41d8 | Exact parent is a9a75ec |

Non-fast-forward merge of the exact `3259dd6` endpoint into `6505fc8`, retaining
both original E107 commits. No moving branch tip or worktree file copying was used.
Only two E107 commits occur after the common ancestor. Source path sets: E107 16,
registration 4, intersection 0. There were **zero Git conflicts**. All 16 E107 blobs
are unchanged from their source endpoint; registration policy/script/doc retain
their exact source blobs. The final merge SHA/tree is returned in the chat receipt.

Integration-only changes (no product implementation changes):

- `tests/theme/test_mvp_registration.py`: replace the registration-only test's
  entire-Desktop baseline freeze with the unchanged dedicated identity/token/
  credential/runtime boundary. Approved MainWindow/lifecycle changes must not be
  misclassified as a registration policy regression.
- `tests/theme/test_account_uat_integration.py`: enforce exact source ancestry,
  disjoint paths, index/working-copy blob identity for all approved source files
  except that documented test adjustment; enforce ordinary original regression
  assertions and the shortcut's trusted launcher contract.
- This integration record.

## Product boundary review

The history namespace is captured from verified issuer/subject and the authorized
organization/workspace held by the existing runtime, not a display name or fixed
username. Read/write, latest cards, recent history, prefetch/library, keywords,
model context and portable preference snapshots use that scope. Requests capture
their originating API and scope/generation; exit seals producers before API drain.
Legacy unscoped history stays in its original location, without automatic import,
assignment or upload. The E107 synthetic tests preserve and reopen both accounts'
nonempty history rather than judging an empty screen.

The LoginGate, embedded login, PKCE/token validation, Locker semantics, workspace
discovery/initialize and backend/migration sources are retained. Backend revision
9a1a6049 and DB head 0035_account_workspace_bootstrap are the supplied existing
deployment state, **not re-read or changed on the server by this task**. The user
reports E1-06 has applied/verified registration; this task retains the policy and
does not reapply or claim a new live registration test.

The personal-test launcher still performs full manifest/payload checks before
application start, with fixed TLS/SSH trust and no tenant overrides. Its embedded
anchors describe the earlier frozen personal-test release, not a newly built
package. E107 must bind the new final package's checker/manifest anchors using the
existing trusted packaging procedure, not simply copy stale anchors or weaken
verification. Shortcut installation and real launch were not executed by A.

## One focused post-merge run

Existing Python environment; offscreen Qt; explicit synthetic E:/Codex/Temp roots;
provider autorestore disabled; no formal database scan or external model call.
Command prefix: `python -m pytest -q -p no:cacheprovider`, with the following exact
selectors, `--basetemp=E:/Codex/Temp/MF-A-UAT-20260906-01-pytest`, and
`--junitxml=E:/Codex/Temp/MF-A-UAT-20260906-01-runtime/focused.xml`:

```text
desktop/tests/test_owner0_nonempty_isolation.py
desktop/tests/test_analogy_history_isolation.py
backend/tests/test_analogy_history_context.py
desktop/tests/test_login_smooth.py::test_first_screen_starts_credentials_without_second_auth_window
desktop/tests/test_login_smooth.py::test_remember_then_restart_uses_real_refresh_path
desktop/tests/test_login_smooth.py::test_explicit_logout_disables_restore_and_never_claims_remote_success_on_failure
backend/tests/test_personal_test_launcher.py
backend/tests/test_windows_package_privacy.py::test_manifest_exclusion_needs_no_second_entry_resolution
backend/tests/test_account_core_package_launch.py::test_immutable_program_tampering_still_rejected
backend/tests/test_account_core_package_launch.py::test_external_mutable_config_does_not_change_immutable_manifest
backend/tests/test_account_core_package_launch.py::test_launcher_cleans_inherited_identity_tls_and_python_settings
tests/theme/test_mvp_registration.py::test_minimal_merge_preserves_validators_permissions_security_and_custom_fields
tests/theme/test_mvp_registration.py::test_other_profiles_and_security_code_identical_to_accepted_base
tests/theme/test_account_uat_integration.py
```

Result: **31 passed, 0 failed, 0 skipped, 0 xfailed**, 54.04 seconds, exit 0.
One existing Starlette/httpx deprecation warning. The original
`test_recent_history_must_not_cross_accounts` is an ordinary PASS. Includes real
QSettings/independent-process reopen, A→B→A persistence, late thread/callback/API
capture, nonempty Vault/Plan/Progress/assets, and actual model-message assembly
intercepted before invocation. Network response fixtures are not live users.

Additional checks: 14 changed Python files compiled from staged blobs without
writing pyc; 6 production modules imported; both new PowerShell scripts parsed
with zero errors; `git diff --cached --check` passed. No full suite rerun.

Reused owner evidence: `reports/enterprise/history-isolation-repair-20260906.md`
records 45+10=55 PASS, 0 mandatory XFAIL/SKIP. These are prior owner results and are
not added to A's 31 as independent new tests. The earlier preparation report's
history XFAIL is superseded by the exact repair and this ordinary PASS. Its package
checks reported 73 PASS/4 symlink-privilege SKIP; those skips remain historical
limitations, not newly passed security gates. No request was sent to rerun owner QA.

## Final-package handoff, not yet completed

E1-07 takes the returned unified local SHA/tree, builds one final package, then
checks actual shortcut launch; in-app registration/login/initialize; remember and
restart; two distinct nonempty Plan/Progress/history/resource datasets; A→B→A;
close/reopen isolation; underlying reads and model context markers; application
and owned local API process termination. Do not infer isolation from an empty UI.

Fast-start performance, complete owner0 original-data import and formal migration
reconciliation remain incomplete. This candidate is for account-registration and
isolation hand testing, **not a claim that owner0's old data is restored**.

Remote push is NOT_RUN_BY_USER_DECISION. No new remote backup exists for this
candidate. Preserve the local commits, object database and worktree. No project,
user, cache, package, session or worktree cleanup was performed; test fixtures use
only their own synthetic temporary files and ordinary fixture lifecycles.
