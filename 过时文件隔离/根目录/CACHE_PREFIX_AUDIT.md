# MentorFlow Provider Cache Prefix Audit

Date: 2026-08-24

Scope: read-only inspection of the current Teacher, Planning, and Knowledge Map prompt assembly paths.

Boundary: this report does not change prompts, business reducers, local result caches, or Provider policy.

## Interpretation boundary

This audit concerns **Provider-side prompt-prefix caching** (for example, DeepSeek's reported `prompt_cache_hit_tokens` and `prompt_cache_miss_tokens`). It is not the same mechanism as MentorFlow's local planning result caches. The AI-01 receipt therefore records Provider prompt-cache counters separately and leaves local result-cache state unavailable unless a caller supplies it explicitly.

No cache-hit result is claimed in this report. Prefix reuse depends on the exact serialized request, Provider implementation, request timing, and Provider retention policy; only real usage receipts can establish it.

## Teacher

Current status: **low cross-turn prefix stability; measurable after AI-01**.

Observed prompt shapes:

- Teaching expression puts a stable system message first, then serializes a user object whose earliest fields include the action, route position, source-grounded draft, tools, persona, learner context, mastery model, memory, navigation context, latest message, and evidence. Stable rules appear near the end of that dynamic object (`backend/app/active_teaching.py`, `_provider_teaching_response`).
- TeacherTurnV1 expression starts its user object with prompt version, phase, concept, learner context, evidence, analogy, check, and visual contract before the stable rule list (`_v1_teacher_turn`).
- Rubric evaluation starts with the current knowledge point, learner context, learner answer, and source evidence; the prompt version and rules come later (`_evaluate_understanding`).

Top cache killers:

1. Route state, latest learner message, answer, memory, and citations change every turn before most reusable instructions.
2. Persona and learner-profile objects are serialized in the prefix even when only a small value changed.
3. Evidence quotes and generated drafts are large, turn-specific values ahead of the stable rule suffix.

Safe future opportunity (not implemented): preserve the current application-owned semantics while moving stable versioned instructions/schema ahead of dynamic state, then keep dynamic state in a final bounded message. This requires its own behavioral regression task because prompt order can affect model behavior.

## Planning

Current status: **stable system prefix, highly dynamic user prefix; local result cache exists but is a separate mechanism**.

Observed prompt shapes:

- Planning interpretation uses a stable system message followed by a JSON object containing the current message, goal, conversation/history, selected resources, candidate discovery state, and exact references before reusable requirements and response schema (`backend/app/generic_knowledge_api.py`, planning-assistant Provider request).
- Candidate semantic review starts with objective, learner message, anchor, discovery mode/target/round, existing candidate summaries, and the numbered candidate URLs/titles/snippets. The reusable response schema and review requirements follow these values (`_planning_provider_candidate_review`).
- MentorFlow already maintains planning Provider-result/search caches keyed by request content. Those hits must not be reported as DeepSeek prompt-cache hits; AI-01 leaves `local_result_cache_hit=null` unless explicitly provided by the owning cache path.

Top cache killers:

1. Full conversation/history and exact learner message precede the reusable planning contract.
2. Candidate identity, URL, title, snippet, page-expansion round, and selected-resource summaries change between discovery rounds.
3. Reusable requirements/schema are placed after the largest dynamic candidate payload.

Safe future opportunity (not implemented): give planning interpretation and candidate review separate prompt versions, place the stable contract/schema first, and keep candidate arrays in the last message. Validate independently from local result-cache behavior.

## Knowledge Map

Current status: **best intra-job reuse shape, but concurrent batches and dynamic leading scope may reduce realized hits**.

Observed prompt shape:

- Each extraction batch sends a stable system message. The user object starts with learner level, prompt version, and the full anchor/support scope, followed by stable scope rules, knowledge-map rules, schema, and finally the batch-specific chunks (`backend/app/generic_knowledge_api.py`, knowledge-map batch generation).
- Within one map job, learner level, scope, rules, and schema are normally identical while chunks change at the end. This is structurally more cache-friendly than the Teacher and Planning prompts.

Top cache killers:

1. Learner level and the complete scope object precede the reusable rule/schema block, reducing reuse across workspaces or scope revisions.
2. Multiple extraction batches are launched concurrently. Even identical prefixes may not be available in the Provider cache before sibling requests begin.
3. Any ordering or serialization drift in scope/material metadata changes the exact prefix.

Safe future opportunity (not implemented): retain deterministic serialization, version the extraction prompt explicitly, place stable scope rules/schema before the dynamic scope, and evaluate whether bounded sequential warm-up improves hit ratio without unacceptable latency.

## Measurement recommendation

Collect AI-01 receipts by `task_type`, `provider`, `model`, `prompt_version`, and `schema_version`, then compare:

- prompt-cache hit ratio distribution (do not coerce unavailable to zero);
- prompt/completion/total tokens;
- latency and error class;
- request count and successful-request count.

Only after representative real-provider receipts exist should a separate task change prompt ordering. The first candidate is Knowledge Map batch extraction because its dynamic chunks are already last and its correctness contract is strongly schema-validated.
