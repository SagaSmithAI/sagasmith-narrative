# AGENTS.md

This repository contains MCP-first Skills for `SagaSmith-narrative-mcp`.

## Boundaries

- Use only the native, dynamically exposed Narrative MCP facade.
- Never add CLI, direct database/core access, fixed tool supersets, text simulation, or compatibility fallbacks.
- Keep authoritative state, authorization, revisions, random streams, idempotency, and settlement in MCP.
- Keep semantic interpretation, evidence review, audience decisions, and narration in the Agent/Skills.
- Treat finalized profiles and Packs as immutable; changes create a new draft/version.
- Block only for missing authority, unresolved human choice, stale authoritative state, conflicting or missing indispensable evidence, or mechanically required data.

## Skill structure

- Initialize every new Skill with the system `skill-creator` `init_skill.py`.
- Keep `SKILL.md` concise with only `name` and `description` frontmatter.
- Put detailed contracts in one-level `references/` files and generate `agents/openai.yaml`.
- Validate every Skill with `quick_validate.py`.
- Do not introduce standalone or portable variants.

## Workflow

- Preserve unrelated changes.
- Do not commit or push unless explicitly requested.
- Keep one current tool protocol and update all affected Skills when it changes.
