#!/usr/bin/env python3
"""i18n lint: report t() calls not in en.json, and en.json keys unused in JS/HTML."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
I18N_FILE = ROOT / "frontend/static/i18n/en.json"
JS_DIRS = [ROOT / "frontend/static/js"]
HTML_DIRS = [ROOT / "frontend/templates"]


def load_keys() -> set[str]:
    with I18N_FILE.open(encoding="utf-8") as f:
        return set(json.load(f).keys())


def find_t_calls(dirs: list[Path], globs: list[str]) -> set[str]:
    """Find all t('...') and t("...") string literals."""
    # Match t('...') or t("...") — single or double quotes
    # Must be preceded by whitespace, operator, or start of line
    # Avoid matching toast(), cat(), etc.
    pattern = re.compile(r"""(?<![a-zA-Z0-9_])t\(\s*['"](.+?)['"]\s*\)""")
    found = set()
    for d in dirs:
        for glob in globs:
            for f in d.rglob(glob):
                try:
                    content = f.read_text(encoding="utf-8")
                    for match in pattern.findall(content):
                        # Unescape only JavaScript single escape sequences: \' → ', \" → "
                        # Don't use unicode-escape as it corrupts UTF-8 strings
                        unescaped = match.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")
                        found.add(unescaped)
                except Exception:
                    pass
    return found


def find_data_i18n(dirs: list[Path]) -> set[str]:
    """Find all data-i18n="..." attribute values in HTML files."""
    pattern = re.compile(r"""data-i18n=["'](.+?)["']""")
    found = set()
    for d in dirs:
        for f in d.rglob("*.html"):
            try:
                found.update(pattern.findall(f.read_text(encoding="utf-8")))
            except Exception:
                pass
    return found


def main() -> int:
    keys = load_keys()

    t_in_js = find_t_calls(JS_DIRS, ["*.js"])
    t_in_html = find_t_calls(HTML_DIRS, ["*.html"])
    data_i18n = find_data_i18n(HTML_DIRS)

    all_used = t_in_js | t_in_html | data_i18n

    missing = sorted(all_used - keys)
    unused = sorted(keys - all_used)

    rc = 0
    if missing:
        print(f"\n❌ {len(missing)} key(s) used in JS/HTML but NOT in en.json:")
        for k in missing:
            print(f"   {k!r}")
        rc = 1
    else:
        print("✅ All t() calls have a matching key in en.json")

    if unused:
        print(f"\n⚠️  {len(unused)} key(s) in en.json NOT used in JS/HTML:")
        for k in unused:
            print(f"   {k!r}")
    else:
        print("✅ All en.json keys are used")

    return rc


if __name__ == "__main__":
    sys.exit(main())
