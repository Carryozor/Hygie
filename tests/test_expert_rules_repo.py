import pytest
import pytest_asyncio
from backend.db.schema import init_db
from backend.db.repositories import (
    save_expert_rule, get_expert_rules, delete_expert_rule, get_expert_rule_by_id,
)
from backend.rules.models import (
    ExpertRule, Condition, ConditionField, ConditionOp, RuleOperator, RuleAction,
)

_RULE = ExpertRule(
    name="Test rule",
    conditions=[Condition(field=ConditionField.DAYS_NOT_WATCHED, op=ConditionOp.GT, value=365)],
)

@pytest_asyncio.fixture
async def db_path(monkeypatch, tmp_path):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr("backend.db.utils.DB_PATH", path)
    monkeypatch.setattr("backend.db.schema.DB_PATH", path)
    await init_db()
    return path

@pytest.mark.asyncio
async def test_save_and_get_expert_rules(db_path):
    await save_expert_rule(_RULE, db_path=db_path)
    rules = await get_expert_rules(db_path=db_path)
    assert len(rules) == 1
    assert rules[0].name == "Test rule"
    assert len(rules[0].conditions) == 1

@pytest.mark.asyncio
async def test_save_returns_id(db_path):
    rule_id = await save_expert_rule(_RULE, db_path=db_path)
    assert isinstance(rule_id, int) and rule_id > 0

@pytest.mark.asyncio
async def test_update_expert_rule(db_path):
    rule_id = await save_expert_rule(_RULE, db_path=db_path)
    updated = _RULE.model_copy(update={"id": rule_id, "name": "Updated"})
    await save_expert_rule(updated, db_path=db_path)
    rules = await get_expert_rules(db_path=db_path)
    assert len(rules) == 1
    assert rules[0].name == "Updated"

@pytest.mark.asyncio
async def test_delete_expert_rule(db_path):
    rule_id = await save_expert_rule(_RULE, db_path=db_path)
    await delete_expert_rule(rule_id, db_path=db_path)
    rules = await get_expert_rules(db_path=db_path)
    assert len(rules) == 0

@pytest.mark.asyncio
async def test_get_by_id(db_path):
    rule_id = await save_expert_rule(_RULE, db_path=db_path)
    rule = await get_expert_rule_by_id(rule_id, db_path=db_path)
    assert rule is not None
    assert rule.id == rule_id

@pytest.mark.asyncio
async def test_get_by_id_not_found(db_path):
    rule = await get_expert_rule_by_id(999, db_path=db_path)
    assert rule is None

@pytest.mark.asyncio
async def test_only_enabled_by_default(db_path):
    await save_expert_rule(_RULE, db_path=db_path)
    disabled = _RULE.model_copy(update={"name": "Disabled", "enabled": False})
    await save_expert_rule(disabled, db_path=db_path)
    rules = await get_expert_rules(db_path=db_path, enabled_only=True)
    assert len(rules) == 1
    assert rules[0].name == "Test rule"
