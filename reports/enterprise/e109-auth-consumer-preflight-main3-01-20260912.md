# E109 consumer activation preflight — MAIN3 / 01

- TASK_ID: `E109_AUTH_CONSUMER_ACTIVATION_PREFLIGHT_V1`
- DISPATCH_ID: `MF-MAIN3-01-AUTH-CONSUMER-PREFLIGHT-20260912-01`
- Authority: `docs/E109_AUTH_ISSUER_MIGRATION.md` at exact main2
  `04faf2a9fbb50c5f9403dad5abf46354c9b53d82`.
- Scope: source, build inputs, offline consumer tests and next activation gates.
  This is not a deployed-consumer or hostname-cutover receipt.
- STATUS: `READY_FOR_AUTH_CONSUMER_ACTIVATION_WINDOW` at source/test level.
  Backend/desktop deployment and real login remain prerequisites below.

## Phase 0, before writes

| Command | Observed result |
| --- | --- |
| `git rev-parse --show-toplevel` | `E:/Codex/Data/worktrees/8831/MentorFlow` |
| `git rev-parse --git-dir` | `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow1` |
| `git rev-parse --git-common-dir` | `E:/Codex/Projects/MentorFlow/.git` |
| `git rev-parse --git-path index` | `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow1/index` |
| `git rev-parse HEAD` | `04faf2a9fbb50c5f9403dad5abf46354c9b53d82` |
| `git branch --show-current` | empty: detached HEAD |
| `git status --porcelain` | empty: clean |

`git worktree list --porcelain` reported:

```text
worktree E:/Codex/Projects/MentorFlow
HEAD 04faf2a9fbb50c5f9403dad5abf46354c9b53d82
branch refs/heads/main2

worktree E:/Codex/Data/worktrees/2735/MentorFlow
HEAD 04faf2a9fbb50c5f9403dad5abf46354c9b53d82
detached

worktree E:/Codex/Data/worktrees/8831/MentorFlow
HEAD 04faf2a9fbb50c5f9403dad5abf46354c9b53d82
detached
```

`worker/main3/01` and `main2` both resolved to BASE_SHA. No other worktree
owned `worker/main3/01`; attached using `git switch worker/main3/01`.
No force, reset, relocation, integration or remote operations were used.
Root performed all work; no child agents were created.

## Source and configuration findings

| Area | Evidence and result |
| --- | --- |
| Inactive release input | `release/auth-authority.json` remains `PREPARED_NOT_ACTIVE`, `migration: null`; no dates assigned. Build loader returns `None`, so no active authority enters the current manifest. |
| Account switches | `release/account-capabilities.json` remains `register=false`, `forgot_password=false`. |
| Closed trust | `issuer_migration.py` retains the exact OLD/NEW issuers and authority SHA-256 `5662cb790710dde77eaf1d8d49edd6e4d476426def0c2ded73146d38d8d0d550`; integer `[not_before, not_after)` duration is at most 172800 seconds. No trust expansion. |
| Backend compatibility inputs | Dockerfile `backend-image` inherits the current `backend/` copy (tests excluded). The optional Account Core overlay changes only backend's issuer, transport, identity alias and migration JSON. The readiness script is a separate read-only bind mount, and must be delivered with the image. No image was built or deployed here. |
| Backend identity | NEW primary, OLD identity namespace; `OidcIdentityAdapter` constructs the aliased principal after cryptographic validation. Runtime directory construction also uses `canonical_identity_issuer`. |
| Desktop build binding | `build_e108_user_candidate.py` validates active input before writes, records its hash, and rejects future/expired windows. `real-delivery-app.spec` rebinds current desktop source and five backend authentication modules; binary verification compares packaged modules with current source. No App/installer was built or installed here. |
| Desktop entry paths | `windows_installed_launch.py` and `startup_connection.py` both consume manifest `auth_authority` through `bind_auth_authority`; API and callback keys are untouched. |
| Dynamic callback | `DesktopOidcConfig.callback_port=0`; receiver binds `127.0.0.1` and returns its allocated port. Realm registration stays `http://127.0.0.1/oauth/callback`, with S256 and direct grants disabled. Port 18080 remains transport/API, not the OIDC callback. |
| API authority | Desktop API stays `https://localhost:18080`; SSH forward stays `127.0.0.1:18080:127.0.0.1:443`. The backend OIDC loopback proxy keeps its realm-only route. Shared public-origin/hostname variables were not replaced. |
| Verification | RS256/signature, audience/client, expiry, state, nonce, S256 PKCE, configured JWKS authority and TLS verification remain enforced. |

Three bounded defects required fixes:

