# Session settlement contract

## Inputs

- Campaign/branch/session, expected revisions, participant/actor grants, active scene/activity/conversations, and unsettled proposals.
- Events, facts, knowledge, relationships, tracks/resources, goals/threads, commitments, consequences, profile advancement/recovery, and requested snapshot/phase outcome.

## Evidence

- Authoritative session/scene/continuity queries, conversation journals, mechanic/random receipts, source anchors, and explicit participant choices.

## Workflow

1. Refresh exposure and inventory the authoritative session ledger; do not use chat memory as the inventory.
2. Close or abort every isolated NPC conversation and exclusive activity.
3. Classify each pending item as accept, reject, or explicitly defer. Request unresolved player decisions.
4. Reconcile event audiences, objective facts, ActorKnowledge, relationships, clocks/tracks, resources, commitments, advancement/recovery, and scene progress.
5. Submit one atomic session settlement with `narrative_settle`, expected revisions, and idempotency.
6. Verify event IDs, receipts, revisions, random cursor, and context binding.
7. Create a milestone snapshot with `snapshot_change` action `create` when requested or policy requires it.
8. Generate public, participant, actor-private, and facilitator summaries only from committed projections.
9. Report deferred items and the next legal phase/action.

## Outputs

- Session settlement receipt, resulting revisions, event/fact/knowledge IDs, snapshot ID, audience summaries, deferred items, diagnostics, and next binding.

## Blocking conditions

- Unresolved player choice, missing permission, stale authoritative revision, conflicting/missing indispensable evidence, or missing mechanically required input.

## Context reset and boundary

Refresh after settlement, snapshot, phase/profile/grant/branch/restore changes, or notification. A recap is never authority. Use MCP only; no CLI, DB/core, fabricated receipt, or manual fallback settlement.

Every campaign write carries `campaign_id`, trusted `principal_id`, `expected_revision`, `expected_branch_id`, and `idempotency_key`. Verify the returned `host_context_binding`, including profile checksum and exposure revision.
