"""Structural parity SQLite <-> MariaDB (no container needed).

test_schema_parity.py only compares column NAMES. This compares, per common
table: normalized type class (text/int/float), NOT NULL, normalized DEFAULT,
PK, UNIQUE sets, FK (+ON DELETE), AUTO_INCREMENT, and the index set
(_SQLITE_INDEXES vs MARIADB_INDEXES).

Every difference is a key like ``("column.default", "refresh_tokens.created_at")``
with the two values. A difference must be either:
  * in ALLOWED (legitimate dialect difference, reason written next to it), or
  * in KNOWN_DRIFT (real drift that exists today; NOT fixed here — covered by
    a strict xfail so that fixing it forces removal of the entry).
Anything else fails the test. Stale ALLOWED/KNOWN_DRIFT entries fail too
(a guard that cannot fail is not a guard).

Limits (not compared): VARCHAR length, charset/collation, engine — see
scripts/schema_drift_report.py for the live comparison against real MariaDB.
"""
import pytest

from backend.db.schema import _SQLITE_INDEXES, _TABLES
from backend.db.schema_mariadb import MARIADB_INDEXES, MARIADB_TABLES

from tests.schema_introspect import (
    NOW_MARKER, mariadb_indexes, mariadb_models, sqlite_models,
)

# ── Legitimate differences ──────────────────────────────────────────────────
# key -> reason. Key = (aspect, subject).
ALLOWED = {
    ("column.default", "refresh_tokens.created_at"):
        "SQLite DEFAULT (strftime(now)); MariaDB DDL has no server-side clock "
        "default by design (schema_mariadb.py header) — the app supplies the timestamp",
    ("column.default", "expert_rules.created_at"):
        "same as refresh_tokens.created_at (strftime default vs app-supplied)",
    ("column.default", "notifications.sent_at"):
        "same as refresh_tokens.created_at (strftime default vs app-supplied)",
    ("index.missing_in_sqlite", "refresh_tokens(user_id)"):
        "InnoDB needs an index on an FK column (idx_rt_user); SQLite does not "
        "and the FK lookup on refresh_tokens.user_id is tiny",
    ("index.name", "idx_media_library_id|idx_media_lib_id"):
        "cosmetic: same table/columns, different name; no code references either "
        "(only tests/test_v240_fixes.py checks the SQLite name)",
    ("index.name", "idx_ignored_emby_id|idx_ignored_emby"):
        "cosmetic: same table/columns, different name",
}

# ── Real drifts existing today (documented, not fixed by this test) ─────────
KNOWN_DRIFT = {
    ("column.type", "expert_rules.library_id"):
        "SQLite INTEGER vs MariaDB VARCHAR(255) while every other library_id "
        "(libraries.id, media_queue, seerr_user_rules) is TEXT/VARCHAR; SQLite "
        "INTEGER affinity would coerce numeric-looking ids",
    ("column.not_null", "expert_rules.created_at"):
        "SQLite NOT NULL (with default), MariaDB nullable -> a NULL created_at "
        "is possible on MariaDB only",
    ("column.not_null", "notifications.sent_at"):
        "SQLite NOT NULL (with default), MariaDB nullable -> a NULL sent_at "
        "is possible on MariaDB only",
    ("column.default", "media_queue.plex_rating_key"):
        "SQLite DEFAULT '' (DDL + ALTER), MariaDB DEFAULT NULL -> rows inserted "
        "without the column read '' on SQLite, NULL on MariaDB",
}


