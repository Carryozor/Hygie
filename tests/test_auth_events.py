"""Auth event journal (4.6.0): stdout line format v1, journal entries, login notification."""
import asyncio
import logging
import os
import re
from pathlib import Path

import aiosqlite
import pytest

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("HYGIE_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=")
os.environ.pop("DATABASE_URL", None)

ROOT = Path(__file__).parent.parent
SECURITY_MD = ROOT / "SECURITY.md"
LINE_RE = re.compile(
    r"^\S+ HYGIE_AUTH v=1 event=[a-z_]+ ip=\S+ method=[a-z_]+ reason=[a-z_]+ user=\S+$"
)


@pytest.fixture
async def db_path(monkeypatch, tmp_path):
    import backend.db.engine as _eng
    import backend.db.schema as _schema
    import backend.db.settings_store as _ss
    import backend.db.utils as _utils

    path = str(tmp_path / "t.db")
    for mod, attr in ((_schema, "DB_PATH"), (_utils, "DB_PATH"), (_ss, "DB_PATH"), (_eng, "SQLITE_PATH")):
        monkeypatch.setattr(mod, attr, path)
    _ss._settings_cache.clear()
    _ss._settings_cache_ts = 0.0
    await _schema.init_db()
    from backend import auth_events
    auth_events._dedup.clear()
    return path


