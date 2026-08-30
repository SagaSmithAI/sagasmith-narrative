from __future__ import annotations

import json
from pathlib import Path

import pytest
from sagasmith_core.database import Database, sqlite_database_url
from sagasmith_core.models import Campaign
from sagasmith_narrative.contracts import narrative_document, state_with_narrative

from sagasmith_narrative_mcp.actor_memory import select_actor_memory_context
from sagasmith_narrative_mcp.runtime import NarrativeRuntime


def runtime(path: Path) -> NarrativeRuntime:
    database = Database(sqlite_database_url(path))
    database.create_schema()
    return NarrativeRuntime(database)


def state(rt: NarrativeRuntime, campaign_id: str) -> tuple[int, str]:
    return rt.campaigns.get(campaign_id).revision, rt.branch_id(campaign_id)


def setup_campaign(rt: NarrativeRuntime, *, facilitator_roles: list[str] | None = None) -> str:
    campaign_id = rt.campaign_create(
        name="Memory playthrough", principal_id="owner", idempotency_key="campaign"
    )["id"]
    profile = {
        "id": "profile.memory",
        "version": "1",
        "mechanics_level": 0,
        "capabilities": ["npc_conversation"],
        "authority": {
            "facilitator_roles": facilitator_roles or ["owner", "dm"],
            "audience_scopes": [
                "table",
                "public",
                "group",
                "actor",
                "facilitator",
                "private_worker",
            ],
        },
        "sources": [{"type": "self-authored", "citation": __file__}],
    }
    revision, branch_id = state(rt, campaign_id)
    rt.profile_change(
        campaign_id,
        action="create_draft",
        profile=profile,
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="profile-draft",
    )
    revision, branch_id = state(rt, campaign_id)
    rt.profile_change(
        campaign_id,
        action="finalize",
        profile_key="profile.memory@1",
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="profile-finalize",
    )
    revision, branch_id = state(rt, campaign_id)
    rt.profile_change(
        campaign_id,
        action="activate",
        profile_key="profile.memory@1",
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="profile-activate",
    )
    return campaign_id


def enter_play(rt: NarrativeRuntime, campaign_id: str) -> None:
    revision, branch_id = state(rt, campaign_id)
    rt.set_phase(
        campaign_id,
        phase="play",
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="play",
    )


def actor(
    rt: NarrativeRuntime,
    campaign_id: str,
    *,
    name: str = "Ilyra",
    character_type: str = "npc",
    idempotency_key: str = "actor",
) -> dict:
    revision, branch_id = state(rt, campaign_id)
    return rt.actor_create(
        campaign_id,
        actor={
            "name": name,
            "type": character_type,
            "summary": "A watch captain who protects the old quarter.",
            "sheet": {"goal": "Keep the floodgate closed."},
        },
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key=idempotency_key,
    )


def test_actor_memory_reserves_each_track_without_exceeding_strict_budget() -> None:
    actor_state = {
        "id": "actor.guardian",
        "name": "The river guardian with a deliberately long identity memory",
        "summary": "Remembers the shape of every old bridge in the valley.",
        "state_facts": [
            {
                "id": "goal.guardian",
                "kind": "goal",
                "content": "Keep the last bridge open until every traveler is safe.",
            }
        ],
    }
    knowledge = [
        {
            "id": "knowledge.tide",
            "proposition": "The black tide arrives whenever the third bell sounds.",
        }
    ]
    events = [
        {
            "id": f"event.{index:03d}",
            "summary": f"Urgent bell memory {index}",
            "retrieval_text": f"urgent bell memory {index}",
            "importance": 5,
            "confidence": 5,
            "sequence": 10_000 + index,
        }
        for index in range(80)
    ]
    complete = select_actor_memory_context(
        actor_state=actor_state,
        actor_knowledge=knowledge,
        events=events,
        query="urgent bell",
        budget_chars=100_000,
    )

    def cost(item: dict) -> int:
        return len(
            json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        )

    floor_budget = sum(
        cost(complete[track][0])
        for track in ("identity", "motivational", "semantic", "episodic")
    )
    balanced = select_actor_memory_context(
        actor_state=actor_state,
        actor_knowledge=knowledge,
        events=events,
        query="urgent bell",
        budget_chars=floor_budget,
    )
    assert all(
        len(balanced[track]) >= 1
        for track in ("identity", "motivational", "semantic", "episodic")
    )
    assert balanced["diagnostics"]["used_chars"] <= floor_budget

    tiny = select_actor_memory_context(
        actor_state=actor_state,
        actor_knowledge=knowledge,
        events=events,
        query="urgent bell",
        budget_chars=1,
    )
    assert tiny["diagnostics"]["used_chars"] <= 1
    assert tiny["diagnostics"]["remaining_chars"] >= 0


