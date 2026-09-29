"""Coverage tests for backend/rules/engine.py — the unified condition/rule
comparison engine used by both expert rules and legacy library conditions.

Targets branches not exercised by tests/test_rules_engine.py: IN/NOT_IN with
a None actual value, a comparison that raises (must fail safe, not crash the
scan), and a condition group with an empty conditions list mixed into a rule
with other groups.
"""
from backend.rules.engine import evaluate_condition
from backend.rules.models import Condition, ConditionField, ConditionOp


def test_evaluate_condition_in_with_none_actual_never_matches():
    """A field with no value must never satisfy an IN inclusion list."""
    c = Condition(field=ConditionField.SEERR_USER_ID, op=ConditionOp.IN, value=[42, 99])
    assert evaluate_condition(c, {"seerr_user_id": None}) is False


def test_evaluate_condition_not_in_with_none_actual_never_matches():
    """A field with no value must never satisfy a NOT_IN exclusion check —
    'no value' is not something to positively exclude/include on."""
    c = Condition(field=ConditionField.SEERR_USER_ID, op=ConditionOp.NOT_IN, value=[1, 2])
    assert evaluate_condition(c, {"seerr_user_id": None}) is False


def test_evaluate_condition_swallows_comparison_exception():
    """A numeric operator comparing incompatible types (e.g. str vs int) must
    fail safe (return False) instead of crashing the whole scan."""
    c = Condition(field=ConditionField.RATING, op=ConditionOp.GT, value=3.0)
    result = evaluate_condition(c, {"rating": "not-a-number"})
    assert result is False


# NOTE: engine.py's `if not group.conditions: continue` (an empty-conditions
# guard on a ConditionGroup) is not exercised here. ConditionGroup.conditions
# is declared `Field(..., min_length=1)` in backend/rules/models.py, and every
# construction path in the codebase (legacy_conditions.py, db/repositories.py)
# goes through that Pydantic validator — an empty-conditions group cannot
# exist in practice without bypassing validation (e.g. model_construct(),
# unused anywhere in this codebase). Confirmed: ConditionGroup(conditions=[])
# raises pydantic.ValidationError ("List should have at least 1 item").
# Left uncovered as genuinely unreachable defensive code.
