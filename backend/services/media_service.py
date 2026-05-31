# backend/services/media_service.py
"""Media queue service — business logic for queue management.

Extracted from routers/media.py to separate HTTP concerns from domain logic.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def format_queue_item(item: dict) -> dict:
    """Normalize a media_queue DB row for API responses.

    Computes derived fields: days_remaining, is_overdue.
    """
    delete_at = item.get("delete_at")
    days_remaining = None
    is_overdue = False

    if delete_at:
        try:
            dt = datetime.fromisoformat(delete_at.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = dt - datetime.now(timezone.utc)
            days_remaining = delta.days
            is_overdue = delta.total_seconds() < 0
        except (ValueError, AttributeError):
            pass

    return {
        **dict(item),
        "days_remaining": days_remaining,
        "is_overdue": is_overdue,
    }


def filter_queue_items(
    items: list[dict],
    *,
    status: Optional[str] = None,
    media_type: Optional[str] = None,
    search: Optional[str] = None,
) -> list[dict]:
    """Apply filters to a list of queue items (in-memory, post-DB)."""
    result = items
    if status:
        result = [i for i in result if i.get("status") == status]
    if media_type:
        result = [i for i in result if i.get("media_type") == media_type]
    if search:
        q = search.lower()
        result = [i for i in result if q in (i.get("title") or "").lower()]
    return result


def sort_queue_items(items: list[dict], sort_by: str = "delete_at", order: str = "asc") -> list[dict]:
    """Sort queue items by a given field."""
    reverse = order.lower() == "desc"
    try:
        return sorted(items, key=lambda i: (i.get(sort_by) or "") or "", reverse=reverse)
    except Exception:
        return items
