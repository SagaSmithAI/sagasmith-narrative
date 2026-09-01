"""Small authoritative facade over sagasmith-core campaign primitives."""

from __future__ import annotations

import hashlib
import hmac
import random
import secrets
import time
import uuid
from copy import deepcopy
from dataclasses import asdict
from typing import Any, Callable, Mapping
from uuid import uuid4

import jsonschema
from sagasmith_core import (
    AccessService,
    ActorKnowledgeService,
    BranchService,
    CampaignService,
    CharacterService,
    ContinuityCommitService,
    ContinuityService,
    EventService,
    IdempotencyService,
    MemoryService,
    RevisionService,
    SnapshotService,
    StateMutationService,
)
from sagasmith_core.branches import resolve_branch
from sagasmith_core.database import Database
from sagasmith_core.idempotency import request_hash
from sagasmith_core.integrity import canonical_json
from sagasmith_core.knowledge import ACTOR_KNOWLEDGE_DISCLOSURE_SCOPES
from sagasmith_core.models import (
    ActorGrant,
    ActorKnowledge,
    ActorKnowledgeRevision,
    BranchActorKnowledgeHead,
    Campaign,
    CampaignEvent,
    CampaignMembership,
    CampaignMemory,
    Character,
    Principal,
)
from sagasmith_core.visibility import (
    PLAYER_MEMORY_DISCLOSURE_SCOPES,
    PLAYER_OWNED_ACTOR_DISCLOSURE_SCOPES,
)
from sagasmith_narrative.contracts import (
    CAMPAIGN_ROLES,
    PHASE_CONFLICT,
    PHASE_LOBBY,
    PHASE_PLAY,
    active_profile,
    checksum,
    initial_document,
    narrative_document,
    required_id,
    required_text,
    state_with_narrative,
    validate_audience,
    validate_profile,
    validate_record,
    validate_sources,
)
from sagasmith_narrative.narrative_design import (
    validate_campaign_design,
    validate_progress_change,
    validate_runtime_manifest,
)
from sqlalchemy import delete, select, update

from .actor_memory import select_actor_memory_context

ADMIN_ROLES = {"owner", "dm"}
PUBLIC_ACTOR_DISCLOSURE_SCOPES = frozenset({"party", "public", "player"})
PRIVATE_EVENT_AUDIENCE = "dm"
PRIVATE_KNOWLEDGE_DISCLOSURE = "dm"


