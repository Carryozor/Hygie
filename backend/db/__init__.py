# backend/db/__init__.py
"""Subpackage — re-exports everything from individual modules.

This file grows with each refactoring task. After Task 1, it exports
utils symbols only.
"""
from .utils import (
    DB_PATH,
    STATUS_PENDING,
    STATUS_DELETED,
    STATUS_ERROR,
    TIMEOUT_SHORT,
    TIMEOUT_MEDIUM,
    TIMEOUT_LONG,
    now_utc,
    sanitize_url,
    parse_iso_dt,
    http_retry,
)

from .encryption import (
    _ENC_PREFIX,
    SENSITIVE_KEYS,
    _get_fernet,
    _encrypt_value,
    _decrypt_value,
    _migrate_encrypt_settings,
)

from .settings_store import (
    DEFAULT_SETTINGS,
    get_setting,
    set_setting,
    get_bool_setting,
    get_int_setting,
    get_all_settings,
    _invalidate_settings_cache,
)

from .media_servers import (
    get_media_servers,
    save_media_servers,
    _invalidate_media_servers_cache,
)

from .websocket import (
    _ws_clients,
    register_ws,
    unregister_ws,
    _broadcast,
)

from .logs import (
    add_log,
    add_job_run,
    finish_job_run,
)

from .repositories import (
    get_pending_queue,
    get_queued_and_ignored_ids,
    get_enabled_libraries,
    insert_queue_entry,
    mark_notified_detected,
    update_queue_status,
)

from .schema import init_db

__all__ = [
    "DB_PATH", "STATUS_PENDING", "STATUS_DELETED", "STATUS_ERROR",
    "TIMEOUT_SHORT", "TIMEOUT_MEDIUM", "TIMEOUT_LONG",
    "now_utc", "sanitize_url", "parse_iso_dt", "http_retry",
    "_ENC_PREFIX", "SENSITIVE_KEYS", "_get_fernet", "_encrypt_value", "_decrypt_value", "_migrate_encrypt_settings",
    "DEFAULT_SETTINGS",
    "get_setting", "set_setting", "get_bool_setting", "get_int_setting", "get_all_settings",
    "_invalidate_settings_cache",
    "get_media_servers", "save_media_servers", "_invalidate_media_servers_cache",
    "_ws_clients", "register_ws", "unregister_ws", "_broadcast",
    "add_log", "add_job_run", "finish_job_run",
    "get_pending_queue", "get_queued_and_ignored_ids", "get_enabled_libraries",
    "insert_queue_entry", "mark_notified_detected", "update_queue_status",
    "init_db",
]
