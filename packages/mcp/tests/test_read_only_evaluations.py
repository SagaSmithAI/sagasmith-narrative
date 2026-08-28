from __future__ import annotations

import asyncio
from pathlib import Path
from xml.etree import ElementTree

from sagasmith_core.database import sqlite_database_url

from sagasmith_narrative_mcp.config import McpConfig
from sagasmith_narrative_mcp.server import create_server


async def _call(server, name: str, arguments: dict | None = None):
    result = (await server.call_tool(name, arguments or {})).structured_content
    if isinstance(result, dict):
        return result.get("result", result)
    return result


def _fixture(server) -> None:
    for name, description in (
        ("Ash Harbor Archive", "A completed coastal mystery archive."),
        ("Echo Manor Voices", "A completed manor voice archive."),
        ("Moss Road Seasons", "A completed seasonal road chronicle."),
    ):
        server.runtime.campaign_create(
            name=name,
            description=description,
            principal_id="system:local",
            idempotency_key=f"evaluation:{name}",
        )


async def _campaigns(server) -> list[dict]:
    values: list[dict] = []
    cursor = None
    while True:
        page = await _call(
            server,
            "campaign_query",
            {"action": "list", "query": "completed", "limit": 1, "cursor": cursor},
        )
        values.extend(page["campaigns"])
        cursor = page["next_cursor"]
        if cursor is None:
            return values


async def _solve(server) -> list[str]:
    campaigns = await _campaigns(server)
    manor = await _call(
        server, "campaign_query", {"action": "list", "query": "manor voice", "limit": 10}
    )
    first_page = await _call(
        server, "campaign_query", {"action": "list", "query": "completed", "limit": 2}
    )
    capabilities = await _call(server, "server_capabilities")
    catalog = await server.list_tools()
    campaign_tool = next(tool for tool in catalog if tool.name == "campaign_query")
    return [
        str(len(campaigns)),
        campaigns[-1]["name"],
        manor["campaigns"][0]["slug"],
        str(first_page["next_cursor"]),
        capabilities["authoritative_contract"]["protocols"]["2026-07-28"],
        str(capabilities["catalog_cache"]["ttl_ms"]),
        next(
            item
            for item in capabilities["optional_phases"]
            if item not in capabilities["base_phases"]
        ),
        capabilities["system_id"],
        catalog[0].name,
        str(campaign_tool.input_schema["properties"]["limit"]["maximum"]),
    ]


def test_builder_evaluations_are_independent_read_only_and_actually_solved(
    tmp_path: Path,
) -> None:
    evaluation_path = Path(__file__).parents[1] / "evaluations" / "read_only.xml"
    root = ElementTree.parse(evaluation_path).getroot()
    pairs = root.findall("qa_pair")
    assert len(pairs) >= 10
    questions = [str(pair.findtext("question") or "").strip() for pair in pairs]
    answers = [str(pair.findtext("answer") or "").strip() for pair in pairs]
    assert len(questions) == len(set(questions))
    assert all(questions) and all(answers)

    async def exercise() -> None:
        server = create_server(
            McpConfig(database_url=sqlite_database_url(tmp_path / "evaluations.db"))
        )
        _fixture(server)
        catalog = {tool.name: tool for tool in await server.list_tools()}
        for name in ("campaign_query", "server_capabilities"):
            annotations = catalog[name].annotations
            assert annotations is not None
            assert annotations.read_only_hint is True
            assert annotations.idempotent_hint is True
        assert await _solve(server) == answers

    asyncio.run(exercise())
