"""lm()'s first parameter is the message id; several templates also use a
{key} placeholder (plex.deleted, collection.plex_restored). Passing key=...
used to raise TypeError ("got multiple values for argument 'key'"), which made
a *successful* Plex deletion abort the pipeline and mark the item 'error'."""
from unittest.mock import AsyncMock, patch

from backend.deletion_pipeline import DeletionContext, MediaServerStep
from backend.logmsg import lm


def test_lm_accepts_a_key_placeholder_parameter():
    msg = lm("plex.deleted", key="rk-4242")
    assert "rk-4242" in msg


def test_lm_formats_collection_plex_restored_with_key():
    assert "ratingKey=rk-7" in lm("collection.plex_restored", key="rk-7")


async def test_plex_media_server_step_completes_after_successful_delete():
    item = {"id": 1, "title": "Inception", "media_type": "Movie", "emby_id": "rk-4242",
            "file_path": "/movies/inception.mkv", "_server_id": "0"}
    ctx = DeletionContext(item=item, dry_run=False)
    ctx.server = {"type": "plex", "url": "http://plex:32400", "api_key": "t"}
    add_log = AsyncMock()
    with (
        patch("backend.media_server_factory.delete_server_item", new=AsyncMock(return_value=True)),
        patch("backend.media_server_factory.get_server_item_id", return_value="rk-4242"),
        patch("backend.db.logs.add_log", new=add_log),
    ):
        await MediaServerStep().execute(ctx)  # must not raise

    logged = [c.args[1] for c in add_log.await_args_list]
    assert any("rk-4242" in m for m in logged)
