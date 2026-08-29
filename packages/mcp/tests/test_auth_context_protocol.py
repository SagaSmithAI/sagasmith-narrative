from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sagasmith_core.auth_context import AUTH_CONTEXT_META_KEY, sign_auth_context
from sagasmith_core.database import sqlite_database_url

from sagasmith_narrative_mcp.config import McpConfig

SECRET = "test-auth-context-secret-with-at-least-32-bytes"


def _meta(*, actor: str, nonce: str, authorization_epoch: int = 0) -> dict[str, object]:
    return {
        AUTH_CONTEXT_META_KEY: sign_auth_context(
            secret=SECRET,
            host="test-host",
            channel="discord",
            actor_principal=actor,
            conversation_principal="discord:group:shared-room",
            session_id="test-session",
            authorization_epoch=authorization_epoch,
            nonce=nonce,
        )
    }


def test_short_auth_context_secret_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SAGASMITH_NARRATIVE_MCP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SAGASMITH_AUTH_CONTEXT_SECRET", "short")

    with pytest.raises(ValueError, match="at least 32 bytes"):
        McpConfig.from_environment()


def test_local_proposal_signing_secret_is_stable_and_private_to_mcp_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SAGASMITH_NARRATIVE_MCP_HOME", str(home))
    monkeypatch.delenv("SAGASMITH_AUTH_CONTEXT_SECRET", raising=False)
    monkeypatch.delenv("SAGASMITH_NARRATIVE_MCP_PROPOSAL_SECRET", raising=False)

    first = McpConfig.from_environment()
    second = McpConfig.from_environment()

    assert first.proposal_attestation_secret == second.proposal_attestation_secret
    assert first.proposal_attestation_secret
    assert len(first.proposal_attestation_secret.encode("utf-8")) >= 32
    assert (home / "proposal-signing.key").read_text(encoding="utf-8").strip() == (
        first.proposal_attestation_secret
    )


def test_explicit_file_database_gets_stable_adjacent_proposal_key(tmp_path: Path) -> None:
    database_url = sqlite_database_url(tmp_path / "explicit.db")
    first = McpConfig(database_url=database_url).resolved_proposal_attestation_secret()
    second = McpConfig(database_url=database_url).resolved_proposal_attestation_secret()

    assert first == second
    assert (tmp_path / "explicit.db.proposal-signing.key").is_file()


def test_remote_database_requires_explicit_proposal_secret() -> None:
    with pytest.raises(ValueError, match="proposal attestation secret"):
        McpConfig(
            database_url="postgresql+psycopg://db.example/narrative"
        ).resolved_proposal_attestation_secret()


def test_environment_explicit_sqlite_uses_database_key_across_two_homes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database_path = tmp_path / "shared" / "explicit.db"
    monkeypatch.setenv(
        "SAGASMITH_NARRATIVE_MCP_DATABASE_URL", sqlite_database_url(database_path)
    )
    monkeypatch.delenv("SAGASMITH_AUTH_CONTEXT_SECRET", raising=False)
    monkeypatch.delenv("SAGASMITH_NARRATIVE_MCP_PROPOSAL_SECRET", raising=False)

    values = []
    for home_name in ("host-a", "host-b"):
        monkeypatch.setenv("SAGASMITH_NARRATIVE_MCP_HOME", str(tmp_path / home_name))
        config = McpConfig.from_environment()
        assert config.proposal_attestation_secret is None
        values.append(config.resolved_proposal_attestation_secret())

    assert values[0] == values[1]
    assert (tmp_path / "shared" / "explicit.db.proposal-signing.key").is_file()
    assert not (tmp_path / "host-a" / "proposal-signing.key").exists()
    assert not (tmp_path / "host-b" / "proposal-signing.key").exists()


def test_environment_remote_database_without_shared_secret_fails_across_two_homes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "SAGASMITH_NARRATIVE_MCP_DATABASE_URL",
        "postgresql+psycopg://db.example/narrative",
    )
    monkeypatch.delenv("SAGASMITH_AUTH_CONTEXT_SECRET", raising=False)
    monkeypatch.delenv("SAGASMITH_NARRATIVE_MCP_PROPOSAL_SECRET", raising=False)

    for home_name in ("host-a", "host-b"):
        home = tmp_path / home_name
        monkeypatch.setenv("SAGASMITH_NARRATIVE_MCP_HOME", str(home))
        config = McpConfig.from_environment()
        assert config.proposal_attestation_secret is None
        with pytest.raises(ValueError, match="proposal attestation secret"):
            config.resolved_proposal_attestation_secret()
        assert not (home / "proposal-signing.key").exists()


def test_stdio_rejects_missing_tampered_and_replayed_auth_context(tmp_path: Path) -> None:
    async def exercise() -> None:
        env = dict(os.environ)
        env.update(
            {
                "SAGASMITH_NARRATIVE_MCP_HOME": str(tmp_path / "home"),
                "SAGASMITH_AUTH_CONTEXT_SECRET": SECRET,
            }
        )
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "sagasmith_narrative_mcp.server"],
            cwd=Path(__file__).parents[1],
            env=env,
        )
        arguments = {"action": "open", "principal_id": "discord:user:member-1"}
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                missing = await session.call_tool("exposure", arguments)
                assert missing.is_error

                tampered = await session.call_tool(
                    "exposure",
                    arguments,
                    meta=_meta(actor="discord:user:member-2", nonce="tampered"),
                )
                assert tampered.is_error

                metadata = _meta(actor="discord:user:member-1", nonce="accepted")
                accepted = await session.call_tool("exposure", arguments, meta=metadata)
                assert not accepted.is_error
                receipt = accepted.content[0].meta["sagasmith_auth_context_receipt"]
                assert receipt["actor_principal"] == "discord:user:member-1"
                assert receipt["conversation_principal"] == "discord:group:shared-room"
                assert receipt["tool"] == "exposure"

                replayed = await session.call_tool("exposure", arguments, meta=metadata)
                assert replayed.is_error
                assert "already used" in replayed.content[0].text

                opened_epoch = int(receipt["revision"])
                updated = await session.call_tool(
                    "exposure",
                    {
                        "action": "set",
                        "principal_id": "discord:user:member-1",
                        "add_tool_ids": ["campaign_query"],
                    },
                    meta=_meta(
                        actor="discord:user:member-1",
                        nonce="set-tools",
                        authorization_epoch=opened_epoch,
                    ),
                )
                assert not updated.is_error
                stale = await session.call_tool(
                    "exposure",
                    {
                        "action": "search",
                        "principal_id": "discord:user:member-1",
                        "query": "campaign_query",
                    },
                    meta=_meta(
                        actor="discord:user:member-1",
                        nonce="stale-epoch",
                        authorization_epoch=opened_epoch,
                    ),
                )
                assert stale.is_error
                assert "authorization_epoch is stale" in stale.content[0].text

                rebound = await session.call_tool(
                    "exposure",
                    {"action": "open", "principal_id": "discord:user:member-2"},
                    meta=_meta(actor="discord:user:member-2", nonce="actor-rebind"),
                )
                assert not rebound.is_error
                rebound_receipt = rebound.content[0].meta["sagasmith_auth_context_receipt"]
                assert rebound_receipt["actor_principal"] == "discord:user:member-2"
                assert rebound_receipt["conversation_principal"] == ("discord:group:shared-room")

    asyncio.run(exercise())
