"""Coverage for _delete_from_seerr() and _handle_qbit() — the qBittorrent
action dispatch decides whether a torrent gets destroyed or just tagged, so
each branch here is a distinct, user-configured destructive/non-destructive
outcome.
"""
from unittest.mock import AsyncMock, patch



# ─── _delete_from_seerr ─────────────────────────────────────────────────────

async def test_delete_from_seerr_removes_linked_request():
    from backend.deletion import _delete_from_seerr

    row = {"seerr_id": 555, "title": "Inception"}
    with patch("backend.deletion.seerr_delete_request", new=AsyncMock(return_value=True)) as mock_del:
        await _delete_from_seerr(row)

    mock_del.assert_awaited_once_with(555)


async def test_delete_from_seerr_noop_without_seerr_id():
    """A media item never requested through Seerr has nothing to clean up —
    must not call the Seerr API at all."""
    from backend.deletion import _delete_from_seerr

    row = {"seerr_id": None, "title": "Manually Added Movie"}
    with patch("backend.deletion.seerr_delete_request", new=AsyncMock()) as mock_del:
        await _delete_from_seerr(row)

    mock_del.assert_not_awaited()


# ─── _handle_qbit ────────────────────────────────────────────────────────

async def test_handle_qbit_delete_torrent_action_deletes_with_files():
    from backend.deletion import _handle_qbit

    with patch("backend.deletion.qbit_delete_torrent", new=AsyncMock(return_value=True)) as mock_delete:
        await _handle_qbit("hash1", "Inception", "delete_torrent", "Supprimé")

    mock_delete.assert_awaited_once_with("hash1", delete_files=True)


async def test_handle_qbit_delete_files_action_also_deletes_with_files():
    """'delete_files' is an alias of 'delete_torrent' in the dispatch — both
    must route to the same destructive qBit call, not to tagging."""
    from backend.deletion import _handle_qbit

    with (
        patch("backend.deletion.qbit_delete_torrent", new=AsyncMock(return_value=True)) as mock_delete,
        patch("backend.deletion.qbit_add_tag", new=AsyncMock()) as mock_tag,
    ):
        await _handle_qbit("hash1", "Inception", "delete_files", "Supprimé")

    mock_delete.assert_awaited_once_with("hash1", delete_files=True)
    mock_tag.assert_not_awaited()


async def test_handle_qbit_tag_only_action_tags_instead_of_deleting():
    from backend.deletion import _handle_qbit

    with (
        patch("backend.deletion.qbit_add_tag", new=AsyncMock(return_value=True)) as mock_tag,
        patch("backend.deletion.qbit_delete_torrent", new=AsyncMock()) as mock_delete,
    ):
        await _handle_qbit("hash1", "Inception", "tag_only", "Supprimé-Hygie")

    mock_tag.assert_awaited_once_with("hash1", "Supprimé-Hygie")
    mock_delete.assert_not_awaited()


async def test_handle_qbit_unknown_action_falls_back_to_tag_only():
    """Any action value other than delete_torrent/delete_files defaults to the
    non-destructive tag path — the safe default must hold for garbage config."""
    from backend.deletion import _handle_qbit

    with (
        patch("backend.deletion.qbit_add_tag", new=AsyncMock(return_value=True)) as mock_tag,
        patch("backend.deletion.qbit_delete_torrent", new=AsyncMock()) as mock_delete,
    ):
        await _handle_qbit("hash1", "Inception", "garbage-value", "Supprimé-Hygie")

    mock_tag.assert_awaited_once()
    mock_delete.assert_not_awaited()


async def test_handle_qbit_logs_warning_when_delete_fails():
    from backend.deletion import _handle_qbit

    with patch("backend.deletion.qbit_delete_torrent", new=AsyncMock(return_value=False)):
        with patch("backend.deletion.add_log", new=AsyncMock()) as mock_log:
            await _handle_qbit("hash1", "Inception", "delete_torrent", "Supprimé")

    level = mock_log.await_args.args[0]
    assert level == "WARN"


async def test_handle_qbit_swallows_exception_and_logs_warning():
    """qBittorrent unreachable/timeout must not propagate and abort the
    pipeline — it is logged and the deletion continues."""
    from backend.deletion import _handle_qbit

    with (
        patch("backend.deletion.qbit_delete_torrent", new=AsyncMock(side_effect=RuntimeError("qbit down"))),
        patch("backend.deletion.add_log", new=AsyncMock()) as mock_log,
    ):
        await _handle_qbit("hash1", "Inception", "delete_torrent", "Supprimé")  # must not raise

    assert mock_log.await_args.args[0] == "WARN"
