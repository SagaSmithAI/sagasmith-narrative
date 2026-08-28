from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SECRET = "auth-bridge-real-integration-secret-at-least-32-bytes"


def test_external_host_auth_bridge_preserves_dynamic_native_mcp(tmp_path: Path) -> None:
    asyncio.run(_exercise_bridge(tmp_path))


async def _exercise_bridge(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    monorepo = root.parents[1]
    workspace = monorepo.parent
    agent_root = workspace / "SagaSmith-agent"
    agent_python = (
        agent_root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    narrative_python = (
        monorepo / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    if not agent_python.is_file() or not narrative_python.is_file():
        pytest.skip("Agent and Narrative development environments are required")

    config_path = tmp_path / "bridge.json"
    context_path = tmp_path / "context.json"
    secret_path = tmp_path / "secret"
    config_path.write_text(
        json.dumps(
            {
                "type": "stdio",
                "command": str(narrative_python),
                "args": ["-m", "sagasmith_narrative_mcp.server"],
                "cwd": str(root),
                "env": {
                    "SAGASMITH_NARRATIVE_MCP_HOME": str(tmp_path / "narrative-home"),
                    "SAGASMITH_AUTH_CONTEXT_SECRET": SECRET,
                    "PYTHONPATH": os.pathsep.join(
                        [
                            str(root / "src"),
                            str(monorepo / "packages" / "domain" / "src"),
                            str(workspace / "sagasmith-core" / "src"),
                        ]
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    context_path.write_text(
        json.dumps(
            {
                "host": "openclaw",
                "channel": "discord",
                "actor_principal": "discord:user:alice",
                "conversation_principal": "discord:group:table-1",
                "session_id": "openclaw:discord:table-1",
                "tenant_id": "guild-1",
            }
        ),
        encoding="utf-8",
    )
    secret_path.write_text(SECRET, encoding="utf-8")

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(agent_root)
    params = StdioServerParameters(
        command=str(agent_python),
        args=[
            "-m",
            "nanobot.sagasmith_hosts.bridge",
            "--config",
            str(config_path),
            "--context",
            str(context_path),
            "--secret-file",
            str(secret_path),
        ],
        cwd=agent_root,
        env=environment,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            initial = {tool.name for tool in (await session.list_tools()).tools}
            assert "exposure" in initial
            assert "campaign_setup" not in initial

            opened = await session.call_tool(
                "exposure",
                {"action": "open", "principal_id": "model:forged-owner"},
            )
            assert not opened.is_error
            receipt = opened.content[0].meta["sagasmith_auth_context_receipt"]
            assert receipt["actor_principal"] == "discord:user:alice"
            assert receipt["conversation_principal"] == "discord:group:table-1"

            loaded = await session.call_tool(
                "exposure",
                {
                    "action": "set",
                    "principal_id": "model:forged-owner",
                    "add_tool_ids": ["campaign_setup"],
                },
            )
            assert not loaded.is_error
            refreshed = {tool.name for tool in (await session.list_tools()).tools}
            assert "campaign_setup" in refreshed

            resources = await session.list_resources()
            prompts = await session.list_prompts()
            if resources.resources:
                assert (await session.read_resource(resources.resources[0].uri)).contents
            if prompts.prompts:
                assert await session.get_prompt(prompts.prompts[0].name)
