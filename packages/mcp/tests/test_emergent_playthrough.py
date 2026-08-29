from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from sagasmith_core.database import Database, sqlite_database_url
from sagasmith_core.integrity import canonical_json

from sagasmith_narrative_mcp.runtime import NarrativeRuntime

PROPOSAL_SECRET = "narrative-proposal-attestation-test-secret-at-least-32-bytes"


def runtime(path: Path, *, proposal_secret: str | None = None) -> NarrativeRuntime:
    database = Database(sqlite_database_url(path))
    database.create_schema()
    return NarrativeRuntime(database, proposal_attestation_secret=proposal_secret)


def state(rt: NarrativeRuntime, campaign_id: str) -> tuple[int, str]:
    return rt.campaigns.get(campaign_id).revision, rt.branch_id(campaign_id)


def manifest(
    identifier: str,
    *,
    classification: str,
    root_id: str | None = None,
    parent_id: str = "",
    generation: int = 0,
    scene_id: str = "scene.gate",
) -> dict:
    return {
        "schema_version": 1,
        "id": identifier,
        "classification": classification,
        "title": identifier,
        "lineage": {
            "root_id": root_id or identifier,
            "parent_id": parent_id,
            "generation": generation,
            "basis_refs": ["scene:scene.gate"] if generation else [],
        },
        "setting": {"premise": "The drowned city bargains with its memories."},
        "atlas": {
            "chapters": [
                {"id": f"chapter.{generation}", "summary": "A chapter", "scene_ids": [scene_id]}
            ],
            "scenes": [{"id": scene_id, "summary": f"Play at {scene_id}."}],
        },
        "fronts": [
            {"id": f"front.{generation}", "summary": "The tide advances."}
        ],
        "threads": [
            {"id": f"thread.{generation}", "summary": "Who opened the sluice?"}
        ],
        "clues": [
            {
                "id": f"clue.{generation}",
                "summary": "A wet brass key.",
                "scene_ids": [scene_id],
            }
        ],
        "character_arcs": [
            {
                "id": f"arc.{generation}",
                "actor_ref": "actor.pc",
                "arc_type": "player_opportunity",
                "question": "Will the hero trust the ferryman?",
                "opportunities": [f"scene:{scene_id}"],
            }
        ],
    }


def pack(
    pack_id: str,
    version: str,
    runtime_manifest: dict,
    *,
    dependencies: list[object] | None = None,
    runtime_attestation: dict | None = None,
) -> dict:
    return {
        "id": pack_id,
        "version": version,
        "title": pack_id,
        "kind": "module",
        "profile_requirements": [],
        "dependencies": dependencies or [],
        "sources": [{"type": "self-authored", "citation": __file__}],
        "rights": {"distribution": "private", "license": "self-authored"},
        "content": {
            "runtime_manifest": runtime_manifest,
            **(
                {"runtime_attestation": runtime_attestation}
                if runtime_attestation is not None
                else {}
            ),
        },
        "review": {"agent_finalization": True},
    }


def pack_lifecycle(rt: NarrativeRuntime, campaign_id: str, value: dict) -> str:
    key = f"{value['id']}@{value['version']}"
    for index, action in enumerate(("create_draft", "finalize", "import", "activate")):
        revision, branch_id = state(rt, campaign_id)
        rt.pack_change(
            campaign_id,
            action=action,
            pack=value if action == "create_draft" else None,
            pack_key=None if action == "create_draft" else key,
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key=f"{key}-{index}",
        )
    return key


def activate_profile(
    rt: NarrativeRuntime,
    campaign_id: str,
    *,
    capabilities: list[str] | None = None,
) -> None:
    profile = {
        "id": "profile.play",
        "version": "1",
        "mechanics_level": 0,
        "capabilities": capabilities or [],
        "authority": {"facilitator_roles": ["owner", "dm"]},
        "sources": [{"type": "self-authored", "citation": __file__}],
    }
    for index, action in enumerate(("create_draft", "finalize", "activate")):
        revision, branch_id = state(rt, campaign_id)
        rt.profile_change(
            campaign_id,
            action=action,
            profile=profile if action == "create_draft" else None,
            profile_key=None if action == "create_draft" else "profile.play@1",
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key=f"profile-{index}",
        )


