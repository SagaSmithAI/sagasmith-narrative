from __future__ import annotations

from copy import deepcopy

import pytest

from sagasmith_narrative import validate_campaign_design, validate_runtime_manifest


def manifest(
    identifier: str,
    *,
    classification: str = "authored_narrative",
    root_id: str | None = None,
    parent_id: str = "",
    generation: int = 0,
) -> dict:
    return {
        "schema_version": 1,
        "id": identifier,
        "classification": classification,
        "title": identifier,
        "lineage": {
            "root_id": root_id or identifier,
            "parent_id": parent_id,
            "generation": generation,
            "basis_refs": ["event:choice"] if generation else [],
        },
        "setting": {"premise": "A city remembers every bargain."},
        "atlas": {
            "chapters": [
                {
                    "id": "chapter.one",
                    "summary": "Arrival",
                    "scene_ids": ["scene.gate"],
                },
                {
                    "id": "chapter.two",
                    "summary": "Consequences",
                    "scene_ids": ["scene.archive"],
                },
            ],
            "scenes": [
                {
                    "id": "scene.gate",
                    "summary": "The gate opens.",
                    "chapter_id": "chapter.one",
                },
                {
                    "id": "scene.archive",
                    "summary": "The archive answers.",
                    "chapter_id": "chapter.two",
                },
            ],
        },
        "fronts": [
            {
                "id": "front.flood",
                "summary": "The river rises.",
                "linked_thread_ids": ["thread.bell"],
            }
        ],
        "threads": [
            {
                "id": "thread.bell",
                "summary": "Who rang the drowned bell?",
                "clue_ids": ["clue.silt"],
                "linked_front_ids": ["front.flood"],
            }
        ],
        "clues": [
            {
                "id": "clue.silt",
                "summary": "Blue silt marks the rope.",
                "thread_ids": ["thread.bell"],
                "scene_ids": ["scene.gate"],
            }
        ],
        "character_arcs": [
            {
                "id": "arc.pc",
                "actor_ref": "actor.pc",
                "arc_type": "player_opportunity",
                "question": "Will they trust the ferryman?",
                "opportunities": ["scene:scene.gate"],
            }
        ],
    }


def test_authored_root_and_evidence_bound_off_atlas_child_infer_mode() -> None:
    root = manifest("manifest.root")
    child = manifest(
        "manifest.wharf",
        classification="emergent_episode",
        root_id="manifest.root",
        parent_id="manifest.root",
        generation=1,
    )
    child["atlas"]["chapters"] = [
        {"id": "chapter.wharf", "summary": "Unplanned wharf", "scene_ids": ["scene.wharf"]}
    ]
    child["atlas"]["scenes"] = [
        {"id": "scene.wharf", "summary": "A reasonable place beyond the Atlas."}
    ]
    child["fronts"] = []
    child["threads"] = []
    child["clues"] = []
    child["character_arcs"] = []
    design = validate_campaign_design({"root@1": root, "wharf@1": child})
    assert design["campaign_mode"] == "authored_with_extensions"
    assert design["manifests"]["wharf@1"]["lineage"]["basis_refs"] == ["event:choice"]


@pytest.mark.parametrize("generation", [True, "1", -1])
def test_generation_is_a_strict_non_negative_integer(generation: object) -> None:
    value = manifest("manifest.bad")
    value["lineage"]["generation"] = generation
    with pytest.raises(ValueError, match="generation"):
        validate_runtime_manifest(value)


def test_campaign_design_requires_exactly_one_contiguous_rooted_lineage() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        validate_campaign_design({})
    with pytest.raises(ValueError, match="exactly one"):
        validate_campaign_design({"one": manifest("one"), "two": manifest("two")})
    skipped = manifest(
        "child",
        classification="emergent_episode",
        root_id="root",
        parent_id="root",
        generation=2,
    )
    skipped["fronts"] = []
    skipped["threads"] = []
    skipped["clues"] = []
    skipped["character_arcs"] = []
    skipped["atlas"] = {
        "chapters": [
            {"id": "chapter.child", "summary": "Child", "scene_ids": ["scene.child"]}
        ],
        "scenes": [{"id": "scene.child", "summary": "Child scene"}],
    }
    with pytest.raises(ValueError, match="contiguous"):
        validate_campaign_design({"root": manifest("root"), "child": skipped})


