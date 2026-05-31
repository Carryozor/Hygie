"""Backward-compatibility shim — all symbols now live in rules.legacy_conditions.

Import from backend.rules.legacy_conditions directly in new code.
"""
# Re-export everything for backward compatibility
from .rules.legacy_conditions import *  # noqa: F401, F403
from .rules.legacy_conditions import (  # noqa: F401 (explicit for IDE support)
    ScanContext,
    _eval_op,
    _days_since,
    _evaluate_conditions,
    _seerr_filter_passes,
    _get_seerr_grace,
    _update_delete_at_if_pending,
    _get_poster_url,
    _evaluate_item,
)
