# Isolated NPC dialogue contract

## Inputs

- Campaign/branch/context binding, NPC actor ID, initiating actor/participant, conversation purpose, and proposed public/private audiences.
- NPC-private continuity supplied only by MCP to the isolated host worker.

## Evidence

- Caller/NPC actor grants, active scene, activation revision, worker binding, and MCP-filtered context.
- Conversation journal and proposed outputs remain non-authoritative until close.

## Workflow

1. Confirm Play exposure contains `npc_conversation`.
2. Ensure no incompatible active conversation or exclusive activity exists.
3. Open with `npc_conversation` action `open` and expected campaign/branch/actor revisions. Pass the activation only to the isolated worker.
4. Keep raw private context, motives, reasoning, and proposals out of the Director session.
5. Exchange candidate turns through action `propose` while the host transport keeps private worker context isolated and MCP validates freshness.
6. Publish only explicitly approved utterances with action `publish` and explicit audiences.
7. Treat proposed facts, knowledge, relationships, commitments, and consequences as proposals.
8. Use action `close` to atomically accept selected proposals, or `abort` with a reason and no authoritative semantic deltas.
9. Verify the close/abort receipt, refresh context, and narrate only authorized publications.

## Outputs

- Conversation ID, activation/close receipts, approved publications, accepted event/state deltas, and updated context binding.

## Blocking conditions

- Missing NPC/participant grant, stale activation, private context delivery failure, incompatible active activity, or unresolved human approval for an authoritative proposal.

## Mandatory reset

Close or abort before mechanics, scene mutation, phase/Conflict transition, role/grant/profile changes, branch/restore, any advertised revision recovery, or restart. On staleness, reactivate; never reconstruct the worker from Director memory.

Every write carries `campaign_id`, trusted `principal_id`, `expected_revision`, `expected_branch_id`, and `idempotency_key`. Verify the complete returned `host_context_binding` after open/publish/close/abort.

## Boundary

Host owns worker execution; MCP owns activation, journal, authorization, publications, revisions, and close settlement; Agent owns NPC response selection. No CLI, direct DB/core, raw proposal leak, or fallback dialogue path.
