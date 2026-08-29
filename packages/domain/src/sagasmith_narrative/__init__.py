"""Deterministic declarative contracts for SagaSmith Narrative."""

__version__ = "0.1.0"
from .narrative_design import (
    CAMPAIGN_MODES,
    MANIFEST_CLASSIFICATIONS,
    PROGRESS_ENTITY_TYPES,
    validate_campaign_design,
    validate_progress_change,
    validate_runtime_manifest,
)

__all__ = [
    "CAMPAIGN_MODES",
    "MANIFEST_CLASSIFICATIONS",
    "PROGRESS_ENTITY_TYPES",
    "validate_campaign_design",
    "validate_progress_change",
    "validate_runtime_manifest",
]
