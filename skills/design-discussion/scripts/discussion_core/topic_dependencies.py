"""Stable public façade for topic dependency protocol operations.

The implementation is split by responsibility: authority discovery, mutable
lifecycle records, and derived gate evaluation/release.  Existing callers keep
importing this module.
"""

from .topic_dependency_authority import (
    freeze_authority_selection, has_current_authority,
    release_child_result_dependencies, retained_checkpoint_identities,
)
from .topic_dependency_gates import (
    derived_gate, evaluate_topic_gate, release_topic_gate, require_open_gate,
    require_open_gate_for_phase_transition,
)
from .topic_dependency_lifecycle import (
    prepare_initial_dependencies, reclose_directly_affected,
    update_topic_dependency,
)

__all__ = [
    "derived_gate", "evaluate_topic_gate", "freeze_authority_selection",
    "has_current_authority", "prepare_initial_dependencies",
    "reclose_directly_affected", "release_child_result_dependencies",
    "release_topic_gate", "require_open_gate",
    "require_open_gate_for_phase_transition", "retained_checkpoint_identities",
    "update_topic_dependency",
]
