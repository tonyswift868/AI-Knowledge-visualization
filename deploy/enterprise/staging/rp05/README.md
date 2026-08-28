# RP-05 current-base minimal staging package

This package is a deployment asset, not evidence that host `89.45.45.28` was
changed or that a real TLS/staging run passed. It deliberately excludes
SeaweedFS, Docling, Prometheus, Grafana, Tempo, Loki, and k6 from the default
Compose graph.

## Host contract

The release builder targets `/opt/mentorflow` and preserves these operator-owned
areas: `releases`, `current`, `config`, `secrets`, `data`, `backups`, `evidence`,
and `logs`. Secrets and certificates are never generated into or copied from the
Git tree. Required secret filenames are listed by `rp05_staging.py preflight`.

The durable worker factory is a required current-product integration input. The
current repository has the production `app.enterprise.worker_entry` but no
complete default business-handler factory, so the package fails closed until
`MENTORFLOW_WORKER_RUNTIME_FACTORY=module:callable` is supplied.

## Reproducible sequence

1. Copy `runtime-images.template.json` and replace every image with an exact
   `name@sha256:<64 lowercase hex>` reference.
2. Copy `compose.env.example` and set the public HTTPS origin, hostname, worker
   factory, and project name. Do not put secrets in this file.
3. Create the nine external secret files under `/opt/mentorflow/secrets`.
4. Build a release with `build_rp05_staging_release.py`; transfer the resulting
   directory without changing its manifest-covered files.
5. On the Linux host, run `python3 rp05_staging.py preflight`, `config`, `pull`,
   `migrate`, `up`, `readiness`, and `evidence` in that order.
6. Use `activate` only after readiness succeeds. `rollback` changes only the
   atomic `current` symlink to a previously validated release.

`/health/startup` is used for backend process startup. Current canonical
`/health/ready` requires an object store in a staging environment, while RP-05
explicitly excludes that service; therefore that full-product readiness gate
remains `PENDING` until the separate object-storage delivery is integrated.
