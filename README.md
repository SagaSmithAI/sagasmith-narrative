# SagaSmith Narrative

SagaSmith Narrative is the vertical monorepo for system-neutral long-form
tabletop narrative play. It versions deterministic declarative contracts, the
authoritative MCP server, and Agent Skills together while keeping their package
and runtime responsibilities separate.

## Repository layout

```text
packages/domain/                       declarative narrative schemas and validators
packages/mcp/                          authoritative session and tool runtime
skills/                                narrative facilitation procedures
skills/narrative-project-generator/    profile and Pack authoring procedure
```

The Domain package contains deterministic validation only. MCP remains the sole
authority for campaign state, authorization, phases, revisions, idempotency,
random streams, dynamic tool exposure, and atomic settlement. Semantic review,
audience decisions, NPC choices, and narration remain Agent/Skill concerns.

## Development

```bash
uv sync --all-packages --all-extras
uv run --no-sync ruff check packages/domain packages/mcp
uv run --no-sync pytest packages/domain/tests
uv run --no-sync pytest packages/mcp/tests
```

An application UI may be added later under `apps/ui`; no placeholder runtime or
fallback protocol is shipped today.
