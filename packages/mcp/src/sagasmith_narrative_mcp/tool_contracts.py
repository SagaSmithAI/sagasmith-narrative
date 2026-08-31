"""Public MCP catalog contracts.

The runtime deliberately accepts profile- and Pack-defined nested documents.  The
public tool boundary is still closed at the top level: every tool and parameter is
described, every collection/string/object input is bounded, and every result lists
the fields the Host may rely on.  Only explicitly extensible nested documents use
``additionalProperties``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class PublicContractValidationError(ValueError):
    """Safe argument-bound failure raised before a public tool body executes."""


TOOL_DESCRIPTIONS = {
    "server_capabilities": "Describe Narrative authority, protocol, catalog, and identity.",
    "campaign_query": "List filtered accessible campaigns or read one campaign summary.",
    "skill_query": "List, search, or read bounded installed Narrative workflow guidance.",
    "exposure": (
        "Create or inspect a Host guidance handle without changing the modern catalog. "
        "In the legacy initialized adapter, search then set the returned handle to load "
        "Lobby tools. In Lobby, set only tools currently available in that context; after "
        "game_phase(phase='play'), reuse the same campaign-bound handle and search then set "
        "the Play tools. A set containing an unavailable tool fails atomically."
    ),
    "campaign_setup": (
        "Create a campaign with an idempotent owner grant and initial branch. "
        "After creation, open a campaign-bound exposure, then query profile and Pack state; "
        "an empty campaign has no built-in defaults, so author drafts with profile_change and "
        "pack_change before entering Play."
    ),
    "access_change": "Grant or revoke campaign, actor, or element authority at a revision.",
    "profile_change": (
        "Create, revise, finalize, or activate a versioned Narrative game profile. "
        "Use create_draft with an authored profile document; no default profile is synthesized. "
        'Minimal Level-0 profile JSON: {"id":"profile.example","version":"1","mechanics_level":0,'
        '"capabilities":[],"authority":{},"actor_schema":{"type":"object"},"record_extensions":{},'
        '"mechanics":[],"sources":[{"type":"self-authored","citation":"source"}]}. '
        'Then finalize and '
        "activate with profile_key='profile.example@1'."
    ),
    "pack_change": (
        "Create, revise, finalize, import, or activate a versioned content Pack. "
        "Use create_draft with an authored Pack document; no default Pack is synthesized. "
        'Minimal campaign_seed Pack JSON: {"id":"seed.example","version":"1","title":"Seed",'
        '"kind":"campaign_seed","profile_requirements":[],"dependencies":[],"sources":[{"type":"self-authored",'
        '"citation":"source"}],"rights":{"distribution":"private","license":"self-authored"},'
        '"review":{"agent_finalization":true},"content":{"principals":[],"actors":[],"records":[],'
        '"actor_knowledge":[]}}. Then finalize, import, '
        "and activate with pack_key='seed.example@1'; apply_seed materializes non-empty "
        "seed entries."
    ),
    "game_phase": (
        "Move a campaign between lobby and play at an expected revision and branch. After "
        "entering Play, reuse the campaign-bound exposure handle and search then set the "
        "tools available in Play."
    ),
    "actor_query": "List visible actors with bounded filtering or read one authorized actor.",
    "actor_change": "Create or update one actor with optimistic revision and idempotency checks.",
    "scene_change": "Start, update, or end the current narrative scene atomically.",
    "narrative_query": "Read a bounded page or one profile, Pack, scene, or narrative record.",
    "campaign_design_change": "Advance one declared narrative line with explicit evidence.",
    "campaign_expansion": "Issue or validate a bounded zero-tool narrative expansion proposal.",
    "narrative_change": "Create or update one profile-defined narrative record.",
    "narrative_settle": "Atomically settle an event, records, facts, knowledge, and snapshot.",
    "continuity_query": "Read bounded, audience-filtered continuity for a campaign or actor.",
    "mechanic_resolve": "Resolve a profile mechanic with a deterministic campaign random receipt.",
    "npc_conversation": (
        "Open, claim, refresh, propose, publish, close, or abort a participant-bounded "
        "private NPC conversation."
    ),
    "downtime_settle": "Atomically settle profile-enabled downtime and its continuity changes.",
    "world_turn_settle": "Atomically settle a profile-enabled world turn and continuity changes.",
    "conflict_start": "Start the profile-defined authoritative conflict state.",
    "conflict_query": "Read the current authoritative conflict visible to the caller.",
    "conflict_act": "Apply one authorized action to the current conflict state.",
    "conflict_end": "End the current conflict and persist the supplied resolution data.",
    "snapshot_query": "List campaign snapshots with bounded filtering and cursor pagination.",
    "snapshot_change": "Create or restore a campaign snapshot at an expected revision and branch.",
    "branch_query": "List campaign branches with bounded filtering and cursor pagination.",
    "branch_change": "Create or check out a campaign branch with optimistic concurrency checks.",
    "state_revision": "Read a bounded newest-first campaign revision history.",
}


PARAMETER_DESCRIPTIONS = {
    "action": "Requested operation for this facade tool; only the enumerated values are accepted.",
    "actor": "Profile-defined actor document with system-specific sheet and extension fields.",
    "actor_id": "Authoritative actor identifier within the selected campaign.",
    "actor_knowledge": "Audience-scoped actor knowledge changes to settle atomically.",
    "add_tool_ids": "Tool identifiers to add to this Host guidance handle.",
    "audience_scope": "Audience allowed to observe the settled event and payload.",
    "branch_id": "Authoritative branch identifier to check out.",
    "campaign_id": "Authoritative campaign identifier; never inferred from model prose.",
    "can_control": "Whether the target principal may mutate the actor or element.",
    "can_view_private": "Whether the target principal may read private actor data.",
    "changes": "Profile-defined narrative record changes to settle atomically.",
    "checkout": "Whether a newly created branch becomes the current branch immediately.",
    "conversation_id": "Opaque server-issued NPC conversation handle.",
    "current_refs": "Bounded authoritative references currently relevant to the actor or scene.",
    "cursor": "Opaque cursor from the preceding response; omit for the first page.",
    "data": "Action-specific bounded data object; nested keys follow the active profile contract.",
    "description": "Human-readable campaign description.",
    "element_ref": "Profile-defined authority element reference.",
    "entity_id": "Identifier of the declared front, thread, clue, or character arc.",
    "entity_type": "Declared narrative-line type advanced by this evidence-bound change.",
    "evidence_refs": (
        "Authoritative event, scene, record, or fact references supporting the change."
    ),
    "event": "Authoritative event document to commit during settlement.",
    "expected_actor_revision": "Actor revision that must still be current for an update.",
    "expected_branch_id": "Branch that must still be current before a write commits.",
    "expected_record_revision": "Record revision that must still be current for an update.",
    "expected_revision": "Campaign revision that must still be current before a write commits.",
    "exposure_handle": "Server-issued Host guidance handle; it is not an authorization capability.",
    "facts": "Objective continuity facts to settle atomically.",
    "idempotency_key": "Stable business-operation key reused unchanged across retries.",
    "inputs": "Bounded mechanic inputs defined by the active profile.",
    "kind": "Narrative document kind to query.",
    "label": "Human-readable snapshot label.",
    "limit": "Maximum records to return; the server rejects values outside 1 through 100.",
    "mechanic_id": "Identifier of a mechanic declared by the active profile.",
    "name": "Human-readable campaign or branch name.",
    "note": "Optional bounded facilitator note explaining the evidence-bound change.",
    "npc_actor_id": "Actor identifier for the NPC owned by the private conversation.",
    "pack": "Versioned Pack document; content is intentionally profile extensible.",
    "pack_key": "Versioned Pack key in the form selected by the server.",
    "participants": "Bounded event participant descriptors.",
    "payload": "Bounded profile-defined event payload.",
    "phase": "Target authoritative campaign phase.",
    "principal_id": "Authenticated requester; hosted calls overwrite model-supplied values.",
    "profile": "Versioned game-profile document with declarative schemas and capabilities.",
    "profile_key": "Versioned profile key in the form selected by the server.",
    "purpose": "Continuity projection to produce; actor memory requires actor authority.",
    "query": "Case-insensitive filter text; empty text matches all authorized records.",
    "record": "Profile-defined narrative record document.",
    "record_changes": "Bounded create/update operations for profile-defined records.",
    "record_id": "Authoritative narrative record identifier.",
    "remove_tool_ids": "Tool identifiers to remove from this Host guidance handle.",
    "role": "Campaign role granted to the target principal.",
    "scene": "Profile-defined scene document.",
    "scene_id": "Authoritative scene identifier.",
    "scope": "Bounded profile-defined element authority scope.",
    "section": "Exact installed skill section heading.",
    "skill_id": "Installed Narrative skill identifier.",
    "slot": "Snapshot slot selected for restoration.",
    "slug": "Optional stable URL-safe campaign slug.",
    "snapshot": "Optional snapshot request to create during settlement.",
    "summary": "Audience-safe summary of the activity or event.",
    "status": "Declared target status accepted by the active narrative contract.",
    "target_principal_id": "Principal receiving or losing the requested authority.",
    "budget_chars": "Maximum character budget for the returned deterministic memory capsule.",
}


_LONG_TEXT = {"description": 4_000, "summary": 2_000}
_SHORT_TEXT = {
    "actor_id",
    "branch_id",
    "campaign_id",
    "conversation_id",
    "element_ref",
    "entity_id",
    "entity_type",
    "expected_branch_id",
    "exposure_handle",
    "mechanic_id",
    "npc_actor_id",
    "pack_key",
    "principal_id",
    "profile_key",
    "purpose",
    "record_id",
    "scene_id",
    "skill_id",
    "target_principal_id",
}
_COLLECTION_LIMITS = {
    "actor_knowledge": 128,
    "add_tool_ids": 64,
    "changes": 128,
    "current_refs": 128,
    "evidence_refs": 128,
    "facts": 128,
    "participants": 128,
    "record_changes": 128,
    "remove_tool_ids": 64,
}
_OBJECT_LIMITS = {
    "actor": 128,
    "data": 128,
    "event": 64,
    "inputs": 64,
    "pack": 128,
    "payload": 128,
    "profile": 128,
    "record": 128,
    "scene": 128,
    "scope": 64,
    "snapshot": 64,
}


def _json_value() -> dict[str, Any]:
    return {
        "anyOf": [
            {"type": "null"},
            {"type": "boolean"},
            {"type": "integer"},
            {"type": "number"},
            {"type": "string"},
            {"type": "array", "items": {}},
            {"type": "object", "additionalProperties": True},
        ]
    }


def _open_document(description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "additionalProperties": True,
    }


def _closed_record(
    fields: set[str],
    *,
    required: set[str] | None = None,
    free_documents: set[str] | None = None,
    nullable_documents: set[str] | None = None,
) -> dict[str, Any]:
    free_documents = free_documents or set()
    nullable_documents = nullable_documents or set()
    properties: dict[str, Any] = {}
    for field in sorted(fields):
        if field in free_documents:
            document = _open_document(
                "Profile-defined extension document validated by the active Narrative contract."
            )
            properties[field] = (
                {"anyOf": [document, {"type": "null"}]}
                if field in nullable_documents
                else document
            )
        elif field in {
            "revision",
            "sequence",
            "slot",
            "cursor_before",
            "cursor_after",
            "importance",
            "line",
        }:
            properties[field] = {"type": "integer", "minimum": 0}
        elif field == "confidence":
            properties[field] = {"type": "integer", "minimum": 0, "maximum": 5}
        elif field in {"is_current", "is_head", "applied", "redoable", "reversible"}:
            properties[field] = {"type": "boolean"}
        elif field in {
            "base_snapshot_id",
            "head_snapshot_id",
            "parent_id",
            "player_name",
            "snapshot_id",
            "source_event_id",
            "template_id",
            "valid_from",
            "valid_to",
        }:
            properties[field] = {
                "anyOf": [
                    {"type": "string", "maxLength": 8_192},
                    {"type": "null"},
                ]
            }
        elif field in {
            "accepted_proposal_ids",
            "active_context_refs",
            "draws",
            "participants",
            "publications",
            "source_event_ids",
        }:
            properties[field] = {"type": "array", "items": _json_value(), "maxItems": 10_000}
        else:
            properties[field] = {"type": "string", "maxLength": 32_768}
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = sorted(required)
    return schema


_CAMPAIGN_SCHEMA = _closed_record(
    {
        "id",
        "system_id",
        "slug",
        "name",
        "status",
        "description",
        "settings",
        "state",
        "revision",
    },
    required={"id", "system_id", "slug", "name", "status", "description", "revision"},
    free_documents={"settings", "state"},
    nullable_documents={"state"},
)
_ACTOR_SCHEMA = _closed_record(
    {
        "id",
        "actor_ref",
        "system_id",
        "campaign_id",
        "template_id",
        "character_type",
        "name",
        "player_name",
        "summary",
        "sheet",
        "notes",
        "revision",
    },
    required={"id", "system_id", "character_type", "name", "summary", "revision"},
    free_documents={"sheet", "notes"},
)
_BRANCH_SCHEMA = _closed_record(
    {"id", "campaign_id", "name", "base_snapshot_id", "head_snapshot_id", "is_current"},
    required={"id", "campaign_id", "name", "is_current"},
)
_SNAPSHOT_SCHEMA = _closed_record(
    {
        "id",
        "campaign_id",
        "parent_id",
        "slot",
        "label",
        "checksum",
        "is_head",
        "created_at",
        "branch_id",
    },
    required={
        "id",
        "campaign_id",
        "slot",
        "label",
        "checksum",
        "is_head",
        "created_at",
        "branch_id",
    },
)
_REVISION_SCHEMA = _closed_record(
    {
        "id",
        "campaign_id",
        "sequence",
        "branch_key",
        "operation",
        "entity_type",
        "entity_id",
        "applied",
        "redoable",
        "mutation_group_id",
        "idempotency_key",
        "request_hash",
        "reversible",
    },
    required={"id", "campaign_id", "sequence", "operation", "applied", "reversible"},
)
_EVENT_SCHEMA = _closed_record(
    {
        "id",
        "campaign_id",
        "sequence",
        "event_type",
        "summary",
        "retrieval_text",
        "payload",
        "audience_scope",
        "created_at",
        "participants",
    },
    required={"id", "campaign_id", "sequence", "event_type", "summary", "audience_scope"},
    free_documents={"payload"},
)
_FACT_SCHEMA = _closed_record(
    {
        "id",
        "campaign_id",
        "kind",
        "subject",
        "revision_id",
        "content",
        "metadata",
        "snapshot_id",
        "fact_key",
        "subject_ref",
        "predicate",
        "status",
        "valid_from",
        "valid_to",
        "source_event_ids",
        "importance",
        "disclosure_scope",
        "created_at",
        "updated_at",
    },
    required={"id", "campaign_id", "kind", "content", "status"},
    free_documents={"metadata"},
)
_KNOWLEDGE_SCHEMA = _closed_record(
    {
        "id",
        "campaign_id",
        "actor_id",
        "knowledge_key",
        "subject_ref",
        "revision_id",
        "proposition",
        "epistemic_status",
        "confidence",
        "source_event_id",
        "cause",
        "disclosure_scope",
    },
    required={"id", "campaign_id", "actor_id", "knowledge_key", "proposition"},
)


def _field_schema(name: str) -> dict[str, Any]:
    if name in {
        "actor_knowledge_materialized",
        "actors_created",
        "campaign_revision",
        "element_grants_materialized",
        "records_materialized",
        "revision",
        "schema_version",
        "slot",
        "ttl_ms",
        "lease_expires_at_ns",
    }:
        return {"type": "integer", "minimum": 0}
    if name in {
        "can_control",
        "can_view_private",
        "changed",
        "checkout",
        "explicit_exposure_handle",
        "is_current",
        "loopback_streamable_http_supported",
        "native_dynamic_tools",
        "shared_network_transport_supported",
    }:
        return {"type": "boolean"}
    if name == "next_cursor":
        return {"type": ["string", "null"], "maxLength": 128}
    if name in {
        "active_scene_id",
        "campaign_id",
        "parent_id",
        "player_name",
        "template_id",
    }:
        return {
            "anyOf": [
                {"type": "string", "maxLength": 8_192},
                {"type": "null"},
            ]
        }
    if name == "actors":
        return {"type": "array", "items": deepcopy(_ACTOR_SCHEMA), "maxItems": 10_000}
    if name == "branches":
        return {"type": "array", "items": deepcopy(_BRANCH_SCHEMA), "maxItems": 10_000}
    if name == "snapshots":
        return {"type": "array", "items": deepcopy(_SNAPSHOT_SCHEMA), "maxItems": 10_000}
    if name == "revisions":
        return {"type": "array", "items": deepcopy(_REVISION_SCHEMA), "maxItems": 10_000}
    if name == "events":
        return {"type": "array", "items": deepcopy(_EVENT_SCHEMA), "maxItems": 10_000}
    if name == "facts":
        return {"type": "array", "items": deepcopy(_FACT_SCHEMA), "maxItems": 10_000}
    if name == "actor_knowledge":
        return {"type": "array", "items": deepcopy(_KNOWLEDGE_SCHEMA), "maxItems": 10_000}
    if name == "actor_bindings":
        return {
            "type": "object",
            "description": "Profile actor references mapped to authoritative actor identifiers.",
            "additionalProperties": {"type": "string", "maxLength": 8_192},
            "maxProperties": 10_000,
        }
    if name in {
        "loaded_tools",
        "optional_phases",
        "base_phases",
        "sections",
        "settlement_route",
        "visible_tools",
    }:
        return {
            "type": "array",
            "items": {"type": "string", "maxLength": 8_192},
            "maxItems": 10_000,
        }
    if name == "mechanics_levels":
        return {"type": "array", "items": {"type": "integer", "minimum": 0}, "maxItems": 32}
    if name == "skills":
        return {
            "type": "array",
            "items": _closed_record({"id", "path"}, required={"id", "path"}),
            "maxItems": 10_000,
        }
    if name == "hits":
        return {
            "type": "array",
            "items": _closed_record(
                {"skill_id", "line", "excerpt"}, required={"skill_id", "line", "excerpt"}
            ),
            "maxItems": 10_000,
        }
    if name == "matches":
        match = _closed_record(
            {"tool_id", "description", "loaded"}, required={"tool_id", "description", "loaded"}
        )
        match["properties"]["loaded"] = {"type": "boolean"}
        return {"type": "array", "items": match, "maxItems": 10_000}
    if name in {"items", "records", "module_evidence"}:
        return {
            "type": "array",
            "description": "Profile-defined narrative projection records.",
            "items": _open_document("Profile-defined narrative projection record."),
            "maxItems": 10_000,
        }
    if name in {
        "active",
        "conflict",
        "npc_conversation",
        "profile",
        "scoped_scene",
        "snapshot",
        "state",
    }:
        return {"anyOf": [{"type": "object", "additionalProperties": True}, {"type": "null"}]}
    if name in {
        "actor",
        "activation",
        "actor_memory",
        "budget",
        "context",
        "context_receipt",
        "constraints",
        "campaign_design",
        "change",
        "conversation",
        "finalized",
        "imports",
        "memory",
        "pack",
        "proposal",
        "proposal_attestation",
        "publication",
        "record",
        "result",
        "scene",
        "sheet",
        "settings",
        "notes",
        "worker_contract",
    }:
        return _open_document(
            "Profile-defined extension document validated by the active Narrative contract."
        )
    if name == "branch":
        return deepcopy(_BRANCH_SCHEMA)
    if name == "event":
        return deepcopy(_EVENT_SCHEMA)
    if name == "catalog_cache":
        cache = _closed_record({"scope", "ttl_ms"}, required={"scope", "ttl_ms"})
        cache["properties"]["ttl_ms"] = {"type": "integer", "minimum": 0}
        return cache
    if name == "authoritative_contract":
        contract = _closed_record(
            {
                "schema",
                "protocols",
                "transports",
                "shared_handlers",
                "dynamic_tool_exposure",
                "revision_model",
                "idempotency_model",
                "authority_model",
                "error_model",
            },
            required={"schema", "protocols", "transports", "shared_handlers"},
        )
        contract["properties"]["protocols"] = _closed_record(
            {"2026-07-28", "2025-11-25"}, required={"2026-07-28", "2025-11-25"}
        )
        contract["properties"]["transports"] = {
            "type": "array",
            "items": {"type": "string", "enum": ["stdio", "streamable-http"]},
            "maxItems": 2,
        }
        contract["properties"]["shared_handlers"] = {"type": "boolean"}
        return contract
    if name == "random_stream_receipt":
        return _closed_record(
            {"profile_checksum", "mechanic_id", "cursor_before", "cursor_after", "draws"},
            required={"profile_checksum", "mechanic_id", "cursor_before", "cursor_after", "draws"},
        )
    if name == "retrieval":
        retrieval = _closed_record(
            {
                "strategy",
                "query",
                "budget_chars",
                "used_chars",
                "structured_ledger_chars",
                "pinned_module_evidence_chars",
                "pinned_budget_overflow",
                "candidate_count",
                "returned_count",
                "truncated",
                "active_context_refs",
                "pinned_module_evidence_count",
                "pagination",
            },
            required={"strategy", "query", "budget_chars", "used_chars", "truncated"},
        )
        for integer_field in {
            "budget_chars",
            "used_chars",
            "structured_ledger_chars",
            "pinned_module_evidence_chars",
            "candidate_count",
            "returned_count",
            "pinned_module_evidence_count",
        }:
            retrieval["properties"][integer_field] = {"type": "integer", "minimum": 0}
        for boolean_field in {"pinned_budget_overflow", "truncated"}:
            retrieval["properties"][boolean_field] = {"type": "boolean"}
        page = _closed_record(
            {"offset", "page_limit", "has_more", "next_offset", "streams"},
            required={"offset", "page_limit", "has_more", "next_offset", "streams"},
        )
        for field in {"offset", "page_limit"}:
            page["properties"][field] = {"type": "integer", "minimum": 0}
        page["properties"]["has_more"] = {"type": "boolean"}
        page["properties"]["next_offset"] = {
            "anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]
        }
        stream = _closed_record(
            {"candidate_count", "has_more"}, required={"candidate_count", "has_more"}
        )
        stream["properties"]["candidate_count"] = {"type": "integer", "minimum": 0}
        stream["properties"]["has_more"] = {"type": "boolean"}
        page["properties"]["streams"] = _closed_record(
            {"facts", "events", "actor_knowledge"},
            required={"facts", "events", "actor_knowledge"},
        )
        for field in {"facts", "events", "actor_knowledge"}:
            page["properties"]["streams"]["properties"][field] = deepcopy(stream)
        retrieval["properties"]["pagination"] = page
        return retrieval
    if name == "campaigns":
        return {"type": "array", "items": deepcopy(_CAMPAIGN_SCHEMA), "maxItems": 10_000}
    if name == "publications":
        return {
            "type": "array",
            "items": _open_document("Audience-safe NPC publication payload."),
            "maxItems": 10_000,
        }
    return {"type": "string", "maxLength": 8_192}


_CAMPAIGN_FIELDS = {
    "id",
    "system_id",
    "slug",
    "name",
    "status",
    "description",
    "settings",
    "state",
    "revision",
    "phase",
    "profile",
    "role",
    "active_scene_id",
}
_ACTOR_FIELDS = {
    "id",
    "actor_ref",
    "system_id",
    "campaign_id",
    "template_id",
    "character_type",
    "name",
    "player_name",
    "summary",
    "sheet",
    "notes",
    "revision",
}
_WRITE_FIELDS = {"campaign_id", "campaign_revision", "branch_id", "phase"}
_SETTLEMENT_FIELDS = _WRITE_FIELDS | {
    "event",
    "records",
    "facts",
    "actor_knowledge",
    "snapshot",
    "npc_conversation",
}


OUTPUT_FIELDS = {
    "server_capabilities": {
        "system_id",
        "schema_version",
        "authoritative_contract",
        "base_phases",
        "optional_phases",
        "mechanics_levels",
        "native_dynamic_tools",
        "tools_list_changed",
        "catalog_cache",
        "explicit_exposure_handle",
        "identity_mode",
        "loopback_streamable_http_supported",
        "shared_network_transport_supported",
    },
    "campaign_query": _CAMPAIGN_FIELDS | {"campaigns", "next_cursor"},
    "skill_query": {
        "skills",
        "hits",
        "query",
        "next_cursor",
        "skill_id",
        "sections",
        "section",
        "content",
    },
    "exposure": {
        "exposure_id",
        "exposure_handle",
        "revision",
        "principal_id",
        "campaign_id",
        "phase",
        "loaded_tools",
        "visible_tools",
        "ttl_ms",
        "catalog_effect",
        "matches",
        "next_cursor",
        "changed",
    },
    "campaign_setup": _CAMPAIGN_FIELDS,
    "access_change": _WRITE_FIELDS
    | {
        "principal_id",
        "actor_id",
        "actor_ref",
        "element_ref",
        "target_principal_id",
        "role",
        "can_control",
        "can_view_private",
        "status",
    },
    "profile_change": _WRITE_FIELDS | {"profile_key", "profile", "status"},
    "pack_change": _WRITE_FIELDS
    | {
        "pack_key",
        "pack",
        "status",
        "actor_bindings",
        "actors_created",
        "records_materialized",
        "element_grants_materialized",
        "actor_knowledge_materialized",
    },
    "game_phase": _WRITE_FIELDS | {"previous_phase"},
    "actor_query": _ACTOR_FIELDS | {"actors", "next_cursor"},
    "actor_change": _WRITE_FIELDS | _ACTOR_FIELDS,
    "scene_change": _WRITE_FIELDS | {"scene"},
    "narrative_query": {
        "items",
        "next_cursor",
        "active",
        "finalized",
        "drafts",
        "finalized_profiles",
        "finalized_packs",
        "imports",
        "pack_key",
        "pack",
        "profile_key",
        "profile",
        "record",
        "scene",
        "status",
        "campaign_design",
    },
    "campaign_design_change": _WRITE_FIELDS | {"campaign_design", "change"},
    "campaign_expansion": {
        "schema_version",
        "status",
        "context",
        "context_receipt",
        "budget",
        "worker_contract",
        "proposal",
        "proposal_attestation",
        "campaign_design",
        "settlement_route",
    },
    "narrative_change": _WRITE_FIELDS | {"record"},
    "narrative_settle": _SETTLEMENT_FIELDS,
    "continuity_query": {
        "schema_version",
        "purpose",
        "campaign_id",
        "branch_id",
        "actor_id",
        "actor_ref",
        "audience",
        "branch",
        "facts",
        "events",
        "actor_knowledge",
        "module_evidence",
        "scoped_scene",
        "retrieval",
        "memory",
        "constraints",
        "next_cursor",
    },
    "mechanic_resolve": _WRITE_FIELDS | {"mechanic_id", "result", "random_stream_receipt"},
    "npc_conversation": _WRITE_FIELDS
    | {
        "schema_version",
        "conversation",
        "conversation_id",
        "activation",
        "activation_id",
        "activation_ref",
        "actor_runtime_id",
        "worker_id",
        "lease_id",
        "lease_expires_at_ns",
        "context_receipt",
        "actor_memory",
        "constraints",
        "close_token",
        "proposal_id",
        "publication",
        "status",
        "publications",
    },
    "downtime_settle": _SETTLEMENT_FIELDS,
    "world_turn_settle": _SETTLEMENT_FIELDS,
    "conflict_start": _WRITE_FIELDS | {"conflict"},
    "conflict_query": {"conflict"},
    "conflict_act": _WRITE_FIELDS | {"conflict"},
    "conflict_end": _WRITE_FIELDS | {"conflict"},
    "snapshot_query": {"snapshots", "next_cursor"},
    "snapshot_change": _WRITE_FIELDS | {"snapshot"},
    "branch_query": {"branches", "next_cursor"},
    "branch_change": _WRITE_FIELDS | {"branch"},
    "state_revision": {"revisions", "next_cursor"},
}


def _bounded_input_schema(tool_name: str, schema: dict[str, Any]) -> dict[str, Any]:
    value = deepcopy(schema)
    for name, property_schema in (value.get("properties") or {}).items():
        property_schema["description"] = PARAMETER_DESCRIPTIONS[name]
        variants = property_schema.get("anyOf") or [property_schema]
        for variant in variants:
            if not isinstance(variant, dict) or variant.get("type") == "null":
                continue
            if variant.get("type") == "string" and not any(
                key in variant for key in ("maxLength", "enum", "const")
            ):
                variant["maxLength"] = _LONG_TEXT.get(name, 128 if name in _SHORT_TEXT else 512)
            elif variant.get("type") == "integer" and not any(
                key in variant for key in ("maximum", "exclusiveMaximum", "enum", "const")
            ):
                variant.setdefault("minimum", 0)
                variant["maximum"] = 2_147_483_647
            elif variant.get("type") == "array":
                variant.setdefault("maxItems", _COLLECTION_LIMITS.get(name, 128))
            elif variant.get("type") == "object" and variant.get("additionalProperties"):
                variant.setdefault("maxProperties", _OBJECT_LIMITS.get(name, 128))
    value["title"] = f"{tool_name}Input"
    return value


def _output_schema(tool_name: str) -> dict[str, Any]:
    fields = OUTPUT_FIELDS[tool_name]
    base = {
        "title": f"{tool_name}Output",
        "type": "object",
        "properties": {name: _field_schema(name) for name in sorted(fields)},
        "additionalProperties": False,
        "minProperties": 1,
    }
    if tool_name != "narrative_query":
        return base

    string_list = {
        "type": "array",
        "items": {"type": "string", "maxLength": 8_192},
        "maxItems": 10_000,
    }
    base["properties"].update(
        {
            "active": {
                "anyOf": [
                    {"type": "object", "additionalProperties": True},
                    deepcopy(string_list),
                    {"type": "null"},
                ]
            },
            "finalized": deepcopy(string_list),
            "drafts": _open_document("Profile or Pack drafts visible to an administrator."),
            "finalized_profiles": _open_document("Finalized profiles visible to an administrator."),
            "finalized_packs": _open_document("Finalized Packs visible to an administrator."),
            "imports": _open_document("Pack import status records keyed by Pack version."),
        }
    )
    direct_record = _closed_record(
        {"id", "kind", "title", "status", "revision", "audience", "controller", "source", "data"},
        required={"id", "kind", "title", "status", "revision"},
        free_documents={"audience", "controller", "source", "data"},
    )
    direct_record["title"] = "narrative_queryDirectRecordOutput"
    return {
        "title": "narrative_queryOutput",
        "type": "object",
        "oneOf": [base, direct_record],
    }


def apply_public_contract(tool: Any) -> None:
    """Attach the audited public contract to one registered SDK tool."""

    tool.description = TOOL_DESCRIPTIONS[tool.name]
    tool.parameters = _bounded_input_schema(tool.name, tool.parameters)
    tool.output_schema = _output_schema(tool.name)


def _matching_variants(schema: dict[str, Any], value: Any) -> list[dict[str, Any]]:
    variants = schema.get("anyOf")
    if not isinstance(variants, list):
        return [schema]
    matched: list[dict[str, Any]] = []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        value_type = variant.get("type")
        if value is None and value_type == "null":
            matched.append(variant)
        elif value_type == "string" and isinstance(value, str):
            matched.append(variant)
        elif value_type == "integer" and isinstance(value, int) and not isinstance(value, bool):
            matched.append(variant)
        elif (
            value_type == "number"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            matched.append(variant)
        elif value_type == "boolean" and isinstance(value, bool):
            matched.append(variant)
        elif value_type == "array" and isinstance(value, list):
            matched.append(variant)
        elif value_type == "object" and isinstance(value, dict):
            matched.append(variant)
    return matched


def validate_public_arguments(tool: Any, arguments: dict[str, Any]) -> None:
    """Enforce the advertised top-level size/range bounds before execution.

    The SDK's Pydantic model remains responsible for types, literals, required
    arguments and defaults.  This additional check keeps the constraints added
    to the published schema from being documentation-only.
    """

    properties = dict(tool.parameters.get("properties") or {})
    for name, value in arguments.items():
        schema = properties.get(name)
        if not isinstance(schema, dict):
            continue
        for variant in _matching_variants(schema, value):
            if value is None:
                continue
            if isinstance(value, str):
                minimum = variant.get("minLength")
                maximum = variant.get("maxLength")
                if isinstance(minimum, int) and len(value) < minimum:
                    raise PublicContractValidationError(
                        f"{name} must contain at least {minimum} characters"
                    )
                if isinstance(maximum, int) and len(value) > maximum:
                    raise PublicContractValidationError(
                        f"{name} must contain at most {maximum} characters"
                    )
            elif isinstance(value, int) and not isinstance(value, bool):
                minimum = variant.get("minimum")
                maximum = variant.get("maximum")
                if isinstance(minimum, (int, float)) and value < minimum:
                    raise PublicContractValidationError(f"{name} must be at least {minimum:g}")
                if isinstance(maximum, (int, float)) and value > maximum:
                    raise PublicContractValidationError(f"{name} must be at most {maximum:g}")
            elif isinstance(value, list):
                maximum = variant.get("maxItems")
                if isinstance(maximum, int) and len(value) > maximum:
                    raise PublicContractValidationError(
                        f"{name} must contain at most {maximum} items"
                    )
            elif isinstance(value, dict):
                maximum = variant.get("maxProperties")
                if isinstance(maximum, int) and len(value) > maximum:
                    raise PublicContractValidationError(
                        f"{name} must contain at most {maximum} properties"
                    )
