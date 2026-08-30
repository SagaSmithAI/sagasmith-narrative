# Architecture and authority

## Product boundary

`SagaSmith-narrative-mcp` is the authoritative, system-neutral long-campaign
layer. It reuses `sagasmith-core` without changing it. Core supplies durable
campaigns, principals and grants, documents, branch-aware continuity ledgers,
events, snapshots, revisions, and database transactions. This package owns all
new narrative contracts and coordinates them at the MCP boundary.

The MCP owns:

- campaign phase, deterministic tool catalog, and Host-selected model exposure;
- trusted transport identity, role, actor, and element authorization;
- campaign and record revisions, branch checks, idempotency, and random-stream
  receipts;
- immutable profile and Pack versions, activation, dependency checks, and seed
  materialization;
- campaign membership/actor/element grant and revoke operations, last-owner
  protection, and actor-sheet updates under profile-aware authority;
- typed narrative records, scenes, conflicts, downtime, world turns, facts,
  actor knowledge, events, and atomic settlement;
- audience-filtered four-track actor-memory retrieval for identity,
  motivational, semantic, and episodic context;
- facilitator-private fronts, plot threads, clues, and character arcs, plus
  signed off-Atlas expansion into lineage-linked child episode Packs;
- isolated NPC conversation journals and selected-proposal settlement;
- snapshot and branch recovery with refreshed host context.

The Agent and Skills own semantic work: source interpretation, audience choice,
fictional positioning, NPC response selection, consequence selection, and prose.
The MCP validates explicit decisions; it does not infer them.

Game-, setting-, module-, and campaign-specific truth stays in finalized
profiles and Packs. A deterministic mechanic may remain Level 1 only when it
fits one of the bounded declared forms. A game needs a separate system package
when it requires reusable state transitions, interacting subsystems, a custom
resolution graph, or canonical schema validation that cannot be expressed by
those forms without prose interpretation.

## Runtime lifecycle

`lobby` exposes campaign administration, profiles, Packs, actors, grants, and
recovery. `play` exposes scenes, narrative state, continuity, dialogue,
downtime, and world evolution. `conflict` exists only when the active profile
declares it and an authorized principal starts an encounter. Ending the
encounter returns to `play`.

For MCP 2026-07-28 the native tool catalog is complete, sorted, and privately
cacheable. The Host computes the model-visible subset from phase, active profile
capabilities, campaign membership, actor or element authority, and current
conflict ownership. Every call repeats the same checks. Legacy connection
exposure may emit `tools/list_changed`, but it is only a compatibility adapter.
The Host uses the returned `host_context_binding` before the next write.

Every campaign write includes `campaign_id`, `expected_revision`,
`expected_branch_id`, and `idempotency_key`. Exact retries return the original
receipt. A reused key with a different payload, a stale revision, or a stale
branch fails without a partial state change. Multi-ledger narrative settlement
uses one MCP-owned database transaction and conditional campaign update.

## Audience and narrative authority

Canonical record audiences are `table`, `public`, `group`, `actor`,
`facilitator`, and `private_worker`. The Agent supplies the audience; MCP checks
its shape and projects reads. Actor knowledge is not a public fact. A principal
must control the target actor to write its knowledge unless the active profile
grants facilitator authority.

Administrative ownership and narrative facilitation are separate. In a profile
with `facilitator_roles: []`, an owner can manage membership, profiles, Packs,
and recovery but cannot read private narrative state or author objective facts
without actor or element authority. Element grants may be scoped to a scene and
can rotate without creating a hidden permanent GM.

Campaign design is part of that private narrative state. Reading or advancing
fronts, threads, clues, and character arcs requires active facilitator authority
in addition to the administrative write gate. Expansion proposals are
zero-tool, bounded recommendations: the MCP signs their parent checksum,
lineage, and evidence envelope, while a later authoritative Pack lifecycle
performs the actual state change.

That separation also applies to actor reads, ActorKnowledge writes, NPC
activation, event participants, and scene lifecycle. Scene start/update/end
requires facilitator authority or a declared active steward whose current
element grants match the scene.

Persistent NPC dialogue is bound to its owning principal, NPC actor grant, and
private worker identifier. Raw proposals remain private. A close operation may
select proposals and atomically commit the approved public event and state
deltas. Any intervening authoritative mutation makes the conversation stale;
the caller must close or abort rather than reconstruct private context in the
Director.

## Recovery contract

`state_revision` is an audit listing surface. It does not advertise generic
undo/redo because core revision documents do not cover every narrative side
ledger as one reversible unit. Authoritative recovery uses administrator-only
snapshots and branches: inspect, restore into a recoverable branch, refresh
exposure and host binding, then prove the next legal native call. Snapshot
creation participates in campaign CAS and advances the campaign revision. Any
open NPC conversation must first be closed or aborted; recovery never carries a
live private-worker context across an authoritative checkout.
Skills must never simulate a compatibility undo path.

Snapshot storage is a Core-owned implementation detail. The current schema
stores every full state document as one bounded `zlib-1` record with both
document and record checksums. Narrative reads and recovery always cross Core's
single materialization boundary; they neither inspect storage columns nor walk
parents to reconstruct state. Server startup applies the single current Core
Alembic head and requires the current snapshot schema. Deployment requires a
consistent database backup; rollback restores that database with its matching
runtime as one unit.

## Deployment boundary

Unbound stdio is a trusted, single-user local mode. Client-supplied principal
fields are not authentication. Hosted HTTP requires a short-lived signed v2
delegation targeted specifically at `sagasmith-narrative-mcp` on every request.
Browser tokens and tokens for another audience are never accepted by
passthrough. A process-bound principal remains available for legacy local kits.
