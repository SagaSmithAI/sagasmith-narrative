from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

from mcp import Client
from sagasmith_core.access import LOCAL_SYSTEM_PRINCIPAL_ID
from sagasmith_core.database import sqlite_database_url

from sagasmith_narrative_mcp.config import McpConfig
from sagasmith_narrative_mcp.server import create_server


async def _call(client: Client, name: str, arguments: dict | None = None):
    result = (await client.call_tool(name, arguments or {})).structured_content
    if isinstance(result, dict):
        return result.get("result", result)
    return result


def _fixture(server) -> None:
    campaigns: dict[str, dict] = {}
    for name, description in (
        ("Ash Harbor Archive", "A completed coastal mystery archive."),
        ("Echo Manor Voices", "A completed manor voice archive."),
        ("Moss Road Seasons", "A completed seasonal road chronicle."),
    ):
        campaigns[name] = server.runtime.campaign_create(
            name=name,
            description=description,
            principal_id=LOCAL_SYSTEM_PRINCIPAL_ID,
            idempotency_key=f"evaluation:{name}",
        )

    rosters = {
        "Ash Harbor Archive": [
            ("Alice North", "pc", "A patient cartographer."),
            ("Borin Shaw", "npc", "A veteran harbor guide."),
            ("Lumen Grey", "npc", "Keeper of an unreliable lantern."),
            ("Mira Cole", "npc", "The tide-record archive custodian."),
            ("Glass Tide", "creature", "An unnatural tide beneath the quay."),
        ],
        "Echo Manor Voices": [
            ("Juniper Vale", "pc", "An oral historian cataloguing the voices."),
            ("Rhea Ward", "pc", "A conservator of damaged recordings."),
            ("Nyx Marsh", "npc", "Custodian of the sealed observatory."),
            ("Orin Bell", "npc", "The manor's retired groundskeeper."),
        ],
        "Moss Road Seasons": [
            ("Zora Vale", "pc", "A surveyor following the old road."),
            ("Tamsin Reed", "pc", "A botanist mapping seasonal changes."),
            ("Edda Pike", "npc", "The last keeper of the moss-road milepost."),
        ],
    }
    for campaign_name, roster in rosters.items():
        campaign_id = campaigns[campaign_name]["id"]
        for name, actor_type, summary in roster:
            server.runtime.actor_create(
                campaign_id,
                principal_id=LOCAL_SYSTEM_PRINCIPAL_ID,
                idempotency_key=f"evaluation:{campaign_name}:{name}",
                actor={"name": name, "type": actor_type, "summary": summary},
            )


async def _completed_campaigns(client: Client) -> list[dict]:
    values: list[dict] = []
    cursor = None
    while True:
        page = await _call(
            client,
            "campaign_query",
            {"action": "list", "query": "completed", "limit": 1, "cursor": cursor},
        )
        values.extend(page["campaigns"])
        cursor = page["next_cursor"]
        if cursor is None:
            return values


async def _actor_details(client: Client, campaign_id: str) -> list[dict]:
    roster: list[dict] = []
    cursor = None
    while True:
        page = await _call(
            client,
            "actor_query",
            {"campaign_id": campaign_id, "limit": 2, "cursor": cursor},
        )
        assert "actors" in page, page
        roster.extend(page["actors"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    return [
        await _call(
            client,
            "actor_query",
            {"campaign_id": campaign_id, "actor_id": actor["id"]},
        )
        for actor in roster
    ]


async def _explore_archive(client: Client) -> tuple[list[dict], dict[str, list[dict]]]:
    campaigns = await _completed_campaigns(client)
    rosters = {
        campaign["id"]: await _actor_details(client, campaign["id"])
        for campaign in campaigns
    }
    return campaigns, rosters


async def _solve_pair(client: Client, index: int) -> str:
    # Every evaluation is solved from a fresh bounded traversal. No question
    # depends on an answer or cached exploration from another question.
    campaigns, rosters = await _explore_archive(client)
    actors = [
        {**actor, "campaign_id": campaign["id"]}
        for campaign in campaigns
        for actor in rosters[campaign["id"]]
    ]
    campaign_by_id = {campaign["id"]: campaign for campaign in campaigns}
    if index == 0:
        return max(campaigns, key=lambda item: len(rosters[item["id"]]))["name"]
    if index == 1:
        return max(actor["name"] for actor in actors if actor["character_type"] == "pc")
    if index == 2:
        creature = next(
            actor
            for actor in actors
            if actor["character_type"] == "creature" and "unnatural tide" in actor["summary"]
        )
        return campaign_by_id[creature["campaign_id"]]["name"]
    if index == 3:
        return str(sum(actor["character_type"] == "npc" for actor in actors))
    if index == 4:
        return next(
            campaign["name"]
            for campaign in campaigns
            if sum(actor["character_type"] == "pc" for actor in rosters[campaign["id"]])
            == sum(actor["character_type"] == "npc" for actor in rosters[campaign["id"]])
        )
    if index == 5:
        campaign = next(
            item for item in campaigns if "seasonal road chronicle" in item["description"]
        )
        return str(len({actor["character_type"] for actor in rosters[campaign["id"]]}))
    if index == 6:
        return next(actor["name"] for actor in actors if "sealed observatory" in actor["summary"])
    if index == 7:
        return str(len(actors))
    if index == 8:
        campaign = next(item for item in campaigns if len(rosters[item["id"]]) == 3)
        resolved = await _call(
            client,
            "campaign_query",
            {"action": "get", "campaign_id": campaign["id"]},
        )
        return resolved["slug"]
    if index == 9:
        return Counter(actor["character_type"] for actor in actors).most_common(1)[0][0]
    raise AssertionError(f"unknown evaluation index: {index}")


def test_builder_evaluations_are_independent_complex_read_only_and_actually_solved(
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
    assert all(
        any(term in question.casefold() for term in ("every", "complete", "all"))
        for question in questions
    )

    async def exercise() -> None:
        server = create_server(
            McpConfig(database_url=sqlite_database_url(tmp_path / "evaluations.db"))
        )
        _fixture(server)
        async with Client(server, mode="2026-07-28") as client:
            catalog = {tool.name: tool for tool in (await client.list_tools()).tools}
            for name in ("campaign_query", "actor_query"):
                annotations = catalog[name].annotations
                assert annotations is not None
                assert annotations.read_only_hint is True
                assert annotations.idempotent_hint is True
            solved = [await _solve_pair(client, index) for index in range(len(pairs))]
            assert solved == answers

    asyncio.run(exercise())
