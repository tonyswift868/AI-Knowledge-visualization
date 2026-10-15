# MentorFlow Java server (first migration step)

This Spring Boot 3 / Java 21 service is a local implementation of the authenticated catalog endpoints and the three account-core health endpoints. Its contracts are checked over HTTP against the existing Python server. Python defaults to `PERSONAL_LOCAL`; this first Java version requires `ENTERPRISE_SERVER`. It is not wired into production routing and is not ready for an online cutover.

## Build locally

Install or unpack Temurin JDK 21 and Maven 3.9.16 using the task's portable toolchain. From the repository root, put build output in the task scratch directory and keep Maven's reusable repository under `.local/java/m2`:

```powershell
$javaRoot = 'D:/MentorFlow/.local/test-tmp/java142'
$maven = 'D:/MentorFlow/.local/java/apache-maven-3.9.16/bin/mvn.cmd'
$env:JAVA_HOME = 'D:/MentorFlow/.local/java/jdk-21.0.12.1+1'
$env:Path = "$env:JAVA_HOME/bin;$env:Path"
& $maven -Dmaven.repo.local='D:/MentorFlow/.local/java/m2' `
  -Djava142.build.directory="$javaRoot/build" `
  -f refactor/server-java/pom.xml package
```

The generated jar is `D:/MentorFlow/.local/test-tmp/java142/build/mentorflow-server-0.1.0.jar`. The `Dockerfile` builds from the repository root with a Maven/Temurin 21 builder and runs on Temurin 21 JRE. The container binds to `0.0.0.0`; local runs default to `127.0.0.1`.

## Configuration

The deployment mode and OIDC configuration use the existing Python environment names. Python defaults to `PERSONAL_LOCAL`; this Java service supports only the enterprise server and must be configured explicitly for `ENTERPRISE_SERVER`.

| Variable | Purpose |
| --- | --- |
| `MENTORFLOW_DEPLOYMENT_MODE` | Must be `ENTERPRISE_SERVER`. |
| `MENTORFLOW_KEYCLOAK_ISSUER` | Issuer expected in the JWT. |
| `MENTORFLOW_KEYCLOAK_IDENTITY_ISSUER` | Optional canonical identity issuer. |
| `MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER` | Optional issuer URL used to fetch JWKS. |
| `MENTORFLOW_KEYCLOAK_AUDIENCE` | Required token audience. |
| `MENTORFLOW_KEYCLOAK_CLIENT_ID` | Client ID used for authorized-party checks. |
| `MENTORFLOW_KEYCLOAK_REDIRECT_URI` | Existing OIDC callback setting; required, also selects the default cookie name. |
| `MENTORFLOW_KEYCLOAK_ALLOW_LOOPBACK_HTTP` | Explicit opt-in for local HTTP fixtures; default false. |
| `MENTORFLOW_KEYCLOAK_ALGORITHMS` | Existing RS256-only algorithm setting. |
| `MENTORFLOW_KEYCLOAK_CLOCK_SKEW_SECONDS` | Numeric-date tolerance; default 30. |
| `MENTORFLOW_KEYCLOAK_MAX_TOKEN_LIFETIME_SECONDS` | Maximum accepted token lifetime; default 86400. |
| `MENTORFLOW_KEYCLOAK_HTTP_TIMEOUT_SECONDS` | JWKS request timeout; default 10. |
| `MENTORFLOW_KEYCLOAK_JWKS_CACHE_SECONDS` | JWKS cache period; default 300. |
| `MENTORFLOW_DATABASE_URL` | Existing database connection setting used by the identity directory and readiness check. |
| `MENTORFLOW_IDENTITY_SESSION_COOKIE_NAME` | Used to detect cookie/bearer conflicts; cookie sessions remain Python-owned. |
| `MENTORFLOW_SERVICE_IDENTITY_GRANTS_JSON` | Existing configured service-account grants, with the same permission enum. |
| `MENTORFLOW_KEYCLOAK_DISCOVERY_URL` | URL used by the readiness probe. |
| `MENTORFLOW_CATALOG_DIR` | Catalog directory; defaults to `/run/mentorflow/catalog`. |
| `MENTORFLOW_BIND_ADDRESS` | HTTP bind address; local default `127.0.0.1`, Docker sets `0.0.0.0`. |
| `PORT` | HTTP port; default 8080. |

Catalog files are `source-whitelist.json` and `model-prices.json`. Each must be a JSON object with a `version` key and must fit in 1 MiB. Responses keep the source bytes unchanged.

## Migration boundary

This first step validates RS256 bearer JWTs and resolves organization/workspace membership through the shared identity directory. Opaque desktop-native access tokens, Python browser-cookie sessions, and issuer migration/continuity remain Python-owned. Do not point production traffic at this service or switch an nginx route based only on this local build. A production cutover requires the separately requested shadow comparison and explicit user approval.

## HTTP contracts and measurements

Build first using the command above, then run from the repository root:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = 'D:/MentorFlow/.local/test-tmp/java142'
$env:TMP = $env:TEMP
$env:JAVA142_ARTIFACTS = "$env:TEMP/build"
& 'D:/MentorFlow/.venv/Scripts/python.exe' -B -m pytest tests/refactor/server_java `
  -q -p no:cacheprovider --basetemp "$env:TEMP/pytest-contract"
```

The same assertions receive a Python or Java base URL. Both real local servers use a local RSA/JWKS issuer and isolated external membership/health fixtures; no real accounts or services are contacted. Test controllers and adapters compile into `test-classes` and are excluded from the production jar. Missing prepared Java artifacts are reported as skips, not as Java passes.

Set `JAVA142_BENCHMARK=1` for a separate measurement run. It makes 20 warm-up requests and 1000 sequential requests per backend and writes `build/resource142.json`; default test runs skip this optional measurement. RSS sums the server process and descendants, including the Windows virtualenv redirector and its actual Python worker. This is a local Windows measurement with fake external membership state, not a production PostgreSQL/Linux capacity test.

Response comparisons preserve status, application headers, content length and UTF-8 body bytes. Readiness excludes `dependencies[*].duration_ms` and the resulting content length only; HTTP server/date/connection framing is transport-specific. Fixed request IDs and token times keep the remaining values deterministic.
