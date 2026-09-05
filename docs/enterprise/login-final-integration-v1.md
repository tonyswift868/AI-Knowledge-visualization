# Login theme + Desktop: one canonical integration

Dispatch: `MF-A-LOGIN-FINAL-INTEGRATION-20260906-01`

Task: `MENTORFLOW_LOGIN_THEME_DESKTOP_FINAL_INTEGRATION_V1`

## Decision

READY_FOR_LIVE_AND_PACKAGE_QUALIFICATION, not final end-user acceptance.
No product redesign, deployment, final Desktop build, dependency mutation,
live database operation, Release/tag, default-branch merge or manual Actions run.

## Exact provenance

Checkpoint `f458db4261af6f62786d551ac9bc0974a3429b46`, tree
`5e7f036292841e0a6cd909bfeb4b379df2fcb48a`, already contains accepted backend
`9a1a6049420e11b6429fa8f3f5f6f41c26110052` as an ancestor. All 11 backend delivery
paths match exact Git blobs in the checkpoint and integrated candidate.
`dc6b8bbb24a9fbc5cc878841674bffca2a51519a` and
`e7594205374382248de91d86c37af22aecdc31ad` are also checkpoint ancestors.
None was reapplied. The single migration head remains
`0035_account_workspace_bootstrap`; this is a source graph check, not live DB status.

The existing A theme commit remains unchanged. A non-fast-forward merge of the
**exact** final Desktop endpoint preserves both ordered original commits:

| Source | Integrated identity |
| --- | --- |
| Theme `4b750660a29dd8a64f6404bf136185d615730062` | Same commit, first-parent ancestry |
| Desktop `e69eddc31007f38f428d2888e64ccf2ee13da91d` | Same original commit, merged ancestry |
| Desktop `023e89a24da4d9beeeadc511cf28edd46b6673ff` | Same original commit, second merge parent |

The new Desktop chain's first parent is exactly `e759420...`; its final source
tree is `67a557306b17e4d1b25f5bba59440761d9ef0116`. Stable patch review found both
new patches absent before integration. No branch tip or duplicate implementation
was consumed. All 14 Desktop source/test paths retain their final source blobs;
all 13 theme delivery paths retain their accepted source blobs. Direct theme /
Desktop path intersection: **0**. There were no conflicts to resolve.

Integration-only files:

- `tests/theme/test_login_integration.py` — exact source-release theme inclusion
  and offline migration-head checks.
- This document — provenance, combined gates and qualification boundaries.

## Preserved behavior and focused evidence

The same LoginGate hosts the off-the-record authorization content; its duplicate
native brand/login button is hidden while embedded content is present. No
system-browser handoff or second top-level authentication dialog is introduced.
MainWindow waits for verified authentication and account bootstrap. Opt-in
Remember, Remember-required Auto Login, expired-session fallback, cancel,
explicit logout, account switching, generation fencing and rotation-safe
Locker use remain source-identical to the accepted Desktop delivery.

The native Qt repair is preserved byte-for-byte: detach retiring dialogs,
retain ownership until QObject destruction, destroy page then profile then
shell, defer browser creation until validated discovery, and drain late events.
The same-gate focused regression includes close-during-discovery destruction.

Per-account local API spawn, transferred reserved socket, account binding before
backend import, inherited DB URL removal, no token startup arguments, verified
old-process exit and parent-death handling remain unchanged. A/B/A tests exercise
real local spawned processes and disposable SQLite readers, not live accounts.

GET `/api/enterprise/account/workspaces` and POST
`/api/enterprise/account/workspaces/initialize` with `{}` remain connected to
the 0035 backend. Discovery sends bearer-only authorization, selects only returned
workspaces, then applies validated metadata headers. Error responses do not
become empty-account success. Returning-device and empty-account bootstrap
regressions run without reopening or redesigning recovery/relink components.

Focused integration runs (all isolated scratch storage):

| Group | Result | Scope |
| --- | --- | --- |
| Theme + release inclusion | 13 passed | Existing 11 theme checks plus source-archive inclusion and one migration head |
| Desktop seams | 36 passed | Full login_smooth, account_backend, enterprise_oidc; two bootstrap cases and one A/B/A switching case |
| Backend contracts | 8 passed | Normal-user initialization/rediscovery, bilateral denial, strict desktop JWT validation, TLS, PKCE/state/nonce, MVP configuration/API |
| Compile/import | PASS | 14 changed Python files compiled in memory; 9 production modules imported in isolated environment |
| Git diff | PASS | No whitespace errors or conflict markers |

Total: **57 passed, 0 skipped**, one pre-existing Starlette/httpx deprecation
warning. Source-release test initially exposed Windows archive CRLF conversion;
the integration-only export check now explicitly uses `core.autocrlf=false` to
compare exact Git blob bytes. No accepted product source was changed for this.

The owner's 169 passed / 4 WinError 1314 skips and 49 additional checks are
historical evidence, not rerun totals. Those four skips remain skips, not PASS.
The existing 9 theme browser fixtures and Windows Locker smoke were not rerun.

## Publication and remaining qualification

Publish only the existing `codex/e1-account-core-r1-a` branch, normal non-force
push. The eight workflow trigger declarations were inspected: this branch
matches none of the configured push triggers. No PR, Release, tag or manual
workflow is created. The external final receipt records the exact merge SHA/tree,
GitHub readback and one bounded post-push Actions observation.

Not run here: real Keycloak theme rendering, password login, live refresh TTL or
revocation, live A/B accounts, live workspace discovery, native packaged Windows
DPI, packaged spawn/freeze_support, packaged Qt lifecycle, and final end-user UX.
Static fixtures, source tests and isolated SQLite do not qualify those gates.

Next owners: **E1-06 then E1-07**, under their existing authority. Next action:
`DEPLOY_REAL_THEME_THEN_BUILD_AND_RUN_ONE_FINAL_WINDOWS_PACKAGE`.
The existing-realm client-only theme activation and read-only mount procedure is
in `desktop-login-theme-v1.md`; importing the realm at restart does not update
an already-created client's theme. This integration does not execute that step.
