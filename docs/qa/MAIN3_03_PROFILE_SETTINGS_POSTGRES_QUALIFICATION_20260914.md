# Profile/settings PostgreSQL qualification

The bounded qualification used the owned native loopback PostgreSQL 17.6 fixture. Alembic reached `0039_generic_sync_kinds` and the application role passed the canonical structured-sync and RLS checks. The relay delivered local SQLite outbox intents through `StructuredSyncService`; an ACK-loss retry replayed safely by request id. A fresh local store restored profile and nonsecret settings from the PostgreSQL snapshot without echo writes. A second synthetic account could not read the first account's snapshot, and secret fields were rejected before persistence.

Runner receipt: `E:/Codex/Temp/MF-MAIN3-03-PROFILE-SETTINGS-PG-QUALIFY-20260914-06c/mf-schema-0036-c0b30646b1c1653b-profile-settings.json`.