def test_authored_campaign_grows_off_atlas_through_bounded_proposal_and_pack_lineage(
    tmp_path: Path,
) -> None:
    rt = runtime(tmp_path / "emergent.db")
    campaign_id = rt.campaign_create(
        name="Emergent playthrough", principal_id="owner", idempotency_key="campaign"
    )["id"]
    activate_profile(rt, campaign_id)
    root_manifest = manifest("manifest.root", classification="authored_narrative")
    root_key = pack_lifecycle(rt, campaign_id, pack("city", "1", root_manifest))
    assert rt.query(campaign_id, principal_id="owner", kind="campaign_design")[
        "campaign_mode"
    ] == "authored_narrative"

    context = rt.campaign_expansion(
        campaign_id, action="context", data=None, principal_id="owner"
    )
    contract = context["worker_contract"]
    assert contract["tools_exposed"] is False
    assert contract["proposal_only"] is True
    assert contract["authoritative_result"] is False
    child_manifest = manifest(
        "manifest.wharf",
        classification="emergent_episode",
        root_id="manifest.root",
        parent_id="manifest.root",
        generation=1,
        scene_id="scene.wharf",
    )
    validated = rt.campaign_expansion(
        campaign_id,
        action="validate",
        data={
            "context_receipt": context["context_receipt"],
            "proposal": {
                "pack_key": "city-wharf@1",
                "runtime_manifest": child_manifest,
            },
        },
        principal_id="owner",
    )
    assert validated["status"] == "validated_proposal"
    assert validated["settlement_route"][-1] == "pack_change.activate"
    child_key = pack_lifecycle(
        rt,
        campaign_id,
        pack(
            "city-wharf",
            "1",
            validated["proposal"]["runtime_manifest"],
            dependencies=[
                {
                    "id": "city",
                    "version": "1",
                    "checksum": rt.query(
                        campaign_id,
                        principal_id="owner",
                        kind="pack",
                        record_id=root_key,
                    )["pack"]["checksum"],
                }
            ],
            runtime_attestation=validated["proposal_attestation"],
        ),
    )
    assert child_key != root_key
    design = rt.query(campaign_id, principal_id="owner", kind="campaign_design")
    assert design["campaign_mode"] == "authored_with_extensions"
    assert design["manifests"][child_key]["lineage"]["generation"] == 1

    revision, branch_id = state(rt, campaign_id)
    rt.set_phase(
        campaign_id,
        phase="play",
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="play",
    )
    evidence = []
    for index, scene_id in enumerate(("scene.gate", "scene.wharf")):
        revision, branch_id = state(rt, campaign_id)
        rt.scene_change(
            campaign_id,
            action="start",
            scene={"id": scene_id, "title": scene_id, "audience": {"scope": "table"}},
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key=f"scene-{index}",
        )
        revision, branch_id = state(rt, campaign_id)
        settled = rt.narrative_settle(
            campaign_id,
            event={
                "event_type": "chapter.played",
                "summary": f"Players changed the situation at {scene_id}.",
                "audience_scope": "public",
            },
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key=f"settle-{index}",
        )
        evidence.append(f"event:{settled['event']['id']}")
        revision, branch_id = state(rt, campaign_id)
        rt.scene_change(
            campaign_id,
            action="end",
            scene_id=scene_id,
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key=f"end-{index}",
        )
    revision, branch_id = state(rt, campaign_id)
    advanced = rt.campaign_design_change(
        campaign_id,
        entity_type="clue",
        entity_id="clue.1",
        status="discovered",
        evidence_refs=[evidence[-1]],
        note="The wharf chapter exposed the key.",
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="reveal-child-clue",
    )
    assert advanced["change"]["evidence_refs"] == [evidence[-1]]