def test_actor_memory_current_refs_rank_exact_context_and_deduplicate_revisions() -> None:
    memory = select_actor_memory_context(
        actor_state={"id": "actor.one", "name": "One"},
        actor_knowledge=[
            {
                "id": "knowledge.old",
                "revision": 1,
                "proposition": "The repeated proposition.",
            },
            {
                "id": "knowledge.new",
                "revision": 2,
                "proposition": "The repeated proposition.",
            },
            {
                "id": "knowledge.focus",
                "revision": 1,
                "proposition": "A quiet contextual fact.",
                "metadata": {"scene_id": "scene.focus"},
            },
            {
                "id": "knowledge.loud",
                "revision": 99,
                "importance": 5,
                "proposition": "A loud but unrelated fact.",
            },
        ],
        events=[],
        current_refs=["scene:scene.focus"],
        budget_chars=50_000,
    )
    semantic_refs = [item["basis_ref"] for item in memory["semantic"]]
    assert semantic_refs[0] == "knowledge:knowledge.focus"
    assert "knowledge:knowledge.new" in semantic_refs
    assert "knowledge:knowledge.old" not in semantic_refs
    assert memory["diagnostics"]["deduplicated_count"] < memory["diagnostics"][
        "candidate_count"
    ]

    direct = select_actor_memory_context(
        actor_state={"id": "actor.one", "name": "One"},
        actor_knowledge=[
            {"id": "knowledge.focus", "proposition": "Quiet memory."},
            {"id": "knowledge.loud", "proposition": "Loud memory.", "importance": 5},
        ],
        events=[
            {"id": "event.focus", "summary": "Quiet event."},
            {"id": "event.loud", "summary": "Loud event.", "importance": 5},
        ],
        current_refs=["knowledge:knowledge.focus", "event:event.focus"],
        budget_chars=50_000,
    )
    assert direct["semantic"][0]["basis_ref"] == "knowledge:knowledge.focus"
    assert direct["episodic"][0]["basis_ref"] == "event:event.focus"


def test_actor_memory_filters_core_scopes_and_narrative_audiences_before_diagnostics(
    tmp_path: Path,
) -> None:
    rt = runtime(tmp_path / "audience.db")
    campaign_id = setup_campaign(rt, facilitator_roles=["dm"])
    npc = actor(rt, campaign_id, character_type="persistent_npc")
    for principal, role in (
        ("dm", "dm"),
        ("actor.owner", "player"),
        ("group.member", "player"),
        ("group.outsider", "player"),
        ("rotating.steward", "player"),
    ):
        rt.access.ensure_principal(principal)
        rt.access.grant_campaign(campaign_id, principal, role=role)
    for principal in ("owner", "dm", "actor.owner", "group.member", "group.outsider"):
        rt.access.grant_actor(
            campaign_id,
            principal,
            npc["id"],
            can_control=principal in {"owner", "dm", "actor.owner"},
            can_view_private=True,
        )
    rt.knowledge.add(
        campaign_id,
        actor_id=npc["id"],
        knowledge_key="owner-secret",
        proposition="The captain owns the brass cipher.",
        disclosure_scope="owner",
    )
    rt.knowledge.add(
        campaign_id,
        actor_id=npc["id"],
        knowledge_key="facilitator-secret",
        proposition="The captain caused the first flood.",
        disclosure_scope="dm",
    )
    records = [
        (
            "record.facilitator",
            {"scope": "facilitator"},
            "Only the facilitator sees this motive.",
        ),
        (
            "record.actor",
            {"scope": "actor", "actor_id": npc["id"]},
            "The captain remembers an old promise.",
        ),
        (
            "record.group",
            {"scope": "group", "principal_ids": ["group.member"]},
            "The group shares a coded signal.",
        ),
    ]
    for index, (record_id, audience, summary) in enumerate(records):
        revision, branch_id = state(rt, campaign_id)
        rt.record_change(
            campaign_id,
            action="create",
            record={
                "id": record_id,
                "kind": "relationship",
                "title": record_id,
                "audience": audience,
                "controller": {"actor_id": npc["id"]},
                "data": {"summary": summary},
            },
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key=f"record-{index}",
        )

    def basis(principal: str) -> tuple[set[str], int]:
        memory = rt.actor_memory_context(
            campaign_id,
            actor_id=npc["id"],
            principal_id=principal,
            current_refs=[item[0] for item in records],
            budget_chars=50_000,
        )["memory"]
        return (
            {
                item["basis_ref"]
                for track in ("identity", "motivational", "semantic", "episodic")
                for item in memory[track]
            },
            memory["diagnostics"]["candidate_count"],
        )

    dm_basis, dm_count = basis("dm")
    owner_basis, owner_count = basis("actor.owner")
    member_basis, member_count = basis("group.member")
    outsider_basis, outsider_count = basis("group.outsider")
    assert any("record.facilitator" in item for item in dm_basis)
    assert not any(
        "record.facilitator" in item
        for item in owner_basis | member_basis | outsider_basis
    )
    assert any("record.actor" in item for item in owner_basis)
    assert any("record.group" in item for item in member_basis)
    assert not any("record.group" in item for item in outsider_basis)
    assert dm_count > member_count > outsider_count
    assert owner_count < dm_count
    with pytest.raises(PermissionError, match="actor memory"):
        rt.actor_memory_context(
            campaign_id,
            actor_id=npc["id"],
            principal_id="rotating.steward",
            current_refs=["record.group"],
        )


