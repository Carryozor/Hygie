"""Arr clients subpackage — re-exports all public symbols for backward compatibility."""
from .shared import _arr_auth, _path_matches
from .radarr import (
    build_radarr_path_cache,
    get_radarr_servers,
    radarr_delete,
    radarr_delete_by_id,
    radarr_find_by_path,
    radarr_find_by_path_cached,
    radarr_get,
    radarr_get_poster_url,
    radarr_get_torrent_hash,
    radarr_get_torrent_hash_any,
    test_radarr,
)
from .sonarr import (
    build_sonarr_path_cache,
    get_sonarr_servers,
    sonarr_delete_episode_file,
    sonarr_delete_season,
    sonarr_delete_series,
    sonarr_find_by_path,
    sonarr_find_by_path_cached,
    sonarr_get_cache_entry,
    sonarr_get_poster_url,
    sonarr_get_series,
    sonarr_get_series_by_id,
    sonarr_get_torrent_hash,
    test_sonarr,
)
from .seerr import (
    build_seerr_request_cache,
    seerr_delete_request,
    seerr_find_request_by_tmdb,
    seerr_get_users,
    test_seerr,
)
