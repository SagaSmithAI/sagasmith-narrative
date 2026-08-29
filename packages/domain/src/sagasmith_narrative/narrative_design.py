"""Declarative contracts for authored and emergent narrative continuity."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .contracts import required_id, required_text

MANIFEST_CLASSIFICATIONS = frozenset(
    {"authored_narrative", "emergent_seed", "emergent_episode"}
)
CAMPAIGN_MODES = frozenset(
    {"authored_narrative", "authored_with_extensions", "emergent"}
)
PROGRESS_ENTITY_TYPES = frozenset({"front", "thread", "clue", "character_arc"})
ENTITY_STATUSES = {
    "chapter": frozenset({"open", "active", "completed"}),
    "scene": frozenset({"open", "active", "completed"}),
    "front": frozenset({"dormant", "open", "active", "escalated", "resolved"}),
    "thread": frozenset({"open", "active", "resolved", "abandoned"}),
    "clue": frozenset({"hidden", "open", "discovered", "interpreted"}),
    "character_arc": frozenset({"open", "active", "completed", "abandoned"}),
}
PROGRESS_TRANSITIONS = {
    "front": {
        "dormant": frozenset({"open", "active", "resolved"}),
        "open": frozenset({"active", "resolved"}),
        "active": frozenset({"escalated", "resolved"}),
        "escalated": frozenset({"resolved"}),
        "resolved": frozenset(),
    },
    "thread": {
        "open": frozenset({"active", "resolved", "abandoned"}),
        "active": frozenset({"resolved", "abandoned"}),
        "resolved": frozenset(),
        "abandoned": frozenset(),
    },
    "clue": {
        "hidden": frozenset({"discovered"}),
        "open": frozenset({"discovered"}),
        "discovered": frozenset({"interpreted"}),
        "interpreted": frozenset(),
    },
    "character_arc": {
        "open": frozenset({"active", "completed", "abandoned"}),
        "active": frozenset({"completed", "abandoned"}),
        "completed": frozenset(),
        "abandoned": frozenset(),
    },
}


def _exact_fields(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    if unknown := sorted(set(value) - allowed):
        raise ValueError(f"{field} has unknown fields: {unknown}")


def _list(value: Any, field: str, maximum: int = 512) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} must be an array with at most {maximum} entries")
    return list(value)


def _object(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return deepcopy(dict(value))


def _refs(value: Any, field: str, *, required: bool = False) -> list[str]:
    result = [required_text(item, f"{field}[]", limit=300) for item in _list(value, field)]
    if len(result) != len(set(result)):
        raise ValueError(f"{field} must not contain duplicates")
    if required and not result:
        raise ValueError(f"{field} requires at least one evidence reference")
    return result


def _unique(items: list[dict[str, Any]], field: str) -> None:
    identifiers = [item["id"] for item in items]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"{field} contains duplicate ids")


def _entities(
    value: Any,
    field: str,
    *,
    extra: tuple[str, ...] = (),
    entity_type: str,
) -> list[dict[str, Any]]:
    result = []
    for index, raw in enumerate(_list(value, field)):
        item = _object(raw, f"{field}[{index}]")
        _exact_fields(
            item,
            {"id", "title", "status", "summary", "evidence_refs", *extra},
            f"{field}[{index}]",
        )
        status = required_id(item.get("status") or "open", f"{field}[{index}].status")
        if status not in ENTITY_STATUSES[entity_type]:
            raise ValueError(f"{field}[{index}].status is unsupported")
        normalized = {
            "id": required_id(item.get("id"), f"{field}[{index}].id"),
            "title": required_text(
                item.get("title") or item.get("id"),
                f"{field}[{index}].title",
                limit=300,
            ),
            "status": status,
            "summary": required_text(
                item.get("summary") or item.get("question") or item.get("statement"),
                f"{field}[{index}].summary",
                limit=2_000,
            ),
            "evidence_refs": _refs(
                item.get("evidence_refs"), f"{field}[{index}].evidence_refs"
            ),
        }
        for key in extra:
            if key.endswith("_refs") or key.endswith("_ids"):
                normalized[key] = _refs(item.get(key), f"{field}[{index}].{key}")
            elif item.get(key) is not None:
                normalized[key] = required_text(
                    item.get(key), f"{field}[{index}].{key}", limit=2_000
                )
        result.append(normalized)
    _unique(result, field)
    return result


def validate_runtime_manifest(
    value: Mapping[str, Any], *, pack_key: str | None = None
) -> dict[str, Any]:
    """Validate one immutable authored root, emergent seed, or episode shard."""

    manifest = _object(value, "runtime_manifest")
    _exact_fields(
        manifest,
        {
            "schema_version",
            "id",
            "pack_key",
            "classification",
            "title",
            "lineage",
            "setting",
            "atlas",
            "fronts",
            "threads",
            "clues",
            "character_arcs",
        },
        "runtime_manifest",
    )
    if isinstance(manifest.get("schema_version"), bool) or manifest.get("schema_version") != 1:
        raise ValueError("runtime_manifest.schema_version must be 1")
    manifest_id = required_id(manifest.get("id"), "runtime_manifest.id")
    classification = str(manifest.get("classification") or "")
    if classification not in MANIFEST_CLASSIFICATIONS:
        raise ValueError("runtime_manifest.classification is unsupported")
    lineage = _object(manifest.get("lineage"), "runtime_manifest.lineage")
    _exact_fields(
        lineage, {"root_id", "parent_id", "generation", "basis_refs"}, "lineage"
    )
    root_id = required_id(lineage.get("root_id") or manifest_id, "lineage.root_id")
    parent_id = str(lineage.get("parent_id") or "").strip()
    if parent_id:
        parent_id = required_id(parent_id, "lineage.parent_id")
    generation = lineage.get("generation", 0)
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise ValueError("lineage.generation must be a non-negative integer")
    basis_refs = _refs(lineage.get("basis_refs"), "lineage.basis_refs")
    if classification in {"authored_narrative", "emergent_seed"}:
        if root_id != manifest_id or parent_id or generation != 0:
            raise ValueError("root runtime manifests must be generation 0 with no parent")
    else:
        if not parent_id or generation < 1 or root_id == manifest_id:
            raise ValueError("emergent_episode requires a parent, root, and positive generation")
        if not basis_refs:
            raise ValueError("emergent_episode lineage requires evidence basis_refs")

    setting = _object(manifest.get("setting"), "runtime_manifest.setting")
    _exact_fields(
        setting,
        {"premise", "themes", "boundaries", "evidence_refs"},
        "runtime_manifest.setting",
    )
    normalized_setting = {
        "premise": required_text(setting.get("premise"), "setting.premise", limit=4_000),
        "themes": _refs(setting.get("themes"), "setting.themes"),
        "boundaries": _refs(setting.get("boundaries"), "setting.boundaries"),
        "evidence_refs": _refs(setting.get("evidence_refs"), "setting.evidence_refs"),
    }

    atlas = _object(manifest.get("atlas"), "runtime_manifest.atlas")
    _exact_fields(atlas, {"chapters", "scenes"}, "runtime_manifest.atlas")
    chapters = _entities(
        atlas.get("chapters"),
        "atlas.chapters",
        extra=("scene_ids",),
        entity_type="chapter",
    )
    scenes = _entities(
        atlas.get("scenes"),
        "atlas.scenes",
        extra=("chapter_id", "location_ref", "source_kind"),
        entity_type="scene",
    )
    if classification in {"emergent_seed", "emergent_episode"} and not scenes:
        raise ValueError("emergent runtime manifests require at least one initial scene")
    chapter_ids = {item["id"] for item in chapters}
    scene_ids = {item["id"] for item in scenes}
    for scene in scenes:
        chapter_id = str(scene.get("chapter_id") or "")
        if chapter_id and chapter_id not in chapter_ids:
            raise ValueError(f"scene cites unknown chapter: {chapter_id}")
    for chapter in chapters:
        unknown = set(chapter.get("scene_ids") or []) - scene_ids
        if unknown:
            raise ValueError(f"chapter cites unknown scenes: {sorted(unknown)}")

    fronts = _entities(
        manifest.get("fronts"),
        "fronts",
        extra=("pressure", "stakes", "linked_thread_ids"),
        entity_type="front",
    )
    threads = _entities(
        manifest.get("threads"),
        "threads",
        extra=("question", "clue_ids", "linked_front_ids"),
        entity_type="thread",
    )
    clues = _entities(
        manifest.get("clues"),
        "clues",
        extra=("statement", "thread_ids", "scene_ids"),
        entity_type="clue",
    )
    front_ids = {item["id"] for item in fronts}
    thread_ids = {item["id"] for item in threads}
    clue_ids = {item["id"] for item in clues}
    for front in fronts:
        if unknown := set(front.get("linked_thread_ids") or []) - thread_ids:
            raise ValueError(f"front cites unknown threads: {sorted(unknown)}")
    for thread in threads:
        if unknown := set(thread.get("clue_ids") or []) - clue_ids:
            raise ValueError(f"thread cites unknown clues: {sorted(unknown)}")
        if unknown := set(thread.get("linked_front_ids") or []) - front_ids:
            raise ValueError(f"thread cites unknown fronts: {sorted(unknown)}")
    for clue in clues:
        if unknown := set(clue.get("thread_ids") or []) - thread_ids:
            raise ValueError(f"clue cites unknown threads: {sorted(unknown)}")
        if unknown := set(clue.get("scene_ids") or []) - scene_ids:
            raise ValueError(f"clue cites unknown scenes: {sorted(unknown)}")

    arcs = []
    for index, raw in enumerate(_list(manifest.get("character_arcs"), "character_arcs")):
        item = _object(raw, f"character_arcs[{index}]")
        _exact_fields(
            item,
            {
                "id",
                "actor_ref",
                "arc_type",
                "title",
                "question",
                "status",
                "opportunities",
                "evidence_refs",
            },
            f"character_arcs[{index}]",
        )
        arc_type = str(item.get("arc_type") or "")
        if arc_type not in {"player_opportunity", "npc_arc"}:
            raise ValueError("character arc type must be player_opportunity or npc_arc")
        status = required_id(
            item.get("status") or "open", f"character_arcs[{index}].status"
        )
        if status not in ENTITY_STATUSES["character_arc"]:
            raise ValueError(f"character_arcs[{index}].status is unsupported")
        opportunities = _refs(
            item.get("opportunities"),
            f"character_arcs[{index}].opportunities",
            required=arc_type == "player_opportunity",
        )
        if arc_type == "player_opportunity" and status != "open":
            raise ValueError("player opportunity arcs must begin open and unresolved")
        arcs.append(
            {
                "id": required_id(item.get("id"), f"character_arcs[{index}].id"),
                "actor_ref": required_text(
                    item.get("actor_ref"), f"character_arcs[{index}].actor_ref", limit=300
                ),
                "arc_type": arc_type,
                "title": required_text(
                    item.get("title") or item.get("id"),
                    f"character_arcs[{index}].title",
                    limit=300,
                ),
                "question": required_text(
                    item.get("question"), f"character_arcs[{index}].question", limit=2_000
                ),
                "status": status,
                "opportunities": opportunities,
                "evidence_refs": _refs(
                    item.get("evidence_refs"), f"character_arcs[{index}].evidence_refs"
                ),
            }
        )
    _unique(arcs, "character_arcs")
    return {
        "schema_version": 1,
        "id": manifest_id,
        "pack_key": required_text(
            pack_key or manifest.get("pack_key") or manifest_id,
            "runtime_manifest.pack_key",
            limit=300,
        ),
        "classification": classification,
        "title": required_text(
            manifest.get("title") or manifest_id, "runtime_manifest.title", limit=300
        ),
        "lineage": {
            "root_id": root_id,
            "parent_id": parent_id,
            "generation": generation,
            "basis_refs": basis_refs,
        },
        "setting": normalized_setting,
        "atlas": {"chapters": chapters, "scenes": scenes},
        "fronts": fronts,
        "threads": threads,
        "clues": clues,
        "character_arcs": arcs,
    }


def validate_campaign_design(
    manifests: Mapping[str, Mapping[str, Any]], *, requested_mode: str | None = None
) -> dict[str, Any]:
    """Validate cross-Pack lineage and infer the safe campaign mode."""

    normalized = {
        str(key): validate_runtime_manifest(value, pack_key=str(key))
        for key, value in manifests.items()
    }
    by_id = {item["id"]: item for item in normalized.values()}
    if len(by_id) != len(normalized):
        raise ValueError("campaign design contains duplicate runtime manifest ids")
    roots = [item for item in normalized.values() if item["lineage"]["generation"] == 0]
    if len(roots) != 1:
        raise ValueError("campaign design requires exactly one lineage root")
    for collection in (
        "fronts",
        "threads",
        "clues",
        "character_arcs",
    ):
        seen: dict[str, str] = {}
        for pack_key, manifest in normalized.items():
            for item in manifest[collection]:
                previous = seen.setdefault(item["id"], pack_key)
                if previous != pack_key:
                    raise ValueError(
                        f"campaign design {collection} id collision: {item['id']}"
                    )
    for collection in ("chapters", "scenes"):
        seen = {}
        for pack_key, manifest in normalized.items():
            for item in manifest["atlas"][collection]:
                previous = seen.setdefault(item["id"], pack_key)
                if previous != pack_key:
                    raise ValueError(
                        f"campaign design {collection} id collision: {item['id']}"
                    )
    for item in normalized.values():
        if item["classification"] != "emergent_episode":
            continue
        lineage = item["lineage"]
        parent = by_id.get(lineage["parent_id"])
        root = by_id.get(lineage["root_id"])
        if parent is None or root is None:
            raise ValueError("emergent episode parent and root must be active manifests")
        if parent["lineage"]["root_id"] != lineage["root_id"]:
            raise ValueError("emergent episode parent belongs to another lineage")
        if lineage["generation"] != parent["lineage"]["generation"] + 1:
            raise ValueError("emergent episode generations must be contiguous")
    mode = "emergent"
    if roots:
        root_type = roots[0]["classification"]
        if root_type == "authored_narrative":
            mode = (
                "authored_with_extensions"
                if any(item["classification"] == "emergent_episode" for item in normalized.values())
                else "authored_narrative"
            )
    if requested_mode is not None:
        if requested_mode not in CAMPAIGN_MODES:
            raise ValueError("unsupported campaign mode")
        if requested_mode != mode:
            raise ValueError(f"campaign mode {requested_mode!r} conflicts with active lineage")
    return {
        "schema_version": 1,
        "campaign_mode": mode,
        "manifests": normalized,
        "progress": {entity: {} for entity in sorted(PROGRESS_ENTITY_TYPES)},
    }


def validate_progress_change(
    design: Mapping[str, Any],
    *,
    entity_type: str,
    entity_id: str,
    status: str,
    evidence_refs: list[str],
    note: str = "",
) -> dict[str, Any]:
    if entity_type not in PROGRESS_ENTITY_TYPES:
        raise ValueError("unsupported campaign progress entity type")
    identifier = required_id(entity_id, "campaign progress entity_id")
    collection = "character_arcs" if entity_type == "character_arc" else f"{entity_type}s"
    available = {
        item["id"]: item
        for manifest in dict(design.get("manifests") or {}).values()
        for item in list(dict(manifest).get(collection) or [])
    }
    if identifier not in available:
        raise LookupError(identifier)
    target = available[identifier]
    requested_status = required_id(status, "campaign progress status")
    if requested_status not in ENTITY_STATUSES[entity_type]:
        raise ValueError("campaign progress status is unsupported")
    current = dict(dict(design.get("progress") or {}).get(entity_type) or {}).get(identifier)
    current_status = str(dict(current or {}).get("status") or target.get("status") or "open")
    if requested_status not in PROGRESS_TRANSITIONS[entity_type].get(
        current_status, frozenset()
    ):
        raise ValueError(
            f"illegal {entity_type} progress transition: {current_status} -> {requested_status}"
        )
    normalized_evidence = _refs(
        evidence_refs, "campaign progress evidence_refs", required=True
    )
    if (
        entity_type == "character_arc"
        and target.get("arc_type") == "player_opportunity"
        and requested_status == "completed"
        and not set(target.get("opportunities") or []).intersection(normalized_evidence)
    ):
        raise ValueError(
            "player arc completion must cite one of its declared opportunities"
        )
    return {
        "entity_type": entity_type,
        "entity_id": identifier,
        "status": requested_status,
        "evidence_refs": normalized_evidence,
        "note": required_text(note or status, "campaign progress note", limit=2_000),
    }