def test_actor_memory_searches_old_branch_events_and_is_branch_isolated(tmp_path: Path) -> None:
    rt = runtime(tmp_path / "old-events.db")
    campaign_id = setup_campaign(rt)
    npc = actor(rt, campaign_id)
    main_branch = rt.branch_id(campaign_id)
    old = rt.events.add(
        campaign_id,
        event_type="old.promise",
        summary="Ilyra hid the moonstone under the western bell.",
        retrieval_text="moonstone western bell unique-old-memory",
        audience_scope="public",
        participants=[{"actor_id": npc["id"], "role": "speaker"}],
        branch_id=main_branch,
    )
    for index in range(205):
        rt.events.add(
            campaign_id,
            event_type="routine",
            summary=f"Routine watch event {index}",
            audience_scope="public",
            participants=[{"actor_id": npc["id"], "role": "witness"}],
            branch_id=main_branch,
        )
    memory = rt.actor_memory_context(
        campaign_id,
        actor_id=npc["id"],
        principal_id="owner",
        query="unique-old-memory",
        budget_chars=50_000,
    )["memory"]
    assert any(item["record"]["id"] == old.id for item in memory["episodic"])
    revision, branch_id = state(rt, campaign_id)
    rt.snapshot_create(
        campaign_id,
        label="branch point",
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="snapshot-branch-point",
    )
    revision, branch_id = state(rt, campaign_id)
    child = rt.branches.create(
        campaign_id,
        name="what-if",
        checkout=False,
        expected_revision=revision,
        expected_branch_id=branch_id,
    )
    child_only = rt.events.add(
        campaign_id,
        event_type="branch.secret",
        summary="Only the child branch remembers the red lantern.",
        retrieval_text="red-lantern-child-only",
        audience_scope="public",
        participants=[{"actor_id": npc["id"], "role": "speaker"}],
        branch_id=child.id,
    )
    main_memory = rt.actor_memory_context(
        campaign_id,
        actor_id=npc["id"],
        principal_id="owner",
        current_refs=[f"event:{child_only.id}"],
        budget_chars=50_000,
    )["memory"]
    assert not any(
        item["record"]["id"] == child_only.id for item in main_memory["episodic"]
    )
    historical = rt.actor_memory_context(
        campaign_id,
        actor_id=npc["id"],
        principal_id="owner",
        branch_id=child.id,
        current_refs=[f"event:{child_only.id}"],
        budget_chars=50_000,
    )["memory"]
    assert any(item["record"]["id"] == child_only.id for item in historical["episodic"])


def test_actor_memory_hides_dm_event_from_actor_owner_but_shows_facilitator(
    tmp_path: Path,
) -> None:
    rt = runtime(tmp_path / "dm-event.db")
    campaign_id = setup_campaign(rt, facilitator_roles=["dm"])
    player_actor = actor(rt, campaign_id)
    for principal, role in (("dm", "dm"), ("actor.owner", "player")):
        rt.access.ensure_principal(principal)
        rt.access.grant_campaign(campaign_id, principal, role=role)
        rt.access.grant_actor(
            campaign_id,
            principal,
            player_actor["id"],
            can_control=principal == "actor.owner",
            can_view_private=True,
        )
    branch_id = rt.branch_id(campaign_id)
    dm_only = rt.events.add(
        campaign_id,
        event_type="hidden.revelation",
        summary="The masked patron secretly paid the ferryman.",
        retrieval_text="masked patron ferryman dm-only-revelation",
        audience_scope="dm",
        participants=[{"actor_id": player_actor["id"], "role": "witness"}],
        branch_id=branch_id,
    )

    def recalled(principal: str) -> dict:
        return rt.actor_memory_context(
            campaign_id,
            actor_id=player_actor["id"],
            principal_id=principal,
            query="dm-only-revelation",
            budget_chars=50_000,
        )["memory"]

    player_memory = recalled("actor.owner")
    facilitator_memory = recalled("dm")
    assert dm_only.id not in str(player_memory)
    assert "masked patron" not in str(player_memory).lower()
    assert any(
        item["record"]["id"] == dm_only.id
        for item in facilitator_memory["episodic"]
    )
    assert facilitator_memory["diagnostics"]["candidate_count"] > player_memory[
        "diagnostics"
    ]["candidate_count"]


def test_pc_memory_respects_view_only_nonprivate_grant_and_pc_worker_is_rejected(
    tmp_path: Path,
) -> None:
    rt = runtime(tmp_path / "pc-memory.db")
    campaign_id = setup_campaign(rt)
    revision, branch_id = state(rt, campaign_id)
    pc = rt.actor_create(
        campaign_id,
        actor={
            "name": "Player hero",
            "type": "pc",
            "summary": "A hero with a private oath.",
            "sheet": {"secret_oath": "Never cross the black bridge."},
        },
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="pc",
    )
    rt.access.ensure_principal("viewer")
    rt.access.grant_campaign(campaign_id, "viewer", role="player")
    rt.access.grant_actor(
        campaign_id,
        "viewer",
        pc["id"],
        can_control=False,
        can_view_private=False,
    )
    rt.knowledge.add(
        campaign_id,
        actor_id=pc["id"],
        knowledge_key="owner-only",
        proposition="The hero privately owns the obsidian seal.",
        disclosure_scope="owner",
    )
    rt.knowledge.add(
        campaign_id,
        actor_id=pc["id"],
        knowledge_key="public-memory",
        proposition="The hero crossed the market at dawn.",
        disclosure_scope="public",
    )
    memory = rt.actor_memory_context(
        campaign_id,
        actor_id=pc["id"],
        principal_id="viewer",
        query="hero",
        budget_chars=50_000,
    )
    assert "market at dawn" in str(memory)
    assert "obsidian seal" not in str(memory)
    assert "secret_oath" not in str(memory)

    enter_play(rt, campaign_id)
    revision, branch_id = state(rt, campaign_id)
    with pytest.raises(ValueError, match="NPC-only"):
        rt.npc_conversation(
            campaign_id,
            action="open",
            npc_actor_id=pc["id"],
            data={
                "interlocutors": {
                    "principal_ids": ["owner"],
                    "publication_scopes": ["public"],
                }
            },
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="pc-worker",
        )


