from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from mcp import Client, StdioServerParameters

from sagasmith_narrative_mcp.policies import CORE_TOOLS


def _environment(tmp_path: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "SAGASMITH_NARRATIVE_MCP_HOME": str(tmp_path / "home"),
            "SAGASMITH_NARRATIVE_MCP_BOUND_PRINCIPAL_ID": "matrix:local-user",
        }
    )
    return environment


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_port(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"Narrative MCP exited with {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError("Narrative MCP streamable HTTP endpoint did not start")


@contextmanager
def _http_server(tmp_path: Path) -> Iterator[str]:
    port = _unused_loopback_port()
    environment = _environment(tmp_path)
    environment.update(
        {
            "SAGASMITH_NARRATIVE_MCP_TRANSPORT": "streamable-http",
            "SAGASMITH_NARRATIVE_MCP_HTTP_HOST": "127.0.0.1",
            "SAGASMITH_NARRATIVE_MCP_HTTP_PORT": str(port),
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "sagasmith_narrative_mcp.server"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_port(port, process)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _stdio_server(tmp_path: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "sagasmith_narrative_mcp.server"],
        cwd=Path(__file__).parents[1],
        env=_environment(tmp_path),
    )


@pytest.mark.parametrize("transport", ["stdio", "streamable-http"])
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
def test_real_protocol_era_transport_matrix(tmp_path: Path, transport: str, mode: str) -> None:
    async def exercise(target) -> None:
        async with Client(target, mode=mode) as client:
            listed = await client.list_tools(cache_mode="reload")
            names = [tool.name for tool in listed.tools]
            if mode == "2026-07-28":
                assert client.protocol_version == "2026-07-28"
                assert names == sorted(names)
                assert len(names) == 29
                assert listed.ttl_ms == 300_000
                assert listed.cache_scope == "private"
                assert listed.meta["io.modelcontextprotocol/serverInfo"]["name"] == (
                    "SagaSmith Narrative"
                )
            else:
                assert client.protocol_version != "2026-07-28"
                assert set(names) == set(CORE_TOOLS)

            invalid = await client.call_tool("campaign_query", {"action": "get"})
            assert invalid.is_error is True
            assert invalid.structured_content["error"]["code"] == "invalid_request"
            assert invalid.structured_content["error"]["retryable"] is False

    case_home = tmp_path / f"{transport}-{mode}"
    if transport == "stdio":
        asyncio.run(exercise(_stdio_server(case_home)))
    else:
        with _http_server(case_home) as url:
            asyncio.run(exercise(url))
