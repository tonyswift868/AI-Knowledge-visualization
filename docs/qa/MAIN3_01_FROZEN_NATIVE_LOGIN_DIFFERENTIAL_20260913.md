# Frozen native login differential receipt

Dispatch: `MF-MAIN3-01-FROZEN-NATIVE-LOGIN-DIFFERENTIAL-20260913-15`.
Task: `MENTORFLOW_SOURCE_VS_FROZEN_NATIVE_LOGIN_ROOT_CAUSE_V1`.
Status: **SOURCE_FIX_PASS_NEW_CANDIDATE_REQUIRED**.

## Isolation and preserved authority

Phase 0 before writes: clean `worker/main3/01`, HEAD
`0775b1a53e8c4ef101494b86858a45266220d245`.

```text
TOPLEVEL = E:/Codex/Data/worktrees/8831/MentorFlow
GIT_DIR = E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow1
COMMON_DIR = E:/Codex/Projects/MentorFlow/.git
INDEX = E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow1/index
READ_ONLY_INTEGRATION = d73f3f9850fc0ff99ce874eaf8166f988ba3637b
BUILD_SOURCE = 2906893fe7d77e6ff70cf8efc374953423eb2450
TEST_ID = MAIN3_CROSS_DEVICE_AB_V1
```

The exact current EXE remains
`E:/Codex/Temp/CQ/build-2906893fe7d7/dist/MentorFlow-CrossDevice.exe`, SHA256
`7db576d4e89f184ea33568a0e469edebf1d50f5eb9322903e85ef7678ff37c46`.
No new worktree, integration edit, publication, live public issuer change or
GitHub Actions execution occurred.

The latest acceptance lease remained valid. Verified HTTPS startup health and
Identity discovery succeeded before and after investigation. Backend PID 90892,
Identity PID 23780 and PostgreSQL PID 3988 remained running. The public CA digest
and old EXE digest remained unchanged; the backend private-key reference remained
present. No acceptance TLS/mTLS materials or runtime services were cleaned.

## First actual divergence

Unmodified source native login succeeds. In the exact original frozen EXE,
`DesktopOidcConfig.from_env()` fails while evaluating `_migration_contract()`:

```text
AUTH_MODULE_LOADED
  -> DesktopOidcConfig.from_env
  -> _migration_contract (original enterprise_oidc.py:52)
  -> ModuleNotFoundError, missing module app.enterprise.identity
```

This happens **before** `AUTH_CONFIG_READY`, native HTTP-client construction,
TCP, TLS, HTTP response parsing or session bootstrap. No effective frozen config
object exists to compare at that point; this is not evidence of different issuer
values, and printing a matching environment would not prove config parity.

The exact PYZ contains the full canonical `app.enterprise.identity` tree,
including `issuer_migration`. It also contains `backend.app.enterprise`, but not
`backend.app.enterprise.identity`. The original auth helper prepends
`_MEIPASS/backend` to `sys.path`. Actual frozen loader observations show:

| Imported name | Actual PYZ entry selected |
| --- | --- |
| `app` | `backend.app` |
| `app.enterprise` | `backend.app.enterprise` |
| `app.enterprise.identity` | Missing under the incorrectly selected tree |

Thus a checkout-only path adjustment shadows the packaged canonical namespace.
The missing-module exception has no recognized native result code. The unchanged
`MentorFlowApplication._begin_interactive_login` exception handler uses its
`MESSAGES.get(..., default)` branch, producing the reported generic Chinese error
copy. No user-facing copy was changed.

## Controlled tracing and Backend correlation

Evidence directory: `E:/Codex/Temp/MF-MAIN3-01-FROZEN-15`.
`probe.py` invokes the unchanged `login_native_account_from_env` and records only
allowlisted configuration, stage, exception type, module provenance, request
shape, TLS mode and correlation metadata. It never records credential values,
response credentials, cookies or keys. Instrumentation observes requests without
substituting responses, authentication, TLS, proxies or account identities.

The original EXE probe runs through its existing PyInstaller multiprocessing
helper dispatch (`-B -S -I -c`), after entry imports, using its actual
`PyiFrozenLoader` modules. It does not launch the source application instead.
Source controls use the exact build-source directory and `SourceFileLoader`.
An initial probe-local `exec` namespace error was corrected and excluded from
product evidence; its diagnostic error window was closed.

Backend access logging is disabled in this acceptance runtime. No server patch
or restart was needed: an observer sampled established TCP peers for the exact
Backend PID and port using `GetExtendedTcpTable`, excluding pre-existing peers.
The original frozen attempt produced zero new observed peers and never reached
the HTTP-client boundary. Source and corrected-fixture login connections were
observed on that Backend socket, with matching client ports; the existing
Backend `X-Request-ID` echo corroborated the HTTP responses. The TCP observer is
sampled, not a lossless HTTP audit; the decisive no-request evidence is the
reproduced exception before HTTP-client creation, independently corroborated by
the Backend-side socket observation.

## Bounded fix and frozen retest

01 fix: `_prepare_backend_imports()` adds the backend checkout path only for
source execution. Frozen execution uses the existing canonical `app.*` archive
namespace. Both migration-contract loading and the existing Backend OIDC token
validator use this helper. Missing contracts still fail closed; no migration
policy, issuer allowlist, identity mapping or verifier rule is bypassed.

A separate one-file PyInstaller **diagnostic fixture**, not a candidate, was built
from the original frozen-input manifest, with the corrected
`desktop.enterprise_oidc` and a diagnostic entry point. Archive comparison:

```text
ORIGINAL_PYZ_MODULE_COUNT = 13749
FIXTURE_PYZ_MODULE_COUNT = 13749
MISSING_MODULES = []
ADDED_MODULES = []
CHANGED_PYZ_MODULES = [desktop.enterprise_oidc]
FIXTURE_SHA256 = f81b39ea97ad25290fe2eb27788c0bb03b801424e5949775116fcae1ffc21ac4
```

