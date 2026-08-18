# Isolated NPC dialogue contract

## Inputs

- Campaign/branch/context binding, NPC actor ID, initiating actor/participant, conversation purpose, and proposed public/private audiences.
- NPC-private continuity supplied only by MCP to the isolated host worker.

## Evidence

- Caller/NPC actor grants, active scene, activation revision, worker binding, and MCP-filtered context.
- Conversation journal and proposed outputs remain non-authoritative until close.
- When a finalized Pack declares a character-performance contract, preserve its exact public-goal, private-motive, red-line, false-belief, relationship-history, voice-marker, and arc-stage references as proposal evidence. These declarations constrain the Agent's selection; they do not make MCP infer prose quality or NPC intent.

## Workflow

1. Confirm Play exposure contains `npc_conversation`.
2. Ensure no incompatible active conversation or exclusive activity exists.
3. Open with `npc_conversation` action `open`, expected campaign revision/branch, a controlled NPC actor ID, and a non-empty private-worker binding. Pass the activation only to the isolated worker.
4. Keep raw private context, motives, reasoning, and proposals out of the Director session.
5. Exchange candidate turns through action `propose` while the host transport keeps private worker context isolated and MCP validates freshness.
6. For a declared performance beat, record the exact Pack references used, the belief before/after edge and causal event, the respected red lines, relationship links, arc stage, and a verbatim declared voice marker. Keep this runner/audit evidence outside the native MCP argument object.
7. Publish only explicitly approved utterances with action `publish` and explicit audiences. Reuse the declared voice marker, but reject any publication containing a Pack-declared private token.
8. Treat proposed facts, knowledge, relationships, commitments, and consequences as proposals.
9. Use action `close` to atomically accept selected proposal IDs and an explicit settlement, or `abort` with no authoritative semantic deltas. A stale conversation may only be aborted from the current binding.
10. Verify the close/abort receipt, which may contain accepted proposal IDs but never raw private proposal text. Refresh context and narrate only authorized publications.

## Outputs

- Conversation ID, activation/close receipts, approved publications, accepted proposal IDs and event/state deltas, updated context binding, and—when declared—performance evidence linking voice, motive, belief transition, relationship, red-line, and arc stage.

## Blocking conditions

- Missing NPC/participant grant, stale activation, private context delivery failure, incompatible active activity, or unresolved human approval for an authoritative proposal.

## Mandatory reset

Close or abort before mechanics, scene mutation, phase/Conflict transition, role/grant/profile changes, branch/restore, any advertised revision recovery, or restart. On staleness, abort from the current binding and reactivate; never reconstruct the worker from Director memory.

Every write carries `campaign_id`, trusted `principal_id`, `expected_revision`, `expected_branch_id`, and `idempotency_key`. Verify the complete returned `host_context_binding` after open/publish/close/abort.

## Boundary

Host owns worker execution; MCP owns activation, journal, authorization, publications, revisions, and close settlement; Agent owns NPC response selection. No CLI, direct DB/core, raw proposal leak, or fallback dialogue path.
