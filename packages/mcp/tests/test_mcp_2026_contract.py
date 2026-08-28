from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_core.auth_context import (
    AUTH_CONTEXT_DELEGATION_SCHEMA,
    AUTH_CONTEXT_META_KEY,
    sign_delegated_auth_context,
)
from sagasmith_core.database import sqlite_database_url

from sagasmith_narrative_mcp.config import McpConfig
from sagasmith_narrative_mcp.server import create_server

SECRET = "narrative-modern-auth-context-secret-32-bytes"
SERVICE = "sagasmith-narrative-mcp"


def delegated_meta(*, nonce: str, operation: str, target_service: str = SERVICE):
    return {
        AUTH_CONTEXT_META_KEY: sign_delegated_auth_context(
            secret=SECRET,
            issuer="sagasmith-web",
            target_service=target_service,
            caller_principal="workload:hosted-agent",
            workload_identity="deployment:sagasmith-agent/test",
            requester_principal="user:authorized",
            resource_owner_principal="user:authorized",
            acting_host_principal="host:facilitator",
            acting_character_id="actor:hero",
            authorized_audience=SERVICE,
            allowed_operations=[operation],
            conversation_principal="room:narrative:test",
            campaign_id="campaign:scope",
            room_turn_id="turn:narrative-modern",
            base_revision=0,
            nonce=nonce,
        )
    }


def modern_context(server, metadata, *, headers: dict[str, str] | None = None) -> Context:
    request_context = SimpleNamespace(
        meta=metadata,
        protocol_version="2026-07-28",
        request=SimpleNamespace(headers=headers),
    )
    return Context(
        request_context=request_context,
        mcp_server=server,
        subscriptions=server._subscriptions,
    )


def test_modern_identity_audience_revision_and_trace_are_request_scoped(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(
            McpConfig(
                database_url=sqlite_database_url(tmp_path / "modern.db"),
                auth_context_secret=SECRET,
            )
        )
        context = modern_context(
            server,
            delegated_meta(nonce="accepted", operation="campaign_query"),
            headers={"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
        )
        result = await server.call_tool(
            "campaign_query", {"action": "list", "principal_id": "model:forged"}, context
        )
        assert not result.is_error
        receipt = result.content[0].meta["sagasmith_auth_context_receipt"]
        assert receipt["schema"] == AUTH_CONTEXT_DELEGATION_SCHEMA
        assert receipt["requester_principal"] == "user:authorized"
        assert receipt["target_service"] == SERVICE
        assert result.meta["sagasmith_trace_context"]["traceparent"].startswith("00-")

        wrong = modern_context(
            server,
            delegated_meta(
                nonce="wrong-service",
                operation="campaign_query",
                target_service="sagasmith-dnd-mcp",
            ),
        )
        with pytest.raises(ToolError, match="target service"):
            await server.call_tool("campaign_query", {"action": "list"}, wrong)

        stale = modern_context(server, delegated_meta(nonce="stale", operation="campaign_query"))
        with pytest.raises(ToolError, match="base revision is stale"):
            await server.call_tool("campaign_query", {"action": "list", "base_revision": 1}, stale)

    asyncio.run(exercise())


def test_modern_catalog_is_stable_sorted_annotated_and_schema_backed(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(McpConfig(database_url=sqlite_database_url(tmp_path / "catalog.db")))
        tools = await server.list_tools()
        names = [tool.name for tool in tools]
        assert names == sorted(names)
        assert len(names) == len(set(names))
        assert all(tool.output_schema is not None for tool in tools)
        assert all(tool.annotations is not None for tool in tools)
        assert all(tool.annotations.read_only_hint is not None for tool in tools)
        assert all(tool.annotations.destructive_hint is not None for tool in tools)
        assert all(tool.annotations.idempotent_hint is not None for tool in tools)
        assert all(tool.annotations.open_world_hint is False for tool in tools)

        server.registry.open("legacy:side-effect", "system:local", None, "lobby")
        assert [tool.name for tool in await server.list_tools()] == names

    asyncio.run(exercise())


def test_modern_exposure_handle_is_explicit_guidance_not_catalog_state(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(
            McpConfig(
                database_url=sqlite_database_url(tmp_path / "handle.db"),
                auth_context_secret=SECRET,
            )
        )
        baseline = [tool.name for tool in await server.list_tools()]
        opened = (
            await server.call_tool(
                "exposure",
                {"action": "open", "principal_id": "model:forged"},
                modern_context(server, delegated_meta(nonce="handle-open", operation="exposure")),
            )
        ).structured_content
        assert opened["catalog_effect"] == "guidance_only"
        assert opened["ttl_ms"] > 0
        handle = opened["exposure_handle"]

        current = (
            await server.call_tool(
                "exposure",
                {
                    "action": "get",
                    "exposure_handle": handle,
                    "principal_id": "model:forged-again",
                },
                modern_context(server, delegated_meta(nonce="handle-get", operation="exposure")),
            )
        ).structured_content
        assert current["exposure_id"] == handle
        assert current["principal_id"] == "user:authorized"
        assert [tool.name for tool in await server.list_tools()] == baseline

    asyncio.run(exercise())


def test_campaign_query_is_filtered_and_bounded(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(
            McpConfig(database_url=sqlite_database_url(tmp_path / "pagination.db"))
        )
        for index in range(3):
            server.runtime.campaign_create(
                name=f"Harbor Chronicle {index}",
                principal_id="system:local",
                idempotency_key=f"campaign-{index}",
            )
        first = (
            await server.call_tool(
                "campaign_query", {"action": "list", "query": "harbor", "limit": 2}
            )
        ).structured_content
        assert len(first["campaigns"]) == 2
        assert first["next_cursor"] == "p:2"
        second = (
            await server.call_tool(
                "campaign_query",
                {
                    "action": "list",
                    "query": "harbor",
                    "limit": 2,
                    "cursor": first["next_cursor"],
                },
            )
        ).structured_content
        assert len(second["campaigns"]) == 1
        assert second["next_cursor"] is None

    asyncio.run(exercise())
