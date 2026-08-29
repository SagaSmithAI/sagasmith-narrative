# Narrative project generation workflow

## Plan the artifact set

Record the requested project identity, source and distribution rights, intended
play modes, authority model, actor schema, declarative mechanics, Pack classes,
dependencies, scenes, cast, locations, clues, routes, continuity seeds, assets,
and endings. Decide which artifacts are required:

- a declarative game profile for actor/authority/mechanics contracts;
- Content Packs for reusable setting facts and licensed assets;
- Module Packs for runnable scene structures;
- Campaign Seed Packs for portable initial logical state.

For a runnable long-form project, select a runtime mode deliberately. An
authored root may be played as written or extended through child episodes; an
emergent seed starts from a bounded setting and initial scenes. In both cases,
put schema-version-1 runtime metadata in `content.runtime_manifest`, require one
generation-zero root, and model fronts, threads, clues, and character arcs with
evidence references. Player-character arcs describe opportunities rather than
fixed outcomes.

Never treat a live campaign backup as a distributable Pack.

## Build the profile first

1. Confirm Lobby authoring support in the host-selected stable catalog with `server_capabilities`; use `exposure` as current guidance only.
2. Create or resume an MCP-owned profile draft through `profile_change`.
3. Declare identity, actor schema, authority/audience rules, record extensions,
   and only supported Level 0/1 mechanics. Level 1 supports bounded dice pools,
   tables, track deltas, and resource deltas.
4. Attach reproducible source evidence and semantic decision notes. Validate
   with representative examples and edge cases.
5. Escalate to an independent system package when the declarative surface
   cannot define the mechanic without hidden behavior.
6. Finalize the reviewed current revision. Activation is separate.

## Build Packs against the exact profile

1. Bind each Pack draft to the exact finalized profile identity/version and
   dependency locks.
2. Create or resume the draft with `pack_change`; run the mechanical first pass.
3. Audit identity, compatibility, source provenance, licenses, private content,
   scenes, cast, continuity seeds, assets, profile extensions, and endings.
4. Repair all in-scope draft fields through MCP. Store source-specific rulings
   and evidence with the draft, then revalidate until only advisory diagnostics
   remain.
5. Confirm the distribution decision independently from private-use validity.
6. Finalize explicitly. Import inactive and activate only when separately
   authorized against fresh authoritative context.
7. Do not pre-author every possible detour. During play, reasonable off-Atlas
   and emergent additions are proposed from a signed `campaign_expansion`
   context by a bounded zero-tool worker, validated and attested by MCP, and
   persisted only through a new child Pack whose checksummed parent dependency
   and unchanged manifest pass create/finalize/import/activate verification.

## Write discipline and recovery

Every campaign-bound write uses the current `campaign_id`, trusted principal,
`expected_revision`, `expected_branch_id`, and a request-specific
`idempotency_key`. Verify returned `host_context_binding` before continuing.
After activation, branch/restore, or exposure notification, discard cached
context, refresh native tools, and reread authoritative state.

Block only for missing permission, stale authoritative state, unresolved human
choice, conflicting/missing indispensable evidence, undefined mechanics, or
unclear rights for the requested distribution. Optional presentation fields,
portraits, and rendering remain diagnostics.
