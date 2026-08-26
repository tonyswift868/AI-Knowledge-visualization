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

Current evidence is static/offline only because Docker and Linux staging are
not available in Worker 06. Do not report the stack as running until `docker
compose config`, container health, OTLP ingestion, datasource queries, alert
loading, and dashboard provisioning are observed on staging.

Recovery commands use the dependency-free Python entry point and a plan that
contains only password environment-variable names:

```sh
PYTHONPATH=backend python -m app.observability.recovery_cli backup --plan /absolute/recovery-plan.json
PYTHONPATH=backend python -m app.observability.recovery_cli verify --backup-directory /absolute/backup-id
PYTHONPATH=backend python -m app.observability.recovery_cli restore --plan /absolute/recovery-plan.json
```
