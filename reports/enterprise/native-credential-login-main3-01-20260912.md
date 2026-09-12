# Native credential login — worker MAIN3/01

DISPATCH_ID: `MF-MAIN3-01-NATIVE-CREDENTIAL-LOGIN-20260912-04`

TASK_ID: `MENTORFLOW_NATIVE_USERNAME_PASSWORD_LOGIN_CONTRACT_V1`

**STATUS = PARTIAL_ARCHITECTURE_GATE.** The native form, controlled backend session
boundary and client/bootstrap integration are implemented and locally tested.
The identity-owned verifier is not implemented/deployed in this repository. Default
configuration returns `NATIVE_AUTH_NOT_READY`; real existing-account login is not
claimed. Normal login never falls back to a provider page or external browser.

## Office continuity

Pre-write Phase 0 verified a clean, isolated existing office:

```text
OFFICE_REUSED = YES
PHASE0_TOPLEVEL = E:/Codex/Data/worktrees/8831/MentorFlow
PHASE0_GIT_DIR = E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow1
PHASE0_COMMON_DIR = E:/Codex/Projects/MentorFlow/.git
PHASE0_INDEX = E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow1/index
START_HEAD = 03d979710a4cde44917c4f10c2f08d2390c8426d
START_BRANCH = worker/main3/01
START_STATUS_PORCELAIN = EMPTY
INTEGRATION_READ_SNAPSHOT = 15f6d0dda0968be1b39295577a139a9017615e82
```

`git worktree list --porcelain` identified this office as the only checkout of
`worker/main3/01`; shared Local remained on `main2`, integration on its own worktree.
No worktree creation, branch switch, reset, merge, rebase, push, or shared-ref update.
The final delivery commit is the commit containing this report; its exact SHA/tree
are returned in the dispatch receipt.

Read-only comparison of START_HEAD and the exact integration snapshot showed no
difference in `backend/app/enterprise/identity`, `backend/app/account_core_mvp.py`,
`backend/app/enterprise/account_workspaces.py`, `desktop/main.py`, or
`release/auth-authority.json`. Their desktop OIDC/provider-UI differences remain
historical, not inputs to native credential authentication.

## Source result

- LoginGate owns native account and masked password fields, a show/hide control,
  login, Remember session, automatic login, and capability-gated account actions.
  Password clears on submit/cancel/close/update block. Failure messages are allowlisted.
- Normal startup, expired recovery and account switching use that same gate. Explicit
  credentials override a different remembered account. Browser entry is removed from
  normal main/settings routing; legacy provider code is labelled accordingly.
- Native client sends credentials only to the configured HTTPS API, with fixed
  desktop client/API audience. It validates the opaque session contract, then uses
  existing workspace discovery/initialization and `/auth/me` binding checks.
- Controlled native login/refresh/logout routes, an mTLS identity-bridge caller,
  bounded hashed session storage, rotation/replay revocation, throttling, and strict
  disabled-by-default configuration are implemented. No local password store exists.
- Native and old bearer tokens share the same trusted identity directory and workspace
  authorization. The new token namespace does not bypass membership checks or alter
  legacy JWT validation. E109 OLD canonical identity and distinct API authority remain.
- Consent uses the existing Windows Credential Locker interface. Automatic restore
  additionally checks actual saved refresh material. Native rotation is saved before
  workspace work; expiry clears only the affected account. Legacy refresh remains
  noninteractive; native opaque tokens never go to an OIDC endpoint.
- The delivery spec includes `desktop.native_auth`; exact backend auth-source binding
  includes `app.enterprise.identity.native`, needed by the current runtime.

`03d979710a4cde44917c4f10c2f08d2390c8426d` is superseded as the normal browser-login
product direction. Its protocol implementation remains only for explicit legacy use.

## Validation

Final local checks: **216 passed** (162 desktop, 48 backend, 6 build-source checks).
Backend emitted one pre-existing Starlette/httpx deprecation warning. No skipped
native acceptance was substituted with a browser test. Five changed runtime modules
also parsed under Python 3.11.15; `git diff --check` passed.

