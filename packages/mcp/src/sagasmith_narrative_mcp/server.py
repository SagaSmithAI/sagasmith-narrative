"""Native dynamic MCP surface for system-neutral narrative campaigns."""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from typing import Annotated, Any, Literal, Mapping, TypeVar
from uuid import uuid4
from weakref import WeakValueDictionary

from mcp.server.caching import CacheHint
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    ToolAnnotations,
)
from pydantic import Field
from sagasmith_core import IdempotencyWrite, default_local_principal
from sagasmith_core.access import LOCAL_SYSTEM_PRINCIPAL_ID
from sagasmith_core.auth_context import (
    AUTH_CONTEXT_META_KEY,
    AUTH_CONTEXT_RECEIPT_META_KEY,
    AuthContext,
    AuthContextNonceGuard,
    verify_auth_context,
)
from sagasmith_core.database import Database
from sagasmith_narrative.contracts import active_profile, narrative_document

from .config import McpConfig
from .exposure import Exposure, ExposureError, ExposureRegistry
from .policies import ADMIN_TOOLS, CORE_TOOLS, allowed_tools, policy_for_tool
from .runtime import ADMIN_ROLES, NarrativeRuntime
from .skills import SkillCatalog
from .tool_contracts import (
    PublicContractValidationError,
    apply_public_contract,
    validate_public_arguments,
)

PageLimit = Annotated[int, Field(ge=1, le=100, description="Maximum records to return.")]
PageCursor = Annotated[
    str | None, Field(max_length=32, description="Opaque cursor from the preceding response.")
]
SearchText = Annotated[str, Field(max_length=256, description="Case-insensitive filter text.")]
PageItem = TypeVar("PageItem")


def _bounded_page(
    values: list[PageItem], *, limit: int = 50, cursor: str | None = None
) -> tuple[list[PageItem], str | None]:
    if isinstance(limit, bool) or not 1 <= int(limit) <= 100:
        raise ValueError("limit must be between 1 and 100")
    raw = str(cursor or "").strip()
    if raw and (not raw.startswith("p:") or not raw[2:].isdigit()):
        raise ValueError("cursor is invalid; reuse next_cursor from the preceding response")
    offset = int(raw[2:]) if raw else 0
    page = values[offset : offset + int(limit)]
    next_offset = offset + len(page)
    return page, f"p:{next_offset}" if next_offset < len(values) else None


def _auth_receipt_revision(value: Any) -> int | str | None:
    if not isinstance(value, dict):
        return None
    for key in ("campaign_revision", "revision", "new_revision", "to_revision"):
        revision = value.get(key)
        if isinstance(revision, (int, str)) and not isinstance(revision, bool):
            return revision
    for nested in value.values():
        if isinstance(nested, dict) and (revision := _auth_receipt_revision(nested)) is not None:
            return revision
    return None


def _attach_auth_receipt(result: Any, context: AuthContext | None, tool: str) -> Any:
    if context is None:
        return result
    if isinstance(result, CallToolResult):
        content, structured = result.content, result.structured_content
    elif isinstance(result, tuple) and len(result) == 2:
        content, structured = result
    else:
        return result
    receipt = context.audit_receipt(tool=tool, revision=_auth_receipt_revision(structured))
    updated = []
    attached = False
    for item in content:
        if not attached and isinstance(item, TextContent):
            metadata = dict(item.meta or {})
            metadata[AUTH_CONTEXT_RECEIPT_META_KEY] = receipt
            updated.append(item.model_copy(update={"meta": metadata}))
            attached = True
        else:
            updated.append(item)
    if isinstance(result, CallToolResult):
        return result.model_copy(update={"content": updated})
    return updated, structured


