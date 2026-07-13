# Known Limitations

- `dev_fixture_minimal` is intentionally not a formal story. It exists only for automated runtime verification and is visibly marked as a development fixture.
- No production Story Pack, canonical character bible, official dialogue, music, voice IDs or external assets were supplied, so none were invented or downloaded.
- V1 Persona Renderer preserves Tutor text exactly; a future renderer can add style only after a semantic-preservation implementation is supplied.
- Story dialogue is fixed/public-facts-only in V1. It does not create a formal character route or reveal hidden pack fields.
- FakeVoiceProvider records cache metadata and returns text fallback; playback/audio generation is not enabled.
- The existing Today Learning screen remains the authoritative Tutor/Evaluator interaction. Narrative Campaign links into it instead of duplicating lesson, retrieval or evaluator business logic.
- Dynamic Story Pack asset paths are validated by the backend contract; the current dev fixture deliberately uses a pure CSS placeholder background and anonymous silhouettes.
