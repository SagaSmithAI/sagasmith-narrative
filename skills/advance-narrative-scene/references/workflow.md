# Scene advancement and settlement

## Inputs

- Campaign/branch, active scene occurrence, participants, player intent, expected revisions, and idempotency key.
- Profile capabilities and evidence; current events, facts, ActorKnowledge, relationships, tracks, resources, threads, location/travel, and statuses.

## Evidence

- Authoritative `narrative_query`, `actor_query`, and `continuity_query` results filtered for the caller.
- Source fragments where a rule or module fact matters.
- Mechanic/random receipts for engine-owned procedures.

## Workflow

1. Discover and set Play exposure; re-query the active scene with `narrative_query` and load `actor_query`, `continuity_query`, and the binding.
2. Confirm actor/element authority and identify unresolved human choices. A facilitator-less campaign owner has administrative power only; scene start/update/end requires the caller to be a declared active steward with matching current element grants.
3. Determine whether the intent needs no mechanic, a declared profile mechanic, or an independent system provider.
4. Call `mechanic_resolve` only if current exposure includes it and all explicit inputs are known.
5. Construct one settlement proposal: scene transition, objective event, audiences, fact revisions, ActorKnowledge changes, relationships, tracks/resources/tags, threads, commitments, consequences, and source anchors.
6. Do not infer audience membership; provide explicit audience facts selected by the Agent.
7. Commit through `narrative_settle` using expected revisions and idempotency. Use `scene_change` actions `start`, `update`, and `end` only for scene lifecycle; include the Pack-declared `active_stewards` and verify each stewarded element against MCP grants.
8. On stale revision, refresh and re-evaluate instead of replaying a semantically changed proposal.
9. Verify the receipt and narrate only committed projections.

## Outputs

- Event/settlement IDs, random/mechanic receipts, new campaign/entity revisions, context binding, audience publications, diagnostics, and next legal actions.

## Blocking conditions

- Missing permission; unresolved player choice; stale revision; conflicting/missing indispensable evidence; mechanically missing input; or an active NPC conversation/activity that must close first.

## Context reset and boundary

Refresh after scene/phase/profile/grant/branch/restore/Conflict changes or `tools/list_changed`. The Agent decides meaning, audience, and consequences; MCP validates and commits. Never split atomic settlement, patch DB/core, or simulate a missing tool.

Every campaign write carries `campaign_id`, trusted `principal_id`, `expected_revision`, and `idempotency_key`; branch-sensitive writes also carry `expected_branch_id`. Verify the full returned `host_context_binding`.
