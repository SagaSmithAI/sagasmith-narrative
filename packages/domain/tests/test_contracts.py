from __future__ import annotations

import pytest

from sagasmith_narrative.contracts import (
    PHASE_LOBBY,
    initial_document,
    narrative_document,
    validate_profile,
    validate_record,
)


def test_initial_document_starts_in_lobby_without_authoritative_side_effects() -> None:
    document = initial_document()

    assert document["phase"] == PHASE_LOBBY
    assert document["profiles"]["active"] is None
    assert document["random_stream"] == {"seed": None, "cursor": 0}


def test_level_zero_profile_rejects_executable_mechanics() -> None:
    with pytest.raises(ValueError, match="Level 0 profiles cannot declare mechanics"):
        validate_profile(
            {
                "id": "profile.freeform",
                "version": "1.0.0",
                "mechanics_level": 0,
                "mechanics": [{"id": "mechanic.roll", "kind": "table", "entries": [{}]}],
            }
        )


def test_record_validation_preserves_explicit_audience_and_controller() -> None:
    record = validate_record(
        {
            "id": "thread.harbor",
            "kind": "thread",
            "audience": {"scope": "actor", "actor_id": "actor.mira"},
            "controller": {"scope": "steward", "principal_id": "user:mira"},
        }
    )

    assert record["audience"] == {"scope": "actor", "actor_id": "actor.mira"}
    assert record["controller"] == {"scope": "steward", "principal_id": "user:mira"}


def test_narrative_document_rejects_unknown_phase() -> None:
    with pytest.raises(ValueError, match="invalid narrative phase"):
        narrative_document({"narrative": {**initial_document(), "phase": "combat"}})
