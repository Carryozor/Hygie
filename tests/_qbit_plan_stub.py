"""Shared fixture: QbitStep(delete_files) with a reachable qBittorrent.

Since v4.6.0 (D2a) QbitStep raises when the torrent listing is unavailable, so
tests that only care about the pipeline flow must provide a plan explicitly:
every requested hash is removable with its files, nothing else is touched.
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.qbit_siblings import Removal


async def _echo_plan(hashes, include_twins=False, protected=frozenset()):
    return [Removal(h, True, False, "", "/dl/x") for h in sorted(hashes)]


@pytest.fixture
def echo_qbit_plan():
    with (
        patch("backend.qbit_siblings.build_plan", new=_echo_plan),
        patch("backend.qbit_siblings.still_exclusive", new=AsyncMock(return_value=True)),
        patch("backend.db.settings_store.get_bool_setting", new=AsyncMock(return_value=False)),
    ):
        yield
