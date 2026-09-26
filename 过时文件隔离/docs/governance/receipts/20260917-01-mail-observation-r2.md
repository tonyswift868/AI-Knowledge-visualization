# MF-R2-01-MAIL-OBSERVATION-FIX

Base: `c08a44c6d4954eb618cf1e395e5603a54074e257`.
Owner branch: `worker/main3/01-mail-observation-r2`; preserve account-security delivery `664993e4351085d4eed6ee9915943fe0a948b264`. Exact result commit/tree are in the final receipt.

## Correction and observation contract

The prior health probe tested SMTP from Backend's internal-only network. Actual mail is sent by Keycloak. This change observes the sender without granting Backend SMTP egress or copying SMTP credentials into Backend.

Keycloak 26.7.3 `EmailSenderProvider.validate()` was inspected locally: it validates address configuration only, not a connection. The new fixed native bridge operation is:

```json
{"operation":"email-transport-health"}
```

It uses the existing `/realms/<configured realm>/mentorflow-native-identity/v1` endpoint and **unchanged** raw backend-certificate fingerprint/realm boundary. Additional request keys (host, recipient, subject, credentials, etc.) are rejected. No new public API.

The active realm's `getSmtpConfig()` is the sole SMTP configuration source. The active default Keycloak sender validates its address configuration; its existing BASIC `EmailAuthenticator` resolves credentials, including existing vault references. The bundled Jakarta Mail/Angus transport performs connect/EHLO, required STARTTLS or configured implicit TLS, AUTH, NOOP, close/QUIT. Successful encrypted transport plus AUTH 235 and NOOP 250 are required. No message, MAIL FROM, RCPT TO, DATA or send call is constructed. TLS hostname/certificate validation stays enabled with the existing Keycloak truststore; trust-all policy is rejected. Probe socket connect/read/write timeout is 1 second, and EHLO uses a fixed address literal to avoid local hostname reverse-DNS delays. No protocol debugging or provider exceptions are logged/returned.

Only these private two-field outcomes are accepted by Backend:

| status | code | Meaning |
| --- | --- | --- |
| `READY` | `EMAIL_TLS_AUTH_READY` | Current sender-side TLS/AUTH/NOOP succeeded; **not submission or delivery**. |
| `UNAVAILABLE` | `EMAIL_AUTHORITY_NOT_CONFIGURED` | Realm has no SMTP configuration. |
| `UNAVAILABLE` | `EMAIL_CONFIGURATION_UNAVAILABLE` | Invalid/insecure/unsupported configuration or unsupported sender/authenticator. |
| `UNAVAILABLE` | `EMAIL_TRANSPORT_UNAVAILABLE` | Connection/TLS/AUTH/NOOP failure. |

This bounded probe supports the current default Keycloak sender and BASIC authentication with exactly one TLS mode. Another provider, token authentication or unauthenticated/plaintext relay is reported unavailable rather than asserted healthy.

Backend `MtlsIdentityVerifier.email_transport_health()` uses the **existing** fixed endpoint and mTLS context. It enforces a 5-second HTTP timeout, a 512-byte response limit, strict fields/allowed outcome combinations and duplicate-key rejection; network/status/schema failures fail closed. A per-verifier lock coalesces concurrent requests, and both success and failure are cached for 60 seconds. `HealthRegistry.email` remains optional for login and retains the existing health/UI status vocabulary. Both full Backend and Account Core startup bind this probe via existing overview setup. Missing native authority never falls back to a Backend SMTP socket. Other probes and account/password operations are unchanged.

## A application instructions (not executed by 01)

1. Build the SPI using updated `scripts/build_native_identity_verifier.py` (now includes `MailTransportHealth.java`). Apply this Backend source and SPI together; restart the relevant Backend and Keycloak services. No realm update, schema migration, new permission or new secret is needed.
2. Remove the **Backend-only** `MENTORFLOW_SMTP_CONFIG=/run/mentorflow/smtp/config.json` injection and the R2 observation mount `/opt/mentorflow/artifacts/r2-a-e79e9271/smtp` → `/run/mentorflow/smtp` (read-only). Remove any env injection used solely by that projection's `SMTP_SECRET_ENV`, if present. This corrects the earlier 04 recommendation to mount SMTP credentials into Backend for readiness.
3. Do not delete the host projection or source secrets as part of this change. Preserve Keycloak's existing realm SMTP settings/credential authority, `E:/Codex/Secrets/MentorFlow/smtp.env`, existing lifecycle/admin resolver mounts and all native mTLS endpoint/CA/client cert/key/pin configuration. No Backend network extension, sidecar or proxy.
4. Observe the `email` dependency through existing `/health/ready` and administrator overview after restart; allow up to 60 seconds for a subsequent cached sample to expire. A stale/unsupported SPI returns unavailable, never a fabricated healthy result. A performs sender-side live no-send qualification; 01 has not deployed or probed the real relay.

## Verification scope

- Focused Backend observation, email registry and administrator overview tests: **35 passed**, one existing Starlette/httpx deprecation warning.
- Real local fixture uses disposable Keycloak 26.7.3, existing mTLS fixture machinery, and a loopback STARTTLS/AUTH relay which records only command names and rejects sending. No GUI, actual login, real SMTP, account/data mutation, deployment or cleanup script.
- Final `scripts/mail_observation_fixture.py` run: **22 passed** under `E:/Codex/Temp/mf-r2-mail-fixture-06`. Covers actual realm-source connection, TLS/AUTH/NOOP, safe response shape, destination override rejection, wrong realm, missing/wrong client certificate, no SMTP activity for denied callers, real Backend bridge consumption/cache, AUTH/NOOP failures, absent AUTH/STARTTLS, disconnect, socket timeout, negative refresh, insecure configuration rejection, TLS hostname mismatch after STARTTLS but before AUTH, recovery and zero send commands.
- Fixture debugging found EHLO local-host resolution delaying the connection before protocol progress; the fixed EHLO literal avoids that unnecessary DNS dependency. Temporary non-sensitive exception-class diagnostics were removed before final build/verification. TLS validation was not loosened.
- Exact Keycloak 26.7.3 compilation and `git diff --check` passed. Historical 71/19/18 account suites were not rerun. Real deployment/sender-side qualification remains A's next step; no live PASS is claimed here.
