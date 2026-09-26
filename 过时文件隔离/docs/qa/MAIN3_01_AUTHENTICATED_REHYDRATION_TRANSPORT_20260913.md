# MAIN3 / 01 authenticated rehydration receipt

Dispatch: `MF-MAIN3-01-AUTHENTICATED-REHYDRATION-TRANSPORT-20260913-14`.
Task: `MENTORFLOW_LOGIN_TO_SERVER_REHYDRATION_TRANSPORT_V1`.
Qualification: **source and disposable fixtures**, not a live or packaged release.

## Phase 0

Recorded before writing:

```text
TOPLEVEL = E:/Codex/Data/worktrees/8831/MentorFlow
GIT_DIR = E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow1
COMMON_DIR = E:/Codex/Projects/MentorFlow/.git
INDEX = E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow1/index
START_HEAD = 16f5b9ad36e7191a16089fd5a353af469cb9adef
BRANCH = worker/main3/01
START_STATUS = CLEAN
AUTHORITATIVE_INTEGRATION = 04e2d61e3e39729886b3a2e6c7aff022b65a382a
NEW_WORKTREE_CREATED = NO
```

The worktree inventory showed this branch owned only by this isolated office.
No main2, integration/main3 or other worker ref/index/worktree was changed.

## Result

```text
STATUS = PASS_AUTHENTICATED_REHYDRATION_TRANSPORT
AUTHENTICATED_TRANSPORT = PASS_SOURCE_FIXTURE
ACCOUNT_CONTEXT_SERVER_BOUND = PASS
WORKSPACE_CONTEXT_SERVER_BOUND = PASS
SESSION_EXPIRY = PASS
SESSION_RENEWAL = PASS
LOGOUT_INVALIDATION = PASS
ACCOUNT_SWITCH_INVALIDATION = PASS
LATE_RESULT_REJECTED = PASS
USERNAME_USED_AS_AUTHORITY = NO
EMAIL_USED_AS_AUTHORITY = NO
REAL_USER_DATA_MIGRATION = NO
LIVE_MUTATION = 0
SMTP_SEND = 0
SYNC_0037_SCHEMA_CHANGED = NO
WORKER_STATUS = SILENT
```

The native login result now carries the server-confirmed transport, activated by
accepted login only. Product bootstrap invokes the unchanged 04 producer. Existing
metadata/progress requests share its token owner so rotation cannot strand them
with a stale access token. Returned envelopes enforce account/session epoch at
the consumer's final synchronous commit. Cancellation and logout invalidate before
waiting on background network work. See the consumer and lifecycle contract in
`docs/decisions/AUTHENTICATED_REHYDRATION_TRANSPORT_V1.md`.

## Tests

Environment: `E:/Codex/Temp/mf-e1s1-rp01-test-venv/Scripts/python.exe`, Python 3.13,
Qt offscreen, synthetic identities and E: disposable test storage. `PYTHONPATH`
contains this worker root and its `backend` directory.

Expanded regression: **170 passed, 1 deselected** (125.53 seconds):

```text
python -m pytest
  desktop/tests/test_rehydration_transport.py
  desktop/tests/test_native_login.py
  desktop/tests/test_login_smooth.py
  desktop/tests/test_login_gate_bootstrap.py
  desktop/tests/test_account_switching.py
  desktop/tests/test_enterprise_oidc.py
  desktop/tests/test_stable_account_storage.py
  backend/tests/test_authenticated_rehydration.py
  backend/tests/test_native_auth.py
  backend/tests/test_stable_account_identity.py
  -q -k "not test_auto_restore_refreshes_even_unexpired_access_and_preserves_rotation_on_workspace_error"
  --basetemp=E:/Codex/Temp/g14e
```

After final session receipt-time, configuration-validation and unauthenticated
envelope refinements, all directly affected suites passed again:
**76 passed** (77.68 seconds):

```text
python -m pytest
  desktop/tests/test_rehydration_transport.py
  desktop/tests/test_native_login.py
  backend/tests/test_authenticated_rehydration.py
  backend/tests/test_native_auth.py
  -q --basetemp=E:/Codex/Temp/g14f
```

Coverage includes login -> workspace initialization -> /auth/me -> authenticated
sync, exact 04 route and producer, denied tampered workspace, rejected wrong
account confirmation, server membership revocation, expiry, 401 renewal,
concurrent renewal once, remembered and memory-only rotations, latest-token
logout, cancel, A->B switch, late A response, deferred commit rejection, and actual
product bootstrap with shared metadata/progress token rotation. No raw session
token is exposed in transport errors or persisted outside the existing consented
Locker path. Only test-defined synthetic credential values were used.

One pre-existing test is explicitly **not passed**:
`desktop/tests/test_login_smooth.py::test_auto_restore_refreshes_even_unexpired_access_and_preserves_rotation_on_workspace_error`.
It expects legacy OIDC rotation to persist before a failed workspace check. The
starting implementation stores that rotation only after server account mapping
confirmation. The same assertion failed when the exact
`16f5b9ad36e7191a16089fd5a353af469cb9adef:desktop/enterprise_oidc.py` Git blob was
loaded read-only into the test module (`--basetemp=E:/Codex/Temp/g14base`). This
delivery does not alter that legacy restore implementation or weaken its mapping
gate. Native session renewal is separately tested and passes. The only suite
warning was the existing Starlette/httpx TestClient deprecation.

## Changed files and handoff

- `desktop/rehydration_transport.py`: authenticated request owner and result gate.
- `desktop/native_auth.py`: create inactive transport after verified native login/restore.
- `desktop/enterprise_oidc.py`: optional private transport handle on existing result.
- `desktop/login_session.py`: activation, consented rotation and latest-token logout.
- `desktop/startup_flow.py`: shared account API transport and structured bootstrap result.
- `desktop/main.py`: login cancellation, shutdown and account-switch invalidation hooks.
- `desktop/runtime.py`: context clearing and fallback switch invalidation.
- `desktop/tests/test_rehydration_transport.py`: producer, lifecycle and product bootstrap tests.
- `desktop/tests/test_native_login.py`: canonical account ID in two existing fixtures.
- `backend/tests/test_authenticated_rehydration.py`: real native/context HTTP integration test.
- `docs/decisions/AUTHENTICATED_REHYDRATION_TRANSPORT_V1.md`: consumer contract.
- This receipt.

No changes to 04's sync service, producer, schema or snapshot semantics, or to 03's
presentation. The exact 04 modules are loaded read-only in tests because the
worker base lacks them. Missing producer/runtime endpoint is explicitly
`TRANSPORT_UNAVAILABLE`; it is never treated as an empty server-confirmed cache.
03/04 retain the final store/UI binding through the envelope's `publish` contract.
No PostgreSQL RLS, real identity verifier, real user migration or live activation
pass is claimed by this task's fixtures.

Integration applicability: `git apply --cached --check` passed against exact
`04e2d61e3e39729886b3a2e6c7aff022b65a382a`, using only
`E:/Codex/Temp/MF-MAIN3-01-TRANSPORT-20260913-14/integration-check.index`.
`git diff --cached --check` passed. The integration worktree/index remained untouched.
