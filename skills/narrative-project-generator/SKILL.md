---
name: narrative-project-generator
description: Turn source material for a system-neutral narrative tabletop project into reviewed declarative game-profile and Content, Module, or Campaign Seed Pack drafts through SagaSmith Narrative MCP.
---

# Narrative Project Generator

Read [workflow.md](references/workflow.md) before creating or revising project
artifacts.

Use this Skill when a project needs both a declarative game profile and one or
more portable Narrative Packs. Use the narrower `build-game-profile` or
`author-audit-content-pack` Skill when only one artifact class is in scope.

## Boundaries

- Author only MCP-owned drafts in Lobby. Use the native dynamic tool list and
  refresh after profile/Pack activation or any context-changing operation.
- Let the Agent interpret source meaning and record evidence-backed semantic
  decisions. Let the Narrative Domain validators enforce the bounded
  declarative contract. Let MCP own authorization, revisions, idempotency,
  finalization, import, activation, random streams, and settlement.
- Encode only supported Level 0/1 declarations. Recommend an independent system
  package when the rules require arbitrary code, multi-step reaction windows,
  complex derived state, unique phases, or cross-entity invariants.
- Keep source-specific repairs and decisions with the draft. Never promote one
  project's meaning into Core, the Narrative Domain, or MCP.
- Default to finalized artifacts only. Import and activation are separate
  authorized outcomes.

## Completion

Report profile and Pack identities, immutable versions/checksums, evidence and
license status, diagnostics, dependency locks, receipts, and the exact built,
imported, or active state of each artifact.
