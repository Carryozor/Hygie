import pytest
from pydantic import ValidationError
from backend.rules.models import (
    Condition, ExpertRule, RuleAction, RuleOperator,
    ConditionField, ConditionOp,
)

def test_condition_valid():
    c = Condition(field=ConditionField.DAYS_NOT_WATCHED, op=ConditionOp.GT, value=365)
    assert c.field == ConditionField.DAYS_NOT_WATCHED
    assert c.value == 365

def test_condition_invalid_op_for_list_field():
    with pytest.raises(ValidationError):
        Condition(field=ConditionField.SEERR_USER_ID, op=ConditionOp.IN, value=42)

def test_expert_rule_defaults():
    rule = ExpertRule(
        name="Old movies",
        conditions=[Condition(field=ConditionField.DAYS_NOT_WATCHED, op=ConditionOp.GT, value=365)],
    )
    assert rule.operator == RuleOperator.AND
    assert rule.action == RuleAction.QUEUE
    assert rule.enabled is True
    assert rule.priority == 0

def test_expert_rule_serialization():
    rule = ExpertRule(
        name="Old movies",
        conditions=[Condition(field=ConditionField.DAYS_NOT_WATCHED, op=ConditionOp.GT, value=365)],
    )
    data = rule.model_dump_json()
    restored = ExpertRule.model_validate_json(data)
    assert restored.name == rule.name
    assert len(restored.conditions) == 1

def test_condition_in_op_requires_list():
    c = Condition(field=ConditionField.SEERR_USER_ID, op=ConditionOp.IN, value=[1, 2, 3])
    assert isinstance(c.value, list)

def test_rule_with_multiple_conditions():
    rule = ExpertRule(
        name="Unwatched low-rated",
        operator=RuleOperator.AND,
        conditions=[
            Condition(field=ConditionField.DAYS_NOT_WATCHED, op=ConditionOp.GT, value=180),
            Condition(field=ConditionField.RATING, op=ConditionOp.LT, value=5.0),
        ],
    )
    assert len(rule.conditions) == 2
