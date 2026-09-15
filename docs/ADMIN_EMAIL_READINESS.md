# Admin Overview Email readiness

Email uses the existing Backend `HealthRegistry`; Desktop and Admin Overview do
not resolve credentials or independently determine relay health.

Set the **Backend-only** `MENTORFLOW_SMTP_CONFIG` to the absolute path of the
same provider-neutral SMTP configuration used to configure the mail transport
(`deploy/enterprise/auth-email-security/smtp.example.json`). The operator must
bind it to the current transport, not an unrelated test relay. This reference
does not apply Keycloak settings or enable account capabilities.

The existing SMTP field contract is reused: REAL_RELAY, host, port, user, from,
exactly one of STARTTLS/SSL, and the environment-secret or private file reference.
On POSIX secret files require private permissions; Windows deployments must use
the existing service-owned secret-store ACL. Do not copy credentials into this
document, API payloads, Desktop configuration, or logs.

`email` is an optional dependency for aggregate Backend readiness so a mail outage
does not prevent existing-account login. Its own status is still unavailable on
failure. Every observation validates configuration and resolves secret material,
then uses Python's standard SMTP transport for verified TLS, AUTH and NOOP.
Socket operations have a one-second timeout. No MAIL/RCPT/DATA or message send
occurs; this is transport readiness, not proof of delivery or inbox receipt.

Codes are fixed and contain no provider response text:

- `EMAIL_TLS_AUTH_READY`: current authenticated TLS transport responded to NOOP.
- `EMAIL_AUTHORITY_NOT_CONFIGURED`: no canonical config reference is bound.
- `EMAIL_CONFIGURATION_UNAVAILABLE`: invalid configuration or unresolved secret.
- `EMAIL_TRANSPORT_UNAVAILABLE`: transport/TLS/authentication/protocol failure.
- `EMAIL_AUTHORITY_UNAVAILABLE`: Overview has no registry observation, including
  a registry failure or its existing bounded collection timeout.

Historical delivery receipts never establish current health. The optional status
does not override email acceptance gates. Missing authority is unavailable, never
healthy or permanently unknown. Tests in `backend/tests/test_email_health.py` use
protocol doubles and do not send or claim real email acceptance.