| Required behavior | Evidence |
| --- | --- |
| Native form, mask/show/hide, no password persistence, no browser | `desktop/tests/test_native_login.py::test_native_form_masking_clear_no_persistence_or_browser` |
| Wrong and unknown accounts receive the same error | `backend/tests/test_native_auth.py::test_wrong_password_and_unknown_account_are_identical_and_not_echoed`; client error tests |
| Network error is safe and does not expose server detail | `test_client_wrong_unknown_and_network_errors_are_safe` |
| Success shows bootstrap state before workspace requests | `test_client_success_verifies_workspace_and_me_on_api_authority` |
| Main waits for account/workspace and local service | `test_verified_native_result_only_opens_main_after_workspace_and_local_service_ready` |
| Bootstrap failure keeps main closed | `test_success_enters_bootstrap_and_failure_cannot_open_main` |
| Safe refresh rotation, workspace-error continuity, expiry | `test_native_restore_rotates_secure_material_before_workspace`; session lifecycle tests |
| Expired session returns to native gate | `desktop/tests/test_login_smooth.py::test_expired_session_returns_to_original_gate_form` |
| Remember preferences alone cannot trigger auto login | `test_auto_login_needs_saved_secure_material` |
| Register/reset false; provider capability cannot open browser | Native form test and updated `desktop/tests/test_prelogin_update_ux.py` |
| Replay/concurrent refresh/logout revoke family | `backend/tests/test_native_auth.py` rotation/replay/concurrency/logout tests |
| Native client → real account-core routes → isolated persistence → same old workspace | `test_native_desktop_to_account_core_bootstrap_preserves_old_identity_membership` |
| Revoked membership rejects both native and old bearer | Same HTTP/persistence test and native runtime test |
| TLS, mTLS configuration, fixed client/audience, strict bridge response, MFA stop | Native transport, scope and bridge tests |
| New dependencies present without executing an installer/build | `scripts/test_e109_auth_source_binding.py`; candidate-path tests |

Identity verification in these tests is synthetic. The persistence integration uses
isolated SQLite fixtures; it does not qualify deployed PostgreSQL RLS, live Keycloak,
installed binaries, production TLS topology, or real Windows Locker deployment.
Existing Locker behavior is reused; native consent/routing is tested through its
controlled adapter. Offscreen form layout was inspected with a local CJK font.

Commands (all with `PYTHONDONTWRITEBYTECODE=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`,
project/backend on `PYTHONPATH`, `-p no:cacheprovider`, and isolated E: basetemp):

```text
Python 3.13.14 / PySide6 6.11.1:
pytest desktop/tests/test_native_login.py desktop/tests/test_login_smooth.py
  desktop/tests/test_login_gate_bootstrap.py desktop/tests/test_enterprise_account_shell.py
  desktop/tests/test_prelogin_update_ux.py desktop/tests/test_e109_capability_config.py
  desktop/tests/test_e109_integration_binding.py desktop/tests/test_e109_system_browser.py
  desktop/tests/test_enterprise_oidc.py -q

Python 3.12.10:
pytest backend/tests/test_native_auth.py backend/tests/test_enterprise_identity_compatibility.py
  backend/tests/test_enterprise_identity_runtime.py backend/tests/test_account_core_mvp_runtime.py -q
pytest scripts/test_e109_auth_source_binding.py scripts/test_e108_candidate_paths.py -q
```

Logs: `E:/Codex/Temp/MF-MAIN3-01-NATIVE-CREDENTIAL-LOGIN-20260912-04/`
(`desktop-tests.log`, `backend-tests.log`, `build-tests.log`).

## Next gate and limits

The exact architecture and activation prerequisites are in
[ADR_NATIVE_CREDENTIAL_AUTH_V1.md](../../docs/ADR_NATIVE_CREDENTIAL_AUTH_V1.md).
Next work must implement/qualify the identity-owned verifier, mTLS deployment,
required-action/MFA policy, rejection timing, actual process topology/shared store,
long-session renewal and required revocation latency, then exercise a real native
candidate with existing accounts. None of those deployment gates is represented as
passed by the local tests. Keep register/reset capabilities false.

```text
NORMAL_LOGIN_EXTERNAL_BROWSER = NO
NATIVE_USERNAME_FIELD = YES
NATIVE_PASSWORD_FIELD = MASKED_WITH_SHOW_HIDE
PASSWORD_PERSISTED = NO
PASSWORD_GRANT_USED = NO
KEYCLOAK_DIRECT_GRANT_USED = NO
AUTH_BACKEND_CONTRACT = IMPLEMENTED_DEFAULT_DISABLED_IDENTITY_VERIFIER_GATE
SESSION_STORAGE = WINDOWS_CREDENTIAL_LOCKER_CONSENT / SERVER_HASHED_SINGLE_PROCESS
LOGIN_SUCCESS = SYNTHETIC_E2E_PASS / REAL_IDENTITY_NOT_QUALIFIED
WRONG_PASSWORD = GENERIC_MESSAGE_TESTED
ANTI_ENUMERATION = IDENTICAL_STATUS_AND_BODY_TESTED / BRIDGE_TIMING_GATE
WORKSPACE_BOOTSTRAP = TESTED_SAME_OLD_IDENTITY_NAMESPACE
MAIN_WINDOW_GATE = TESTED
OLD_SYSTEM_BROWSER_BEHAVIOR_SUPERSEDED = YES
AUTH_AUTHORITY_STATUS = PREPARED_NOT_ACTIVE
MIGRATION_WINDOW_STARTED = NO
LIVE_SERVER_MUTATION = 0
KEYCLOAK_MUTATION = 0
SMTP_SEND = 0
REMOTE_PUSH = 0
GITHUB_ACTIONS = 0
WORKER_STATUS = SILENT
```
