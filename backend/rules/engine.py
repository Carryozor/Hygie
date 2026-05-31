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
    """Return True if item matches rule.

    Each ConditionGroup is evaluated independently (conditions combined with
    the group's own operator). Groups are then combined with rule.operator.
    Disabled rules always return False.
    """
    if not rule.enabled or not rule.condition_groups:
        return False

    group_results = []
    for group in rule.condition_groups:
        cond_results = [evaluate_condition(c, item) for c in group.conditions]
        if group.operator == RuleOperator.AND:
            group_results.append(all(cond_results))
        else:
            group_results.append(any(cond_results))

    if rule.operator == RuleOperator.AND:
        return all(group_results)
    return any(group_results)
