"""Mandalorian scenario on real files: only what is meant to disappear does."""
import os
from unittest.mock import AsyncMock

import pytest

from backend import qbit_siblings as qs
from backend.deletion_pipeline import DeletionContext, QbitStep


class FakeQbit:
    """qBittorrent over a real directory: deleting with files unlinks them."""

    def __init__(self, root):
        self.root, self.torrents, self.log = root, {}, []

    def add(self, h, rel, *, size=10, link_of=None, content=b"x" * 10):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if link_of:
            os.link(os.path.join(self.root, link_of), path)      # real hardlink
        elif not os.path.exists(path):
            open(path, "wb").write(content)
        self.torrents[h] = {"hash": h, "name": os.path.basename(path), "size": size,
                            "content_path": path, "tags": ""}
        return path

    async def listing(self):
        return [dict(t) for t in self.torrents.values()]

    async def files(self, h):
        t = self.torrents.get(h)
        return [{"name": t["name"], "size": t["size"]}] if t else None

    async def delete(self, h, delete_files=True):
        t = self.torrents.pop(h, None)
        self.log.append((h, delete_files))
        if t and delete_files and os.path.exists(t["content_path"]):
            os.unlink(t["content_path"])
        return True


@pytest.fixture
def world(tmp_path, monkeypatch):
    q = FakeQbit(str(tmp_path))

    async def flag(key, default=False):
        return True

    monkeypatch.setattr(qs, "qbit_list_torrents", q.listing)
    monkeypatch.setattr(qs, "qbit_torrent_files", q.files)
    monkeypatch.setattr("backend.deletion_helpers.qbit_delete_torrent", q.delete)
    monkeypatch.setattr("backend.db.settings_store.get_bool_setting", flag)
    monkeypatch.setattr("backend.deletion_helpers.add_log", AsyncMock())
    monkeypatch.setattr("backend.db.logs.add_log", AsyncMock())
    return q


def ctx(h):
    c = DeletionContext(item={"title": "Movie", "id": 0, "media_type": "Movie"}, dry_run=False,
                        qbit_action="delete_files")
    c.torrent_hashes = {h}
    return c


async def test_mandalorian_scenario_leaves_no_missing_files_and_no_collateral(world):
    q = world
    main = q.add("main", "downloads/Movie.2026.mkv")
    # same path, other trackers (what became missingFiles in prod)
    q.add("twin_same_path", "downloads/Movie.2026.mkv")
    # hardlinked cross-seed copy in its own directory
    link = q.add("twin_link", "cross-seed/V3X/Movie.2026.mkv", link_of="downloads/Movie.2026.mkv")
    # lookalikes that must survive
    repack = q.add("repack", "downloads/Movie.2026.REPACK.mkv", content=b"y" * 10)
    bigger = q.add("bigger", "downloads/Movie.2026.mkv.other/Movie.2026.mkv", size=99, content=b"z" * 99)
    other_dir = q.add("same_name_other_size", "elsewhere/Movie.2026.mkv", size=11, content=b"w" * 11)

    await QbitStep().execute(ctx("main"))

    assert set(q.torrents) == {"repack", "bigger", "same_name_other_size"}
    assert not os.path.exists(main) and not os.path.exists(link)
    for survivor in (repack, bigger, other_dir):
        assert os.path.exists(survivor), survivor
    # nothing left pointing at a deleted file
    assert all(os.path.exists(t["content_path"]) for t in q.torrents.values())


async def test_survivor_on_the_same_path_keeps_the_file(world):
    q = world
    main = q.add("main", "downloads/Movie.2026.mkv")
    q.add("renamed_but_same_file", "downloads/Movie.2026.mkv")
    q.torrents["renamed_but_same_file"]["name"] = "Totally Different Name.mkv"   # not a twin

    await QbitStep().execute(ctx("main"))

    assert set(q.torrents) == {"renamed_but_same_file"}
    assert os.path.exists(main)                 # file kept: a survivor still seeds it
