"""Pure-logic tests for scripts/schema_drift_report.py (no docker)."""
import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "schema_drift_report",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "schema_drift_report.py"),
)
report = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(report)

BASE = "\n".join([
    "VERSION\t11.4.0",
    "SCHEMA\tutf8mb4\tutf8mb4_general_ci",
    "TABLE\tt\tInnoDB\tutf8mb4_general_ci\tDynamic",
    "COL\tt\tid\t1\tint(11)\tNO\t<NULL>\tauto_increment\t\t",
    "COL\tt\tname\t2\tvarchar(255)\tNO\t<NULL>\t\tutf8mb4\tutf8mb4_general_ci",
    "IDX\tt\tPRIMARY\t0\t1\tid\tBTREE",
    "IDX\tt\tidx_name\t1\t1\tname\tBTREE",
    "FK\tt\tfk_x\tid\tu\tid\tCASCADE\tRESTRICT",
])


def test_identical_schemas_have_no_diff():
    assert report.diff_schemas(report.parse_introspection(BASE), report.parse_introspection(BASE)) == []


def test_each_kind_of_difference_is_reported():
    mutated = (BASE
               .replace("varchar(255)\tNO", "text\tYES")
               .replace("InnoDB", "MyISAM")
               .replace("IDX\tt\tidx_name\t1\t1\tname\tBTREE", "")
               .replace("CASCADE", "SET NULL")
               .replace("COL\tt\tid\t1", "COL\tt\tid\t3"))
    lines = report.diff_schemas(report.parse_introspection(mutated), report.parse_introspection(BASE))
    text = "\n".join(lines)
    for expected in ("t.name.type", "t.name.nullable", "t.engine", "idx_name", "[fk] t.fk_x", "[order] t"):
        assert expected in text, (expected, text)


def test_table_only_on_one_side():
    extra = BASE + "\nTABLE\tzz\tInnoDB\tc\tDynamic"
    lines = report.diff_schemas(report.parse_introspection(extra), report.parse_introspection(BASE))
    assert "[table] only in PROD: zz" in lines
