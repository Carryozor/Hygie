#!/usr/bin/env python3
"""
Check that every Python file using lm() has imported it from logmsg.

Usage:
    python3 scripts/check_lm_imports.py          # check
    python3 scripts/check_lm_imports.py --fix    # auto-add missing imports (dry-run shows what would change)
"""
import pathlib
import re
import sys

BACKEND = pathlib.Path(__file__).parent.parent / "backend"
IMPORT_RE = re.compile(r"from\s+[.\w]*logmsg\s+import\s+lm\b|from\s+logmsg\s+import\s+lm\b")
CALL_RE   = re.compile(r"\blm\(")

FIX = "--fix" in sys.argv

errors = []
fixed  = 0

for py in sorted(BACKEND.rglob("*.py")):
    if "__pycache__" in str(py):
        continue
    src = py.read_text()
    calls = CALL_RE.findall(src)
    if not calls:
        continue
    if IMPORT_RE.search(src):
        continue  # import already present

    rel = py.relative_to(BACKEND.parent)
    errors.append((rel, len(calls)))

    if FIX:
        # Determine relative import depth
        depth = len(py.relative_to(BACKEND).parts) - 1
        imp   = "from " + "." * (depth + 1) + "logmsg import lm"
        # Insert after the last "from .db.logs import" or "from ..db.logs import"
        lines = src.splitlines(keepends=True)
        insert_after = -1
        for i, line in enumerate(lines):
            if "from" in line and "logs import" in line and "add_log" in line:
                insert_after = i
        if insert_after >= 0:
            lines.insert(insert_after + 1, imp + "\n")
            py.write_text("".join(lines))
            print(f"  FIXED  {rel}  ({len(calls)} calls)")
            fixed += 1
        else:
            print(f"  MANUAL {rel}  — could not find insertion point")

if not FIX:
    if errors:
        print("lm() used without import:")
        for rel, n in errors:
            print(f"  {rel}  ({n} calls)")
        sys.exit(1)
    else:
        total = sum(1 for p in BACKEND.rglob("*.py")
                    if "__pycache__" not in str(p) and CALL_RE.search(p.read_text()))
        print(f"OK — {total} file(s) with lm() all have the import")
else:
    if fixed:
        print(f"Fixed {fixed} file(s). Run without --fix to verify.")
    else:
        print("Nothing to fix.")