def test_ordinary_continuity_cannot_read_sibling_or_historical_branch(
    tmp_path: Path,
) -> None:
    rt = runtime(tmp_path / "continuity-branch.db")
    campaign_id = setup_campaign(rt)
    rt.access.ensure_principal("player")
    rt.access.grant_campaign(campaign_id, "player", role="player")
    revision, branch_id = state(rt, campaign_id)
    rt.snapshot_create(
        campaign_id,
        label="branch point",
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="continuity-snapshot",
    )
    revision, branch_id = state(rt, campaign_id)
    sibling = rt.branches.create(
        campaign_id,
        name="private sibling",
        checkout=False,
        expected_revision=revision,
        expected_branch_id=branch_id,
    )
    with pytest.raises(PermissionError, match="historical branch"):
        rt.continuity_context(
            campaign_id,
            principal_id="player",
            actor_id=None,
            audience="player",
            limit=50,
            query="",
            branch_id=sibling.id,
        )
    admin = rt.continuity_context(
        campaign_id,
        principal_id="owner",
        actor_id=None,
        audience="dm",
        limit=50,
        query="",
        branch_id=sibling.id,
    )
    assert isinstance(admin, dict)


def test_conversation_requires_private_control_and_enforces_participant_boundary(
    tmp_path: Path,
) -> None:
    rt = runtime(tmp_path / "conversation-participants.db")
    campaign_id = setup_campaign(rt)
    npc = actor(rt, campaign_id)
    revision, branch_id = state(rt, campaign_id)
    pc = rt.actor_create(
        campaign_id,
        actor={"name": "Listener", "type": "pc"},
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="listener-pc",
    )
    rt.access.ensure_principal("controller")
    rt.access.grant_campaign(campaign_id, "controller", role="player")
    rt.access.grant_actor(
        campaign_id,
        "controller",
        npc["id"],
        can_control=True,
        can_view_private=False,
    )
    enter_play(rt, campaign_id)
    revision, branch_id = state(rt, campaign_id)
    with pytest.raises(PermissionError, match="control and private"):
        rt.npc_conversation(
            campaign_id,
            action="open",
            npc_actor_id=npc["id"],
            data={
                "interlocutors": {
                    "principal_ids": ["controller"],
                    "publication_scopes": ["public"],
                }
            },
            principal_id="controller",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="no-private-worker",
        )

    opened = rt.npc_conversation(
        campaign_id,
        action="open",
        npc_actor_id=npc["id"],
        data={
            "interlocutors": {
                "actor_ids": [pc["id"]],
                "principal_ids": ["owner"],
                "publication_scopes": ["actor"],
            }
        },
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="bounded-open",
    )
    revision, branch_id = state(rt, campaign_id)
    claimed = rt.npc_conversation(
        campaign_id,
        action="claim",
        conversation_id=opened["conversation"]["id"],
        data={"activation_ref": opened["activation"]["activation_ref"]},
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="bounded-claim",
    )

    def proposal_data(*, target: str, receipt: dict) -> dict:
        return {
            "activation_ref": claimed["activation_ref"],
            "lease_id": claimed["lease_id"],
            "context_receipt": receipt,
            "proposal": {
                "schema_version": 1,
                "activation_id": claimed["activation_id"],
                "actor_runtime_id": claimed["actor_runtime_id"],
                "private_intent": "Answer only the declared listener.",
                "utterance_segments": [
                    {
                        "text": "I heard you.",
                        "content_mode": "nonfactual",
                        "basis_refs": [],
                        "targets": [target],
                    }
                ],
            },
        }

    revision, branch_id = state(rt, campaign_id)
    with pytest.raises(PermissionError, match="undeclared interlocutors"):
        rt.npc_conversation(
            campaign_id,
            action="propose",
            conversation_id=opened["conversation"]["id"],
            data=proposal_data(target="actor.not-present", receipt=claimed["context_receipt"]),
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="bad-target",
        )
    tampered = dict(claimed["context_receipt"])
    tampered["context_digest"] = "0" * 64
    with pytest.raises(PermissionError, match="receipt is invalid"):
        rt.npc_conversation(
            campaign_id,
            action="propose",
            conversation_id=opened["conversation"]["id"],
            data=proposal_data(target=pc["id"], receipt=tampered),
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="bad-receipt",
        )
    proposed = rt.npc_conversation(
        campaign_id,
        action="propose",
        conversation_id=opened["conversation"]["id"],
        data=proposal_data(target=pc["id"], receipt=claimed["context_receipt"]),
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="bounded-proposal",
    )
    revision, branch_id = state(rt, campaign_id)
    with pytest.raises(PermissionError, match="undeclared interlocutors"):
        rt.npc_conversation(
            campaign_id,
            action="publish",
            conversation_id=opened["conversation"]["id"],
            data={
                "proposal_id": proposed["proposal_id"],
                "audience": {"scope": "actor", "actor_id": "actor.not-present"},
            },
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="bad-publication-target",
        )


