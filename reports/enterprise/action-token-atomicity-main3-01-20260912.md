# Action-token atomic single-use repair — MAIN3 / 01

Dispatch: `MF-MAIN3-01-ACTION-TOKEN-ATOMICITY-20260912-06`

Task: `KEYCLOAK_ACTION_TOKEN_ATOMIC_SINGLE_USE_REPAIR_V1`

Result: `PASS_ACTION_TOKEN_ATOMICITY_REPAIR`

## Office continuity

Before writing, the existing office was clean at
`1d78c0b19f63bd8fe7cfb18df181779b7fb1a728`, branch `worker/main3/01`.
The worktree inventory showed this branch owned only by this office.

| PHASE 0 field | Recorded value |
| --- | --- |
| Toplevel | `E:/Codex/Data/worktrees/8831/MentorFlow` |
| Git directory | `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow1` |
| Common directory | `E:/Codex/Projects/MentorFlow/.git` |
| Index | `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow1/index` |
| Starting status | Empty / clean |
| New worktree / branch switch / merge / rebase | None |

All writes belong to 01. No other office or shared branch was changed. No children
were spawned. The final commit and artifact hashes are in the delivery receipt.

## Root cause and negative controls

`ROOT_CAUSE_VERIFICATION_RACE`: the native email handler inherits
`AbstractActionTokenHandler.canUseTokenRepeatedly() == true`. It tests mutable
email/required-action state, then writes that state and emits success without an
atomic token claim. Concurrent handlers can therefore both pass. Database scheduling
can also make a loser fail while deleting `USER_REQUIRED_ACTION`; that incidental
transaction failure is not a single-use guarantee.

`ROOT_CAUSE_REARM`: no consumed record is created for that repeatable email token.
Adding `VERIFY_EMAIL` again makes the old signed token eligible for success. The
confirmation link changes its authentication-session binding but retains the native
action key, so the repair binds consumption to that stable key, not the URL or session.

`ROOT_CAUSE_RESET_500`: `canUseTokenRepeatedly == false` performs a used-record read,
then arranges invalidation through an authentication-session note. The native reset
flow and credential write can run before final invalidation in
`AuthenticationManager.finishedRequiredActions`. Pending profile requirements delay
that completion. Concurrent credential updates then race on `CREDENTIAL.VERSION`;
the losing JTA commit raises `OptimisticLockException` / `StaleObjectStateException`
outside the handler's controlled verification-error path. The final upstream
PostgreSQL fixture reproduced one success plus HTTP 500 at concurrency 2, with the
stack through `JtaTransactionWrapper.commit` and a version-checked `CREDENTIAL` update.
At concurrency 5 it even recorded three password successes and two 500 responses.

The read-only 04 receipt was
`E:/Codex/Temp/mf-e109-sec04-15df052cc2a2-result.json`: verification 2 successes,
used-after-rearm 1 new success, reset responses 500/200 with 1 success.
The new independent negative controls use no admin grant or SMTP:

- H2 upstream: `E:/Codex/Temp/mf-native-6ac8c23165cc/receipt.json`.
- PostgreSQL upstream: `E:/Codex/Temp/mf-native-b0dc1c2ec5b4/receipt.json`.
- Earlier PostgreSQL upstream run `mf-native-45fcb2e2e565` recorded 3 verification
  successes at concurrency 5 and 6 at concurrency 10.
- Upstream copied reset forms can return another password form to losers. The final
  repair closes that continuation as well as initial link redemption.

The upstream failures are intentional negative controls, not accepted delivery runs.
The first parallel baseline startup also exposed a disposable HTTP port 8080 collision;
the fixture now assigns its HTTP listener a dynamic loopback port.

## Bounded implementation

Four production Java files add the authority guard to the existing verifier JAR:

- `AtomicActionTokens`: atomic `SingleUseObjectProvider.putIfAbsent` keyed by realm
  plus native token key. Only the winner writes Keycloak's persistent revocation
  record. The preliminary read is not the concurrency guarantee. Claims are never
  released on re-arm, resend or handler failure.
- `AtomicVerifyEmailActionTokenHandler`: preserve the fresh-browser confirmation
  page, then claim before email/required-action mutation. Already consumed links
  receive a generic expired-action HTTP 400 without deleting the winner's session.
- `AtomicResetCredentialsActionTokenHandler`: retain native realm/email checks,
  reject the verifier's pre-handler required-action continuation, and claim before
  entering the reset flow. Bind its session to the signed native action key.
- `AtomicResetPassword`: only bound reset sessions gain an atomic completion claim
  before credential mutation. Copied forms cannot restart a consumed action. Native
  missing/mismatch and password-policy correction remain usable. Other password
  required actions retain the stock implementation.

There is no application token database, global/user lock, timing delay or frontend
single-use state. The Keycloak retention margin is 60 seconds beyond signed expiry;
token validity itself is not extended. Existing signatures, issuer/client binding,
email binding, TLS, password policy and final expiry checks remain enforced.

The builder registers both action-token factories and the password-action factory
at order 100. Its explicit source list excludes the lab control. The native credential
verifier source was untouched; all three existing compiled verifier classes are
byte-for-byte identical to the previous delivery's JAR.

## Accepted evidence

Final run: `E:/Codex/Temp/mf-native-c11c36d9bba7/receipt.json`

Identity: real Keycloak **26.7.3**, JDK **21**, PostgreSQL **17.6**.

Checks: **61/61 PASS**, including **28 native verifier/desktop checks**.

