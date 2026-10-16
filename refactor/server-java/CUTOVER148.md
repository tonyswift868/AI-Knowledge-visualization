# Whole-group cutover checklist (local validation only)

This document does not authorize a production change. The architect must obtain
the user's approval, resolve the actual production paths/container names, and
save the current routing/configuration before executing any server command.

## Before switching

- Deploy the tested Java artifact beside Python without routing public requests
  to it. Keep the current Python artifact/container and routing file for rollback.
- Both implementations must use the same PostgreSQL database/schema, canonical
  identity issuer and issuer-continuity configuration. Alembic remains the schema
  owner; Java performs no migrations. Verify the native-session and account-closure
  migrations and the application/closure roles and grants already exist.
- Copy the existing native identity mTLS endpoint/CA/certificate/key settings and
  lifecycle Admin REST configuration through the established secret mounts. Use
  permanent family TTL `MENTORFLOW_IDENTITY_SESSION_TTL_SECONDS=0`, explicitly
  approved single process `MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS=1` and
  `WEB_CONCURRENCY=1`. Do not print secret files or bearer/refresh credentials.
- Verify catalog mounts, deployment mode, Keycloak issuer/audience/client settings,
  superadmin subjects and the existing trusted-proxy/network override. Preserve
  the existing OIDC loopback proxy where the transport issuer requires it.
- Check `/health/live`, `/health/startup`, `/health/ready`; test native refresh,
  authenticated directory/workspace access and sync using an approved test account.
  Confirm lifecycle configuration is active, including the production closure hook.
- Switch all protected/account/sync routes as a group. Do not randomly balance
  them between Python and Java: access-token hashes are process-local. Refresh
  families/rotations, sync rows and closure state are database-backed.
- Capture the currently running client's response to an old access token rejected
  after cutover before scheduling user-visible switching. Local HTTP tests prove
  refresh continuity, not the desktop's automatic recovery UX. Browser OIDC
  cookie sessions cannot be transferred from Python's private process memory.

## Observe / roll back

Monitor 401/403/429/5xx by route, native refresh/replay errors, sync conflict/error
rates, closure PREPARED/IN_PROGRESS/RETRYABLE_FAILURE states, database connections,
health, and latency. Keep credential values out of access/application logs.
Do not dual-write a request to both versions, and do not replay closure/password
mutations whose outcome is unknown.

The architect first sets the following variables to the **verified existing**
paths/names and saves the backup before switching. This is a command template,
not a script that has been executed or a guess at the production nginx layout.

```sh
: "${ACTIVE_ROUTE_FILE:?verified nginx route file}"
: "${PYTHON_ROUTE_BACKUP:?pre-cutover copy of that file}"
: "${NGINX_CONTAINER:?existing nginx container name}"
: "${PYTHON_COMPOSE:?existing Python compose file}"
: "${SESSION_OVERRIDE:?existing permanent-session override}"
: "${NETWORK_OVERRIDE:?existing BUG-45 network override}"
cp -- "$PYTHON_ROUTE_BACKUP" "$ACTIVE_ROUTE_FILE"
docker exec "$NGINX_CONTAINER" nginx -t
docker exec "$NGINX_CONTAINER" nginx -s reload
docker compose -f "$PYTHON_COMPOSE" -f "$SESSION_OVERRIDE" -f "$NETWORK_OVERRIDE" ps
```

Restore routes to the still-running Python service before stopping any Java
container. Verify health and refresh/sync again; Java-issued refresh tokens can
rotate on Python through the same database, while Java-issued access tokens need
refresh. Rollback must retain the current database state: do not restore an older
database snapshot over refresh rotations, sync writes or completed closures.
