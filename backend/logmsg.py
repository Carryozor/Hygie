# backend/logmsg.py
"""Translated log message strings loaded from backend/locales/{lang}.json.

Usage:
    from .logmsg import lm
    await add_log("INFO", lm("scan.done", n=5), "job")

To add a message: edit backend/locales/fr.json (and other languages).
No Python code changes required.
"""
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCALE_DIR = Path(__file__).parent / "locales"
_cache: dict[str, dict[str, str]] = {}


def _load(lang: str) -> dict[str, str]:
    if lang not in _cache:
        path = _LOCALE_DIR / f"{lang}.json"
        try:
            _cache[lang] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            _cache[lang] = {}
    return _cache[lang]


def lm(key: str, **params: Any) -> str:
    """Return a translated log message for the current UI language."""
    from .db.settings_store import get_language_sync
    lang = get_language_sync()
    msgs = _load(lang)
    if not msgs:
        msgs = _load("fr")
    template = msgs.get(key) or _load("fr").get(key, key)
    if params:
        try:
            return template.format_map(params)
        except Exception:
            return template
    return template
