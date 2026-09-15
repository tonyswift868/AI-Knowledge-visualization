# Native principal fixture contract repair

Base: `e6bac890657103e20266f0f7b796b4d8aa54f623`, tree
`4a8c044e7f18834ded85e081ccf1927de1e1064a`. Worker 01 was rebased onto
this exact base without merging control or changing integration.

## Diagnosis

ROOT_CAUSE = STALE_TEST_FIXTURE

FIRST_REJECTED_FIXTURE = `backend/tests/test_native_auth.py::principal`,
imported by `test_change_password_binding.py`.

The A workspace's retained pytest failure cache names:

- `test_change_password_binding[expired]`
- `test_actual_runtime_revalidation_accepts_native_actor_without_session`

Both create a native session using this shared helper before password mutation.
Unmodified standalone suites also sometimes pass (11/11 on both Python 3.11.15
and 3.13.14); the defect depends on clock sampling and is not deterministic.

The exact original helper was extracted with Python AST and executed against
the unchanged `NativeSessions._principal`. Under A's Python 3.11.15 it was
rejected at sample 20045 with these nonsecret fixture values:

| Field | Actual value |
| --- | --- |
| exact type | `app.enterprise.identity.models.AuthenticatedPrincipal` |
| issuer | `https://identity.example.test/realms/mentorflow` |
| subject | `user-a` |
| actor kind | `HUMAN` |
| audiences | `('mentorflow-api',)` |
| client | `mentorflow-desktop` |
| authentication strength | `password_verified` |
| issued_at | `2026-09-15T07:48:40.395903+00:00` |
| expires_at | `2026-09-15T07:53:40.395903+00:00` |
| validation clock | `1789458520.3959029` |
| issued_at minus clock | `2.384185791015625e-07` seconds |

`_principal()` correctly requires a typed principal, exact issuer, HUMAN,
audience/client/strength, issuance and not-before no later than its clock, and
unexpired credentials. The fixture passed the type and authority checks but
failed `principal.issued_at.timestamp() > self.clock()` by one float ULP.

EXPECTED_PRINCIPAL_CONTRACT: the native verifier constructs an
`AuthenticatedPrincipal` using `datetime.fromtimestamp(int(time.time()), UTC)`
with a five-minute expiry. This already prevents Windows microsecond/float
rounding from appearing as future issuance.

ACTUAL_FIXTURE_CONTRACT: the same type and authority fields, but
`datetime.now(UTC)` with microseconds. The sessionless Bearer fix changed neither
`native.py` nor `models.py`; there is no production principal authority change.

## Repair and security boundary

The shared test helper now follows the production verifier's integer-second
issuance. No production file changes. No tolerance, dictionary acceptance,
missing identity allowance, or validation bypass was added.

A deterministic regression uses the exact rejected clock sample: the repaired
fixture passes, while the original 0.238-microsecond future timestamp must still
be rejected. Additional tests retain malformed-object, missing issuer/subject,
and issuer/audience/client/strength rejection. The existing binding suite
continues to reject mismatched issuer and subject at reauthentication.

No live runtime, account, password, SMTP, EXE, Workbench or GitHub Actions operation
was performed. `tonyswift` was not read or modified.

## Validation

- Python 3.11.15 (A's existing `.venv` interpreter): the two exact failed tests
  plus deterministic issuance regression: **3 passed**.
- Python 3.11.15: `test_change_password_binding.py`, `test_native_auth.py`,
  `test_enterprise_identity_runtime.py`: **63 passed, 0 failed, 0 skipped**.
- Python 3.13.14: binding and native auth suites: **55 passed, 0 failed,
  0 skipped**.
- The unchanged change-password binding suite contributes **11 passed** in
  each run, including sessionless Bearer, wrong current password, expired and
  revoked tokens, missing actor, issuer/subject mismatch and optional session.
- `git diff --check`: PASS. Only the shared fixture/tests and this diagnosis
  document differ from the authoritative base.
