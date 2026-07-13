# Architecture Map

```text
Story Pack files (read-only)
        |
        v
story_pack.py  -- schema/reference/checksum/path validation
        |
        v
narrative_runtime.py -- Campaign state + revision + event log
        |                         ^
        | learning events          | public scene / actions
        v                         |
Tutor / Evaluator / Task API ----+---- React Narrative Campaign page
        |
        v
Retrieval + Grounding + ProviderSession
```

- JSON business DB continues to own existing Workspace/Plan/Task/Session APIs.
- SQLAlchemy ORM owns Narrative Campaign/Event/Voice Cache persistence and Alembic migration.
- Workspace ownership is checked through the existing business DB before Campaign creation.
- Public Campaign responses remove true identity, false beliefs, secrets and forbidden speech.
- Fixed scene transitions are controlled by Story Pack graph and learning gates.
