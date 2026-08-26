# Tenant isolation load report

Status: **OFFLINE_VERIFIED_CORPUS / STAGING_EXECUTION_PENDING**. `REAL_TENANT_LOAD_GATE = PENDING_STAGING` and `REAL_POSTGRES_RLS = NOT_RUN`.

The deterministic fixture contains exactly three synthetic organizations, 21 users, two workspaces per organization, and three foreign IDs for each resource family: Document, Knowledge Map, Plan, Progress, Review, Cost, and Audit. Credentials are not included; CI supplies them through `LOAD_USERS_JSON`.

Each VU performs valid own-tenant traffic, verifies its session/workspace scope, then rotates through foreign resource probes from another organization. Foreign requests accept only 401/403/404. Any 2xx increments the machine metric `unauthorized_success_count`; the enterprise threshold is exactly zero.

Static validation proves the corpus shape, resource coverage, randomized foreign-probe seam, and threshold configuration. It does not prove server authorization, RLS, cache isolation, or tenant-safe background jobs. Those require E1-02/E1-04/E1-05 owner evidence, the database lease, an integrated server, and retained real k6 output.