class RequestScopedMCPServer(MCPServer):
    """Serve legacy and modern MCP without using a connection as authority."""

    def __init__(
        self,
        *args: Any,
        registry: ExposureRegistry,
        runtime: NarrativeRuntime,
        bound_principal_id: str | None = None,
        auth_context_secret: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.registry = registry
        self.runtime = runtime
        self._bound_principal_id = bound_principal_id.strip() if bound_principal_id else None
        self._auth_context_secret = auth_context_secret
        self._auth_context_nonces = AuthContextNonceGuard() if auth_context_secret else None
        self._exposure_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._campaign_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._metric_counts: Counter[tuple[str, str, str, str]] = Counter()
        super().__init__(*args, **kwargs)
        original = self._lowlevel_server.create_initialization_options

        def options(
            notification_options: NotificationOptions | None = None,
            experimental_capabilities: dict[str, dict[str, Any]] | None = None,
        ):
            return original(
                notification_options
                or NotificationOptions(
                    tools_changed=True, prompts_changed=False, resources_changed=False
                ),
                experimental_capabilities,
            )

        self._lowlevel_server.create_initialization_options = options  # type: ignore[method-assign]

    @staticmethod
    def _request_session(context: Context | None = None) -> tuple[str, Any] | None:
        """Return a legacy compatibility key, never an identity."""

        if context is None:
            return None
        try:
            session = context.session
        except (AttributeError, LookupError, ValueError):
            return None
        connection = getattr(session, "_connection", None)
        key = getattr(connection, "session_id", None) or f"legacy:{id(connection)}"
        return key, session

    def _principal_argument(self, name: str) -> str | None:
        tool = self._tool_manager.get_tool(name)
        properties = dict((tool.parameters if tool else {}).get("properties") or {})
        for candidate in ("principal_id", "by_principal_id"):
            if candidate in properties:
                return candidate
        return None

    def _bind_principal(
        self, name: str, arguments: dict[str, Any], exposure: Exposure | None
    ) -> dict[str, Any]:
        result = dict(arguments)
        argument = self._principal_argument(name)
        expected = self._bound_principal_id or (exposure.principal_id if exposure else None)
        if (
            name == "exposure"
            and result.get("action") == "open"
            and self._auth_context_secret is not None
            and self._bound_principal_id is None
        ):
            return result
        if argument is None or expected is None:
            return result
        supplied = result.get(argument)
        if supplied is not None and supplied != expected and self._bound_principal_id is None:
            raise ExposureError("tool principal does not match session exposure")
        result[argument] = expected
        return result

    def _allowed(self, exposure: Exposure) -> set[str]:
        document = (
            narrative_document(self.runtime.campaigns.get(exposure.campaign_id).state)
            if exposure.campaign_id
            else None
        )
        profile = active_profile(document) if document else None
        capabilities = set(profile.get("capabilities", [])) if profile else set()
        result = allowed_tools(exposure.phase, capabilities)
        if exposure.campaign_id:
            membership = self.runtime.access.require_campaign(
                exposure.campaign_id, exposure.principal_id
            )
            if membership.role not in ADMIN_ROLES:
                result -= set(ADMIN_TOOLS)
                if not self.runtime.principal_controls_actor(
                    exposure.campaign_id, exposure.principal_id
                ):
                    result.discard("actor_change")
            assert document is not None
            authority = dict(profile.get("authority") or {}) if profile else {}
            facilitator = self.runtime._has_facilitator_authority(document, membership.role)
            actor_conflict_authority = bool(
                authority.get("player_controls_owned_actor")
            ) and self.runtime.principal_controls_actor(exposure.campaign_id, exposure.principal_id)
            current_conflict = document.get("conflict")
            owns_conflict = bool(
                current_conflict
                and current_conflict.get("controller_principal_id") == exposure.principal_id
            )
            if not facilitator and not actor_conflict_authority and not owns_conflict:
                result -= {
                    "conflict_start",
                    "conflict_query",
                    "conflict_act",
                    "conflict_end",
                }
            if membership.role == "observer":
                result -= {
                    name
                    for name in result
                    if name.endswith("_change")
                    or name.endswith("_settle")
                    or name
                    in {
                        "game_phase",
                        "mechanic_resolve",
                        "npc_conversation",
                        "narrative_settle",
                        "conflict_start",
                        "conflict_act",
                        "conflict_end",
                    }
                }
        return result

    @staticmethod
    def _argument_campaign_id(arguments: dict[str, Any]) -> str:
        campaign_id = str(arguments.get("campaign_id") or "").strip()
        if campaign_id:
            return campaign_id
        for key in ("payload", "data"):
            nested = arguments.get(key)
            if isinstance(nested, dict) and (value := str(nested.get("campaign_id") or "").strip()):
                return value
        return ""

    def _verify_request_auth_context(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        context: Context | None,
        exposure: Exposure | None,
    ) -> AuthContext | None:
        principal_argument = self._principal_argument(name)
        if self._auth_context_secret is None or principal_argument is None:
            return None
        try:
            if context is None:
                raise ValueError("signed auth context requires an MCP request context")
            metadata = context.request_context.meta
            envelope = (
                metadata.get(AUTH_CONTEXT_META_KEY)
                if isinstance(metadata, Mapping)
                else getattr(metadata, AUTH_CONTEXT_META_KEY, None)
            )
            supplied_principal = str(arguments.get(principal_argument) or "").strip()
            verified = verify_auth_context(envelope, self._auth_context_secret)
            modern = verified.schema == "sagasmith.auth-context/v2"
            if not modern and not supplied_principal:
                raise ValueError("tool caller principal is required")
            # Modern authorization follows the signed requester. The separately
            # signed acting Host remains the authoritative actor retained by the
            # audit receipt. Legacy callers keep their exact actor binding.
            arguments[principal_argument] = (
                verified.authorization_principal if modern else verified.actor_principal
            )
            expected_campaign = self._argument_campaign_id(arguments)
            if (
                not expected_campaign
                and exposure is not None
                and not (name == "exposure" and arguments.get("action") == "open")
            ):
                expected_campaign = exposure.campaign_id or ""
            expected_revision = arguments.get("expected_revision", arguments.get("base_revision"))
            if expected_revision is not None and (
                isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
            ):
                raise ValueError("expected_revision/base_revision must be an integer")
            verified = verify_auth_context(
                envelope,
                self._auth_context_secret,
                expected_actor=(
                    verified.authority_principal if modern else supplied_principal
                ),
                expected_campaign=expected_campaign or None,
                expected_service="sagasmith-narrative-mcp" if modern else None,
                expected_operation=name if modern else None,
                expected_audience="sagasmith-narrative-mcp" if modern else None,
                expected_room_turn=(
                    str(arguments["room_turn_id"]).strip()
                    if modern and arguments.get("room_turn_id")
                    else None
                ),
                expected_base_revision=expected_revision if modern else None,
                expected_resource_owner=(
                    str(arguments["resource_owner_principal"]).strip()
                    if modern and arguments.get("resource_owner_principal")
                    else None
                ),
                expected_acting_character=(
                    str(arguments["acting_character_id"]).strip()
                    if modern and arguments.get("acting_character_id")
                    else None
                ),
                expected_requester=(
                    verified.authorization_principal if modern else None
                ),
            )
        except ValueError as exc:
            raise ExposureError(str(exc)) from exc
        expected_epoch = (
            exposure.revision
            if exposure is not None
            and not (name == "exposure" and arguments.get("action") == "open")
            else 0
        )
        if (
            verified.schema != "sagasmith.auth-context/v2"
            and verified.authorization_epoch != expected_epoch
        ):
            raise ExposureError("auth context authorization_epoch is stale")
        assert self._auth_context_nonces is not None
        try:
            self._auth_context_nonces.remember(verified)
        except (RuntimeError, ValueError) as exc:
            raise ExposureError(str(exc)) from exc
        return verified

    async def _refresh(self, campaign_id: str | None = None) -> bool:
        changed = []
        candidates = (
            self.registry.for_campaign(campaign_id)
            if campaign_id
            else list(self.registry._items.values())
        )
        for exposure in candidates:
            if exposure.campaign_id is None:
                continue
            phase = self.runtime.phase(exposure.campaign_id)
            if self.registry.refresh(exposure, phase, self._allowed(exposure)):
                changed.append(exposure.session_key)
        return bool(changed)

    @staticmethod
    def _attach(result: Any, binding: dict[str, Any] | None) -> Any:
        if binding is None:
            return result
        if isinstance(result, CallToolResult):
            content, structured = result.content, result.structured_content
        elif isinstance(result, tuple) and len(result) == 2:
            content, structured = result
        else:
            return result

        def apply(value: Any) -> Any:
            if not isinstance(value, dict):
                return value
            output = deepcopy(value)
            payload = output.get("result")
            if isinstance(payload, dict):
                payload["host_context_binding"] = deepcopy(binding)
            else:
                output["host_context_binding"] = deepcopy(binding)
            return output

        changed_content = []
        for item in content:
            if not isinstance(item, TextContent):
                changed_content.append(item)
                continue
            try:
                decoded = json.loads(item.text)
            except json.JSONDecodeError:
                changed_content.append(item)
                continue
            changed_content.append(
                item.model_copy(
                    update={
                        "text": json.dumps(
                            apply(decoded), ensure_ascii=False, separators=(",", ":")
                        )
                    }
                )
            )
        if isinstance(result, CallToolResult):
            metadata = dict(result.meta or {})
            metadata["sagasmith_host_context_binding"] = deepcopy(binding)
            return result.model_copy(update={"content": changed_content, "meta": metadata})
        return changed_content, apply(structured)

    async def list_tools(self):  # type: ignore[override]
        return sorted(await super().list_tools(), key=lambda tool: tool.name)

    async def _handle_list_tools(
        self, ctx: ServerRequestContext, params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        tools = await self.list_tools()
        era = "modern" if ctx.protocol_version == "2026-07-28" else "legacy"
        self._metric_counts[("catalog", era, "tools/list", "success")] += 1
        if ctx.protocol_version != "2026-07-28":
            context = Context(
                request_context=ctx, mcp_server=self, subscriptions=self._subscriptions
            )
            request = self._request_session(context)
            if request is not None:
                session_key, _ = request
                await self._refresh()
                visible = self.registry.visible(self.registry.active(session_key))
                tools = [tool for tool in tools if tool.name in visible]
        return ListToolsResult(tools=tools)

    async def _handle_call_tool(
        self, ctx: ServerRequestContext, params: CallToolRequestParams
    ) -> CallToolResult:
        result = await super()._handle_call_tool(ctx, params)
        era = "modern" if ctx.protocol_version == "2026-07-28" else "legacy"
        outcome = "error" if result.is_error else "success"
        stage = "exposure" if params.name == "exposure" else "tool"
        self._metric_counts[(stage, era, params.name, outcome)] += 1
        return result

    def metrics_snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "stage": stage,
                "protocol_era": era,
                "operation": operation,
                "outcome": outcome,
                "count": count,
            }
            for (stage, era, operation, outcome), count in sorted(self._metric_counts.items())
        ]

    @staticmethod
    def _structured_tool_error(message: str) -> CallToolResult:
        """Return a safe, model-repairable execution error.

        Unknown methods, unknown tools, and JSON-schema validation remain protocol
        errors in the SDK.  This shape is only for failures reached while executing
        a known tool.
        """

        text = message.strip() or "The tool request was rejected."
        lowered = text.casefold()
        retryable = any(
            marker in lowered for marker in ("stale", "expired", "timeout", "temporar", "conflict")
        )
        if "revision" in lowered and any(marker in lowered for marker in ("stale", "conflict")):
            code = "stale_revision"
        elif "expired" in lowered and any(
            marker in lowered for marker in ("handle", "exposure", "conversation")
        ):
            code = "expired_handle"
        elif any(
            marker in lowered for marker in ("auth", "principal", "permission", "access", "role")
        ):
            code = "authorization_denied"
        elif isinstance(message, str) and "not found" in lowered:
            code = "not_found"
        else:
            code = "invalid_request"
        recovery = (
            "Refresh the authoritative revision or handle and retry with the same idempotency key."
            if retryable
            else "Correct the tool arguments before retrying."
        )
        return CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text=text)],
            structured_content={
                "error": {
                    "code": code,
                    "message": text,
                    "retryable": retryable,
                    "recovery": recovery,
                }
            },
        )

    @staticmethod
    def _attach_trace_context(result: Any, context: Context | None) -> Any:
        if not isinstance(result, CallToolResult) or context is None:
            return result
        headers = context.headers
        if not isinstance(headers, Mapping):
            return result
        propagated = {
            key: value
            for key in ("traceparent", "tracestate", "baggage")
            if isinstance((value := headers.get(key)), str) and 0 < len(value) <= 2048
        }
        if not propagated:
            return result
        metadata = dict(result.meta or {})
        metadata["sagasmith_trace_context"] = propagated
        return result.model_copy(update={"meta": metadata})

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context | None = None,
    ):  # type: ignore[override]
        try:
            return await self._call_tool_impl(name, arguments, context)
        except ToolError as exc:
            if context is None:
                raise
            message = str(exc)
            if message.startswith("Unknown tool") or "validation error" in message.casefold():
                raise
            return self._structured_tool_error(message)
        except (ExposureError, LookupError, PermissionError, ValueError) as exc:
            if context is None:
                raise
            return self._structured_tool_error(str(exc))

    async def _call_tool_impl(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context | None = None,
    ):
        arguments = dict(arguments or {})
        registered_tool = self._tool_manager.get_tool(name)
        if registered_tool is not None:
            try:
                validate_public_arguments(registered_tool, arguments)
            except PublicContractValidationError as exc:
                raise ToolError(f"input validation error: {exc}") from exc
        if self._bound_principal_id:
            arguments = self._bind_principal(name, arguments, None)
        legacy_request = (
            self._request_session(context)
            if context is not None and context.protocol_version != "2026-07-28"
            else None
        )
        if context is None:
            if name not in CORE_TOOLS:
                raise ExposureError("domain tools require a real MCP session exposure")
            return await super().call_tool(name, arguments, context)
        session_key = legacy_request[0] if legacy_request else None
        if session_key is not None:
            await self._refresh()
        exposure = None
        if name == "exposure" and arguments.get("action") != "open":
            handle = str(arguments.get("exposure_handle") or "").strip()
            if handle:
                exposure = self.registry.get(handle)
            elif session_key is not None:
                exposure = self.registry.active(session_key)
        elif session_key is not None:
            exposure = self.registry.active(session_key)
        if session_key is not None:
            arguments = self._bind_principal(name, arguments, exposure)
        auth_context = self._verify_request_auth_context(
            name=name,
            arguments=arguments,
            context=context,
            exposure=exposure,
        )
        if name not in CORE_TOOLS:
            if session_key is not None and exposure is None:
                raise ExposureError("open a session exposure before calling domain tools")
            if session_key is not None and exposure is not None:
                self.registry.require(exposure, name)
            campaign_arg = str(arguments.get("campaign_id") or "") or None
            if exposure is not None and exposure.campaign_id != campaign_arg:
                raise ExposureError("tool campaign does not match session exposure")
            policy = policy_for_tool(name)
            if policy and policy.admin_only:
                self.runtime.access.require_campaign(
                    campaign_arg,
                    str(
                        arguments.get("principal_id")
                        or arguments.get("by_principal_id")
                        or (
                            auth_context.authorization_principal
                            if auth_context is not None
                            else LOCAL_SYSTEM_PRINCIPAL_ID
                        )
                    ),
                    roles=ADMIN_ROLES,
                )
            try:
                if campaign_arg:
                    async with self._campaign_locks.setdefault(campaign_arg, asyncio.Lock()):
                        result = await super().call_tool(name, arguments, context)
                else:
                    result = await super().call_tool(name, arguments, context)
            except UnexpectedToolError as exc:
                if isinstance(exc.__cause__, (LookupError, PermissionError, ValueError)):
                    raise ToolError(str(exc.__cause__)) from exc.__cause__
                raise
        else:
            try:
                result = await super().call_tool(name, arguments, context)
            except UnexpectedToolError as exc:
                if isinstance(exc.__cause__, (LookupError, PermissionError, ValueError)):
                    raise ToolError(str(exc.__cause__)) from exc.__cause__
                raise
        if (
            legacy_request is not None
            and name == "exposure"
            and arguments.get("action") in {"open", "set"}
        ):
            await legacy_request[1].send_tool_list_changed()
        campaign_id = str(arguments.get("campaign_id") or "") or None
        campaign_id = campaign_id or (exposure.campaign_id if exposure else None)
        if campaign_id:
            if session_key is not None:
                await self._refresh(campaign_id)
            principal = (
                exposure.principal_id
                if exposure
                else str(
                    arguments.get("principal_id")
                    or arguments.get("by_principal_id")
                    or (
                        auth_context.authorization_principal
                        if auth_context is not None
                        else LOCAL_SYSTEM_PRINCIPAL_ID
                    )
                )
            )
            binding = self.runtime.binding(campaign_id, principal)
            current = self.registry.active(session_key) if session_key is not None else None
            binding["authorization_epoch"] = current.revision if current else 0
            result = self._attach(result, binding)
        result = _attach_auth_receipt(result, auth_context, name)
        return self._attach_trace_context(result, context)


