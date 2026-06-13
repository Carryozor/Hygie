# backend/db/websocket.py
"""WebSocket client tracking.

Log delivery uses DB polling (logs table) rather than in-process push so that
all workers in a multi-worker deployment share the same log stream — the WS
endpoint running on worker B can deliver logs written by worker A because both
read from the shared database.

_broadcast() is kept as a no-op to avoid changes to add_log() callers, but it
does nothing: the WS endpoint polls independently.
"""
import logging

logger = logging.getLogger(__name__)

# ─── Client registry (still useful for graceful disconnect tracking) ──────────
_ws_clients: set = set()

def register_ws(ws):
    _ws_clients.add(ws)

def unregister_ws(ws):
    _ws_clients.discard(ws)

async def _broadcast(payload: dict):
    """No-op — log delivery is now DB-poll based (see main.py websocket_endpoint)."""
