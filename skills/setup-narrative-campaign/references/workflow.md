# Campaign setup contract

## Inputs

- Desired campaign name, play mode, participants, roles, owned actors, safety preferences, and starting premise.
- Exact finalized profile identifier, version, checksum, and required Pack versions.
- Caller principal and authorization supplied by the trusted MCP transport.

## Evidence

- `server_capabilities`, current `exposure`, and a bounded `skill_query` result proving this Skill is available.
- Profile validation/finalization receipt and Pack manifests, dependencies, evidence, and license metadata.
- Explicit human choices for unresolved profile, authority, ownership, and activation decisions.

## Workflow

1. Call `server_capabilities`; use `skill_query` list/search/get_section rather than reading whole Skill files; search and set `exposure` for Lobby setup. Use only returned native tools.
2. Query existing campaigns before creating one. Resume rather than duplicate when an idempotent setup already exists; otherwise create with `campaign_setup`, preserve the returned binding, then open a campaign-bound Lobby exposure.
3. Query campaign-scoped profile and Pack drafts/releases with `narrative_query`. If the requested finalized profile does not exist, switch to `build-game-profile`; if a required finalized Pack does not exist, switch to `author-audit-content-pack`. Complete evidence review and explicit finalization there, then query the released versions again. Absence is neither permission to invent a release nor a permanent blocker. Do not bind drafts or infer compatibility.
5. Grant, update, or revoke membership, roles, actor ownership, and optional element stewardship with `access_change`. Preserve at least one owner and never trust an unbound caller-supplied principal.
6. Bind the exact profile with `profile_change` action `activate` and activate exact Pack versions with `pack_change` action `activate`.
7. Create or update initial actors with `actor_change`; apply a finalized Campaign Seed Pack for declared logical principals, memberships, actor/element grants, records, and initial ActorKnowledge. Verify logical-to-authoritative actor bindings and materialized grant counts.
8. Run authoritative readiness validation. Separate blockers from diagnostics.
9. If the caller has campaign-administrator authority, create a baseline snapshot with `snapshot_change` action `create` when requested; then transition with `game_phase`.
10. Reconcile exposure and re-query the campaign before reporting success.

## Outputs

- Campaign ID, branch, revision, phase, context binding, profile and Pack locks.
- Membership, role, actor-grant and stewardship summary.
- Validation blockers, non-blocking diagnostics, and setup receipts.

## Blocking conditions

- Missing owner permission or trusted transport identity.
- Unresolved player choice needed for authoritative state.
- Draft/incompatible profile or Pack, checksum/dependency conflict, or indispensable schema data.
- Stale branch/revision that must be refreshed.

## Context reset

Discard tool schemas and cached context after campaign creation or selection, phase/profile/Pack changes, role or actor-grant changes, branch operations, restore, any advertised revision recovery, or `tools/list_changed`.

Every campaign write carries `campaign_id`, trusted `principal_id`, `expected_revision`, and `idempotency_key`; branch-sensitive writes also carry `expected_branch_id`. Verify the returned `host_context_binding` fields: campaign, branch, campaign revision, phase, profile ID/version/checksum, principal, role, and exposure revision.

## Boundary

Use Narrative MCP only. Do not access a CLI, database, filesystem state, core service, or compatibility fallback. The Agent recommends meanings; MCP owns state, authorization, revisions, random streams, and settlement.
