from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

SECRET = "real-agent-host-auth-context-secret-at-least-32-bytes"


def test_real_sagasmith_agent_registry_refreshes_native_tools(tmp_path: Path) -> None:
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
        narrative_python = monorepo / ".venv" / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        assert narrative_python.is_file(), "install Narrative MCP dev environment first"
        connections = await connect_mcp_servers(
            {
                "narrative": MCPServerConfig(
                    command=str(narrative_python),
                    args=["-m", "sagasmith_narrative_mcp.server"],
                    env=env,
                    cwd=str(root),
                    enabled_tools=["*"],
                    inject_principal=True,
                    auth_context_secret=SECRET,
                )
            },
            registry,
            session_store=SessionManager(tmp_path / "sessions"),
        )
        try:
            assert "mcp_narrative_campaign_setup" not in registry.tool_names
            exposure = registry.get("mcp_narrative_exposure")
            assert exposure is not None
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
                await exposure.execute(action="open")
                await exposure.execute(action="set", add_tool_ids=["campaign_setup"])
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
                assert reopened.audit_receipt["actor_principal"] == "discord:user:member-2"
                assert reopened.audit_receipt["conversation_principal"] == (
                    "discord:group:table-1"
                )
                assert "mcp_narrative_campaign_setup" not in registry.tool_names
                await exposure.execute(action="set", add_tool_ids=["campaign_setup"])
            assert "mcp_narrative_campaign_setup" in registry.tool_names
        finally:
            for connection in connections.values():
                await connection.aclose()
    finally:
        sys.path.remove(str(agent_root))
