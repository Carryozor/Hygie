"""Tests for rules/engine.py — pure evaluation, no I/O."""
import pytest
from backend.rules.engine import evaluate_rule, evaluate_condition
from backend.rules.models import (
    Condition, ConditionField, ConditionOp,
    ConditionGroup, ExpertRule, RuleOperator, RuleAction,
)


def cond(field: str, op: str, value) -> Condition:
    return Condition(field=ConditionField(field), op=ConditionOp(op), value=value)


def group(*conditions, operator=RuleOperator.AND) -> ConditionGroup:
    return ConditionGroup(conditions=list(conditions), operator=operator)


def rule(*groups, operator=RuleOperator.AND, enabled=True) -> ExpertRule:
    return ExpertRule(
        name="test",
        condition_groups=list(groups),
        operator=operator,
        action=RuleAction.QUEUE,
        enabled=enabled,
        priority=0,
    )


# ── evaluate_condition ────────────────────────────────────────────────────────

def test_condition_gt_passes():
    c = cond("added_days_ago", "gt", 30)
    assert evaluate_condition(c, {"added_days_ago": 45}) is True


def test_condition_gt_fails():
    c = cond("added_days_ago", "gt", 30)
    assert evaluate_condition(c, {"added_days_ago": 10}) is False


def test_condition_gte_boundary():
    c = cond("play_count", "gte", 5)
    assert evaluate_condition(c, {"play_count": 5}) is True
    assert evaluate_condition(c, {"play_count": 4}) is False


def test_condition_lt_passes():
    c = cond("play_count", "lt", 5)
    assert evaluate_condition(c, {"play_count": 2}) is True


def test_condition_lte_boundary():
    c = cond("days_not_watched", "lte", 30)
    assert evaluate_condition(c, {"days_not_watched": 30}) is True
    assert evaluate_condition(c, {"days_not_watched": 31}) is False


def test_condition_eq():
    c = cond("media_type", "eq", "Movie")
    assert evaluate_condition(c, {"media_type": "Movie"}) is True
    assert evaluate_condition(c, {"media_type": "Episode"}) is False


def test_condition_in():
    c = cond("seerr_user_id", "in", [1, 2, 3])
    assert evaluate_condition(c, {"seerr_user_id": 2}) is True
    assert evaluate_condition(c, {"seerr_user_id": 99}) is False


def test_condition_not_in():
    c = cond("seerr_user_id", "not_in", [1, 2])
    assert evaluate_condition(c, {"seerr_user_id": 5}) is True
    assert evaluate_condition(c, {"seerr_user_id": 1}) is False


def test_condition_missing_field_returns_false():
    c = cond("play_count", "gt", 0)
    assert evaluate_condition(c, {}) is False


# ── evaluate_rule — single group AND ─────────────────────────────────────────

def test_and_all_pass():
    r = rule(group(cond("added_days_ago", "gt", 30), cond("play_count", "lt", 5)))
    assert evaluate_rule(r, {"added_days_ago": 45, "play_count": 2}) is True


def test_and_one_fails():
    r = rule(group(cond("added_days_ago", "gt", 30), cond("play_count", "lt", 5)))
    assert evaluate_rule(r, {"added_days_ago": 45, "play_count": 10}) is False


def test_and_all_fail():
    r = rule(group(cond("added_days_ago", "gt", 30), cond("play_count", "lt", 5)))
    assert evaluate_rule(r, {"added_days_ago": 5, "play_count": 10}) is False


# ── evaluate_rule — single group OR ──────────────────────────────────────────

def test_or_one_passes():
    r = rule(group(cond("added_days_ago", "gt", 30), cond("play_count", "lt", 5), operator=RuleOperator.OR))
    assert evaluate_rule(r, {"added_days_ago": 5, "play_count": 2}) is True


def test_or_all_fail():
    r = rule(group(cond("added_days_ago", "gt", 30), cond("play_count", "lt", 5), operator=RuleOperator.OR))
    assert evaluate_rule(r, {"added_days_ago": 5, "play_count": 10}) is False


def test_or_all_pass():
    r = rule(group(cond("added_days_ago", "gt", 30), cond("play_count", "lt", 5), operator=RuleOperator.OR))
    assert evaluate_rule(r, {"added_days_ago": 45, "play_count": 2}) is True


# ── evaluate_rule — multiple groups ──────────────────────────────────────────

def test_two_groups_and_both_pass():
    # (A OR B) AND (C)
    g1 = group(cond("added_days_ago", "gt", 30), cond("play_count", "lt", 5), operator=RuleOperator.OR)
    g2 = group(cond("days_not_watched", "gt", 10))
    r = rule(g1, g2, operator=RuleOperator.AND)
    assert evaluate_rule(r, {"added_days_ago": 5, "play_count": 2, "days_not_watched": 15}) is True


def test_two_groups_and_one_fails():
    g1 = group(cond("added_days_ago", "gt", 30), cond("play_count", "lt", 5), operator=RuleOperator.OR)
    g2 = group(cond("days_not_watched", "gt", 10))
    r = rule(g1, g2, operator=RuleOperator.AND)
    # g1 passes (play_count < 5), g2 fails (days_not_watched = 5)
    assert evaluate_rule(r, {"added_days_ago": 5, "play_count": 2, "days_not_watched": 5}) is False


def test_two_groups_or_one_passes():
    g1 = group(cond("added_days_ago", "gt", 100))  # fails
    g2 = group(cond("play_count", "eq", 0))        # passes
    r = rule(g1, g2, operator=RuleOperator.OR)
    assert evaluate_rule(r, {"added_days_ago": 5, "play_count": 0}) is True


def test_two_groups_or_both_fail():
    g1 = group(cond("added_days_ago", "gt", 100))
    g2 = group(cond("play_count", "gt", 50))
    r = rule(g1, g2, operator=RuleOperator.OR)
    assert evaluate_rule(r, {"added_days_ago": 5, "play_count": 0}) is False


# ── edge cases ────────────────────────────────────────────────────────────────

def test_disabled_rule_never_matches():
    r = rule(group(cond("added_days_ago", "gt", 0)), enabled=False)
    assert evaluate_rule(r, {"added_days_ago": 999}) is False


def test_empty_conditions_raises():
    with pytest.raises(Exception):
        ExpertRule(name="x", condition_groups=[], operator=RuleOperator.AND,
                   action=RuleAction.QUEUE, enabled=True, priority=0)


def test_empty_group_conditions_raises():
    with pytest.raises(Exception):
        ConditionGroup(conditions=[], operator=RuleOperator.AND)
