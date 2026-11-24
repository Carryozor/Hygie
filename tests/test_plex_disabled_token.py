"""4.6.0 (P4-13/D7): a disabled Plex server never receives its token."""
from backend import proxy as proxy_mod

PLEX = "http://plex.test:32400"


def test_disabled_plex_server_gets_no_token():
    servers = [{"id": "p", "type": "plex", "url": PLEX, "api_key": "tok", "enabled": False}]
    assert proxy_mod._plex_headers(f"{PLEX}/library/x", servers) == {}


def test_enabled_or_unflagged_plex_server_still_gets_token():
    enabled = [{"type": "plex", "url": PLEX, "api_key": "t", "enabled": True}]
    unflagged = [{"type": "plex", "url": PLEX, "api_key": "t"}]
    assert proxy_mod._plex_headers(f"{PLEX}/x", enabled) == {"X-Plex-Token": "t"}
    assert proxy_mod._plex_headers(f"{PLEX}/x", unflagged) == {"X-Plex-Token": "t"}
