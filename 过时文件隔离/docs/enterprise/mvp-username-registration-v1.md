# Restricted MVP username registration candidate

Dispatch: `MF-A-MVP-REGISTRATION-20260906-01`.
Task: `MENTORFLOW_ACCOUNT_CORE_MVP_USERNAME_REGISTRATION_V1`.
Accepted source: `1531c65116063ec606ed1c623acd29fb68e9e8b7`, tree `c441dbfd48fa4bedcac1ee7212866728bc8ac03c`.

This is an offline configuration candidate for E1-06 to apply under its live
authority, **not evidence that registration is enabled on the server**. A has not
connected to a VPS, created users, handled a user password, or run live registration.

## Exact scope

The standalone `account-core-registration-policy.json` is an explicit
`account-core-mvp` overlay consumed only by the operator command below. It is not
loaded at service startup and does not change the shared Enterprise realm import,
Compose profiles, Desktop, migrations or product identity code.

| Resource | Allowed change | Retained |
| --- | --- | --- |
| Existing `mentorflow` realm | registrationAllowed=true; registrationEmailAsUsername=false; verifyEmail=false; resetPasswordAllowed=false | Password policy (including absence), brute-force settings, clients, flows, roles, groups, users, sessions and network access |
| Current User Profile | Remove `required` from email, firstName, lastName | Current validators, permissions, annotations, selectors, groups, custom fields and username configuration |
| VERIFY_EMAIL | defaultAction=false | enabled, provider, priority, name and config |
| VERIFY_PROFILE and all other actions | No changes | Existing profile validation, password update, MFA and other required actions |

Fields may still be displayed but are optional; values are never fabricated. The
existing inherited Keycloak registration form supplies password and password
confirmation. Password requirements remain server-enforced. No registration API,
password bridge or new form is introduced. The existing login template already
conditions registration/reset links on these realm flags.

**These are realm-wide settings, not per-client settings.** E1-06 must confirm
this realm/server is dedicated to the restricted MVP. Every client in this realm
will see the changed registration/Profile rules. If full Enterprise workloads
share it, STOP for architecture review; do not use the confirmation flag falsely.
Other realm instances/profile source defaults are untouched. Do not open public
ingress as part of this procedure.

## Preconditions and operator files

Use the deployed Keycloak **26.7.3** Admin CLI and an already authenticated private
Admin CLI session in that execution environment. Confirm the version before use.
Do not put passwords/tokens in command arguments, prefix JSON, logs, receipts, or
this repository. This script neither authenticates nor creates an admin session.
If no session exists, E1-06 must use its existing approved authentication procedure.

Stop concurrent realm administration for the apply/rollback window. The Admin API
does not provide a cross-resource transaction or compare-and-swap here. Per-write
drift checks are optimistic checks, **not an atomic lock**. Acquire the existing
operator maintenance authority; do not treat a passing plan as a concurrency lease.

On the authorized operator host, prepare a nonsecret JSON argv prefix file. A direct
installed CLI example is:

```json
["/opt/keycloak/bin/kcadm.sh"]
```

For the existing MVP Compose deployment, use its actual current env/release paths:

```json
["docker", "compose", "--profile", "account-core-mvp", "--env-file", "/actual/private/current.env", "-f", "/actual/release/deploy/enterprise/staging/rp05/compose.account-core-mvp.yml", "exec", "-T", "keycloak", "/opt/keycloak/bin/kcadm.sh"]
```

Paths labelled `/actual/...` are operator substitutions, not discovered server
facts. `--session-config` is the existing private session path **inside the CLI
environment/container**; `--prefix-file` and `--plan-dir` are on the host running
Python. Plan files have no login tokens or user data but contain internal config:
store them privately, never in Git. Linux directory/file modes are 0700/0600;
on Windows E1-06 must enforce an equivalent private ACL. Do not reuse a plan
directory from another execution target or change prefix/session paths mid-plan.

## Executable existing-realm procedure

Run from the exact candidate source root using Python 3 and the standard library.
Set these nonsecret shell variables to the approved actual paths first:

```sh
REG_PREFIX=/actual/private/mvp-kcadm-prefix.json
REG_SESSION=/actual/private/existing-kcadm.config
REG_PLAN=/actual/private/new-registration-plan

python3 scripts/enterprise/configure_mvp_registration.py plan \
  --profile account-core-mvp --confirm-exclusive-mvp-realm \
  --prefix-file "$REG_PREFIX" --session-config "$REG_SESSION" --plan-dir "$REG_PLAN"
```

