from __future__ import annotations

from pathlib import Path

import pytest
from sagasmith_core.database import Database, sqlite_database_url
from sagasmith_narrative.contracts import narrative_document

from sagasmith_narrative_mcp.runtime import NarrativeRuntime


def runtime(tmp_path: Path) -> NarrativeRuntime:
    database = Database(sqlite_database_url(tmp_path / "test.db"))
    database.create_schema()
    return NarrativeRuntime(database)


def state(rt: NarrativeRuntime, campaign_id: str) -> tuple[int, str]:
    return rt.campaigns.get(campaign_id).revision, rt.branch_id(campaign_id)


def create_campaign(rt: NarrativeRuntime) -> str:
    return rt.campaign_create(name="Test Campaign", principal_id="owner", idempotency_key="create")[
        "id"
    ]


def finalize_profile(rt: NarrativeRuntime, campaign_id: str, *, conflict: bool = True) -> None:
    revision, branch = state(rt, campaign_id)
    profile = {
        "id": "profile.test",
        "version": "1.0.0",
        "title": "Test",
        "mechanics_level": 1,
        "capabilities": ["mechanics", "conflict"] if conflict else ["mechanics"],
        "mechanics": [
            {
                "id": "risk",
                "kind": "dice_pool",
                "sides": 6,
                "max_dice": 4,
                "bands": [
                    {"minimum": 1, "maximum": 3, "outcome": "cost"},
                    {"minimum": 4, "maximum": 6, "outcome": "success"},
                ],
            }
        ],
        "sources": [{"kind": "original", "ref": "fixture"}],
    }
    rt.profile_change(
        campaign_id,
        action="create_draft",
        profile=profile,
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch,
        idempotency_key="profile-draft",
    )
    revision, branch = state(rt, campaign_id)
    rt.profile_change(
        campaign_id,
        action="finalize",
        profile_key="profile.test@1.0.0",
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch,
        idempotency_key="profile-finalize",
    )
    revision, branch = state(rt, campaign_id)
    rt.profile_change(
        campaign_id,
        action="activate",
        profile_key="profile.test@1.0.0",
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch,
        idempotency_key="profile-activate",
    )


def test_profile_phase_mechanic_and_exact_replay(tmp_path: Path) -> None:
    rt = runtime(tmp_path)
    campaign_id = create_campaign(rt)
    finalize_profile(rt, campaign_id)
    revision, branch = state(rt, campaign_id)
    rt.set_phase(
        campaign_id,
        phase="play",
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch,
        idempotency_key="play",
    )
    revision, branch = state(rt, campaign_id)
    arguments = dict(
        mechanic_id="risk",
        inputs={"dice": 2},
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch,
        idempotency_key="roll",
    )
    first = rt.mechanic_resolve(campaign_id, **arguments)
    second = rt.mechanic_resolve(campaign_id, **arguments)
    assert first == second
    assert first["random_stream_receipt"]["cursor_after"] == 2


def test_element_steward_controls_only_granted_record(tmp_path: Path) -> None:
    rt = runtime(tmp_path)
    campaign_id = create_campaign(rt)
    rt.access.ensure_principal("player")
    rt.access.grant_campaign(campaign_id, "player", role="player")
    revision, branch = state(rt, campaign_id)
    rt.access_change(
        campaign_id,
        principal_id="owner",
        action="element_grant",
        target_principal_id="player",
        element_ref="faction.moss",
        can_control=True,
        expected_revision=revision,
        expected_branch_id=branch,
        idempotency_key="grant",
    )
    revision, branch = state(rt, campaign_id)
    rt.record_change(
        campaign_id,
        action="create",
        record={
            "id": "faction.moss",
            "kind": "faction",
            "controller": {"element_ref": "faction.moss"},
        },
        principal_id="player",
        expected_revision=revision,
        expected_branch_id=branch,
        idempotency_key="moss-create",
    )
    revision, branch = state(rt, campaign_id)
    with pytest.raises(PermissionError):
        rt.record_change(
            campaign_id,
            action="create",
            record={
                "id": "faction.stone",
                "kind": "faction",
                "controller": {"element_ref": "faction.stone"},
            },
            principal_id="player",
            expected_revision=revision,
            expected_branch_id=branch,
            idempotency_key="stone-create",
        )


def test_narrative_settlement_is_atomic(tmp_path: Path) -> None:
    rt = runtime(tmp_path)
    campaign_id = create_campaign(rt)
    revision, branch = state(rt, campaign_id)
    result = rt.narrative_settle(
        campaign_id,
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch,
        idempotency_key="settle",
        event={
            "summary": "The village learns the truth.",
            "retrieval_text": "village truth public revelation",
            "audience_scope": "public",
        },
        record_changes=[{"action": "create", "record": {"id": "clock.truth", "kind": "clock"}}],
        facts=[
            {
                "action": "add",
                "fact_key": "truth.revealed",
                "content": "The truth is public.",
                "kind": "fact",
            }
        ],
    )
    assert result["event"]["sequence"] == 1
    assert result["event"]["retrieval_text"] == "village truth public revelation"
    assert narrative_document(rt.campaigns.get(campaign_id).state)["records"]["clock.truth"]


