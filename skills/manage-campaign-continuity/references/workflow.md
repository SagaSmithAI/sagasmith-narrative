# Continuity workflow

## Evidence hierarchy

1. Branch-scoped authoritative events, fact revisions, ActorKnowledge, and settlement receipts.
2. Exact finalized profile/Pack versions and source anchors.
3. Scene/module progress and actor state.
4. Session summaries and retrieval results as projections, never truth.
5. Agent inference, clearly marked and never silently persisted.

## Inputs

- Campaign, branch, caller audience/actor, issue description, time/scene scope, and requested repair or disclosure.

## Workflow

1. Refresh binding and request a bounded `continuity_query`; page or narrow scope rather than blocking on context size.
2. Separate objective facts, actor knowledge, secrets, rumors, summaries, and inference.
3. Identify the smallest conflicting claims and their branches/revisions/sources.
4. Classify the issue: stale projection, evidence conflict, accidental disclosure, missing event, incorrect knowledge, or deliberate canon change.
5. If sources determine the answer, propose the narrowest source-backed correction. If canon choice remains, request authorized human selection.
6. Commit a new auditable event/fact/knowledge revision or disclosure through `narrative_change` or atomic `narrative_settle`. An authorized campaign administrator may use `branch_change` action `create`/`checkout` for an explicit alternate timeline. Never rewrite old history silently.
7. Preserve audience boundaries in diagnostics and repair narration.
8. Re-query continuity and verify that the contradiction is resolved without erasing legitimate uncertainty.

## Outputs

- Evidence ledger, issue classification, affected revisions/audiences, repair or branch receipt, remaining uncertainty, and refreshed context binding.

## Blocking conditions

- Conflicting/missing indispensable evidence, missing permission, unresolved human canon choice, stale revision, or inability to repair without unauthorized disclosure.

## Context reset and boundary

Refresh after repair, disclosure, checkout, restore, advertised revision recovery, profile/Pack activation, or notification. Agent judges semantic conflict; MCP owns branch-scoped facts, knowledge, audiences, revisions, events, and atomic commit. No DB/core edit or summary-as-truth fallback.

Administrative recovery uses `branch_query`, `snapshot_query`, and `state_revision` action `list`; these tools are intentionally unavailable to ordinary players/observers. Non-admin callers use their current binding and audience-filtered continuity or hand recovery to an administrator. Recover with snapshots and branches; do not invent an undo/redo compatibility path. Every write carries the common campaign/revision/branch/idempotency fields and must return a verified `host_context_binding`.
