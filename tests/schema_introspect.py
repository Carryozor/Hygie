"""Structural schema introspection for the SQLite<->MariaDB parity tests.

Both dialects are reduced to the same normalized model (see ``TableModel``):
SQLite by really executing the DDL in an in-memory database and reading the
PRAGMAs (so what is compared is what SQLite actually built, including
columns added by ``_TABLES[*][2]`` ALTERs); MariaDB by parsing the DDL text
(no server needed).
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

NOW_MARKER = "<now>"  # any strftime('now') / CURRENT_TIMESTAMP default


@dataclass
class Column:
    type_class: str          # text | int | float | other:<raw>
    not_null: bool           # PK columns are always NOT NULL
    default: str | None      # normalized; None = no default / DEFAULT NULL
    autoinc: bool = False


@dataclass
class TableModel:
    columns: dict[str, Column] = field(default_factory=dict)
    pk: tuple[str, ...] = ()
    uniques: set[tuple[str, ...]] = field(default_factory=set)
    # (col, ref_table, ref_col, on_delete)
    fks: set[tuple[str, str, str, str]] = field(default_factory=set)


_TYPE_CLASSES = {
    "TEXT": "text", "VARCHAR": "text", "LONGTEXT": "text", "CHAR": "text",
    "INTEGER": "int", "INT": "int", "TINYINT": "int", "BIGINT": "int",
    "SMALLINT": "int",
    "REAL": "float", "DOUBLE": "float", "FLOAT": "float",
}


def type_class(raw: str) -> str:
    base = re.sub(r"\(.*\)", "", raw).strip().upper()
    return _TYPE_CLASSES.get(base, f"other:{base}")


def normalize_default(raw: str | None) -> str | None:
    """'x' / ('x') / x / NULL / strftime(..) -> comparable string."""
    if raw is None:
        return None
    v = raw.strip()
    while v.startswith("(") and v.endswith(")"):
        v = v[1:-1].strip()
    if v.upper() == "NULL":
        return None
    if "strftime" in v.lower() or v.upper().startswith("CURRENT_TIMESTAMP"):
        return NOW_MARKER
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1]
    return v


def normalize_on_delete(raw: str | None) -> str:
    v = (raw or "NO ACTION").strip().upper()
    return "NO ACTION" if v == "RESTRICT" else v


def _cols(expr: str) -> tuple[str, ...]:
    out = []
    for part in expr.split(","):
        name = re.sub(r"\s+(ASC|DESC)$", "", part.strip(), flags=re.IGNORECASE)
        out.append(name.strip("`"))
    return tuple(out)


# ─── SQLite ──────────────────────────────────────────────────────────────────
def sqlite_models(tables, indexes: list[str]):
    """Build the DDL in memory. Returns (models, index_set).

    ``tables`` is schema._TABLES (name, ddl, alter_cols); ``index_set`` is a
    set of (index_name, table, cols, unique) parsed from ``indexes``.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        for _name, ddl, _alter in tables:
            conn.execute(ddl)
        for name, _ddl, alter in tables:
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({name})")}
            for col, definition in alter:
                if col not in have:
                    conn.execute(f"ALTER TABLE {name} ADD COLUMN {col} {definition}")
        models = {name: _sqlite_model(conn, name, ddl) for name, ddl, _ in tables}
    finally:
        conn.close()
    return models, {_parse_index(sql) for sql in indexes}


def _sqlite_model(conn, table: str, ddl: str) -> TableModel:
    model = TableModel()
    pk_cols: dict[int, str] = {}
    for _cid, name, ctype, notnull, dflt, pk in conn.execute(f"PRAGMA table_info({table})"):
        if pk:
            pk_cols[pk] = name
        model.columns[name] = Column(
            type_class=type_class(ctype),
            not_null=bool(notnull) or bool(pk),
            default=normalize_default(dflt),
        )
    model.pk = tuple(pk_cols[k] for k in sorted(pk_cols))
    if "AUTOINCREMENT" in ddl.upper() and len(model.pk) == 1:
        model.columns[model.pk[0]].autoinc = True
    for _seq, iname, unique, origin, _partial in conn.execute(f"PRAGMA index_list({table})"):
        if unique and origin == "u":
            cols = tuple(r[2] for r in conn.execute(f"PRAGMA index_info({iname})"))
            model.uniques.add(cols)
    for row in conn.execute(f"PRAGMA foreign_key_list({table})"):
        _id, _seq, ref_table, frm, to, _upd, on_del, _match = row
        model.fks.add((frm, ref_table, to, normalize_on_delete(on_del)))
    return model


