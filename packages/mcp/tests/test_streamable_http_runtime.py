from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import TextIO

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def _unused_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_port(port: int, process: subprocess.Popen[str], output: TextIO) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output.flush()
            log = Path(output.name).read_text(encoding="utf-8", errors="replace")
            raise AssertionError(
                f"Narrative MCP exited before startup ({process.returncode}):\n{log}"
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError("Narrative MCP streamable HTTP endpoint did not start")


def _value(result):
    assert not result.is_error, result.content
    return json.loads(result.content[0].text)


def test_real_streamable_http_uses_the_authoritative_contract(tmp_path: Path) -> None:
    port = _unused_loopback_port()
    environment = os.environ.copy()
    environment.update(
        {
            "SAGASMITH_NARRATIVE_MCP_TRANSPORT": "streamable-http",
            "SAGASMITH_NARRATIVE_MCP_HTTP_HOST": "127.0.0.1",
            "SAGASMITH_NARRATIVE_MCP_HTTP_PORT": str(port),
            "SAGASMITH_NARRATIVE_MCP_HOME": str(tmp_path / "home"),
            "SAGASMITH_NARRATIVE_MCP_BOUND_PRINCIPAL_ID": "local-contract-user",
        }
    )
    output = (tmp_path / "narrative-mcp.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "sagasmith_narrative_mcp.server"],
        env=environment,
        stdout=output,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_port(port, process, output)

        async def exercise() -> None:
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as streams:
                read, write = streams
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    initial_tools = await session.list_tools()
                    schemas = {item.name: item.input_schema for item in initial_tools.tools}
                    assert "server_capabilities" in schemas
                    assert "exposure" in schemas

                    capabilities = _value(await session.call_tool("server_capabilities", {}))
                    contract = capabilities["authoritative_contract"]
                    assert contract["schema"] == "sagasmith.authoritative-mcp/v2"
                    assert contract["transports"] == ["stdio", "streamable-http"]
                    assert contract["shared_handlers"] is True
                    assert capabilities["identity_mode"] == "process_bound_principal"

                    error = await session.call_tool("campaign_query", {"action": "get"})
                    assert error.is_error is True
                    assert "campaign_id is required" in error.content[0].text

                    opened = _value(
                        await session.call_tool(
                            "exposure",
                            {"action": "open", "principal_id": "model:forged"},
                        )
                    )
                    assert opened["principal_id"] == "local-contract-user"
                    _value(
                        await session.call_tool(
                            "exposure",
                            {"action": "set", "add_tool_ids": ["campaign_setup"]},
                        )
                    )
                    create_arguments = {
                        "action": "create",
                        "name": "HTTP Narrative",
                        "idempotency_key": "http-create",
                        "principal_id": "model:forged",
                    }
                    created = _value(await session.call_tool("campaign_setup", create_arguments))
                    replayed = _value(await session.call_tool("campaign_setup", create_arguments))
                    assert replayed == created
                    campaign_id = created["id"]
                    _value(
                        await session.call_tool(
                            "exposure", {"action": "open", "campaign_id": campaign_id}
                        )
                    )
                    _value(
                        await session.call_tool(
                            "exposure",
                            {
                                "action": "set",
                                "add_tool_ids": ["branch_query", "profile_change"],
                            },
                        )
                    )
                    campaign = _value(
                        await session.call_tool(
                            "campaign_query", {"action": "get", "campaign_id": campaign_id}
                        )
                    )
                    branch = _value(
                        await session.call_tool("branch_query", {"campaign_id": campaign_id})
                    )["branches"][0]
                    stale = await session.call_tool(
                        "profile_change",
                        {
                            "campaign_id": campaign_id,
                            "action": "create_draft",
                            "profile": {
                                "id": "profile.http",
                                "version": "1",
                                "title": "HTTP",
                                "mechanics_level": 0,
                                "sources": [{"kind": "original", "ref": "test"}],
                            },
                            "expected_revision": campaign["revision"] + 1,
                            "expected_branch_id": branch["id"],
                            "idempotency_key": "http-stale",
                        },
                    )
                    assert stale.is_error is True
                    assert "revision" in stale.content[0].text.casefold()

        asyncio.run(exercise())
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        output.close()


def test_non_loopback_streamable_http_is_rejected(monkeypatch) -> None:
    from sagasmith_narrative_mcp import server

    monkeypatch.setenv("SAGASMITH_NARRATIVE_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("SAGASMITH_NARRATIVE_MCP_HTTP_HOST", "0.0.0.0")

    try:
        server.main()
    except ValueError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("non-loopback Narrative HTTP bind was accepted")
