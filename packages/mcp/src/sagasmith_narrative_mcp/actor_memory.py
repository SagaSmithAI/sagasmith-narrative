"""Deterministic four-track memory selection for narrative actors.

The selector is intentionally persistence- and portrayal-neutral. Callers must
authorize and branch-filter every input before invoking it; the result cannot
choose an actor's intent or write campaign state.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable, Mapping

MEMORY_TRACKS = ("identity", "motivational", "semantic", "episodic")
MOTIVATIONAL_PREDICATES = frozenset(
    {
        "bond",
        "commitment",
        "desire",
        "drive",
        "duty",
        "fear",
        "goal",
        "motivation",
        "objective",
        "promise",
        "relationship",
        "relationship_to",
    }
)
_TOKEN_RE = re.compile(r"[\w:-]+", re.UNICODE)


def _record(value: Any, field: str) -> dict[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object or dataclass instance")
    return deepcopy(dict(value))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _refs(value: Any) -> list[str]:
    result: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for raw_key, nested in item.items():
                key = str(raw_key)
                if key.endswith("_ref") and _text(nested):
                    result.add(_text(nested))
                elif key.endswith("_refs") and isinstance(nested, (list, tuple, set)):
                    result.update(_text(ref) for ref in nested if _text(ref))
                elif key in {"actor_id", "speaker_actor_id"} and _text(nested):
                    result.add(f"actor:{_text(nested)}")
                elif key == "actor_ids" and isinstance(nested, (list, tuple, set)):
                    result.update(f"actor:{_text(ref)}" for ref in nested if _text(ref))
                elif key == "scene_id" and _text(nested):
                    result.add(f"scene:{_text(nested)}")
                elif key in {"event_id", "source_event_id"} and _text(nested):
                    result.add(f"event:{_text(nested)}")
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)

    visit(value)
    return sorted(result)


def _bounded(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return max(0, min(5, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def _marker(item: Mapping[str, Any]) -> float:
    for key in ("sequence", "revision", "state_version"):
        value = item.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 0.0


def _state_candidates(actor_state: Any) -> list[dict[str, Any]]:
    if actor_state is None:
        return []
    if isinstance(actor_state, Mapping) or is_dataclass(actor_state):
        projection = _record(actor_state, "actor_state")
        nested = projection.pop("state_facts", projection.pop("facts", []))
        if not isinstance(nested, list):
            raise ValueError("actor_state facts must be a list")
        return [projection, *[_record(item, "actor_state.facts[]") for item in nested]]
    if isinstance(actor_state, (str, bytes)):
        raise ValueError("actor_state must be an object or iterable of objects")
    return [_record(item, "actor_state[]") for item in actor_state]


def _candidate(track: str, source: str, basis_ref: str, content: str, item: dict) -> dict:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    return {
        "track": track,
        "source": source,
        "basis_ref": basis_ref,
        "content": content,
        "refs": _refs(item),
        "record": item,
        "confidence": _bounded(item.get("confidence"), 3),
        "salience": _bounded(item.get("importance", metadata.get("importance")), 3),
        "recency": _marker(item),
    }


def select_actor_memory_context(
    *,
    actor_state: Any,
    actor_knowledge: Iterable[Mapping[str, Any] | Any],
    events: Iterable[Mapping[str, Any] | Any],
    current_refs: Iterable[str] = (),
    query: str = "",
    budget_chars: int = 8_000,
) -> dict[str, Any]:
    """Return a strict-budget, deterministic memory projection."""

    if isinstance(budget_chars, bool) or not isinstance(budget_chars, int):
        raise ValueError("budget_chars must be an integer")
    if budget_chars < 0:
        raise ValueError("budget_chars must not be negative")
    candidates: list[dict[str, Any]] = []
    for item in _state_candidates(actor_state):
        predicate = _text(item.get("predicate")).casefold().replace("-", "_")
        motivational = predicate in MOTIVATIONAL_PREDICATES or _text(
            item.get("kind")
        ).casefold() in {"goal", "commitment", "relationship", "motivation"}
        identifier = _text(item.get("id") or item.get("fact_key") or item.get("name"))
        revision = _text(item.get("revision_id") or item.get("revision"))
        prefix = "fact" if item.get("fact_key") else "actor"
        basis = f"{prefix}:{identifier or len(candidates)}"
        if revision:
            basis += f":{revision}"
        candidates.append(
            _candidate(
                "motivational" if motivational else "identity",
                "actor_state_fact" if item.get("fact_key") else "actor_state",
                basis,
                _text(item.get("content") or item.get("summary") or item.get("name"))
                or _canonical(item),
                item,
            )
        )
    for raw in actor_knowledge:
        item = _record(raw, "actor_knowledge[]")
        identifier = _text(item.get("id") or item.get("knowledge_key"))
        revision = _text(item.get("revision_id"))
        basis = f"knowledge:{identifier}"
        if revision:
            basis += f":{revision}"
        candidates.append(
            _candidate(
                "semantic",
                "actor_knowledge",
                basis,
                _text(item.get("proposition")) or _canonical(item),
                item,
            )
        )
    for raw in events:
        item = _record(raw, "events[]")
        identifier = _text(item.get("id")) or str(len(candidates))
        candidates.append(
            _candidate(
                "episodic",
                "event",
                f"event:{identifier}",
                _text(item.get("retrieval_text") or item.get("summary")) or _canonical(item),
                item,
            )
        )

    deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
    for item in candidates:
        key = (item["track"], " ".join(item["content"].casefold().split()))
        current = deduplicated.get(key)
        preference = (item["recency"], item["salience"], item["confidence"], item["basis_ref"])
        if current is None or preference > (
            current["recency"],
            current["salience"],
            current["confidence"],
            current["basis_ref"],
        ):
            deduplicated[key] = item

    refs = {_text(item) for item in current_refs if _text(item)}
    terms = sorted(set(_TOKEN_RE.findall(query.casefold())))
    ranked = []
    for item in deduplicated.values():
        candidate_refs = {item["basis_ref"], *item["refs"]}
        haystack = " ".join(
            [item["content"], item["basis_ref"], *item["refs"], _canonical(item["record"])]
        ).casefold()
        exact = len(refs.intersection(candidate_refs))
        lexical = sum(term in haystack for term in terms)
        score = (
            exact * 1_000_000
            + lexical * 10_000
            + item["salience"] * 1_000
            + item["confidence"] * 100
            + int(item["recency"])
        )
        rank = (
            -exact,
            -lexical,
            -item["salience"],
            -item["confidence"],
            -item["recency"],
            item["basis_ref"],
        )
        ranked.append((rank, item, score))
    ranked.sort(key=lambda value: value[0])

    materialized = []
    for _, item, score in ranked:
        public = {
            key: deepcopy(value)
            for key, value in item.items()
            if key not in {"track", "confidence", "salience", "recency"}
        }
        public["score"] = score
        materialized.append((item["track"], public, len(_canonical(public))))

    selected: dict[str, list[dict[str, Any]]] = {track: [] for track in MEMORY_TRACKS}
    selected_indexes: set[int] = set()
    used = 0

    # Reserve one deterministic best-ranked candidate per available track when
    # the strict budget can hold it. This prevents a dense episodic history from
    # starving stable identity, motivation, or semantic context.
    for track in MEMORY_TRACKS:
        floor = next(
            (
                (index, public, cost)
                for index, (candidate_track, public, cost) in enumerate(materialized)
                if candidate_track == track
            ),
            None,
        )
        if floor is None:
            continue
        index, public, cost = floor
        if used + cost <= budget_chars:
            selected[track].append(public)
            selected_indexes.add(index)
            used += cost

    # Spend the remaining budget in the original global rank order. Candidates
    # that could not fit their floor attempt remain eligible here in case a
    # smaller higher-density selection leaves room later.
    for index, (track, public, cost) in enumerate(materialized):
        if index in selected_indexes or used + cost > budget_chars:
            continue
        selected[track].append(public)
        selected_indexes.add(index)
        used += cost
    omitted = len(materialized) - len(selected_indexes)
    return {
        **selected,
        "diagnostics": {
            "strategy": "per_track_floor_exact_refs_lexical_salience_confidence_recency_v2",
            "budget_chars": budget_chars,
            "used_chars": used,
            "remaining_chars": budget_chars - used,
            "candidate_count": len(candidates),
            "deduplicated_count": len(deduplicated),
            "selected_count": sum(len(items) for items in selected.values()),
            "omitted_for_budget": omitted,
            "current_refs": sorted(refs),
            "query_terms": terms,
        },
    }
