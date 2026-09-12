# MentorFlow Desktop login theme provenance

Parent: `keycloak`, from the repository's pinned Keycloak **26.7.3** runtime.
This is a login-only child theme; no admin/account/email theme overrides.

`login/login.ftl` is derived from the Keycloak Authors' Apache-2.0 source:
[26.7.3 login.ftl](https://github.com/keycloak/keycloak/blob/26.7.3/themes/src/main/resources/theme/base/login/login.ftl).
The license is included in `LICENSE.keycloak.txt`. The only change to that template
is deletion of the `realm.rememberMe` block. A leading comment records
the modification. No hidden checked Remember Me field is submitted instead.

Original UTF-8/LF SHA-256:
`8b179d40968e07776b9c9c4b46103f8e3489eaaa249d18be202ddcd3da565e34`.
This matches both the tagged source and the released themes JAR. The offline
test restores the deleted block and requires this exact upstream identity.
`login/login-update-password.ftl` additionally adapts the pinned upstream
[UPDATE_PASSWORD template](https://github.com/keycloak/keycloak/blob/26.7.3/themes/src/main/resources/theme/base/login/login-update-password.ftl).
It keeps the native POST target, field names, logout-sessions macro and
app-initiated cancellation. Confirmation has no reveal control; the small
`password-pair.js` module checks equality and toggles only the first field.
The field markup now lives in `mentorflow-password-pair.ftl`, shared by that
native page and the inactive future lifecycle shell. `bindPasswordPair` owns
the same equality/reveal behavior for each independent form and can be disposed.
It does not implement password policy, token validation or credential updates.
Native policy errors use localized, separate messages. The provider still
validates the complete realm policy on submission; matching alone does not
mean a password has passed that policy. No password values are saved or bridged
to Desktop. Ordinary paste and password manager filling remain available.

All remaining templates, required actions, authenticator selection, login
visibility JS, passkey handling, sanitized messages and session checks remain
inherited. Never copy an entire old theme forward when upgrading Keycloak:
re-audit the template delta and re-run real rendering/challenge checks.

Official references:

- [Theme inheritance and directory deployment](https://www.keycloak.org/ui-customization/themes).
- [Client-specific login_theme selection](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/theme/DefaultThemeSelectorProvider.java).

Static browser checks used the official Maven artifacts (not runtime installs):

| Artifact | SHA-256 |
| --- | --- |
| keycloak-themes-26.7.3.jar | 32189fbfb6da000a9be7836169790ddf7400ebe68c1b06b8ac31f220fed9f829 |
| keycloak-themes-vendor-26.7.3.jar | 12c8ad49264ec998b2339500b364f80715fec0218fea1a1899fbb6ce218f2351 |

These JARs are scratch test inputs, not product dependencies or committed assets.
No current VPS image was inspected or changed in this task; the release owner
must verify the running image version before activation. Source-version
compatibility does not establish live deployment compatibility.

Reset success boundary: the native `force_login=true` flow in 26.7.3 ends its
authentication session and emits **accountUpdatedMessage**, shared with other
required actions. It does not emit **accountPasswordUpdatedMessage**. This theme
provides the requested password-specific wording for the latter key, but does
not relabel generic account/email changes as password resets. Exact reset-only
success wording and a verified return to the normal Desktop login gate still
need a canonical reset completion signal from the auth owner before activation.
Desktop now also rejects a login result from the forgot-password intent: it
consumes the existing OIDC callback validation once, performs no token exchange,
and returns to the ordinary login gate. This protects against an unexpected
provider login callback; it does not identify a successful credential reset.
No account capability or deployment configuration is enabled by these UI files.

Future lifecycle presentation: `mentorflow-account-lifecycle.ftl` is a custom,
unrouted template, not a `register.ftl` or `login-reset-password.ftl` override.
Its JS state machine and forms implement the next Desktop UX contract only.
The register UI presents 8–128 Unicode code points without composition rules,
as requested in dispatch 05; the current native realm's 15-character policy is
unchanged. No native login page imports the lifecycle module, and the forms
use `method="dialog"` without POST destinations. Lifecycle intents carry only
an intent name, request correlation id or nonsecret account key. The shell has
no network or credential/Vault adapter, even when test capabilities are enabled.
Future remember-password/auto-login selections live only in that UI instance.
See `docs/qa/MAIN3_03_ACCOUNT_LIFECYCLE_UX_SHELL_20260913.md` for the presentation
contract and pending owner 01/04 integration boundaries.

References for that boundary:

- [Pinned native reset flow](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/authenticators/resetcred/ResetCredentialEmail.java).
- [Pinned required-action completion](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/services/managers/AuthenticationManager.java).
- [OWASP forgot-password guidance](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html): confirm the password and return through normal authentication rather than creating an automatic login session.
