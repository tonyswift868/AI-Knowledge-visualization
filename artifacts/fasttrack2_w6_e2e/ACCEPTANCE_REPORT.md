# FAST-TRACK 2 W6 Acceptance Report

Date: 2026-07-24

## Reproducible coverage

- `backend/tests/test_w6_acceptance_matrix.py` uses isolated legacy/ORM
  databases and generated fixtures; it does not read user materials.
- Three datasets cover algorithms, a mixed textbook, and a code repository.
- The HTTP matrix covers archive receipt, source-grounded knowledge map, goal,
  plan, today, Learning Run/Evaluator, review, reward, half completion,
  absence, goal/capacity change, replan/undo, bounded repeated failure,
  explicit self-study, Provider failure, and KKS unavailable/partial fallback.
- PPTX/XLSX archive ingestion remains an explicit `ignored + reason` boundary;
  their bounded conversion adapters are verified separately by W2 tests.

## Current results

```text
powershell -ExecutionPolicy Bypass -File .\scripts\w6_fasttrack2_acceptance.ps1 -IncludeLegacyBrowser
HTTP/security hard gate: 14 passed
Playwright browser matrix: 4 passed
exit 0
```

The browser matrix now covers:

1. Demo to the current import → knowledge map → planning → today → AI tutor
   workflow and server-gated completion.
2. A real Provider failure remaining visible without fallback to Fake.
3. Normal ZIP preview/import with internal paths.
4. ZIP traversal rejection before source import.

The stale browser failures recorded in commit `5751185` were repaired after
that audit. Root causes were an actual local-date bug around midnight (UTC
serialization produced yesterday and Goal returned 422) plus selectors for
the retired pre-LearningFlow UI. Stable test ids now follow the current flow.

## Still unproven

- Native KKS PMX/GLB fidelity, material quality, real 20+ card memory/VRAM
  behavior, and visual regression remain blocked by absent legal native
  artifacts.
- External public Provider availability is not claimed; local failure safety
  and deterministic teacher fallback are verified.
- Production legacy/ORM source-of-truth convergence is not complete. Recovery
  primitives and migration safety exist, but silent dual-write removal
  requires a separately approved data migration.
