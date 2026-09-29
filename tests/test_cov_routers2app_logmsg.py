"""Coverage tests for backend/logmsg.py — translated log message lookup.

Covers: locale file load failure (falls back to empty dict, cached), the
current-language file being empty (falls back to "fr"), and a bad format
string (falls back to the raw template instead of raising).
"""
from unittest.mock import patch

import backend.logmsg as logmsg_mod


def _reset_cache():
    logmsg_mod._cache.clear()


def test_load_returns_empty_dict_and_caches_it_when_file_read_fails():
    _reset_cache()
    with patch.object(logmsg_mod.Path, "read_text", side_effect=OSError("not found")):
        result = logmsg_mod._load("xx")
    assert result == {}
    assert logmsg_mod._cache["xx"] == {}


def test_load_returns_cached_value_without_rereading_file():
    _reset_cache()
    logmsg_mod._cache["fr"] = {"already": "cached"}
    with patch.object(logmsg_mod.Path, "read_text") as mock_read:
        result = logmsg_mod._load("fr")
    assert result == {"already": "cached"}
    mock_read.assert_not_called()


def test_lm_falls_back_to_fr_when_current_language_file_is_empty():
    _reset_cache()
    with patch("backend.db.settings_store.get_language_sync", return_value="xx"), \
         patch.object(logmsg_mod, "_load", side_effect=lambda lang: {} if lang == "xx" else {"scan.done": "Scan done: {n}"}):
        result = logmsg_mod.lm("scan.done", n=5)
    assert result == "Scan done: 5"


def test_lm_returns_key_itself_when_message_missing_everywhere():
    _reset_cache()
    with patch("backend.db.settings_store.get_language_sync", return_value="fr"), \
         patch.object(logmsg_mod, "_load", return_value={}):
        result = logmsg_mod.lm("unknown.key")
    assert result == "unknown.key"


def test_lm_returns_raw_template_when_format_map_raises():
    _reset_cache()
    with patch("backend.db.settings_store.get_language_sync", return_value="fr"), \
         patch.object(logmsg_mod, "_load", return_value={"bad.template": "Missing {undeclared_field}"}):
        result = logmsg_mod.lm("bad.template", n=5)
    assert result == "Missing {undeclared_field}"


def test_lm_returns_template_unchanged_when_no_params_given():
    _reset_cache()
    with patch("backend.db.settings_store.get_language_sync", return_value="fr"), \
         patch.object(logmsg_mod, "_load", return_value={"plain.msg": "No placeholders here"}):
        result = logmsg_mod.lm("plain.msg")
    assert result == "No placeholders here"
