"""Tests for DbConn._q() — regex must NOT replace ? inside string literals."""
import pytest
from backend.db.engine import DbConn


def _q(sql: str) -> str:
    """Exercise the private _q method via a throw-away DbConn."""
    conn = DbConn.__new__(DbConn)
    conn._dialect = "mariadb"
    return conn._q(sql)


# ─── Paramètre normal ─────────────────────────────────────────────────────────

def test_q_replaces_plain_param():
    assert _q("SELECT * FROM t WHERE id = ?") == "SELECT * FROM t WHERE id = %s"


def test_q_replaces_multiple_params():
    assert _q("INSERT INTO t (a, b) VALUES (?, ?)") == "INSERT INTO t (a, b) VALUES (%s, %s)"


def test_q_no_op_for_sqlite():
    conn = DbConn.__new__(DbConn)
    conn._dialect = "sqlite"
    assert conn._q("SELECT ? FROM t") == "SELECT ? FROM t"


# ─── Littéraux de chaîne — ne doivent PAS être remplacés ─────────────────────

def test_q_preserves_question_mark_in_single_quoted_literal():
    sql = "SELECT * FROM t WHERE value LIKE '?%'"
    result = _q(sql)
    assert "'?%'" in result, f"Literal '?' must stay intact: {result}"
    assert "%s" not in result


def test_q_preserves_question_mark_in_double_quoted_literal():
    sql = 'SELECT * FROM t WHERE note = "has ? inside"'
    result = _q(sql)
    assert '"has ? inside"' in result
    assert "%s" not in result


def test_q_mixed_literal_and_param():
    sql = "SELECT * FROM t WHERE value LIKE '?%' AND id = ?"
    result = _q(sql)
    assert "'?%'" in result
    assert result.endswith("%s")
    assert result.count("%s") == 1


def test_q_json_with_question_mark():
    sql = "SELECT * FROM t WHERE json_col = '{\"key\": \"val?\"}' AND id = ?"
    result = _q(sql)
    assert result.count("%s") == 1  # Only the parameter placeholder
    assert "val?" in result


# ─── Escaped quotes inside strings ────────────────────────────────────────────

def test_q_handles_escaped_quote_in_literal():
    sql = "SELECT * FROM t WHERE v = 'it\\'s ? here' AND id = ?"
    # The escaped quote ends the literal — behavior may vary, but must not crash
    result = _q(sql)
    assert isinstance(result, str)
