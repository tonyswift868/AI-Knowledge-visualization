# MentorFlow Desktop login theme provenance

Parent: `keycloak`, from the repository's pinned Keycloak **26.7.3** runtime.
This is a login-only child theme; no admin/account/email theme overrides.

`login/login.ftl` is derived from the Keycloak Authors' Apache-2.0 source:
[26.7.3 login.ftl](https://github.com/keycloak/keycloak/blob/26.7.3/themes/src/main/resources/theme/base/login/login.ftl).
The license is included in `LICENSE.keycloak.txt`. The only functional template
change is deletion of the `realm.rememberMe` block. A leading comment records
the modification. No hidden checked Remember Me field is submitted instead.

Original UTF-8/LF SHA-256:
`8b179d40968e07776b9c9c4b46103f8e3489eaaa249d18be202ddcd3da565e34`.
This matches both the tagged source and the released themes JAR. The offline
test restores the deleted block and requires this exact upstream identity.
All other templates, required actions, authenticator selection, password
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
