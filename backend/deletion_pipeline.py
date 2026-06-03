# backend/deletion_pipeline.py
"""Deletion pipeline — ordered steps that together delete one media item.

Each step is a single responsibility: torrent lookup, Discord notification,
Emby/Plex deletion, Radarr/Sonarr removal, Seerr cleanup, qBit tagging, stats.

Adding a new integration (e.g. Prowlarr) = add a new DeletionStep subclass
and insert it into build_default_pipeline() at the right position.

Step execution order matters:
  1. SizeLookupStep       — file size before arr deletion (metadata disappears after)
  2. TorrentHashStep      — hash before arr deletion (history disappears after)
  3. DiscordNotifyStep    — before Emby deletion (poster still accessible)
  4. ServerResolveStep    — resolve server dict for subsequent steps
  5. MediaServerStep      — delete hardlink from Emby / Plex
  6. ArrStep              — remove from Radarr/Sonarr (keep files)
  7. SeerrStep            — delete Seerr request
  8. QbitStep             — tag or delete torrent
  9. StatsStep            — record in stats_history
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ── Context ──────────────────────────────────────────────────────────────────

@dataclass
class DeletionContext:
    """Shared mutable state threaded through all pipeline steps."""
    item:        dict
    dry_run:     bool
    run_id:      int  = 0
    qbit_action: str  = "tag_only"
    qbit_tag:    str  = "Supprimé-Hygie"

    # Populated by pipeline steps
    server:       Optional[dict] = field(default=None, repr=False)
    torrent_hash: Optional[str]  = None
    size_bytes:   int            = 0

    # Tracks soft step failures — steps that failed but didn't abort the pipeline.
    # Populated by steps that catch their own exceptions (SizeLookup, TorrentHash, etc.).
    # Used by StatsStep and the pipeline to surface persistent partial failures.
    step_warnings: list = field(default_factory=list)

    # ── Convenience properties ────────────────────────────────────────────────
    @property
    def title(self) -> str:
        return self.item.get("title", "?")

    @property
    def emby_id(self) -> str:
        return str(self.item.get("emby_id", ""))

    @property
    def media_type(self) -> str:
        return self.item.get("media_type", "Movie")

    @property
    def server_id(self) -> str:
        return str(self.item.get("_server_id") or "0")

    @property
    def dry_prefix(self) -> str:
        return "[DRY RUN] " if self.dry_run else ""

    @property
    def job_tag(self) -> str:
        return f"[job:{self.run_id}] " if self.run_id else ""

    @property
    def log_prefix(self) -> str:
        return self.job_tag + self.dry_prefix


# ── Step base ─────────────────────────────────────────────────────────────────

class DeletionStep:
    """Base class for pipeline steps. Raise on unrecoverable error."""

    async def execute(self, ctx: DeletionContext) -> None:
        raise NotImplementedError


# ── Concrete steps ────────────────────────────────────────────────────────────

class SizeLookupStep(DeletionStep):
    """Look up file size before deletion for stats recording."""

    async def execute(self, ctx: DeletionContext) -> None:
        if ctx.dry_run:
            return
        try:
            from .arr_clients import radarr_get, sonarr_get_series_by_id
            if ctx.media_type == "Movie":
                rid = ctx.item.get("radarr_id")
                if rid:
                    movie = await radarr_get(int(rid))
                    ctx.size_bytes = int((movie.get("movieFile") or {}).get("size") or 0) if movie else 0
            else:
                sid = ctx.item.get("sonarr_series_id")
                if sid:
                    series = await sonarr_get_series_by_id(int(sid))
                    ctx.size_bytes = int((series.get("statistics") or {}).get("sizeOnDisk") or 0) if series else 0
        except Exception as e:
            logger.info("SizeLookupStep: size unavailable for '%s' (stats will show 0): %s", ctx.title, e)
            ctx.step_warnings.append(f"SizeLookup: {e}")


class TorrentHashStep(DeletionStep):
    """Find torrent hash BEFORE removing from arr — history disappears after deletion."""

    async def execute(self, ctx: DeletionContext) -> None:
        if ctx.dry_run:
            return
        try:
            from .deletion import _find_torrent_hash
            ctx.torrent_hash = await _find_torrent_hash(ctx.item)
        except Exception as e:
            logger.info("TorrentHashStep: hash unavailable for '%s' (qBit step will be skipped): %s", ctx.title, e)
            ctx.step_warnings.append(f"TorrentHash: {e}")


class DiscordNotifyStep(DeletionStep):
    """Send Discord deletion notification before Emby removal (poster still accessible)."""

    async def execute(self, ctx: DeletionContext) -> None:
        if ctx.dry_run:
            return
        try:
            from .discord_client import send_notification
            await send_notification([ctx.item], "now", dry_run=False)
        except Exception as e:
            # Isolated logging — if add_log itself fails (DB issue), we must not
            # propagate that exception up to the pipeline and abort the deletion.
            try:
                from .db.logs import add_log
                from .logmsg import lm
                await add_log("WARN", lm("deletion.discord_warn", prefix=ctx.log_prefix, detail=e), "deletion")
            except Exception:
                logger.warning("DiscordNotifyStep: both notification and add_log failed for '%s'", ctx.title)


class ServerResolveStep(DeletionStep):
    """Resolve the media server dict for this item's library."""

    async def execute(self, ctx: DeletionContext) -> None:
        try:
            from .db.media_servers import get_media_servers
            all_servers = await get_media_servers()
            ctx.server = next(
                (s for s in all_servers if str(s.get("id")) == ctx.server_id), None
            )
        except Exception as e:
            logger.debug("ServerResolveStep for '%s': %s", ctx.title, e)
            ctx.server = None


