# MentorFlow E1-06 external observability stack

This directory is an external staging-container target. The MentorFlow
application does not start, supervise, or embed these containers.

Before startup, create a root-readable Grafana admin password file outside the
repository and set `MENTORFLOW_GRAFANA_ADMIN_PASSWORD_FILE` to its absolute
path. Then, on a Linux staging host with Docker Compose:

```sh
docker compose -f ops/observability/docker-compose.staging.yml config
docker compose -f ops/observability/docker-compose.staging.yml up -d
```

Configure the backend with `MENTORFLOW_OTEL_ENABLED=true` and
`OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4317` only after the Python OTel
dependency lease is granted. Prometheus expects the backend at
`host.docker.internal:8000`; replace that target with the staging service DNS
name when the backend is containerized.

The Collector routes traces to Tempo, logs to Loki, and metrics to its
Prometheus exporter. Grafana provisions Prometheus, Tempo, Loki, the seven
required dashboard sections, and immutable dashboard files. Prometheus loads
the E1 alert catalogue from `alerts.yml`.

The E1-06B verifier turns those checks into machine evidence. On the Linux
staging host, configure endpoint URLs and token environment variables, then
run one command at a time:

```sh
python scripts/mentorflow-enterprise-verify preflight
python scripts/mentorflow-enterprise-verify health
python scripts/mentorflow-enterprise-verify otel
python scripts/mentorflow-enterprise-verify backup --plan /absolute/backup-plan.json
python scripts/mentorflow-enterprise-verify restore-test --plan /absolute/restore-drill.json --disposable-only
MENTORFLOW_STAGING_INCIDENT_DRILLS=true \
  python scripts/mentorflow-enterprise-verify incident \
  --plan /absolute/incident-plan.json --allow-disruption
```

Each invocation writes an immutable
`mentorflow.enterprise-staging-evidence.v1` file and prints a compact JSON
receipt. Missing Linux tools, dependencies, endpoints, tokens, plans, or live
Grafana provisioning are `BLOCKED` or `FAIL`; they are never silently passed.
Use `all` only with all three plan paths and both destructive-operation flags.

Do not report the stack as running until the server-generated evidence shows
container health, OTLP ingestion, Tempo trace retrieval, Prometheus queries,
alert simulation, dashboard provisioning, backup hashes, disposable restore
comparison, and incident recovery. The Collector remains an external
container. The verifier does not add or lease any Python or JavaScript OTel
dependency.

Recovery commands use the dependency-free Python entry point and a plan that
contains only password environment-variable names:

```sh
PYTHONPATH=backend python -m app.observability.recovery_cli backup --plan /absolute/recovery-plan.json
PYTHONPATH=backend python -m app.observability.recovery_cli verify --backup-directory /absolute/backup-id
PYTHONPATH=backend python -m app.observability.recovery_cli restore --plan /absolute/recovery-plan.json
```

The alert harness runs `promtool check rules` plus `promtool test rules` against
`alert-tests.yml`. The five simulations cover backend down, HTTP error spike,
stuck queue, stale backup, and fake Provider outage. Grafana API verification
requires `MENTORFLOW_GRAFANA_TOKEN`; metrics and synthetic probes use separate
server-owned bearer tokens. Values are never copied into evidence.
