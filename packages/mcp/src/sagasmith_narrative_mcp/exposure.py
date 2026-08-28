"""Ephemeral session-native tool exposure."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable
from uuid import uuid4

from mcp.server.mcpserver.exceptions import ToolError

from .policies import CORE_TOOLS, policy_for_tool


class ExposureError(ToolError):
    pass


@dataclass
class Exposure:
    id: str
    session_key: str
    principal_id: str
    campaign_id: str | None
    phase: str
    revision: int = 0
    loaded_tools: set[str] = field(default_factory=set)
    expires_at: float = field(default_factory=lambda: time.monotonic() + 900.0)


class ExposureRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Exposure] = {}

    def open(
        self, session_key: str, principal_id: str, campaign_id: str | None, phase: str
    ) -> Exposure:
        value = Exposure(f"exp_{uuid4().hex}", session_key, principal_id, campaign_id, phase)
        self._items[session_key] = value
        return value

    def active(self, session_key: str) -> Exposure | None:
        value = self._items.get(session_key)
        if value is not None and value.expires_at <= time.monotonic():
            self._items.pop(session_key, None)
            return None
        return value

    def get(self, handle: str, *, principal_id: str | None = None) -> Exposure:
        value = next((item for item in self._items.values() if item.id == handle), None)
        if value is None:
            raise ExposureError("exposure handle is unknown; open a new exposure")
        if value.expires_at <= time.monotonic():
            self._items.pop(value.session_key, None)
            raise ExposureError("exposure handle expired; open a new exposure")
        if principal_id is not None and value.principal_id != principal_id:
            raise ExposureError("exposure handle belongs to another principal")
        return value

    def for_campaign(self, campaign_id: str) -> list[Exposure]:
        return [item for item in self._items.values() if item.campaign_id == campaign_id]

    def refresh(self, exposure: Exposure, phase: str, allowed: set[str]) -> bool:
        retained = exposure.loaded_tools & allowed
        changed = exposure.phase != phase or retained != exposure.loaded_tools
        if changed:
            exposure.phase = phase
            exposure.loaded_tools = retained
            exposure.revision += 1
        return changed

    def set_tools(
        self, exposure: Exposure, add: Iterable[str], remove: Iterable[str], allowed: set[str]
    ) -> bool:
        additions = {str(item).strip() for item in add if str(item).strip()}
        removals = {str(item).strip() for item in remove if str(item).strip()}
        if additions & removals:
            raise ExposureError("the same tool cannot be added and removed")
        for name in additions:
            if policy_for_tool(name) is None or name not in allowed:
                raise ExposureError(f"tool is unavailable in this context: {name}")
        updated = (exposure.loaded_tools | additions) - removals
        changed = updated != exposure.loaded_tools
        if changed:
            exposure.loaded_tools = updated
            exposure.revision += 1
        return changed

    @staticmethod
    def visible(exposure: Exposure | None) -> set[str]:
        return set(CORE_TOOLS) | (set(exposure.loaded_tools) if exposure else set())

    @staticmethod
    def require(exposure: Exposure, name: str) -> None:
        if name not in CORE_TOOLS and name not in exposure.loaded_tools:
            raise ExposureError(f"tool is not loaded for this session: {name}")

    @staticmethod
    def status(exposure: Exposure) -> dict[str, object]:
        return {
            "exposure_id": exposure.id,
            "revision": exposure.revision,
            "principal_id": exposure.principal_id,
            "campaign_id": exposure.campaign_id,
            "phase": exposure.phase,
            "loaded_tools": sorted(exposure.loaded_tools),
            "visible_tools": sorted(ExposureRegistry.visible(exposure)),
            "ttl_ms": max(0, int((exposure.expires_at - time.monotonic()) * 1000)),
        }