`plan` performs GETs only. It freezes before.json, after.json and plan.json with
canonical hashes and target/policy binding. The new directory must not exist.
Review the diff privately: precisely four realm booleans, three optional profile
attributes and VERIFY_EMAIL.defaultAction. Preserve the original password policy
even if absent. Any unexpected required custom field/standard schema stops the
command for owner review, not silent removal. Additional actions are fingerprinted
including VERIFY_PROFILE; they are never disabled or overwritten.

Once approved, execute:

```sh
python3 scripts/enterprise/configure_mvp_registration.py apply \
  --profile account-core-mvp --confirm-exclusive-mvp-realm \
  --prefix-file "$REG_PREFIX" --session-config "$REG_SESSION" --plan-dir "$REG_PLAN"
python3 scripts/enterprise/configure_mvp_registration.py verify \
  --profile account-core-mvp --confirm-exclusive-mvp-realm \
  --prefix-file "$REG_PREFIX" --session-config "$REG_SESSION" --plan-dir "$REG_PLAN"
python3 scripts/enterprise/configure_mvp_registration.py apply \
  --profile account-core-mvp --confirm-exclusive-mvp-realm \
  --prefix-file "$REG_PREFIX" --session-config "$REG_SESSION" --plan-dir "$REG_PLAN"
```

The second apply must report `ALREADY_MATCHED` without PUTs. Application order:
User Profile → VERIFY_EMAIL → realm registration flags; each PUT is read back.
Realm PUT sends only the four permitted fields. Profile/action PUT sends a copy
of the current representation with the permitted delta, **not factory defaults**.
CLI `-f - -n` sends JSON via stdin and disables kcadm's additional implicit merge.
Do not restart with `--import-realm`, delete/rebuild the realm or recreate users.

Rollback uses the exact saved baseline and reverse order, including interrupted
application where resources match either frozen before or after:

```sh
python3 scripts/enterprise/configure_mvp_registration.py rollback \
  --profile account-core-mvp --confirm-exclusive-mvp-realm \
  --prefix-file "$REG_PREFIX" --session-config "$REG_SESSION" --plan-dir "$REG_PLAN"
```

Expect `ROLLED_BACK_VERIFIED`, or `ALREADY_MATCHED` when already rolled back. If a
third-party edit/readback mismatch exists, fail closed; preserve plan and error
evidence for owner review. Do not force overwrite a newer config. Rollback changes
configuration only: users legitimately created meanwhile and their data are not
deleted. A failed multi-resource request is not claimed to be automatically rolled
back; use the guarded command explicitly after inspection.

## Limitations and live acceptance for E1-06

- No SMTP is configured: both automatic email verification and email recovery are
  disabled for this MVP; no alternate password recovery is promised.
- Existing per-user VERIFY_EMAIL/UPDATE_PROFILE/security actions are not cleared;
  an explicitly assigned legacy action can still challenge an existing user. Do
  not set emailVerified=true in bulk. Investigate individual legacy cases separately.
- VERIFY_PROFILE remains enabled/default as it was. Keycloak reevaluates against
  the new profile; other invalid values or unrelated security actions still apply.
- A real duplicate-username submission must reject rather than overwrite. Synthetic
  registration fields must not grant an admin role, tenant or existing data owner.
- Live acceptance: synthetic username-only registration → PKCE login → auth/me 200
  → initialize `{}` creates LEARNER personal space → logout/relogin retains issuer,
  subject and space. Verify attempted role/tenant injection and A/B space access are
  denied, with no SMTP. Do not occupy username `114514` or obtain its password.
- Real registration, PostgreSQL RLS and server configuration application are
  `NOT_RUN` by A. Isolated SQLite authorization checks do not prove live RLS.

## Offline evidence

`tests/theme/test_mvp_registration.py` exercises minimal merge, original password
policy preservation, unsupported extra fields, idempotence, partial apply/rollback,
drift/readback failure, stdin update boundaries, plan integrity, unchanged shared
security sources and a genuinely signed email/name-free JWT through the existing
verifier and display-identity fallback. Theme tests cover inherited registration
and reset conditionals. Selected existing OIDC and workspace tests cover security
regressions, not a new user signup on Keycloak.

## Mechanism references

The existing User Profile GET/PUT endpoints and required-action representations
are documented in the [Keycloak 26.7.3 Admin REST API](https://www.keycloak.org/docs-api/26.7.3/rest-api/index.html).
The [Admin CLI documentation](https://www.keycloak.org/docs/latest/server_admin/index.html#the-admin-cli)
describes stdin input and no-merge updates. The [26.7.3 VERIFY_PROFILE implementation](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/requiredactions/VerifyUserProfile.java)
reevaluates profile validation; the [VERIFY_EMAIL implementation](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/requiredactions/VerifyEmail.java)
explains why existing individually assigned email actions are a separate limitation.