def test_every_open_conversation_action_rejects_phase_drift(tmp_path: Path) -> None:
    rt = runtime(tmp_path / "conversation-phase-drift.db")
    campaign_id = setup_campaign(rt)
    npc = actor(rt, campaign_id)
    enter_play(rt, campaign_id)
    revision, branch_id = state(rt, campaign_id)
    opened = rt.npc_conversation(
        campaign_id,
        action="open",
        npc_actor_id=npc["id"],
        data={
            "interlocutors": {
                "principal_ids": ["owner"],
                "publication_scopes": ["public"],
            }
        },
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="phase-drift-open",
    )
    with rt.database.transaction() as session:
        campaign = session.get(Campaign, campaign_id)
        assert campaign is not None
        document = narrative_document(campaign.state)
        document["phase"] = "lobby"
        campaign.state = state_with_narrative(campaign.state, document)

    revision, branch_id = state(rt, campaign_id)
    for action in ("claim", "refresh", "propose", "publish", "close"):
        with pytest.raises(ValueError, match="non-conflict Play"):
            rt.npc_conversation(
                campaign_id,
                action=action,
                conversation_id=opened["conversation"]["id"],
                data={},
                principal_id="owner",
                expected_revision=revision,
                expected_branch_id=branch_id,
                idempotency_key=f"phase-drift-{action}",
            )


def test_persistent_zero_tool_conversation_refreshes_actor_local_activation(tmp_path: Path) -> None:
    rt = runtime(tmp_path / "conversation.db")
    campaign_id = setup_campaign(rt)
    npc = actor(rt, campaign_id)
    rt.knowledge.add(
        campaign_id,
        actor_id=npc["id"],
        knowledge_key="bell-secret",
        proposition="The bell rope was cut from inside.",
        disclosure_scope="owner",
    )
    enter_play(rt, campaign_id)
    revision, branch_id = state(rt, campaign_id)
    opened = rt.npc_conversation(
        campaign_id,
        action="open",
        npc_actor_id=npc["id"],
        data={
            "reason": "the player asks about the bell",
            "interlocutors": {
                "principal_ids": ["owner"],
                "publication_scopes": ["public"],
            },
        },
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="open",
    )
    first_ref = opened["activation"]["activation_ref"]
    revision, branch_id = state(rt, campaign_id)
    first = rt.npc_conversation(
        campaign_id,
        action="claim",
        conversation_id=opened["conversation"]["id"],
        data={"activation_ref": first_ref},
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="claim-first",
    )
    assert first["constraints"] == {
        "allowed_basis_refs": first["constraints"]["allowed_basis_refs"],
        "may_call_tools": False,
        "may_roll_random": False,
        "may_write_state": False,
        "output_contract": "character-conversation-proposal.v1",
    }
    basis_ref = next(
        item for item in first["constraints"]["allowed_basis_refs"] if item.startswith("knowledge:")
    )
    revision, branch_id = state(rt, campaign_id)
    refreshed = rt.npc_conversation(
        campaign_id,
        action="refresh",
        conversation_id=opened["conversation"]["id"],
        data={},
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="refresh",
    )
    replacement = refreshed["activation"]
    assert replacement["replacement_for"] == first_ref
    assert replacement["reason"] == opened["activation"]["reason"]
    assert replacement["from_cursor"] == opened["activation"]["from_cursor"]
    revision, branch_id = state(rt, campaign_id)
    with pytest.raises((PermissionError, ValueError), match="invalid|invalidated|replacement"):
        rt.npc_conversation(
            campaign_id,
            action="claim",
            conversation_id=opened["conversation"]["id"],
            data={"activation_ref": first_ref},
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="claim-invalidated",
        )
    claimed = rt.npc_conversation(
        campaign_id,
        action="claim",
        conversation_id=opened["conversation"]["id"],
        data={"activation_ref": replacement["activation_ref"]},
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="claim-replacement",
    )
    revision, branch_id = state(rt, campaign_id)
    with pytest.raises(ValueError, match="outside"):
        rt.npc_conversation(
            campaign_id,
            action="propose",
            conversation_id=opened["conversation"]["id"],
            data={
                "activation_ref": claimed["activation_ref"],
                "lease_id": claimed["lease_id"],
                "context_receipt": claimed["context_receipt"],
                "proposal": {
                    "schema_version": 1,
                    "activation_id": claimed["activation_id"],
                    "actor_runtime_id": claimed["actor_runtime_id"],
                    "private_intent": "Conceal responsibility.",
                    "utterance_segments": [
                        {
                            "text": "The rope looked weathered.",
                            "content_mode": "deception",
                            "basis_refs": ["knowledge:forged"],
                        }
                    ],
                },
            },
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="bad-basis",
        )
    proposal = rt.npc_conversation(
        campaign_id,
        action="propose",
        conversation_id=opened["conversation"]["id"],
        data={
            "activation_ref": claimed["activation_ref"],
            "lease_id": claimed["lease_id"],
            "context_receipt": claimed["context_receipt"],
            "proposal": {
                "schema_version": 1,
                "activation_id": claimed["activation_id"],
                "actor_runtime_id": claimed["actor_runtime_id"],
                "private_intent": "Conceal responsibility.",
                "utterance_segments": [
                    {
                        "text": "I cannot be certain who cut it.",
                        "content_mode": "uncertain",
                        "basis_refs": [basis_ref],
                    },
                    {
                        "text": "The rope looked weathered.",
                        "content_mode": "deception",
                        "basis_refs": [basis_ref],
                    },
                    {
                        "text": "I hear your concern.",
                        "content_mode": "nonfactual",
                        "basis_refs": [],
                    },
                ],
            },
        },
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="proposal",
    )
    revision, branch_id = state(rt, campaign_id)
    publication = rt.npc_conversation(
        campaign_id,
        action="publish",
        conversation_id=opened["conversation"]["id"],
        data={"proposal_id": proposal["proposal_id"], "audience": {"scope": "public"}},
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="publish",
    )["publication"]
    assert "private_intent" not in publication
    assert "content_mode" not in str(publication)
    assert "basis_refs" not in str(publication)
    revision, branch_id = state(rt, campaign_id)
    closed = rt.npc_conversation(
        campaign_id,
        action="close",
        conversation_id=opened["conversation"]["id"],
        data={
            "close_token": opened["close_token"],
            "selected_proposal_ids": [proposal["proposal_id"]],
        },
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="close",
    )
    assert closed["status"] == "closed"
    assert "Conceal responsibility" not in str(closed)


