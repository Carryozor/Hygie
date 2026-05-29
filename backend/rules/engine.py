import logging
from .models import Condition, ConditionOp, ExpertRule, RuleOperator

logger = logging.getLogger(__name__)

_NUMERIC_OPS = {
    ConditionOp.GT:  lambda a, b: a > b,
    ConditionOp.LT:  lambda a, b: a < b,
    ConditionOp.GTE: lambda a, b: a >= b,
    ConditionOp.LTE: lambda a, b: a <= b,
    ConditionOp.EQ:  lambda a, b: a == b,
}

def evaluate_condition(condition: Condition, item: dict) -> bool:
    """Return True if item satisfies condition. Missing field = False."""
    key = condition.field.value
    if key not in item:
        return False
    raw = item[key]
    try:
        op = condition.op
        val = condition.value
        if op in _NUMERIC_OPS:
            return _NUMERIC_OPS[op](raw, val)
        if op == ConditionOp.IN:
            return raw in val
        if op == ConditionOp.NOT_IN:
            return raw not in val
    except Exception as e:
        logger.debug("evaluate_condition(%s): %s", condition.field, e)
    return False


def evaluate_rule(rule: ExpertRule, item: dict) -> bool:
    """Return True if item matches rule. Disabled rules always return False."""
    if not rule.enabled or not rule.conditions:
        return False
    results = [evaluate_condition(c, item) for c in rule.conditions]
    if rule.operator == RuleOperator.AND:
        return all(results)
    return any(results)  # OR
