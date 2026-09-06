# E107 OIDC readiness within owned launch — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 88cf9f9991bf3895b6c057f8200bf0170d095e50.

A preliminary discovery GET before launching timed out (curl 28); local TCP
inspection then found no listener on 18080. This is not proof of server outage:
the existing launcher owns an ephemeral preview tunnel and closes it on exit.
Added --probe-oidc to observe the pinned discovery endpoint DURING the owned
launch, after a passive dwell. It uses system curl with explicit existing CA,
no proxy, bounded connect/request timeout, no insecure flag, and requires exact
issuer equality. Only exit status/boolean are recorded, not response contents.
Failure cannot qualify the observation. It never submits credentials or alters
membership/deployment. HTTP requests are not claimed zero.

Actual observation using native helper
156fad0c0bd7ac008269b716fa4bdfab1a9a05682b1c8dbb6dfdb1ad36bfdc4c
and the existing isolated old-product login installation:
- Original copy hashes PASS.
- Full activation 3.663362s, hash 1.364268s.
- LoginGate visible 4.200846s; QtCore loaded from the isolated bundle.
- Eight-second passive dwell, discovery curl exit 0 and exact issuer match true.
- No pre-auth MainWindow observed in sampling.
- Close 0.605524s, exit 0, no owned children remaining.
- Evidence: E:/Codex/Temp/e107-installed-login-8ea6931bafba/observation-88460.json.

Read only remember_me/auto_login booleans: both false. No profile identifiers,
tokens or password values read or modified. The source OIDC module explicitly
does not read DOM or form values; no diagnostic DOM injection was introduced.
Discovery readiness is NOT proof of rendered or interactive authorization form.
It narrows the missing evidence to actual page/form readiness, rather than a
demonstrated unavailable transport during this launch. No <=8s form claim.

Three focused diagnostic tests PASS in 2.355s, including five mocked discovery
outcomes (wrong issuer, HTTP failure, malformed/incorrect JSON all reject),
argument guards and existing synthetic UI Automation fixture. The latter remains
unqualified for QtWebEngine. No production runtime changes, final build, dependency
install, push, server mutation or formal data write. Historical gate timings,
security tests and four WinError 1314 skips keep their recorded scope/status.