class MediaServerStep(DeletionStep):
    """Delete the item from Emby/Jellyfin or Plex."""

    async def execute(self, ctx: DeletionContext) -> None:
        if ctx.dry_run:
            return
        emby_id = ctx.emby_id
        # Synthetic consolidated entries (sonarr-{id}) have no media server file
        if not emby_id or emby_id.startswith("sonarr-"):
            return

        from .db.media_servers import is_plex
        from .db.logs import add_log
        from .logmsg import lm

        if is_plex(ctx.server or {}):
            from .plex_client import build_plex_client
            plex = build_plex_client(ctx.server or {})
            if plex:
                rating_key = ctx.item.get("plex_rating_key") or emby_id
                await plex.delete_item(rating_key)
                await add_log("DEBUG", lm("plex.deleted", key=rating_key), "deletion")
        else:
            from .emby_client import delete_item
            await delete_item(emby_id, server_id=ctx.server_id)
            await add_log("DEBUG", lm("emby.hardlink", title=ctx.title), "deletion")


class ArrStep(DeletionStep):
    """Remove from Radarr or Sonarr (keep files — media server owns the hardlinks)."""

    async def execute(self, ctx: DeletionContext) -> None:
        if ctx.dry_run:
            return
        from .deletion import _delete_from_arr
        await _delete_from_arr(ctx.item)


class SeerrStep(DeletionStep):
    """Delete the Seerr request linked to this media item."""

    async def execute(self, ctx: DeletionContext) -> None:
        if ctx.dry_run:
            return
        from .deletion import _delete_from_seerr
        await _delete_from_seerr(ctx.item)


class QbitStep(DeletionStep):
    """Tag or delete the torrent in qBittorrent based on configured action."""

    async def execute(self, ctx: DeletionContext) -> None:
        if ctx.dry_run:
            return
        from .db.logs import add_log
        from .logmsg import lm
        from .deletion import _handle_qbit

        if ctx.torrent_hash:
            await _handle_qbit(ctx.torrent_hash, ctx.title, ctx.qbit_action, ctx.qbit_tag)
        else:
            await add_log(
                "INFO",
                lm("deletion.qbit_not_found", prefix=ctx.log_prefix, title=ctx.title),
                "deletion",
            )


class StatsStep(DeletionStep):
    """Record the deletion in stats_history."""

    async def execute(self, ctx: DeletionContext) -> None:
        if ctx.dry_run:
            return
        try:
            from .db.engine import get_db
            from .db.utils import now_utc
            month  = now_utc().strftime("%Y-%m")
            lib_id = ctx.item.get("library_id") or None
            async with get_db() as db:
                await db.execute(
                    "INSERT INTO stats_history "
                    "(ts, total_deleted, total_scanned, space_freed_bytes, month, library_id) "
                    "VALUES (?, 1, 0, ?, ?, ?)",
                    (now_utc().isoformat(), ctx.size_bytes, month, lib_id),
                )
                await db.commit()
        except Exception as e:
            logger.debug("StatsStep for '%s': %s", ctx.title, e)


# ── Pipeline ──────────────────────────────────────────────────────────────────

class DeletionPipeline:
    """Ordered sequence of deletion steps with per-step error isolation."""

    def __init__(self, steps: list[DeletionStep]) -> None:
        self._steps = steps

    async def execute(self, ctx: DeletionContext) -> bool:
        """Run all steps in order. Returns False if any step raises an exception."""
        for step in self._steps:
            try:
                await step.execute(ctx)
            except Exception as e:
                logger.exception(
                    "DeletionPipeline step %s failed for '%s': %s",
                    type(step).__name__, ctx.title, e,
                )
                return False
        if ctx.step_warnings:
            logger.warning(
                "Deletion of '%s' completed with %d soft step failure(s): %s",
                ctx.title, len(ctx.step_warnings), "; ".join(ctx.step_warnings),
            )
        return True


def build_default_pipeline() -> DeletionPipeline:
    """Instantiate the standard deletion pipeline in the correct execution order."""
    return DeletionPipeline([
        SizeLookupStep(),
        TorrentHashStep(),
        DiscordNotifyStep(),
        ServerResolveStep(),
        MediaServerStep(),
        ArrStep(),
        SeerrStep(),
        QbitStep(),
        StatsStep(),
    ])
