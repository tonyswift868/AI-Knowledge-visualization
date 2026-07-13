# MentorFlow Final MVP Acceptance

| Area | Result | Evidence |
|---|---|---|
| Backend regression | PASS | 92 tests passed |
| Frontend regression | PASS | 44 tests passed |
| Build | PASS | `npm run build` |
| Clean install | PASS | clean temp directory: `npm ci`, test, build |
| E2E | PASS | 9 tests passed, including reference ending/postgame |
| Smoke | PASS | workspace/source/plan/tutor/progress/replan |
| Security smoke | PASS | root rejection, ZIP preview confirmation, key redaction |
| Alembic | PASS | upgrade through `0007_learning_run_idempotency` |
| Catalog | PASS | 10,000 total; 41 curated; 9,959 candidate; unique IDs |
| Reference Story Pack | PASS | 13 reachable scenes; checksum verified |
| Real Provider mode | PASS | mock transport; no fake fallback; key scan clean |

## Implemented contract points

- Server-only Learning Event publication; browser-forged evaluator events are rejected.
- `scene_completed` is server-derived from Learning Run and is not accepted from the client.
- Learning Run phases, review phases, final assessment, evaluator scoring, hints, evidence, and idempotency are persisted.
- Narrative line cursor, fixed choices, CAS revision checks, entry/exit/ending conditions, and gate bindings are enforced.
- Story Pack assets are served by `asset_id`; path traversal, unsafe references, wrong asset kinds, and future character knowledge are rejected or filtered.
- Reference Story Pack backgrounds and character asset URLs are exposed to the theater; the frontend also uses its original character illustration set for a more readable game presentation.
