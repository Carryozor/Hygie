"""Typed exception hierarchy for Hygie."""


class HygieError(Exception):
    """Base class for all Hygie application errors."""


class MediaServerUnreachable(HygieError):
    """A media server (Emby/Jellyfin) is unreachable or returned an unexpected response."""


class ArrClientError(HygieError):
    """An *arr client (Radarr/Sonarr/Seerr) returned an error or is unreachable."""


class DeletionFailed(HygieError):
    """A media deletion operation failed."""


class ConfigurationError(HygieError):
    """A required configuration key is missing or invalid."""