def test_player_arc_cannot_prewrite_choice_or_ending() -> None:
    value = manifest("manifest.forced")
    value["character_arcs"][0]["planned_ending"] = "They must betray the ferryman."
    with pytest.raises(ValueError, match="unknown fields"):
        validate_runtime_manifest(value)


def test_cross_references_fail_closed() -> None:
    value = manifest("manifest.bad-refs")
    value["threads"][0]["clue_ids"] = ["clue.missing"]
    with pytest.raises(ValueError, match="unknown clues"):
        validate_runtime_manifest(value)
    value = deepcopy(manifest("manifest.bad-scene"))
    value["clues"][0]["scene_ids"] = ["scene.missing"]
    with pytest.raises(ValueError, match="unknown scenes"):
        validate_runtime_manifest(value)


def unique_child() -> dict:
    value = manifest(
        "manifest.child",
        classification="emergent_episode",
        root_id="manifest.root",
        parent_id="manifest.root",
        generation=1,
    )
    value["atlas"] = {
        "chapters": [
            {
                "id": "chapter.child",
                "summary": "Child chapter",
                "scene_ids": ["scene.child"],
            }
        ],
        "scenes": [
            {
                "id": "scene.child",
                "summary": "Child scene",
                "chapter_id": "chapter.child",
            }
        ],
    }
    value["fronts"] = [
        {
            "id": "front.child",
            "summary": "Child front",
            "linked_thread_ids": ["thread.child"],
        }
    ]
    value["threads"] = [
        {
            "id": "thread.child",
            "summary": "Child thread",
            "clue_ids": ["clue.child"],
            "linked_front_ids": ["front.child"],
        }
    ]
    value["clues"] = [
        {
            "id": "clue.child",
            "summary": "Child clue",
            "thread_ids": ["thread.child"],
            "scene_ids": ["scene.child"],
        }
    ]
    value["character_arcs"] = [
        {
            "id": "arc.child",
            "actor_ref": "actor.pc",
            "arc_type": "player_opportunity",
            "question": "Will the hero listen?",
            "opportunities": ["scene:scene.child"],
        }
    ]
    return value


@pytest.mark.parametrize(
    ("collection", "duplicate_id"),
    [
        ("chapter", "chapter.one"),
        ("scene", "scene.gate"),
        ("front", "front.flood"),
        ("thread", "thread.bell"),
        ("clue", "clue.silt"),
        ("character_arc", "arc.pc"),
    ],
)
def test_cross_manifest_entity_ids_are_unambiguous(
    collection: str, duplicate_id: str
) -> None:
    child = unique_child()
    if collection == "chapter":
        child["atlas"]["chapters"][0]["id"] = duplicate_id
        child["atlas"]["scenes"][0]["chapter_id"] = duplicate_id
    elif collection == "scene":
        child["atlas"]["scenes"][0]["id"] = duplicate_id
        child["atlas"]["chapters"][0]["scene_ids"] = [duplicate_id]
        child["clues"][0]["scene_ids"] = [duplicate_id]
    elif collection == "front":
        child["fronts"][0]["id"] = duplicate_id
        child["threads"][0]["linked_front_ids"] = [duplicate_id]
    elif collection == "thread":
        child["threads"][0]["id"] = duplicate_id
        child["fronts"][0]["linked_thread_ids"] = [duplicate_id]
        child["clues"][0]["thread_ids"] = [duplicate_id]
    elif collection == "clue":
        child["clues"][0]["id"] = duplicate_id
        child["threads"][0]["clue_ids"] = [duplicate_id]
    else:
        child["character_arcs"][0]["id"] = duplicate_id
    with pytest.raises(ValueError, match="collision"):
        validate_campaign_design({"root@1": manifest("manifest.root"), "child@1": child})


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["setting"].update({"metadata": {"private": True}}),
        lambda value: value["character_arcs"][0].update(
            {"plannedOutcome": "The hero must betray the ferryman."}
        ),
        lambda value: value.update({"schema_version": "1"}),
    ],
)
def test_runtime_manifest_rejects_unknown_nested_and_variant_outcome_fields(
    mutation,
) -> None:
    value = manifest("manifest.strict")
    mutation(value)
    with pytest.raises(ValueError, match="unknown fields|schema_version"):
        validate_runtime_manifest(value)
