# backend/scanner/_expert_rules.py
"""Build item_data dict and evaluate expert rules against it."""
from typing import Optional

from ..db.utils import now_utc, parse_iso_dt
from ..db.repositories import get_expert_rules as _get_expert_rules
from ..rules.engine import evaluate_rule as _evaluate_rule
from ..rules.models import RuleAction as _RuleAction


def _build_plex_item_data(item: dict) -> dict:
    """Build item_data from a preprocessed Plex scan item for expert rule evaluation.

    Maps Plex-specific fields to the same schema as _build_item_data so that
    expert rules work identically across Emby/Jellyfin and Plex libraries.
    """
    now = now_utc()
    added_at       = parse_iso_dt(item.get("added_at") or "")
    last_viewed_at = parse_iso_dt(item.get("last_viewed_at") or "")

    view_count = int(item.get("view_count") or 0)

    if last_viewed_at:
        days_not_watched = (now - last_viewed_at).days
    elif added_at:
        days_not_watched = (now - added_at).days
    else:
        days_not_watched = 0

    return {
        "days_not_watched": days_not_watched,
        "play_count":       view_count,
        "rating":           float(item.get("rating") or 0.0),
        "file_size_gb":     0.0,  # not available from Plex scan API
        "added_days_ago":   (now - added_at).days if added_at else 0,
        "media_type":       item.get("media_type") or "Movie",
        "seerr_user_id":    None,
        "never_watched":    1 if (view_count == 0 and not last_viewed_at) else 0,
    }


def _build_item_data(
    item: dict,
    play_count: int,
    last_played,
    added_date,
    seerr_user_id=None,
) -> dict:
    """Build the item_data dict expected by evaluate_rule.

    Keys match ConditionField enum values:
    days_not_watched, play_count, rating, file_size_gb, added_days_ago,
    media_type, seerr_user_id.
    """
    now = now_utc()

    if last_played is not None:
        days_not_watched = (now - last_played).days
    elif added_date is not None:
        days_not_watched = (now - added_date).days
    else:
        days_not_watched = 0

    added_days_ago = (now - added_date).days if added_date is not None else 0
    rating = float(item.get("CommunityRating") or 0.0)

    size_bytes = 0
    media_sources = item.get("MediaSources") or []
    if media_sources and isinstance(media_sources[0], dict):
        size_bytes = int(media_sources[0].get("Size") or 0)
    file_size_gb = round(size_bytes / (1024 ** 3), 4) if size_bytes else 0.0

    return {
        "days_not_watched": days_not_watched,
        "play_count":       play_count,
        "rating":           rating,
        "file_size_gb":     file_size_gb,
        "added_days_ago":   added_days_ago,
        "media_type":       item.get("Type") or "Movie",
        "seerr_user_id":    seerr_user_id,
        "never_watched":    1 if (play_count == 0 and last_played is None) else 0,
    }


async def _evaluate_expert_rules(
    item_data: dict,
    library_id=None,
    *,
    rules_cache: Optional[list] = None,
) -> tuple[Optional[str], int]:
    """Return (action, grace_days) if any enabled expert rule matches, else (None, 7).

    rules_cache: pre-loaded list of ExpertRule objects. Pass this to avoid one DB query
    per item — load once per library scan and pass it through.
    """
    rules = rules_cache if rules_cache is not None else await _get_expert_rules(enabled_only=True)
    for rule in rules:
        # library_ids (multi-select) takes precedence over legacy library_id
        if rule.library_ids is not None:
            if library_id is None or str(library_id) not in [str(x) for x in rule.library_ids]:
                continue
        elif rule.library_id is not None and library_id is not None:
            if str(rule.library_id) != str(library_id):
                continue
        if _evaluate_rule(rule, item_data):
            return rule.action.value, rule.grace_days
    return None, 7
