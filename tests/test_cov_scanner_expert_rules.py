"""Coverage tests for backend/scanner/_expert_rules.py.

Targets branches not exercised by tests/test_rules_engine.py's smoke test of
_build_item_data/_build_plex_item_data: the "watched" (last_played present)
branch of days_not_watched, the file-size-from-MediaSources branch, and the
library-scoping logic in _evaluate_expert_rules (a rule scoped to another
library must NOT fire, whether scoped via the modern `library_ids` list or
the legacy singular `library_id`).
"""
from datetime import datetime, timedelta, timezone

from backend.scanner._expert_rules import (
    _build_item_data, _build_plex_item_data, _evaluate_expert_rules,
)
from backend.rules.models import (
    Condition, ConditionField, ConditionGroup, ConditionOp, ExpertRule, RuleAction, RuleOperator,
)


def _now():
    return datetime.now(timezone.utc)


# ─── _build_item_data ───────────────────────────────────────────────────────────

def test_build_item_data_days_not_watched_uses_last_played_when_present():
    last_played = _now() - timedelta(days=10)
    added_date = _now() - timedelta(days=100)
    data = _build_item_data({"Type": "Movie"}, 3, last_played, added_date)
    assert data["days_not_watched"] == 10
    assert data["added_days_ago"] == 100
    assert data["never_watched"] == 0


def test_build_item_data_computes_file_size_from_media_sources():
    item = {"Type": "Movie", "MediaSources": [{"Size": 3 * 1024 ** 3}]}
    data = _build_item_data(item, 0, None, None)
    assert data["file_size_gb"] == 3.0


def test_build_item_data_zero_size_when_media_sources_missing():
    data = _build_item_data({"Type": "Movie"}, 0, None, None)
    assert data["file_size_gb"] == 0.0


def test_build_item_data_days_not_watched_falls_back_to_added_date_when_never_played():
    added_date = _now() - timedelta(days=42)
    data = _build_item_data({"Type": "Movie"}, 0, None, added_date)
    assert data["days_not_watched"] == 42
    assert data["never_watched"] == 1


# ─── _build_plex_item_data ───────────────────────────────────────────────────────

def test_build_plex_item_data_uses_last_viewed_at_when_present():
    last_viewed = _now() - timedelta(days=5)
    added = _now() - timedelta(days=200)
    item = {
        "title": "x", "view_count": 2,
        "last_viewed_at": last_viewed.isoformat(), "added_at": added.isoformat(),
    }
    data = _build_plex_item_data(item)
    assert data["days_not_watched"] == 5
    assert data["added_days_ago"] == 200
    assert data["never_watched"] == 0


def test_build_plex_item_data_falls_back_to_added_at_when_never_viewed():
    added = _now() - timedelta(days=15)
    item = {"title": "x", "view_count": 0, "added_at": added.isoformat()}
    data = _build_plex_item_data(item)
    assert data["days_not_watched"] == 15
    assert data["never_watched"] == 1


# ─── _evaluate_expert_rules — library scoping ───────────────────────────────────

def _always_match_rule(**kwargs) -> ExpertRule:
    return ExpertRule(
        name="always-match",
        condition_groups=[ConditionGroup(conditions=[
            Condition(field=ConditionField.PLAY_COUNT, op=ConditionOp.GTE, value=0),
        ])],
        operator=RuleOperator.AND,
        action=RuleAction.QUEUE,
        enabled=True,
        **kwargs,
    )


async def test_evaluate_expert_rules_skips_rule_scoped_to_other_library_via_library_ids():
    rule = _always_match_rule(library_ids=["lib-other"])
    action, grace = await _evaluate_expert_rules(
        {"play_count": 5}, library_id="lib-this", rules_cache=[rule],
    )
    assert action is None


async def test_evaluate_expert_rules_matches_when_library_id_in_library_ids():
    rule = _always_match_rule(library_ids=["lib-this", "lib-other"], grace_days=3)
    action, grace = await _evaluate_expert_rules(
        {"play_count": 5}, library_id="lib-this", rules_cache=[rule],
    )
    assert action == "queue"
    assert grace == 3


async def test_evaluate_expert_rules_skips_rule_scoped_to_other_library_via_singular_id():
    rule = _always_match_rule(library_id="lib-other")
    action, grace = await _evaluate_expert_rules(
        {"play_count": 5}, library_id="lib-this", rules_cache=[rule],
    )
    assert action is None


async def test_evaluate_expert_rules_matches_when_singular_library_id_equal():
    rule = _always_match_rule(library_id="lib-this")
    action, grace = await _evaluate_expert_rules(
        {"play_count": 5}, library_id="lib-this", rules_cache=[rule],
    )
    assert action == "queue"


async def test_evaluate_expert_rules_global_rule_matches_any_library():
    """A rule with no library scoping (library_id and library_ids both None)
    must fire regardless of which library the item is in."""
    rule = _always_match_rule()
    action, grace = await _evaluate_expert_rules(
        {"play_count": 5}, library_id="lib-anything", rules_cache=[rule],
    )
    assert action == "queue"


async def test_evaluate_expert_rules_returns_none_when_no_rule_matches():
    rule = ExpertRule(
        name="never-match",
        condition_groups=[ConditionGroup(conditions=[
            Condition(field=ConditionField.PLAY_COUNT, op=ConditionOp.EQ, value=999),
        ])],
        action=RuleAction.QUEUE,
    )
    action, grace = await _evaluate_expert_rules(
        {"play_count": 0}, library_id="lib1", rules_cache=[rule],
    )
    assert action is None
    assert grace == 7
