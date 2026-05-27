"""Unit tests for scheduler condition evaluation — pure functions, no DB or HTTP."""
from datetime import datetime, timezone, timedelta
import pytest

from backend.conditions import (
    _eval_op,
    _evaluate_conditions,
    _seerr_filter_passes,
)


def _dt(days_ago: int) -> datetime:
    """Return a timezone-aware datetime `days_ago` days in the past."""
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


# ─── _eval_op ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("actual,op,threshold,expected", [
    # gt
    (10, "gt",  5,   True),
    (5,  "gt",  5,   False),
    (0,  "gt",  5,   False),
    # gte
    (5,  "gte", 5,   True),
    (6,  "gte", 5,   True),
    (4,  "gte", 5,   False),
    # lt
    (3,  "lt",  5,   True),
    (5,  "lt",  5,   False),
    (6,  "lt",  5,   False),
    # lte
    (5,  "lte", 5,   True),
    (4,  "lte", 5,   True),
    (6,  "lte", 5,   False),
    # eq
    (5,  "eq",  5,   True),
    (4,  "eq",  5,   False),
    (0,  "eq",  0,   True),
])
def test_eval_op_all_operators(actual, op, threshold, expected):
    assert _eval_op(actual, op, threshold) is expected


def test_eval_op_none_actual_always_false():
    for op in ("gt", "gte", "lt", "lte", "eq"):
        assert _eval_op(None, op, 5) is False, f"Expected False for op={op!r} with None"


def test_eval_op_unknown_operator_returns_false():
    assert _eval_op(10, "ne", 5) is False
    assert _eval_op(10, "contains", 5) is False
    assert _eval_op(10, "", 5) is False


# ─── _evaluate_conditions — empty / edge cases ─────────────────────────────

def test_empty_conditions_always_returns_false():
    assert _evaluate_conditions([], "AND", _dt(10), None, 0, True) is False
    assert _evaluate_conditions([], "OR",  _dt(10), None, 0, True) is False


def test_unknown_field_evaluates_to_false():
    conditions = [{"field": "nonexistent_field", "op": "gt", "value": 0}]
    assert _evaluate_conditions(conditions, "AND", _dt(10), None, 0, True) is False


# ─── days_since_added ─────────────────────────────────────────────────────────

def test_days_since_added_gt_matches():
    conditions = [{"field": "days_since_added", "op": "gt", "value": 30}]
    assert _evaluate_conditions(conditions, "AND", _dt(40), None, 0, True) is True


def test_days_since_added_gt_does_not_match():
    conditions = [{"field": "days_since_added", "op": "gt", "value": 30}]
    assert _evaluate_conditions(conditions, "AND", _dt(20), None, 0, True) is False


def test_days_since_added_eq_boundary():
    conditions = [{"field": "days_since_added", "op": "eq", "value": 30}]
    assert _evaluate_conditions(conditions, "AND", _dt(30), None, 0, True) is True
    assert _evaluate_conditions(conditions, "AND", _dt(31), None, 0, True) is False


# ─── days_not_watched ─────────────────────────────────────────────────────────

def test_days_not_watched_never_watched_always_matches():
    """never_watched=True means we treat it as infinitely long since last watch."""
    conditions = [{"field": "days_not_watched", "op": "gt", "value": 30}]
    # Even added just today, never watched → condition passes
    assert _evaluate_conditions(conditions, "AND", _dt(0), None, 0, True) is True


def test_days_not_watched_with_recent_play_does_not_match():
    conditions = [{"field": "days_not_watched", "op": "gt", "value": 30}]
    # Played 10 days ago → only 10 days not watched → condition fails
    assert _evaluate_conditions(conditions, "AND", _dt(10), _dt(10), 1, False) is False


def test_days_not_watched_with_old_play_matches():
    conditions = [{"field": "days_not_watched", "op": "gt", "value": 30}]
    # Played 40 days ago → 40 days not watched → condition passes
    assert _evaluate_conditions(conditions, "AND", _dt(10), _dt(40), 1, False) is True


def test_days_not_watched_no_last_played_and_not_never_watched():
    """never_watched=False but last_played=None: _days_since returns None → False."""
    conditions = [{"field": "days_not_watched", "op": "gt", "value": 0}]
    # never_watched=False means system thinks it was played, but no timestamp → False
    assert _evaluate_conditions(conditions, "AND", _dt(10), None, 1, False) is False


# ─── never_watched ───────────────────────────────────────────────────────────

def test_never_watched_eq_true_when_unwatched():
    conditions = [{"field": "never_watched", "op": "eq", "value": True}]
    assert _evaluate_conditions(conditions, "AND", _dt(10), None, 0, True) is True


