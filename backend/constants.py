"""Application-wide string constants — single source of truth."""

# Media server types
SERVER_EMBY      = "emby"
SERVER_JELLYFIN  = "jellyfin"
SERVER_PLEX      = "plex"
EMBY_LIKE_TYPES  = frozenset({SERVER_EMBY, SERVER_JELLYFIN, ""})

# Media types (Emby/Jellyfin API values)
MEDIA_MOVIE   = "Movie"
MEDIA_SERIES  = "Series"
MEDIA_EPISODE = "Episode"

# Seerr condition types
SEERR_INCLUDE = "user_include"
SEERR_EXCLUDE = "user_exclude"