def test_expansion_receipt_lineage_permissions_and_new_pack_version_fail_closed(
    tmp_path: Path,
) -> None:
    rt = runtime(tmp_path / "expansion-errors.db")
    campaign_id = rt.campaign_create(
        name="Expansion errors", principal_id="owner", idempotency_key="campaign"
    )["id"]
    activate_profile(rt, campaign_id)
    pack_lifecycle(
        rt,
        campaign_id,
        pack("seed", "1", manifest("manifest.seed", classification="emergent_seed")),
    )
    rt.access.ensure_principal("player")
    rt.access.grant_campaign(campaign_id, "player", role="player")
    with pytest.raises(PermissionError, match="facilitator"):
        rt.campaign_expansion(campaign_id, action="context", data=None, principal_id="player")
    context = rt.campaign_expansion(
        campaign_id, action="context", data=None, principal_id="owner"
    )
    tampered = deepcopy(context["context_receipt"])
    tampered["campaign_revision"] += 1
    with pytest.raises(PermissionError, match="invalid"):
        rt.campaign_expansion(
            campaign_id,
            action="validate",
            data={"context_receipt": tampered, "proposal": {}},
            principal_id="owner",
        )
    bad = manifest(
        "manifest.bad",
        classification="emergent_episode",
        root_id="manifest.seed",
        parent_id="manifest.seed",
        generation=2,
        scene_id="scene.bad",
    )
    with pytest.raises(ValueError, match="contiguous"):
        rt.campaign_expansion(
            campaign_id,
            action="validate",
            data={
                "context_receipt": context["context_receipt"],
                "proposal": {"pack_key": "bad@1", "runtime_manifest": bad},
            },
            principal_id="owner",
        )
    duplicate = manifest(
        "manifest.child",
        classification="emergent_episode",
        root_id="manifest.seed",
        parent_id="manifest.seed",
        generation=1,
        scene_id="scene.child",
    )
    with pytest.raises(ValueError, match="new Pack version"):
        rt.campaign_expansion(
            campaign_id,
            action="validate",
            data={
                "context_receipt": context["context_receipt"],
                "proposal": {"pack_key": "seed@1", "runtime_manifest": duplicate},
            },
            principal_id="owner",
        )


def test_expansion_context_is_strictly_bounded_and_only_attests_delivered_evidence(
    tmp_path: Path,
) -> None:
    rt = runtime(tmp_path / "bounded-expansion.db")
    campaign_id = rt.campaign_create(
        name="Bounded expansion", principal_id="owner", idempotency_key="campaign"
    )["id"]
    activate_profile(rt, campaign_id)
    pack_lifecycle(
        rt,
        campaign_id,
        pack("seed", "1", manifest("manifest.seed", classification="emergent_seed")),
    )
    revision, branch_id = state(rt, campaign_id)
    rt.record_change(
        campaign_id,
        action="create",
        record={
            "id": "record.worker-secret",
            "kind": "secret",
            "title": "Worker secret",
            "audience": {"scope": "private_worker", "principal_id": "owner"},
            "controller": {"scope": "facilitator"},
            "data": {"summary": "Never disclose this isolated worker motive."},
        },
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="private-worker-record",
    )
    context = rt.campaign_expansion(
        campaign_id,
        action="context",
        data={"budget_chars": 1_024},
        principal_id="owner",
    )
    assert len(canonical_json(context["context"])) <= 1_024
    delivered = {item["ref"] for item in context["context"]["evidence"]}
    assert set(context["context"]["allowed_basis_refs"]) == delivered
    assert "record:record.worker-secret" not in delivered
    assert "isolated worker motive" not in str(context)


