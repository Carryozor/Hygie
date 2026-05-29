import pytest
from backend.rules.models import (
    Condition, ExpertRule, RuleAction, RuleOperator,
    ConditionField, ConditionOp,
)
from backend.rules.engine import evaluate_condition, evaluate_rule

MOVIE = {
    "days_not_watched": 400,
    "play_count": 1,
    "rating": 4.5,
    "file_size_gb": 25.0,
    "added_days_ago": 500,
    "media_type": "Movie",
    "seerr_user_id": 42,
}

@pytest.mark.parametrize("op,value,expected", [
    (ConditionOp.GT,  365, True),
    (ConditionOp.GT,  500, False),
    (ConditionOp.LT,  500, True),
    (ConditionOp.LT,  365, False),
    (ConditionOp.GTE, 400, True),
    (ConditionOp.LTE, 400, True),
    (ConditionOp.EQ,  400, True),
    (ConditionOp.EQ,  399, False),
])
def test_evaluate_condition_numeric(op, value, expected):
    c = Condition(field=ConditionField.DAYS_NOT_WATCHED, op=op, value=value)
    assert evaluate_condition(c, MOVIE) == expected

def test_evaluate_condition_in():
    c = Condition(field=ConditionField.SEERR_USER_ID, op=ConditionOp.IN, value=[42, 99])
    assert evaluate_condition(c, MOVIE) is True
    c2 = Condition(field=ConditionField.SEERR_USER_ID, op=ConditionOp.IN, value=[1, 2])
    assert evaluate_condition(c2, MOVIE) is False

def test_evaluate_condition_not_in():
    c = Condition(field=ConditionField.SEERR_USER_ID, op=ConditionOp.NOT_IN, value=[1, 2])
    assert evaluate_condition(c, MOVIE) is True

def test_evaluate_condition_media_type_eq():
    c = Condition(field=ConditionField.MEDIA_TYPE, op=ConditionOp.EQ, value="Movie")
    assert evaluate_condition(c, MOVIE) is True

def test_evaluate_condition_missing_field_returns_false():
    c = Condition(field=ConditionField.RATING, op=ConditionOp.GT, value=3.0)
    assert evaluate_condition(c, {}) is False

def test_evaluate_rule_and_all_match():
    rule = ExpertRule(name="r", conditions=[
        Condition(field=ConditionField.DAYS_NOT_WATCHED, op=ConditionOp.GT, value=365),
        Condition(field=ConditionField.RATING, op=ConditionOp.LT, value=5.0),
    ], operator=RuleOperator.AND)
    assert evaluate_rule(rule, MOVIE) is True

def test_evaluate_rule_and_partial_match():
    rule = ExpertRule(name="r", conditions=[
        Condition(field=ConditionField.DAYS_NOT_WATCHED, op=ConditionOp.GT, value=365),
        Condition(field=ConditionField.RATING, op=ConditionOp.GT, value=8.0),
    ], operator=RuleOperator.AND)
    assert evaluate_rule(rule, MOVIE) is False

def test_evaluate_rule_or_partial_match():
    rule = ExpertRule(name="r", conditions=[
        Condition(field=ConditionField.DAYS_NOT_WATCHED, op=ConditionOp.GT, value=365),
        Condition(field=ConditionField.RATING, op=ConditionOp.GT, value=8.0),
    ], operator=RuleOperator.OR)
    assert evaluate_rule(rule, MOVIE) is True

def test_evaluate_rule_disabled_always_false():
    rule = ExpertRule(name="r", enabled=False, conditions=[
        Condition(field=ConditionField.DAYS_NOT_WATCHED, op=ConditionOp.GT, value=1),
    ])
    assert evaluate_rule(rule, MOVIE) is False
