# backend/database.py
"""Compatibility shim — all symbols now live in backend/db/.

This module re-exports every public and private symbol that routers, the scheduler,
and tests currently import from backend.database.  It also forwards attribute
mutations (DB_PATH patching in tests, cache resets) to the actual submodules so
that existing test fixtures continue to work without modification.
"""
import sys
import types

# ── import the real submodules ────────────────────────────────────────────────
import backend.db.utils as _utils
import backend.db.encryption as _enc
import backend.db.settings_store as _ss
import backend.db.media_servers as _ms
import backend.db.websocket as _ws
import backend.db.logs as _logs
import backend.db.schema as _schema

# ── attribute → submodule routing table ──────────────────────────────────────
# Each entry maps an attribute name that tests may set on backend.database to the
# (submodule, attr_name) pair that actually owns the state.  Writes to database.X
# are forwarded to the owning submodule so that functions in those submodules see
# the patched value.
_FORWARDED_ATTRS = {
    # DB path — all submodules read DB_PATH from their own namespace after import,
    # so we forward to every submodule that owns a copy.
    "DB_PATH": [(_utils, "DB_PATH"), (_ss, "DB_PATH"), (_ms, "DB_PATH"),
                (_logs, "DB_PATH"), (_schema, "DB_PATH")],
    # Settings cache
    "_settings_cache":    [(_ss, "_settings_cache")],
    "_settings_cache_ts": [(_ss, "_settings_cache_ts")],
    # Media-servers cache
    "_ms_cache":    [(_ms, "_ms_cache")],
    "_ms_cache_ts": [(_ms, "_ms_cache_ts")],
    # WebSocket clients set
    "_ws_clients":  [(_ws, "_ws_clients")],
}


class _DatabaseShim(types.ModuleType):
    """Module subclass that forwards attribute writes to the owning submodules."""

    def __setattr__(self, name, value):
        targets = _FORWARDED_ATTRS.get(name)
        if targets is not None:
            for mod, attr in targets:
                object.__setattr__(mod, attr, value)
        # Always keep the attribute on this module too so reads work.
        super().__setattr__(name, value)


# ── replace this module object with the shim instance ────────────────────────
_shim = _DatabaseShim(__name__, __doc__)
_shim.__file__    = __file__
_shim.__package__ = __package__
_shim.__spec__    = __spec__
_shim.__loader__  = __loader__
_shim.__path__    = getattr(sys.modules[__name__], "__path__", None)

# ── populate all re-exported symbols ─────────────────────────────────────────
# utils
_shim.DB_PATH          = _utils.DB_PATH
_shim.STATUS_PENDING   = _utils.STATUS_PENDING
_shim.STATUS_DELETED   = _utils.STATUS_DELETED
_shim.STATUS_ERROR     = _utils.STATUS_ERROR
_shim.TIMEOUT_SHORT    = _utils.TIMEOUT_SHORT
_shim.TIMEOUT_MEDIUM   = _utils.TIMEOUT_MEDIUM
_shim.TIMEOUT_LONG     = _utils.TIMEOUT_LONG
_shim.now_utc          = _utils.now_utc
_shim.sanitize_url     = _utils.sanitize_url
_shim.parse_iso_dt     = _utils.parse_iso_dt
_shim.http_retry       = _utils.http_retry

# encryption
_shim._ENC_PREFIX              = _enc._ENC_PREFIX
_shim.SENSITIVE_KEYS           = _enc.SENSITIVE_KEYS
_shim._get_fernet              = _enc._get_fernet
_shim._encrypt_value           = _enc._encrypt_value
_shim._decrypt_value           = _enc._decrypt_value
_shim._migrate_encrypt_settings = _enc._migrate_encrypt_settings

# settings_store
_shim.DEFAULT_SETTINGS          = _ss.DEFAULT_SETTINGS
_shim.get_setting               = _ss.get_setting
_shim.set_setting               = _ss.set_setting
_shim.get_bool_setting          = _ss.get_bool_setting
_shim.get_int_setting           = _ss.get_int_setting
_shim.get_all_settings          = _ss.get_all_settings
_shim._invalidate_settings_cache = _ss._invalidate_settings_cache
# expose the live cache objects (mutable — same reference as in settings_store)
_shim._settings_cache    = _ss._settings_cache
_shim._settings_cache_ts = _ss._settings_cache_ts

# media_servers
_shim.get_media_servers              = _ms.get_media_servers
_shim.save_media_servers             = _ms.save_media_servers
_shim._invalidate_media_servers_cache = _ms._invalidate_media_servers_cache
# expose the live cache objects
_shim._ms_cache    = _ms._ms_cache
_shim._ms_cache_ts = _ms._ms_cache_ts

# websocket
_shim._ws_clients   = _ws._ws_clients
_shim.register_ws   = _ws.register_ws
_shim.unregister_ws = _ws.unregister_ws
_shim._broadcast    = _ws._broadcast

# logs
_shim.add_log       = _logs.add_log
_shim.add_job_run   = _logs.add_job_run
_shim.finish_job_run = _logs.finish_job_run

# schema
_shim.init_db = _schema.init_db

# ── install the shim ──────────────────────────────────────────────────────────
sys.modules[__name__] = _shim
