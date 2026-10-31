"""qBittorrent health (4.5): what Hygie can see of the client it deletes through.

The 2026-10 audit found 50 torrents in `missingFiles` that nobody had looked at.
A read-only summary makes that visible without opening the qBittorrent UI.
"""
from unittest.mock import AsyncMock

import httpx
import pytest

from backend import qbit_health as qh


def tor(h, state="stalledUP", name=None, size=10, progress=1.0):
    return {"hash": h, "name": name or h, "state": state, "size": size, "progress": progress}


def _transfer(status="connected"):
    return httpx.Response(200, json={"connection_status": status, "dl_info_speed": 0, "up_info_speed": 5})


@pytest.fixture
def qbit(monkeypatch):
    state = {"torrents": [], "transfer": _transfer()}
    monkeypatch.setattr(qh, "qbit_list_torrents", AsyncMock(side_effect=lambda: state["torrents"]))
    monkeypatch.setattr(qh, "_request", AsyncMock(side_effect=lambda m, p, **k: state["transfer"]))
    return state


async def test_unreachable_when_listing_fails(qbit, monkeypatch):
    monkeypatch.setattr(qh, "qbit_list_torrents", AsyncMock(return_value=None))
    out = await qh.qbit_health()
    assert out["reachable"] is False and out["problems"] == []


async def test_counts_states_and_flags_problem_torrents(qbit):
    qbit["torrents"] = [tor("a"), tor("b"), tor("m1", "missingFiles", progress=0), tor("e1", "error")]
    out = await qh.qbit_health()
    assert out["reachable"] is True
    assert out["total"] == 4
    assert out["states"] == {"stalledUP": 2, "missingFiles": 1, "error": 1}
    assert {p["hash"] for p in out["problems"]} == {"m1", "e1"}
    assert out["missing_files"] == 1 and out["errored"] == 1


async def test_missing_files_size_is_summed(qbit):
    qbit["torrents"] = [tor("m1", "missingFiles", size=100), tor("m2", "missingFiles", size=50)]
    assert (await qh.qbit_health())["missing_files_bytes"] == 150


async def test_problem_list_is_capped_but_counts_are_not(qbit):
    qbit["torrents"] = [tor(f"m{i}", "missingFiles") for i in range(qh.MAX_PROBLEMS + 25)]
    out = await qh.qbit_health()
    assert len(out["problems"]) == qh.MAX_PROBLEMS
    assert out["missing_files"] == qh.MAX_PROBLEMS + 25


async def test_connection_status_reported_and_firewalled_is_flagged(qbit):
    qbit["transfer"] = _transfer("firewalled")
    out = await qh.qbit_health()
    assert out["connection_status"] == "firewalled"


async def test_transfer_info_failure_does_not_hide_the_torrent_summary(qbit):
    qbit["transfer"] = httpx.Response(500)
    out = await qh.qbit_health()
    assert out["reachable"] is True and out["connection_status"] is None


async def test_malformed_entries_are_ignored(qbit):
    qbit["torrents"] = [tor("a"), {"hash": "x"}, {"hash": "y", "state": None, "size": "n/a"}]
    out = await qh.qbit_health()
    assert out["total"] == 3                       # still counted, never crash
    assert out["states"].get("unknown") == 2


def test_endpoint_requires_auth(test_client):
    assert test_client.get("/api/qbit/health").status_code in (401, 403)


def test_endpoint_returns_summary(test_client, monkeypatch):
    import backend.routers.qbit as router_mod
    test_client.app.dependency_overrides[router_mod.require_auth] = lambda: "testuser"
    monkeypatch.setattr(router_mod, "qbit_health", AsyncMock(return_value={"reachable": True, "total": 3}))
    try:
        r = test_client.get("/api/qbit/health")
    finally:
        test_client.app.dependency_overrides.pop(router_mod.require_auth, None)
    assert r.status_code == 200 and r.json()["total"] == 3