def _diff(sqlite_m, sqlite_idx, maria_m, maria_idx):
    """-> {key: (sqlite_value, mariadb_value)} for every difference."""
    out = {}
    for table in sorted(set(sqlite_m) | set(maria_m)):
        if table not in sqlite_m or table not in maria_m:
            out[("table.presence", table)] = (table in sqlite_m, table in maria_m)
            continue
        a, b = sqlite_m[table], maria_m[table]
        for col in sorted(set(a.columns) | set(b.columns)):
            x, y = a.columns.get(col), b.columns.get(col)
            subject = f"{table}.{col}"
            if x is None or y is None:
                out[("column.presence", subject)] = (x is not None, y is not None)
                continue
            for aspect in ("type_class", "not_null", "default", "autoinc"):
                vx, vy = getattr(x, aspect), getattr(y, aspect)
                if vx != vy:
                    key_aspect = {"type_class": "column.type"}.get(aspect, f"column.{aspect}")
                    out[(key_aspect, subject)] = (vx, vy)
        for aspect, vx, vy in (
            ("table.pk", a.pk, b.pk),
            ("table.unique", sorted(a.uniques), sorted(b.uniques)),
            ("table.fk", sorted(a.fks), sorted(b.fks)),
        ):
            if vx != vy:
                out[(aspect, table)] = (vx, vy)

    shape = lambda idx: {(t, c, u): n for n, t, c, u in idx}  # noqa: E731
    s_idx, m_idx = shape(sqlite_idx), shape(maria_idx)
    for t, c, u in sorted(set(s_idx) - set(m_idx)):
        out[("index.missing_in_mariadb", f"{t}({','.join(c)})")] = (s_idx[(t, c, u)], None)
    for t, c, u in sorted(set(m_idx) - set(s_idx)):
        out[("index.missing_in_sqlite", f"{t}({','.join(c)})")] = (None, m_idx[(t, c, u)])
    for shared in sorted(set(s_idx) & set(m_idx)):
        if s_idx[shared] != m_idx[shared]:
            out[("index.name", f"{s_idx[shared]}|{m_idx[shared]}")] = (s_idx[shared], m_idx[shared])
    return out


@pytest.fixture(scope="module")
def diffs():
    s_models, s_idx = sqlite_models(_TABLES, _SQLITE_INDEXES)
    return _diff(s_models, s_idx, mariadb_models(MARIADB_TABLES), mariadb_indexes(MARIADB_INDEXES))


def test_no_unexplained_structural_difference(diffs):
    unexplained = {k: v for k, v in diffs.items() if k not in ALLOWED and k not in KNOWN_DRIFT}
    assert not unexplained, (
        "SQLite/MariaDB structural divergence not in ALLOWED/KNOWN_DRIFT:\n"
        + "\n".join(f"  {k}: sqlite={v[0]!r} mariadb={v[1]!r}" for k, v in sorted(unexplained.items()))
    )


def test_allowlists_have_no_stale_entries(diffs):
    stale = [k for k in {**ALLOWED, **KNOWN_DRIFT} if k not in diffs]
    assert not stale, f"ALLOWED/KNOWN_DRIFT entries no longer observed, remove them: {stale}"


@pytest.mark.parametrize(
    "key",
    [
        pytest.param(k, id="-".join(k), marks=pytest.mark.xfail(
            strict=True, reason=f"known drift: {why}"))
        for k, why in sorted(KNOWN_DRIFT.items())
    ],
)
def test_known_drift_is_resolved(diffs, key):
    """XFAIL(strict) while the drift exists; XPASS (= failure) once fixed, so the
    KNOWN_DRIFT entry gets removed and the guard tightens."""
    assert key not in diffs


def test_now_default_only_allowed_where_listed(diffs):
    """A NOW default on one side only is legitimate solely for the listed columns."""
    for (aspect, subject), (vx, vy) in diffs.items():
        if aspect == "column.default" and NOW_MARKER in (vx, vy):
            assert (aspect, subject) in ALLOWED, subject


def test_parser_sanity_on_known_columns():
    """The MariaDB DDL parser must see what we know is there (guards against a
    parser that silently reads nothing and makes every comparison vacuous)."""
    m = mariadb_models(MARIADB_TABLES)
    assert m["settings"].pk == ("key",)
    assert m["refresh_tokens"].fks == {("user_id", "users", "id", "CASCADE")}
    assert m["notifications"].uniques == {("media_id", "threshold")}
    assert m["libraries"].columns["conditions"].default == "[]"
    assert m["media_queue"].columns["id"].autoinc is True
    assert m["media_queue"].columns["status"].not_null is True
    s, _ = sqlite_models(_TABLES, _SQLITE_INDEXES)
    assert s["settings"].pk == ("key",)
    assert s["refresh_tokens"].fks == {("user_id", "users", "id", "CASCADE")}
    assert s["media_queue"].columns["id"].autoinc is True
    assert sum(len(t.columns) for t in m.values()) > 100
