"""Read-only summary of the qBittorrent instance Hygie deletes through."""
from collections import Counter
from typing import Optional

from .qbit_client import _request, qbit_list_torrents

MAX_PROBLEMS = 50
_PROBLEM_STATES = ("missingFiles", "error")


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


async def _connection_status() -> Optional[str]:
    r = await _request("GET", "/api/v2/transfer/info")
    if r is None or r.status_code != 200:
        return None
    try:
        status = r.json().get("connection_status")
    except Exception:
        return None
    return status if isinstance(status, str) else None


async def qbit_health() -> dict:
    torrents = await qbit_list_torrents()
    if torrents is None:
        return {"reachable": False, "total": 0, "states": {}, "problems": [],
                "missing_files": 0, "missing_files_bytes": 0, "errored": 0, "connection_status": None}

    def state_of(t: dict) -> str:
        s = t.get("state")
        return s if isinstance(s, str) and s else "unknown"

    states = Counter(state_of(t) for t in torrents)
    problems = [t for t in torrents if state_of(t) in _PROBLEM_STATES]
    return {
        "reachable": True,
        "total": len(torrents),
        "states": dict(states),
        "missing_files": states["missingFiles"],
        "missing_files_bytes": sum(_int(t.get("size")) for t in problems if state_of(t) == "missingFiles"),
        "errored": states["error"],
        "problems": [
            {"hash": t["hash"], "name": str(t.get("name") or ""), "state": state_of(t), "size": _int(t.get("size"))}
            for t in problems[:MAX_PROBLEMS]
        ],
        "connection_status": await _connection_status(),
    }
