from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from mcp import Client
from sagasmith_core.database import sqlite_database_url

from sagasmith_narrative_mcp.config import McpConfig
from sagasmith_narrative_mcp.server import create_server


def _server(tmp_path: Path):
    return create_server(
        McpConfig(database_url=sqlite_database_url(tmp_path / "tool-contract-quality.db"))
    )


def _non_null_variants(schema: dict[str, Any]) -> list[dict[str, Any]]:
    variants = schema.get("anyOf")
    if not isinstance(variants, list):
        return [schema]
    return [item for item in variants if isinstance(item, dict) and item.get("type") != "null"]


def _assert_bounded_parameter(tool_name: str, name: str, schema: dict[str, Any]) -> None:
    for variant in _non_null_variants(schema):
        value_type = variant.get("type")
        if value_type == "string" and not any(
            key in variant for key in ("maxLength", "enum", "const")
        ):
            raise AssertionError(f"{tool_name}.{name} is an unbounded string")
        if value_type == "integer" and not any(
            key in variant for key in ("maximum", "exclusiveMaximum", "enum", "const")
        ):
            raise AssertionError(f"{tool_name}.{name} is an unbounded integer")
        if value_type == "array" and "maxItems" not in variant:
            raise AssertionError(f"{tool_name}.{name} is an unbounded array")
        if (
            value_type == "object"
            and variant.get("additionalProperties")
            and ("maxProperties" not in variant)
        ):
            raise AssertionError(f"{tool_name}.{name} is an unbounded object")


def _assert_precise_output_schema(tool_name: str, schema: dict[str, Any]) -> None:
    Draft202012Validator.check_schema(schema)
    candidates = schema.get("oneOf") or schema.get("anyOf") or [schema]
    assert isinstance(candidates, list) and candidates, f"{tool_name} has no output alternatives"
    for candidate in candidates:
        assert isinstance(candidate, dict), f"{tool_name} has an invalid output alternative"
        if candidate.get("type") != "object":
            continue
        assert candidate.get("properties"), f"{tool_name} has an open, property-free output"
        assert candidate.get("additionalProperties") is not True, (
            f"{tool_name} permits arbitrary top-level output fields"
        )


def test_every_public_tool_has_a_bounded_described_precise_contract(tmp_path: Path) -> None:
    async def exercise() -> None:
        tools = await _server(tmp_path).list_tools()
        assert len(tools) == 31
        for tool in tools:
            assert (tool.description or "").strip(), f"{tool.name} has no description"
            for name, schema in (tool.input_schema.get("properties") or {}).items():
                assert isinstance(schema, dict)
                assert str(schema.get("description") or "").strip(), (
                    f"{tool.name}.{name} has no description"
                )
                _assert_bounded_parameter(tool.name, name, schema)
            assert tool.output_schema is not None
            _assert_precise_output_schema(tool.name, tool.output_schema)
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint is not None
            assert tool.annotations.destructive_hint is not None
            assert tool.annotations.idempotent_hint is not None
            assert tool.annotations.open_world_hint is not None

    asyncio.run(exercise())


def test_bootstrap_descriptions_explain_empty_campaign_path(tmp_path: Path) -> None:
    async def exercise() -> None:
        tools = {tool.name: tool for tool in await _server(tmp_path).list_tools()}
        assert "no built-in defaults" in tools["campaign_setup"].description
        assert "branch_id" in tools["campaign_setup"].description
        assert "campaign-bound exposure" in tools["campaign_setup"].description
        assert "profile_change" in tools["campaign_setup"].description
        assert "pack_change" in tools["campaign_setup"].description
        assert "no default profile" in tools["profile_change"].description
        assert "no default Pack" in tools["pack_change"].description
        assert '"id":"profile.example"' in tools["profile_change"].description
        assert '"actor_schema":{"type":"object"}' in tools["profile_change"].description
        assert "profile.example@1" in tools["profile_change"].description
        assert '"kind":"campaign_seed"' in tools["pack_change"].description
        assert '"principals":[]' in tools["pack_change"].description
        assert "seed.example@1" in tools["pack_change"].description

    asyncio.run(exercise())


