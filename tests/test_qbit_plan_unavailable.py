"""v4.6.0 T2: QbitStep never erases files blind; retry end to end; log wording; N1; N3."""
import json
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

import backend.db.settings_store as _db_ss
import backend.db.schema as _db_schema
import backend.db.utils as _db_utils
from backend import qbit_siblings as qs
from backend.deletion_pipeline import DeletionContext, MediaServerStep, QbitStep, build_default_pipeline

MKV = "Movie.2026.mkv"
F = [{"name": MKV, "size": 6_000}]
LOCALES = pathlib.Path(__file__).parent.parent / "backend" / "locales"


def tor(h, path, name=MKV):
    return {"hash": h, "name": name, "size": 6_000, "content_path": path, "tags": "", "state": "stalledUP"}


def _ctx(action="delete_files", **item):
    base = {"title": "Movie", "id": 0, "media_type": "Movie"}
    c = DeletionContext(item={**base, **item}, dry_run=False, qbit_action=action, qbit_tag="tg")
    c.torrent_hashes = {"t"}
    return c


@pytest.fixture
def qbit(monkeypatch):
    state = {"torrents": [], "files": {}, "calls": [], "listing_up": True}

    async def delete(h, delete_files=True):
        state["calls"].append((h, delete_files))
        return True

    async def listing():
        return state["torrents"] if state["listing_up"] else None

    async def files(h):
        return state["files"].get(h)

    async def flag(key, default=False):
        return True if key == "qbit_delete_linked_crossseeds" else default

    monkeypatch.setattr(qs, "qbit_list_torrents", listing)
    monkeypatch.setattr(qs, "qbit_torrent_files", files)
    monkeypatch.setattr("backend.deletion_helpers.qbit_delete_torrent", delete)
    monkeypatch.setattr("backend.db.settings_store.get_bool_setting", flag)
    monkeypatch.setattr("backend.db.logs.add_log", AsyncMock())
    monkeypatch.setattr("backend.deletion_helpers.add_log", AsyncMock())
    return state


# ── P1-2 ─────────────────────────────────────────────────────────────────────

async def test_listing_unavailable_never_erases_files(qbit):
    qbit["listing_up"] = False
    with pytest.raises(RuntimeError, match="qBittorrent"):
        await QbitStep().execute(_ctx())
    assert qbit["calls"] == []


# ── P3-10 ────────────────────────────────────────────────────────────────────

async def test_delete_torrent_log_does_not_claim_files_deleted(monkeypatch):
    from backend import deletion_helpers as h
    log = AsyncMock()
    monkeypatch.setattr(h, "add_log", log)
    monkeypatch.setattr(h, "qbit_delete_torrent", AsyncMock(return_value=True))
    assert await h._handle_qbit("t", "Movie", "delete_torrent", "tg")
    msg = log.await_args.args[1]
    assert "torrent + fichier" not in msg
    assert "(fichiers conservés)" not in msg  # no hard-coded suffix
    assert "conserv" in msg


async def test_delete_files_log_still_says_files_deleted(monkeypatch):
    from backend import deletion_helpers as h
    log = AsyncMock()
    monkeypatch.setattr(h, "add_log", log)
    monkeypatch.setattr(h, "qbit_delete_torrent", AsyncMock(return_value=True))
    await h._handle_qbit("t", "Movie", "delete_files", "tg")
    assert "torrent + fichier" in log.await_args.args[1]


def test_new_locale_key_in_all_languages_without_hardcoded_french():
    keys = {p.stem: json.loads(p.read_text())["qbit.torrent_removed_files_kept"]
            for p in LOCALES.glob("*.json")}
    assert len(keys) == 8 and all("{title}" in v for v in keys.values())
    assert "fichiers conservés" not in keys["en"] and "fichiers conservés" not in keys["de"]


# ── N3 ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("action", ["tag_only", "pause"])
async def test_failed_tag_or_pause_is_counted_in_kept_note(qbit, monkeypatch, action):
    from backend import deletion_helpers as h
    monkeypatch.setattr(h, "qbit_add_tag", AsyncMock(return_value=False))
    monkeypatch.setattr(h, "qbit_pause_torrent", AsyncMock(return_value=False))
    c = _ctx(action=action)
    await QbitStep().execute(c)
    assert c.kept_note.startswith("1 torrent(s)")


