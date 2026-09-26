# E109 security native runtime — Stage 2

Dispatch: `MF-MAIN3-04-SECURITY-NATIVE-STAGE2-20260912-02`.
Task: `E109_ACCOUNT_SECURITY_NATIVE_RUNTIME_STAGE2_V2`; employee 04 / MAIN3.
2026-09-12; this is a continuation of the Stage 1 PARTIAL, not final product acceptance.

## Office and integration reference

The existing office was reused without reset, rebuild, switch, or a new worktree:

- Top-level: `E:/Codex/Data/worktrees/b18c/MentorFlow`.
- Branch: `worker/main3/04`.
- Starting HEAD: `f5a65e18167f70eb2c9a435d10cb7b3f150d3bfd`; clean.
- Git dir: `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow4`.
- Common dir: `E:/Codex/Projects/MentorFlow/.git`; worktree-specific `index` and `HEAD`.
- `integration/main3` read at the expected snapshot
  `15f6d0dda0968be1b39295577a139a9017615e82`.
- Read its `CURRENT_STATE.md`, `MAIN3.md`, and `DEPENDENCY_QUEUE.json`: Batch01 source
  accepted, account security remains partial, public issuer / SMTP require Gate A.
  No merge, cherry-pick, rebase, checkout, write, or push to integration. No polling 01.

## Environment admission

`POSTGRES_NATIVE=BLOCKED_ENVIRONMENT`. No current isolated RLS/bootstrap DSN or PG
connection/service environment was configured. PostgreSQL, Docker/Podman, and Keycloak
were not running; Docker/Podman were unavailable and WSL had no installed distribution.
Local PostgreSQL 17.6 binaries were found, but the nearby prior gate folders contained
historical CI logs and SQLite test output. A stopped secondary PostgreSQL data directory
had no current authorization/provenance establishing a reusable disposable target.
It was not started or opened. Binary availability is not environment authorization.

The two existing native gates remain valid entry points after explicit environment admission:

- `test_real_postgres_rls_is_fail_closed_and_pool_safe` requires the isolated native test
  database and migration role; it includes DDL and must never be given a live DSN.
- `test_real_isolated_postgres_first_use_rls_if_provisioned` requires a pre-provisioned,
  isolated 0035 database and the application role. Its role checker rejects superuser,
  BYPASSRLS, table ownership, and missing FORCE RLS. Role flags, SELECT/INSERT/UPDATE/DELETE,
  pool A→B→A, rollback leakage and WITH CHECK are **NOT_RUN** here, not inferred from SQLite.

No safe local Keycloak runtime/test fixture was available. The only local Keycloak JARs
found were theme resources. No Java runtime, Keycloak server or token-redemption fixture
was available through the inspected paths. The existing email UAT imports live admin
access and sends Mailpit messages; it was neither imported nor run.

Verification and reset VALID/WRONG/EXPIRED/MALFORMED/USED/REPLAY/SECOND USE/concurrent double
redemption and cross-purpose rejection therefore remain **PENDING_SAFE_NATIVE_KEYCLOAK_FIXTURE**.
We did not invent a token service, use OIDC access-token tests as email-token evidence, or
substitute rejected disabled routes for successful native redemption. The Stage 1 concern
about verification's strict consumption guarantee remains open. A second success in the
future concurrent native test must fail acceptance; absent native evidence is not a pass.

## Native rate-limit defect and source fix

The old nginx map keyed required-action email POSTs using raw `$arg_execution`. With the
email bucket exhausted, the literal `execution=VERIFY_EMAIL` request was rejected, but
`execution=%56ERIFY_EMAIL` bypassed the map and reached the upstream (404 instead of 429).
This was reproduced against a real nginx process, using loopback traffic and the exact
checked-in map/rate/limit directives; it was not a Python simulation of a rate limiter.

The source now matches normalized request method/path for every required-action POST.
Encoded, repeated, missing or different execution arguments cannot select an empty key.
The existing thresholds are unchanged: token 30/min burst20, login 10/min burst10,
email 3/min burst3, with `nodelay` and status 429. This intentionally makes **all**
required-action POSTs, including UPDATE_PASSWORD, share the existing email-action budget.
Repeated password-action submissions can consequently require the existing refill wait.
Ordinary authentication POSTs retain the login budget; GETs retain empty keys.

Only the checked-in source template changed. No live nginx config, hostname, issuer migration,
server, Keycloak realm, release capability, Cloudflare or CI configuration was changed.
The fix is for subsequent Batch02 review, not an applied server deployment.

## What actually ran

`backend/tests/test_e109_security_native_stage2.py` starts the real Account Core ASGI app
on a reserved IPv4 loopback socket with Uvicorn and lifespan disabled. It reuses the
existing synthetic OIDC fixture and isolated SQLite workspace bootstrap fixture. The
four HTTP cases create an existing synthetic account via HTTP, confirm its membership
exists and an absent subject does not, and compare both inputs on disabled account actions.