def test_episode_pack_requires_signed_proposal_parent_checksum_and_all_nested_evidence(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "episode-attestation.db"
    rt = runtime(database_path, proposal_secret=PROPOSAL_SECRET)
    campaign_id = rt.campaign_create(
        name="Episode attestation", principal_id="owner", idempotency_key="campaign"
    )["id"]
    activate_profile(rt, campaign_id)
    root_key = pack_lifecycle(
        rt,
        campaign_id,
        pack("seed", "1", manifest("manifest.seed", classification="emergent_seed")),
    )
    parent = rt.query(
        campaign_id,
        principal_id="owner",
        kind="pack",
        record_id=root_key,
    )["pack"]
    parent_dependency = {
        "id": parent["id"],
        "version": parent["version"],
        "checksum": parent["checksum"],
    }
    context = rt.campaign_expansion(
        campaign_id, action="context", data=None, principal_id="owner"
    )
    child = manifest(
        "manifest.child",
        classification="emergent_episode",
        root_id="manifest.seed",
        parent_id="manifest.seed",
        generation=1,
        scene_id="scene.child",
    )
    revision, branch_id = state(rt, campaign_id)
    with pytest.raises(PermissionError, match="signed campaign expansion"):
        rt.pack_change(
            campaign_id,
            action="create_draft",
            pack=pack("child-direct", "1", child, dependencies=[parent_dependency]),
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="direct-bypass",
        )

    nested_fake = deepcopy(child)
    nested_fake["setting"]["evidence_refs"] = ["event:not-on-this-branch"]
    with pytest.raises(ValueError, match="unauthorized basis refs"):
        rt.campaign_expansion(
            campaign_id,
            action="validate",
            data={
                "context_receipt": context["context_receipt"],
                "proposal": {
                    "pack_key": "child-fake@1",
                    "runtime_manifest": nested_fake,
                },
            },
            principal_id="owner",
        )

    validated = rt.campaign_expansion(
        campaign_id,
        action="validate",
        data={
            "context_receipt": context["context_receipt"],
            "proposal": {"pack_key": "child@1", "runtime_manifest": child},
        },
        principal_id="owner",
    )
    with pytest.raises(ValueError, match="dependencies"):
        rt.pack_change(
            campaign_id,
            action="create_draft",
            pack=pack(
                "child",
                "1",
                validated["proposal"]["runtime_manifest"],
                runtime_attestation=validated["proposal_attestation"],
            ),
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="missing-parent-dependency",
        )
    tampered_manifest = deepcopy(validated["proposal"]["runtime_manifest"])
    tampered_manifest["title"] = "A different unvalidated episode"
    with pytest.raises(ValueError, match="out of scope"):
        rt.pack_change(
            campaign_id,
            action="create_draft",
            pack=pack(
                "child",
                "1",
                tampered_manifest,
                dependencies=[parent_dependency],
                runtime_attestation=validated["proposal_attestation"],
            ),
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="tampered-manifest",
        )

    restarted = runtime(database_path, proposal_secret=PROPOSAL_SECRET)
    child_key = pack_lifecycle(
        restarted,
        campaign_id,
        pack(
            "child",
            "1",
            validated["proposal"]["runtime_manifest"],
            dependencies=[parent_dependency],
            runtime_attestation=validated["proposal_attestation"],
        ),
    )
    assert child_key in restarted.query(
        campaign_id, principal_id="owner", kind="campaign_design"
    )["manifests"]

    wrong_key_runtime = runtime(
        database_path,
        proposal_secret="different-proposal-attestation-test-secret-at-least-32-bytes",
    )
    revision, branch_id = state(wrong_key_runtime, campaign_id)
    with pytest.raises(PermissionError, match="signed campaign expansion"):
        wrong_key_runtime.pack_change(
            campaign_id,
            action="activate",
            pack_key=child_key,
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="wrong-instance-key",
        )


def test_progress_requires_legal_transition_real_branch_evidence_and_declared_opportunity(
    tmp_path: Path,
) -> None:
    rt = runtime(tmp_path / "progress.db")
    campaign_id = rt.campaign_create(
        name="Progress", principal_id="owner", idempotency_key="campaign"
    )["id"]
    activate_profile(rt, campaign_id)
    pack_lifecycle(
        rt,
        campaign_id,
        pack("root", "1", manifest("manifest.root", classification="authored_narrative")),
    )
    revision, branch_id = state(rt, campaign_id)
    rt.set_phase(
        campaign_id,
        phase="play",
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="play",
    )
    revision, branch_id = state(rt, campaign_id)
    with pytest.raises(ValueError, match="unsupported"):
        rt.campaign_design_change(
            campaign_id,
            entity_type="clue",
            entity_id="clue.0",
            status="revealed",
            evidence_refs=["event:invented"],
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="bad-status",
        )
    with pytest.raises(ValueError, match="missing branch evidence"):
        rt.campaign_design_change(
            campaign_id,
            entity_type="clue",
            entity_id="clue.0",
            status="discovered",
            evidence_refs=["event:invented"],
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="fake-evidence",
        )
    with pytest.raises(ValueError, match="occurred branch-local"):
        rt.campaign_design_change(
            campaign_id,
            entity_type="character_arc",
            entity_id="arc.0",
            status="completed",
            evidence_refs=["scene:scene.gate"],
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="scene-only-opportunity",
        )
    event = rt.events.add(
        campaign_id,
        event_type="choice",
        summary="The players inspect the gate clue.",
        payload={"scene_id": "scene.gate"},
        audience_scope="public",
        branch_id=branch_id,
    )
    evidence_ref = f"event:{event.id}"
    with pytest.raises(ValueError, match="declared opportunities"):
        rt.campaign_design_change(
            campaign_id,
            entity_type="character_arc",
            entity_id="arc.0",
            status="completed",
            evidence_refs=[evidence_ref],
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="forced-ending",
        )
    completed = rt.campaign_design_change(
        campaign_id,
        entity_type="character_arc",
        entity_id="arc.0",
        status="completed",
        evidence_refs=["scene:scene.gate", evidence_ref],
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="chosen-opportunity",
    )
    assert completed["change"]["status"] == "completed"


def test_modern_conflict_tools_block_progress_and_expansion_at_runtime_boundary(
    tmp_path: Path,
) -> None:
    rt = runtime(tmp_path / "conflict-boundary.db")
    campaign_id = rt.campaign_create(
        name="Conflict boundary", principal_id="owner", idempotency_key="campaign"
    )["id"]
    activate_profile(rt, campaign_id, capabilities=["conflict"])
    pack_lifecycle(
        rt,
        campaign_id,
        pack("root", "1", manifest("manifest.root", classification="authored_narrative")),
    )
    revision, branch_id = state(rt, campaign_id)
    rt.set_phase(
        campaign_id,
        phase="play",
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="play",
    )
    assert rt.campaign_expansion(
        campaign_id, action="context", data=None, principal_id="owner"
    )["worker_contract"]["tools_exposed"] is False
    revision, branch_id = state(rt, campaign_id)
    rt.conflict(
        campaign_id,
        action="start",
        data={"stakes": "the gate"},
        principal_id="owner",
        expected_revision=revision,
        expected_branch_id=branch_id,
        idempotency_key="conflict-start",
    )
    with pytest.raises(ValueError, match="Conflict"):
        rt.campaign_expansion(
            campaign_id, action="context", data=None, principal_id="owner"
        )
    revision, branch_id = state(rt, campaign_id)
    with pytest.raises(ValueError, match="non-conflict Play"):
        rt.campaign_design_change(
            campaign_id,
            entity_type="clue",
            entity_id="clue.0",
            status="discovered",
            evidence_refs=["scene:scene.gate"],
            principal_id="owner",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="conflict-progress",
        )
