# Real email lifecycle — inbox credential gate

Dispatch: `MF-MAIN3-01-REAL-EMAIL-LIFECYCLE-20260915-27`

Base: `69b7e210ce7ff4abeb14337e4d969b0420db476a`.
Fetched all remotes with pruning; rebased only worker/main3/01 onto this exact
review authority. No control/integration/main branch was changed.

STATUS = BLOCKED_ONLY_BY_QQ_CREDENTIAL_REFERENCE

This is a preparation result, not real email lifecycle acceptance.
No registration/forgot request or real message was sent in this dispatch.

## Credential discovery

- `E:/Codex/Secrets/MentorFlow`: enumerated filenames. Only SMTP configuration,
  SSH and TLS material were found. Private SSH/TLS values were not read.
- `smtp.env`: inspected key names only. It contains SMTP configuration and
  SMTP_SECRET; no QQ inbox/IMAP/POP credential reference.
- Windows Credential Manager: inspected target metadata only. The approved
  address appears in MicrosoftAccount SSO targets, which are not QQ IMAP/POP
  authorization credentials. Those credential values were not read or tried.
- No mailbox/IMAP/POP/QQ environment reference was present.
- Current repository scripts/tests/deploy/tools, retained Account MVP scripts
  and fresh UAT scripts contain no QQ IMAP/POP reader or approved inbox resolver.
- Dispatch 26's authority receipt independently records the same missing
  mailbox resolver. Its SMTP readiness is not proof of inbox access.

MAILBOX_CREDENTIAL_REFERENCE = NOT_FOUND

The remaining external input is an approved secure reference resolving a QQ
mailbox-specific IMAP credential, scoped to the already approved TEST address.
Do not provide the value in Git, logs, receipts or chat. No Microsoft SSO,
product login password, Brevo secret or unrelated Provider credential may be
substituted. No resolver was falsely marked configured or operational.

## Fresh UAT preflight

Reviewed `MF-A-UAT-26-98d64fb0/lease.json` and dispatch 26's immutable receipt.
The lease expires at `2026-09-15T17:39:15.334761+00:00`; at the approximately
15:20 UTC check its remaining duration was over two hours.

However, a verified-TLS HTTP probe of its Backend at
`https://127.0.0.1:63570/health/live` failed before TLS with WinError 10061
(connection refused). No listeners were observed at Backend 63570, identity
63569 or PostgreSQL 53321. The dated READY receipt is not current health proof.
No stale runtime was used for acceptance and no service was restarted.

Before any later real-send run, provision a fresh owned isolated TEST runtime
using the retained `provision.py` as a reviewed template, exact current source,
new ports/realm, restricted secret-directory ACL and bounded lease. Do not run
the template unchanged: it targets A's checkout and includes a VPS SMTP tunnel.
This dispatch did not deploy to or mutate the VPS. Runtime provisioning is
local preparatory work, not another missing external credential authority.

## Prepared execution contract once the inbox reference exists

1. Resolve the approved inbox credential in memory. Connect with verified TLS;
   select INBOX read-only (`EXAMINE`), record UIDVALIDITY and UIDNEXT before
   sending. Never STORE, DELETE, EXPUNGE or mark messages seen.
2. Use a unique per-run correlation ID in the disposable realm's email subject
   presentation (TEST-only configuration), a synthetic username, and the exact
   approved recipient. Confirm correlation is present before enabling SMTP.
   Server-side search must restrict to that subject, approved recipient/sender,
   and new UIDs. Fetch only exact matches with BODY.PEEK, never private history.
   Ambiguous matches fail closed. Keep MIME, cookies and action links in memory.
3. Reject links outside the exact TEST issuer and official action-token path.
   Do not copy links into receipts or use server logs as a link source. Follow
   links only after inbox correlation; no handcrafted action tokens.
4. Register through the existing lifecycle API. Check the account is unverified
   and native login is denied. Receive the real message, consume its official
   link, then prove identity-authority emailVerified and normal native login.
   HTTP 200/HTML/redirect text alone is not proof of mutation.
5. Reuse the verified disposable account for forgot-password. Compare the public
   response with an unknown account, ignoring independently opaque transaction
   IDs. Receive its correlated reset email. Consume using two matching password
   inputs, then require canonical completion status, old-password rejection,
   new-password success and unchanged issuer/subject/account/workspace.
6. Replay each consumed link in a separate cookie context. Require rejection or
   the explicitly established verification idempotence contract. Reset replay
   must not change credentials or create a second completion transition.
7. Test expiry with officially issued links and actual elapsed lifespan. The
   current admin helper explicitly supplies 600 seconds; changing only a realm
   default will not shorten those links. Never edit or mint tokens to simulate
   expiry. Use a reviewed TEST-only issuance lifespan or wait out the real one.
8. Minimum planned delivery budget: four messages (valid and expired verification,
   valid and expired reset). Where needed, use an expired first verification
   followed by the official resend for the same synthetic account, avoiding
   duplicate-mailbox accounts. Replay and nonexistent-account checks send none.
   No automatic retries after ambiguous submissions. Capture safe counts and
   state transitions only; revoke only the verification sessions created here.

All live stages remain NOT_RUN. Source/fixture tests below are preparation only.
No Frozen UI, migration, packaged-app change, real-user account mutation or
GitHub Actions execution is included.

## Executed offline preparation

Python 3.13.14:
`pytest backend/tests/test_account_lifecycle_admin.py backend/tests/test_account_mvp_wiring.py backend/tests/test_reset_completion.py -q --disable-warnings -ra`

Result: **59 passed, 0 failed, 0 skipped**, one warning. Coverage includes the
official admin HTTP operation shapes, malformed registration inputs, provider
error redaction, uniform forgot response, bounded completion states, old/foreign
observation rejection and client-declared-success rejection. These use synthetic
HTTP authority and do not prove QQ receipt or actual Keycloak token consumption.

REAL_SMTP_SEND_COUNT = 0
TONYSWIFT_ACCOUNT_MUTATION = 0
REAL_USER_DATA_MUTATED = NO
SECRET_EXPOSURE_COUNT = 0
PRODUCT_CODE_CHANGED = NO
