# backend/db/websocket.py
"""WebSocket broadcast helpers — tracks connected clients and fan-outs JSON payloads."""
import asyncio
import logging

logger = logging.getLogger(__name__)

# ─── WebSocket broadcast ──────────────────────────────────────────────────────
_ws_clients: set = set()

def register_ws(ws):
    _ws_clients.add(ws)

def unregister_ws(ws):
    _ws_clients.discard(ws)

async def _broadcast(payload: dict):
    """Send a payload to all connected WebSocket clients.

    Each send is wrapped in a 5-second timeout so a slow or stuck client
    cannot block the broadcast for all other subscribers.
    """
    if not _ws_clients:
        return
    dead = []
    for ws in list(_ws_clients):
        try:
            await asyncio.wait_for(ws.send_json(payload), timeout=5.0)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)