async def test_successful_tag_leaves_no_kept_note(qbit, monkeypatch):
    from backend import deletion_helpers as h
    monkeypatch.setattr(h, "qbit_add_tag", AsyncMock(return_value=True))
    c = _ctx(action="tag_only")
    await QbitStep().execute(c)
    assert c.kept_note == ""


# ── N1 ───────────────────────────────────────────────────────────────────────

async def test_consolidated_delete_targets_the_recorded_sonarr():
    item = {"title": "Show", "emby_id": "sonarr-9", "media_type": "Series", "sonarr_series_id": 9,
            "arr_server_url": "http://sonarr-b:8989"}
    c = DeletionContext(item=item, dry_run=False)
    get = AsyncMock(return_value=None)
    with patch("backend.arr_clients.sonarr_get_series_by_id_any", new=get):
        await MediaServerStep().execute(c)
    assert get.await_args.kwargs.get("arr_server_url") == "http://sonarr-b:8989"


# ── End to end retry ─────────────────────────────────────────────────────────

@pytest.fixture
async def dbpath(tmp_path, monkeypatch):
    import backend.db.engine as _e
    import backend.deletion as _d
    path = str(tmp_path / "t2.db")
    for mod, attr in ((_db_utils, "DB_PATH"), (_db_ss, "DB_PATH"), (_db_schema, "DB_PATH"),
                      (_d, "DB_PATH"), (_e, "SQLITE_PATH")):
        monkeypatch.setattr(mod, attr, path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    from backend.db.engine import get_db
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    async with get_db() as db:
        rid = await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            ("e1", "Movie", "Movie", "lib", "Lib", "/dl/Movie.mkv", past, past, "pending"))
        await db.commit()
    return rid


async def _stats_rows():
    from backend.db.engine import get_db
    async with get_db() as db:
        return len(await db.fetch_all("SELECT * FROM stats_history"))


async def test_retry_after_qbit_listing_outage_deletes_with_guards(dbpath, qbit):
    row_id = dbpath
    qbit["torrents"] = [tor("t", "/dl/Movie.mkv"), tor("x", "/cs/Movie.mkv"),
                        tor("s", "/dl/Movie.mkv", name="survivor-name")]
    qbit["files"] = {"t": F, "x": F}
    server_del, arr_del, hash_lookup = AsyncMock(return_value=True), AsyncMock(return_value=True), \
        AsyncMock(return_value="t")

    def item(attempts):
        return {"id": row_id, "emby_id": "e1", "title": "Movie", "media_type": "Movie",
                "file_path": "/dl/Movie.mkv", "attempts": attempts}

    patches = (
        patch("backend.deletion_helpers._find_torrent_hash", new=hash_lookup),
        patch("backend.media_server_factory.delete_server_item", new=server_del),
        patch("backend.deletion_helpers._delete_from_arr", new=arr_del),
        patch("backend.deletion_helpers._delete_from_seerr", new=AsyncMock()),
        patch("backend.discord_client.send_notification", new=AsyncMock()),
    )
    for p in patches:
        p.start()
    try:
        # 1st pass: listing down -> QbitStep raises -> pipeline reports failure, nothing erased.
        qbit["listing_up"] = False
        first = DeletionContext(item=item(0), dry_run=False, qbit_action="delete_files")
        assert await build_default_pipeline().execute(first) is False
        assert qbit["calls"] == []
        assert await _stats_rows() == 0

        # 2nd pass (retry): listing back, arr history gone -> persisted hash used.
        qbit["listing_up"] = True
        hash_lookup.return_value = None
        second = DeletionContext(item=item(1), dry_run=False, qbit_action="delete_files")
        assert await build_default_pipeline().execute(second) is True
    finally:
        for p in patches:
            p.stop()
    # Twin x removed with files, target t removed WITHOUT files (survivor s shares its path).
    assert ("x", True) in qbit["calls"] and ("t", False) in qbit["calls"]
    assert ("t", True) not in qbit["calls"]
    assert await _stats_rows() == 1
    # Emby / *arr steps replayed idempotently on the retry.
    assert server_del.await_args_list[-1].kwargs["missing_ok"] is True
    assert arr_del.await_args_list[-1].kwargs == {"missing_ok": True}
