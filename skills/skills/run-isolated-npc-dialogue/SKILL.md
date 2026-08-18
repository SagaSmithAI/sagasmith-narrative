---
name: run-isolated-npc-dialogue
description: Run, continue, publish, close, or abort a persistent per-NPC isolated conversation through SagaSmith Narrative MCP and its host worker transport. Use when an NPC needs private knowledge, motives, memory, proposals, or audience-controlled dialogue without leaking private context to the Director.
---

# Run Isolated NPC Dialogue

Read [the isolation and settlement contract](references/workflow.md) before acting.

Activate exactly one authorized NPC worker context, keep private context and raw proposals out of the Director session, and publish only MCP-approved audience output. Treat worker text as a proposal until the MCP closes the conversation and atomically accepts selected events, knowledge, relationships, or commitments.

Close or abort before any mechanic, scene mutation, phase/Conflict transition, branch change, restore, role change, or actor-grant change invalidates activation.

On stale activation, do not reconstruct private state from memory; reset and reactivate from authoritative context.
