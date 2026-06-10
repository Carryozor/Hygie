import pytest
from backend.rules.models import (
    Condition, ConditionGroup, ExpertRule, RuleOperator,
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
    rule = ExpertRule(
        name="r",
        condition_groups=[ConditionGroup(
            conditions=[
                Condition(field=ConditionField.DAYS_NOT_WATCHED, op=ConditionOp.GT, value=365),
                Condition(field=ConditionField.RATING, op=ConditionOp.LT, value=5.0),
            ],
            operator=RuleOperator.AND,
        )],
        operator=RuleOperator.AND,
    )
    assert evaluate_rule(rule, MOVIE) is True

def test_evaluate_rule_and_partial_match():
    rule = ExpertRule(
        name="r",
        condition_groups=[ConditionGroup(
            conditions=[
                Condition(field=ConditionField.DAYS_NOT_WATCHED, op=ConditionOp.GT, value=365),
                Condition(field=ConditionField.RATING, op=ConditionOp.GT, value=8.0),
            ],
            operator=RuleOperator.AND,
        )],
        operator=RuleOperator.AND,
    )
    assert evaluate_rule(rule, MOVIE) is False

def test_evaluate_rule_or_partial_match():
    rule = ExpertRule(
        name="r",
        condition_groups=[ConditionGroup(
            conditions=[
                Condition(field=ConditionField.DAYS_NOT_WATCHED, op=ConditionOp.GT, value=365),
                Condition(field=ConditionField.RATING, op=ConditionOp.GT, value=8.0),
            ],
            operator=RuleOperator.OR,
        )],
        operator=RuleOperator.OR,
    )
    assert evaluate_rule(rule, MOVIE) is True

def test_evaluate_rule_disabled_always_false():
    rule = ExpertRule(
        name="r",
        enabled=False,
        condition_groups=[ConditionGroup(
            conditions=[
                Condition(field=ConditionField.DAYS_NOT_WATCHED, op=ConditionOp.GT, value=1),
            ],
            operator=RuleOperator.AND,
        )],
    )
    assert evaluate_rule(rule, MOVIE) is False


# ─── never_watched — first-class expert rule field (engine unification) ───────

def test_never_watched_field_matches():
    from backend.rules.models import Condition, ConditionField, ConditionGroup, ConditionOp, ExpertRule
    from backend.rules.engine import evaluate_rule
    rule = ExpertRule(
        name="unwatched only",
        condition_groups=[ConditionGroup(conditions=[
            Condition(field=ConditionField.NEVER_WATCHED, op=ConditionOp.EQ, value=1),
        ])],
    )
    assert evaluate_rule(rule, {"never_watched": 1}) is True
    assert evaluate_rule(rule, {"never_watched": 0}) is False
    assert evaluate_rule(rule, {}) is False  # missing field never matches


def test_item_data_builders_expose_never_watched():
    from backend.scanner._expert_rules import _build_item_data, _build_plex_item_data
    emby_unwatched = _build_item_data({"Type": "Movie"}, 0, None, None)
    assert emby_unwatched["never_watched"] == 1
    emby_watched = _build_item_data({"Type": "Movie"}, 3, None, None)
    assert emby_watched["never_watched"] == 0
    plex_unwatched = _build_plex_item_data({"title": "x", "view_count": 0})
    assert plex_unwatched["never_watched"] == 1
    plex_watched = _build_plex_item_data({"title": "x", "view_count": 2})
    assert plex_watched["never_watched"] == 0