def test_conversation_rejects_unbounded_memory_candidate_journal(tmp_path: Path) -> None:
    rt = runtime(tmp_path / "conversation-bounds.db")
    campaign_id = setup_campaign(rt)
    npc = actor(rt, campaign_id)
    enter_play(rt, campaign_id)
    revision, branch_id = state(rt, campaign_id)
    opened = rt.npc_conversation(
        campaign_id,
        action="open",
        npc_actor_id=npc["id"],
        data={
            "reason": "bounded interview",
            "interlocutors": {
                "principal_ids": ["owner"],
                "publication_scopes": ["public"],
            },
        },
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="open",
    )
    revision, branch_id = state(rt, campaign_id)
    claimed = rt.npc_conversation(
        campaign_id,
        action="claim",
        conversation_id=opened["conversation"]["id"],
        data={"activation_ref": opened["activation"]["activation_ref"]},
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="claim",
    )
    revision, branch_id = state(rt, campaign_id)
    with pytest.raises(ValueError, match="memory_candidates.*at most 32"):
        rt.npc_conversation(
            campaign_id,
            action="propose",
            conversation_id=opened["conversation"]["id"],
            data={
                "activation_ref": claimed["activation_ref"],
                "lease_id": claimed["lease_id"],
                "context_receipt": claimed["context_receipt"],
                "proposal": {
                    "schema_version": 1,
                    "activation_id": claimed["activation_id"],
                    "actor_runtime_id": claimed["actor_runtime_id"],
                    "private_intent": "Keep bounded private notes.",
                    "utterance_segments": [],
                    "memory_candidates": [{"summary": str(index)} for index in range(33)],
                },
            },
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="oversized-memory-candidates",
        )
    assert state(rt, campaign_id) == (revision, branch_id)
    with pytest.raises(ValueError, match="settlement.record_changes.*at most 128"):
        rt.npc_conversation(
            campaign_id,
            action="close",
            conversation_id=opened["conversation"]["id"],
            data={
                "close_token": opened["close_token"],
                "selected_proposal_ids": [],
                "settlement": {
                    "event": {
                        "event_type": "bounded.close",
                        "summary": "Reject an oversized nested settlement.",
                        "audience_scope": "public",
                    },
                    "record_changes": [{} for _ in range(129)],
                },
            },
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="oversized-nested-settlement",
        )
    assert state(rt, campaign_id) == (revision, branch_id)


