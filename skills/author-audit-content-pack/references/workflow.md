# Pack authoring and audit workflow

## Pack classes

- Content Pack: reusable setting/content facts and licensed assets.
- Module Pack: runnable scenes, cast, locations, clues, routes, continuity seeds, and optional endings.
- Campaign Seed Pack: portable initial state using logical principal/actor references, declared memberships, initial actor/element grants, records, and initial ActorKnowledge. It excludes external transport credentials, live session identities, random cursors, snapshots, and branches.
- Live backup is private operational state, not a distributable Pack.

## Runtime narrative manifests

- Put live-growth metadata in `content.runtime_manifest` schema version 1. A campaign design has exactly one generation-zero root classified as `authored_narrative` or `emergent_seed`.
- Record setting anchors and initial Atlas scenes plus fronts, threads, clues, and character arcs. Player-character arcs expose opportunities and pressures, never a required beat or predetermined ending.
- Every later `emergent_episode` is a new Pack key/version. Its lineage uses the same root, names an already active parent, advances generation by exactly one, and cites only delivered authorized evidence from the expansion context. All scene/chapter/front/thread/clue/arc IDs remain unique across the active lineage.
- A reasonable authored-narrative detour outside the current Atlas uses the same child-Pack path. Never patch the finalized parent in place.

## Inputs

- Target class, identity/version, compatible profile, dependencies, source material, assets, license/distribution facts, and draft content.
- Requested operation: create, import, review, repair, finalize, activate, migrate, or audit.

## Evidence

- Source references, extraction provenance, candidate decisions, checksums, dependency locks, license metadata, and review history.
- MCP validation/finalization/import receipts.

## Workflow

1. Confirm the host-selected stable catalog through `server_capabilities`; use `exposure` only as Lobby Pack guidance.
2. Query campaign-scoped drafts with `narrative_query`; create with `pack_change` action `create_draft`, or resume an existing draft with `update_draft`. Inspect dependency and schema diagnostics before semantic review.
3. Run the mechanical first pass; then audit identity, compatibility, schema, sources, private content, scenes, cast, continuity seeds, assets, and profile extensions.
4. Repair every in-scope draft field through MCP authoring tools. Store source-specific decisions with this Pack.
5. Revalidate until only advisory diagnostics remain.
6. Require every evidence entry to name its type and a reproducible locator. Verify the explicit distribution decision (`public`, `private`, `restricted`, or `internal`) and license/rights basis separately from the ability to use content privately.
7. Obtain explicit authorized finalization with `pack_change` action `finalize`. Never mutate the released version.
8. Import finalized versions with action `import`; keep them inactive until a separate `activate` action.
9. For upgrades, create a new draft/version and preview explicit campaign remapping before snapshot/fork and activation. The current facade has no generic Pack-migration action; do not simulate one.
10. For runtime growth, obtain a signed strictly bounded context with `campaign_expansion` action `context`; run the proposal-only zero-tool worker; validate every nested evidence reference with action `validate`; attach the returned proposal attestation to the unchanged child manifest; declare the active parent Pack as a dependency with its checksum; then use the returned settlement route to create, finalize, import, and activate the child Pack. The attestation is bound to the server instance's restart-stable signing key, never a Pack-embedded secret. Validation is not persistence, and direct child drafts are rejected.

## Outputs

- Pack class, immutable version/checksum, dependency graph, evidence/license index, audit findings, activation state, and receipts.

## Blocking conditions

- Missing permission; stale draft; checksum/dependency conflict; incompatible profile; mechanically indispensable data; unresolved source conflict; or unclear rights for the requested distribution.
- Portraits, optional cards, estimated sessions, presentation quality, and render failures are diagnostics unless mechanically required.

## Context reset and boundary

Refresh after activation, an explicitly planned version transition, checkout, restore, or notification. Use only Narrative MCP; never write archives, DB rows, core state, or a compatibility format directly. The Agent reviews meaning; MCP owns drafts, validation, immutable versions, activation, and authoritative state. The current facade has no generic Pack-migration action: make an administrator-approved snapshot/fork, apply explicit native state changes, and activate the new finalized version only after verification.

Every campaign-bound write carries `campaign_id`, trusted `principal_id`, `expected_revision`, `expected_branch_id`, and `idempotency_key`. Verify the returned `host_context_binding` before continuing.
