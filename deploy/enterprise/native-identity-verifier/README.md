# Native identity verifier — Keycloak 26.7.3

This extension completes the identity-side seam in `docs/ADR_NATIVE_CREDENTIAL_AUTH_V1.md`.
It remains disabled without explicit server configuration. It is not a token-grant
endpoint or a replacement password database. No production server was changed.

## Build and isolate

Use JDK 21 and the exact Keycloak 26.7.3 distribution:

```powershell
python scripts/build_native_identity_verifier.py --keycloak <keycloak-26.7.3> --jdk <jdk-21> --output <scratch>/mentorflow-native-verifier.jar
python scripts/native_identity_fixture.py --keycloak-zip <keycloak-26.7.3.zip> --jdk <jdk-21> --desktop-python <python-with-PySide6> --output <E-drive-scratch>
```

The fixture needs the backend test dependencies, cryptography, uvicorn, and a Windows
desktop Python with the existing desktop test dependencies. It starts fresh H2-backed
Keycloak and SQLite-backed account-core instances on dynamic IPv4 loopback ports.
Both network hops use verified TLS; the identity hop additionally uses mTLS. Realm
import creates only random disposable credentials, then removes the import file.
The fixture's separate `NativeIdentityFixtureControl.java` is never included by the
production builder. Never deploy `fixture-control.jar`.

The native Qt form, real password provider, workspace API, account metadata/Vault,
and Windows Credential Locker execute in the acceptance. The business page renderer
and local inference service are substituted after bootstrap; the actual `_show_main`
guard runs. This proves the authentication gate, not a complete installed-app UAT or
PostgreSQL RLS qualification. The receipt records exact checks and process cleanup.

## Private contract

`POST /realms/<exact-realm>/mentorflow-native-identity/v1` accepts only JSON:

* Authenticate: `operation`, `username`, `password`.
* Revalidate: `operation`, `issuer`, `subject`, `verification_handle`.

The backend also retains the exact `/mentorflow-native-identity/v1` proxy alias;
arbitrary realm paths, redirects and issuer changes are rejected. Success is the
strict `mentorflow.native-identity.v1` contract in the ADR. No password, password
hash, provider token, client secret or administrative credential is returned.
The private handle is unrelated random material; only Keycloak retains its
credential binding. Desktop receives only MentorFlow opaque session material.

The SPI recognizes only the stock local-password/conditional-2FA policy. It refuses
unsupported flow shapes, required MFA/other credentials, federation, organizations,
client consent, required actions and failed trigger evaluation. Temporary passwords
are represented by Keycloak's `UPDATE_PASSWORD` required action; realm import must
seed that action explicitly. No login endpoint clears or satisfies a required action.

Unknown/wrong/disabled/locked responses have identical status/body. A dummy password
hash uses Keycloak's own provider for absent/disabled/locked users. This is not a
statistical anti-timing proof. Limits are five attempts per normalized submitted name,
100 total logins and 200 revalidations per minute, plus the backend's peer/account
limits and Keycloak's own account lockout. The backend enforces a ten-second total
verifier deadline and an 8192-byte streamed response limit.

## Prerequisites for a later deployment

1. Review the JAR/source hash against exact Keycloak 26.7.3 and qualify the target realm
   policy. Install the production JAR only in a separately authorized deployment.
2. Configure raw backend-to-Keycloak TLS with client authentication (`request` or
   `required`) and a trusted client CA. Pin the approved backend leaf certificate.
   Do not terminate this mTLS hop at a proxy or substitute certificate headers.
3. Restrict the verifier route to the private backend network at ingress/firewall.
   Anonymous and unapproved-cert requests must fail even if accidentally routed.
   Never publish this route for desktop or general Internet access.
4. In the identity process explicitly set `MF_NATIVE_VERIFIER_ENABLED=1`,
   `MF_NATIVE_VERIFIER_REALM`, `MF_NATIVE_VERIFIER_ISSUER` (canonical identity namespace),
   and `MF_NATIVE_VERIFIER_BACKEND_CERT_SHA256`. No values are supplied by desktop.
   Keep this process single-instance until distributed rate-limit behavior is qualified.
5. In the backend configure `MENTORFLOW_NATIVE_IDENTITY_ENDPOINT`,
   `MENTORFLOW_NATIVE_IDENTITY_CA_FILE`, `MENTORFLOW_NATIVE_IDENTITY_CERT_FILE`,
   `MENTORFLOW_NATIVE_IDENTITY_KEY_FILE`, `MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS=1`
   and `WEB_CONCURRENCY=1`. Prove actual topology; a flag is not topology evidence.
6. Qualify policy changes/revocation, certificate rotation, synchronized clocks,
   fixed identity lease expiry, native session restart/renewal and existing membership.
   Global Keycloak user-session logout without a not-before update is not integrated;
   access can remain valid up to five minutes. Set the operational revocation gate
   accordingly before activation. Do not weaken required actions for acceptance.
7. Disable request bodies, full-memory dumps and credential-bearing debug/proxy logs.
   Keep server keys out of desktop build inputs and follow least-privilege file ACLs.
8. Stage compatible backend and native desktop builds, then obtain the specific live
   activation/cutover authorization. This work leaves `release/auth-authority.json`
   at `PREPARED_NOT_ACTIVE`, `migration:null`; no issuer migration dates are set.

API authority and canonical OLD identity namespace remain independent of the NEW auth
hostname. Existing JWT/PKCE/state/nonce/signing/audience/client/expiry checks and the
dynamic IPv4 callback remain unchanged. Registration/reset capabilities remain off.

Implementation API references: [Keycloak credential authenticator](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/authenticators/browser/AbstractUsernameFormAuthenticator.java),
[required password action](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/requiredactions/UpdatePassword.java),
[raw TLS certificate lookup](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/services/x509/DefaultClientCertificateLookup.java).