def test_private_settlement_cannot_back_visible_actor_knowledge_before_any_write(
    tmp_path: Path,
) -> None:
    rt = runtime(tmp_path)
    campaign_id = create_campaign(rt)
    revision, branch = state(rt, campaign_id)
    npc = rt.actor_create(
        campaign_id,
        actor={"name": "Keeper", "type": "npc", "summary": "A careful keeper."},
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch,
        idempotency_key="keeper",
    )
    revision, branch = state(rt, campaign_id)
    with pytest.raises(ValueError, match="private dm event"):
        rt.narrative_settle(
            campaign_id,
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch,
            idempotency_key="private-leak",
            event={
                "summary": "The keeper learns a private secret.",
                "audience_scope": "dm",
                "participants": [{"actor_id": npc["id"], "role": "witness"}],
            },
            record_changes=[
                {"action": "create", "record": {"id": "secret", "kind": "clue"}}
            ],
            actor_knowledge=[
                {
                    "action": "add",
                    "actor_id": npc["id"],
                    "knowledge_key": "keeper-secret",
                    "proposition": "The sealed door is already open.",
                    "disclosure_scope": "owner",
                }
            ],
        )

    assert state(rt, campaign_id) == (revision, branch)
    assert rt.events.list(campaign_id, branch_id=branch) == []
    assert rt.knowledge.list(campaign_id, actor_id=npc["id"], branch_id=branch) == []
    assert "secret" not in narrative_document(rt.campaigns.get(campaign_id).state)["records"]

    # The failed request did not reserve idempotency, and this remains true
    # after reopening the authoritative database.
    restarted = runtime(tmp_path)
    result = restarted.narrative_settle(
        campaign_id,
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch,
        idempotency_key="private-leak",
        event={"summary": "A public clue is recorded.", "audience_scope": "public"},
        actor_knowledge=[
            {
                "action": "add",
                "actor_id": npc["id"],
                "knowledge_key": "public-clue",
                "proposition": "The sealed door is already open.",
                "disclosure_scope": "public",
            }
        ],
    )
    assert result["actor_knowledge"][0]["source_event_id"] == result["event"]["id"]
    assert result["actor_knowledge"][0]["disclosure_scope"] == "public"

    revision, branch = state(restarted, campaign_id)
    knowledge_id = result["actor_knowledge"][0]["id"]
    with pytest.raises(ValueError, match="private dm event"):
        restarted.narrative_settle(
            campaign_id,
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch,
            idempotency_key="private-revise-leak",
            event={"summary": "A private correction.", "audience_scope": "dm"},
            actor_knowledge=[
                {
                    "action": "revise",
                    "actor_id": npc["id"],
                    "knowledge_id": knowledge_id,
                    "proposition": "The sealed door was never open.",
                }
            ],
        )
    assert state(restarted, campaign_id) == (revision, branch)


def test_public_settlement_knowledge_survives_player_projection_and_restart(
    tmp_path: Path,
) -> None:
    rt = runtime(tmp_path)
    campaign_id = create_campaign(rt)
    revision, branch = state(rt, campaign_id)
    pc = rt.actor_create(
        campaign_id,
        actor={"name": "Hero", "type": "pc", "summary": "A traveling hero."},
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch,
        idempotency_key="hero",
    )
    rt.access.ensure_principal("player")
    rt.access.grant_campaign(campaign_id, "player", role="player")
    rt.access.grant_actor(
        campaign_id,
        "player",
        pc["id"],
        can_control=False,
        can_view_private=False,
    )
    revision, branch = state(rt, campaign_id)
    result = rt.narrative_settle(
        campaign_id,
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch,
        idempotency_key="public-memory",
        event={
            "summary": "The hero crosses the bright market.",
            "audience_scope": "public",
            "participants": [{"actor_id": pc["id"], "role": "witness"}],
        },
        actor_knowledge=[
            {
                "actor_id": pc["id"],
                "knowledge_key": "market-crossing",
                "proposition": "The hero crossed the market at dawn.",
                "disclosure_scope": "public",
            }
        ],
    )
    assert result["actor_knowledge"][0]["source_event_id"] == result["event"]["id"]
    player_memory = rt.actor_memory_context(
        campaign_id,
        actor_id=pc["id"],
        principal_id="player",
        query="market dawn",
        budget_chars=50_000,
    )
    assert "market at dawn" in str(player_memory["memory"])

    restarted = runtime(tmp_path)
    player_memory_after_restart = restarted.actor_memory_context(
        campaign_id,
        actor_id=pc["id"],
        principal_id="player",
        query="market dawn",
        budget_chars=50_000,
    )
    assert "market at dawn" in str(player_memory_after_restart["memory"])


def test_stale_revision_rejected(tmp_path: Path) -> None:
    rt = runtime(tmp_path)
    campaign_id = create_campaign(rt)
    revision, branch = state(rt, campaign_id)
    rt.record_change(
        campaign_id,
        action="create",
        record={"id": "clock.one", "kind": "clock"},
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch,
        idempotency_key="one",
    )
    with pytest.raises(ValueError, match="revision conflict"):
        rt.record_change(
            campaign_id,
            action="create",
            record={"id": "clock.two", "kind": "clock"},
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch,
            idempotency_key="two",
        )
