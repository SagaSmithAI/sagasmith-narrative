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
        assert len(tools) == 29
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