1. The actual desktop dialog still used embedded WebEngine for E109. NEW-primary
   configuration now opens the system browser from the shared startup/account
   dialog, retains Code + PKCE and the dynamic callback, and keeps system-browser
   behavior after the migration config is retired. Launch failure closes the
   callback with no embedded fallback. PREPARED/legacy defaults remain inactive.
2. `oidc-ready` previously accepted any nonempty error in HTTP 400, including
   `invalid_client`. It now requires `invalid_grant` for the intentional invalid
   authorization-code probe, so broken client/grant configurations cannot pass.
3. The RP-05 source verifier still required the pre-migration literal
   `"issuer": public_issuer`, rejecting main2's bounded discovery implementation.
   It now requires the discovered issuer together with `validate_binding`, the
   exact bounded `accepts_issuer` gate and the strict invalid-grant check.

## Validation evidence

Logs and JUnit XML live under:
`E:/Codex/Temp/MF-MAIN3-01-AUTH-CONSUMER-PREFLIGHT-20260912-01/`.

- Exact-baseline audit: `baseline.log`, 77 passed.
- System-browser regression before fix: `browser-red.log`, six expected failures
  showed both dialogs attempted embedded authentication before/after cutover.
- Final desktop, browser, login shell and startup/build binding regression:
  `desktop-final.log` / `desktop-final.xml`, 104 passed. This includes 13 browser
  gates covering both surfaces, pre/post-cutover and migration-config removal,
  launch failure/exception, cancellation and opposite-issuer callback rejection.
- Backend migration, signed-token continuity, OIDC, membership directory,
  runtime, strict readiness and release checks: `backend-final.log` /
  `backend-final.xml`, 90 passed (one existing FastAPI/Starlette deprecation
  warning). Together with desktop, the final runs pass 194 tests with no skips
  or failures.
- One combined collection attempt (`final.log`) stopped because the desktop
  test environment lacked `procrastinate`. Backend tests use the existing
  Account Core environment instead; no dependencies were installed or changed.
- The first backend expansion (`backend.log`) had 87 passes and the one stale
  source-verifier failure described above. Its targeted rerun plus two release
  checks passed (`asset-final.log`, three passes).
- `asset-verifier.log`: static asset verifier PASS, no failures. Docker Compose
  is unavailable (`docker_compose_config=NOT_RUN_TOOL_UNAVAILABLE`); live gates
  remain PENDING. `config-audit.json` verifies the parsed YAML environment merge
  changes only the four backend auth keys and preserves callback/client/hosts;
  it does not claim actual Docker Compose interpolation was executed.
- `release/auth-authority.json` byte SHA-256:
  `3ecc97e9c40a55347d554abce0a2c1b8d5d0a209b9449c3237062e2716fd9830`.
  All release-input, overlay, base-compose, realm and proxy files remain identical
  to BASE_SHA; their byte hashes are recorded in `config-audit.json`.
- Python 3.11 syntax compilation in memory passed for the four changed production
  Python files. `git diff --check` passed.

Reproduction: use `python -B -m pytest -q -p no:cacheprovider --basetemp <fresh-E-temp>`
with repository root and `backend` on `PYTHONPATH`, plugin autoload disabled,
`QT_QPA_PLATFORM=offscreen`, and scratch `TEMP`, `TMP`, `MENTORFLOW_DB` and
`MENTORFLOW_ORM_DB`. Clear inherited `MENTORFLOW_*` settings before backend tests.

- Backend interpreter: `E:/Codex/Temp/mf-account-core-r1-venv/Scripts/python.exe`,
  Python 3.12.10, pytest 8.4.2, httpx 0.28.1, cryptography 46.0.7, pydantic 2.13.5.
  Modules: `backend/tests/test_e109_issuer_migration.py`,
  `test_e109_consumer_continuity.py`, `test_enterprise_identity_oidc.py`,
  `test_enterprise_identity_context.py`, `test_enterprise_identity_directory.py`,
  `test_enterprise_identity_runtime.py`, `test_account_core_recovery_seams.py`.
  Also `backend/tests/test_e1_release_assets.py::test_rp05_oidc_loopback_bridge_is_lifecycle_and_dns_bound`
  and `::test_rp05_release_includes_the_oidc_loopback_proxy_asset`.
- Desktop interpreter: `E:/Codex/Temp/mf-e1s1-rp01-test-venv/Scripts/python.exe`,
  Python 3.13.14, pytest 8.4.2, httpx 0.28.1, cryptography 46.0.7,
  pydantic 2.13.4, PySide6 6.11.1.
  Modules: `desktop/tests/test_enterprise_oidc.py`, `test_in_app_oidc.py`,
  `test_e109_system_browser.py`, `test_login_smooth.py`, `test_prelogin_update_ux.py`;
  `scripts/test_e109_auth_source_binding.py`, `test_windows_installed_launch.py`,
  `test_single_window_startup.py`.

