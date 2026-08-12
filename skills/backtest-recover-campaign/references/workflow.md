# Public-facade regression and recovery

## Inputs

- Real MCP endpoint/transport and real host, Skill installation, clean test storage or explicitly selected campaign, and machine-readable scenario manifest.
- Expected profiles/Packs, principals/roles/actors, legal endings, focused alternate paths, and explicit exclusions.
- A validated fixture runner for the declared route schema, including logical-to-authoritative actor alias resolution. Do not improvise an undocumented route dialect during execution.

## Required evidence

- Bounded `skill_query` list/search/get_section, `server_capabilities`, exposure search/set, native `tools/list_changed`, host schema refresh, and context bindings.
- Complete native tool request/result timeline with revisions, idempotency keys, random receipts, settlements, restart/resume, and final authoritative state.
- Authorization/audience denial evidence and no private-context leakage.
- For character-performance fixtures, a Pack-declared oracle and exact evidence for private/public beat pairing, voice markers, public-goal/private-motive links, red-line observance, belief transitions with causal events, relationship arcs, and monotonic character stages. This is deterministic contract validation, not model-scored prose quality.

## Regression workflow

1. Start a real MCP server and host; use bounded `skill_query` to discover the installed Skill and native facade without loading whole Skill files.
2. Validate the fixture manifest/route against the runner's supported schema and establish actor/reference alias materialization before any campaign write. Stop if the runner cannot preserve referential identity.
3. Create/import/finalize/activate through public Lobby tools; never seed internal services.
4. Exercise Lobby to Play and optional Conflict to Play where supported. Confirm tools appear/disappear and the next legal native call succeeds.
5. Cover facilitator/player/observer and actor/element grants, audience projections, stale revisions, exact idempotent retry, payload mismatch, and random replay.
6. Cover isolated NPC activation/publication/close and private-context isolation. Where a performance contract exists, require at least three paired private/public beats per declared NPC, exercise every declared voice, prove allowed belief transitions and relationship causes, and fail on private-token publication, red-line mismatch, unsupported belief edges, or arc-stage regression.
7. Restart/resume; `snapshot_change` create/restore; `branch_change` create/checkout; and `state_revision` action `list`. Force context refresh after each state-changing operation.
8. Drive every discovered runnable campaign fixture to a legal ending; record exclusions and focused mutually exclusive paths without Cartesian explosion.
9. Run all discovered original Packs in parallel, in distinct campaigns. The minimum matrix contains: one random, facilitator-led long campaign with factions, clocks, downtime, and optional Conflict; one diceless, facilitator-optional campaign with distributed element stewardship and no Conflict tools; and one dialogue-heavy campaign with at least five persistent NPCs, conflicting public/private motives, false-belief changes, relationship arcs, red lines, voice markers, and a recovered alternate ending. Prove different exposure sets and legal endings.
10. Assert campaign, branch, actor knowledge, exposure revision, idempotency scope, and random stream isolation across the parallel runs. Also prove stale-revision rejection for concurrent writers in one campaign.
11. Compare final state and receipts with declared invariants. Prose cannot close missing evidence.

## Recovery workflow

1. Stop writes, refresh trusted principal, and call `server_capabilities`, `campaign_query`, and `exposure`.
2. Inspect state with `narrative_query`, `continuity_query`, `snapshot_query`, `branch_query`, `state_revision` action `list`, and current exposure.
3. Choose the narrowest native recovery: exact retry, close/abort stale activity, refresh revision, restart/resume, snapshot restore to a new branch, or checkout. Never simulate an unadvertised undo/redo path.
4. Verify exposure notification, binding, and the next legal native call.

## Outputs

- Machine-readable case verdicts, per-campaign tool/notification timelines, receipts, legal ending states, cross-campaign isolation assertions, character-performance counters where declared, exclusions, failure/recovery classification, and residual risk.

## Context reset

After restart, phase/profile/role/grant changes, snapshot restore, branch checkout, any advertised revision recovery, or `tools/list_changed`, discard stale native schemas and cached context. Re-run `exposure` and `campaign_query`, verify the returned binding, and prove the next legal native call.

## Blocking conditions and boundary

Block when the real facade/host cannot prove authority or when safe recovery needs human selection. Never call DB/core/internal services, fabricate results, use fixed/fallback tools, weaken authorization/evidence, or overwrite a live branch without explicit authority.

Every campaign write carries `campaign_id`, trusted `principal_id`, `expected_revision`, `idempotency_key`, and `expected_branch_id` when branch-sensitive. Assert every `host_context_binding` field and exposure revision.