SessionExposureFastMCP = RequestScopedMCPServer


def create_server(config: McpConfig | None = None) -> MCPServer:
    config = config or McpConfig.from_environment()
    database = Database(config.database_url)
    database.upgrade_schema()
    default_local_principal(database)
    runtime = NarrativeRuntime(database)
    registry = ExposureRegistry()
    skills = SkillCatalog()
    mcp = SessionExposureFastMCP(
        "SagaSmith Narrative",
        registry=registry,
        runtime=runtime,
        bound_principal_id=config.bound_principal_id,
        auth_context_secret=config.auth_context_secret,
        cache_hints={"tools/list": CacheHint(ttl_ms=300000, scope="private")},
    )

    def common(
        principal_id: str,
        expected_revision: int,
        expected_branch_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return {
            "principal_id": principal_id,
            "expected_revision": expected_revision,
            "expected_branch_id": expected_branch_id,
            "idempotency_key": idempotency_key,
        }

    @mcp.tool()
    def server_capabilities() -> dict[str, Any]:
        return {
            "system_id": "narrative",
            "schema_version": 1,
            "authoritative_contract": {
                "schema": "sagasmith.authoritative-mcp/v2",
                "protocols": {
                    "2026-07-28": "request-scoped-primary",
                    "2025-11-25": "legacy-session-adapter",
                },
                "transports": ["stdio", "streamable-http"],
                "shared_handlers": True,
                "dynamic_tool_exposure": "host-selected-stable-catalog",
                "revision_model": "optimistic",
                "idempotency_model": "required-for-writes",
                "authority_model": "server-owned-request-validated",
                "error_model": "mcp-tool-error",
            },
            "base_phases": ["lobby", "play"],
            "optional_phases": ["conflict"],
            "mechanics_levels": [0, 1],
            "native_dynamic_tools": False,
            "tools_list_changed": "legacy-adapter-only",
            "catalog_cache": {"scope": "private", "ttl_ms": 300000},
            "explicit_exposure_handle": True,
            "identity_mode": (
                "process_bound_principal"
                if config.bound_principal_id
                else (
                    "signed_request_delegation"
                    if config.auth_context_secret
                    else "trusted_single_user_local_transport"
                )
            ),
            "loopback_streamable_http_supported": True,
            "shared_network_transport_supported": config.auth_context_secret is not None,
        }

    @mcp.tool()
    def campaign_query(
        action: Literal["list", "get"],
        campaign_id: str | None = None,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
        query: SearchText = "",
        limit: PageLimit = 50,
        cursor: PageCursor = None,
    ) -> dict[str, Any]:
        if action == "list":
            terms = [term.casefold() for term in query.split() if term.strip()]
            campaigns = sorted(
                runtime.campaign_list(principal_id), key=lambda item: (item["name"], item["id"])
            )
            if terms:
                campaigns = [
                    item
                    for item in campaigns
                    if all(
                        term
                        in (
                            f"{item.get('id', '')} {item.get('name', '')} "
                            f"{item.get('slug', '')} {item.get('description', '')}"
                        ).casefold()
                        for term in terms
                    )
                ]
            page, next_cursor = _bounded_page(campaigns, limit=limit, cursor=cursor)
            return {"campaigns": page, "next_cursor": next_cursor}
        if not campaign_id:
            raise ValueError("campaign_id is required")
        return runtime.campaign_get(campaign_id, principal_id)

    @mcp.tool()
    def skill_query(
        action: Literal["list", "get_section", "search"],
        skill_id: str | None = None,
        section: str | None = None,
        query: SearchText = "",
        limit: PageLimit = 20,
        cursor: PageCursor = None,
    ) -> dict[str, Any]:
        if action == "list":
            terms = [term.casefold() for term in query.split() if term.strip()]
            values = [
                item
                for item in skills.list()
                if not terms
                or all(
                    term in f"{item.get('id', '')} {item.get('path', '')}".casefold()
                    for term in terms
                )
            ]
            page, next_cursor = _bounded_page(values, limit=limit, cursor=cursor)
            return {"skills": page, "next_cursor": next_cursor}
        if action == "search":
            return skills.search(query, limit=limit, cursor=cursor)
        if not skill_id:
            raise ValueError("skill_id is required")
        if section is None:
            return {"skill_id": skill_id, "sections": skills.sections(skill_id)}
        return skills.get_section(skill_id, section)

    @mcp.tool()
    async def exposure(
        action: Literal["open", "get", "search", "set"],
        ctx: Context,
        exposure_handle: str | None = None,
        campaign_id: str | None = None,
        query: SearchText = "",
        limit: PageLimit = 50,
        cursor: PageCursor = None,
        add_tool_ids: list[str] | None = None,
        remove_tool_ids: list[str] | None = None,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        request = mcp._request_session(ctx)
        modern = ctx.protocol_version == "2026-07-28"
        session_key = request[0] if request else f"direct:{principal_id}"
        if modern:
            session_key = f"handle:{uuid4().hex}"
        if config.bound_principal_id:
            principal_id = config.bound_principal_id
        if action == "open":
            phase = "lobby"
            if campaign_id:
                runtime.access.require_campaign(campaign_id, principal_id)
                phase = runtime.phase(campaign_id)
            opened = registry.open(session_key, principal_id, campaign_id, phase)
            return {
                **registry.status(opened),
                "exposure_handle": opened.id,
                "catalog_effect": "guidance_only" if modern else "legacy_compatibility",
            }
        handle = str(exposure_handle or "").strip()
        if modern and not handle:
            raise ExposureError("exposure_handle is required on the 2026-07-28 path")
        current = (
            registry.get(handle, principal_id=principal_id)
            if handle
            else registry.active(session_key)
        )
        if current is None:
            raise ExposureError("no active exposure; use action='open'")
        if action == "get":
            return registry.status(current)
        allowed = mcp._allowed(current)
        if action == "search":
            terms = [item.casefold() for item in query.split() if item.strip()]
            matches = []
            for tool in sorted(mcp._tool_manager.list_tools(), key=lambda item: item.name):
                if tool.name not in allowed or tool.name in CORE_TOOLS:
                    continue
                haystack = f"{tool.name} {tool.description or ''}".casefold()
                if terms and not all(term in haystack for term in terms):
                    continue
                matches.append(
                    {
                        "tool_id": tool.name,
                        "description": tool.description or "",
                        "loaded": tool.name in current.loaded_tools,
                    }
                )
            page, next_cursor = _bounded_page(matches, limit=limit, cursor=cursor)
            return {**registry.status(current), "matches": page, "next_cursor": next_cursor}
        changed = registry.set_tools(current, add_tool_ids or [], remove_tool_ids or [], allowed)
        return {**registry.status(current), "changed": changed}

    @mcp.tool()
    def campaign_setup(
        action: Literal["create"],
        name: str,
        description: str = "",
        slug: str | None = None,
        idempotency_key: str = "",
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        return runtime.campaign_create(
            name=name,
            description=description,
            slug=slug,
            principal_id=principal_id,
            idempotency_key=idempotency_key,
        )

    @mcp.tool()
    def access_change(
        campaign_id: str,
        action: Literal[
            "campaign_grant",
            "campaign_revoke",
            "actor_grant",
            "actor_revoke",
            "element_grant",
            "element_revoke",
        ],
        target_principal_id: str,
        role: str | None = None,
        actor_id: str | None = None,
        can_control: bool = False,
        can_view_private: bool = False,
        element_ref: str | None = None,
        scope: dict[str, Any] | None = None,
        expected_revision: int = 0,
        expected_branch_id: str = "",
        idempotency_key: str = "",
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        return runtime.access_change(
            campaign_id,
            principal_id=principal_id,
            action=action,
            target_principal_id=target_principal_id,
            role=role,
            actor_id=actor_id,
            can_control=can_control,
            can_view_private=can_view_private,
            element_ref=element_ref,
            scope=scope,
            expected_revision=expected_revision,
            expected_branch_id=expected_branch_id,
            idempotency_key=idempotency_key,
        )

    @mcp.tool()
    def profile_change(
        campaign_id: str,
        action: Literal["create_draft", "update_draft", "finalize", "activate"],
        profile: dict[str, Any] | None = None,
        profile_key: str | None = None,
        expected_revision: int = 0,
        expected_branch_id: str = "",
        idempotency_key: str = "",
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        return runtime.profile_change(
            campaign_id,
            action=action,
            profile=profile,
            profile_key=profile_key,
            **common(principal_id, expected_revision, expected_branch_id, idempotency_key),
        )

    @mcp.tool()
    def pack_change(
        campaign_id: str,
        action: Literal[
            "create_draft", "update_draft", "finalize", "import", "activate", "apply_seed"
        ],
        pack: dict[str, Any] | None = None,
        pack_key: str | None = None,
        expected_revision: int = 0,
        expected_branch_id: str = "",
        idempotency_key: str = "",
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        if action == "apply_seed":
            return runtime.campaign_seed_apply(
                campaign_id,
                pack_key=str(pack_key),
                principal_id=principal_id,
                expected_revision=expected_revision,
                expected_branch_id=expected_branch_id,
                idempotency_key=idempotency_key,
            )
        return runtime.pack_change(
            campaign_id,
            action=action,
            pack=pack,
            pack_key=pack_key,
            **common(principal_id, expected_revision, expected_branch_id, idempotency_key),
        )

    @mcp.tool()
    def game_phase(
        campaign_id: str,
        phase: Literal["lobby", "play"],
        expected_revision: int,
        expected_branch_id: str,
        idempotency_key: str,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        return runtime.set_phase(
            campaign_id,
            phase=phase,
            **common(principal_id, expected_revision, expected_branch_id, idempotency_key),
        )

    @mcp.tool()
    def actor_query(
        campaign_id: str,
        actor_id: str | None = None,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
        query: SearchText = "",
        limit: PageLimit = 50,
        cursor: PageCursor = None,
    ) -> dict[str, Any]:
        result = runtime.actor_query(campaign_id, principal_id=principal_id, actor_id=actor_id)
        if actor_id:
            return result
        terms = [term.casefold() for term in query.split() if term.strip()]
        values = sorted(result["actors"], key=lambda item: (item.get("name", ""), item["id"]))
        if terms:
            values = [
                item
                for item in values
                if all(term in json.dumps(item, sort_keys=True).casefold() for term in terms)
            ]
        page, next_cursor = _bounded_page(values, limit=limit, cursor=cursor)
        return {"actors": page, "next_cursor": next_cursor}

    @mcp.tool()
    def actor_change(
        campaign_id: str,
        action: Literal["create", "update"],
        actor: dict[str, Any],
        expected_revision: int,
        expected_branch_id: str,
        idempotency_key: str,
        actor_id: str | None = None,
        expected_actor_revision: int | None = None,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        if action == "update":
            if not actor_id or expected_actor_revision is None:
                raise ValueError("actor update requires actor_id and expected_actor_revision")
            return runtime.actor_update(
                campaign_id,
                principal_id=principal_id,
                actor_id=actor_id,
                actor=actor,
                expected_actor_revision=expected_actor_revision,
                expected_revision=expected_revision,
                expected_branch_id=expected_branch_id,
                idempotency_key=idempotency_key,
            )
        return runtime.actor_create(
            campaign_id,
            principal_id=principal_id,
            idempotency_key=idempotency_key,
            actor=actor,
            expected_revision=expected_revision,
            expected_branch_id=expected_branch_id,
        )

    @mcp.tool()
    def scene_change(
        campaign_id: str,
        action: Literal["start", "update", "end"],
        scene: dict[str, Any] | None = None,
        scene_id: str | None = None,
        expected_revision: int = 0,
        expected_branch_id: str = "",
        idempotency_key: str = "",
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        return runtime.scene_change(
            campaign_id,
            action=action,
            scene=scene,
            scene_id=scene_id,
            **common(principal_id, expected_revision, expected_branch_id, idempotency_key),
        )

    @mcp.tool()
    def narrative_query(
        campaign_id: str,
        kind: Literal["profile", "pack", "scene", "record"],
        record_id: str | None = None,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
        query: SearchText = "",
        limit: PageLimit = 50,
        cursor: PageCursor = None,
    ) -> dict[str, Any]:
        result = runtime.query(
            campaign_id, principal_id=principal_id, kind=kind, record_id=record_id
        )
        if record_id or "items" not in result:
            return result
        terms = [term.casefold() for term in query.split() if term.strip()]
        values = sorted(result["items"], key=lambda item: str(item.get("id") or ""))
        if terms:
            values = [
                item
                for item in values
                if all(term in json.dumps(item, sort_keys=True).casefold() for term in terms)
            ]
        page, next_cursor = _bounded_page(values, limit=limit, cursor=cursor)
        return {"items": page, "next_cursor": next_cursor}

    @mcp.tool()
    def narrative_change(
        campaign_id: str,
        action: Literal["create", "update"],
        record: dict[str, Any],
        expected_record_revision: int | None = None,
        expected_revision: int = 0,
        expected_branch_id: str = "",
        idempotency_key: str = "",
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        return runtime.record_change(
            campaign_id,
            action=action,
            record=record,
            expected_record_revision=expected_record_revision,
            **common(principal_id, expected_revision, expected_branch_id, idempotency_key),
        )

    @mcp.tool()
    def narrative_settle(
        campaign_id: str,
        event: dict[str, Any],
        record_changes: list[dict[str, Any]] | None,
        facts: list[dict[str, Any]] | None,
        actor_knowledge: list[dict[str, Any]] | None,
        snapshot: dict[str, Any] | None,
        expected_revision: int,
        expected_branch_id: str,
        idempotency_key: str,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        return runtime.narrative_settle(
            campaign_id,
            principal_id=principal_id,
            expected_revision=expected_revision,
            expected_branch_id=expected_branch_id,
            idempotency_key=idempotency_key,
            event=event,
            record_changes=record_changes,
            facts=facts,
            actor_knowledge=actor_knowledge,
            snapshot=snapshot,
        )

    @mcp.tool()
    def continuity_query(
        campaign_id: str,
        actor_id: str | None = None,
        query: SearchText = "",
        limit: PageLimit = 50,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        membership = runtime.access.require_campaign(campaign_id, principal_id)
        if actor_id:
            actor_id = runtime.require_actor_authority(campaign_id, actor_id, principal_id)
        elif membership.role not in ADMIN_ROLES:
            visible_actors = runtime.actor_query(campaign_id, principal_id=principal_id).get(
                "actors", []
            )
            if len(visible_actors) == 1:
                actor_id = str(visible_actors[0]["id"])
        document = narrative_document(runtime.campaigns.get(campaign_id).state)
        audience = (
            "dm" if runtime._has_facilitator_authority(document, membership.role) else "player"
        )
        return runtime.continuity.context(
            campaign_id,
            query=query,
            actor_id=actor_id,
            audience=audience,
            limit=limit,
        )

    @mcp.tool()
    def mechanic_resolve(
        campaign_id: str,
        mechanic_id: str,
        inputs: dict[str, Any],
        expected_revision: int,
        expected_branch_id: str,
        idempotency_key: str,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        return runtime.mechanic_resolve(
            campaign_id,
            mechanic_id=mechanic_id,
            inputs=inputs,
            **common(principal_id, expected_revision, expected_branch_id, idempotency_key),
        )

    @mcp.tool()
    def npc_conversation(
        campaign_id: str,
        action: Literal["open", "propose", "publish", "close", "abort"],
        conversation_id: str | None = None,
        npc_actor_id: str | None = None,
        data: dict[str, Any] | None = None,
        expected_revision: int = 0,
        expected_branch_id: str = "",
        idempotency_key: str = "",
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        return runtime.npc_conversation(
            campaign_id,
            action=action,
            conversation_id=conversation_id,
            npc_actor_id=npc_actor_id,
            data=data,
            **common(principal_id, expected_revision, expected_branch_id, idempotency_key),
        )

    @mcp.tool()
    def downtime_settle(
        campaign_id: str,
        summary: str,
        changes: list[dict[str, Any]],
        expected_revision: int,
        expected_branch_id: str,
        idempotency_key: str,
        facts: list[dict[str, Any]] | None = None,
        actor_knowledge: list[dict[str, Any]] | None = None,
        snapshot: dict[str, Any] | None = None,
        audience_scope: str = "public",
        participants: list[dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        return runtime.activity_settle(
            campaign_id,
            activity="downtime",
            summary=summary,
            changes=changes,
            facts=facts,
            actor_knowledge=actor_knowledge,
            snapshot=snapshot,
            audience_scope=audience_scope,
            participants=participants,
            payload=payload,
            **common(principal_id, expected_revision, expected_branch_id, idempotency_key),
        )

    @mcp.tool()
    def world_turn_settle(
        campaign_id: str,
        summary: str,
        changes: list[dict[str, Any]],
        expected_revision: int,
        expected_branch_id: str,
        idempotency_key: str,
        facts: list[dict[str, Any]] | None = None,
        actor_knowledge: list[dict[str, Any]] | None = None,
        snapshot: dict[str, Any] | None = None,
        audience_scope: str = "public",
        participants: list[dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        return runtime.activity_settle(
            campaign_id,
            activity="world_turn",
            summary=summary,
            changes=changes,
            facts=facts,
            actor_knowledge=actor_knowledge,
            snapshot=snapshot,
            audience_scope=audience_scope,
            participants=participants,
            payload=payload,
            **common(principal_id, expected_revision, expected_branch_id, idempotency_key),
        )

    @mcp.tool()
    def conflict_start(
        campaign_id: str,
        data: dict[str, Any],
        expected_revision: int,
        expected_branch_id: str,
        idempotency_key: str,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        return runtime.conflict(
            campaign_id,
            action="start",
            data=data,
            **common(principal_id, expected_revision, expected_branch_id, idempotency_key),
        )

    @mcp.tool()
    def conflict_query(
        campaign_id: str, principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID
    ) -> dict[str, Any]:
        runtime.access.require_campaign(campaign_id, principal_id)
        return {
            "conflict": narrative_document(runtime.campaigns.get(campaign_id).state).get("conflict")
        }

    @mcp.tool()
    def conflict_act(
        campaign_id: str,
        data: dict[str, Any],
        expected_revision: int,
        expected_branch_id: str,
        idempotency_key: str,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        return runtime.conflict(
            campaign_id,
            action="act",
            data=data,
            **common(principal_id, expected_revision, expected_branch_id, idempotency_key),
        )

    @mcp.tool()
    def conflict_end(
        campaign_id: str,
        data: dict[str, Any],
        expected_revision: int,
        expected_branch_id: str,
        idempotency_key: str,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        return runtime.conflict(
            campaign_id,
            action="end",
            data=data,
            **common(principal_id, expected_revision, expected_branch_id, idempotency_key),
        )

    @mcp.tool()
    def snapshot_query(
        campaign_id: str,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
        query: SearchText = "",
        limit: PageLimit = 50,
        cursor: PageCursor = None,
    ) -> dict[str, Any]:
        runtime.access.require_campaign(campaign_id, principal_id, roles=ADMIN_ROLES)
        terms = [term.casefold() for term in query.split() if term.strip()]
        values = [asdict(item) for item in runtime.snapshots.list(campaign_id)]
        if terms:
            values = [
                item
                for item in values
                if all(term in json.dumps(item, sort_keys=True).casefold() for term in terms)
            ]
        page, next_cursor = _bounded_page(values, limit=limit, cursor=cursor)
        return {"snapshots": page, "next_cursor": next_cursor}

    @mcp.tool()
    def snapshot_change(
        campaign_id: str,
        action: Literal["create", "restore"],
        label: str = "",
        slot: int | None = None,
        expected_revision: int = 0,
        expected_branch_id: str = "",
        idempotency_key: str = "",
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        runtime.access.require_campaign(campaign_id, principal_id, roles=ADMIN_ROLES)
        if action == "restore" and slot is None:
            raise ValueError("snapshot restore requires slot")
        if action == "create":
            return runtime.snapshot_create(
                campaign_id,
                label=label,
                principal_id=principal_id,
                expected_revision=expected_revision,
                expected_branch_id=expected_branch_id,
                idempotency_key=idempotency_key,
            )
        runtime.require_no_open_npc_conversation(campaign_id)
        scope = f"narrative:snapshot:{action}:{campaign_id}:{expected_branch_id}:{principal_id}"
        payload = {
            "action": action,
            "label": label,
            "slot": slot,
            "expected_revision": expected_revision,
        }
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("idempotency_key is required")
        replay = runtime.idempotency.lookup(scope, key, payload)
        if replay is not None and replay.response is not None:
            return deepcopy(replay.response)
        write = IdempotencyWrite(scope, payload, lambda result: {"snapshot": asdict(result)})
        result = runtime.snapshots.restore(
            campaign_id,
            int(slot),
            expected_revision=expected_revision,
            expected_branch_id=expected_branch_id,
            idempotency_key=key,
            idempotency_write=write,
        )
        return {"snapshot": asdict(result)}

    @mcp.tool()
    def branch_query(
        campaign_id: str,
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
        query: SearchText = "",
        limit: PageLimit = 50,
        cursor: PageCursor = None,
    ) -> dict[str, Any]:
        runtime.access.require_campaign(campaign_id, principal_id, roles=ADMIN_ROLES)
        terms = [term.casefold() for term in query.split() if term.strip()]
        values = [asdict(item) for item in runtime.branches.list(campaign_id)]
        if terms:
            values = [
                item
                for item in values
                if all(term in json.dumps(item, sort_keys=True).casefold() for term in terms)
            ]
        page, next_cursor = _bounded_page(values, limit=limit, cursor=cursor)
        return {"branches": page, "next_cursor": next_cursor}

    @mcp.tool()
    def branch_change(
        campaign_id: str,
        action: Literal["create", "checkout"],
        name: str = "",
        branch_id: str | None = None,
        checkout: bool = False,
        expected_revision: int = 0,
        expected_branch_id: str = "",
        idempotency_key: str = "",
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
    ) -> dict[str, Any]:
        runtime.access.require_campaign(campaign_id, principal_id, roles=ADMIN_ROLES)
        runtime.require_no_open_npc_conversation(campaign_id)
        scope = f"narrative:branch:{action}:{campaign_id}:{expected_branch_id}:{principal_id}"
        payload = {
            "action": action,
            "name": name,
            "branch_id": branch_id,
            "checkout": checkout,
            "expected_revision": expected_revision,
        }
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("idempotency_key is required")
        replay = runtime.idempotency.lookup(scope, key, payload)
        if replay is not None and replay.response is not None:
            return deepcopy(replay.response)

        def branch_response(result: Any) -> dict[str, Any]:
            value = result.get("branch") if isinstance(result, dict) else result
            revision = int(expected_revision) + (
                1 if action == "create" else int(str(branch_id or "") != str(expected_branch_id))
            )
            return {"branch": asdict(value), "campaign_revision": revision}

        write = IdempotencyWrite(scope, payload, branch_response)
        if action == "checkout" and not branch_id:
            raise ValueError("branch checkout requires branch_id")
        result = (
            runtime.branches.create(
                campaign_id,
                name=name,
                checkout=checkout,
                expected_revision=expected_revision,
                expected_branch_id=expected_branch_id,
                idempotency_key=key,
                idempotency_write=write,
            )
            if action == "create"
            else runtime.branches.checkout(
                campaign_id,
                str(branch_id),
                expected_revision=expected_revision,
                expected_branch_id=expected_branch_id,
                idempotency_key=key,
                idempotency_write=write,
            )
        )
        return {
            "branch": asdict(result),
            "campaign_revision": runtime.campaigns.get(campaign_id).revision,
        }

    @mcp.tool()
    def state_revision(
        campaign_id: str,
        action: Literal["list"],
        principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID,
        query: SearchText = "",
        limit: PageLimit = 100,
        cursor: PageCursor = None,
    ) -> dict[str, Any]:
        runtime.access.require_campaign(campaign_id, principal_id, roles=ADMIN_ROLES)
        terms = [term.casefold() for term in query.split() if term.strip()]
        values = [asdict(item) for item in runtime.revisions.history(campaign_id, limit=500)]
        if terms:
            values = [
                item
                for item in values
                if all(term in json.dumps(item, sort_keys=True).casefold() for term in terms)
            ]
        page, next_cursor = _bounded_page(values, limit=limit, cursor=cursor)
        return {
            "revisions": page,
            "next_cursor": next_cursor,
        }

    for registered_tool in mcp._tool_manager.list_tools():
        apply_public_contract(registered_tool)
        name = registered_tool.name
        read_only = name in {
            "server_capabilities",
            "campaign_query",
            "skill_query",
            "actor_query",
            "narrative_query",
            "continuity_query",
            "conflict_query",
            "snapshot_query",
            "branch_query",
            "state_revision",
        }
        destructive = name in {
            "access_change",
            "scene_change",
            "conflict_end",
            "snapshot_change",
            "branch_change",
        }
        properties = dict(registered_tool.parameters.get("properties") or {})
        registered_tool.annotations = ToolAnnotations(
            read_only_hint=read_only,
            destructive_hint=destructive,
            idempotent_hint=read_only or "idempotency_key" in properties,
            open_world_hint=False,
        )
        registered_tool.meta = {
            **dict(registered_tool.meta or {}),
            "sagasmith_domain_context": "sagasmith-narrative",
        }
        if registered_tool.name == "campaign_query":
            registered_tool.meta["sagasmith_context_sync"] = True

    return mcp


def main() -> None:
    config = McpConfig.from_environment()
    transport = os.environ.get("SAGASMITH_NARRATIVE_MCP_TRANSPORT", "stdio").strip().casefold()
    if transport not in {"stdio", "streamable-http"}:
        raise ValueError("SAGASMITH_NARRATIVE_MCP_TRANSPORT must be 'stdio' or 'streamable-http'")
    if (
        transport == "streamable-http"
        and config.http_host
        not in {
            "127.0.0.1",
            "::1",
            "localhost",
        }
        and config.auth_context_secret is None
    ):
        raise ValueError(
            "Narrative non-loopback Streamable HTTP requires SAGASMITH_AUTH_CONTEXT_SECRET"
        )
    server = create_server(config)
    if transport == "streamable-http":
        server.run(
            transport=transport,
            host=config.http_host,
            port=config.http_port,
            streamable_http_path=config.http_path,
        )
    else:
        server.run(transport=transport)


if __name__ == "__main__":
    main()
