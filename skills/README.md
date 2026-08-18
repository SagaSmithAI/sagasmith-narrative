# SagaSmith Narrative Skills

[Website](https://sagasmithai.github.io) · [Platform overview](https://github.com/SagaSmithAI/.github/blob/main/profile/README.md) · [Hosted service](https://github.com/SagaSmithAI/SagaSmith-service) · [Content catalog](https://github.com/SagaSmithAI/SagaSmith-dnd-content-library)

MCP-first Agent procedures for system-neutral, long-form narrative tabletop role-playing through `SagaSmith-narrative-mcp`.

These Skills do not contain a portable engine, CLI, database access, or fallback protocol. Narrative MCP owns authoritative state and transactions; Skills own reusable semantic review and facilitation procedures.

## Skills

| Skill | Purpose |
|---|---|
| `setup-narrative-campaign` | Campaign creation, profile/Pack binding, access, actors, and Lobby-to-Play readiness |
| `build-game-profile` | Evidence-backed declarative profile drafting, validation, finalization, and escalation |
| `author-audit-content-pack` | Content, Module, and Campaign Seed Pack authoring, trust review, and activation |
| `direct-narrative-campaign` | Live facilitation, authority, audiences, mechanics, and narration |
| `advance-narrative-scene` | Scene lifecycle and atomic narrative settlement |
| `run-isolated-npc-dialogue` | Persistent per-NPC private worker dialogue and controlled publication |
| `manage-campaign-continuity` | Fact/knowledge separation, contradiction repair, disclosure, and branching |
| `maintain-long-campaign` | Downtime, world turns, relationships, factions, clocks, and persistent evolution |
| `settle-game-session` | Authoritative post-session reconciliation, snapshot, and audience summaries |
| `backtest-recover-campaign` | Real-host public-facade regression and safe recovery |

Each Skill lives under `skills/<skill-name>/`, includes UI metadata in `agents/openai.yaml`, and places its detailed input/output/evidence/block/reset contract in `references/workflow.md`.

## Required MCP behavior

Skills discover the native tool list at runtime. `mechanic_resolve` and Conflict tools are optional profile capabilities and must never be assumed. Every context-changing operation requires exposure and `host_context_binding` refresh.

Campaign-scoped authoring queries are available in Lobby. Actor updates require
facilitator or explicit actor control; a facilitator-less owner has no implicit
narrative read/write authority. Snapshot/branch/revision recovery is reserved
for campaign administrators, and private NPC close receipts expose proposal IDs
without proposal text.

## Validation

Run the system `skill-creator` validator once for each `skills/*` directory.
