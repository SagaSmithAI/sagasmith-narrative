---
name: run-isolated-npc-dialogue
description: Run, continue, publish, close, or abort a persistent per-NPC isolated conversation through SagaSmith Narrative MCP and its host worker transport. Use when an NPC needs private knowledge, motives, memory, proposals, or audience-controlled dialogue without leaking private context to the Director.
---

# Run Isolated NPC Dialogue

Read [the isolation and settlement contract](references/workflow.md) before acting.

Open only an NPC for which the caller has both control and private access, declare its actor/principal interlocutors and permitted publication scopes, then claim the server-issued activation and lease in exactly one persistent zero-tool worker. Keep private context and raw proposals out of the Director session, and publish only MCP-approved participant-bounded output. PC workers are forbidden.

Close or abort before any mechanic, scene mutation, phase/Conflict transition, branch change, restore, role change, or actor-grant change invalidates activation.

On actor-local context change, use `refresh` and move the worker to the replacement activation, which preserves its reason and cursors. The old activation is invalid. On broader campaign staleness, abort from current authority and reopen; never reconstruct private state from Director memory.
