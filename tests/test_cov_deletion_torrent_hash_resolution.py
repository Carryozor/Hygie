"""Coverage for _find_torrent_hash(), _find_torrent_hashes_consolidated(), and
the still-uncovered branches of _delete_from_arr() (Radarr/Sonarr path-match
fallback when no stored id is available).

Torrent hash resolution MUST happen before the arr removal call — history
disappears afterwards — so every routing branch here decides whether the
matching torrent ever gets tagged/deleted in qBittorrent at all.
"""
from unittest.mock import AsyncMock, patch



def _movie_row(**overrides) -> dict:
    base = {
        "title": "Inception",
        "media_type": "Movie",
        "file_path": "/movies/inception.mkv",
        "radarr_id": None,
    }
    base.update(overrides)
    return base


def _episode_row(**overrides) -> dict:
    base = {
        "title": "Lupin",
        "media_type": "Episode",
        "file_path": "/series/lupin/s01e10.mkv",
        "sonarr_id": None,
    }
    base.update(overrides)
    return base


# ─── _find_torrent_hash — Movie ────────────────────────────────────────────

async def test_find_torrent_hash_movie_uses_stored_radarr_id():
    from backend.deletion import _find_torrent_hash

    row = _movie_row(radarr_id=42)
    with (
        patch("backend.deletion.radarr_get_torrent_hash_any", new=AsyncMock(return_value="hash-by-id")) as by_id,
        patch("backend.deletion.radarr_find_by_path", new=AsyncMock()) as by_path,
        patch("backend.deletion.qbit_find_by_path", new=AsyncMock()) as by_qbit,
    ):
        result = await _find_torrent_hash(row)

    assert result == "hash-by-id"
    by_id.assert_awaited_once_with(42)
    by_path.assert_not_awaited()
    by_qbit.assert_not_awaited()


async def test_find_torrent_hash_movie_falls_back_to_radarr_path_match():
    from backend.deletion import _find_torrent_hash

    row = _movie_row(radarr_id=None)
    with (
        patch("backend.deletion.radarr_find_by_path",
              new=AsyncMock(return_value=(99, "http://radarr", "key"))),
        patch("backend.deletion.radarr_get_torrent_hash",
              new=AsyncMock(return_value="hash-by-path")) as by_hash,
        patch("backend.deletion.qbit_find_by_path", new=AsyncMock()) as by_qbit,
    ):
        result = await _find_torrent_hash(row)

    assert result == "hash-by-path"
    by_hash.assert_awaited_once_with(99, url="http://radarr", key="key")
    by_qbit.assert_not_awaited()


async def test_find_torrent_hash_movie_falls_back_to_qbit_path_when_radarr_has_no_match():
    """No radarr_id and no path match in Radarr history — last resort is
    qBittorrent's own path index."""
    from backend.deletion import _find_torrent_hash

    row = _movie_row(radarr_id=None)
    with (
        patch("backend.deletion.radarr_find_by_path", new=AsyncMock(return_value=None)),
        patch("backend.deletion.qbit_find_by_path", new=AsyncMock(return_value="hash-by-qbit")) as by_qbit,
    ):
        result = await _find_torrent_hash(row)

    assert result == "hash-by-qbit"
    by_qbit.assert_awaited_once_with(row["file_path"])


# ─── _find_torrent_hash — Episode ──────────────────────────────────────────

async def test_find_torrent_hash_episode_uses_stored_sonarr_id():
    from backend.deletion import _find_torrent_hash

    row = _episode_row(sonarr_id=901)
    with (
        patch("backend.deletion.sonarr_get_torrent_hash", new=AsyncMock(return_value="ep-hash")) as by_id,
        patch("backend.deletion.sonarr_find_by_path", new=AsyncMock()) as by_path,
    ):
        result = await _find_torrent_hash(row)

    assert result == "ep-hash"
    by_id.assert_awaited_once_with(901)
    by_path.assert_not_awaited()


async def test_find_torrent_hash_episode_falls_back_to_sonarr_path_match():
    from backend.deletion import _find_torrent_hash

    row = _episode_row(sonarr_id=None)
    with (
        patch("backend.deletion.sonarr_find_by_path", new=AsyncMock(return_value=901)),
        patch("backend.deletion.sonarr_get_torrent_hash", new=AsyncMock(return_value="ep-hash-2")) as by_hash,
    ):
        result = await _find_torrent_hash(row)

    assert result == "ep-hash-2"
    by_hash.assert_awaited_once_with(901)


