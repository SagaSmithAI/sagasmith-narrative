# Game profile workflow

## Inputs

- Game identity and version; authoritative/openly usable sources; intended play modes.
- Actor schema, authority model, activities, narrative records, mechanics, advancement, recovery, and optional Conflict capabilities.
- Representative examples, edge cases, and expected receipts.

## Evidence

- Exact source references and license/distribution facts.
- Candidate decisions linking each declared mechanic to source evidence.
- MCP validation diagnostics and test vectors.

## Workflow

1. Discover Lobby profile authoring tools with `server_capabilities` and `exposure`.
2. Create or resume an MCP-owned draft with `profile_change` action `create_draft` or `update_draft`. Never edit a finalized profile.
3. Declare identity, compatibility, actor schemas, authority capabilities, activities, record extensions, and presentation labels.
4. Encode only bounded pure mechanics supported by the server: declared random procedures/tables, outcome bands, resource or track deltas, tag/status transitions, and fixed input/output schemas.
5. Store semantic interpretation, edge-case decisions, evidence, and reviewer notes in the draft audit record.
6. Validate mechanically and with representative fixtures; repair the draft and repeat.
7. Stop and recommend an independent system package if the profile needs arbitrary code, multi-step reactions, complex derived state, unique authoritative phases, or cross-entity invariants.
8. Present the complete validation/evidence report to an authorized reviewer.
9. Finalize explicitly with `profile_change` action `finalize`. Activation uses action `activate` as a separate authorized operation.
10. On migration, preview incompatibilities, remapping, and state deltas; snapshot/fork before applying.

## Outputs

- Immutable profile identity, version, checksum, capability manifest, validation report, evidence index, and finalization receipt.
- Migration preview or independent-system escalation report where applicable.

## Blocking conditions

- Missing/conflicting indispensable rule evidence or distribution rights.
- Unsupported mechanic whose result cannot be defined by the declared surface.
- Missing reviewer permission, stale draft revision, or unresolved authoritative choice.

## Context reset

Refresh exposure and campaign state after profile finalization, activation, migration, capability changes, branch/restore, or notification.

Every campaign-bound write carries `campaign_id`, trusted `principal_id`, `expected_revision`, `expected_branch_id`, and `idempotency_key`. Verify the complete returned `host_context_binding`, including exact profile ID/version/checksum and exposure revision.

## Boundary

Do not interpret prose into hidden executable behavior. Skills own review and semantic mapping; MCP owns drafts, validation, immutable versions, activation, random streams, and authoritative writes. Do not call core directly.
