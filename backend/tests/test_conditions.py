"""Tests for conditions.py — _evaluate_conditions and _seerr_filter_passes."""
from datetime import datetime, timezone, timedelta

from backend.rules.legacy_conditions import _evaluate_conditions, _seerr_filter_passes


def utc_days_ago(n: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=n)


# ── _evaluate_conditions ──────────────────────────────────────────────────────

def test_and_days_since_added_match():
    conds = [{"field": "days_since_added", "op": "gt", "value": 30}]
    assert _evaluate_conditions(conds, "AND", utc_days_ago(45), None, 0, True) is True


def test_and_days_since_added_no_match():
    conds = [{"field": "days_since_added", "op": "gt", "value": 30}]
    assert _evaluate_conditions(conds, "AND", utc_days_ago(5), None, 0, True) is False


def test_and_all_conditions_must_pass():
    conds = [
        {"field": "days_since_added", "op": "gt", "value": 30},
        {"field": "play_count", "op": "eq", "value": 0},
    ]
    assert _evaluate_conditions(conds, "AND", utc_days_ago(45), None, 0, True) is True
    assert _evaluate_conditions(conds, "AND", utc_days_ago(45), None, 3, True) is False


def test_or_one_condition_matches():
    conds = [
        {"field": "days_since_added", "op": "gt", "value": 30},
        {"field": "play_count", "op": "gt", "value": 10},
    ]
    assert _evaluate_conditions(conds, "OR", utc_days_ago(5), None, 15, True) is True


def test_or_no_match():
    conds = [
        {"field": "days_since_added", "op": "gt", "value": 30},
        {"field": "play_count", "op": "gt", "value": 10},
    ]
    assert _evaluate_conditions(conds, "OR", utc_days_ago(5), None, 2, True) is False


def test_empty_conditions_returns_false():
    assert _evaluate_conditions([], "AND", utc_days_ago(100), None, 0, True) is False


def test_never_watched_true():
    conds = [{"field": "never_watched", "op": "eq", "value": 1}]
    assert _evaluate_conditions(conds, "AND", utc_days_ago(1), None, 0, True) is True


def test_never_watched_false():
    conds = [{"field": "never_watched", "op": "eq", "value": 1}]
    assert _evaluate_conditions(conds, "AND", utc_days_ago(1), None, 0, False) is False


def test_days_not_watched_never_watched_always_true():
    conds = [{"field": "days_not_watched", "op": "gt", "value": 30}]
    assert _evaluate_conditions(conds, "AND", utc_days_ago(5), None, 0, True) is True


def test_days_not_watched_with_last_played():
    conds = [{"field": "days_not_watched", "op": "gt", "value": 30}]
    last_played = utc_days_ago(45)
    assert _evaluate_conditions(conds, "AND", utc_days_ago(60), last_played, 1, False) is True


def test_days_not_watched_recent_play_fails():
    conds = [{"field": "days_not_watched", "op": "gt", "value": 30}]
    last_played = utc_days_ago(5)
    assert _evaluate_conditions(conds, "AND", utc_days_ago(60), last_played, 1, False) is False


def test_unknown_field_treated_as_no_match():
    conds = [{"field": "not_a_real_field", "op": "gt", "value": 0}]
    assert _evaluate_conditions(conds, "AND", utc_days_ago(100), None, 0, True) is False


# ── _seerr_filter_passes ──────────────────────────────────────────────────────

def test_no_seerr_conditions_always_passes():
    assert _seerr_filter_passes(42, []) is True
    assert _seerr_filter_passes(None, []) is True


def test_include_list_user_in():
    conds = [{"type": "user_include", "user_id": 5}]
    assert _seerr_filter_passes(5, conds) is True


def test_include_list_user_not_in():
    conds = [{"type": "user_include", "user_id": 5}]
    assert _seerr_filter_passes(99, conds) is False


def test_exclude_list_user_in():
    conds = [{"type": "user_exclude", "user_id": 5}]
    assert _seerr_filter_passes(5, conds) is False


def test_exclude_list_user_not_in():
    conds = [{"type": "user_exclude", "user_id": 5}]
    assert _seerr_filter_passes(99, conds) is True
