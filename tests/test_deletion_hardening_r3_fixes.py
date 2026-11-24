"""R3 fixes: persisted-hash precedence (F1), whole-series pack guard + visible
retention note (F2), error counting (F4), capped attempts increments (F5/F6),
explicit failure-recording error (F7)."""
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tests._qbit_plan_stub import echo_qbit_plan  # noqa: F401  (fixture)

import backend.db.utils as _db_utils
import backend.db.settings_store as _db_ss
import backend.db.schema as _db_schema

H1 = "aa00000000000000000000000000000000aaaa"
H2 = "bb00000000000000000000000000000000bbbb"
KEPT_PREFIX = "Conservation : "


@pytest.fixture
async def dbpath(tmp_path, monkeypatch):
    import backend.db.engine as _e
    import backend.deletion as _d
    path = str(tmp_path / "r3.db")
    for mod, attr in ((_db_utils, "DB_PATH"), (_db_ss, "DB_PATH"), (_db_schema, "DB_PATH"),
                      (_d, "DB_PATH"), (_e, "SQLITE_PATH")):
        monkeypatch.setattr(mod, attr, path)
    _db_ss._settings_cache.clear()
    _db_ss._settings_cache_ts = 0.0
    await _db_schema.init_db()
    return path


_SEQ = iter(range(1, 10_000))


async def _seed(status="deleting", attempts=0, torrent_hash="", last_error=None,
                media_type="Movie") -> int:
    from backend.db.engine import get_db
    async with get_db() as db:
        nid = await db.execute(
            "INSERT INTO media_queue (emby_id, title, media_type, library_id, library_name, "
            "file_path, detected_at, delete_at, status, attempts, torrent_hash, last_error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"e{next(_SEQ)}", "T", media_type, "l", "L", "/f.mkv", "2026-01-01", "2026-01-01",
             status, attempts, torrent_hash, last_error),
        )
        await db.commit()
    return nid


async def _row(nid) -> dict:
    from backend.db.engine import get_db
    async with get_db() as db:
        return dict(await db.fetch_one("SELECT * FROM media_queue WHERE id=?", (nid,)))


# ═══ F1 — fresh lookup beats the persisted hash ═════════════════════════════

async def _hash_step(nid, attempts, fresh):
    from backend.deletion_pipeline import DeletionContext, TorrentHashStep
    ctx = DeletionContext(
        item={"id": nid, "title": "M", "media_type": "Movie", "attempts": attempts},
        dry_run=False)
    with patch("backend.deletion_helpers._find_torrent_hash", new=AsyncMock(return_value=fresh)):
        await TorrentHashStep().execute(ctx)
    return ctx


async def test_f1_fresh_hash_wins_over_persisted_and_is_rewritten(dbpath):
    nid = await _seed(attempts=1, torrent_hash=H1)
    ctx = await _hash_step(nid, 1, H2)
    assert ctx.torrent_hash == H2
    assert (await _row(nid))["torrent_hash"] == H2


async def test_f1_persisted_hash_used_when_fresh_empty_on_retry(dbpath):
    nid = await _seed(attempts=1, torrent_hash=H1)
    ctx = await _hash_step(nid, 1, None)
    assert ctx.torrent_hash == H1


async def test_f1_persisted_hash_ignored_on_first_attempt(dbpath):
    nid = await _seed(attempts=0, torrent_hash=H1)
    ctx = await _hash_step(nid, 0, None)
    assert ctx.torrent_hash is None


async def test_f1_persisted_used_when_fresh_lookup_raises_on_retry(dbpath):
    from backend.deletion_pipeline import DeletionContext, TorrentHashStep
    nid = await _seed(attempts=2, torrent_hash=H1)
    ctx = DeletionContext(item={"id": nid, "title": "M", "media_type": "Movie", "attempts": 2},
                          dry_run=False)
    with patch("backend.deletion_helpers._find_torrent_hash",
               new=AsyncMock(side_effect=RuntimeError("arr down"))):
        await TorrentHashStep().execute(ctx)
    assert ctx.torrent_hash == H1


async def test_f1_deleted_status_clears_torrent_hash(dbpath):
    from backend.db.repositories import update_queue_status
    nid = await _seed(attempts=2, torrent_hash=H1, last_error="boom")
    await update_queue_status(nid, "deleted")
    row = await _row(nid)
    assert row["torrent_hash"] == ""
    assert row["attempts"] == 0 and row["last_error"] is None


# ═══ F2 — whole-series guard + retention note ═══════════════════════════════

def _series_ctx(hashes=(H1, H2)):
    from backend.deletion_pipeline import DeletionContext
    item = {"id": None, "title": "Série", "media_type": "Episode",
            "emby_id": "sonarr-series:5", "sonarr_series_id": 5, "season_number": None}
    ctx = DeletionContext(item=item, dry_run=False, qbit_action="delete_files")
    ctx.torrent_hashes = set(hashes)
    return ctx


async def _run_qbit(ctx, *, series_count, remaining):
    from backend.deletion_pipeline import QbitStep
    count = AsyncMock(side_effect=series_count) if isinstance(series_count, Exception) \
        else AsyncMock(return_value=series_count)
    with (
        patch("backend.arr_clients.sonarr_series_file_count", new=count),
        patch("backend.arr_clients.sonarr_remaining_files_for_hash",
              new=AsyncMock(return_value=remaining)),
        patch("backend.deletion_helpers._handle_qbit", new=AsyncMock(return_value=True)) as qb,
        patch("backend.db.logs.add_log", new=AsyncMock()),
    ):
        await QbitStep().execute(ctx)
    return qb


