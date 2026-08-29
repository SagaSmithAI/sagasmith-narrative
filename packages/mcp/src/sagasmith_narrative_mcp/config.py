"""Server configuration."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from sagasmith_core.database import sqlite_database_url
from sqlalchemy.engine import make_url


def _auth_context_secret() -> str | None:
    value = os.environ.get("SAGASMITH_AUTH_CONTEXT_SECRET", "")
    if not value:
        return None
    if len(value.encode("utf-8")) < 32:
        raise ValueError("SAGASMITH_AUTH_CONTEXT_SECRET must contain at least 32 bytes")
    return value


def _validate_secret(value: str, field: str) -> str:
    if len(value.encode("utf-8")) < 32:
        raise ValueError(f"{field} must contain at least 32 bytes")
    return value


def _derive_proposal_secret(auth_secret: str) -> str:
    return hmac.new(
        auth_secret.encode("utf-8"),
        b"sagasmith-narrative/campaign-expansion/v1",
        hashlib.sha256,
    ).hexdigest()


def _load_or_create_secret(secret_path: Path) -> str:
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = secret_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        candidate = secrets.token_urlsafe(48)
        try:
            descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            value = secret_path.read_text(encoding="utf-8").strip()
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(candidate)
            value = candidate
    try:
        os.chmod(secret_path, 0o600)
    except OSError:
        pass
    return _validate_secret(value, str(secret_path))


def _proposal_attestation_secret(home: Path, auth_secret: str | None) -> str:
    configured = os.environ.get("SAGASMITH_NARRATIVE_MCP_PROPOSAL_SECRET", "")
    if configured:
        return _validate_secret(
            configured, "SAGASMITH_NARRATIVE_MCP_PROPOSAL_SECRET"
        )
    if auth_secret:
        return _derive_proposal_secret(auth_secret)
    return _load_or_create_secret(home / "proposal-signing.key")


@dataclass(frozen=True)
class McpConfig:
    database_url: str | None = None
    bound_principal_id: str | None = None
    auth_context_secret: str | None = None
    proposal_attestation_secret: str | None = None
    http_host: str = "127.0.0.1"
    http_port: int = 8770
    http_path: str = "/mcp"

    def resolved_proposal_attestation_secret(self) -> str:
        if self.proposal_attestation_secret:
            return _validate_secret(
                self.proposal_attestation_secret, "proposal_attestation_secret"
            )
        if self.auth_context_secret:
            return _derive_proposal_secret(self.auth_context_secret)
        if self.database_url:
            url = make_url(self.database_url)
            if url.get_backend_name() == "sqlite" and url.database not in {None, ":memory:"}:
                database_path = Path(str(url.database))
                return _load_or_create_secret(
                    database_path.with_name(database_path.name + ".proposal-signing.key")
                )
        raise ValueError(
            "a proposal attestation secret is required for non-file or remote databases"
        )

    @classmethod
    def from_environment(cls) -> "McpConfig":
        configured_home = os.environ.get("SAGASMITH_NARRATIVE_MCP_HOME")
        home = (
            Path(configured_home).expanduser()
            if configured_home
            else Path.home() / ".sagasmith" / "narrative-mcp"
        )
        configured_url = os.environ.get("SAGASMITH_NARRATIVE_MCP_DATABASE_URL")
        uses_default_database = configured_url is None
        if uses_default_database:
            home.mkdir(parents=True, exist_ok=True)
            configured_url = sqlite_database_url(home / "narrative.db")
        auth_secret = _auth_context_secret()
        has_explicit_proposal_secret = bool(
            os.environ.get("SAGASMITH_NARRATIVE_MCP_PROPOSAL_SECRET", "")
        )
        proposal_secret = (
            _proposal_attestation_secret(home, auth_secret)
            if uses_default_database or auth_secret or has_explicit_proposal_secret
            else None
        )
        return cls(
            database_url=configured_url,
            bound_principal_id=os.environ.get("SAGASMITH_NARRATIVE_MCP_BOUND_PRINCIPAL_ID"),
            auth_context_secret=auth_secret,
            proposal_attestation_secret=proposal_secret,
            http_host=os.environ.get("SAGASMITH_NARRATIVE_MCP_HTTP_HOST", "127.0.0.1"),
            http_port=int(os.environ.get("SAGASMITH_NARRATIVE_MCP_HTTP_PORT", "8770")),
            http_path=os.environ.get("SAGASMITH_NARRATIVE_MCP_HTTP_PATH", "/mcp"),
        )
