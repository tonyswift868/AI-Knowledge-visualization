# Story Catalog Matching Acceptance

The existing MentorFlow story-selection page now matches the current Workspace `TaskSemanticProfile` against the complete offline catalog before applying the display limit.

- Scans 10,000 SQLite catalog records plus the built-in overlay, then returns exactly three candidates when available.
- Keeps the highest READY Story Pack playable and keeps curated records without an installed Story Pack visible as `PACK_REQUIRED`.
- READY cards use `选择并启动`; PACK_REQUIRED cards use `提交策展请求` and cannot create a Campaign.
- Shows task type, knowledge structure, review need, coupling recommendation, and recommended narrative structures above the cards.
- Algorithm and law profiles produce different candidate sets, fit reasons, coupling scores, and narrative dimensions.
- Fake-provider matching is deterministic and returns one READY plus two PACK_REQUIRED candidates.
- Re-match rereads the current Workspace material and goal; profile writes are serialized to prevent duplicate browser requests racing SQLite.

See `story_catalog_matching_results.json` for algorithm, law, and fake-mode evidence.
