# Director authority contract

## Inputs

- Campaign/branch/context binding, active scene and profile capabilities.
- Player-authored intent, participant/actor grants, content evidence, continuity, and ActorKnowledge projections.
- Safety preferences and current activity or optional Conflict state.

## Evidence

- `continuity_query`, `narrative_query`, `actor_query`, the exposed native tool list, and prior authoritative receipts.
- Human intent is evidence for intent, not proof that a mechanic succeeded.

## Workflow

1. Refresh exposure, call `campaign_query`, `narrative_query`, and `actor_query`, and load audience-filtered state with `continuity_query`.
2. Establish what is known, unresolved, and controlled by each participant.
3. Ask only for choices that cannot be inferred without overriding human authority.
4. Interpret fictional positioning and source evidence. Decide explicit audiences, perception, comprehension, response eligibility, NPC intent, and unresolved narrative geometry.
5. Call `mechanic_resolve` only if current exposure includes it; otherwise make only an authorized semantic ruling.
6. Submit explicit events and state deltas through `narrative_settle` with expected revisions and an idempotency key. Start/update/end a scene only as a profile facilitator or a declared active steward whose scene elements are currently granted; never treat campaign ownership alone as narrative authority.
7. Verify the receipt and context binding, then narrate from committed public/actor-specific results.
8. Use the isolated NPC dialogue Skill for persistent private NPC turns.
9. Call `conflict_start`, `conflict_query`, `conflict_act`, and `conflict_end` only when the active profile exposes them. Do not assume Conflict exists.

## Outputs

- Public and audience-specific narration tied to authoritative event IDs.
- Settlement receipt, updated revisions, pending human choices, and non-blocking diagnostics.

## Blocking conditions

- Unresolved player intent, missing permission, stale authority, conflicting/missing indispensable evidence, or missing mechanic input that makes the result undefined.

## Context reset

Close private NPC sessions before other authoritative mutations. If the context is already stale, use `abort` with the current binding; do not try to close or publish stale proposals. Refresh exposure after mechanic-owning mutations, scene/phase/Conflict transitions, profile/role/grant changes, branch/restore, advertised revision recovery, restart, or notification.

Every campaign write carries `campaign_id`, trusted `principal_id`, `expected_revision`, `idempotency_key`, and `expected_branch_id` when branch-sensitive. Verify every returned `host_context_binding` before narration continues.

## Boundary

The Agent owns interpretation and narration. MCP owns authoritative state, random results, authorization, revisions, audiences supplied explicitly, and settlement. No CLI, DB, core, fabricated result, fixed superset, or fallback.
