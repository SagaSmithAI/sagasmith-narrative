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