class NarrativeRuntime:
    """Authoritative service used by every public MCP tool.

    Campaign documents are one revisioned authority. MCP transport serializes
    writes per campaign; this class still validates revision and branch at the
    transaction boundary so direct callers cannot bypass CAS semantics.
    """

    def __init__(
        self, database: Database, *, proposal_attestation_secret: str | bytes | None = None
    ) -> None:
        self.database = database
        self.campaigns = CampaignService(database)
        self.characters = CharacterService(database)
        self.access = AccessService(database)
        self.snapshots = SnapshotService(database)
        self.branches = BranchService(database)
        self.revisions = RevisionService(database)
        self.events = EventService(database)
        self.facts = MemoryService(database)
        self.knowledge = ActorKnowledgeService(database)
        self.continuity = ContinuityService(database)
        self.continuity_commits = ContinuityCommitService(database)
        self.idempotency = IdempotencyService(database)
        self.state_mutations = StateMutationService(database)
        if proposal_attestation_secret is None:
            self._proposal_secret = secrets.token_bytes(32)
        elif isinstance(proposal_attestation_secret, str):
            self._proposal_secret = proposal_attestation_secret.encode("utf-8")
        else:
            self._proposal_secret = bytes(proposal_attestation_secret)
        if len(self._proposal_secret) < 32:
            raise ValueError("proposal_attestation_secret must contain at least 32 bytes")

    def _sign_expansion_envelope(self, value: Mapping[str, Any]) -> str:
        message = (
            b"sagasmith-narrative/campaign-expansion/v1\0"
            + canonical_json(value).encode("utf-8")
        )
        return hmac.new(self._proposal_secret, message, hashlib.sha256).hexdigest()

    def continuity_cursor(
        self, *, binding: Mapping[str, Any], next_offset: int
    ) -> str:
        """Issue a restart-stable cursor bound to one authorized continuity query."""

        offset = int(next_offset)
        if not 0 <= offset <= 100_000:
            raise ValueError("continuity cursor offset is outside the supported range")
        message = (
            b"sagasmith-narrative/continuity-cursor/v1\0"
            + canonical_json({"binding": dict(binding), "offset": offset}).encode("utf-8")
        )
        signature = hmac.new(self._proposal_secret, message, hashlib.sha256).hexdigest()
        return f"c1:{offset}:{signature}"

    def continuity_cursor_offset(
        self, cursor: str | None, *, binding: Mapping[str, Any]
    ) -> int:
        """Validate an opaque query-bound cursor and return its Core offset."""

        if cursor is None:
            return 0
        value = required_text(cursor, "cursor", limit=128)
        parts = value.split(":")
        if len(parts) != 3 or parts[0] != "c1" or not parts[1].isdigit():
            raise ValueError("continuity cursor is invalid; reuse next_cursor unchanged")
        offset = int(parts[1])
        if not 0 <= offset <= 100_000:
            raise ValueError("continuity cursor is invalid; reuse next_cursor unchanged")
        expected = self.continuity_cursor(binding=binding, next_offset=offset).rsplit(":", 1)[1]
        if not secrets.compare_digest(parts[2], expected):
            raise ValueError(
                "continuity cursor does not belong to this query; start a new query without it"
            )
        return offset

    def campaign_create(
        self,
        *,
        name: str,
        principal_id: str,
        idempotency_key: str,
        description: str = "",
        slug: str | None = None,
    ) -> dict[str, Any]:
        info = self.campaigns.create_owned(
            system_id="narrative",
            name=required_text(name, "name", limit=200),
            principal_id=principal_id,
            idempotency_key=idempotency_key,
            slug=slug,
            description=description,
            state=state_with_narrative({}, initial_document()),
        )
        return {**asdict(info), "branch_id": self.branch_id(info.id)}

    def campaign_get(self, campaign_id: str, principal_id: str) -> dict[str, Any]:
        membership = self.access.require_campaign(campaign_id, principal_id)
        campaign = self.campaigns.get(campaign_id)
        document = narrative_document(campaign.state)
        return {
            **asdict(campaign),
            "state": None,
            "phase": document["phase"],
            "profile": active_profile(document),
            "role": membership.role,
            "active_scene_id": document.get("active_scene_id"),
        }

    def campaign_list(self, principal_id: str) -> list[dict[str, Any]]:
        allowed = self.access.accessible_campaign_ids(principal_id)
        return [
            {**asdict(item), "state": None}
            for item in self.campaigns.list(system_id="narrative")
            if item.id in allowed
        ]

    def phase(self, campaign_id: str) -> str:
        return str(narrative_document(self.campaigns.get(campaign_id).state)["phase"])

    def profile_capabilities(self, campaign_id: str) -> set[str]:
        profile = active_profile(narrative_document(self.campaigns.get(campaign_id).state))
        return set(profile.get("capabilities", [])) if profile else set()

    def require_no_open_npc_conversation(self, campaign_id: str) -> None:
        document = narrative_document(self.campaigns.get(campaign_id).state)
        if any(
            item.get("status") == "open"
            for item in document.get("npc_conversations", {}).values()
        ):
            raise ValueError(
                "close or abort every NPC conversation before authoritative recovery"
            )

    def _require_live_npc_conversation_access(
        self,
        campaign_id: str,
        *,
        conversation_id: str | None,
        principal_id: str,
        expected_branch_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Recheck phase, branch, actor kind, and grant on every worker operation."""

        document = narrative_document(self.campaigns.get(campaign_id).state)
        if document["phase"] != PHASE_PLAY or document.get("conflict"):
            raise ValueError("NPC conversation requires non-conflict Play")
        current_branch_id = self.branch_id(campaign_id)
        if expected_branch_id != current_branch_id:
            raise ValueError("NPC conversation requires the current readable branch")
        identifier = required_text(conversation_id, "conversation_id", limit=100)
        conversation = dict(document.get("npc_conversations", {}).get(identifier) or {})
        if not conversation or conversation.get("status") != "open":
            raise ValueError("NPC conversation is not open")
        context_branch_id = str(
            dict(conversation.get("private_context") or {}).get("branch_id") or ""
        )
        if context_branch_id != current_branch_id:
            raise ValueError("NPC conversation context is not on the current readable branch")
        actor_id = self.resolve_actor_ref(campaign_id, str(conversation["npc_actor_id"]))
        character = self.characters.get(actor_id)
        if character.character_type not in {"npc", "persistent_npc"}:
            raise ValueError("isolated conversation workers are NPC-only")
        membership = self.access.require_campaign(campaign_id, principal_id)
        if not self._actor_authorized(
            document,
            campaign_id=campaign_id,
            actor_id=actor_id,
            principal_id=principal_id,
            role=membership.role,
            control=True,
            private=True,
        ):
            raise PermissionError("NPC conversation requires actor control and private access")
        return document, conversation, actor_id

    @staticmethod
    def _normalize_actor_input(actor: Mapping[str, Any]) -> dict[str, Any]:
        """Normalize the public actor type spelling before any write begins."""

        value = deepcopy(dict(actor))
        if "type" in value and "character_type" in value:
            if value["type"] != value["character_type"]:
                raise ValueError(
                    "actor.type and actor.character_type must match when both are provided"
                )
        elif "type" not in value and "character_type" in value:
            value["type"] = value["character_type"]
        value.pop("character_type", None)
        return value

    @staticmethod
    def _validate_actor_profile(
        profile: Mapping[str, Any] | None, actor: Mapping[str, Any]
    ) -> None:
        schema = dict(profile.get("actor_schema") or {}) if profile else {}
        if schema:
            try:
                jsonschema.validate(instance=dict(actor.get("sheet") or {}), schema=schema)
            except jsonschema.ValidationError as error:
                raise ValueError(
                    f"actor sheet does not satisfy active profile: {error.message}"
                ) from error

    @staticmethod
    def _validate_record_profile(
        profile: Mapping[str, Any] | None, record: Mapping[str, Any]
    ) -> None:
        extension = (
            dict(dict(profile.get("record_extensions") or {}).get(record.get("kind")) or {})
            if profile
            else {}
        )
        required_data = extension.get("required_data") or []
        if not isinstance(required_data, list) or len(required_data) > 128:
            raise ValueError("profile record required_data must be a bounded list")
        data = dict(record.get("data") or {})
        missing = [str(item) for item in required_data if str(item) not in data]
        if missing:
            raise ValueError(
                f"record {record.get('id')} is missing profile data: " + ", ".join(missing)
            )
        allowed_audiences = set(
            dict(profile.get("authority") or {}).get("audience_scopes") or []
        ) if profile else set()
        scope = str(dict(record.get("audience") or {}).get("scope") or "table")
        if allowed_audiences and scope not in allowed_audiences:
            raise ValueError(f"record audience {scope!r} is not allowed by the active profile")

    def _actor_authorized(
        self,
        document: Mapping[str, Any],
        *,
        campaign_id: str,
        actor_id: str,
        principal_id: str,
        role: str,
        control: bool = False,
        private: bool = False,
    ) -> bool:
        """Apply profile narrative authority without core admin-role escalation."""

        readable, can_control, can_view_private = self._actor_access(
            document,
            campaign_id=campaign_id,
            actor_id=actor_id,
            principal_id=principal_id,
            role=role,
        )
        return bool(
            readable
            and (not control or can_control)
            and (not private or can_view_private)
        )

    def _actor_access(
        self,
        document: Mapping[str, Any],
        *,
        campaign_id: str,
        actor_id: str,
        principal_id: str,
        role: str,
    ) -> tuple[bool, bool, bool]:
        """Return readable/control/private capabilities for one real actor grant."""

        core_actor_id = str(dict(document.get("actor_bindings") or {}).get(actor_id) or actor_id)
        if self._has_facilitator_authority(document, role):
            try:
                self.access.require_actor(campaign_id, core_actor_id, principal_id)
                return True, True, True
            except (LookupError, PermissionError):
                return False, False, False
        with self.database.transaction() as session:
            actor = session.get(Character, core_actor_id)
            if actor is None or actor.campaign_id != campaign_id:
                return False, False, False
            grant = session.get(
                ActorGrant,
                {
                    "campaign_id": campaign_id,
                    "principal_id": principal_id,
                    "actor_id": core_actor_id,
                },
            )
            return (
                grant is not None,
                bool(grant and grant.can_control),
                bool(grant and grant.can_view_private),
            )

    @staticmethod
    def _validate_settlement_knowledge_audience(
        session,
        *,
        campaign_id: str,
        branch_id: str,
        settlement_audience: str,
        actor_knowledge: list[dict[str, Any]],
    ) -> None:
        """Keep private source events from backing player-visible knowledge.

        A knowledge entry may explicitly point at an existing source event;
        otherwise the settlement event is its source.  Resolve the effective
        disclosure for revisions before accepting the atomic settlement so no
        conversation, document, event, revision, or idempotency row can be
        committed for an incompatible pair.
        """

        for item in actor_knowledge:
            action = str(item.get("action") or "add")
            disclosure = item.get("disclosure_scope")
            if action == "revise" and disclosure is None:
                knowledge_id = str(item.get("knowledge_id") or "")
                knowledge = session.get(ActorKnowledge, knowledge_id)
                if knowledge is not None and knowledge.campaign_id == campaign_id:
                    head = session.get(
                        BranchActorKnowledgeHead,
                        {"branch_id": branch_id, "knowledge_id": knowledge.id},
                    )
                    current = session.get(
                        ActorKnowledgeRevision,
                        head.revision_id,
                    ) if head is not None else None
                    disclosure = current.disclosure_scope if current is not None else None
            if disclosure not in ACTOR_KNOWLEDGE_DISCLOSURE_SCOPES:
                # Preserve the core validator and its error for invalid or
                # missing values; this helper only checks valid vocab entries.
                continue

            source_audience = settlement_audience
            source_event_id = item.get("source_event_id")
            if source_event_id:
                source = session.get(CampaignEvent, str(source_event_id))
                if source is not None and source.campaign_id == campaign_id:
                    source_audience = str(source.audience_scope)
            if (
                source_audience == PRIVATE_EVENT_AUDIENCE
                and disclosure != PRIVATE_KNOWLEDGE_DISCLOSURE
            ):
                raise ValueError(
                    "private dm event cannot support actor knowledge disclosure "
                    f"{disclosure!r}; use disclosure_scope='dm' or a non-private source event"
                )

    def require_actor_authority(
        self,
        campaign_id: str,
        actor_id: str,
        principal_id: str,
        *,
        control: bool = False,
        private: bool = False,
    ) -> str:
        membership = self.access.require_campaign(campaign_id, principal_id)
        document = narrative_document(self.campaigns.get(campaign_id).state)
        core_actor_id = str(dict(document.get("actor_bindings") or {}).get(actor_id) or actor_id)
        if not self._actor_authorized(
            document,
            campaign_id=campaign_id,
            actor_id=core_actor_id,
            principal_id=principal_id,
            role=membership.role,
            control=control,
            private=private,
        ):
            raise PermissionError(f"principal does not control actor: {actor_id}")
        return core_actor_id

    def branch_id(self, campaign_id: str) -> str:
        return self.branches.current(campaign_id).id

    def readable_branch_id(
        self, campaign_id: str, principal_id: str, branch_id: str | None
    ) -> str:
        """Resolve a branch while keeping non-administrators on current authority."""

        membership = self.access.require_campaign(campaign_id, principal_id)
        current = self.branches.current(campaign_id)
        selected = current if not branch_id else self.branches.get(campaign_id, branch_id)
        if selected.id != current.id and membership.role not in ADMIN_ROLES:
            raise PermissionError("historical branch continuity is administrator-only")
        return selected.id

    def binding(self, campaign_id: str, principal_id: str) -> dict[str, Any]:
        campaign = self.campaigns.get(campaign_id)
        document = narrative_document(campaign.state)
        profile = active_profile(document)
        membership = self.access.require_campaign(campaign_id, principal_id)
        value = {
            "domain": "sagasmith-narrative",
            "campaign_id": campaign_id,
            "principal_fingerprint": hashlib.sha256(
                principal_id.encode("utf-8")
            ).hexdigest(),
            "authorization_fingerprint": self.access.authorization_fingerprint(
                campaign_id, principal_id
            ),
            "role": membership.role,
            "audience": "dm" if membership.role in ADMIN_ROLES else "player",
            "branch_id": self.branch_id(campaign_id),
            "memory_policy": "domain_authoritative",
            "campaign_revision": campaign.revision,
            "phase": document["phase"],
            "profile": (
                {key: profile[key] for key in ("id", "version", "checksum")} if profile else None
            ),
        }
        epoch_fields = {
            key: value[key]
            for key in (
                "domain",
                "campaign_id",
                "principal_fingerprint",
                "authorization_fingerprint",
                "role",
                "audience",
                "branch_id",
            )
        }
        return {
            **value,
            "context_epoch": hashlib.sha256(
                canonical_json(epoch_fields).encode("utf-8")
            ).hexdigest(),
        }

    def actor_memory_context(
        self,
        campaign_id: str,
        *,
        actor_id: str,
        principal_id: str,
        query: str = "",
        branch_id: str | None = None,
        current_refs: list[str] | None = None,
        budget_chars: int = 8_000,
    ) -> dict[str, Any]:
        """Assemble an authorized, branch-local four-track actor memory view."""

        if current_refs is not None and (
            not isinstance(current_refs, list) or len(current_refs) > 128
        ):
            raise ValueError("current_refs must be an array with at most 128 entries")
        requested_current_refs = [
            required_text(item, "current_refs[]", limit=300)
            for item in current_refs or []
        ]
        membership = self.access.require_campaign(campaign_id, principal_id)
        document = narrative_document(self.campaigns.get(campaign_id).state)
        core_actor_id = self.resolve_actor_ref(campaign_id, actor_id)
        readable, can_control, can_view_private = self._actor_access(
            document,
            campaign_id=campaign_id,
            actor_id=core_actor_id,
            principal_id=principal_id,
            role=membership.role,
        )
        if not readable:
            raise PermissionError("principal cannot access this actor memory")
        selected_branch_id = self.readable_branch_id(campaign_id, principal_id, branch_id)
        actor = self.characters.get(core_actor_id)
        aliases = [
            key
            for key, value in dict(document.get("actor_bindings") or {}).items()
            if value == core_actor_id
        ]
        subject_refs = {core_actor_id, f"actor:{core_actor_id}"}
        subject_refs.update(aliases)
        subject_refs.update(f"actor:{item}" for item in aliases)
        facts = self.facts.list_for_subject_refs(
            campaign_id,
            subject_refs=subject_refs,
            branch_id=selected_branch_id,
        )
        facilitator = self._has_facilitator_authority(document, membership.role)
        allowed_knowledge_scopes = (
            ACTOR_KNOWLEDGE_DISCLOSURE_SCOPES
            if facilitator
            else (
                PLAYER_OWNED_ACTOR_DISCLOSURE_SCOPES
                if can_control or can_view_private
                else PUBLIC_ACTOR_DISCLOSURE_SCOPES
            )
        )
        # Keep the disclosure decision at both ActorKnowledge query entries.
        knowledge = self.knowledge.list(
            campaign_id,
            actor_id=core_actor_id,
            branch_id=selected_branch_id,
            disclosure_scopes=allowed_knowledge_scopes,
        )
        searched_knowledge = self.knowledge.search(
            campaign_id,
            actor_id=core_actor_id,
            query=query or " ",
            branch_id=selected_branch_id,
            limit=100,
            disclosure_scopes=allowed_knowledge_scopes,
        )
        by_knowledge_id = {item.id: item for item in [*knowledge, *searched_knowledge]}
        recent_events = self.events.list_for_actor(
            campaign_id,
            actor_id=core_actor_id,
            limit=200,
            branch_id=selected_branch_id,
            knowledge_disclosure_scopes=allowed_knowledge_scopes,
            audience="dm" if facilitator else "player",
        )
        searched_events = (
            self.events.search_for_actor(
                campaign_id,
                actor_id=core_actor_id,
                query=query,
                knowledge_disclosure_scopes=allowed_knowledge_scopes,
                audience="dm" if facilitator else "player",
                limit=500,
                branch_id=selected_branch_id,
            )
            if query.strip()
            else []
        )
        requested_event_ids = list(
            dict.fromkeys(
                item.removeprefix("event:")
                for item in requested_current_refs
                if item.startswith("event:") and item != "event:"
            )
        )
        exact_events = (
            self.events.list_for_actor_event_ids(
                campaign_id,
                actor_id=core_actor_id,
                event_ids=requested_event_ids,
                knowledge_disclosure_scopes=allowed_knowledge_scopes,
                audience="dm" if facilitator else "player",
                branch_id=selected_branch_id,
            )
            if requested_event_ids
            else []
        )
        by_event_id = {
            item.id: item
            for item in [*recent_events, *searched_events, *exact_events]
        }
        if not facilitator:
            facts = [
                item for item in facts if item.disclosure_scope in PLAYER_MEMORY_DISCLOSURE_SCOPES
            ]
        logical_actor_refs = {core_actor_id, *aliases}
        visible_records = []
        for record in document.get("records", {}).values():
            audience = dict(record.get("audience") or {})
            controller = dict(record.get("controller") or {})
            relevant_refs = {
                str(item)
                for item in (
                    [audience.get("actor_id"), controller.get("actor_id")]
                    + list(audience.get("actor_ids") or [])
                    + list(controller.get("actor_ids") or [])
                )
                if item
            }
            record_id = str(record.get("id") or "")
            requested_refs = set(requested_current_refs)
            explicitly_current = bool(
                record_id
                and ({record_id, f"record:{record_id}"} & requested_refs)
            )
            if not (logical_actor_refs.intersection(relevant_refs) or explicitly_current):
                continue
            if not self._record_visible(
                document,
                campaign_id=campaign_id,
                principal_id=principal_id,
                role=membership.role,
                record=record,
            ):
                continue
            record_data = dict(record.get("data") or {})
            visible_records.append(
                {
                    "id": str(record["id"]),
                    "fact_key": f"narrative_record.{record['id']}",
                    "revision": int(record.get("revision") or 0),
                    "kind": str(record.get("kind") or "narrative_record"),
                    "predicate": str(
                        record_data.get("memory_predicate")
                        or (
                            "relationship"
                            if record.get("kind") in {"relationship", "character_arc", "goal"}
                            else "narrative_context"
                        )
                    ),
                    "content": str(
                        record_data.get("summary")
                        or record_data.get("content")
                        or record.get("title")
                        or record.get("id")
                    ),
                    "metadata": {
                        "record_id": str(record["id"]),
                        "record_ref": f"record:{record['id']}",
                    },
                }
            )
        actor_projection = asdict(actor)
        if not facilitator and not can_view_private:
            actor_projection["sheet"] = {}
        actor_projection["actor_ref"] = aliases[0] if aliases else core_actor_id
        actor_projection["state_facts"] = [
            *[asdict(item) for item in facts],
            *visible_records,
        ]
        memory = select_actor_memory_context(
            actor_state=actor_projection,
            actor_knowledge=by_knowledge_id.values(),
            events=by_event_id.values(),
            current_refs=requested_current_refs,
            query=query,
            budget_chars=budget_chars,
        )
        allowed_basis_refs = sorted(
            {
                item["basis_ref"]
                for track in ("identity", "motivational", "semantic", "episodic")
                for item in memory[track]
            }
        )
        return {
            "schema_version": 1,
            "purpose": "actor_memory",
            "campaign_id": campaign_id,
            "branch_id": selected_branch_id,
            "actor_id": core_actor_id,
            "actor_ref": aliases[0] if aliases else core_actor_id,
            "audience": "facilitator" if facilitator else "player",
            "memory": memory,
            "constraints": {
                "allowed_basis_refs": allowed_basis_refs,
                "may_choose_intent": False,
                "may_write_state": False,
            },
        }

    def continuity_context(
        self,
        campaign_id: str,
        *,
        principal_id: str,
        actor_id: str | None,
        audience: str,
        limit: int,
        query: str,
        branch_id: str | None,
        offset: int = 0,
        budget_chars: int = 12_000,
    ) -> dict[str, Any]:
        """Read ordinary continuity through the same branch authority gate."""

        selected_branch_id = self.readable_branch_id(
            campaign_id, principal_id, branch_id
        )
        return self.continuity.context(
            campaign_id,
            actor_id=actor_id,
            audience=audience,
            limit=limit,
            offset=offset,
            budget_chars=budget_chars,
            query=query,
            branch_id=selected_branch_id,
        )

    def _record_visible(
        self,
        document: Mapping[str, Any],
        *,
        campaign_id: str,
        principal_id: str,
        role: str,
        record: Mapping[str, Any],
    ) -> bool:
        """Project Narrative audience authority without translating it to Core scopes."""

        audience = dict(record.get("audience") or {})
        scope = audience.get("scope", "table")
        if scope in {"table", "public"}:
            return True
        if self._has_facilitator_authority(document, role):
            return True
        if scope == "group":
            if principal_id in set(audience.get("principal_ids") or []):
                return True
            bindings = dict(document.get("actor_bindings") or {})
            for actor_ref in audience.get("actor_ids") or []:
                if self._actor_authorized(
                    document,
                    campaign_id=campaign_id,
                    actor_id=str(bindings.get(actor_ref) or actor_ref),
                    principal_id=principal_id,
                    role=role,
                ):
                    return True
            return False
        if scope == "actor":
            actor_refs = []
            if audience.get("actor_id"):
                actor_refs.append(str(audience["actor_id"]))
            actor_refs.extend(str(item) for item in audience.get("actor_ids", []))
            bindings = dict(document.get("actor_bindings") or {})
            for actor_ref in actor_refs:
                if self._actor_authorized(
                    document,
                    campaign_id=campaign_id,
                    actor_id=str(bindings.get(actor_ref) or actor_ref),
                    principal_id=principal_id,
                    role=role,
                ):
                    return True
            return self._record_authorized(
                document,
                campaign_id=campaign_id,
                principal_id=principal_id,
                role=role,
                record=record,
                control=False,
            )
        if scope == "private_worker":
            return audience.get("principal_id") == principal_id
        if scope == "facilitator":
            return False
        return False

    def snapshot_create(
        self,
        campaign_id: str,
        *,
        label: str,
        principal_id: str,
        expected_revision: int,
        expected_branch_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Create a checkpoint under the same campaign CAS used by narrative writes."""

        self.access.require_campaign(campaign_id, principal_id, roles=ADMIN_ROLES)
        key = required_text(idempotency_key, "idempotency_key", limit=200)
        scope = f"narrative:snapshot:create:{campaign_id}:{expected_branch_id}:{principal_id}"
        request = {
            "label": label,
            "expected_revision": int(expected_revision),
            "expected_branch_id": expected_branch_id,
        }
        with self.database.transaction() as session:
            replay = self.idempotency.lookup_in_session(session, scope, key, request)
            if replay is not None and replay.response is not None:
                return deepcopy(replay.response)
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                raise LookupError(campaign_id)
            branch = resolve_branch(session, campaign)
            if branch.id != expected_branch_id or campaign.revision != int(expected_revision):
                raise ValueError("campaign revision or branch conflict")
            document = narrative_document(campaign.state)
            if any(
                item.get("status") == "open"
                for item in document.get("npc_conversations", {}).values()
            ):
                raise ValueError("close or abort every NPC conversation before snapshot")
            before_state = deepcopy(campaign.state)
            changed = session.execute(
                update(Campaign)
                .where(
                    Campaign.id == campaign_id,
                    Campaign.revision == int(expected_revision),
                    Campaign.active_branch_id == expected_branch_id,
                )
                .values(revision=Campaign.revision + 1)
                .returning(Campaign.revision)
            ).scalar_one_or_none()
            if changed is None:
                raise ValueError("campaign revision conflict during snapshot creation")
            session.expire(campaign)
            session.refresh(campaign)
            snapshot = self.snapshots._create_in_session(
                session, campaign, label=str(label or "Narrative checkpoint")
            )
            response = {
                "campaign_id": campaign_id,
                "campaign_revision": int(changed),
                "branch_id": branch.id,
                "phase": document["phase"],
                "snapshot": asdict(snapshot),
            }
            revisions = self.revisions.record_group_in_session(
                session,
                campaign_id,
                operation="narrative.snapshot.create",
                actor=principal_id,
                branch_id=branch.id,
                idempotency_key=key,
                request_hash=request_hash(request),
                reversible=False,
                changes=[
                    {
                        "entity_type": "campaign",
                        "entity_id": campaign_id,
                        "before": {"state": before_state, "revision": int(expected_revision)},
                        "after": {"state": before_state, "revision": int(changed)},
                    }
                ],
            )
            self.idempotency.remember_in_session(
                session,
                scope,
                key,
                request,
                response,
                campaign_id=campaign_id,
                mutation_group_id=revisions[0].mutation_group_id,
            )
            return response

    def _require_branch_revision(
        self,
        campaign_id: str,
        *,
        principal_id: str,
        expected_revision: int,
        expected_branch_id: str,
        roles: set[str] | None = None,
    ) -> tuple[Any, str]:
        self.access.require_campaign(campaign_id, principal_id, roles=roles)
        membership = self.access.require_campaign(campaign_id, principal_id)
        if membership.role == "observer":
            raise PermissionError("observer role is read-only")
        campaign = self.campaigns.get(campaign_id)
        branch_id = self.branch_id(campaign_id)
        if branch_id != expected_branch_id:
            raise ValueError(
                f"campaign branch conflict: expected {expected_branch_id}, found {branch_id}"
            )
        if campaign.revision != int(expected_revision):
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        return campaign, branch_id

    def _write(
        self,
        campaign_id: str,
        *,
        principal_id: str,
        expected_revision: int,
        expected_branch_id: str,
        idempotency_key: str,
        operation: str,
        payload: dict[str, Any],
        mutate: Callable[[dict[str, Any]], dict[str, Any]],
        roles: set[str] | None = None,
    ) -> dict[str, Any]:
        key = required_text(idempotency_key, "idempotency_key", limit=200)
        scope = f"narrative:{operation}:{campaign_id}:{expected_branch_id}:{principal_id}"
        request = {
            "operation": operation,
            "campaign_id": campaign_id,
            "expected_revision": int(expected_revision),
            "expected_branch_id": expected_branch_id,
            "payload": deepcopy(payload),
        }
        self.access.require_campaign(campaign_id, principal_id, roles=roles)
        with self.database.transaction() as session:
            replay = self.idempotency.lookup_in_session(session, scope, key, request)
            if replay is not None and replay.response is not None:
                return deepcopy(replay.response)
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                raise LookupError(campaign_id)
            branch = resolve_branch(session, campaign)
            if branch.id != expected_branch_id:
                raise ValueError(
                    f"campaign branch conflict: expected {expected_branch_id}, found {branch.id}"
                )
            if campaign.revision != int(expected_revision):
                raise ValueError(
                    "campaign revision conflict: "
                    f"expected {expected_revision}, found {campaign.revision}"
                )
            before_state = deepcopy(campaign.state)
            document = narrative_document(before_state)
            open_conversations = [
                item
                for item in document.get("npc_conversations", {}).values()
                if item.get("status") == "open"
            ]
            if open_conversations and not operation.startswith("npc."):
                raise ValueError(
                    "close or abort every NPC conversation before authoritative mutation"
                )
            result_payload = mutate(document)
            next_state = state_with_narrative(before_state, document)
            # MCP-local SQL CAS is intentional: core currently validates expected
            # revisions in Python. The conditional UPDATE makes two server
            # processes fail closed without changing sagasmith-core.
            changed = session.execute(
                update(Campaign)
                .where(
                    Campaign.id == campaign_id,
                    Campaign.revision == int(expected_revision),
                    Campaign.active_branch_id == expected_branch_id,
                )
                .values(state=next_state, revision=Campaign.revision + 1)
                .returning(Campaign.revision)
            ).scalar_one_or_none()
            if changed is None:
                raise ValueError("campaign revision conflict during conditional commit")
            response = {
                "campaign_id": campaign_id,
                "campaign_revision": int(changed),
                "branch_id": branch.id,
                "phase": document["phase"],
                **deepcopy(result_payload),
            }
            revisions = self.revisions.record_group_in_session(
                session,
                campaign_id,
                operation=f"narrative.{operation}",
                actor=principal_id,
                branch_id=branch.id,
                idempotency_key=key,
                request_hash=request_hash(request),
                changes=[
                    {
                        "entity_type": "campaign",
                        "entity_id": campaign_id,
                        "before": {"state": before_state, "revision": int(expected_revision)},
                        "after": {"state": next_state, "revision": int(changed)},
                    }
                ],
            )
            self.idempotency.remember_in_session(
                session,
                scope,
                key,
                request,
                response,
                campaign_id=campaign_id,
                mutation_group_id=revisions[0].mutation_group_id,
            )
            return response

    def set_phase(self, campaign_id: str, *, phase: str, **common: Any) -> dict[str, Any]:
        if phase not in {PHASE_LOBBY, PHASE_PLAY}:
            raise ValueError("game_phase only transitions between lobby and play")

        def mutate(document: dict[str, Any]) -> dict[str, Any]:
            current = str(document["phase"])
            if current == PHASE_CONFLICT:
                raise ValueError("end the active conflict before changing game phase")
            if phase == PHASE_LOBBY and document.get("active_scene_id"):
                raise ValueError("end the active scene before returning to lobby")
            if phase == PHASE_PLAY and active_profile(document) is None:
                raise ValueError("activate a finalized profile before entering play")
            document["phase"] = phase
            return {"previous_phase": current}

        return self._write(
            campaign_id,
            operation="phase.change",
            payload={"phase": phase},
            mutate=mutate,
            roles=ADMIN_ROLES,
            **common,
        )

    def profile_change(
        self,
        campaign_id: str,
        *,
        action: str,
        profile: dict[str, Any] | None = None,
        profile_key: str | None = None,
        **common: Any,
    ) -> dict[str, Any]:
        def mutate(document: dict[str, Any]) -> dict[str, Any]:
            profiles = document["profiles"]
            if document["phase"] != PHASE_LOBBY:
                raise ValueError("profiles can change only in lobby")
            if action in {"create_draft", "update_draft"}:
                normalized = validate_profile(profile or {})
                key = f"{normalized['id']}@{normalized['version']}"
                if key in profiles["finalized"]:
                    raise ValueError("a finalized profile version is immutable")
                if action == "create_draft" and key in profiles["drafts"]:
                    raise ValueError(f"profile draft already exists: {key}")
                if action == "update_draft" and key not in profiles["drafts"]:
                    raise LookupError(key)
                profiles["drafts"][key] = normalized
                return {"profile_key": key, "profile": normalized, "status": "draft"}
            key = required_text(profile_key, "profile_key", limit=200)
            if action == "finalize":
                draft = profiles["drafts"].get(key)
                if draft is None:
                    raise LookupError(key)
                finalized = validate_profile(draft, finalized=True)
                profiles["finalized"][key] = finalized
                profiles["drafts"].pop(key)
                return {"profile_key": key, "profile": finalized, "status": "finalized"}
            if action == "activate":
                if key not in profiles["finalized"]:
                    raise LookupError(key)
                candidate = profiles["finalized"][key]
                capabilities = set(candidate.get("capabilities", []))
                if int(candidate.get("mechanics_level", 0)) == 0 and "mechanics" in capabilities:
                    raise ValueError("Level 0 profile cannot activate mechanics capability")
                profiles["active"] = key
                return {
                    "profile_key": key,
                    "profile": profiles["finalized"][key],
                    "status": "active",
                }
            raise ValueError(f"unsupported profile action: {action}")

        return self._write(
            campaign_id,
            operation=f"profile.{action}",
            payload={"action": action, "profile": profile, "profile_key": profile_key},
            mutate=mutate,
            roles=ADMIN_ROLES,
            **common,
        )

    @staticmethod
    def _validate_pack(pack: Mapping[str, Any], *, finalized: bool = False) -> dict[str, Any]:
        value = deepcopy(dict(pack))
        kind = str(value.get("kind") or "")
        if kind not in {"content", "module", "campaign_seed"}:
            raise ValueError("pack.kind must be content, module, or campaign_seed")
        raw_requirements = list(value.get("profile_requirements") or [])
        raw_dependencies = list(value.get("dependencies") or [])
        if len(raw_requirements) > 32 or len(raw_dependencies) > 64:
            raise ValueError("Pack requirements or dependencies exceed the supported bound")
        requirements: list[dict[str, Any]] = []
        for index, item in enumerate(raw_requirements):
            item = {"id": item} if isinstance(item, str) else deepcopy(dict(item))
            requirement = {
                "id": required_id(item.get("id"), f"profile_requirements[{index}].id"),
                "version": required_text(
                    item.get("version"),
                    f"profile_requirements[{index}].version",
                    limit=64,
                ),
                "capabilities": sorted(
                    {
                        required_id(capability, "profile capability")
                        for capability in item.get("capabilities", [])
                    }
                ),
                "forbidden_capabilities": sorted(
                    {
                        required_id(capability, "forbidden profile capability")
                        for capability in item.get("forbidden_capabilities", [])
                    }
                ),
            }
            if item.get("checksum") is not None:
                requirement["checksum"] = required_text(
                    item.get("checksum"),
                    f"profile_requirements[{index}].checksum",
                    limit=128,
                )
            requirements.append(requirement)
        dependencies: list[dict[str, Any]] = []
        for index, item in enumerate(raw_dependencies):
            if isinstance(item, str):
                if "@" not in item:
                    raise ValueError("string Pack dependency must use id@version")
                dependency_id, dependency_version = item.rsplit("@", 1)
                item = {"id": dependency_id, "version": dependency_version}
            else:
                item = deepcopy(dict(item))
            dependency = {
                "id": required_id(item.get("id"), f"dependencies[{index}].id"),
                "version": required_text(
                    item.get("version"), f"dependencies[{index}].version", limit=64
                ),
            }
            if item.get("checksum") is not None:
                dependency["checksum"] = required_text(
                    item.get("checksum"), f"dependencies[{index}].checksum", limit=128
                )
            dependencies.append(dependency)
        sources = validate_sources(
            value.get("sources") or [], field="pack.sources", finalized=finalized
        )
        raw_rights = value.get("rights") or {}
        if not isinstance(raw_rights, Mapping):
            raise ValueError("pack.rights must be an object")
        rights = deepcopy(dict(raw_rights))
        raw_review = value.get("review") or {}
        if not isinstance(raw_review, Mapping):
            raise ValueError("pack.review must be an object")
        review = deepcopy(dict(raw_review))
        pack_id = required_id(value.get("id"), "pack.id")
        pack_version = required_text(value.get("version"), "pack.version", limit=64)
        pack_key = f"{pack_id}@{pack_version}"
        content = deepcopy(dict(value.get("content") or {}))
        if content.get("runtime_manifest") is not None:
            if kind not in {"module", "campaign_seed"}:
                raise ValueError("runtime manifests belong to module or campaign_seed Packs")
            content["runtime_manifest"] = validate_runtime_manifest(
                content["runtime_manifest"], pack_key=pack_key
            )
        normalized = {
            "id": pack_id,
            "version": pack_version,
            "title": required_text(value.get("title"), "pack.title", limit=200),
            "kind": kind,
            "profile_requirements": requirements,
            "dependencies": dependencies,
            "sources": sources,
            "rights": rights,
            "content": content,
            "review": review,
        }
        if finalized:
            if normalized["review"].get("agent_finalization") is not True:
                raise ValueError("Pack finalization requires explicit Agent review")
            distribution = str(normalized["rights"].get("distribution") or "")
            if distribution not in {"internal", "private", "public", "restricted"}:
                raise ValueError("Pack finalization requires a distribution rights decision")
            if not str(normalized["rights"].get("license") or "").strip():
                raise ValueError("Pack finalization requires a license or rights basis")
        normalized["checksum"] = checksum(normalized)
        return normalized

    def pack_change(
        self,
        campaign_id: str,
        *,
        action: str,
        pack: dict[str, Any] | None = None,
        pack_key: str | None = None,
        **common: Any,
    ) -> dict[str, Any]:
        def mutate(document: dict[str, Any]) -> dict[str, Any]:
            if document["phase"] != PHASE_LOBBY:
                raise ValueError("Pack lifecycle changes are lobby-only")
            packs = document["packs"]
            if action in {"create_draft", "update_draft"}:
                normalized = self._validate_pack(pack or {})
                key = f"{normalized['id']}@{normalized['version']}"
                self._verify_episode_pack_boundary(
                    document,
                    campaign_id=campaign_id,
                    branch_id=str(common["expected_branch_id"]),
                    pack_key=key,
                    pack=normalized,
                )
                if key in packs["finalized"]:
                    raise ValueError("a finalized Pack version is immutable")
                if action == "create_draft" and key in packs["drafts"]:
                    raise ValueError(f"Pack draft already exists: {key}")
                if action == "update_draft" and key not in packs["drafts"]:
                    raise LookupError(key)
                packs["drafts"][key] = normalized
                return {"pack_key": key, "pack": normalized, "status": "draft"}
            key = required_text(pack_key, "pack_key", limit=200)
            if action == "finalize":
                value = packs["drafts"].get(key)
                if value is None:
                    raise LookupError(key)
                finalized = self._validate_pack(value, finalized=True)
                manifest = dict(finalized.get("content") or {}).get("runtime_manifest")
                if manifest and manifest.get("classification") == "emergent_episode":
                    self._verify_episode_pack_boundary(
                        document,
                        campaign_id=campaign_id,
                        branch_id=str(common["expected_branch_id"]),
                        pack_key=key,
                        pack=finalized,
                    )
                    active_manifests = {
                        active_key: dict(
                            dict(packs["finalized"].get(active_key) or {}).get("content") or {}
                        ).get("runtime_manifest")
                        for active_key in packs["active"]
                    }
                    active_manifests = {
                        active_key: value
                        for active_key, value in active_manifests.items()
                        if isinstance(value, Mapping)
                    }
                    validate_campaign_design({**active_manifests, key: manifest})
                packs["finalized"][key] = finalized
                packs["drafts"].pop(key)
                return {"pack_key": key, "pack": finalized, "status": "finalized"}
            if action == "import":
                if key not in packs["finalized"]:
                    raise LookupError(key)
                packs["imports"][key] = {"status": "inactive", "recovery": "complete"}
                return {"pack_key": key, "status": "inactive"}
            if action == "activate":
                imported = packs["imports"].get(key)
                if not imported or imported.get("status") not in {"inactive", "active"}:
                    raise ValueError("finalize and import the Pack before activation")
                target_pack = packs["finalized"][key]
                self._verify_episode_pack_boundary(
                    document,
                    campaign_id=campaign_id,
                    branch_id=str(common["expected_branch_id"]),
                    pack_key=key,
                    pack=target_pack,
                )
                profile = active_profile(document)
                requirements = list(target_pack.get("profile_requirements") or [])
                if requirements and profile is None:
                    raise ValueError("Pack activation requires an active profile")
                for requirement in requirements:
                    required_profile = str(requirement.get("id") or "")
                    required_version = requirement.get("version")
                    if required_profile and profile and profile.get("id") != required_profile:
                        raise ValueError("Pack profile requirement is not satisfied")
                    if required_version and profile and profile.get("version") != required_version:
                        raise ValueError("Pack profile version requirement is not satisfied")
                    if (
                        requirement.get("checksum")
                        and profile
                        and profile.get("checksum") != requirement["checksum"]
                    ):
                        raise ValueError("Pack profile checksum requirement is not satisfied")
                    profile_capabilities = (
                        set(profile.get("capabilities") or []) if profile else set()
                    )
                    missing_capabilities = (
                        set(requirement.get("capabilities") or []) - profile_capabilities
                    )
                    forbidden_capabilities = (
                        set(requirement.get("forbidden_capabilities") or []) & profile_capabilities
                    )
                    if missing_capabilities:
                        raise ValueError(
                            "Pack profile capabilities are missing: "
                            + ", ".join(sorted(missing_capabilities))
                        )
                    if forbidden_capabilities:
                        raise ValueError(
                            "Pack profile has forbidden capabilities: "
                            + ", ".join(sorted(forbidden_capabilities))
                        )
                missing_dependencies = []
                for dependency in target_pack.get("dependencies", []):
                    dependency_key = f"{dependency['id']}@{dependency['version']}"
                    if dependency_key not in packs["active"]:
                        missing_dependencies.append(dependency_key)
                        continue
                    required_checksum = dependency.get("checksum")
                    active_dependency = packs["finalized"].get(dependency_key)
                    if (
                        required_checksum
                        and active_dependency
                        and active_dependency.get("checksum") != required_checksum
                    ):
                        raise ValueError(f"Pack dependency checksum mismatch: {dependency_key}")
                if missing_dependencies:
                    raise ValueError(
                        "Pack activation has missing dependencies: "
                        + ", ".join(missing_dependencies)
                    )
                runtime_manifest = dict(target_pack.get("content") or {}).get(
                    "runtime_manifest"
                )
                if runtime_manifest is not None:
                    current_design = dict(document.get("campaign_design") or {})
                    manifests = dict(current_design.get("manifests") or {})
                    manifests[key] = runtime_manifest
                    next_design = validate_campaign_design(manifests)
                    next_design["progress"] = deepcopy(
                        current_design.get("progress") or next_design["progress"]
                    )
                    document["campaign_design"] = next_design
                imported["status"] = "active"
                if key not in packs["active"]:
                    packs["active"].append(key)
                return {"pack_key": key, "status": "active"}
            raise ValueError(f"unsupported Pack action: {action}")

        return self._write(
            campaign_id,
            operation=f"pack.{action}",
            payload={"action": action, "pack": pack, "pack_key": pack_key},
            mutate=mutate,
            roles=ADMIN_ROLES,
            **common,
        )

    def campaign_seed_apply(
        self,
        campaign_id: str,
        *,
        pack_key: str,
        principal_id: str,
        expected_revision: int,
        expected_branch_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Materialize one active campaign_seed with stable actor references atomically."""

        self.access.require_campaign(campaign_id, principal_id, roles=ADMIN_ROLES)
        key = required_text(idempotency_key, "idempotency_key", limit=200)
        request = {
            "pack_key": pack_key,
            "expected_revision": expected_revision,
            "expected_branch_id": expected_branch_id,
        }
        scope = f"narrative:seed.apply:{campaign_id}:{expected_branch_id}:{principal_id}"
        with self.database.transaction() as session:
            replay = self.idempotency.lookup_in_session(session, scope, key, request)
            if replay is not None and replay.response is not None:
                return deepcopy(replay.response)
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                raise LookupError(campaign_id)
            branch = resolve_branch(session, campaign)
            if branch.id != expected_branch_id or campaign.revision != expected_revision:
                raise ValueError("campaign revision or branch conflict")
            document = narrative_document(campaign.state)
            if document["phase"] != PHASE_LOBBY:
                raise ValueError("campaign_seed apply is lobby-only")
            if any(
                item.get("status") == "open"
                for item in document.get("npc_conversations", {}).values()
            ):
                raise ValueError(
                    "close or abort every NPC conversation before campaign_seed apply"
                )
            if pack_key not in document["packs"]["active"]:
                raise ValueError("campaign_seed Pack must be active before apply")
            pack = document["packs"]["finalized"].get(pack_key)
            if not pack or pack.get("kind") != "campaign_seed":
                raise ValueError("Pack is not a campaign_seed")
            content = deepcopy(dict(pack.get("content") or {}))
            profile = active_profile(document)
            raw_principals = content.get("principals") or []
            raw_actors = content.get("actors") or []
            raw_records = content.get("records") or []
            raw_knowledge = content.get("actor_knowledge") or []
            for field, values, maximum in (
                ("principals", raw_principals, 128),
                ("actors", raw_actors, 512),
                ("records", raw_records, 5000),
                ("actor_knowledge", raw_knowledge, 5000),
            ):
                if not isinstance(values, list) or len(values) > maximum:
                    raise ValueError(f"campaign_seed {field} must be a bounded array")
                if any(not isinstance(item, Mapping) for item in values):
                    raise ValueError(f"campaign_seed {field} entries must be objects")
            principal_ids = [required_id(item.get("id"), "principal.id") for item in raw_principals]
            actor_refs = [required_id(item.get("id"), "actor.id") for item in raw_actors]
            record_ids = [required_id(item.get("id"), "record.id") for item in raw_records]
            for field, values in (
                ("principal", principal_ids),
                ("actor", actor_refs),
                ("record", record_ids),
            ):
                if len(values) != len(set(values)):
                    raise ValueError(f"campaign seed has duplicate {field} references")
            for item in raw_principals:
                role = str(item.get("role") or "player")
                if role not in CAMPAIGN_ROLES:
                    raise ValueError(f"unsupported campaign seed role: {role}")
            bindings: dict[str, str] = {}
            for item in raw_principals:
                target_id = required_id(item.get("id"), "principal.id")
                if session.get(Principal, target_id) is None:
                    session.add(
                        Principal(
                            id=target_id,
                            platform="fixture",
                            external_id=target_id,
                            display_name=target_id,
                            is_service=False,
                        )
                    )
            session.flush()
            for item in raw_principals:
                target_id = required_id(item.get("id"), "principal.id")
                membership = session.get(
                    CampaignMembership,
                    {"campaign_id": campaign_id, "principal_id": target_id},
                )
                if membership is None:
                    session.add(
                        CampaignMembership(
                            campaign_id=campaign_id,
                            principal_id=target_id,
                            role=str(item.get("role") or "player"),
                        )
                    )
                elif target_id != principal_id:
                    membership.role = str(item.get("role") or membership.role)
            session.flush()
            for item in raw_actors:
                actor_ref = required_id(item.get("id"), "actor.id")
                self._validate_actor_profile(profile, item)
                if actor_ref in document["actor_bindings"] or actor_ref in bindings:
                    raise ValueError(f"campaign seed actor reference collision: {actor_ref}")
                core_id = str(uuid.uuid4())
                session.add(
                    Character(
                        id=core_id,
                        system_id="narrative",
                        campaign_id=campaign_id,
                        character_type=str(item.get("type") or "npc"),
                        name=required_text(item.get("name"), "actor.name", limit=200),
                        summary=str(item.get("summary") or ""),
                        sheet=deepcopy(dict(item.get("sheet") or {})),
                        notes={"actor_ref": actor_ref},
                    )
                )
                bindings[actor_ref] = core_id
            session.flush()
            document["actor_bindings"].update(bindings)
            for item in raw_principals:
                target_id = str(item["id"])
                for actor_ref in item.get("actor_grants", []):
                    core_id = document["actor_bindings"].get(actor_ref)
                    if not core_id:
                        raise ValueError(f"unknown actor grant reference: {actor_ref}")
                    if (
                        session.get(
                            ActorGrant,
                            {
                                "campaign_id": campaign_id,
                                "principal_id": target_id,
                                "actor_id": core_id,
                            },
                        )
                        is None
                    ):
                        session.add(
                            ActorGrant(
                                campaign_id=campaign_id,
                                principal_id=target_id,
                                actor_id=core_id,
                                can_control=True,
                                can_view_private=True,
                            )
                        )
            seed_element_grants: dict[tuple[str, str], dict[str, Any]] = {}
            valid_element_refs = set(record_ids) | set(actor_refs)
            declared_grants: list[tuple[str, Any]] = []
            for item in raw_principals:
                declared_grants.extend(
                    (str(item["id"]), raw_grant)
                    for raw_grant in item.get("element_grants") or []
                )
            raw_stewardship = content.get("element_stewardship") or []
            if not isinstance(raw_stewardship, list) or len(raw_stewardship) > 5000:
                raise ValueError("campaign_seed element_stewardship must be a bounded array")
            for raw_grant in raw_stewardship:
                if not isinstance(raw_grant, Mapping):
                    raise ValueError("element_stewardship entries must be objects")
                declared_grants.append(
                    (
                        required_id(
                            raw_grant.get("principal_id"),
                            "element_stewardship.principal_id",
                        ),
                        raw_grant,
                    )
                )
            for target_id, raw_grant in declared_grants:
                    if target_id not in set(principal_ids):
                        raise ValueError(f"unknown seed element principal: {target_id}")
                    grant_data = (
                        {"element_ref": raw_grant}
                        if isinstance(raw_grant, str)
                        else deepcopy(dict(raw_grant))
                    )
                    element_ref = required_id(
                        grant_data.get("element_ref"), "element_grant.element_ref"
                    )
                    if element_ref not in valid_element_refs:
                        raise ValueError(f"unknown seed element grant reference: {element_ref}")
                    raw_scope = grant_data.get("scope") or {}
                    if not isinstance(raw_scope, Mapping):
                        raise ValueError("element_grant.scope must be an object")
                    scope_value = deepcopy(dict(raw_scope))
                    if set(scope_value) - {"mode", "scene_id"}:
                        raise ValueError("element_grant.scope supports only mode and scene_id")
                    if scope_value.get("mode") not in {None, "campaign", "scene"}:
                        raise ValueError("element_grant.scope.mode must be campaign or scene")
                    if scope_value.get("scene_id") is not None:
                        scope_value["scene_id"] = required_id(
                            scope_value["scene_id"], "element_grant.scope.scene_id"
                        )
                    seed_element_grants[(target_id, element_ref)] = {
                            "principal_id": target_id,
                            "element_ref": element_ref,
                            "can_control": bool(grant_data.get("can_control", True)),
                            "can_view_private": bool(
                                grant_data.get("can_view_private", True)
                            ),
                            "scope": scope_value,
                        }
            materialized_element_grants = list(seed_element_grants.values())
            document["element_grants"].extend(materialized_element_grants)
            for item in raw_records:
                normalized = validate_record(item)
                self._validate_record_profile(profile, normalized)
                if normalized["id"] in document["records"]:
                    raise ValueError(
                        f"campaign seed narrative record collision: {normalized['id']}"
                    )
                normalized["revision"] = 1
                document["records"][normalized["id"]] = normalized
            knowledge_results = []
            for item in raw_knowledge:
                knowledge = deepcopy(dict(item))
                actor_ref = required_id(knowledge.get("actor_id"), "actor_knowledge.actor_id")
                core_actor_id = document["actor_bindings"].get(actor_ref)
                if core_actor_id is None:
                    raise ValueError(f"unknown actor knowledge reference: {actor_ref}")
                knowledge_results.append(
                    self.knowledge._add_in_session(
                        session,
                        campaign,
                        branch.id,
                        branch.head_snapshot_id,
                        actor_id=core_actor_id,
                        knowledge_key=required_text(
                            knowledge.get("knowledge_key"),
                            "actor_knowledge.knowledge_key",
                            limit=200,
                        ),
                        proposition=required_text(
                            knowledge.get("proposition"),
                            "actor_knowledge.proposition",
                        ),
                        subject_ref=str(knowledge.get("subject_ref") or ""),
                        epistemic_status=str(knowledge.get("epistemic_status") or "known"),
                        confidence=int(knowledge.get("confidence", 3)),
                        source_event_id=None,
                        cause=str(knowledge.get("cause") or "background"),
                        disclosure_scope=str(knowledge.get("disclosure_scope") or "owner"),
                    )
                )
            before = deepcopy(campaign.state)
            next_state = state_with_narrative(before, document)
            changed = session.execute(
                update(Campaign)
                .where(
                    Campaign.id == campaign_id,
                    Campaign.revision == expected_revision,
                    Campaign.active_branch_id == expected_branch_id,
                )
                .values(state=next_state, revision=Campaign.revision + 1)
                .returning(Campaign.revision)
            ).scalar_one_or_none()
            if changed is None:
                raise ValueError("campaign revision conflict during seed apply")
            response = {
                "campaign_id": campaign_id,
                "campaign_revision": int(changed),
                "branch_id": branch.id,
                "phase": document["phase"],
                "pack_key": pack_key,
                "actor_bindings": bindings,
                "actors_created": len(bindings),
                "records_materialized": len(raw_records),
                "element_grants_materialized": len(materialized_element_grants),
                "actor_knowledge_materialized": len(knowledge_results),
                "status": "applied",
            }
            revisions = self.revisions.record_group_in_session(
                session,
                campaign_id,
                operation="narrative.seed.apply",
                actor=principal_id,
                branch_id=branch.id,
                idempotency_key=key,
                request_hash=request_hash(request),
                reversible=False,
                changes=[
                    {
                        "entity_type": "campaign",
                        "entity_id": campaign_id,
                        "before": {"state": before, "revision": expected_revision},
                        "after": {"state": next_state, "revision": int(changed)},
                    }
                ],
            )
            self.idempotency.remember_in_session(
                session,
                scope,
                key,
                request,
                response,
                campaign_id=campaign_id,
                mutation_group_id=revisions[0].mutation_group_id,
            )
            return response

    def query(
        self,
        campaign_id: str,
        *,
        principal_id: str,
        kind: str,
        record_id: str | None = None,
    ) -> dict[str, Any]:
        membership = self.access.require_campaign(campaign_id, principal_id)
        document = narrative_document(self.campaigns.get(campaign_id).state)
        if kind == "profile":
            profiles = document["profiles"]
            if record_id:
                if record_id == profiles.get("active"):
                    return {
                        "profile_key": record_id,
                        "status": "active",
                        "profile": deepcopy(profiles["finalized"][record_id]),
                    }
                if membership.role in ADMIN_ROLES:
                    if record_id in profiles["drafts"]:
                        return {
                            "profile_key": record_id,
                            "status": "draft",
                            "profile": deepcopy(profiles["drafts"][record_id]),
                        }
                    if record_id in profiles["finalized"]:
                        return {
                            "profile_key": record_id,
                            "status": "finalized",
                            "profile": deepcopy(profiles["finalized"][record_id]),
                        }
                raise LookupError(record_id)
            return {
                "active": active_profile(document),
                "finalized": list(document["profiles"]["finalized"]),
                **(
                    {
                        "drafts": deepcopy(profiles["drafts"]),
                        "finalized_profiles": deepcopy(profiles["finalized"]),
                    }
                    if membership.role in ADMIN_ROLES
                    else {}
                ),
            }
        if kind == "pack":
            packs = document["packs"]
            if record_id:
                if membership.role not in ADMIN_ROLES and record_id not in packs["active"]:
                    raise LookupError(record_id)
                value = packs["finalized"].get(record_id) or packs["drafts"].get(record_id)
                if value is None:
                    raise LookupError(record_id)
                status = (
                    "active"
                    if record_id in packs["active"]
                    else (
                        str(packs["imports"].get(record_id, {}).get("status") or "finalized")
                        if record_id in packs["finalized"]
                        else "draft"
                    )
                )
                if membership.role not in ADMIN_ROLES:
                    return {
                        "pack_key": record_id,
                        "status": status,
                        "pack": {
                            key: deepcopy(value.get(key))
                            for key in (
                                "id",
                                "version",
                                "title",
                                "kind",
                                "profile_requirements",
                                "dependencies",
                                "rights",
                                "checksum",
                            )
                        },
                    }
                return {"pack_key": record_id, "status": status, "pack": deepcopy(value)}
            return {
                "active": list(packs["active"]),
                "imports": deepcopy(packs["imports"]),
                **(
                    {
                        "drafts": deepcopy(packs["drafts"]),
                        "finalized_packs": deepcopy(packs["finalized"]),
                    }
                    if membership.role in ADMIN_ROLES
                    else {}
                ),
            }
        if kind == "campaign_design":
            if membership.role not in ADMIN_ROLES or not self._has_facilitator_authority(
                document, membership.role
            ):
                raise PermissionError("campaign design is facilitator-private")
            return deepcopy(document.get("campaign_design") or {})
        if kind == "scene":
            values = document["scenes"]
        elif kind == "record":
            values = document["records"]
        else:
            raise ValueError(
                "query kind must be profile, pack, campaign_design, scene, or record"
            )

        def visible(value: Mapping[str, Any]) -> bool:
            return self._record_visible(
                document,
                campaign_id=campaign_id,
                principal_id=principal_id,
                role=membership.role,
                record=value,
            )

        if record_id:
            value = values.get(record_id)
            if value is None:
                raise LookupError(record_id)
            if not visible(value):
                raise PermissionError("record audience denies this principal")
            return deepcopy(value)
        return {"items": [deepcopy(value) for value in values.values() if visible(value)]}

    @staticmethod
    def _manifest_evidence_refs(manifest: Mapping[str, Any]) -> set[str]:
        """Collect every evidence-bearing field without treating cross-links as evidence."""

        result: set[str] = set()

        def visit(value: Any) -> None:
            if isinstance(value, Mapping):
                for key, nested in value.items():
                    if key in {"basis_refs", "evidence_refs"} and isinstance(nested, list):
                        result.update(str(item) for item in nested)
                    else:
                        visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        visit(manifest)
        return result

    def _current_evidence_refs(
        self,
        campaign_id: str,
        *,
        branch_id: str,
        document: Mapping[str, Any],
    ) -> set[str]:
        """Return typed references that demonstrably exist on the selected branch."""

        result = {f"record:{item}" for item in document.get("records", {})}
        result.update(f"scene:{item}" for item in document.get("scenes", {}))
        with self.database.transaction() as session:
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                raise LookupError(campaign_id)
            branch = resolve_branch(session, campaign, branch_id)
            result.update(
                f"event:{item.id}"
                for item in self.events._branch_rows(session, campaign_id, branch)
            )
        result.update(
            f"fact:{item.id}"
            for item in self.facts.list(campaign_id, branch_id=branch_id)
        )
        design = dict(document.get("campaign_design") or {})
        for manifest in dict(design.get("manifests") or {}).values():
            result.add(f"manifest:{manifest['id']}")
            atlas = dict(manifest.get("atlas") or {})
            for collection, prefix in (("chapters", "chapter"), ("scenes", "scene")):
                result.update(
                    f"{prefix}:{item['id']}" for item in atlas.get(collection, [])
                )
            for collection, prefix in (
                ("fronts", "front"),
                ("threads", "thread"),
                ("clues", "clue"),
                ("character_arcs", "character_arc"),
            ):
                result.update(
                    f"{prefix}:{item['id']}" for item in manifest.get(collection, [])
                )
        return result

    def _verify_episode_pack_boundary(
        self,
        document: Mapping[str, Any],
        *,
        campaign_id: str,
        branch_id: str,
        pack_key: str,
        pack: Mapping[str, Any],
    ) -> None:
        manifest = dict(dict(pack.get("content") or {}).get("runtime_manifest") or {})
        if manifest.get("classification") != "emergent_episode":
            return
        attestation = dict(
            dict(pack.get("content") or {}).get("runtime_attestation") or {}
        )
        signature = str(attestation.pop("signature", ""))
        expected = self._sign_expansion_envelope(attestation)
        if not signature or not secrets.compare_digest(signature, expected):
            raise PermissionError(
                "emergent episode requires a signed campaign expansion attestation"
            )
        if (
            attestation.get("schema_version") != 1
            or attestation.get("campaign_id") != campaign_id
            or attestation.get("branch_id") != branch_id
            or attestation.get("pack_key") != pack_key
            or attestation.get("manifest_digest") != checksum(manifest)
        ):
            raise ValueError("campaign expansion attestation is out of scope")
        cited = self._manifest_evidence_refs(manifest)
        if cited != set(attestation.get("evidence_refs") or []):
            raise ValueError("campaign expansion attestation does not cover all evidence refs")
        actual = self._current_evidence_refs(
            campaign_id, branch_id=branch_id, document=document
        )
        if unknown := sorted(cited - actual):
            raise ValueError(f"emergent episode cites missing branch evidence: {unknown}")

        parent_manifest_id = str(dict(manifest.get("lineage") or {}).get("parent_id") or "")
        packs = dict(document.get("packs") or {})
        finalized = dict(packs.get("finalized") or {})
        parent_pack_key = next(
            (
                active_key
                for active_key in packs.get("active") or []
                if dict(dict(finalized.get(active_key) or {}).get("content") or {})
                .get("runtime_manifest", {})
                .get("id")
                == parent_manifest_id
            ),
            None,
        )
        if parent_pack_key is None:
            raise ValueError("emergent episode lineage parent Pack must already be active")
        parent = dict(finalized[parent_pack_key])
        required_dependency = {
            "id": parent["id"],
            "version": parent["version"],
            "checksum": parent["checksum"],
        }
        if required_dependency not in list(pack.get("dependencies") or []):
            raise ValueError(
                "emergent episode dependencies must include its parent Pack checksum"
            )

    @staticmethod
    def _contains_evidence_link(value: Any, links: set[str]) -> bool:
        if isinstance(value, Mapping):
            return any(
                NarrativeRuntime._contains_evidence_link(item, links)
                for item in value.values()
            )
        if isinstance(value, list):
            return any(
                NarrativeRuntime._contains_evidence_link(item, links) for item in value
            )
        return isinstance(value, str) and value in links

    def _player_opportunity_has_occurred_evidence(
        self,
        campaign_id: str,
        *,
        branch_id: str,
        document: Mapping[str, Any],
        target: Mapping[str, Any],
        evidence_refs: list[str],
    ) -> bool:
        """Require player-arc completion proof from play, not authored Atlas metadata."""

        actor_ref = str(target.get("actor_ref") or "")
        core_actor_id = str(
            dict(document.get("actor_bindings") or {}).get(actor_ref) or actor_ref
        )
        declared_scenes = {
            item
            for item in target.get("opportunities") or []
            if str(item).startswith("scene:")
        }
        link_values = {actor_ref, core_actor_id, *declared_scenes}
        link_values.update(item.removeprefix("scene:") for item in declared_scenes)

        event_by_ref: dict[str, dict[str, Any]] = {}
        with self.database.transaction() as session:
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                raise LookupError(campaign_id)
            branch = resolve_branch(session, campaign, branch_id)
            for item in self.events._branch_rows(session, campaign_id, branch):
                event_by_ref[f"event:{item.id}"] = {
                    "audience_scope": str(item.audience_scope),
                    "links": {
                        "payload": deepcopy(dict(item.payload or {})),
                        "participants": [
                            {"actor_id": str(participant.actor_id)}
                            for participant in getattr(item, "participants", [])
                        ],
                    },
                }
        fact_by_ref = {
            f"fact:{item.id}": item
            for item in self.facts.list(campaign_id, branch_id=branch_id)
        }
        for evidence_ref in evidence_refs:
            if evidence_ref.startswith("event:"):
                event = event_by_ref.get(evidence_ref)
                if event is None or event["audience_scope"] in {
                    "dm",
                    "facilitator",
                    "private_worker",
                }:
                    continue
                if self._contains_evidence_link(event["links"], link_values):
                    return True
            elif evidence_ref.startswith("fact:"):
                fact = fact_by_ref.get(evidence_ref)
                if fact is None or str(fact.disclosure_scope) not in {
                    "party",
                    "player",
                    "public",
                }:
                    continue
                if self._contains_evidence_link(
                    {"subject_ref": str(fact.subject_ref or "")}, link_values
                ):
                    return True
            elif evidence_ref.startswith("record:"):
                record = dict(
                    document.get("records", {}).get(evidence_ref.removeprefix("record:"))
                    or {}
                )
                audience = dict(record.get("audience") or {})
                scope = str(audience.get("scope") or "table")
                actor_targets = {
                    str(item)
                    for item in [audience.get("actor_id"), *(audience.get("actor_ids") or [])]
                    if item
                }
                audience_safe = scope in {"public", "table"} or (
                    scope in {"actor", "group"}
                    and bool(actor_targets.intersection({actor_ref, core_actor_id}))
                )
                if audience_safe and self._contains_evidence_link(record, link_values):
                    return True
        return False

    def campaign_design_change(
        self,
        campaign_id: str,
        *,
        entity_type: str,
        entity_id: str,
        status: str,
        evidence_refs: list[str],
        note: str = "",
        **common: Any,
    ) -> dict[str, Any]:
        """Advance one declared narrative line with explicit play evidence."""

        membership = self.access.require_campaign(campaign_id, str(common["principal_id"]))
        current_document = narrative_document(self.campaigns.get(campaign_id).state)
        if not self._has_facilitator_authority(current_document, membership.role):
            raise PermissionError("campaign progress requires facilitator authority")
        if current_document["phase"] != PHASE_PLAY or current_document.get("conflict"):
            raise ValueError("campaign progress changes require non-conflict Play")

        def mutate(document: dict[str, Any]) -> dict[str, Any]:
            if not self._has_facilitator_authority(document, membership.role):
                raise PermissionError("campaign progress requires facilitator authority")
            if document["phase"] != PHASE_PLAY or document.get("conflict"):
                raise ValueError("campaign progress changes require non-conflict Play")
            design = dict(document.get("campaign_design") or {})
            if not design.get("manifests"):
                raise ValueError("campaign design has no active runtime manifest")
            change = validate_progress_change(
                design,
                entity_type=entity_type,
                entity_id=entity_id,
                status=status,
                evidence_refs=evidence_refs,
                note=note,
            )
            actual_refs = self._current_evidence_refs(
                campaign_id,
                branch_id=str(common["expected_branch_id"]),
                document=document,
            )
            if unknown := sorted(set(change["evidence_refs"]) - actual_refs):
                raise ValueError(f"campaign progress cites missing branch evidence: {unknown}")
            if entity_type == "character_arc" and status == "completed":
                target = next(
                    (
                        item
                        for manifest in design["manifests"].values()
                        for item in manifest.get("character_arcs") or []
                        if item["id"] == entity_id
                    ),
                    None,
                )
                if (
                    target
                    and target.get("arc_type") == "player_opportunity"
                    and not self._player_opportunity_has_occurred_evidence(
                        campaign_id,
                        branch_id=str(common["expected_branch_id"]),
                        document=document,
                        target=target,
                        evidence_refs=change["evidence_refs"],
                    )
                ):
                    raise ValueError(
                        "player arc completion requires occurred branch-local "
                        "audience-safe evidence linked to its actor or opportunity scene"
                    )
            progress = document["campaign_design"].setdefault("progress", {})
            values = progress.setdefault(entity_type, {})
            current = dict(values.get(entity_id) or {})
            history = list(current.get("history") or [])
            history.append(
                {
                    "status": change["status"],
                    "evidence_refs": change["evidence_refs"],
                    "note": change["note"],
                    "campaign_revision": int(common["expected_revision"]) + 1,
                }
            )
            values[entity_id] = {
                "status": change["status"],
                "evidence_refs": change["evidence_refs"],
                "note": change["note"],
                "history": history,
            }
            return {"campaign_design": deepcopy(document["campaign_design"]), "change": change}

        return self._write(
            campaign_id,
            operation="campaign_design.advance",
            payload={
                "entity_type": entity_type,
                "entity_id": entity_id,
                "status": status,
                "evidence_refs": evidence_refs,
                "note": note,
            },
            mutate=mutate,
            roles=ADMIN_ROLES,
            **common,
        )

    def campaign_expansion(
        self,
        campaign_id: str,
        *,
        action: str,
        data: dict[str, Any] | None,
        principal_id: str,
    ) -> dict[str, Any]:
        """Issue or validate a bounded zero-tool expansion proposal.

        This boundary intentionally cannot persist content. A validated proposal
        must still travel through the Pack draft/finalize/import/activate lifecycle.
        """

        membership = self.access.require_campaign(campaign_id, principal_id)
        campaign = self.campaigns.get(campaign_id)
        document = narrative_document(campaign.state)
        if not self._has_facilitator_authority(document, membership.role):
            raise PermissionError("campaign expansion requires facilitator authority")
        if document["phase"] not in {PHASE_LOBBY, PHASE_PLAY} or document.get("conflict"):
            raise ValueError("campaign expansion is unavailable during Conflict")
        branch = self.branches.current(campaign_id)
        design = dict(document.get("campaign_design") or {})
        if not design.get("manifests"):
            raise ValueError("campaign expansion requires one active runtime manifest")

        def fingerprint(value: str) -> str:
            return hashlib.sha256(value.encode("utf-8")).hexdigest()

        def sign_receipt(receipt: Mapping[str, Any]) -> str:
            return self._sign_expansion_envelope(receipt)

        payload = deepcopy(dict(data or {}))
        if action == "context":
            budget_chars = payload.get("budget_chars", 24_000)
            if (
                isinstance(budget_chars, bool)
                or not isinstance(budget_chars, int)
                or not 1_024 <= budget_chars <= 50_000
            ):
                raise ValueError("campaign expansion budget_chars must be 1024..50000")
            projections: list[dict[str, Any]] = []
            for manifest in design["manifests"].values():
                projections.append(
                    {
                        "ref": f"manifest:{manifest['id']}",
                        "kind": "manifest",
                        "summary": str(dict(manifest.get("setting") or {}).get("premise") or ""),
                        "lineage": deepcopy(manifest["lineage"]),
                    }
                )
                atlas = dict(manifest.get("atlas") or {})
                for collection, kind in (("chapters", "chapter"), ("scenes", "scene")):
                    projections.extend(
                        {
                            "ref": f"{kind}:{item['id']}",
                            "kind": kind,
                            "summary": item["summary"],
                            "status": item["status"],
                        }
                        for item in atlas.get(collection, [])
                    )
                for collection, kind in (
                    ("fronts", "front"),
                    ("threads", "thread"),
                    ("clues", "clue"),
                    ("character_arcs", "character_arc"),
                ):
                    projections.extend(
                        {
                            "ref": f"{kind}:{item['id']}",
                            "kind": kind,
                            "summary": item.get("summary") or item.get("question") or "",
                            "status": item["status"],
                        }
                        for item in manifest.get(collection, [])
                    )
            for record in document.get("records", {}).values():
                if dict(record.get("audience") or {}).get("scope") == "private_worker":
                    continue
                if not self._record_visible(
                    document,
                    campaign_id=campaign_id,
                    principal_id=principal_id,
                    role=membership.role,
                    record=record,
                ):
                    continue
                record_data = dict(record.get("data") or {})
                projections.append(
                    {
                        "ref": f"record:{record['id']}",
                        "kind": "record",
                        "summary": str(
                            record_data.get("summary")
                            or record_data.get("content")
                            or record.get("title")
                            or record["id"]
                        ),
                        "audience": deepcopy(record.get("audience") or {"scope": "table"}),
                    }
                )
            projections.extend(
                {
                    "ref": f"event:{item.id}",
                    "kind": "event",
                    "summary": item.summary,
                    "audience_scope": item.audience_scope,
                }
                for item in self.events.list_for_audience(
                    campaign_id,
                    audience="dm",
                    branch_id=branch.id,
                    limit=500,
                )
            )
            projections.extend(
                {
                    "ref": f"fact:{item.id}",
                    "kind": "fact",
                    "summary": item.content,
                    "disclosure_scope": item.disclosure_scope,
                }
                for item in self.facts.list(campaign_id, branch_id=branch.id)
            )
            projections.sort(key=lambda item: (item["kind"], item["ref"]))
            capsule = {
                "schema_version": 1,
                "campaign_id": campaign_id,
                "branch_id": branch.id,
                "campaign_revision": campaign.revision,
                "principal_fingerprint": fingerprint(principal_id),
                "campaign_mode": design["campaign_mode"],
                "evidence": [],
                "allowed_basis_refs": [],
            }
            if len(canonical_json(capsule)) > budget_chars:
                raise ValueError("campaign expansion budget is too small for its binding")
            omitted = 0
            for projection in projections:
                trial = deepcopy(capsule)
                trial["evidence"].append(projection)
                trial["allowed_basis_refs"].append(projection["ref"])
                if len(canonical_json(trial)) > budget_chars:
                    omitted += 1
                    continue
                capsule = trial
            receipt = deepcopy(capsule)
            receipt["signature"] = sign_receipt(capsule)
            return {
                "schema_version": 1,
                "context": capsule,
                "context_receipt": receipt,
                "budget": {
                    "budget_chars": budget_chars,
                    "used_chars": len(canonical_json(capsule)),
                    "omitted_evidence": omitted,
                },
                "worker_contract": {
                    "execution": "bounded_expansion_worker",
                    "tools_exposed": False,
                    "persist_worker_session": False,
                    "authoritative_result": False,
                    "proposal_only": True,
                    "output_contract": "campaign-expansion-proposal.v1",
                },
            }
        if action != "validate":
            raise ValueError("campaign expansion action must be context or validate")
        raw_receipt = payload.get("context_receipt")
        if not isinstance(raw_receipt, Mapping):
            raise ValueError("campaign expansion context_receipt is required")
        receipt = deepcopy(dict(raw_receipt))
        signature = str(receipt.pop("signature", ""))
        if not signature or not secrets.compare_digest(signature, sign_receipt(receipt)):
            raise PermissionError("campaign expansion context receipt is invalid")
        if (
            receipt.get("campaign_id") != campaign_id
            or receipt.get("branch_id") != branch.id
            or receipt.get("campaign_revision") != campaign.revision
            or receipt.get("principal_fingerprint") != fingerprint(principal_id)
        ):
            raise ValueError("campaign expansion context receipt is stale or out of scope")
        proposal = payload.get("proposal")
        if not isinstance(proposal, Mapping):
            raise ValueError("campaign expansion proposal must be an object")
        pack_key = required_text(proposal.get("pack_key"), "proposal.pack_key", limit=300)
        packs = document["packs"]
        if any(pack_key in packs[name] for name in ("drafts", "finalized", "imports")):
            raise ValueError("campaign expansion must propose a new Pack version")
        manifest = validate_runtime_manifest(
            dict(proposal.get("runtime_manifest") or {}),
            pack_key=pack_key,
        )
        if manifest["classification"] != "emergent_episode":
            raise ValueError("campaign expansion must propose an emergent_episode")
        allowed = set(receipt.get("allowed_basis_refs") or [])
        cited = self._manifest_evidence_refs(manifest)
        if unknown := sorted(cited - allowed):
            raise ValueError(f"campaign expansion cites unauthorized basis refs: {unknown}")
        next_design = validate_campaign_design({**design["manifests"], pack_key: manifest})
        attestation = {
            "schema_version": 1,
            "campaign_id": campaign_id,
            "branch_id": branch.id,
            "issued_revision": campaign.revision,
            "principal_fingerprint": fingerprint(principal_id),
            "pack_key": pack_key,
            "manifest_digest": checksum(manifest),
            "evidence_refs": sorted(cited),
            "context_digest": hashlib.sha256(
                canonical_json(receipt).encode("utf-8")
            ).hexdigest(),
        }
        attestation["signature"] = sign_receipt(attestation)
        return {
            "schema_version": 1,
            "status": "validated_proposal",
            "proposal": {"pack_key": pack_key, "runtime_manifest": manifest},
            "proposal_attestation": attestation,
            "campaign_design": next_design,
            "settlement_route": [
                "pack_change.create_draft",
                "pack_change.finalize",
                "pack_change.import",
                "pack_change.activate",
            ],
        }

    def record_change(
        self,
        campaign_id: str,
        *,
        action: str,
        record: dict[str, Any],
        expected_record_revision: int | None = None,
        **common: Any,
    ) -> dict[str, Any]:
        principal_id = str(common["principal_id"])
        role = self.access.require_campaign(campaign_id, principal_id).role

        def mutate(document: dict[str, Any]) -> dict[str, Any]:
            normalized = validate_record(record)
            self._validate_record_profile(active_profile(document), normalized)
            current = document["records"].get(normalized["id"])
            if action == "create":
                if current is not None:
                    raise ValueError("narrative record already exists")
                normalized["revision"] = 1
            elif action == "update":
                if current is None:
                    raise LookupError(normalized["id"])
                if int(current["revision"]) != int(expected_record_revision or -1):
                    raise ValueError("narrative record revision conflict")
                if not self._record_authorized(
                    document,
                    campaign_id=campaign_id,
                    principal_id=principal_id,
                    role=role,
                    record=current,
                    control=True,
                ):
                    raise PermissionError("principal does not control this narrative element")
                normalized["revision"] = int(current["revision"]) + 1
            else:
                raise ValueError("record action must be create or update")
            if action == "create" and not self._has_facilitator_authority(document, role):
                if not self._record_authorized(
                    document,
                    campaign_id=campaign_id,
                    principal_id=principal_id,
                    role=role,
                    record=normalized,
                    control=True,
                ):
                    raise PermissionError("principal cannot create this narrative element")
            document["records"][normalized["id"]] = normalized
            return {"record": normalized}

        return self._write(
            campaign_id,
            operation=f"record.{action}",
            payload={
                "action": action,
                "record": record,
                "expected_record_revision": expected_record_revision,
            },
            mutate=mutate,
            **common,
        )

    def scene_change(
        self,
        campaign_id: str,
        *,
        action: str,
        scene: dict[str, Any] | None = None,
        scene_id: str | None = None,
        **common: Any,
    ) -> dict[str, Any]:
        membership = self.access.require_campaign(campaign_id, common["principal_id"])
        if membership.role == "observer":
            raise PermissionError("observer role is read-only")

        def authorized(document: Mapping[str, Any], value: Mapping[str, Any]) -> bool:
            if self._has_facilitator_authority(document, membership.role):
                return True
            scene_participants = [str(item) for item in value.get("participants") or []]
            if scene_participants and any(
                self._actor_authorized(
                    document,
                    campaign_id=campaign_id,
                    actor_id=actor_ref,
                    principal_id=common["principal_id"],
                    role=membership.role,
                    control=True,
                )
                for actor_ref in scene_participants
                if actor_ref
            ):
                return True
            for steward in value.get("active_stewards") or []:
                if not isinstance(steward, Mapping):
                    continue
                if steward.get("principal_id") != common["principal_id"]:
                    continue
                refs = [str(item) for item in steward.get("element_refs") or []]
                if not refs:
                    continue
                scene_ref = str(value.get("id") or document.get("active_scene_id") or "")

                def controls(ref: str) -> bool:
                    for grant in document.get("element_grants", []):
                        if (
                            grant.get("principal_id") != common["principal_id"]
                            or grant.get("element_ref") != ref
                            or not grant.get("can_control")
                        ):
                            continue
                        grant_scene = str(dict(grant.get("scope") or {}).get("scene_id") or "")
                        if not grant_scene or grant_scene == scene_ref:
                            return True
                    return self._actor_authorized(
                        document,
                        campaign_id=campaign_id,
                        actor_id=ref,
                        principal_id=common["principal_id"],
                        role=membership.role,
                        control=True,
                    )

                if all(controls(ref) for ref in refs):
                    return True
            return False

        def mutate(document: dict[str, Any]) -> dict[str, Any]:
            if document["phase"] != PHASE_PLAY:
                raise ValueError("scene changes require play phase")
            if action == "start":
                if document.get("active_scene_id"):
                    raise ValueError("close the current scene before starting another")
                value = deepcopy(dict(scene or {}))
                identifier = required_id(value.get("id"), "scene.id")
                if identifier in document["scenes"]:
                    raise ValueError("scene already exists")
                if not authorized(document, value):
                    raise PermissionError("scene start requires facilitator or active stewardship")
                value.update({"id": identifier, "status": "active", "revision": 1})
                value["title"] = required_text(
                    value.get("title") or identifier, "scene.title", limit=200
                )
                value["audience"] = validate_audience(
                    value.get("audience"), field="scene.audience"
                )
                profile = active_profile(document)
                allowed_audiences = set(
                    dict(profile.get("authority") or {}).get("audience_scopes") or []
                ) if profile else set()
                if allowed_audiences and value["audience"]["scope"] not in allowed_audiences:
                    raise ValueError("scene audience is not allowed by the active profile")
                document["scenes"][identifier] = value
                document["active_scene_id"] = identifier
                return {"scene": value}
            identifier = required_id(scene_id, "scene_id")
            value = document["scenes"].get(identifier)
            if value is None or document.get("active_scene_id") != identifier:
                raise ValueError("scene is not active")
            if not authorized(document, value):
                raise PermissionError("scene mutation belongs to its facilitator or steward")
            if action == "update":
                changes = deepcopy(dict(scene or {}))
                for protected in ("id", "status", "revision", "active_stewards"):
                    changes.pop(protected, None)
                if "audience" in changes:
                    changes["audience"] = validate_audience(
                        changes["audience"], field="scene.audience"
                    )
                    profile = active_profile(document)
                    allowed_audiences = set(
                        dict(profile.get("authority") or {}).get("audience_scopes") or []
                    ) if profile else set()
                    if (
                        allowed_audiences
                        and changes["audience"]["scope"] not in allowed_audiences
                    ):
                        raise ValueError("scene audience is not allowed by the active profile")
                value.update(changes)
                value["revision"] += 1
            elif action == "end":
                value["status"] = "ended"
                value["revision"] += 1
                document["active_scene_id"] = None
            else:
                raise ValueError("scene action must be start, update, or end")
            return {"scene": deepcopy(value)}

        return self._write(
            campaign_id,
            operation=f"scene.{action}",
            payload={"action": action, "scene": scene, "scene_id": scene_id},
            mutate=mutate,
            **common,
        )

    def actor_query(
        self, campaign_id: str, *, principal_id: str, actor_id: str | None = None
    ) -> dict[str, Any]:
        membership = self.access.require_campaign(campaign_id, principal_id)
        document = narrative_document(self.campaigns.get(campaign_id).state)
        bindings = document.get("actor_bindings", {})

        def project(core_id: str) -> dict[str, Any]:
            readable, _, can_view_private = self._actor_access(
                document,
                campaign_id=campaign_id,
                actor_id=core_id,
                principal_id=principal_id,
                role=membership.role,
            )
            if not readable:
                raise PermissionError(f"principal cannot access actor: {core_id}")
            value = asdict(self.characters.get(core_id))
            if not can_view_private:
                value["sheet"] = {}
                value["notes"] = {}
            aliases = [key for key, value in bindings.items() if value == core_id]
            value["actor_ref"] = aliases[0] if aliases else core_id
            return value

        if actor_id:
            core_id = str(bindings.get(actor_id) or actor_id)
            if not self._actor_authorized(
                document,
                campaign_id=campaign_id,
                actor_id=core_id,
                principal_id=principal_id,
                role=membership.role,
            ):
                raise PermissionError(f"principal cannot access actor: {actor_id}")
            return project(core_id)
        visible = []
        for actor in self.characters.list(campaign_id=campaign_id):
            if not self._actor_authorized(
                document,
                campaign_id=campaign_id,
                actor_id=actor.id,
                principal_id=principal_id,
                role=membership.role,
            ):
                continue
            visible.append(project(actor.id))
        return {"actors": visible}

    def resolve_actor_ref(self, campaign_id: str, actor_ref: str) -> str:
        document = narrative_document(self.campaigns.get(campaign_id).state)
        return str(document.get("actor_bindings", {}).get(actor_ref) or actor_ref)

    def principal_controls_actor(self, campaign_id: str, principal_id: str) -> bool:
        """Return whether a campaign member controls at least one actor."""

        with self.database.transaction() as session:
            return (
                session.execute(
                    select(ActorGrant.actor_id).where(
                        ActorGrant.campaign_id == campaign_id,
                        ActorGrant.principal_id == principal_id,
                        ActorGrant.can_control.is_(True),
                    )
                ).first()
                is not None
            )

    def actor_create(
        self,
        campaign_id: str,
        *,
        principal_id: str,
        idempotency_key: str,
        actor: dict[str, Any],
        expected_revision: int | None = None,
        expected_branch_id: str | None = None,
    ) -> dict[str, Any]:
        # Public MCP requires both values. Direct trusted service callers that
        # predate the facade receive an immediately-read CAS target here; the
        # same conditional SQL write still protects the transaction.
        if expected_revision is None:
            expected_revision = self.campaigns.get(campaign_id).revision
        if not expected_branch_id:
            expected_branch_id = self.branch_id(campaign_id)
        self.access.require_campaign(campaign_id, principal_id, roles=ADMIN_ROLES)
        actor = self._normalize_actor_input(actor)
        key = required_text(idempotency_key, "idempotency_key", limit=200)
        value = deepcopy(actor)
        name = required_text(value.get("name"), "actor.name", limit=200)
        actor_id = str(uuid.uuid4())
        payload = {"actor": value, "expected_revision": expected_revision}
        scope = f"narrative:actor.create:{campaign_id}:{expected_branch_id}:{principal_id}"
        with self.database.transaction() as session:
            replay = self.idempotency.lookup_in_session(session, scope, key, payload)
            if replay and replay.response:
                return deepcopy(replay.response)
            campaign = session.get(Campaign, campaign_id)
            if (
                campaign is None
                or campaign.revision != expected_revision
                or campaign.active_branch_id != expected_branch_id
            ):
                raise ValueError("campaign revision or branch conflict")
            document = narrative_document(campaign.state)
            if any(
                item.get("status") == "open"
                for item in document.get("npc_conversations", {}).values()
            ):
                raise ValueError("close or abort every NPC conversation before actor creation")
            actor_row = Character(
                id=actor_id,
                system_id="narrative",
                campaign_id=campaign_id,
                character_type=required_id(value.get("type") or "pc", "actor.type"),
                name=name,
                player_name=value.get("player_name"),
                summary=str(value.get("summary") or ""),
                sheet=deepcopy(dict(value.get("sheet") or {})),
                notes=deepcopy(dict(value.get("notes") or {})),
            )
            self._validate_actor_profile(active_profile(document), value)
            session.add(actor_row)
            session.flush()
            before = deepcopy(campaign.state)
            changed = session.execute(
                update(Campaign)
                .where(
                    Campaign.id == campaign_id,
                    Campaign.revision == expected_revision,
                    Campaign.active_branch_id == expected_branch_id,
                )
                .values(revision=Campaign.revision + 1)
                .returning(Campaign.revision)
            ).scalar_one_or_none()
            if changed is None:
                raise ValueError("campaign revision conflict during actor creation")
            response = {
                "id": actor_id,
                "system_id": "narrative",
                "campaign_id": campaign_id,
                "template_id": None,
                "character_type": actor_row.character_type,
                "name": name,
                "player_name": actor_row.player_name,
                "summary": actor_row.summary,
                "sheet": deepcopy(actor_row.sheet),
                "notes": deepcopy(actor_row.notes),
                "revision": actor_row.revision,
                "campaign_revision": int(changed),
            }
            revisions = self.revisions.record_group_in_session(
                session,
                campaign_id,
                operation="narrative.actor.create",
                actor=principal_id,
                branch_id=expected_branch_id,
                idempotency_key=key,
                request_hash=request_hash(payload),
                reversible=False,
                changes=[
                    {
                        "entity_type": "campaign",
                        "entity_id": campaign_id,
                        "before": {"state": before, "revision": expected_revision},
                        "after": {"state": before, "revision": int(changed)},
                    }
                ],
            )
            self.idempotency.remember_in_session(
                session,
                scope,
                key,
                payload,
                response,
                campaign_id=campaign_id,
                mutation_group_id=revisions[0].mutation_group_id,
            )
            return response

    def actor_update(
        self,
        campaign_id: str,
        *,
        principal_id: str,
        actor_id: str,
        actor: dict[str, Any],
        expected_actor_revision: int,
        expected_revision: int,
        expected_branch_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        membership = self.access.require_campaign(campaign_id, principal_id)
        actor = self._normalize_actor_input(actor)
        key = required_text(idempotency_key, "idempotency_key", limit=200)
        scope = f"narrative:actor.update:{campaign_id}:{expected_branch_id}:{principal_id}"
        payload = {
            "actor_id": actor_id,
            "actor": deepcopy(actor),
            "expected_actor_revision": int(expected_actor_revision),
            "expected_revision": int(expected_revision),
        }
        with self.database.transaction() as session:
            replay = self.idempotency.lookup_in_session(session, scope, key, payload)
            if replay is not None and replay.response is not None:
                return deepcopy(replay.response)
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                raise LookupError(campaign_id)
            branch = resolve_branch(session, campaign)
            if branch.id != expected_branch_id or campaign.revision != int(expected_revision):
                raise ValueError("campaign revision or branch conflict")
            document = narrative_document(campaign.state)
            core_actor_id = str(document.get("actor_bindings", {}).get(actor_id) or actor_id)
            if not self._actor_authorized(
                document,
                campaign_id=campaign_id,
                actor_id=core_actor_id,
                principal_id=principal_id,
                role=membership.role,
                control=True,
            ):
                raise PermissionError("actor update requires actor control")
            if any(
                item.get("status") == "open"
                for item in document.get("npc_conversations", {}).values()
            ):
                raise ValueError("close or abort every NPC conversation before actor update")
            row = session.get(Character, core_actor_id)
            if row is None or row.campaign_id != campaign_id:
                raise LookupError(actor_id)
            if row.revision != int(expected_actor_revision):
                raise ValueError("actor revision conflict")
            changes = deepcopy(dict(actor))
            proposed = {
                "name": changes.get("name", row.name),
                "type": changes.get("type", row.character_type),
                "player_name": changes.get("player_name", row.player_name),
                "summary": changes.get("summary", row.summary),
                "sheet": changes.get("sheet", row.sheet),
                "notes": changes.get("notes", row.notes),
            }
            proposed["name"] = required_text(proposed["name"], "actor.name", limit=200)
            proposed["type"] = required_id(proposed["type"], "actor.type")
            proposed["sheet"] = deepcopy(dict(proposed.get("sheet") or {}))
            proposed["notes"] = deepcopy(dict(proposed.get("notes") or {}))
            self._validate_actor_profile(active_profile(document), proposed)
            changed_actor = session.execute(
                update(Character)
                .where(
                    Character.id == core_actor_id,
                    Character.campaign_id == campaign_id,
                    Character.revision == int(expected_actor_revision),
                )
                .values(
                    name=proposed["name"],
                    character_type=proposed["type"],
                    player_name=proposed["player_name"],
                    summary=str(proposed["summary"] or ""),
                    sheet=proposed["sheet"],
                    notes=proposed["notes"],
                    revision=Character.revision + 1,
                )
                .returning(Character.revision)
            ).scalar_one_or_none()
            if changed_actor is None:
                raise ValueError("actor revision conflict during conditional update")
            changed_campaign = session.execute(
                update(Campaign)
                .where(
                    Campaign.id == campaign_id,
                    Campaign.revision == int(expected_revision),
                    Campaign.active_branch_id == expected_branch_id,
                )
                .values(revision=Campaign.revision + 1)
                .returning(Campaign.revision)
            ).scalar_one_or_none()
            if changed_campaign is None:
                raise ValueError("campaign revision conflict during actor update")
            response = {
                "id": core_actor_id,
                "actor_ref": actor_id,
                "campaign_id": campaign_id,
                "character_type": proposed["type"],
                "name": proposed["name"],
                "player_name": proposed["player_name"],
                "summary": str(proposed["summary"] or ""),
                "sheet": proposed["sheet"],
                "notes": proposed["notes"],
                "revision": int(changed_actor),
                "campaign_revision": int(changed_campaign),
                "branch_id": branch.id,
                "phase": document["phase"],
            }
            revisions = self.revisions.record_group_in_session(
                session,
                campaign_id,
                operation="narrative.actor.update",
                actor=principal_id,
                branch_id=branch.id,
                idempotency_key=key,
                request_hash=request_hash(payload),
                reversible=False,
                changes=[
                    {
                        "entity_type": "character",
                        "entity_id": core_actor_id,
                        "before": {"revision": int(expected_actor_revision)},
                        "after": {"revision": int(changed_actor)},
                    },
                    {
                        "entity_type": "campaign",
                        "entity_id": campaign_id,
                        "before": {
                            "state": deepcopy(campaign.state),
                            "revision": int(expected_revision),
                        },
                        "after": {
                            "state": deepcopy(campaign.state),
                            "revision": int(changed_campaign),
                        },
                    },
                ],
            )
            self.idempotency.remember_in_session(
                session,
                scope,
                key,
                payload,
                response,
                campaign_id=campaign_id,
                mutation_group_id=revisions[0].mutation_group_id,
            )
            return response

    def access_change(
        self,
        campaign_id: str,
        *,
        principal_id: str,
        action: str,
        target_principal_id: str,
        role: str | None = None,
        actor_id: str | None = None,
        can_control: bool = False,
        can_view_private: bool = False,
        element_ref: str | None = None,
        scope: dict[str, Any] | None = None,
        expected_revision: int | None = None,
        expected_branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.access.require_campaign(campaign_id, principal_id, roles={"owner"})
        if action in {"campaign_grant", "campaign_revoke", "actor_grant", "actor_revoke"}:
            if expected_revision is None or not expected_branch_id or not idempotency_key:
                raise ValueError("access changes require revision, branch, and idempotency")
            if action in {"actor_grant", "actor_revoke"} and not actor_id:
                raise ValueError("actor_id is required")
            payload = {
                "action": action,
                "target_principal_id": target_principal_id,
                "role": role,
                "actor_id": actor_id,
                "can_control": can_control,
                "can_view_private": can_view_private,
                "expected_revision": expected_revision,
                "expected_branch_id": expected_branch_id,
            }
            key = required_text(idempotency_key, "idempotency_key", limit=200)
            idempotency_scope = (
                f"narrative:access.{action}:{campaign_id}:{expected_branch_id}:{principal_id}"
            )
            with self.database.transaction() as session:
                replay = self.idempotency.lookup_in_session(
                    session, idempotency_scope, key, payload
                )
                if replay is not None and replay.response is not None:
                    return deepcopy(replay.response)
                campaign = session.get(Campaign, campaign_id)
                if campaign is None:
                    raise LookupError(campaign_id)
                branch = resolve_branch(session, campaign)
                if campaign.revision != int(expected_revision) or branch.id != expected_branch_id:
                    raise ValueError("campaign revision or branch conflict")
                before = deepcopy(campaign.state)
                document = narrative_document(campaign.state)
                if any(
                    item.get("status") == "open"
                    for item in document.get("npc_conversations", {}).values()
                ):
                    raise ValueError("close or abort every NPC conversation before access changes")
                target = session.get(Principal, target_principal_id)
                if target is None and action in {"campaign_revoke", "actor_revoke"}:
                    raise LookupError(target_principal_id)
                if target is None:
                    target = Principal(
                        id=target_principal_id,
                        platform="mcp",
                        external_id=target_principal_id,
                        display_name=target_principal_id,
                        is_service=False,
                    )
                    session.add(target)
                    session.flush()
                if action in {"campaign_grant", "campaign_revoke"}:
                    grant_row = session.get(
                        CampaignMembership,
                        {
                            "campaign_id": campaign_id,
                            "principal_id": target_principal_id,
                        },
                    )
                    if action == "campaign_revoke":
                        if grant_row is None:
                            raise LookupError(target_principal_id)
                        if grant_row.role == "owner":
                            owners = list(
                                session.scalars(
                                    select(CampaignMembership).where(
                                        CampaignMembership.campaign_id == campaign_id,
                                        CampaignMembership.role == "owner",
                                    )
                                )
                            )
                            if len(owners) <= 1:
                                raise ValueError("cannot revoke the campaign's last owner")
                        session.execute(
                            delete(ActorGrant).where(
                                ActorGrant.campaign_id == campaign_id,
                                ActorGrant.principal_id == target_principal_id,
                            )
                        )
                        session.delete(grant_row)
                        document["element_grants"] = [
                            item
                            for item in document.get("element_grants", [])
                            if item.get("principal_id") != target_principal_id
                        ]
                        grant = {
                            "campaign_id": campaign_id,
                            "principal_id": target_principal_id,
                            "status": "revoked",
                        }
                    else:
                        normalized_role = str(role or "player")
                        if normalized_role not in CAMPAIGN_ROLES:
                            raise ValueError(f"unsupported campaign role: {normalized_role}")
                        if (
                            grant_row is not None
                            and grant_row.role == "owner"
                            and normalized_role != "owner"
                        ):
                            owners = list(
                                session.scalars(
                                    select(CampaignMembership).where(
                                        CampaignMembership.campaign_id == campaign_id,
                                        CampaignMembership.role == "owner",
                                    )
                                )
                            )
                            if len(owners) <= 1:
                                raise ValueError("cannot demote the campaign's last owner")
                        if grant_row is None:
                            grant_row = CampaignMembership(
                                campaign_id=campaign_id,
                                principal_id=target_principal_id,
                                role=normalized_role,
                            )
                            session.add(grant_row)
                        else:
                            grant_row.role = normalized_role
                        grant = {
                            "campaign_id": campaign_id,
                            "principal_id": target_principal_id,
                            "role": normalized_role,
                            "status": "granted",
                        }
                else:
                    membership_row = session.get(
                        CampaignMembership,
                        {"campaign_id": campaign_id, "principal_id": target_principal_id},
                    )
                    if membership_row is None:
                        raise ValueError("actor grant target must be a campaign member")
                    core_actor_id = str(
                        document.get("actor_bindings", {}).get(actor_id) or actor_id
                    )
                    actor_row = session.get(Character, core_actor_id)
                    if actor_row is None or actor_row.campaign_id != campaign_id:
                        raise LookupError(str(actor_id))
                    grant_row = session.get(
                        ActorGrant,
                        {
                            "campaign_id": campaign_id,
                            "principal_id": target_principal_id,
                            "actor_id": core_actor_id,
                        },
                    )
                    if action == "actor_revoke":
                        if grant_row is None:
                            raise LookupError(f"{target_principal_id}:{actor_id}")
                        session.delete(grant_row)
                    elif grant_row is None:
                        grant_row = ActorGrant(
                            campaign_id=campaign_id,
                            principal_id=target_principal_id,
                            actor_id=core_actor_id,
                            can_control=bool(can_control),
                            can_view_private=bool(can_view_private),
                        )
                        session.add(grant_row)
                    else:
                        grant_row.can_control = bool(can_control)
                        grant_row.can_view_private = bool(can_view_private)
                    grant = {
                        "campaign_id": campaign_id,
                        "principal_id": target_principal_id,
                        "actor_id": core_actor_id,
                        "actor_ref": actor_id,
                        "can_control": bool(can_control),
                        "can_view_private": bool(can_view_private),
                        "status": "revoked" if action == "actor_revoke" else "granted",
                    }
                changed = session.execute(
                    update(Campaign)
                    .where(
                        Campaign.id == campaign_id,
                        Campaign.revision == int(expected_revision),
                        Campaign.active_branch_id == expected_branch_id,
                    )
                    .values(
                        state=state_with_narrative(before, document),
                        revision=Campaign.revision + 1,
                    )
                    .returning(Campaign.revision)
                ).scalar_one_or_none()
                if changed is None:
                    raise ValueError("campaign revision conflict during access change")
                response = {
                    **grant,
                    "campaign_revision": int(changed),
                    "branch_id": branch.id,
                    "phase": narrative_document(campaign.state)["phase"],
                }
                revisions = self.revisions.record_group_in_session(
                    session,
                    campaign_id,
                    operation=f"narrative.access.{action}",
                    actor=principal_id,
                    branch_id=branch.id,
                    idempotency_key=key,
                    request_hash=request_hash(payload),
                    reversible=False,
                    changes=[
                        {
                            "entity_type": "campaign",
                            "entity_id": campaign_id,
                            "before": {
                                "state": before,
                                "revision": int(expected_revision),
                            },
                            "after": {
                                "state": state_with_narrative(before, document),
                                "revision": int(changed),
                            },
                        },
                    ],
                )
                self.idempotency.remember_in_session(
                    session,
                    idempotency_scope,
                    key,
                    payload,
                    response,
                    campaign_id=campaign_id,
                    mutation_group_id=revisions[0].mutation_group_id,
                )
                return response
        if action in {"element_grant", "element_revoke"}:
            if expected_revision is None or not expected_branch_id or not idempotency_key:
                raise ValueError("element grants require revision, branch, and idempotency")
            normalized_ref = required_id(element_ref, "element_ref")
            if self.access.membership(campaign_id, target_principal_id) is None:
                raise ValueError("element grant target must be a campaign member")
            raw_scope = scope or {}
            if not isinstance(raw_scope, Mapping):
                raise ValueError("element grant scope must be an object")
            normalized_scope = deepcopy(dict(raw_scope))
            if set(normalized_scope) - {"mode", "scene_id"}:
                raise ValueError("element grant scope supports only mode and scene_id")
            if normalized_scope.get("mode") not in {None, "campaign", "scene"}:
                raise ValueError("element grant scope mode must be campaign or scene")
            if normalized_scope.get("scene_id") is not None:
                normalized_scope["scene_id"] = required_id(
                    normalized_scope["scene_id"], "scope.scene_id"
                )

            def mutate(document: dict[str, Any]) -> dict[str, Any]:
                grants = list(document["element_grants"])
                grants = [
                    item
                    for item in grants
                    if not (
                        item["principal_id"] == target_principal_id
                        and item["element_ref"] == normalized_ref
                    )
                ]
                if action == "element_grant":
                    grants.append(
                        {
                            "principal_id": target_principal_id,
                            "element_ref": normalized_ref,
                            "can_control": bool(can_control),
                            "can_view_private": bool(can_view_private),
                            "scope": normalized_scope,
                        }
                    )
                document["element_grants"] = grants
                return {
                    "element_ref": normalized_ref,
                    "target_principal_id": target_principal_id,
                    "status": "granted" if action == "element_grant" else "revoked",
                }

            return self._write(
                campaign_id,
                principal_id=principal_id,
                expected_revision=expected_revision,
                expected_branch_id=expected_branch_id,
                idempotency_key=idempotency_key,
                operation=f"access.{action}",
                payload={
                    "target_principal_id": target_principal_id,
                    "element_ref": normalized_ref,
                    "can_control": can_control,
                    "can_view_private": can_view_private,
                    "scope": normalized_scope,
                },
                mutate=mutate,
                roles={"owner"},
            )
        raise ValueError("unsupported access action")

    def _record_authorized(
        self,
        document: Mapping[str, Any],
        *,
        campaign_id: str,
        principal_id: str,
        role: str,
        record: Mapping[str, Any],
        control: bool,
    ) -> bool:
        if role in ADMIN_ROLES:
            profile = active_profile(document)
            authority = dict(profile.get("authority") or {}) if profile else {}
            facilitator_roles = authority.get("facilitator_roles")
            # Administrative ownership is not narrative authority in an
            # explicitly facilitator-less profile. Before profile activation,
            # the ordinary campaign admin contract still applies.
            if facilitator_roles is None or role in set(facilitator_roles):
                return True
        controller = dict(record.get("controller") or {})
        if controller.get("principal_id") == principal_id:
            return True
        actor_refs = []
        if controller.get("actor_id"):
            actor_refs.append(str(controller["actor_id"]))
        actor_refs.extend(str(item) for item in controller.get("actor_ids", []))
        bindings = dict(document.get("actor_bindings") or {})
        for actor_ref in actor_refs:
            try:
                if self._actor_authorized(
                    document,
                    campaign_id=campaign_id,
                    actor_id=str(bindings.get(actor_ref) or actor_ref),
                    principal_id=principal_id,
                    role=role,
                    control=control,
                    private=not control,
                ):
                    return True
            except (LookupError, PermissionError):
                continue
        element_ref = str(controller.get("element_ref") or record.get("id") or "")
        # Pack data names actors by stable logical references while live calls
        # may carry the authoritative core UUID returned during seed
        # materialization. Element stewardship is defined over the stable Pack
        # reference, so accept either representation at the write boundary.
        element_refs = {element_ref}
        element_refs.update(
            logical_ref
            for logical_ref, authoritative_id in bindings.items()
            if str(authoritative_id) == element_ref
        )
        scene_id = document.get("active_scene_id")
        for grant in document.get("element_grants", []):
            if (
                grant.get("principal_id") != principal_id
                or grant.get("element_ref") not in element_refs
            ):
                continue
            grant_scope = dict(grant.get("scope") or {})
            if grant_scope.get("scene_id") and grant_scope["scene_id"] != scene_id:
                continue
            if control and not grant.get("can_control"):
                continue
            if not control and not (grant.get("can_control") or grant.get("can_view_private")):
                continue
            return True
        return False

    @staticmethod
    def _has_facilitator_authority(document: Mapping[str, Any], role: str) -> bool:
        profile = active_profile(document)
        authority = dict(profile.get("authority") or {}) if profile else {}
        facilitator_roles = authority.get("facilitator_roles")
        return role in ADMIN_ROLES if facilitator_roles is None else role in set(facilitator_roles)

    def mechanic_resolve(
        self,
        campaign_id: str,
        *,
        mechanic_id: str,
        inputs: dict[str, Any],
        **common: Any,
    ) -> dict[str, Any]:
        def mutate(document: dict[str, Any]) -> dict[str, Any]:
            profile = active_profile(document)
            if not profile or int(profile["mechanics_level"]) != 1:
                raise ValueError("the active profile does not provide Level 1 mechanics")
            mechanics = {item["id"]: item for item in profile["mechanics"]}
            mechanic = mechanics.get(mechanic_id)
            if mechanic is None:
                raise LookupError(mechanic_id)
            stream = document["random_stream"]
            cursor_before = int(stream.get("cursor", 0))
            seed = stream.get("seed")
            if seed is None:
                seed = checksum({"campaign": campaign_id, "profile": profile["checksum"]})
                stream["seed"] = seed
            operation_seed = int(
                checksum(
                    {
                        "seed": seed,
                        "cursor": cursor_before,
                        "mechanic": mechanic_id,
                        "key": common["idempotency_key"],
                    }
                )[:16],
                16,
            )
            rng = random.Random(operation_seed)
            kind = mechanic["kind"]
            draws: list[int] = []
            if kind == "dice_pool":
                count = int(inputs.get("dice", 1))
                if count < 1 or count > int(mechanic["max_dice"]):
                    raise ValueError("dice count is outside the mechanic bounds")
                draws = [rng.randint(1, int(mechanic["sides"])) for _ in range(count)]
                score = max(draws)
                outcome = None
                for band in mechanic["bands"]:
                    if (
                        int(band.get("minimum", 0))
                        <= score
                        <= int(band.get("maximum", mechanic["sides"]))
                    ):
                        outcome = deepcopy(dict(band))
                        break
                if outcome is None:
                    raise ValueError("dice result is not covered by a result band")
                result = {"score": score, "draws": draws, "outcome": outcome}
            elif kind == "table":
                entries = mechanic["entries"]
                index = rng.randrange(len(entries))
                draws = [index]
                result = {"index": index, "entry": deepcopy(entries[index])}
            else:
                current = int(inputs.get("current"))
                delta = int(inputs.get("delta"))
                value = min(
                    int(mechanic["maximum"]), max(int(mechanic["minimum"]), current + delta)
                )
                result = {"before": current, "delta": delta, "after": value}
            stream["cursor"] = cursor_before + len(draws)
            receipt = {
                "profile_checksum": profile["checksum"],
                "mechanic_id": mechanic_id,
                "cursor_before": cursor_before,
                "cursor_after": stream["cursor"],
                "draws": draws,
            }
            return {"mechanic_id": mechanic_id, "result": result, "random_stream_receipt": receipt}

        return self._write(
            campaign_id,
            operation=f"mechanic.{mechanic_id}",
            payload={"mechanic_id": mechanic_id, "inputs": inputs},
            mutate=mutate,
            **common,
        )

    def narrative_settle(
        self,
        campaign_id: str,
        *,
        principal_id: str,
        expected_revision: int,
        expected_branch_id: str,
        idempotency_key: str,
        event: dict[str, Any],
        record_changes: list[dict[str, Any]] | None = None,
        facts: list[dict[str, Any]] | None = None,
        actor_knowledge: list[dict[str, Any]] | None = None,
        snapshot: dict[str, Any] | None = None,
        _close_conversation_id: str | None = None,
        _selected_proposal_ids: list[str] | None = None,
        _private_worker_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically settle campaign state, event, facts, knowledge and snapshot."""

        if not isinstance(event, Mapping):
            raise ValueError("event must be an object")
        for field, values in (
            ("record_changes", record_changes),
            ("facts", facts),
            ("actor_knowledge", actor_knowledge),
        ):
            if values is not None and (not isinstance(values, list) or len(values) > 128):
                raise ValueError(f"{field} must be an array with at most 128 entries")
            if any(not isinstance(item, Mapping) for item in values or []):
                raise ValueError(f"{field} entries must be objects")
        participants_input = event.get("participants")
        if participants_input is not None and (
            not isinstance(participants_input, list) or len(participants_input) > 128
        ):
            raise ValueError("event.participants must be an array with at most 128 entries")
        if any(not isinstance(item, Mapping) for item in participants_input or []):
            raise ValueError("event.participants entries must be objects")
        key = required_text(idempotency_key, "idempotency_key", limit=200)
        scope = f"narrative:settle:{campaign_id}:{expected_branch_id}:{principal_id}"
        request = {
            "campaign_id": campaign_id,
            "expected_revision": expected_revision,
            "expected_branch_id": expected_branch_id,
            "event": event,
            "record_changes": record_changes or [],
            "facts": facts or [],
            "actor_knowledge": actor_knowledge or [],
            "snapshot": snapshot,
            "close_conversation_id": _close_conversation_id,
            "selected_proposal_ids": _selected_proposal_ids or [],
            "private_worker_id": _private_worker_id,
        }
        if len(canonical_json(request)) > 1_000_000:
            raise ValueError("narrative settlement exceeds 1000000 canonical characters")
        membership = self.access.require_campaign(campaign_id, principal_id)
        role = membership.role
        if role == "observer":
            raise PermissionError("observer role is read-only")
        replay = self.idempotency.lookup(scope, key, request)
        if replay is not None and replay.response is not None:
            return deepcopy(replay.response)
        with self.database.transaction() as session:
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                raise LookupError(campaign_id)
            branch = resolve_branch(session, campaign)
            if branch.id != expected_branch_id:
                raise ValueError("campaign branch conflict")
            if campaign.revision != int(expected_revision):
                raise ValueError("campaign revision conflict")
            replay_tx = self.idempotency.lookup_in_session(session, scope, key, request)
            if replay_tx is not None and replay_tx.response is not None:
                return deepcopy(replay_tx.response)
            document = narrative_document(campaign.state)
            actor_bindings = dict(document.get("actor_bindings") or {})
            self._validate_settlement_knowledge_audience(
                session,
                campaign_id=campaign_id,
                branch_id=branch.id,
                settlement_audience=str(event.get("audience_scope") or "public"),
                actor_knowledge=actor_knowledge or [],
            )
            if not self._has_facilitator_authority(document, role):
                for participant in event.get("participants") or []:
                    actor_ref = required_text(
                        participant.get("actor_id"), "event participant actor_id", limit=200
                    )
                    if not self._actor_authorized(
                        document,
                        campaign_id=campaign_id,
                        actor_id=actor_bindings.get(actor_ref, actor_ref),
                        principal_id=principal_id,
                        role=role,
                        control=True,
                    ):
                        raise PermissionError(
                            f"principal does not control event participant: {actor_ref}"
                        )
            open_conversations = [
                item
                for item in document.get("npc_conversations", {}).values()
                if item.get("status") == "open"
            ]
            closed_conversation = None
            accepted_proposals: list[dict[str, Any]] = []
            if open_conversations and not _close_conversation_id:
                raise ValueError(
                    "close or abort every NPC conversation before authoritative settlement"
                )
            if _close_conversation_id:
                conversation = document["npc_conversations"].get(_close_conversation_id)
                if conversation is None or conversation.get("status") != "open":
                    raise ValueError("NPC conversation is not open")
                if conversation.get("owner_principal_id") != principal_id:
                    raise PermissionError("NPC conversation belongs to another principal")
                if int(conversation["context_revision"]) != int(expected_revision):
                    raise ValueError("NPC conversation context is stale")
                close_message = f"close:{conversation['id']}:{principal_id}"
                expected_worker = hmac.new(
                    bytes.fromhex(str(conversation["runtime_secret"])),
                    close_message.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                if not secrets.compare_digest(expected_worker, str(_private_worker_id or "")):
                    raise PermissionError("NPC conversation close capability mismatch")
                proposal_by_id = {
                    str(item["id"]): item for item in conversation.get("proposals", [])
                }
                selected_ids = list(dict.fromkeys(_selected_proposal_ids or []))
                missing_ids = [item for item in selected_ids if item not in proposal_by_id]
                if missing_ids:
                    raise LookupError("unknown selected NPC proposal: " + ", ".join(missing_ids))
                if any(not proposal_by_id[item].get("published") for item in selected_ids):
                    raise ValueError(
                        "selected NPC proposals must be published before settlement close"
                    )
                accepted_proposals = [deepcopy(proposal_by_id[item]) for item in selected_ids]
                conversation["status"] = "closed"
                conversation["proposals"] = []
                conversation["accepted_proposal_ids"] = selected_ids
                conversation["private_context"] = {}
                conversation["runtime_secret"] = secrets.token_hex(32)
                for activation in conversation.get("activations") or []:
                    activation["status"] = "settled"
                    activation["lease"] = None
                conversation["context_revision"] = int(expected_revision) + 1
                closed_conversation = conversation
            changed_records = []
            for change in record_changes or []:
                action = str(change.get("action") or "update")
                normalized = validate_record(dict(change.get("record") or {}))
                self._validate_record_profile(active_profile(document), normalized)
                current = document["records"].get(normalized["id"])
                if action == "create":
                    if current is not None:
                        raise ValueError(f"record exists: {normalized['id']}")
                    normalized["revision"] = 1
                elif action == "update":
                    if current is None:
                        raise LookupError(normalized["id"])
                    if int(current["revision"]) != int(change.get("expected_revision", -1)):
                        raise ValueError(f"record revision conflict: {normalized['id']}")
                    if not self._record_authorized(
                        document,
                        campaign_id=campaign_id,
                        principal_id=principal_id,
                        role=role,
                        record=current,
                        control=True,
                    ):
                        raise PermissionError(
                            f"principal does not control record: {normalized['id']}"
                        )
                    normalized["revision"] = int(current["revision"]) + 1
                else:
                    raise ValueError("settlement record action must be create or update")
                if (
                    action == "create"
                    and not self._has_facilitator_authority(document, role)
                    and not self._record_authorized(
                        document,
                        campaign_id=campaign_id,
                        principal_id=principal_id,
                        role=role,
                        record=normalized,
                        control=True,
                    )
                ):
                    raise PermissionError(f"principal cannot create record: {normalized['id']}")
                document["records"][normalized["id"]] = normalized
                changed_records.append(normalized)
            has_authorized_fact = False
            if facts and not self._has_facilitator_authority(document, role):
                for fact in facts:
                    subject_ref = str(fact.get("subject_ref") or "")
                    if str(fact.get("action") or "upsert") == "revise":
                        memory_id = required_text(
                            fact.get("memory_id"), "fact memory_id", limit=200
                        )
                        persisted = session.get(CampaignMemory, memory_id)
                        if persisted is None or persisted.campaign_id != campaign_id:
                            raise LookupError(memory_id)
                        if subject_ref and subject_ref != str(persisted.subject_ref or ""):
                            raise PermissionError(
                                "fact revision subject does not match its persisted target"
                            )
                        subject_ref = str(persisted.subject_ref or "")
                    subject_record = document["records"].get(subject_ref)
                    if subject_record is not None and self._record_authorized(
                        document,
                        campaign_id=campaign_id,
                        principal_id=principal_id,
                        role=role,
                        record=subject_record,
                        control=True,
                    ):
                        has_authorized_fact = True
                        continue
                    actor_id = actor_bindings.get(subject_ref, subject_ref)
                    try:
                        if not self._actor_authorized(
                            document,
                            campaign_id=campaign_id,
                            actor_id=actor_id,
                            principal_id=principal_id,
                            role=role,
                            control=True,
                        ):
                            raise PermissionError(actor_id)
                        has_authorized_fact = True
                    except (LookupError, PermissionError) as error:
                        if self._record_authorized(
                            document,
                            campaign_id=campaign_id,
                            principal_id=principal_id,
                            role=role,
                            record={"id": subject_ref, "controller": {"element_ref": subject_ref}},
                            control=True,
                        ):
                            has_authorized_fact = True
                            continue
                        raise PermissionError(
                            "objective fact subject requires facilitator or active "
                            "steward authority"
                        ) from error
            for item in actor_knowledge or []:
                actor_ref = str(item.get("actor_id") or "")
                actor_id = document.get("actor_bindings", {}).get(actor_ref, actor_ref)
                if str(item.get("action") or "add") == "revise":
                    knowledge_id = required_text(
                        item.get("knowledge_id"), "knowledge_id", limit=200
                    )
                    persisted = session.get(ActorKnowledge, knowledge_id)
                    if persisted is None or persisted.campaign_id != campaign_id:
                        raise LookupError(knowledge_id)
                    if actor_id != persisted.actor_id:
                        raise PermissionError(
                            "actor-knowledge revision actor does not match its persisted target"
                        )
                    actor_id = persisted.actor_id
                if not self._actor_authorized(
                    document,
                    campaign_id=campaign_id,
                    actor_id=actor_id,
                    principal_id=principal_id,
                    role=role,
                    control=True,
                ):
                    raise PermissionError(
                        f"principal does not control actor knowledge target: {actor_ref}"
                    )
            before = {"state": deepcopy(campaign.state), "revision": campaign.revision}
            if (
                not self._has_facilitator_authority(document, role)
                and not changed_records
                and not actor_knowledge
                and not has_authorized_fact
            ):
                raise PermissionError(
                    "non-facilitator settlement requires an authorized record or actor delta"
                )
            next_state = state_with_narrative(campaign.state, document)
            changed = session.execute(
                update(Campaign)
                .where(
                    Campaign.id == campaign_id,
                    Campaign.revision == int(expected_revision),
                    Campaign.active_branch_id == expected_branch_id,
                )
                .values(state=next_state, revision=Campaign.revision + 1)
                .returning(Campaign.revision)
            ).scalar_one_or_none()
            if changed is None:
                raise ValueError("campaign revision conflict during conditional settlement")
            session.expire(campaign)
            session.refresh(campaign)
            participants = deepcopy(list(event.get("participants") or []))
            for participant in participants:
                participant["actor_id"] = actor_bindings.get(
                    participant.get("actor_id"), participant.get("actor_id")
                )
            event_info = self.events._add_in_session(
                session,
                campaign,
                branch.id,
                event_type=str(event.get("event_type") or "narrative"),
                summary=required_text(event.get("summary"), "event.summary", limit=1000),
                retrieval_text=(
                    str(event["retrieval_text"]).strip()
                    if event.get("retrieval_text") is not None
                    else None
                ),
                payload=deepcopy(dict(event.get("payload") or {})),
                audience_scope=str(event.get("audience_scope") or "public"),
                participants=participants,
            )
            fact_results = [
                self.continuity_commits._apply_fact(
                    session, campaign, branch.id, event_info.id, deepcopy(item)
                )
                for item in facts or []
            ]
            normalized_knowledge = deepcopy(actor_knowledge or [])
            for item in normalized_knowledge:
                item["actor_id"] = actor_bindings.get(item.get("actor_id"), item.get("actor_id"))
            knowledge_results = [
                self.continuity_commits._apply_knowledge(
                    session,
                    campaign,
                    branch.id,
                    branch.head_snapshot_id,
                    event_info.id,
                    deepcopy(item),
                )
                for item in normalized_knowledge
            ]
            revision_rows = self.revisions.record_group_in_session(
                session,
                campaign_id,
                operation="narrative.settle",
                actor=principal_id,
                branch_id=branch.id,
                idempotency_key=key,
                request_hash=request_hash(request),
                reversible=False,
                changes=[
                    {
                        "entity_type": "campaign",
                        "entity_id": campaign_id,
                        "before": before,
                        "after": {"state": next_state, "revision": int(changed)},
                    }
                ],
            )
            snapshot_info = None
            if snapshot is not None:
                recap = deepcopy(snapshot.get("recap"))
                if isinstance(recap, dict) and "evidence_event_ids" not in recap:
                    recap["evidence_event_ids"] = [event_info.id]
                snapshot_info = self.snapshots._create_in_session(
                    session,
                    campaign,
                    label=str(snapshot.get("label") or "Narrative settlement"),
                    recap=recap,
                )
            response = {
                "campaign_id": campaign_id,
                "campaign_revision": campaign.revision,
                "branch_id": branch.id,
                "phase": document["phase"],
                "event": asdict(event_info),
                "records": changed_records,
                "facts": [asdict(item) for item in fact_results],
                "actor_knowledge": [asdict(item) for item in knowledge_results],
                "snapshot": asdict(snapshot_info) if snapshot_info else None,
                "npc_conversation": (
                    {
                        "id": closed_conversation["id"],
                        "status": "closed",
                        "accepted_proposal_ids": [item["id"] for item in accepted_proposals],
                        "publications": deepcopy(closed_conversation.get("publications", [])),
                    }
                    if closed_conversation
                    else None
                ),
            }
            self.idempotency.remember_in_session(
                session,
                scope,
                key,
                request,
                response,
                campaign_id=campaign_id,
                mutation_group_id=revision_rows[0].mutation_group_id,
            )
            return response

    def npc_conversation(
        self,
        campaign_id: str,
        *,
        action: str,
        conversation_id: str | None = None,
        npc_actor_id: str | None = None,
        data: dict[str, Any] | None = None,
        **common: Any,
    ) -> dict[str, Any]:
        payload = deepcopy(dict(data or {}))
        if len(canonical_json(payload)) > 1_000_000:
            raise ValueError("NPC conversation data exceeds 1000000 canonical characters")

        def bounded_list(value: Any, field: str, maximum: int) -> list[Any]:
            if value is None:
                return []
            if not isinstance(value, list) or len(value) > maximum:
                raise ValueError(f"{field} must be an array with at most {maximum} entries")
            return list(value)

        def bounded_strings(
            value: Any, field: str, maximum: int, *, item_limit: int = 300
        ) -> list[str]:
            return [
                required_text(item, f"{field}[]", limit=item_limit)
                for item in bounded_list(value, field, maximum)
            ]

        if action != "open":
            self._require_live_npc_conversation_access(
                campaign_id,
                conversation_id=conversation_id,
                principal_id=str(common["principal_id"]),
                expected_branch_id=str(common["expected_branch_id"]),
            )
        if action == "close" and (data or {}).get("settlement") is not None:
            current_profile = active_profile(
                narrative_document(self.campaigns.get(campaign_id).state)
            )
            if not current_profile or "npc_conversation" not in set(
                current_profile.get("capabilities") or []
            ):
                raise ValueError("active profile does not provide NPC conversation")
            raw_settlement = payload.get("settlement")
            if not isinstance(raw_settlement, Mapping):
                raise ValueError("NPC conversation settlement must be an object")
            settlement = deepcopy(dict(raw_settlement))
            raw_event = settlement.get("event")
            if not isinstance(raw_event, Mapping):
                raise ValueError("NPC conversation settlement.event must be an object")
            return self.narrative_settle(
                campaign_id,
                principal_id=common["principal_id"],
                expected_revision=common["expected_revision"],
                expected_branch_id=common["expected_branch_id"],
                idempotency_key=common["idempotency_key"],
                event=deepcopy(dict(raw_event)),
                record_changes=deepcopy(
                    bounded_list(settlement.get("record_changes"), "settlement.record_changes", 128)
                ),
                facts=deepcopy(bounded_list(settlement.get("facts"), "settlement.facts", 128)),
                actor_knowledge=deepcopy(
                    bounded_list(
                        settlement.get("actor_knowledge"), "settlement.actor_knowledge", 128
                    )
                ),
                snapshot=deepcopy(settlement.get("snapshot")),
                _close_conversation_id=required_text(conversation_id, "conversation_id", limit=100),
                _selected_proposal_ids=bounded_strings(
                    payload.get("selected_proposal_ids"),
                    "selected_proposal_ids",
                    256,
                    item_limit=100,
                ),
                _private_worker_id=str(payload.get("close_token") or ""),
            )

        prepared_context: dict[str, Any] | None = None
        prepared_actor_id = ""
        prepared_interlocutors: dict[str, Any] | None = None
        if action == "open":
            prepared_actor_id = self.resolve_actor_ref(
                campaign_id, required_text(npc_actor_id, "npc_actor_id", limit=100)
            )
            character = self.characters.get(prepared_actor_id)
            if character.character_type not in {"npc", "persistent_npc"}:
                raise ValueError("isolated conversation workers are NPC-only")
            current_document = narrative_document(self.campaigns.get(campaign_id).state)
            membership = self.access.require_campaign(campaign_id, str(common["principal_id"]))
            if not self._actor_authorized(
                current_document,
                campaign_id=campaign_id,
                actor_id=prepared_actor_id,
                principal_id=str(common["principal_id"]),
                role=membership.role,
                control=True,
                private=True,
            ):
                raise PermissionError(
                    "NPC conversation requires actor control and private access"
                )
            raw_interlocutors = payload.get("interlocutors")
            if not isinstance(raw_interlocutors, Mapping):
                raise ValueError("NPC conversation requires declared interlocutors")
            interlocutors = dict(raw_interlocutors)
            unknown_interlocutor_fields = set(interlocutors) - {
                "actor_ids",
                "principal_ids",
                "publication_scopes",
            }
            if unknown_interlocutor_fields:
                raise ValueError("NPC conversation interlocutors have unsupported fields")
            actor_ids = [
                self.resolve_actor_ref(campaign_id, str(item))
                for item in bounded_strings(
                    interlocutors.get("actor_ids"), "interlocutors.actor_ids", 64
                )
            ]
            if prepared_actor_id in actor_ids:
                raise ValueError("the NPC worker cannot be its own interlocutor")
            for actor_id in actor_ids:
                self.characters.get(actor_id)
            principal_ids = bounded_strings(
                interlocutors.get("principal_ids"), "interlocutors.principal_ids", 64
            )
            for principal_id in principal_ids:
                self.access.require_campaign(campaign_id, principal_id)
            publication_scopes = bounded_strings(
                interlocutors.get("publication_scopes"),
                "interlocutors.publication_scopes",
                6,
                item_limit=64,
            )
            if not actor_ids and not principal_ids:
                raise ValueError("NPC conversation requires at least one interlocutor")
            if not publication_scopes or len(publication_scopes) != len(
                set(publication_scopes)
            ):
                raise ValueError("NPC conversation requires unique publication_scopes")
            prepared_interlocutors = {
                "actor_ids": list(dict.fromkeys(actor_ids)),
                "principal_ids": list(dict.fromkeys(principal_ids)),
                "publication_scopes": publication_scopes,
            }
            prepared_context = self.actor_memory_context(
                campaign_id,
                actor_id=prepared_actor_id,
                principal_id=str(common["principal_id"]),
                query=(
                    required_text(payload.get("query"), "query", limit=2_000)
                    if payload.get("query")
                    else ""
                ),
                branch_id=str(common["expected_branch_id"]),
                current_refs=bounded_strings(
                    payload.get("current_refs"), "current_refs", 128
                ),
            )
        elif action == "refresh":
            identifier = required_text(conversation_id, "conversation_id", limit=100)
            current_document = narrative_document(self.campaigns.get(campaign_id).state)
            current_conversation = dict(
                current_document.get("npc_conversations", {}).get(identifier) or {}
            )
            if not current_conversation:
                raise LookupError(identifier)
            prepared_actor_id = self.resolve_actor_ref(
                campaign_id, str(current_conversation["npc_actor_id"])
            )
            refresh_query = payload.get("query")
            if refresh_query is None:
                refresh_query = current_conversation.get("query")
            normalized_refresh_query = (
                required_text(refresh_query, "query", limit=2_000)
                if str(refresh_query or "").strip()
                else ""
            )
            prepared_context = self.actor_memory_context(
                campaign_id,
                actor_id=prepared_actor_id,
                principal_id=str(common["principal_id"]),
                query=normalized_refresh_query,
                branch_id=str(common["expected_branch_id"]),
                current_refs=bounded_strings(
                    payload.get("current_refs"), "current_refs", 128
                ),
            )

        def sign(value: Mapping[str, Any], message: str) -> str:
            secret = bytes.fromhex(str(value["runtime_secret"]))
            return hmac.new(secret, message.encode("utf-8"), hashlib.sha256).hexdigest()

        def activation_ref(value: Mapping[str, Any], activation: Mapping[str, Any]) -> str:
            return sign(
                value,
                ":".join(
                    (
                        str(value["id"]),
                        str(activation["id"]),
                        str(activation["actor_runtime_id"]),
                        str(value["owner_principal_id"]),
                    )
                ),
            )

        def find_activation(value: Mapping[str, Any], supplied_ref: str) -> dict[str, Any]:
            for activation in value.get("activations") or []:
                if secrets.compare_digest(
                    activation_ref(value, activation), str(supplied_ref or "")
                ):
                    return activation
            raise PermissionError("invalid actor-scoped activation_ref")

        def public_activation(
            value: Mapping[str, Any], activation: Mapping[str, Any]
        ) -> dict[str, Any]:
            result = {
                "activation_ref": activation_ref(value, activation),
                "actor_id": str(value["npc_actor_id"]),
                "reason": str(activation["reason"]),
                "from_cursor": int(activation["from_cursor"]),
                "to_cursor": int(activation["to_cursor"]),
                "status": str(activation["status"]),
            }
            replaced_id = str(activation.get("replacement_for") or "")
            replaced = next(
                (item for item in value.get("activations") or [] if item["id"] == replaced_id),
                None,
            )
            if replaced is not None:
                result["replacement_for"] = activation_ref(value, replaced)
            return result

        def public_conversation(value: Mapping[str, Any]) -> dict[str, Any]:
            return {
                "id": str(value["id"]),
                "npc_actor_id": str(value["npc_actor_id"]),
                "status": str(value["status"]),
                "context_revision": int(value["context_revision"]),
                "interlocutors": deepcopy(value["interlocutors"]),
                "worker_contract": {
                    "execution": "persistent_actor_worker",
                    "tools_exposed": False,
                    "persist_worker_session": True,
                    "authoritative_result": False,
                    "output_contract": "character-conversation-proposal.v1",
                },
                "activations": [
                    public_activation(value, item)
                    for item in value.get("activations") or []
                    if item.get("status") in {"pending", "claimed"}
                ],
                "publications": deepcopy(value.get("publications") or []),
            }

        def context_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
            context = dict(value["private_context"])
            receipt = {
                "schema_version": 1,
                "conversation_id": str(value["id"]),
                "actor_runtime_id": str(value["actor_runtime_id"]),
                "campaign_id": campaign_id,
                "branch_id": str(context["branch_id"]),
                "actor_id": str(context["actor_id"]),
                "context_digest": hashlib.sha256(
                    canonical_json(context).encode("utf-8")
                ).hexdigest(),
            }
            receipt["signature"] = sign(value, canonical_json(receipt))
            return receipt

        def normalize_proposal(
            value: Mapping[str, Any], activation: Mapping[str, Any], raw: Any
        ) -> dict[str, Any]:
            if not isinstance(raw, Mapping):
                raise ValueError("proposal must be an object")
            proposal = deepcopy(dict(raw))
            if proposal.get("schema_version") != 1:
                raise ValueError("conversation proposal schema_version must be 1")
            if str(proposal.get("activation_id") or "") != str(activation["id"]):
                raise ValueError("proposal activation does not match its lease")
            if str(proposal.get("actor_runtime_id") or "") != str(
                activation["actor_runtime_id"]
            ):
                raise ValueError("proposal actor runtime does not match its activation")
            segments = bounded_list(
                proposal.get("utterance_segments"), "utterance_segments", 32
            )
            allowed_refs = set(
                dict(value["private_context"])["constraints"]["allowed_basis_refs"]
            )
            interlocutors = dict(value["interlocutors"])
            allowed_targets = set(interlocutors["actor_ids"]) | set(
                interlocutors["principal_ids"]
            )
            normalized_segments = []
            for index, raw_segment in enumerate(segments):
                if not isinstance(raw_segment, Mapping):
                    raise ValueError(f"utterance_segments[{index}] must be an object")
                segment = dict(raw_segment)
                mode = str(segment.get("content_mode") or "")
                if mode not in {"nonfactual", "grounded", "deception", "uncertain"}:
                    raise ValueError("utterance content_mode is unsupported")
                refs = bounded_strings(
                    segment.get("basis_refs"),
                    f"utterance_segments[{index}].basis_refs",
                    128,
                )
                targets = bounded_strings(
                    segment.get("targets"),
                    f"utterance_segments[{index}].targets",
                    64,
                )
                if unknown := sorted(set(targets) - allowed_targets):
                    raise PermissionError(
                        f"utterance targets undeclared interlocutors: {unknown}"
                    )
                if len(refs) != len(set(refs)):
                    raise ValueError("utterance basis_refs must not contain duplicates")
                if mode != "nonfactual" and not refs:
                    raise ValueError(f"content_mode={mode} requires actor-owned basis_refs")
                if unknown := sorted(set(refs) - allowed_refs):
                    raise ValueError(f"utterance cites refs outside its actor context: {unknown}")
                normalized_segments.append(
                    {
                        "text": required_text(
                            segment.get("text"), f"utterance_segments[{index}].text", limit=4_000
                        ),
                        "content_mode": mode,
                        "basis_refs": refs,
                        "targets": targets,
                        "language": (
                            required_text(
                                segment.get("language"),
                                f"utterance_segments[{index}].language",
                                limit=100,
                            )
                            if segment.get("language")
                            else ""
                        ),
                        "delivery": (
                            required_text(
                                segment.get("delivery"),
                                f"utterance_segments[{index}].delivery",
                                limit=300,
                            )
                            if segment.get("delivery")
                            else ""
                        ),
                    }
                )
            memory_candidates = []
            for index, item in enumerate(
                bounded_list(
                    proposal.get("memory_candidates"), "proposal.memory_candidates", 32
                )
            ):
                if not isinstance(item, Mapping):
                    raise ValueError(f"proposal.memory_candidates[{index}] must be an object")
                if len(canonical_json(item)) > 4_000:
                    raise ValueError(
                        f"proposal.memory_candidates[{index}] exceeds 4000 characters"
                    )
                memory_candidates.append(deepcopy(dict(item)))
            return {
                "schema_version": 1,
                "activation_id": str(activation["id"]),
                "actor_runtime_id": str(activation["actor_runtime_id"]),
                "private_intent": required_text(
                    proposal.get("private_intent") or "No private intent declared.",
                    "proposal.private_intent",
                    limit=2_000,
                ),
                "utterance_segments": normalized_segments,
                "visible_cues": bounded_strings(
                    proposal.get("visible_cues"), "proposal.visible_cues", 32, item_limit=1_000
                ),
                "memory_candidates": memory_candidates,
            }

        def mutate(document: dict[str, Any]) -> dict[str, Any]:
            conversations = document["npc_conversations"]
            profile = active_profile(document)
            if not profile or "npc_conversation" not in set(profile.get("capabilities") or []):
                raise ValueError("active profile does not provide NPC conversation")
            if action == "open":
                if document["phase"] != PHASE_PLAY or document.get("conflict"):
                    raise ValueError("NPC conversation requires non-conflict Play")
                actor_id = required_text(npc_actor_id, "npc_actor_id", limit=100)
                core_actor_id = prepared_actor_id
                membership = self.access.require_campaign(campaign_id, common["principal_id"])
                if not self._actor_authorized(
                    document,
                    campaign_id=campaign_id,
                    actor_id=core_actor_id,
                    principal_id=common["principal_id"],
                    role=membership.role,
                    control=True,
                ):
                    raise PermissionError("NPC conversation requires actor control")
                identifier = f"npc_{uuid4().hex}"
                worker_id = f"worker_{uuid4().hex}"
                activation_id = f"activation_{uuid4().hex}"
                actor_runtime_id = f"{identifier}:{core_actor_id}:r1"
                conversations[identifier] = {
                    "id": identifier,
                    "npc_actor_id": actor_id,
                    "status": "open",
                    "context_revision": int(common["expected_revision"]) + 1,
                    "worker_id": worker_id,
                    "actor_runtime_id": actor_runtime_id,
                    "runtime_secret": secrets.token_hex(32),
                    "owner_principal_id": common["principal_id"],
                    "interlocutors": deepcopy(prepared_interlocutors),
                    "query": (
                        required_text(payload.get("query"), "query", limit=2_000)
                        if payload.get("query")
                        else ""
                    ),
                    "private_context": deepcopy(prepared_context),
                    "activations": [
                        {
                            "id": activation_id,
                            "actor_runtime_id": actor_runtime_id,
                            "reason": required_text(
                                payload.get("reason") or "conversation opened",
                                "reason",
                                limit=1_000,
                            ),
                            "from_cursor": 0,
                            "to_cursor": 0,
                            "status": "pending",
                            "lease": None,
                        }
                    ],
                    "proposals": [],
                    "publications": [],
                }
                value = conversations[identifier]
                return {
                    "conversation": public_conversation(value),
                    "activation": public_activation(value, value["activations"][0]),
                    "close_token": sign(value, f"close:{identifier}:{common['principal_id']}"),
                }
            identifier = required_text(conversation_id, "conversation_id", limit=100)
            value = conversations.get(identifier)
            if value is None or value["status"] != "open":
                raise ValueError("NPC conversation is not open")
            if value.get("owner_principal_id") != common["principal_id"]:
                raise PermissionError("NPC conversation belongs to another principal")
            if action != "abort" and int(value["context_revision"]) != int(
                common["expected_revision"]
            ):
                raise ValueError("NPC conversation context is stale")
            supplied_close = str(payload.get("close_token") or "")
            expected_close = sign(value, f"close:{identifier}:{common['principal_id']}")
            if action in {"close", "abort"} and not secrets.compare_digest(
                expected_close, supplied_close
            ):
                raise PermissionError("NPC conversation close capability mismatch")
            if action == "claim":
                activation = find_activation(value, str(payload.get("activation_ref") or ""))
                if activation["status"] == "invalidated":
                    raise ValueError("activation is invalidated; use its replacement")
                if activation["status"] != "pending":
                    raise ValueError("activation is not pending")
                lease_id = f"lease_{uuid4().hex}"
                activation["lease"] = {
                    "id": lease_id,
                    "expires_at_ns": time.time_ns() + 300_000_000_000,
                    "context_digest": context_receipt(value)["context_digest"],
                }
                activation["status"] = "claimed"
                value["context_revision"] = int(common["expected_revision"]) + 1
                return {
                    "schema_version": 1,
                    "conversation_id": identifier,
                    "activation_id": activation["id"],
                    "activation_ref": activation_ref(value, activation),
                    "actor_runtime_id": activation["actor_runtime_id"],
                    "worker_id": value["worker_id"],
                    "lease_id": lease_id,
                    "lease_expires_at_ns": activation["lease"]["expires_at_ns"],
                    "context_receipt": context_receipt(value),
                    "actor_memory": deepcopy(value["private_context"]["memory"]),
                    "constraints": {
                        "allowed_basis_refs": deepcopy(
                            value["private_context"]["constraints"]["allowed_basis_refs"]
                        ),
                        "may_call_tools": False,
                        "may_roll_random": False,
                        "may_write_state": False,
                        "output_contract": "character-conversation-proposal.v1",
                    },
                }
            if action == "refresh":
                if len(value.get("activations") or []) >= 64:
                    raise ValueError("NPC conversation activation history is full")
                old = next(
                    (
                        item
                        for item in reversed(value["activations"])
                        if item["status"] in {"pending", "claimed"}
                    ),
                    None,
                )
                if old is None:
                    raise ValueError("conversation has no activation to refresh")
                old["status"] = "invalidated"
                old["lease"] = None
                value["private_context"] = deepcopy(prepared_context)
                value["actor_runtime_id"] = (
                    f"{identifier}:{prepared_actor_id}:c{int(common['expected_revision']) + 1}"
                )
                replacement = {
                    "id": f"activation_{uuid4().hex}",
                    "actor_runtime_id": value["actor_runtime_id"],
                    "reason": old["reason"],
                    "from_cursor": old["from_cursor"],
                    "to_cursor": old["to_cursor"],
                    "status": "pending",
                    "lease": None,
                    "replacement_for": old["id"],
                }
                value["activations"].append(replacement)
                value["context_revision"] = int(common["expected_revision"]) + 1
                return {
                    "conversation_id": identifier,
                    "activation": public_activation(value, replacement),
                }
            if action == "propose":
                if len(value.get("proposals") or []) >= 256:
                    raise ValueError("NPC conversation proposal journal is full")
                activation = find_activation(value, str(payload.get("activation_ref") or ""))
                lease = dict(activation.get("lease") or {})
                if lease.get("id") != str(payload.get("lease_id") or ""):
                    raise PermissionError("invalid conversation activation lease")
                if int(lease.get("expires_at_ns") or 0) <= time.time_ns():
                    raise ValueError("conversation activation lease expired")
                supplied_receipt = payload.get("context_receipt")
                if not isinstance(supplied_receipt, Mapping):
                    raise ValueError("signed conversation context_receipt is required")
                receipt = dict(supplied_receipt)
                receipt_signature = str(receipt.pop("signature", ""))
                if not receipt_signature or not secrets.compare_digest(
                    receipt_signature, sign(value, canonical_json(receipt))
                ):
                    raise PermissionError("conversation context receipt is invalid")
                expected_receipt = context_receipt(value)
                expected_receipt.pop("signature")
                if receipt != expected_receipt or receipt.get("context_digest") != lease.get(
                    "context_digest"
                ):
                    raise ValueError("conversation context receipt is stale or out of scope")
                normalized = normalize_proposal(value, activation, payload.get("proposal"))
                proposal = {
                    "id": f"proposal_{uuid4().hex}",
                    **normalized,
                    "private": True,
                    "published": False,
                }
                value["proposals"].append(proposal)
                activation["to_cursor"] = int(activation["to_cursor"]) + 1
                value["context_revision"] = int(common["expected_revision"]) + 1
                return {
                    "conversation_id": identifier,
                    "proposal_id": proposal["id"],
                    "activation": public_activation(value, activation),
                }
            if action == "publish":
                proposal_id = required_text(payload.get("proposal_id"), "proposal_id", limit=100)
                proposal = next(
                    (item for item in value["proposals"] if item["id"] == proposal_id), None
                )
                if proposal is None:
                    raise LookupError(proposal_id)
                if proposal.get("published"):
                    raise ValueError("NPC proposal is already published")
                publication = {
                    "proposal_id": proposal_id,
                    "content": " ".join(
                        item["text"] for item in proposal["utterance_segments"]
                    ).strip(),
                    "utterance_segments": [
                        {
                            key: deepcopy(segment[key])
                            for key in ("text", "targets", "language", "delivery")
                        }
                        for segment in proposal["utterance_segments"]
                    ],
                    "visible_cues": deepcopy(proposal["visible_cues"]),
                    "audience": validate_audience(
                        payload.get("audience"), field="publication.audience"
                    ),
                }
                interlocutors = dict(value["interlocutors"])
                audience = publication["audience"]
                if audience["scope"] not in set(interlocutors["publication_scopes"]):
                    raise PermissionError(
                        "publication scope was not declared for this conversation"
                    )
                allowed_actor_ids = set(interlocutors["actor_ids"])
                allowed_principal_ids = set(interlocutors["principal_ids"])
                targeted_actor_ids = set(audience.get("actor_ids") or [])
                if audience.get("actor_id"):
                    targeted_actor_ids.add(str(audience["actor_id"]))
                targeted_principal_ids = set(audience.get("principal_ids") or [])
                if audience.get("principal_id"):
                    targeted_principal_ids.add(str(audience["principal_id"]))
                if targeted_actor_ids - allowed_actor_ids or (
                    targeted_principal_ids - allowed_principal_ids
                ):
                    raise PermissionError("publication targets undeclared interlocutors")
                segment_targets = {
                    str(target)
                    for segment in proposal["utterance_segments"]
                    for target in segment.get("targets") or []
                }
                publication_targets = targeted_actor_ids | targeted_principal_ids
                if segment_targets and audience["scope"] in {"public", "table"}:
                    raise PermissionError(
                        "targeted utterance segments cannot be broadcast to table or public"
                    )
                if segment_targets - publication_targets:
                    raise PermissionError(
                        "publication audience does not cover every utterance segment target"
                    )
                if audience["scope"] == "private_worker":
                    raise PermissionError("private worker output cannot be published")
                allowed_audiences = set(
                    dict(profile.get("authority") or {}).get("audience_scopes") or []
                )
                if allowed_audiences and publication["audience"]["scope"] not in allowed_audiences:
                    raise ValueError("publication audience is not allowed by the active profile")
                value["publications"].append(publication)
                proposal["published"] = True
                value["context_revision"] = int(common["expected_revision"]) + 1
                return {"conversation_id": identifier, "publication": publication}
            if action in {"close", "abort"}:
                selected_ids = list(
                    dict.fromkeys(
                        bounded_strings(
                            payload.get("selected_proposal_ids"),
                            "selected_proposal_ids",
                            256,
                            item_limit=100,
                        )
                    )
                )
                proposal_by_id = {str(item["id"]): item for item in value.get("proposals", [])}
                if action == "abort" and selected_ids:
                    raise ValueError("aborted NPC conversation cannot accept proposals")
                missing_ids = [item for item in selected_ids if item not in proposal_by_id]
                if missing_ids:
                    raise LookupError("unknown selected NPC proposal: " + ", ".join(missing_ids))
                if action == "close" and any(
                    not proposal_by_id[item].get("published") for item in selected_ids
                ):
                    raise ValueError("selected NPC proposals must be published before close")
                value["status"] = "closed" if action == "close" else "aborted"
                value["accepted_proposal_ids"] = selected_ids if action == "close" else []
                value["proposals"] = []
                value["private_context"] = {}
                value["runtime_secret"] = secrets.token_hex(32)
                value["context_revision"] = int(common["expected_revision"]) + 1
                return {
                    "conversation_id": identifier,
                    "status": value["status"],
                    "publications": deepcopy(value["publications"]),
                }
            raise ValueError(f"unsupported NPC conversation action: {action}")

        return self._write(
            campaign_id,
            operation=f"npc.{action}",
            payload={
                "action": action,
                "conversation_id": conversation_id,
                "npc_actor_id": npc_actor_id,
                "data": data,
            },
            mutate=mutate,
            **common,
        )

    def activity_settle(
        self,
        campaign_id: str,
        *,
        activity: str,
        summary: str,
        changes: list[dict[str, Any]],
        facts: list[dict[str, Any]] | None = None,
        actor_knowledge: list[dict[str, Any]] | None = None,
        snapshot: dict[str, Any] | None = None,
        audience_scope: str = "public",
        participants: list[dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
        **common: Any,
    ) -> dict[str, Any]:
        if activity not in {"downtime", "world_turn"}:
            raise ValueError("unsupported activity")
        document = narrative_document(self.campaigns.get(campaign_id).state)
        profile = active_profile(document)
        required_capability = "downtime" if activity == "downtime" else "world_turn"
        if document["phase"] != PHASE_PLAY:
            raise ValueError(f"{activity} requires play phase")
        if not profile or required_capability not in set(profile.get("capabilities") or []):
            raise ValueError(f"active profile does not provide {activity}")
        if activity == "world_turn":
            membership = self.access.require_campaign(campaign_id, common["principal_id"])
            if (
                profile
                and dict(profile.get("authority") or {}).get(
                    "world_turn_requires_facilitator", False
                )
                and not self._has_facilitator_authority(
                    document,
                    membership.role,
                )
            ):
                raise PermissionError("world turn requires facilitator authority")
        return self.narrative_settle(
            campaign_id,
            event={
                "event_type": activity,
                "summary": summary,
                "audience_scope": audience_scope,
                "participants": deepcopy(participants or []),
                "payload": deepcopy(payload or {}),
            },
            record_changes=changes,
            facts=facts,
            actor_knowledge=actor_knowledge,
            snapshot=snapshot,
            principal_id=common["principal_id"],
            expected_revision=common["expected_revision"],
            expected_branch_id=common["expected_branch_id"],
            idempotency_key=common["idempotency_key"],
        )

    def conflict(
        self, campaign_id: str, *, action: str, data: dict[str, Any] | None = None, **common: Any
    ) -> dict[str, Any]:
        membership = self.access.require_campaign(campaign_id, common["principal_id"])
        current_document = narrative_document(self.campaigns.get(campaign_id).state)
        profile = active_profile(current_document)
        authority = dict(profile.get("authority") or {}) if profile else {}
        facilitator = self._has_facilitator_authority(current_document, membership.role)
        actor_authority = bool(authority.get("player_controls_owned_actor")) and (
            self.principal_controls_actor(campaign_id, common["principal_id"])
        )
        current_conflict = current_document.get("conflict")
        owns_conflict = bool(
            current_conflict
            and current_conflict.get("controller_principal_id") == common["principal_id"]
        )
        if action == "start":
            if not (facilitator or actor_authority):
                raise PermissionError("conflict start requires profile authority")
        elif not (facilitator or owns_conflict):
            raise PermissionError("conflict lifecycle belongs to another principal")

        def mutate(document: dict[str, Any]) -> dict[str, Any]:
            profile = active_profile(document)
            if not profile or "conflict" not in profile["capabilities"]:
                raise ValueError("active profile does not provide conflict")
            if action == "start":
                if document["phase"] != PHASE_PLAY or document.get("conflict"):
                    raise ValueError("conflict can start only from non-conflict Play")
                if any(item["status"] == "open" for item in document["npc_conversations"].values()):
                    raise ValueError("close or abort every NPC conversation before conflict")
                value = {
                    "id": f"conflict_{uuid4().hex}",
                    "status": "active",
                    "revision": 1,
                    "controller_principal_id": common["principal_id"],
                    "data": deepcopy(data or {}),
                    "log": [],
                }
                document["conflict"] = value
                document["phase"] = PHASE_CONFLICT
            else:
                value = document.get("conflict")
                if document["phase"] != PHASE_CONFLICT or not value:
                    raise ValueError("no active conflict")
                if action == "act":
                    value["log"].append(deepcopy(data or {}))
                    value["revision"] += 1
                elif action == "end":
                    value["status"] = "ended"
                    value["outcome"] = deepcopy(data or {})
                    value["revision"] += 1
                    document["phase"] = PHASE_PLAY
                    document["conflict"] = None
                else:
                    raise ValueError("conflict action must be start, act, or end")
            return {"conflict": deepcopy(value)}

        return self._write(
            campaign_id,
            operation=f"conflict.{action}",
            payload={"action": action, "data": data},
            mutate=mutate,
            **common,
        )
