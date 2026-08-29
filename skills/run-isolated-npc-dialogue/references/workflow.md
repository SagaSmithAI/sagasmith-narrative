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
3. Open with action `open`, the expected campaign revision/branch, an NPC actor ID for which the caller has both control and private access, and explicit actor/principal interlocutors plus permitted publication scopes. MCP—not the caller—issues the actor-scoped activation reference and close capability. Never open a PC worker.
4. Claim the activation. Deliver its server-issued worker ID, lease, signed context receipt, four-track actor memory, and constraints only to one persistent isolated worker. The worker may call no tools, roll no randomness, write no state, or publish directly.
5. Keep raw private context, motives, reasoning, and proposals out of the Director session. Do not substitute a caller-selected `private_worker_id` or an unsigned context blob.
6. Submit candidate turns through action `propose` with the claimed `activation_ref`, `lease_id`, exact signed `context_receipt`, and the `character-conversation-proposal.v1` object. MCP verifies the receipt against the leased context digest. Each utterance segment targets only declared interlocutors and declares `content_mode` as `nonfactual`, `grounded`, `deception`, or `uncertain`; every factual-like mode cites actor-owned `basis_refs`.
7. When only this actor's authoritative context changes, call action `refresh`. Claim the returned replacement activation, preserving the prior reason and cursors; never reuse the invalidated activation or lease.
8. For a declared performance beat, record the exact Pack references used, the belief before/after edge and causal event, the respected red lines, relationship links, arc stage, and a verbatim declared voice marker. Keep this runner/audit evidence outside the native MCP argument object.
9. Publish only explicitly approved utterance segments with action `publish` and an audience scope declared at open; targeted actor/principal IDs remain a subset of the interlocutors, and the publication audience must cover every segment target. Never table/public-broadcast a targeted segment. Publication strips modes, basis references, and private intent. Reuse a declared voice marker, but reject any publication containing a Pack-declared private token.
10. Treat proposed facts, knowledge, relationships, commitments, memory candidates, and consequences as proposals.
11. Use action `close` with the server-issued close token to atomically accept only already-published selected proposal IDs and an explicit settlement, or `abort` with no authoritative semantic deltas. A stale conversation may only be aborted from the current binding.
12. Verify the close/abort receipt, which may contain accepted proposal IDs but never raw private proposal text, worker secret, or private memory. Refresh context and narrate only authorized publications.

## Outputs

- Conversation ID, activation/close receipts, approved publications, accepted proposal IDs and event/state deltas, updated context binding, and—when declared—performance evidence linking voice, motive, belief transition, relationship, red-line, and arc stage.

## Blocking conditions

- Missing NPC/participant grant, stale activation, private context delivery failure, incompatible active activity, or unresolved human approval for an authoritative proposal.

## Mandatory reset

Close or abort before mechanics, scene mutation, phase/Conflict transition, role/grant/profile changes, branch/restore, any advertised revision recovery, or restart. Actor-local memory refresh is the only in-place replacement path. On broader staleness, abort from the current binding and reopen; never reconstruct the worker from Director memory.

Every write carries `campaign_id`, trusted `principal_id`, `expected_revision`, `expected_branch_id`, and `idempotency_key`. Verify the complete returned `host_context_binding` after open/publish/close/abort.

## Boundary

Host owns worker execution; MCP owns activation, journal, authorization, publications, revisions, and close settlement; Agent owns NPC response selection. No CLI, direct DB/core, raw proposal leak, or fallback dialogue path.
