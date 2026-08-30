# sagasmith-narrative

> Current source: `sagasmith-narrative/packages/domain`. It is versioned with the sibling MCP and Skills; the former split repositories are archived.

Deterministic, declarative profile, record, audience, controller, and narrative
document validation shared by the SagaSmith Narrative runtime.

This package does not own sessions, persistence, authorization, random streams,
tool exposure, settlement, semantic interpretation, or narration.

## What belongs here

- immutable game-profile and Pack document shapes;
- actor, audience, controller, record, scene, and conflict declarations;
- runtime-manifest and campaign-design validation for authored or emergent
  episodes, including fronts, threads, clues, character arcs, and child-Pack
  lineage evidence;
- bounded Level 1 mechanics: dice pools, tables, track deltas, and resource
  deltas;
- deterministic JSON Schema validation and checksums.

Level 0 keeps all rulings explicit in the Agent/human workflow. A game needs a
dedicated system provider when it requires executable rule code, interacting
subsystems, a custom resolution graph, or prose interpretation. Do not encode a
rulebook as Narrative Domain branches.

## Install and validate

Python 3.11+:

```bash
pip install sagasmith-narrative
```

For repository development:

```bash
uv sync --all-packages --all-extras
uv run --no-sync pytest packages/domain/tests
uv run --no-sync ruff check packages/domain
```

The authoritative lifecycle, authorization, revisions, persistence, random
receipts, and settlement live in the sibling [Narrative MCP](../mcp). Original
code is licensed under Apache-2.0.
