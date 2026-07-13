# Learning Expedition V1 implementation summary

Implemented as a global, local-profile subsystem inside the existing MentorFlow workspace.

- Added additive SQLAlchemy/Alembic expedition models and startup registration without deleting legacy data.
- Added 24 data-driven heroes, 12 enemy archetypes, 10 boss templates, 9 fixed-stat equipment definitions, 12 pets, gacha banners, infinite-stage generation and asset registry.
- Added server-authoritative deterministic BattleEngine with seeded RNG, action order, initiative stances, energy, statuses, shields, healing, critical/miss/dodge events and append-only persisted event logs.
- Added trusted Learning Run reward publishing: one verified standard minute grants one training EXP and one learning crystal. Ledger rows are idempotent and retain Workspace/Run provenance.
- Added the required `/api/expedition/*` profile, ledger, heroes, training, lineups, equipment, pets, gacha, stages, battles, assets and milestone APIs.
- Added 96 original local SVG assets (24 heroes × portrait/avatar/skill icon/skill cut-in), SHA-256 registry entries, safe asset IDs and no executable/remote SVG content.
- Added the frontend Learning Expedition view with resource dashboard, training slots, hero gallery/detail, cross formation editor, banners, infinite stages and a Phaser 3.90.0 event replay theater with 1x/2x/4x and reduced motion.

Verification: backend suite 107 passed; expedition acceptance 7 passed; frontend suite 46 passed; Playwright expedition flow passed with 18 steps; frontend production build and clean install passed; deterministic balance run 1,152 battles with 0.7665 overall win rate and observable dodge events.