@pytest.mark.usefixtures("echo_qbit_plan")
async def test_f2_whole_series_without_remaining_file_deletes_without_history(dbpath):
    ctx = _series_ctx()
    ctx.item["id"] = await _seed(media_type="Episode")
    qb = await _run_qbit(ctx, series_count=0, remaining=None)
    assert qb.await_count == 2
    assert (await _row(ctx.item["id"]))["last_error"] is None


async def test_f2_whole_series_with_remaining_files_keeps_and_notes(dbpath):
    ctx = _series_ctx()
    ctx.item["id"] = await _seed(media_type="Episode")
    qb = await _run_qbit(ctx, series_count=3, remaining=None)
    qb.assert_not_awaited()
    assert (await _row(ctx.item["id"]))["last_error"] == \
        KEPT_PREFIX + "2 torrent(s) conservé(s) : partagé(s) ou non vérifiable(s)"


async def test_f2_sonarr_down_keeps_and_notes(dbpath):
    ctx = _series_ctx(hashes=(H1,))
    ctx.item["id"] = await _seed(media_type="Episode")
    qb = await _run_qbit(ctx, series_count=RuntimeError("down"), remaining=None)
    qb.assert_not_awaited()
    assert (await _row(ctx.item["id"]))["last_error"].startswith(KEPT_PREFIX + "1 torrent(s)")


async def test_f2_set_queue_note_truncates_and_keeps_status(dbpath):
    from backend.db.repositories import set_queue_note
    nid = await _seed()
    await set_queue_note(nid, "x" * 900)
    row = await _row(nid)
    assert len(row["last_error"]) == 500 and row["status"] == "deleting"


async def test_f2_note_survives_deleted_but_plain_error_is_cleared(dbpath):
    from backend.db.repositories import set_queue_note, update_queue_status
    kept = await _seed(last_error="old failure")
    await set_queue_note(kept, KEPT_PREFIX + "1 torrent(s) conservé(s) : partagé(s) ou non vérifiable(s)")
    plain = await _seed(last_error="old failure")
    await update_queue_status(kept, "deleted")
    await update_queue_status(plain, "deleted")
    assert (await _row(kept))["last_error"].startswith(KEPT_PREFIX)
    assert (await _row(plain))["last_error"] is None


async def test_f2_completion_log_mentions_kept_torrents(dbpath):
    import backend.deletion as d

    class _Pipe:
        async def execute(self, ctx):
            ctx.kept_note = "2 torrent(s) conservé(s) : partagé(s) ou non vérifiable(s)"
            return True

    log = AsyncMock()
    with (patch("backend.deletion_pipeline.build_default_pipeline", return_value=_Pipe()),
          patch.object(d, "add_log", log)):
        assert await d._delete_media({"id": 1, "title": "T", "media_type": "Movie"}, False) is True
    assert any("Suppression complète" in c.args[1] and "2 torrent(s) conservé(s)" in c.args[1]
               for c in log.await_args_list)


# ═══ F4 / F7 — _run_claimed ═════════════════════════════════════════════════

def _cfg():
    return SimpleNamespace(qbit_action="tag_only", qbit_tag="t", run_id=0, alert_on_error=False,
                           mention="", msg_tpl="", alert_threshold=0, dry_run=False)


async def test_f4_every_failure_is_counted_even_when_not_alerted(dbpath):
    import backend.deletion as d
    nid = await _seed(attempts=2)  # becomes attempts=3: not alert-worthy
    counters = {"errors": 0}
    with patch.object(d, "_delete_media", new=AsyncMock(return_value=False)):
        assert await d._run_claimed({"id": nid, "title": "T"}, _cfg(), counters) is False
    assert counters["errors"] == 1


async def test_f7_failure_recording_error_is_logged_and_claim_released(dbpath, caplog):
    import backend.deletion as d
    nid = await _seed()
    with (patch.object(d, "_delete_media", new=AsyncMock(return_value=False)),
          patch.object(d, "record_deletion_failure", new=AsyncMock(side_effect=OSError("db gone"))),
          caplog.at_level(logging.ERROR)):
        with pytest.raises(RuntimeError, match="enregistrer l'échec"):
            await d._run_claimed({"id": nid, "title": "T"}, _cfg(), {"errors": 0})
    assert any("record" in r.getMessage().lower() and r.exc_info for r in caplog.records)
    assert (await _row(nid))["status"] == "pending"


# ═══ F5 / F6 — capped attempts increment ════════════════════════════════════

async def test_f5_interruptions_never_reach_the_final_threshold(dbpath):
    from backend.db.engine import get_db
    from backend.db.repositories import release_claim
    nid = await _seed(attempts=0)
    for _ in range(10):
        assert await release_claim(nid) is True
        assert (await _row(nid))["attempts"] < 5
        async with get_db() as db:
            await db.execute("UPDATE media_queue SET status='deleting' WHERE id=?", (nid,))
            await db.commit()
    assert (await _row(nid))["attempts"] == 4


async def test_f6_reset_deleting_increments_capped(dbpath):
    from backend.db.repositories import reset_deleting_to_pending
    a = await _seed(attempts=0)
    b = await _seed(attempts=3)
    c = await _seed(attempts=4)
    assert await reset_deleting_to_pending() == 3
    assert [(await _row(i))["attempts"] for i in (a, b, c)] == [1, 4, 4]
    assert (await _row(a))["status"] == "pending"