Earlier H2 acceptance of the final production classes: **59/59 PASS**,
`E:/Codex/Temp/mf-native-defa4ffb6f79/receipt.json`.

| Test | Committed success events | Other responses |
| --- | --- | --- |
| Verification concurrent 2 / 5 / 10 | 1 / 1 / 1 | All HTTP 400 |
| Verification replay / old after re-arm | 0 / 0 | HTTP 400 |
| Verification new token after resend | 1 | Success |
| Reset concurrent 2 / 5 / 10 | 1 / 1 / 1 | All HTTP 400 |
| Reset with incomplete profile, concurrent 2 / 5 | 1 / 1 | All HTTP 400 |
| Copied reset form, concurrent 2 / 5 / 10 | 1 / 1 / 1 | All HTTP 400 |
| Reset replay / old after re-arm | 0 / 0 | HTTP 400 |
| Reset new token after resend | 1 | Success |
| Both consumed tokens after Keycloak restart + re-arm | 0 / 0 | HTTP 400 |
| Wrong signature/email, malformed/expired, both purposes | 0 | HTTP 400 |
| Cross-purpose execution/path attempts | No opposite-purpose success | PASS |
| Missing/mismatch/short-password correction | 0 until corrected, then 1 | Native feedback retained |

Tests use independent HTTPS clients and actual thread barriers. Native event-store
queries run after requests complete and count committed successes; the fixture never
implements redemption. No tokens, passwords, cookies or confirmation URLs are stored
in the receipt. The signer/control route requires the fixture's pinned mTLS certificate,
loopback source, explicit fixture enablement and a random fixture realm.

Real native regression covers the Qt form, authoritative password validation,
wrong/unknown/disabled uniform responses, session issuance, Windows Credential Locker,
restore/rotation, logout/revocation, existing workspace membership, workspace bootstrap,
main-window gate, required-action/MFA refusal, expiry, unavailable identity and timeout.
Browser entry functions fail the fixture if invoked; no password/direct grant is used.

Focused pytest evidence: **96 passed**.

```text
Desktop Python 3.13: 44 passed in 12.43s
desktop/tests/test_native_login.py
desktop/tests/test_login_gate_bootstrap.py
desktop/tests/test_enterprise_account_shell.py

Backend Python 3.12: 52 passed in 82.50s
backend/tests/test_native_auth.py
backend/tests/test_enterprise_identity_compatibility.py
backend/tests/test_enterprise_identity_runtime.py
scripts/test_e109_auth_source_binding.py
scripts/test_e108_candidate_paths.py
```

Both runs used `-q -p no:cacheprovider`, disabled plugin autoload and separate E-drive
basetemps. The backend emitted only the existing Starlette/httpx deprecation warning.
Python 3.11 syntax parsing, JDK compilation and `git diff --check` also passed.

Reproduce the real acceptance with the backend test Python:

```powershell
python scripts/native_identity_fixture.py --keycloak-zip <keycloak-26.7.3.zip> --jdk <jdk-21> --desktop-python <PySide6-python> --output <E-drive-scratch> --action-tokens --postgres-bin <postgresql-17.6/bin>
```

Add `--upstream-action-handlers` for the expected-failing negative control. Omit
`--postgres-bin` for a disposable H2 database. No existing cluster is reused.

## Delivery and later deployment gate

Artifact directory:
`E:/Codex/Temp/MF-MAIN3-01-ACTION-TOKEN-ATOMICITY-20260912-06`.

JAR: `mentorflow-native-verifier-26.7.3.jar`

SHA256: `4ea0e9407c271f778deda101541ff8c17038cae8300c80e27e3e16aa0461d32f`.

The directory includes the accepted receipt/log, upstream negative receipt/log,
source/build manifest and final delivery receipt. Only the production JAR is a
deployable candidate; `fixture-control.jar` must never be deployed.

All owned Keycloak, API and PostgreSQL processes stopped, and generated TLS private
key files were removed. Automatic command approval rejected recursive removal of
the stopped fixture directories, including a second attempt using individually
verified literal paths; it returned only `blocked by policy`. Eleven disposable
Keycloak directories and four PostgreSQL data directories remain offline under their
named E-drive fixture roots. `owned-fixture-cleanup.json` lists them explicitly.
No deletion through another shell/tool was attempted after those refusals.

Before any separately authorized deployment: match exact Keycloak/version and JAR
hash; drain or explicitly invalidate outstanding pre-extension verification tokens;
ensure every identity node uses these factories and shares the native single-use
authority with revocation persistence; repeat acceptance on the actual target topology.
Installing this JAR activates its action handlers. The private credential endpoint
still requires its existing explicit mTLS configuration. The disposable tests qualify
one Keycloak process with H2/PostgreSQL, not a distributed cluster or installed-app UAT.

Production Keycloak, production data/users, SMTP, public hostname, DNS/nginx, account
capabilities, installer/updater and GitHub Actions were not changed or executed.
`release/auth-authority.json` remains `PREPARED_NOT_ACTIVE`, `migration:null`.
No migration window, remote push or external-browser normal login occurred.

Upstream API references:
[email handler](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/actiontoken/verifyemail/VerifyEmailActionTokenHandler.java),
[reset handler](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/actiontoken/resetcred/ResetCredentialsActionTokenHandler.java),
[single-use store](https://github.com/keycloak/keycloak/blob/26.7.3/model/infinispan/src/main/java/org/keycloak/models/sessions/infinispan/InfinispanSingleUseObjectProvider.java),
[action completion](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/services/managers/AuthenticationManager.java).
