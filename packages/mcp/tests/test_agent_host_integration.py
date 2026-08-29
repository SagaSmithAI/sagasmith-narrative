from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

SECRET = "real-agent-host-auth-context-secret-at-least-32-bytes"


def test_real_sagasmith_agent_respects_protocol_era_catalog_contract(tmp_path: Path) -> None:
    asyncio.run(_exercise_agent_host(tmp_path))


async def _exercise_agent_host(tmp_path: Path) -> None:
    pytest.importorskip("tiktoken", reason="run with SagaSmith-agent's Python environment")
    root = Path(__file__).parents[1]
    monorepo = root.parents[1]
    workspace = monorepo.parent
    agent_root = workspace / "SagaSmith-agent"
    sys.path.insert(0, str(agent_root))
    try:
        from nanobot.agent.tools.context import RequestContext, request_context
        from nanobot.agent.tools.mcp import connect_mcp_servers
        from nanobot.agent.tools.registry import ToolRegistry
        from nanobot.config.schema import MCPServerConfig
        from nanobot.session.manager import SessionManager

        env = {
            "SAGASMITH_NARRATIVE_MCP_HOME": str(tmp_path / "home"),
            "SAGASMITH_AUTH_CONTEXT_SECRET": SECRET,
            "PYTHONPATH": os.pathsep.join(
                [
                    str(root / "src"),
                    str(monorepo / "packages" / "domain" / "src"),
                    str(workspace / "sagasmith-core" / "src"),
                ]
            ),
        }
        registry = ToolRegistry()
        narrative_python = (
            monorepo / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
        assert narrative_python.is_file(), "install Narrative MCP dev environment first"
        modern_agent = "protocol_mode" in MCPServerConfig.model_fields
        server_options = {
            "command": str(narrative_python),
            "args": ["-m", "sagasmith_narrative_mcp.server"],
            "env": env,
            "cwd": str(root),
            "enabled_tools": ["*"],
            "inject_principal": True,
            "auth_context_secret": SECRET,
        }
        if modern_agent:
            server_options.update(
                {
                    "delegation_secret": SECRET,
                    "target_service": "sagasmith-narrative-mcp",
                    "authorization_audience": "sagasmith-narrative-mcp",
                    "protocol_mode": "2026-07-28",
                }
            )
        connections = await connect_mcp_servers(
            {"narrative": MCPServerConfig(**server_options)},
            registry,
            session_store=SessionManager(tmp_path / "sessions"),
        )
        try:
            exposure = registry.get("mcp_narrative_exposure")
            assert exposure is not None
            initial_names = tuple(registry.tool_names)
            if modern_agent:
                assert len(initial_names) == 31
                assert tuple(sorted(initial_names)) == initial_names
                assert "mcp_narrative_campaign_setup" in initial_names
            else:
                assert "mcp_narrative_campaign_setup" not in initial_names
            with request_context(
                RequestContext(
                    channel="discord",
                    chat_id="table-1",
                    sender_id="member-1",
                    actor_principal="user:member-1",
                    conversation_principal="group:table-1",
                    session_key="discord:table-1",
                )
            ):
                opened = await exposure.execute(action="open")
                assert not opened.is_error, str(opened)
                first_handle = opened.structured_content["exposure_handle"]
                set_arguments = {"action": "set", "add_tool_ids": ["campaign_setup"]}
                if modern_agent:
                    set_arguments["exposure_handle"] = first_handle
                selected = await exposure.execute(**set_arguments)
                assert not selected.is_error, str(selected)
                assert "campaign_setup" in selected.structured_content["loaded_tools"]
            if modern_agent:
                assert tuple(registry.tool_names) == initial_names
            else:
                assert "mcp_narrative_campaign_setup" in registry.tool_names
            with request_context(
                RequestContext(
                    channel="discord",
                    chat_id="table-1",
                    sender_id="member-2",
                    actor_principal="user:member-2",
                    conversation_principal="group:table-1",
                    session_key="discord:table-1",
                )
            ):
                reopened = await exposure.execute(action="open")
                assert not reopened.is_error, str(reopened)
                assert reopened.audit_receipt["conversation_principal"] == ("discord:group:table-1")
                principal_key = "requester_principal" if modern_agent else "actor_principal"
                assert reopened.audit_receipt[principal_key] == "discord:user:member-2"
                second_handle = reopened.structured_content["exposure_handle"]
                assert second_handle != first_handle
                set_arguments = {"action": "set", "add_tool_ids": ["campaign_setup"]}
                if modern_agent:
                    assert reopened.audit_receipt["target_service"] == ("sagasmith-narrative-mcp")
                    assert reopened.audit_receipt["authorized_audience"] == (
                        "sagasmith-narrative-mcp"
                    )
                    set_arguments["exposure_handle"] = second_handle
                selected = await exposure.execute(**set_arguments)
                assert not selected.is_error, str(selected)
            if modern_agent:
                assert tuple(registry.tool_names) == initial_names
            else:
                assert "mcp_narrative_campaign_setup" in registry.tool_names
        finally:
            for connection in connections.values():
                await connection.aclose()
    finally:
        sys.path.remove(str(agent_root))