def test_multiple_npcs_keep_distinct_persistent_workers_across_multiple_rounds(
    tmp_path: Path,
) -> None:
    rt = runtime(tmp_path / "multi-npc.db")
    campaign_id = setup_campaign(rt)
    first_actor = actor(rt, campaign_id)
    second_actor = actor(
        rt,
        campaign_id,
        name="Mara",
        idempotency_key="actor-mara",
    )
    enter_play(rt, campaign_id)

    def run_dialogue(npc: dict, label: str) -> tuple[str, str, list[str]]:
        revision, branch_id = state(rt, campaign_id)
        opened = rt.npc_conversation(
            campaign_id,
            action="open",
            npc_actor_id=npc["id"],
            data={
                "reason": f"interview {label}",
                "interlocutors": {
                    "principal_ids": ["owner"],
                    "publication_scopes": ["public"],
                },
            },
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key=f"{label}-open",
        )
        revision, branch_id = state(rt, campaign_id)
        claimed = rt.npc_conversation(
            campaign_id,
            action="claim",
            conversation_id=opened["conversation"]["id"],
            data={"activation_ref": opened["activation"]["activation_ref"]},
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key=f"{label}-claim",
        )
        proposal_ids = []
        for turn in range(2):
            revision, branch_id = state(rt, campaign_id)
            proposed = rt.npc_conversation(
                campaign_id,
                action="propose",
                conversation_id=opened["conversation"]["id"],
                data={
                    "activation_ref": claimed["activation_ref"],
                    "lease_id": claimed["lease_id"],
                    "context_receipt": claimed["context_receipt"],
                    "proposal": {
                        "schema_version": 1,
                        "activation_id": claimed["activation_id"],
                        "actor_runtime_id": claimed["actor_runtime_id"],
                        "private_intent": f"Answer round {turn + 1} consistently.",
                        "utterance_segments": [
                            {
                                "text": f"{label} answer {turn + 1}",
                                "content_mode": "nonfactual",
                                "basis_refs": [],
                            }
                        ],
                    },
                },
                principal_id="owner",
                expected_revision=revision,
                expected_branch_id=branch_id,
                idempotency_key=f"{label}-proposal-{turn}",
            )
            assert proposed["activation"]["status"] == "claimed"
            assert proposed["activation"]["to_cursor"] == turn + 1
            proposal_ids.append(proposed["proposal_id"])
        for turn, proposal_id in enumerate(proposal_ids):
            revision, branch_id = state(rt, campaign_id)
            rt.npc_conversation(
                campaign_id,
                action="publish",
                conversation_id=opened["conversation"]["id"],
                data={"proposal_id": proposal_id, "audience": {"scope": "public"}},
                principal_id="owner",
                expected_revision=revision,
                expected_branch_id=branch_id,
                idempotency_key=f"{label}-publish-{turn}",
            )
        revision, branch_id = state(rt, campaign_id)
        rt.npc_conversation(
            campaign_id,
            action="close",
            conversation_id=opened["conversation"]["id"],
            data={
                "close_token": opened["close_token"],
                "selected_proposal_ids": proposal_ids,
            },
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key=f"{label}-close",
        )
        return claimed["worker_id"], claimed["actor_runtime_id"], proposal_ids

    first_worker, first_runtime, first_proposals = run_dialogue(first_actor, "ilyra")
    second_worker, second_runtime, second_proposals = run_dialogue(second_actor, "mara")
    assert first_worker != second_worker
    assert first_runtime != second_runtime
    assert first_actor["id"] in first_runtime
    assert second_actor["id"] in second_runtime
    assert set(first_proposals).isdisjoint(second_proposals)


def test_fact_revision_authorizes_the_persisted_subject(tmp_path: Path) -> None:
    rt = runtime(tmp_path / "fact-target.db")
    campaign_id = setup_campaign(rt, facilitator_roles=["dm"])
    controlled = actor(rt, campaign_id, name="Controlled")
    protected = actor(rt, campaign_id, name="Protected", idempotency_key="protected")
    rt.access.ensure_principal("player")
    rt.access.grant_campaign(campaign_id, "player", role="player")
    rt.access.grant_actor(
        campaign_id,
        "player",
        controlled["id"],
        can_control=True,
        can_view_private=True,
    )
    protected_fact = rt.facts.add(
        campaign_id,
        fact_key="protected.fact",
        subject_ref=protected["id"],
        predicate="secret",
        content="Protected truth.",
        disclosure_scope="public",
    )
    enter_play(rt, campaign_id)
    revision, branch_id = state(rt, campaign_id)
    with pytest.raises(PermissionError, match="persisted target"):
        rt.narrative_settle(
            campaign_id,
            principal_id="player",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="forged-fact-revise",
            event={
                "event_type": "attempt",
                "summary": "Attempt a forged fact revision.",
                "audience_scope": "public",
                "participants": [{"actor_id": controlled["id"], "role": "speaker"}],
            },
            facts=[
                {
                    "action": "revise",
                    "memory_id": protected_fact.id,
                    "expected_revision_id": protected_fact.revision_id,
                    "subject_ref": controlled["id"],
                    "content": "Forged truth.",
                    "status": "active",
                    "disclosure_scope": "public",
                }
            ],
        )
    persisted = next(item for item in rt.facts.list(campaign_id) if item.id == protected_fact.id)
    assert persisted.content == "Protected truth."


def test_knowledge_revision_authorizes_the_persisted_actor(tmp_path: Path) -> None:
    rt = runtime(tmp_path / "knowledge-target.db")
    campaign_id = setup_campaign(rt, facilitator_roles=["dm"])
    controlled = actor(rt, campaign_id, name="Controlled")
    protected = actor(rt, campaign_id, name="Protected", idempotency_key="protected")
    rt.access.ensure_principal("player")
    rt.access.grant_campaign(campaign_id, "player", role="player")
    rt.access.grant_actor(
        campaign_id,
        "player",
        controlled["id"],
        can_control=True,
        can_view_private=True,
    )
    protected_knowledge = rt.knowledge.add(
        campaign_id,
        actor_id=protected["id"],
        knowledge_key="protected.knowledge",
        proposition="Protected belief.",
        disclosure_scope="owner",
    )
    enter_play(rt, campaign_id)
    revision, branch_id = state(rt, campaign_id)
    with pytest.raises(PermissionError, match="persisted target"):
        rt.narrative_settle(
            campaign_id,
            principal_id="player",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="forged-knowledge-revise",
            event={
                "event_type": "attempt",
                "summary": "Attempt a forged knowledge revision.",
                "audience_scope": "public",
                "participants": [{"actor_id": controlled["id"], "role": "speaker"}],
            },
            actor_knowledge=[
                {
                    "action": "revise",
                    "actor_id": controlled["id"],
                    "knowledge_id": protected_knowledge.id,
                    "expected_revision_id": protected_knowledge.revision_id,
                    "proposition": "Forged belief.",
                    "epistemic_status": "known",
                    "disclosure_scope": "owner",
                }
            ],
        )
    assert rt.knowledge.get(protected_knowledge.id).proposition == "Protected belief."