Both authenticated and unauthenticated GET/POST cases compare status, exact response body,
error code, content type, redirect and cookie behavior. Authenticated requests yield 404,
unauthenticated requests 401, without account identifiers. A fixed nonsecret request ID
removes response-correlation noise. **This proves closed-capability application HTTP
behavior only**, not the open Keycloak registration/forgot-password enumeration contract.

Three native nginx cases extract the current source maps and directives into disposable
loopback-only configs, proxying to that real ASGI process. The upstream's 404 is the expected
unthrottled result because Account Core does not own `/realms/` email endpoints. The tests
prove nginx admission/rejection, not native Keycloak business behavior.

Each case checks the configured burst, a 429 by burst+2 requests, no bypass by username,
client_id or forged X-Forwarded-For/X-Real-IP/Forwarded, independence of actual source
127.0.0.2, auth GET/health GET preservation, and recovery after 60/rate + 0.3 seconds.
The email case additionally checks literal/encoded/repeated execution arguments and
UPDATE_PASSWORD. Initial native bucket accounting may admit burst or burst+1 requests;
the observed count is recorded, and the burst runs faster than one refill interval.
No thresholds were reduced or clocks patched to obtain a pass.

The existing key is real client IP, not user/tenant. A shared NAT can temporarily share a
budget; tests do not claim per-user independence under one IP. A different real source is
unaffected, and a blocked source recovers; no permanent lockout was introduced. Native
Keycloak's per-account brute-force/lockout behavior remains pending its safe runtime fixture.

Local nginx was downloaded over HTTPS from the [official Windows distribution](https://nginx.org/download/nginx-1.31.5.zip)
and reported `nginx/1.31.5`. This is a local Windows native engine, **not the deployed
Linux image**. [Windows support details](https://nginx.org/en/docs/windows.html) and
[limit_req semantics](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html)
describe its runtime scope and leaky-bucket behavior. All test processes bind only loopback
and are shut down by their exact temporary prefix/PID; no installed or remote service is restarted.

- Archive SHA256: `00ad32a2bf66cee0ec8eb194347e8e79917f47017ccd3ad4bebf5574fabe002c`.
- Executable SHA256: `1e8ece22ddfa93c5ef99e50a1bc9060bb6e2e42b8a44843cf2df9bc5c12ee44f`.
- Download/extraction directory: `E:/Codex/Temp/MF-MAIN3-04-native-stage2-tools`.
- Generated nginx configs contain no live hostnames, secrets or SMTP settings. Access logs are off;
  local error logging is critical-only and request payloads are synthetic identifiers.

## Reproduction and remaining gates

Final local runner result: **109 passed, 0 failed, 0 skipped** (7 runtime, 5 backend
capability, 88 desktop capability, 9 source-policy contracts). Native observed admissions
before rejection: token 20, login 10, email 4; the configured burst/rates were 20/30,
10/10 and 3/3 respectively. All three recovered after their unmodified refill interval.
PostgreSQL and native email-token tests are outside this run and remain NOT_RUN, not
unreported successful tests. The local nginx process pool was empty after completion.

From this office, with the previously available backend/desktop Python environment:

```powershell
& 'E:\Codex\Temp\mf-e1s1-rp01-test-venv\Scripts\python.exe' scripts/e109_security_native_stage2.py --nginx-exe 'E:\Codex\Temp\MF-MAIN3-04-native-stage2-tools\nginx-1.31.5\nginx.exe'
```

The runner uses a new scratch directory under `E:/Codex/Temp`, captures raw pytest output
without printing it, clears DB DSNs and pytest injection settings, and emits only fixed
status/count labels plus numeric rate observations. Each group has a 180-second timeout.
Exact expected tests are runtime 7, backend capability 5, desktop capability 88, policy 9.
Missing/skipped tests, timeouts, errors or failures cannot produce `local_runtime_checks=PASS`.
The output status remains PARTIAL even when these local checks pass. No native PostgreSQL
or Keycloak PASS can be generated by this runner.

The prior embedded/in-app bypass and release/backend capability gates are rerun; both
`register` and `forgot_password` remain false. No source from another worker is needed for
these local tests, so there is no cross-worker implementation import or synchronization.

Next gate: provide an explicitly isolated native PostgreSQL test environment and a safe
native Keycloak fixture capable of preparing/redeeming both action-token purposes without
SMTP or production data. Run all missing native checks, including strict concurrent
single-use and open-flow anti-enumeration. Public NEW issuer and real SMTP verification/reset
remain PENDING_GATE_A. Batch02 integration/deployment and capability enablement are not performed.
