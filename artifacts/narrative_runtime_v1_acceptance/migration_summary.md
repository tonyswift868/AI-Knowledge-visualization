# Migration Summary

- Added Alembic revision `0005_narrative_runtime_v1`.
- Tables: `orm_narrative_campaigns`, `orm_narrative_events`, `orm_narrative_voice_cache`.
- Campaign state is JSON inside SQLAlchemy, with `revision` for optimistic concurrency.
- Events have a unique event ID and a unique `(campaign_id, idempotency_key)` constraint.
- Migration is idempotent with the existing `init_orm()` compatibility behavior: already-created tables are inspected and skipped.
- No existing business tables or user data were deleted or rebuilt.
