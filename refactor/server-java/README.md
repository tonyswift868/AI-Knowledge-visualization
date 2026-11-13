# MentorFlow Java server

This Spring Boot 3 / Java 21 service is the production enterprise backend: account authentication and lifecycle, structured synchronization, catalog, health, administration, and RAG text retrieval. The Python backend remains available for fallback and HTTP contract comparison. Java requires `ENTERPRISE_SERVER`; Python defaults to `PERSONAL_LOCAL`. Build, health checks, routing and rollback use the existing scripts in [deploy/README.md](deploy/README.md). Production operations require separate authorization; a local test does not verify the live deployment.

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
| `MENTORFLOW_IDENTITY_SESSION_COOKIE_NAME` | Cookie/bearer conflict detection and expired-cookie clearing; valid browser-cookie sessions remain Python-owned. |
| `MENTORFLOW_KEYCLOAK_ISSUER_MIGRATION_JSON` | Existing bounded issuer migration window and authority digest. |
| `MENTORFLOW_IDENTITY_CONTINUITY_FILE`, `MENTORFLOW_IDENTITY_CONTINUITY_SHA256`, `MENTORFLOW_IDENTITY_CONTINUITY_SIGNATURE_HEX`, `MENTORFLOW_IDENTITY_CONTINUITY_PUBLIC_KEY_HEX` | Existing signed per-subject issuer continuity configuration. |
| `MENTORFLOW_NATIVE_IDENTITY_ENDPOINT` | Enables native auth through the existing private Keycloak verifier endpoint. |
| `MENTORFLOW_NATIVE_IDENTITY_CA_FILE`, `MENTORFLOW_NATIVE_IDENTITY_CERT_FILE`, `MENTORFLOW_NATIVE_IDENTITY_KEY_FILE` | Existing native-verifier TLS material. |
| `MENTORFLOW_NATIVE_EXTERNAL_ISSUER` | Optional verifier issuer; checked and canonicalized by the identity configuration. |
| `MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS`, `WEB_CONCURRENCY` | Native auth requires both to be `1`. |
| `MENTORFLOW_IDENTITY_SESSION_TTL_SECONDS` | Native family lifetime; default `0` means no time expiration. Access tokens remain short-lived. |
| `MENTORFLOW_ACCOUNT_LIFECYCLE_ENABLED`, `MENTORFLOW_ACCOUNT_LIFECYCLE_CONFIG` | Existing opt-in lifecycle admin configuration, required for checking current account/password binding on native login and refresh. |
| `MENTORFLOW_ADMIN_ACCOUNT_PAGE_ENABLED` | Enables the read-only `/admin/accounts` HTML page on a second connector; default false. |
| `MENTORFLOW_ADMIN_ACCOUNT_PAGE_PORT` | Internal page connector port; default 8081. Deployment maps it to host loopback `127.0.0.1:18081`; it is not exposed through the main 8080 connector. |
| `MENTORFLOW_ADMIN_OVERVIEW_CONFIG` | Optional dedicated Keycloak admin reader configuration; takes precedence when set. |
| `MENTORFLOW_SERVICE_IDENTITY_GRANTS_JSON` | Existing configured service-account grants, with the same permission enum. |
| `MENTORFLOW_KEYCLOAK_DISCOVERY_URL` | URL used by the readiness probe. |
| `MENTORFLOW_CATALOG_DIR` | Catalog directory; defaults to `/run/mentorflow/catalog`. |
| `MENTORFLOW_BIND_ADDRESS` | HTTP bind address; local default `127.0.0.1`, Docker sets `0.0.0.0`. |
| `PORT` | HTTP port; default 8080. |

Catalog files are `source-whitelist.json` and `model-prices.json`. Each must be a JSON object with a `version` key and must fit in 1 MiB. Responses keep the source bytes unchanged.

When the account page is enabled and the dedicated admin reader path is unset, the read-only overview and page may reuse `MENTORFLOW_ACCOUNT_LIFECYCLE_CONFIG` only while `MENTORFLOW_ACCOUNT_LIFECYCLE_ENABLED=1`. It reads the existing `secret_file` in place; it does not create or copy credentials. The lifecycle configuration must retain its approved HTTPS origin and paired mTLS files, and its candidate binding and expiry follow the same checks as `NativeConfiguration144`. Unknown configuration fields remain rejected. The dedicated reader keeps its existing issuer transport binding.

## Migration boundary

The service validates RS256 JWTs, signed issuer continuity, and its own native access tokens, resolving membership through the shared identity directory. Native login/refresh/logout/session use the existing Alembic-owned `native_session_families` table; Java never creates or migrates production tables. Refresh tokens survive service restart and can rotate in either implementation over the same database.

Access-token hashes and valid browser-cookie sessions are process-local in Python. Existing database rows cannot reconstruct them. Therefore a native access token minted by one implementation is not recognized by the other, and Java does not accept a valid Python browser-cookie session. Native desktop clients can refresh against the shared durable family after a restart or cutover; a Python browser-cookie session requires login again. Do not split protected traffic across the two implementations. The production routing and Python fallback procedure, including health and session checks, is documented in [deploy/README.md](deploy/README.md).

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

The `144` HTTP tests additionally start real embedded PostgreSQL 17.6 (test-only Zonky dependency), apply existing Alembic migrations with the existing database roles/schema prerequisites, and use a local TLS Keycloak substitute. Run `dependency:build-classpath -DincludeScope=test -Dmdep.outputFile=<scratch>/test-classpath.txt` after building; their scratch root is `D:/MentorFlow/.local/test-tmp/auth144`. No H2, production credentials, remote identity service or user data are used. Native contract comparisons normalize generated secrets and clock values, and separately compare durable family state and concurrent operations.
