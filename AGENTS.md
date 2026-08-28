# SagaSmith Narrative Agent Guide

## Repository boundary

This is the current vertical source repository for system-neutral long-form
narrative play:

- `packages/domain` owns deterministic declarative schemas and validators.
- `packages/mcp` owns authoritative state, per-request authorization, phases,
  revisions, random streams, idempotency, settlement, and the stable tool catalog.
- `skills` owns reusable facilitation, semantic review, continuity, dialogue,
  and profile/Pack authoring procedures.

The former standalone Narrative MCP, Skills, and generic Module Generator
repositories are archived. Do not restore them as dependencies, mirrors,
fallbacks, or documentation authorities.

## Placement rules

- Domain remains declarative. A mechanic that requires arbitrary executable
  code or a multi-step state machine belongs in a separate game-system provider.
- MCP never infers prose meaning, audiences, NPC choices, geometry, or story.
- Agent/Skills may rule semantic questions when evidence is sufficient; block
  only for missing authority, unresolved human choice, stale state, conflicting
  indispensable evidence, or mechanically required data.
- Preserve isolated per-NPC dialogue workers and publish only explicitly
  settled audience-safe results.
- Target MCP 2026-07-28 with a deterministic catalog and Host-selected model
  subset. Keep the initialized/session path only as an explicit migration
  adapter; never add direct database writes, text simulations, or silent fallbacks.

## Validation

```powershell
uv sync --all-packages --all-extras
uv run --no-sync ruff check packages/domain packages/mcp
uv run --no-sync pytest packages/domain/tests
uv run --no-sync pytest packages/mcp/tests
```

When a public cross-component contract changes, also run the real stdio host
integration and the affected regression fixture path.