The continuity rehearsal uses real RSA verification and the real desktop
validator/JWKS resolver over mock HTTPS. It restores existing OLD credentials,
rotates refresh responses under OLD and NEW discovery, and checks unchanged
credential references, vault path, account subject, membership ID and workspace.
Directory SQL results and credential IO are synthetic. Existing backend fixtures
initialize disposable SQLite schemas only; no business database was accessed.
This does not establish live PostgreSQL/RLS, real browser TLS/login, packaged
consumer behavior, or current public signing-key agreement.

## Exact prerequisites for the next live activation

1. **Before assigning any dates:** deployment owner accepts this delivery's
   source commit/tree. Reserve the coordinated operation, owners, rollback
   configurations and evidence locations. Build and pin the compatible backend
   image/digest, record the mounted readiness-script hash, desktop seed/TOC hashes, approved
   update-feed URL, dependency/toolchain and installer/distribution path. Do not
   use an old installed desktop as compatible-consumer evidence.
2. **Read-only authority gate:** recheck OLD transport and NEW public discovery
   against the same realm/client/signing authority. Compare the canonical RSA
   signing-key set as documented in the authority document. Historical key hash
   `58b30e499176955656b3f6edf0b3cfaa305eed75933c2245315ca8ba10ee5a24`
   is an audit reference, not fresh proof. Any divergence requires review before
   scheduling the window. Verify NEW TLS/reachability and the system browser's
   trust of OLD HTTPS during pre-cutover login; no TLS bypass flags or insecure
   certificate acceptance. Confirm IPv4 ephemeral callback registration, the
   existing API tunnel/proxy, realm users and account data are preserved.
3. **Single explicit window:** only after the preceding preparation is complete,
   approve one integer UTC `[not_before, not_after)` window of at most 48 hours
   and the exact authority hash. Supply the SAME JSON object to
   `MENTORFLOW_E109_ISSUER_MIGRATION_JSON` (overlay input), the resulting backend
   `MENTORFLOW_KEYCLOAK_ISSUER_MIGRATION_JSON`, and desktop build `migration` /
   `MENTORFLOW_DESKTOP_ISSUER_MIGRATION_JSON`. Record equality and remaining
   time. No auto-renewal or timestamps derived from this preflight receipt.
4. **Compatible backend before hostname cutover:** deploy the already pinned image;
   inspect the effective merged Account Core config and target only the backend
   consumer. Preserve OLD token/JWKS transport and identity alias with NEW
   primary. Do not recreate Keycloak or run dependency/migration services as a
   side effect. Read back image digest, source modules, mounted script and all
   four effective auth settings. Run `/opt/mentorflow/rp05/runtime-entrypoint.py
   oidc-ready` inside that backend, then verify old-token authentication/refresh
   and the same existing account membership/workspace through the retained API.
5. **Compatible desktop before hostname cutover:** separately authorize the
   active build input (`status=ACTIVE` with the approved window), build while it
   is active, and preserve the recorded config hash and binary/source checks.
   Distribute/install through the approved path. Read back the installed
   manifest: NEW primary, OLD identity namespace, same window, unchanged API and
   callback port `0`. Verify actual system-browser Code + PKCE login against OLD
   discovery, restart/old credentials, refresh, same account/vault/workspace,
   and API access. Check the ordinary launch entry, not merely a source helper.
6. **Hostname cutover gate:** both deployed consumers must have the previous
   evidence and enough approved window time remaining. Until then, no Keycloak
   hostname change. Only the separately authorized deployment owner may cut over;
   then verify NEW discovery and browser endpoint, a fresh login, refresh, same
   account/vault/membership and API access. An in-flight old transaction may be
   retried; never relax state/nonce/issuer checks. Roll back consumers/server as
   a coordinated unit if acceptance fails, preserving users and data.
7. **After acceptance:** OLD token trust expires automatically at the deadline.
   Remove migration config / move backend transport only after separate network
   and TLS checks; retain OLD identity namespace. Registration and password-reset
   capability switches stay false pending their separately authorized NEW-login,
   real verification-mail and password-reset acceptance. No SMTP is part of this
   preflight or implicitly authorized by it.

## Mutation boundary

`MIGRATION_WINDOW_STARTED=NO`; `LIVE_SERVER_MUTATION=0`;
`KEYCLOAK_MUTATION=0`; `SMTP_SEND=0`; `GITHUB_ACTIONS_EXECUTED=0`.
No nginx/public DNS edit, deployment, installer run, account capability enable,
main2/integration-main3 mutation, remote push or other-worker operation occurred.
