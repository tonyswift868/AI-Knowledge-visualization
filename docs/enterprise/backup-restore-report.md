# Backup and restore report

Status: **STAGING_EXECUTION_PENDING / PENDING_OWNER_EVIDENCE**.

Repository migration/archive tests cover portable export/import integrity, tamper rejection, archive limits, symlink/junction/hardlink defenses, schema validation, and atomic publication. These are component tests, not a staging backup/restore drill.

Required staging evidence: exact database/storage versions; encrypted backup identity and checksum; restore into an isolated target; migration head verification; tenant row/count reconciliation; document-object reconciliation; application health; same-tenant smoke; foreign-tenant denial; recovery point and recovery time; operator and timestamp. E1-03/platform owner must supply encryption, schedule, retention, alerting, and restore receipts. Until that drill is retained, RPO and RTO are unknown and `REAL_POSTGRES_RLS = NOT_RUN`.