def test_never_watched_eq_true_when_watched_fails():
    conditions = [{"field": "never_watched", "op": "eq", "value": True}]
    assert _evaluate_conditions(conditions, "AND", _dt(10), _dt(5), 2, False) is False


def test_never_watched_eq_false_when_watched():
    conditions = [{"field": "never_watched", "op": "eq", "value": False}]
    assert _evaluate_conditions(conditions, "AND", _dt(10), _dt(5), 2, False) is True


# ─── play_count ──────────────────────────────────────────────────────────────

def test_play_count_lte():
    conditions = [{"field": "play_count", "op": "lte", "value": 1}]
    assert _evaluate_conditions(conditions, "AND", _dt(10), _dt(5), 1, False) is True
    assert _evaluate_conditions(conditions, "AND", _dt(10), _dt(5), 2, False) is False


def test_play_count_eq_zero():
    conditions = [{"field": "play_count", "op": "eq", "value": 0}]
    assert _evaluate_conditions(conditions, "AND", _dt(10), None, 0, True) is True
    assert _evaluate_conditions(conditions, "AND", _dt(10), _dt(5), 1, False) is False


# ─── AND / OR logic ──────────────────────────────────────────────────────────

def test_and_logic_all_must_match():
    conditions = [
        {"field": "days_since_added", "op": "gt", "value": 30},
        {"field": "play_count", "op": "eq", "value": 0},
    ]
    # Both pass
    assert _evaluate_conditions(conditions, "AND", _dt(40), None, 0, True) is True
    # First passes, second fails (play_count=1)
    assert _evaluate_conditions(conditions, "AND", _dt(40), _dt(5), 1, False) is False
    # First fails, second passes
    assert _evaluate_conditions(conditions, "AND", _dt(10), None, 0, True) is False


def test_or_logic_any_must_match():
    conditions = [
        {"field": "days_since_added", "op": "gt", "value": 30},
        {"field": "play_count", "op": "eq", "value": 0},
    ]
    # Only second passes (added recently, play=0)
    assert _evaluate_conditions(conditions, "OR", _dt(10), None, 0, True) is True
    # Only first passes (added long ago, play=1)
    assert _evaluate_conditions(conditions, "OR", _dt(40), _dt(5), 1, False) is True
    # Neither passes
    assert _evaluate_conditions(conditions, "OR", _dt(10), _dt(5), 2, False) is False


def test_and_single_condition():
    conditions = [{"field": "days_since_added", "op": "gt", "value": 5}]
    assert _evaluate_conditions(conditions, "AND", _dt(10), None, 0, True) is True
    assert _evaluate_conditions(conditions, "AND", _dt(3), None, 0, True) is False


# ─── _seerr_filter_passes ─────────────────────────────────────────────────────

def test_seerr_no_conditions_always_passes():
    assert _seerr_filter_passes(42, []) is True
    assert _seerr_filter_passes(None, []) is True
    assert _seerr_filter_passes(0, []) is True


def test_seerr_include_passes_for_listed_user():
    conditions = [{"type": "user_include", "user_id": 42}]
    assert _seerr_filter_passes(42, conditions) is True


def test_seerr_include_blocks_unlisted_user():
    conditions = [{"type": "user_include", "user_id": 42}]
    assert _seerr_filter_passes(99, conditions) is False


def test_seerr_include_blocks_none_user():
    """Include list without matching user_id must block items with no requester."""
    conditions = [{"type": "user_include", "user_id": 42}]
    assert _seerr_filter_passes(None, conditions) is False


def test_seerr_exclude_blocks_listed_user():
    conditions = [{"type": "user_exclude", "user_id": 42}]
    assert _seerr_filter_passes(42, conditions) is False


def test_seerr_exclude_allows_unlisted_user():
    conditions = [{"type": "user_exclude", "user_id": 42}]
    assert _seerr_filter_passes(99, conditions) is True


def test_seerr_exclude_allows_none_user():
    """Exclude list must not block items with no known requester."""
    conditions = [{"type": "user_exclude", "user_id": 42}]
    assert _seerr_filter_passes(None, conditions) is True


def test_seerr_multiple_includes():
    conditions = [
        {"type": "user_include", "user_id": 10},
        {"type": "user_include", "user_id": 20},
    ]
    assert _seerr_filter_passes(10, conditions) is True
    assert _seerr_filter_passes(20, conditions) is True
    assert _seerr_filter_passes(30, conditions) is False


def test_seerr_multiple_excludes():
    conditions = [
        {"type": "user_exclude", "user_id": 10},
        {"type": "user_exclude", "user_id": 20},
    ]
    assert _seerr_filter_passes(10, conditions) is False
    assert _seerr_filter_passes(20, conditions) is False
    assert _seerr_filter_passes(30, conditions) is True