_INDEX_RE = re.compile(
    r"CREATE\s+(UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+ON\s+(\w+)\s*\(([^)]*)\)",
    re.IGNORECASE,
)


def _parse_index(sql: str) -> tuple[str, str, tuple[str, ...], bool]:
    """-> (index_name, table, columns, unique)."""
    m = _INDEX_RE.match(sql.strip())
    if not m:
        raise ValueError(f"unparseable index DDL: {sql!r}")
    return m.group(2), m.group(3), _cols(m.group(4)), bool(m.group(1))


# ─── MariaDB (DDL text) ──────────────────────────────────────────────────────
def _split_top_level(body: str) -> list[str]:
    parts, depth, quote, cur = [], 0, None, []
    for ch in body:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


_FK_RE = re.compile(
    r"CONSTRAINT\s+\w+\s+FOREIGN\s+KEY\s*\((\w+)\)\s*REFERENCES\s+(\w+)\s*\((\w+)\)"
    r"(?:\s+ON\s+DELETE\s+(CASCADE|SET NULL|RESTRICT|NO ACTION))?",
    re.IGNORECASE,
)
_COL_RE = re.compile(r"^`?(\w+)`?\s+(\w+(?:\([^)]*\))?)\s*(.*)$", re.DOTALL)
_DEFAULT_RE = re.compile(
    r"\bDEFAULT\s+(\('(?:[^']|'')*'\)|'(?:[^']|'')*'|\([^)]*\)|\S+)", re.IGNORECASE
)


def mariadb_model(ddl: str) -> TableModel:
    m = re.search(r"CREATE TABLE IF NOT EXISTS\s+\w+\s*\((.*)\)\s*ENGINE=", ddl, re.DOTALL)
    if not m:
        raise ValueError("unparseable MariaDB DDL")
    model = TableModel()
    for item in _split_top_level(m.group(1)):
        upper = item.upper()
        if upper.startswith("PRIMARY KEY"):
            model.pk = _cols(re.search(r"\((.*)\)", item).group(1))
        elif upper.startswith(("UNIQUE KEY", "UNIQUE INDEX")):
            model.uniques.add(_cols(re.search(r"\((.*)\)", item).group(1)))
        elif upper.startswith(("KEY ", "INDEX ")):
            continue  # inline non-unique index: not used by the DDL today
        elif upper.startswith("CONSTRAINT"):
            fk = _FK_RE.match(item)
            if not fk:
                raise ValueError(f"unparseable constraint: {item!r}")
            model.fks.add((fk[1], fk[2], fk[3], normalize_on_delete(fk[4])))
        else:
            cm = _COL_RE.match(item)
            name, raw_type, rest = cm[1], cm[2], cm[3]
            dm = _DEFAULT_RE.search(rest)
            model.columns[name] = Column(
                type_class=type_class(raw_type),
                not_null=bool(re.search(r"\bNOT\s+NULL\b", rest, re.IGNORECASE)),
                default=normalize_default(dm.group(1)) if dm else None,
                autoinc="AUTO_INCREMENT" in rest.upper(),
            )
    for c in model.pk:
        model.columns[c].not_null = True
    return model


def mariadb_models(tables) -> dict[str, TableModel]:
    return {name: mariadb_model(ddl) for name, ddl, *_ in tables}


def mariadb_indexes(indexes: list[str]) -> set:
    return {_parse_index(sql) for sql in indexes}
