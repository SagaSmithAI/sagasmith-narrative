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


@pytest.mark.parametrize("value", [True, False, "1", 1.5, None])
def test_profile_level_requires_an_integer(value) -> None:
    with pytest.raises(ValueError, match="mechanics_level"):
        validate_profile({"id": "profile.test", "version": "1", "mechanics_level": value})


@pytest.mark.parametrize("field", ["sides", "max_dice", "minimum", "maximum"])
@pytest.mark.parametrize("value", [True, "6", 6.5, None])
def test_dice_parameters_reject_lossy_numeric_coercion(field, value) -> None:
    mechanic = {
        "id": "mechanic.roll", "kind": "dice_pool", "sides": 6, "max_dice": 20,
        "bands": [{"minimum": 1, "maximum": 6}],
    }
    target = mechanic["bands"][0] if field in {"minimum", "maximum"} else mechanic
    target[field] = value
    with pytest.raises(ValueError, match=field):
        validate_profile({
            "id": "profile.test", "version": "1", "mechanics_level": 1,
            "capabilities": ["mechanics"], "mechanics": [mechanic],
        })


@pytest.mark.parametrize("value", [0, 101])
def test_dice_limit_is_rejected_instead_of_silently_clamped(value) -> None:
    with pytest.raises(ValueError, match="max_dice"):
        validate_profile({
            "id": "profile.test", "version": "1", "mechanics_level": 1,
            "capabilities": ["mechanics"], "mechanics": [{
                "id": "mechanic.roll", "kind": "dice_pool", "max_dice": value,
                "bands": [{"minimum": 1, "maximum": 6}],
            }],
        })


@pytest.mark.parametrize("value", [True, "2", 2.5, None])
def test_record_revision_requires_an_integer(value) -> None:
    with pytest.raises(ValueError, match="revision"):
        validate_record({"id": "thread.test", "kind": "thread", "revision": value})