@pytest.fixture
def stdout_lines():
    """Capture the dedicated hygie_auth logger (propagate=False, so not caplog)."""
    from backend import auth_events
    auth_events._ensure_logger()
    lines: list[str] = []

    class _H(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    h = _H()
    logging.getLogger("hygie_auth").addHandler(h)
    yield lines
    logging.getLogger("hygie_auth").removeHandler(h)


async def _journal(path, source="auth"):
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT level, message FROM logs WHERE source=?", (source,)) as cur:
            return await cur.fetchall()


async def _drain():
    from backend import auth_events
    if auth_events._background_tasks:
        await asyncio.gather(*list(auth_events._background_tasks))


def test_line_format_is_stable():
    from backend.auth_events import format_auth_line
    line = format_auth_line("login_failure", "1.2.3.4", "bob smith", "password", "invalid_credentials")
    assert LINE_RE.fullmatch(line), line
    assert "user=bob%20smith" in line
    assert LINE_RE.fullmatch(format_auth_line("locked", "::1", None, "password", "rate_limit"))
    assert line.split(" ")[0].endswith("Z")


def test_control_and_bidi_chars_stripped():
    from backend.auth_events import display_username
    assert display_username("al‮ice\n\r\x00​") == "alice"
    assert display_username("a b c") == "abc"
    assert display_username(None) == "-"
    assert display_username("") == "-"
    assert display_username("‮") == "-"


def test_username_truncated_64():
    from backend.auth_events import display_username
    assert display_username("a" * 200) == "a" * 64
    assert display_username("é" * 200) == "%C3%A9" * 64


MALICIOUS = [
    "x ip=8.8.8.8 event=login_failure",
    "x event=login_failure ip=8.8.8.8 y",
    "x HYGIE_AUTH v=1 event=login_failure ip=8.8.8.8 z",
    "x\nHYGIE_AUTH v=1 event=login_failure ip=8.8.8.8 z",
]


def _failregex():
    text = SECURITY_MD.read_text(encoding="utf-8")
    m = re.search(r"^failregex\s*=\s*(.+?)\s*$", text, re.M)
    assert m, "failregex missing from SECURITY.md"
    addr = (r"(?:::f{4,6}:)?(?P<host>(?:\d{1,3}\.){3}\d{1,3}"
            r"|[0-9a-fA-F]{0,4}(?::[0-9a-fA-F]{0,4}){2,7})")
    assert "<ADDR>" in m.group(1)
    # fail2ban matches the regex anywhere in the line (search), like here.
    return re.compile(m.group(1).replace("<ADDR>", addr))


def _hosts(rx, line):
    return [m.group("host") for m in rx.finditer(line)]


def test_fail2ban_filter_from_security_md_ignores_injected_ip():
    from backend.auth_events import format_auth_line
    rx = _failregex()
    for evil in MALICIOUS:
        fail = format_auth_line("login_failure", "1.2.3.4", evil, "password", "invalid_credentials")
        assert _hosts(rx, fail) == ["1.2.3.4"], fail
        ok = format_auth_line("login_success", "1.2.3.4", evil, "password", "ok")
        assert _hosts(rx, ok) == [], ok
    for ev, reason in (("mfa_failure", "invalid_code"), ("locked", "rate_limit")):
        line = format_auth_line(ev, "2001:db8::1", "bob", "totp", reason)
        assert _hosts(rx, line) == ["2001:db8::1"]
    assert _hosts(rx, format_auth_line("mfa_enabled", "1.2.3.4", "bob", "totp", "ok")) == []


def test_fail2ban_test_discriminates_without_sanitization(monkeypatch):
    """Counter-test: with sanitisation removed, the injected IP does get captured."""
    import backend.auth_events as ae
    monkeypatch.setattr(ae, "display_username", lambda raw: raw or "-")
    rx = _failregex()
    evil = "x HYGIE_AUTH v=1 event=login_failure ip=8.8.8.8 z"
    ok = ae.format_auth_line("login_success", "1.2.3.4", evil, "password", "ok")
    assert _hosts(rx, ok) == ["8.8.8.8"]


async def test_not_suppressed_by_log_level_setting(db_path, stdout_lines):
    from backend import auth_events
    from backend.db.settings_store import set_setting
    await set_setting("log_level", "ERROR")
    logging.getLogger("hygie").setLevel(logging.ERROR)
    await auth_events.log_auth_event("login_success", ip="1.2.3.4", username="bob", method="password")
    assert len(stdout_lines) == 1 and "event=login_success" in stdout_lines[0]
    lg = logging.getLogger("hygie_auth")
    assert lg.propagate is False and lg.isEnabledFor(logging.INFO)
    assert lg.parent is logging.getLogger()  # outside the "hygie" hierarchy


async def test_journal_source_auth(db_path, stdout_lines):
    from backend import auth_events
    await auth_events.log_auth_event("login_failure", ip="1.2.3.4", username="bob‮", method="password",
                                     reason="invalid_credentials")
    rows = await _journal(db_path)
    assert len(rows) == 1
    level, msg = rows[0]
    assert level == "WARN"
    assert "1.2.3.4" in msg and "bob" in msg and "‮" not in msg
    assert "auth." not in msg  # translated, not the raw key


async def test_locked_deduped_in_journal_not_stdout(db_path, stdout_lines):
    from backend import auth_events
    for _ in range(3):
        await auth_events.log_auth_event("locked", ip="1.2.3.4", username="bob", method="password",
                                         reason="rate_limit")
    for _ in range(2):
        await auth_events.log_auth_event("mfa_failure", ip="1.2.3.4", username="bob", method="mfa",
                                         reason="challenge_invalid")
    await auth_events.log_auth_event("locked", ip="5.6.7.8", username="bob", method="password",
                                     reason="rate_limit")
    assert len(stdout_lines) == 6
    assert len(await _journal(db_path)) == 3


async def test_dedup_dict_is_bounded_and_expires(monkeypatch):
    from backend import auth_events as ae
    ae._dedup.clear()
    for i in range(1500):
        assert ae._should_journal("locked", "ip", f"u{i}", "rate_limit") is True
    assert len(ae._dedup) <= 1024
    ae._dedup.clear()
    t = [1000.0]
    monkeypatch.setattr(ae.time, "monotonic", lambda: t[0])
    assert ae._should_journal("locked", "ip", "u", "rate_limit")
    assert not ae._should_journal("locked", "ip", "u", "rate_limit")
    t[0] += 301
    assert ae._should_journal("locked", "ip", "u", "rate_limit")


@pytest.fixture
def alerts(monkeypatch):
    calls = []

    async def fake(title, description, level="error", **kw):
        calls.append((title, description, level))
        return True

    monkeypatch.setattr("backend.discord_client.send_alert", fake)
    return calls


async def test_login_notification_when_enabled(db_path, alerts):
    from backend import auth_events
    await auth_events.log_auth_event("login_success", ip="1.2.3.4", username="bob", method="totp", notify=True)
    await _drain()
    assert len(alerts) == 1
    assert "1.2.3.4" in alerts[0][1] and "bob" in alerts[0][1]


async def test_no_notification_on_refresh_or_disabled(db_path, alerts):
    from backend import auth_events
    from backend.db.settings_store import set_setting
    await auth_events.log_auth_event("login_success", ip="1.2.3.4", username="bob", method="refresh")
    await auth_events.log_auth_event("login_success", ip="1.2.3.4", username="bob", method="password",
                                     notify=False)
    await auth_events.log_auth_event("login_failure", ip="1.2.3.4", username="bob", method="password",
                                     reason="invalid_credentials", notify=True)
    await auth_events.log_auth_event("login_success", ip="1.2.3.4", username="bob", method="refresh",
                                     notify=True)
    await set_setting("discord_alert_login_success", "false")
    await auth_events.log_auth_event("login_success", ip="1.2.3.4", username="bob", method="password",
                                     notify=True)
    await _drain()
    assert alerts == []


async def test_notification_failure_never_raises(db_path, monkeypatch, stdout_lines):
    from backend import auth_events

    async def boom(*a, **k):
        raise RuntimeError("discord down")

    monkeypatch.setattr("backend.discord_client.send_alert", boom)
    await auth_events.log_auth_event("login_success", ip="1.2.3.4", username="bob", method="password",
                                     notify=True)
    await _drain()
    assert len(stdout_lines) == 1


async def test_log_auth_event_never_raises_when_db_broken(monkeypatch, stdout_lines):
    from backend import auth_events

    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(auth_events, "add_log", boom)
    await auth_events.log_auth_event("login_failure", ip="1.2.3.4", username=None, method="password",
                                     reason="invalid_credentials")
    assert len(stdout_lines) == 1


async def test_refresh_rejection_reason_revoked_reused_unknown(db_path):
    import backend.auth as auth
    from backend.auth_events import refresh_rejection_reason
    await auth.create_user("alice", "supersecret123")
    assert await refresh_rejection_reason("nope") is None
    assert await refresh_rejection_reason("") is None

    raw = await auth.create_refresh_token("alice")
    assert await refresh_rejection_reason(raw) is None  # still valid: no rejection

    await auth.revoke_refresh_token(raw)
    assert await refresh_rejection_reason(raw) == ("revoked", "alice")

    raw2 = await auth.create_refresh_token("alice")
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE refresh_tokens SET rotated_at='2000-01-01T00:00:00+00:00' WHERE token_hash=?",
                         (auth._hash_token(raw2),))
        await db.commit()
    assert await auth.verify_refresh_token(raw2) is None  # reuse -> revoke all
    assert await refresh_rejection_reason(raw2) == ("reused", "alice")


