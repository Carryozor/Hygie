"""Coverage additions for backend/routers/plex_webhook.py.

Gaps before this file (see baseline coverage run): path-based secret
endpoint (line 87) was never exercised, the fail-closed "no secret
configured" branch (line 39), the rate-limit branch (line 33), the
unhandled-event early-return (lines 54-55), and the lastViewedAt-missing
fallback to now() (line 108).
"""
import json
from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
async def _configure_webhook_secret(test_client):
    from backend.db.schema import init_db
    from backend.db.engine import get_db
    from backend.db.settings_store import set_setting, _invalidate_settings_cache

    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM media_queue")
        await db.commit()
    await set_setting("plex_webhook_secret", "cov-secret")
    yield
    await set_setting("plex_webhook_secret", "cov-secret")
    _invalidate_settings_cache()


async def _seed_queue_row(emby_id: str, title: str = "Scrobble Movie") -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status, last_played) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (emby_id, title, "Movie", "lib1", "Library", "/f/x.mkv",
             "2026-01-01T00:00:00+00:00", "2026-01-08T00:00:00+00:00", "pending", None),
        )
        await db.commit()


async def _fetch_queue_row(emby_id: str):
    from backend.db.engine import get_db
    async with get_db() as db:
        return await db.fetch_one("SELECT * FROM media_queue WHERE emby_id=?", (emby_id,))


async def _clear_webhook_secret() -> None:
    from backend.db.engine import get_db
    async with get_db() as db:
        await db.execute("DELETE FROM settings WHERE `key`='plex_webhook_secret'")
        await db.commit()


def _payload(event: str, rating_key: str = "pw-1", with_last_viewed: bool = True) -> str:
    metadata = {"ratingKey": rating_key, "title": "Inception", "type": "movie"}
    if with_last_viewed:
        metadata["lastViewedAt"] = 1700000000
    return json.dumps({
        "event": event,
        "Account": {"id": 1, "title": "testuser"},
        "Metadata": metadata,
    })


def test_path_based_secret_accepted_returns_200(test_client):
    """Path-based webhook (/webhook/{secret_token}) is the recommended form
    but had zero coverage — only the query-param variant was tested."""
    r = test_client.post(
        "/api/plex/webhook/cov-secret",
        data={"payload": _payload("media.play")},
    )
    assert r.status_code == 200


async def test_no_secret_configured_returns_403(test_client):
    """Fail-closed: without a configured secret, no event can be accepted —
    forging scrobble events would let an attacker delay deletions."""
    from backend.db.settings_store import _invalidate_settings_cache

    await _clear_webhook_secret()
    _invalidate_settings_cache()

    r = test_client.post(
        "/api/plex/webhook?secret=anything",
        data={"payload": _payload("media.play")},
    )
    assert r.status_code == 403


def test_rate_limited_returns_429(test_client, monkeypatch):
    """A blocked rate_limit_attempt() must short-circuit before the secret
    is even checked."""
    import backend.routers.plex_webhook as pw_mod

    def _fake_rate_limit_attempt(key):
        return (True, None)

    monkeypatch.setattr(pw_mod, "rate_limit_attempt", _fake_rate_limit_attempt)
    r = test_client.post(
        "/api/plex/webhook?secret=cov-secret",
        data={"payload": _payload("media.play")},
    )
    assert r.status_code == 429


async def test_unhandled_event_returns_200_and_does_not_touch_queue(test_client):
    """An event outside _HANDLED_EVENTS (e.g. media.rate) must be accepted
    (200, so Plex doesn't retry) but must not mutate any queue row."""
    await _seed_queue_row("pw-untouched")

    r = test_client.post(
        "/api/plex/webhook?secret=cov-secret",
        data={"payload": _payload("media.rate", rating_key="pw-untouched")},
    )
    assert r.status_code == 200

    row = await _fetch_queue_row("pw-untouched")
    assert row["last_played"] is None


async def test_scrobble_without_last_viewed_at_falls_back_to_now(test_client):
    """When Plex omits lastViewedAt, last_played must still be set (to
    "now"), not left null — otherwise the deletion clock never resets."""
    await _seed_queue_row("pw-nolastviewed")

    before = datetime.now(timezone.utc)
    r = test_client.post(
        "/api/plex/webhook?secret=cov-secret",
        data={"payload": _payload("media.scrobble", rating_key="pw-nolastviewed", with_last_viewed=False)},
    )
    assert r.status_code == 200

    row = await _fetch_queue_row("pw-nolastviewed")
    assert row["last_played"] is not None
    last_played = datetime.fromisoformat(row["last_played"])
    assert (last_played - before).total_seconds() < 10
    assert row["status"] == "pending"