def test_memory_crud_retractions_idempotency_and_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "memory-crud.db"
    rt = runtime(database_path)
    campaign_id = setup_campaign(rt)
    npc = actor(rt, campaign_id)
    enter_play(rt, campaign_id)
    revision, branch_id = state(rt, campaign_id)
    request = {
        "campaign_id": campaign_id,
        "principal_id": "owner",
        "expected_revision": revision,
        "expected_branch_id": branch_id,
        "idempotency_key": "memory-add",
        "event": {
            "event_type": "memory.added",
            "summary": "The actor makes and learns a promise.",
            "audience_scope": "public",
            "participants": [{"actor_id": npc["id"], "role": "speaker"}],
        },
        "record_changes": [
            {
                "action": "create",
                "record": {
                    "id": "record.promise",
                    "kind": "relationship",
                    "title": "Promise",
                    "audience": {"scope": "actor", "actor_id": npc["id"]},
                    "controller": {"actor_id": npc["id"]},
                    "data": {"summary": "Keep the first promise."},
                },
            }
        ],
        "facts": [
            {
                "action": "add",
                "fact_key": "fact.promise",
                "subject_ref": npc["id"],
                "predicate": "promise",
                "content": "The first promise remains active.",
                "disclosure_scope": "public",
            }
        ],
        "actor_knowledge": [
            {
                "action": "add",
                "actor_id": npc["id"],
                "knowledge_key": "knowledge.promise",
                "proposition": "The bell marks the promise.",
                "disclosure_scope": "owner",
            }
        ],
    }
    added = rt.narrative_settle(**request)
    assert json.dumps(rt.narrative_settle(**request), sort_keys=True) == json.dumps(
        added, sort_keys=True
    )

    restarted = runtime(database_path)
    memory = restarted.actor_memory_context(
        campaign_id,
        actor_id=npc["id"],
        principal_id="owner",
        current_refs=[
            f"fact:{added['facts'][0]['id']}",
            f"knowledge:{added['actor_knowledge'][0]['id']}",
            "record:record.promise",
        ],
        budget_chars=50_000,
    )["memory"]
    assert any(
        "first promise remains" in item["content"].casefold()
        for item in memory["motivational"]
    )
    assert any("bell marks" in item["content"].casefold() for item in memory["semantic"])

    revision, branch_id = state(restarted, campaign_id)
    retract_request = {
        "campaign_id": campaign_id,
        "principal_id": "owner",
        "expected_revision": revision,
        "expected_branch_id": branch_id,
        "idempotency_key": "memory-retract",
        "event": {
            "event_type": "memory.retracted",
            "summary": "The actor releases the promise.",
            "audience_scope": "public",
            "participants": [{"actor_id": npc["id"], "role": "speaker"}],
        },
        "record_changes": [
            {
                "action": "update",
                "expected_revision": 1,
                "record": {
                    "id": "record.promise",
                    "kind": "relationship",
                    "title": "Promise",
                    "audience": {"scope": "actor", "actor_id": npc["id"]},
                    "controller": {"actor_id": npc["id"]},
                    "data": {"summary": "The promise was consciously released."},
                },
            }
        ],
        "facts": [
            {
                "action": "revise",
                "memory_id": added["facts"][0]["id"],
                "expected_revision_id": added["facts"][0]["revision_id"],
                "subject_ref": npc["id"],
                "content": "The first promise is no longer active.",
                "status": "retracted",
                "disclosure_scope": "public",
            }
        ],
        "actor_knowledge": [
            {
                "action": "revise",
                "actor_id": npc["id"],
                "knowledge_id": added["actor_knowledge"][0]["id"],
                "expected_revision_id": added["actor_knowledge"][0]["revision_id"],
                "proposition": "The bell once marked the promise.",
                "epistemic_status": "forgotten",
                "disclosure_scope": "owner",
            }
        ],
    }
    retracted = restarted.narrative_settle(**retract_request)
    assert json.dumps(
        restarted.narrative_settle(**retract_request), sort_keys=True
    ) == json.dumps(retracted, sort_keys=True)
    final_memory = restarted.actor_memory_context(
        campaign_id,
        actor_id=npc["id"],
        principal_id="owner",
        current_refs=["record:record.promise"],
        budget_chars=50_000,
    )["memory"]
    serialized = json.dumps(final_memory, ensure_ascii=False)
    assert "The first promise remains active." not in serialized
    assert "The bell marks the promise." not in serialized
    assert "The promise was consciously released." in serialized
    assert restarted.query(
        campaign_id,
        principal_id="owner",
        kind="record",
        record_id="record.promise",
    )["revision"] == 2
