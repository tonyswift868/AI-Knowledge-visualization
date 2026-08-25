# LIVE-DEMO-04 Delivery Fence

Delivery: `LIVE-DEMO-04-DOCUMENT-IMPORT-V1`

This delivery provides an independent product application API and an independently mountable frontend feature. It intentionally does not register a central backend route or alter the application shell.

## Included

- Selection, safe preflight, batch start, exact-count progress, result, cancel, and checkpoint resume contracts.
- Per-file failure results; sibling files continue after a conversion failure.
- Provenance preview with only verified page, slide, section, or explicitly unknown location.
- Source-authored T0 exercise preview; unanswered items remain `NEEDS_ANSWER_VERIFICATION` with no trusted answer.
- Five-format local fixture corpus: PDF, DOCX, PPTX, HTML, and Markdown.
- Feature-local React view, client seam, styles, and interaction tests.

## Integration-only work

- The host must stage uploaded bytes under the configured `DocumentSourcePolicy.input_root` before calling `select()`.
- The host may adapt the endpoints declared in `integration_manifest.json` to `DocumentImportApplication`.
- The host owns route registration, authentication/authorization, workspace scoping, lifecycle construction, and mounting `DocumentImportView`.
- The host owns the real knowledge-pipeline port. `NOT_CONFIGURED` must remain visible until that port is supplied.

## Forbidden in this delivery

- No modification to `backend/app/main.py`, central backend routers, `frontend/src/App.tsx`, `frontend/src/main.tsx`, desktop shells, database migrations, or shared route ownership.
- No Provider call, network access, remote model fetch, model download, remote HTML fetch, OCR, VLM, external plugin, or new generated exercise.
- No estimated percentage, remaining-time claim, or fake progress fraction.
- No invented page or slide number and no conversion of `UNKNOWN` provenance into precision.
- No trusted answer for `NEEDS_ANSWER_VERIFICATION`.
- No checkpoint replay that reconverts an already completed document.

## Host acceptance gates

1. Mount the feature through a host-owned route without changing the DTO semantics.
2. Verify multipart staging cannot escape the configured input root.
3. Confirm each declared HTTP seam maps to the matching application method.
4. Run a browser pass for DOCX and PPTX using the exact delivery commit.
5. Confirm cancel, partial failure, provenance expansion, T0 warning, and resume are visible in the integrated UI.