The unchanged bundled native client in that fixture reaches TCP, verified TLS,
Backend login 2xx, session-schema validation, workspace 2xx, `/auth/me` 2xx and
`AUTH_SESSION_CREATED`. Source and fixture both reject wrong passwords and
unknown accounts with the same external response. Diagnostic sessions alone were
logged out after success; real user data was not accessed.

After the fix, actual constructed config matches source except the expected
`frozen` flag:

| Field | Source and corrected fixture |
| --- | --- |
| issuer | `https://127.0.0.1:58788/realms/mf-ab-3c58f23a06` |
| API origin | `https://127.0.0.1:58789` |
| client / audience | `mentorflow-desktop` / `mentorflow-api` |
| CA path digest | `1d5131e861ddb151f395615e726aa1545e4f8c0e61926f294752f026140eff4b` |
| CA content digest | `f29e91b623a764da7af123537d8250f917223fa23641082e6fcc3682ced2c81c` |
| product mode | native, migration inactive |

Request parity is exact for method, path, content type, header-name set, JSON
schema keys and 10-second connect/read/write/pool timeouts. The client is HTTPX,
not requests. Both use a real SSLContext with `CERT_REQUIRED`, hostname checking
and the same trusted CA certificate digest. `trust_env=True` is unchanged; both
select `AsyncConnectionPool` directly for the API, despite configured proxy
mounts. No proxy setting was disabled. Desktop does server TLS; Backend owns
the existing verifier mTLS connection.

A separate negative fixture run substituted an unrelated public trust certificate
in that child only: it failed at `connection.start_tls.failed`, returning
`NATIVE_NETWORK_UNAVAILABLE`, with certificate verification still required. This
is a negative test, not an alternative runtime configuration or proposed fix.

## Tests and final fields

79 tests passed with the existing Starlette/httpx deprecation warning:

```text
E:/Codex/Temp/mf-e1s1-rp01-test-venv/Scripts/python.exe -m pytest
  desktop/tests/test_frozen_auth_imports.py
  desktop/tests/test_native_login.py
  desktop/tests/test_enterprise_oidc.py
  desktop/tests/test_e109_system_browser.py
  backend/tests/test_e109_issuer_migration.py
  -q --basetemp=E:/Codex/Temp/g15a
```

`comparison.json` asserts config/request/TLS/proxy parity, rejected negative
credentials, Backend correlation, the negative TLS case, archive parity and
runtime preservation. `source-probe.json`, `frozen-probe.json`,
`fixed-probe.json`, `fixed-bad-ca-probe.json`, `*-backend-tcp.json`,
`archive-parity.json`, `preserved-runtime.json`, `auth-fixture.spec` and the build
log contain the detailed evidence. No ordinary UI/main-window or A/B sync pass is
claimed by this authentication-only diagnostic fixture.

```text
STATUS = SOURCE_FIX_PASS_NEW_CANDIDATE_REQUIRED
SOURCE_EFFECTIVE_CONFIG = CREATED; source-probe.json
FROZEN_EFFECTIVE_CONFIG = NOT_CREATED_BEFORE_FIX; MATCHED_IN_CORRECTED_FIXTURE
CONFIG_PARITY = NO_BEFORE_FIX; YES_AFTER_FIX_EXCEPT_FROZEN_FLAG
SOURCE_LAST_SUCCESS_STAGE = AUTH_SESSION_CREATED
FROZEN_LAST_SUCCESS_STAGE = AUTH_MODULE_LOADED
FIRST_DIVERGENCE_STAGE = AUTH_CONFIG_BUILD / _migration_contract
FROZEN_FAILURE_CLASS = NO_REQUEST
BACKEND_SAW_FROZEN_REQUEST = NO_BEFORE_FIX; YES_CORRECTED_FIXTURE
BACKEND_STATUS_CLASS = NOT_SENT_BEFORE_FIX; 2XX_CORRECTED_FIXTURE
REQUEST_SEMANTICS_PARITY = NOT_REACHED_BEFORE_FIX; MATCH_AFTER_FIX
TLS_VERIFY_PARITY = NOT_CREATED_BEFORE_FIX; MATCH_AFTER_FIX
PROXY_PARITY = NOT_CREATED_BEFORE_FIX; MATCH_AFTER_FIX
RESOURCE_MODULE_PARITY = CANONICAL_MODULE_PRESENT; WRONG_NAMESPACE_SELECTED
ROOT_CAUSE = CHECKOUT_BACKEND_PATH_SHADOWS_FROZEN_CANONICAL_APP_NAMESPACE
FIX_OWNER = 01
TLS_VERIFICATION_DISABLED = NO
BROWSER_FALLBACK = NO
PASSWORD_GRANT_USED = NO
DIRECT_GRANT_USED = NO
CURRENT_OLD_FROZEN_ARTIFACT_FIXED = NO
NEW_CANDIDATE_REQUIRED = YES
ACCEPTANCE_RUNTIME_CLEANED = NO
REAL_USER_DATA_TOUCHED = NO
FILES_CHANGED = desktop/enterprise_oidc.py; desktop/tests/test_frozen_auth_imports.py; this receipt
WORKER_STATUS = SILENT
```

A must integrate the delivery commit and rebuild/admit a new ordinary candidate.
The existing CURRENT artifact and acceptance lease were not replaced. This task
does not repair Workbench runtime binding, Server Sync or unrelated packaging.

`git diff --cached --check` and exact integration `git apply --cached --check`
passed. The latter used only a disposable `integration-check.index` under the
evidence directory, leaving the integration worktree/index unchanged.
