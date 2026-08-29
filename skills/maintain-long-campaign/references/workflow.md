# Long-campaign maintenance

## Inputs

- Campaign/branch/profile, elapsed fictional time, current session/scene closure, authorized maintenance scope, and player/NPC/faction commitments.
- Relationships, factions/projects, clocks/tracks, resources, goals/threads, locations/travel, tags/statuses, and outstanding consequences.

## Evidence

- Authoritative state and continuity; exact profile/Pack sources; previous settlement/random receipts; explicit human choices.

## Workflow

1. Refresh exposure and query branch-scoped campaign state.
2. Review unresolved proposals and active conversations/activities. Close or defer them explicitly.
3. Select `downtime_settle` for participant downtime or `world_turn_settle` for faction/world/time-skip evolution; keep travel and other narrative consequences inside the appropriate explicit settlement.
4. Ask authorized humans/Agent for semantic developments. Do not auto-simulate hidden intent.
5. Invoke `mechanic_resolve` only where explicitly declared, present in the host-selected stable catalog, and currently permitted; exposure is guidance rather than authority.
6. Build one explicit settlement with elapsed time, events/audiences, relationship/faction/clock/resource/thread/location/status changes, knowledge effects, and source anchors.
7. Commit with expected revisions and idempotency; verify receipts and refresh state.
8. Derive compact summaries and diagnostics from committed state. An authorized campaign administrator may snapshot at agreed milestones; ordinary stewards must request that administrative action rather than assuming it.
9. Record front, thread, clue, and character-arc progress through legal status
   transitions with typed current-branch evidence. A player arc completes only
   through one of its declared opportunity references plus audience-safe, branch-local event/fact/record evidence produced by play and linked to that actor or scene; an Atlas scene reference alone is never completion proof. If the next chapter requires a new scene, location, plot line, or
   character outside the active Atlas, use the signed zero-tool expansion
   proposal flow and settle it through a new immutable child Pack before use.

## Outputs

- World-turn/downtime settlement, changed entity revisions, random receipts, timeline event IDs, milestone snapshot, campaign-health diagnostics, and pending decisions.

## Blocking conditions

- Missing permission; unresolved player choice; stale state; conflicting/missing indispensable evidence; or a required profile input that makes the settlement undefined.
- Advisory readiness, portraits, optional fields, rendering, or authorized Agent rulings do not block.

## Context reset and boundary

Refresh after settlement, time jump, phase/profile/grant/branch/restore, advertised revision recovery, snapshot checkout, or notification. Agent chooses semantic evolution; MCP owns time/state/random/settlement. Never mutate DB/core or run a hidden fallback simulation.

Every settlement carries `campaign_id`, trusted `principal_id`, `expected_revision`, `expected_branch_id`, and `idempotency_key`. Verify the returned `host_context_binding` and random receipts.
