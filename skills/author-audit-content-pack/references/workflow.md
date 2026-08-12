# Pack authoring and audit workflow

## Pack classes

- Content Pack: reusable setting/content facts and licensed assets.
- Module Pack: runnable scenes, cast, locations, clues, routes, continuity seeds, and optional endings.
- Campaign Seed Pack: portable profile/Pack locks and initial state; excludes live identities, permissions, knowledge, random cursors, and branches.
- Live backup is private operational state, not a distributable Pack.

## Inputs

- Target class, identity/version, compatible profile, dependencies, source material, assets, license/distribution facts, and draft content.
- Requested operation: create, import, review, repair, finalize, activate, migrate, or audit.

## Evidence

- Source references, extraction provenance, candidate decisions, checksums, dependency locks, license metadata, and review history.
- MCP validation/finalization/import receipts.

## Workflow

1. Discover Lobby Pack tools through `server_capabilities` and `exposure`.
2. Create an MCP draft with `pack_change` action `create_draft`, or resume it with `update_draft`. Inspect archive safety and dependency diagnostics before semantic review.
3. Run the mechanical first pass; then audit identity, compatibility, schema, sources, private content, scenes, cast, continuity seeds, assets, and profile extensions.
4. Repair every in-scope draft field through MCP authoring tools. Store source-specific decisions with this Pack.
5. Revalidate until only advisory diagnostics remain.
6. Verify distribution rights separately from the ability to use content privately.
7. Obtain explicit authorized finalization with `pack_change` action `finalize`. Never mutate the released version.
8. Import finalized versions with action `import`; keep them inactive until a separate `activate` action.
9. For upgrades, create a new draft/version and preview campaign remapping/migration before snapshot/fork and activation.

## Outputs

- Pack class, immutable version/checksum, dependency graph, evidence/license index, audit findings, activation state, and receipts.

## Blocking conditions

- Missing permission; stale draft; checksum/dependency conflict; incompatible profile; mechanically indispensable data; unresolved source conflict; or unclear rights for the requested distribution.
- Portraits, optional cards, estimated sessions, presentation quality, and render failures are diagnostics unless mechanically required.

## Context reset and boundary

Refresh after activation, migration, checkout, restore, or notification. Use only Narrative MCP; never write archives, DB rows, core state, or a compatibility format directly. The Agent reviews meaning; MCP owns drafts, validation, versions, activation, migration, and state.

Every campaign-bound write carries `campaign_id`, trusted `principal_id`, `expected_revision`, `expected_branch_id`, and `idempotency_key`. Verify the returned `host_context_binding` before continuing.