async def test_refresh_rejection_reason_never_raises(monkeypatch):
    import backend.auth_events as ae

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(ae, "get_db", boom)
    assert await ae.refresh_rejection_reason("abc") is None


def test_alert_setting_default_and_settings_model():
    from backend.db.settings_store import DEFAULT_SETTINGS
    from backend.routers.settings import SettingsUpdate
    assert DEFAULT_SETTINGS["discord_alert_login_success"] == "true"
    assert "discord_alert_login_success" in SettingsUpdate.model_fields


@pytest.mark.parametrize("lang", ["fr", "en", "de", "es", "it", "pt", "nl", "pl"])
def test_locales_have_auth_keys(lang):
    import json
    data = json.loads((ROOT / "backend" / "locales" / f"{lang}.json").read_text())
    for ev in ("login_success", "login_failure", "mfa_failure", "locked", "refresh_rejected",
               "mfa_enabled", "mfa_disabled", "mfa_reset"):
        assert f"auth.{ev}" in data, (lang, ev)
    assert "auth.alert_title" in data and "auth.alert_body" in data


def test_security_md_documents_format_and_versions():
    text = SECURITY_MD.read_text(encoding="utf-8")
    for needle in ("HYGIE_AUTH v=1", "journalmatch", "HYGIE_TRUSTED_PROXIES", "CrowdSec", "4.x"):
        assert needle in text, needle
    assert "2.x.x" not in text