async def test_find_torrent_hash_returns_none_without_any_match_or_path():
    from backend.deletion import _find_torrent_hash

    row = _episode_row(sonarr_id=None, file_path="")
    with (
        patch("backend.deletion.sonarr_find_by_path", new=AsyncMock(return_value=None)),
        patch("backend.deletion.qbit_find_by_path", new=AsyncMock()) as by_qbit,
    ):
        result = await _find_torrent_hash(row)

    assert result is None
    by_qbit.assert_not_awaited()


# ─── _find_torrent_hashes_consolidated ─────────────────────────────────────

async def test_find_torrent_hashes_consolidated_resolves_group_hashes_for_consolidated_row():
    from backend.deletion import _find_torrent_hashes_consolidated

    row = {"sonarr_series_id": 274, "sonarr_id": None, "season_number": 1}
    with patch(
        "backend.arr_clients.sonarr_get_torrent_hashes_for_group",
        new=AsyncMock(return_value={"hash-a", "hash-b"}),
    ) as mock_group:
        result = await _find_torrent_hashes_consolidated(row)

    assert result == {"hash-a", "hash-b"}
    mock_group.assert_awaited_once_with(274, season_number=1)


async def test_find_torrent_hashes_consolidated_returns_empty_set_for_normal_row():
    """A normal per-episode row (sonarr_id set) is not consolidated — must
    return an empty set without calling the group resolver at all."""
    from backend.deletion import _find_torrent_hashes_consolidated

    row = {"sonarr_series_id": 274, "sonarr_id": 901, "season_number": 1}
    with patch("backend.arr_clients.sonarr_get_torrent_hashes_for_group", new=AsyncMock()) as mock_group:
        result = await _find_torrent_hashes_consolidated(row)

    assert result == set()
    mock_group.assert_not_awaited()


# ─── _delete_from_arr — Radarr path-match fallback ─────────────────────────

async def test_delete_from_arr_movie_deletes_via_path_match_when_found():
    from backend.deletion import _delete_from_arr

    row = _movie_row(radarr_id=None)
    with (
        patch("backend.deletion.radarr_find_by_path",
              new=AsyncMock(return_value=(99, "http://radarr", "key"))),
        patch("backend.deletion.radarr_delete", new=AsyncMock(return_value=True)) as mock_delete,
    ):
        ok = await _delete_from_arr(row)

    assert ok is True
    mock_delete.assert_awaited_once_with(99, delete_files=False, url="http://radarr", key="key")


async def test_delete_from_arr_movie_path_match_failure_returns_false():
    from backend.deletion import _delete_from_arr

    row = _movie_row(radarr_id=None)
    with (
        patch("backend.deletion.radarr_find_by_path",
              new=AsyncMock(return_value=(99, "http://radarr", "key"))),
        patch("backend.deletion.radarr_delete", new=AsyncMock(return_value=False)),
    ):
        ok = await _delete_from_arr(row)

    assert ok is False


# ─── _delete_from_arr — Sonarr path-match fallback ─────────────────────────

async def test_delete_from_arr_episode_deletes_via_path_match_when_found():
    from backend.deletion import _delete_from_arr

    row = _episode_row(sonarr_id=None, sonarr_series_id=None, season_number=None)
    with (
        patch("backend.deletion.sonarr_find_by_path_full",
              new=AsyncMock(return_value=(55, "http://sonarr", "key"))),
        patch("backend.deletion.sonarr_delete_episode_file", new=AsyncMock(return_value=True)) as mock_delete,
    ):
        ok = await _delete_from_arr(row)

    assert ok is True
    mock_delete.assert_awaited_once_with(55, url="http://sonarr", key="key")


async def test_delete_from_arr_episode_returns_true_when_never_matched_in_sonarr():
    """No stored id, no path match — nothing to remove is not a failure (the
    item may have been added without ever having a Sonarr entry)."""
    from backend.deletion import _delete_from_arr

    row = _episode_row(sonarr_id=None, sonarr_series_id=None, season_number=None)
    with (
        patch("backend.deletion.sonarr_find_by_path_full", new=AsyncMock(return_value=None)),
        patch("backend.deletion.sonarr_delete_episode_file", new=AsyncMock()) as mock_delete,
    ):
        ok = await _delete_from_arr(row)

    assert ok is True
    mock_delete.assert_not_awaited()