def test_campaign_design_and_npc_lifecycle_are_discoverable_from_tools_list(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        tools = {tool.name: tool for tool in await _server(tmp_path).list_tools()}
        pack = tools["pack_change"]
        pack_examples = pack.input_schema["properties"]["pack"]["examples"]
        runtime_manifest = pack_examples[0]["content"]["runtime_manifest"]
        assert runtime_manifest["classification"] == "emergent_seed"
        assert runtime_manifest["atlas"]["scenes"]
        assert runtime_manifest["fronts"]
        assert runtime_manifest["threads"]
        assert runtime_manifest["clues"]
        assert runtime_manifest["character_arcs"]
        assert "server binds" in pack.input_schema["properties"]["pack"]["description"]

        conversation = tools["npc_conversation"]
        Draft202012Validator.check_schema(conversation.input_schema)
        variants = {
            item["properties"]["action"]["const"]: item
            for item in conversation.input_schema["oneOf"]
        }
        assert set(variants) == {
            "open",
            "claim",
            "refresh",
            "propose",
            "publish",
            "close",
            "abort",
        }
        interlocutors = variants["open"]["properties"]["data"]["properties"][
            "interlocutors"
        ]["properties"]
        assert set(interlocutors) == {
            "actor_ids",
            "principal_ids",
            "publication_scopes",
        }
        assert "activation_ref" in variants["claim"]["properties"]["data"]["properties"]
        propose_fields = variants["propose"]["properties"]["data"]["properties"]
        assert set(propose_fields) == {
            "activation_ref",
            "lease_id",
            "context_receipt",
            "proposal",
        }
        assert "proposal_id" in variants["publish"]["properties"]["data"]["properties"]
        assert "close_token" in variants["close"]["properties"]["data"]["properties"]
        for source_name in (
            "activation_ref",
            "activation_id",
            "actor_runtime_id",
            "lease_id",
            "context_receipt",
            "proposal_id",
            "close_token",
        ):
            assert source_name in conversation.description

        design_schema = next(
            item
            for item in tools["narrative_query"].output_schema["oneOf"]
            if item.get("title") == "narrative_queryCampaignDesignOutput"
        )
        assert design_schema["additionalProperties"] is False
        assert set(design_schema["required"]) == {
            "schema_version",
            "campaign_mode",
            "manifests",
            "progress",
        }

    asyncio.run(exercise())


def test_public_emergent_design_and_complete_npc_conversation_lifecycle(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = _server(tmp_path)
        tools = {tool.name: tool for tool in await server.list_tools()}
        pack_example = tools["pack_change"].input_schema["properties"]["pack"][
            "examples"
        ][0]

        async with Client(server, mode="2026-07-28") as client:
            async def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                Draft202012Validator(tools[name].input_schema).validate(arguments)
                result = await client.call_tool(name, arguments)
                assert not result.is_error, (result.structured_content, result.content)
                assert isinstance(result.structured_content, dict)
                return result.structured_content

            campaign = await call(
                "campaign_setup",
                {
                    "action": "create",
                    "name": "Public emergent contract",
                    "idempotency_key": "public-emergent-campaign",
                },
            )
            campaign_id = campaign["id"]
            revision = campaign["revision"]
            branch_id = campaign["branch_id"]

            profile = {
                "id": "profile.public-emergent",
                "version": "1",
                "mechanics_level": 0,
                "capabilities": ["npc_conversation"],
                "authority": {
                    "facilitator_roles": ["owner"],
                    "audience_scopes": ["public"],
                },
                "actor_schema": {"type": "object"},
                "record_extensions": {},
                "mechanics": [],
                "sources": [{"type": "self-authored", "citation": __file__}],
            }
            for index, action in enumerate(("create_draft", "finalize", "activate")):
                changed = await call(
                    "profile_change",
                    {
                        "campaign_id": campaign_id,
                        "action": action,
                        **({"profile": profile} if action == "create_draft" else {}),
                        **(
                            {"profile_key": "profile.public-emergent@1"}
                            if action != "create_draft"
                            else {}
                        ),
                        "expected_revision": revision,
                        "expected_branch_id": branch_id,
                        "idempotency_key": f"public-profile-{index}",
                    },
                )
                revision = changed["campaign_revision"]

            for index, action in enumerate(
                ("create_draft", "finalize", "import", "activate")
            ):
                changed = await call(
                    "pack_change",
                    {
                        "campaign_id": campaign_id,
                        "action": action,
                        **({"pack": pack_example} if action == "create_draft" else {}),
                        **(
                            {"pack_key": "seed.example@1"}
                            if action != "create_draft"
                            else {}
                        ),
                        "expected_revision": revision,
                        "expected_branch_id": branch_id,
                        "idempotency_key": f"public-pack-{index}",
                    },
                )
                revision = changed["campaign_revision"]

            design = await call(
                "narrative_query",
                {"campaign_id": campaign_id, "kind": "campaign_design"},
            )
            Draft202012Validator(tools["narrative_query"].output_schema).validate(design)
            assert design["campaign_mode"] == "emergent"
            assert design["manifests"]["seed.example@1"]["clues"][0]["id"] == "clue.key"

            phase = await call(
                "game_phase",
                {
                    "campaign_id": campaign_id,
                    "phase": "play",
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "public-enter-play",
                },
            )
            revision = phase["campaign_revision"]
            npc = await call(
                "actor_change",
                {
                    "campaign_id": campaign_id,
                    "action": "create",
                    "actor": {"name": "Gate Witness", "type": "npc"},
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "public-create-npc",
                },
            )
            revision = npc["campaign_revision"]
            scene = await call(
                "scene_change",
                {
                    "campaign_id": campaign_id,
                    "action": "start",
                    "scene": {
                        "id": "scene.opening",
                        "title": "The sealed gate",
                        "audience": {"scope": "public"},
                    },
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "public-opening-scene",
                },
            )
            revision = scene["campaign_revision"]
            settled = await call(
                "narrative_settle",
                {
                    "campaign_id": campaign_id,
                    "event": {
                        "event_type": "clue.found",
                        "summary": "The wet brass key is found at the gate.",
                        "audience_scope": "public",
                    },
                    "record_changes": None,
                    "facts": None,
                    "actor_knowledge": None,
                    "snapshot": None,
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "public-key-found",
                },
            )
            revision = settled["campaign_revision"]
            advanced = await call(
                "campaign_design_change",
                {
                    "campaign_id": campaign_id,
                    "entity_type": "clue",
                    "entity_id": "clue.key",
                    "status": "discovered",
                    "evidence_refs": [f"event:{settled['event']['id']}"],
                    "note": "The opening scene exposed the key.",
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "public-discover-key",
                },
            )
            revision = advanced["campaign_revision"]
            assert advanced["campaign_design"]["progress"]["clue"]["clue.key"][
                "status"
            ] == "discovered"
            progressed_design = await call(
                "narrative_query",
                {"campaign_id": campaign_id, "kind": "campaign_design"},
            )
            Draft202012Validator(tools["narrative_query"].output_schema).validate(
                progressed_design
            )
            assert progressed_design["progress"]["clue"]["clue.key"]["history"][0][
                "campaign_revision"
            ] == revision

            opened = await call(
                "npc_conversation",
                {
                    "campaign_id": campaign_id,
                    "action": "open",
                    "npc_actor_id": npc["id"],
                    "data": {
                        "interlocutors": {
                            "principal_ids": ["system:local"],
                            "publication_scopes": ["public"],
                        },
                        "query": "What does the witness admit about the key?",
                    },
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "public-conversation-open",
                },
            )
            revision = opened["campaign_revision"]
            conversation_id = opened["conversation"]["id"]
            close_token = opened["close_token"]
            refreshed = await call(
                "npc_conversation",
                {
                    "campaign_id": campaign_id,
                    "action": "refresh",
                    "conversation_id": conversation_id,
                    "data": {"current_refs": ["clue:clue.key"]},
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "public-conversation-refresh",
                },
            )
            revision = refreshed["campaign_revision"]
            claimed = await call(
                "npc_conversation",
                {
                    "campaign_id": campaign_id,
                    "action": "claim",
                    "conversation_id": conversation_id,
                    "data": {
                        "activation_ref": refreshed["activation"]["activation_ref"]
                    },
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "public-conversation-claim",
                },
            )
            revision = claimed["campaign_revision"]
            proposed = await call(
                "npc_conversation",
                {
                    "campaign_id": campaign_id,
                    "action": "propose",
                    "conversation_id": conversation_id,
                    "data": {
                        "activation_ref": claimed["activation_ref"],
                        "lease_id": claimed["lease_id"],
                        "context_receipt": claimed["context_receipt"],
                        "proposal": {
                            "schema_version": 1,
                            "activation_id": claimed["activation_id"],
                            "actor_runtime_id": claimed["actor_runtime_id"],
                            "private_intent": "Reveal only the observable gate mark.",
                            "utterance_segments": [
                                {
                                    "text": "I saw that mark on the sealed gate.",
                                    "content_mode": "nonfactual",
                                    "basis_refs": [],
                                    "targets": [],
                                }
                            ],
                            "visible_cues": ["The witness watches the key."],
                            "memory_candidates": [],
                        },
                    },
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "public-conversation-propose",
                },
            )
            revision = proposed["campaign_revision"]
            published = await call(
                "npc_conversation",
                {
                    "campaign_id": campaign_id,
                    "action": "publish",
                    "conversation_id": conversation_id,
                    "data": {
                        "proposal_id": proposed["proposal_id"],
                        "audience": {"scope": "public"},
                    },
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "public-conversation-publish",
                },
            )
            revision = published["campaign_revision"]
            closed = await call(
                "npc_conversation",
                {
                    "campaign_id": campaign_id,
                    "action": "close",
                    "conversation_id": conversation_id,
                    "data": {
                        "close_token": close_token,
                        "selected_proposal_ids": [proposed["proposal_id"]],
                    },
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "public-conversation-close",
                },
            )
            revision = closed["campaign_revision"]
            assert closed["status"] == "closed"

            abortable = await call(
                "npc_conversation",
                {
                    "campaign_id": campaign_id,
                    "action": "open",
                    "npc_actor_id": npc["id"],
                    "data": {
                        "interlocutors": {
                            "principal_ids": ["system:local"],
                            "publication_scopes": ["public"],
                        }
                    },
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "public-conversation-open-abort",
                },
            )
            revision = abortable["campaign_revision"]
            aborted = await call(
                "npc_conversation",
                {
                    "campaign_id": campaign_id,
                    "action": "abort",
                    "conversation_id": abortable["conversation"]["id"],
                    "data": {"close_token": abortable["close_token"]},
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "public-conversation-abort",
                },
            )
            assert aborted["status"] == "aborted"

    asyncio.run(exercise())


def test_campaign_setup_returns_the_initial_branch_guard_on_replay(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = _server(tmp_path)
        tool = next(item for item in await server.list_tools() if item.name == "campaign_setup")
        arguments = {
            "action": "create",
            "name": "Branch-ready campaign",
            "idempotency_key": "branch-ready-campaign",
        }

        async with Client(server, mode="2026-07-28") as client:
            created = await client.call_tool("campaign_setup", arguments)
            replayed = await client.call_tool("campaign_setup", arguments)

        assert not created.is_error, created.structured_content
        assert not replayed.is_error, replayed.structured_content
        assert replayed.structured_content == created.structured_content
        result = created.structured_content
        assert result["branch_id"] == server.runtime.branch_id(result["id"])
        Draft202012Validator(tool.output_schema).validate(result)

    asyncio.run(exercise())


def test_phase_transition_descriptions_preserve_exposure_contract(tmp_path: Path) -> None:
    async def exercise() -> None:
        tools = {tool.name: tool for tool in await _server(tmp_path).list_tools()}
        exposure = tools["exposure"].description
        phase = tools["game_phase"].description
        assert "Lobby" in exposure and "currently available" in exposure
        assert "game_phase(phase='play')" in exposure
        assert "same campaign-bound handle" in exposure
        assert "fails atomically" in exposure
        assert "reuse the campaign-bound exposure handle" in phase

    asyncio.run(exercise())


def test_model_repairable_errors_are_structured(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with Client(_server(tmp_path), mode="2026-07-28") as client:
            result = await client.call_tool("campaign_query", {"action": "get"})
            assert result.is_error is True
            assert result.structured_content == {
                "error": {
                    "code": "invalid_request",
                    "message": "campaign_id is required",
                    "retryable": False,
                    "recovery": "Correct the tool arguments before retrying.",
                }
            }

    asyncio.run(exercise())


def test_actor_change_results_match_the_advertised_output_schema(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = _server(tmp_path)
        campaign = server.runtime.campaign_create(
            name="Actor contract",
            principal_id="system:local",
            idempotency_key="actor-contract-campaign",
        )
        tool = next(item for item in await server.list_tools() if item.name == "actor_change")
        validator = Draft202012Validator(tool.output_schema)

        async with Client(server, mode="2026-07-28") as client:
            created = await client.call_tool(
                "actor_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "create",
                    "actor": {
                        "name": "Schema witness",
                        "type": "npc",
                        "summary": "Confirms the public result contract.",
                    },
                    "expected_revision": campaign["revision"],
                    "expected_branch_id": server.runtime.branch_id(campaign["id"]),
                    "idempotency_key": "actor-contract-create",
                },
            )
            assert not created.is_error, created.structured_content
            validator.validate(created.structured_content)

            actor = created.structured_content
            assert actor["character_type"] == "npc"
            updated = await client.call_tool(
                "actor_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "update",
                    "actor_id": actor["id"],
                    "actor": {"summary": "Still schema-valid after an update."},
                    "expected_actor_revision": actor["revision"],
                    "expected_revision": actor["campaign_revision"],
                    "expected_branch_id": server.runtime.branch_id(campaign["id"]),
                    "idempotency_key": "actor-contract-update",
                },
            )
            assert not updated.is_error, updated.structured_content
            validator.validate(updated.structured_content)

    asyncio.run(exercise())


def test_public_actor_change_type_alias_conflict_and_npc_eligibility(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = _server(tmp_path)
        runtime = server.runtime
        campaign = runtime.campaign_create(
            name="Actor type contract",
            principal_id="system:local",
            idempotency_key="actor-type-campaign",
        )
        profile = {
            "id": "profile.actor-type",
            "version": "1",
            "mechanics_level": 0,
            "capabilities": ["npc_conversation"],
            "authority": {
                "facilitator_roles": ["owner"],
                "audience_scopes": ["public"],
            },
            "actor_schema": {"type": "object"},
            "sources": [{"type": "self-authored", "citation": __file__}],
        }

        def state() -> tuple[int, str]:
            return runtime.campaigns.get(campaign["id"]).revision, runtime.branch_id(campaign["id"])

        revision, branch_id = state()
        runtime.profile_change(
            campaign["id"],
            action="create_draft",
            profile=profile,
            principal_id="system:local",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="actor-type-profile-draft",
        )
        revision, branch_id = state()
        runtime.profile_change(
            campaign["id"],
            action="finalize",
            profile_key="profile.actor-type@1",
            principal_id="system:local",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="actor-type-profile-finalize",
        )
        revision, branch_id = state()
        runtime.profile_change(
            campaign["id"],
            action="activate",
            profile_key="profile.actor-type@1",
            principal_id="system:local",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="actor-type-profile-activate",
        )
        revision, branch_id = state()
        runtime.set_phase(
            campaign["id"],
            phase="play",
            principal_id="system:local",
            expected_revision=revision,
            expected_branch_id=branch_id,
            idempotency_key="actor-type-play",
        )

        tools = {item.name: item for item in await server.list_tools()}
        actor_schema = tools["actor_change"].input_schema["properties"]["actor"]
        assert actor_schema["properties"]["name"]["type"] == "string"
        assert actor_schema["properties"]["type"]["type"] == "string"
        assert actor_schema["properties"]["character_type"]["type"] == "string"
        assert "character_type" in tools["actor_change"].description

        async with Client(server, mode="2026-07-28") as client:
            revision, branch_id = state()
            created = await client.call_tool(
                "actor_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "create",
                    "actor": {"name": "Public NPC", "type": "npc"},
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "actor-type-create",
                },
            )
            assert not created.is_error, created.structured_content
            npc = created.structured_content
            assert npc["character_type"] == "npc"
            assert npc["id"]

            revision, branch_id = state()
            alias_created = await client.call_tool(
                "actor_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "create",
                    "actor": {"name": "Alias NPC", "character_type": "persistent_npc"},
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "actor-type-alias-create",
                },
            )
            assert not alias_created.is_error, alias_created.structured_content
            alias_npc = alias_created.structured_content
            assert alias_npc["character_type"] == "persistent_npc"

            revision, branch_id = state()
            unchanged_revision = revision
            conflict = await client.call_tool(
                "actor_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "create",
                    "actor": {
                        "name": "Rejected actor",
                        "type": "pc",
                        "character_type": "npc",
                    },
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "actor-type-conflict",
                },
            )
            assert conflict.is_error is True
            assert "actor.type and actor.character_type must match" in (
                conflict.structured_content["error"]["message"]
            )
            assert runtime.campaigns.get(campaign["id"]).revision == unchanged_revision

            revision, branch_id = state()
            updated = await client.call_tool(
                "actor_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "update",
                    "actor_id": npc["id"],
                    "actor": {
                        "character_type": "npc",
                        "summary": "Updated through the output-compatible alias.",
                    },
                    "expected_actor_revision": npc["revision"],
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "actor-type-alias-update",
                },
            )
            assert not updated.is_error, updated.structured_content
            assert updated.structured_content["character_type"] == "npc"

            revision, branch_id = state()
            opened = await client.call_tool(
                "npc_conversation",
                {
                    "campaign_id": campaign["id"],
                    "action": "open",
                    "npc_actor_id": alias_npc["id"],
                    "data": {
                        "interlocutors": {
                            "principal_ids": ["system:local"],
                            "publication_scopes": ["public"],
                        }
                    },
                    "expected_revision": revision,
                    "expected_branch_id": branch_id,
                    "idempotency_key": "actor-type-conversation",
                },
            )
            assert not opened.is_error, opened.structured_content
            assert opened.structured_content["conversation"]["npc_actor_id"] == alias_npc["id"]

    asyncio.run(exercise())


def test_advertised_input_bounds_are_enforced_before_tool_execution(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with Client(_server(tmp_path), mode="2026-07-28") as client:
            result = await client.call_tool(
                "campaign_query", {"action": "list", "query": "x" * 257}
            )
            assert result.is_error is True
            assert result.structured_content is None
            assert len(result.content) == 1
            message = result.content[0].text
            assert "input validation error" in message
            assert "query must contain at most 256 characters" in message
            assert "x" * 64 not in message

    asyncio.run(exercise())
