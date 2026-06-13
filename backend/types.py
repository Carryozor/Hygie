# backend/types.py
"""Shared type definitions for Hygie's core data contracts."""
from typing import Optional
from typing_extensions import TypedDict, Required


class QueueEntry(TypedDict, total=False):
    """Explicit data contract for a media_queue row/insert dict.

    Fields marked `Required` must always be present.
    Optional fields are absent or None when not resolved.
    """
    emby_id:           Required[str]
    title:             Required[str]
    media_type:        Required[str]
    library_id:        Required[str]
    library_name:      Required[str]
    file_path:         Required[str]
    detected_at:       Required[str]   # ISO-8601
    delete_at:         Required[str]   # ISO-8601

    poster_url:        str
    tmdb_id:           str
    added_date:        Optional[str]   # ISO-8601, None when unknown
    last_played:       Optional[str]   # ISO-8601, None when never watched
    view_count:        int

    seerr_id:          Optional[int]
    seerr_user_id:     Optional[int]
    seerr_username:    str
    seerr_request_url: str

    radarr_id:         Optional[int]
    sonarr_id:         Optional[int]
    sonarr_series_id:  Optional[int]
    season_number:     Optional[int]
