# SagaSmith Narrative

[Domain](packages/domain/README.md) · [MCP](packages/mcp/README.md) ·
[Skills](skills/README.md) ·
[Platform](https://github.com/SagaSmithAI/.github/blob/main/profile/README.md)

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

This repository is the current source of truth for every Narrative component
listed above. The former standalone MCP, Skills, and generic Module Generator
repositories are archived read-only; current issues, releases, integrations,
and documentation belong here.

## Verified integration baseline

The current hosted boundary keeps Narrative process-local over stdio while the
Agent supplies signed `sagasmith.auth-context/v1` principal context and refreshes
native schemas after `tools/list_changed`. Real-Agent integration coverage calls
the public MCP facade rather than fabricated internal results. On 2026-08-20
this revision was included in the rebuilt hosted stack used for the concurrent
D&D and CoC reference regressions; that stack result validates composition and
startup, not a complete Narrative project playthrough.

## Development

```bash
uv sync --all-packages --all-extras
uv run --no-sync ruff check packages/domain packages/mcp
uv run --no-sync pytest packages/domain/tests
uv run --no-sync pytest packages/mcp/tests
```

An application UI may be added later under `apps/ui`; no placeholder runtime or
fallback protocol is shipped today.
