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
    server:         Optional[dict] = field(default=None, repr=False)
    torrent_hash:   Optional[str]  = None
    # Consolidated season/series deletes can span more than one download
    # (season packs, per-episode grabs, replaced releases) — torrent_hashes
    # holds every distinct hash found for the group, torrent_hash stays the
    # single-item (Movie / per-episode) path.
    torrent_hashes: set            = field(default_factory=set)
    size_bytes:     int            = 0

    # Tracks soft step failures — steps that failed but didn't abort the pipeline.
    # Populated by steps that catch their own exceptions (SizeLookup, TorrentHash, etc.).
    # Used by StatsStep and the pipeline to surface persistent partial failures.
    step_warnings: list = field(default_factory=list)

    # Set by QbitStep when torrents were deliberately kept (shared or
    # unverifiable): "N torrent(s) conservé(s) : ..." — surfaced to the user.
    kept_note: str = ""

    # ── Convenience properties ────────────────────────────────────────────────
    @property
    def title(self) -> str:
        return self.item.get("title", "?")

    @property
    def is_retry(self) -> bool:
        """True when a previous attempt already ran on this row (attempts > 0,
        migration m018): "already absent" answers are then legitimate."""
        try:
            return int(self.item.get("attempts") or 0) > 0
        except (TypeError, ValueError):
            return False

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

    @property
    def is_consolidated(self) -> bool:
        """True for the consolidated season/series queue entries produced by
        deletion_unit=season|series — one row stands in for a whole group of
        episodes and carries sonarr_series_id but no per-item sonarr_id."""
        from .deletion_helpers import _is_consolidated_row
        return _is_consolidated_row(self.item)


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
            # *_any() checks every configured server instead of only the
            # legacy default — radarr_id/sonarr_series_id are only meaningful
            # on the specific server that issued them in multi-server setups.
            if ctx.media_type == "Movie":
                ctx.size_bytes = await self._movie_size(ctx)
            else:
                ctx.size_bytes = await self._series_size(ctx)
        except Exception as e:
            logger.info("SizeLookupStep: size unavailable for '%s' (stats will show 0): %s", ctx.title, e)
            ctx.step_warnings.append(f"SizeLookup: {e}")

    @staticmethod
    async def _movie_size(ctx: DeletionContext) -> int:
        from .arr_clients import radarr_get_any
        rid = ctx.item.get("radarr_id")
        if not rid:
            return 0
        srv = ctx.item.get("arr_server_url")
        movie = await (radarr_get_any(int(rid), arr_server_url=srv) if srv else radarr_get_any(int(rid)))
        return int((movie.get("movieFile") or {}).get("size") or 0) if movie else 0

    @staticmethod
    async def _series_size(ctx: DeletionContext) -> int:
        """Size of the files really concerned: one episode file, one season, or
        the whole series. When the per-file size is unavailable, falls back to
        the series' sizeOnDisk (previous behaviour)."""
        from .arr_clients import sonarr_get_files_size, sonarr_get_series_by_id_any
        sid = ctx.item.get("sonarr_series_id")
        if not sid:
            return 0
        srv = ctx.item.get("arr_server_url")
        season = ctx.item.get("season_number")
        episode_file = None if ctx.is_consolidated else ctx.item.get("sonarr_id")
        if episode_file or (ctx.is_consolidated and season is not None):
            size = await sonarr_get_files_size(
                int(sid), season_number=None if episode_file else season,
                episode_file_id=int(episode_file) if episode_file else None,
                arr_server_url=srv,
            )
            if size is not None:
                return size
        series = await (sonarr_get_series_by_id_any(int(sid), arr_server_url=srv)
                        if srv else sonarr_get_series_by_id_any(int(sid)))
        return int((series.get("statistics") or {}).get("sizeOnDisk") or 0) if series else 0


class TorrentHashStep(DeletionStep):
    """Find torrent hash(es) BEFORE removing from arr — history disappears after deletion.

    Consolidated season/series entries can be backed by more than one
    download (season packs, per-episode grabs, replaced releases), so they
    resolve a *set* of hashes instead of the single-item hash.
    """

    async def execute(self, ctx: DeletionContext) -> None:
        """Fresh resolution first; the persisted hash is only a fallback on a
        retry (arr history may have vanished with the media). A fresh result
        always wins — the arr may have replaced the file since — and is
        written back."""
        if ctx.dry_run:
            return
        try:
            await self._resolve_fresh(ctx)
        except Exception as e:
            logger.info("TorrentHashStep: hash unavailable for '%s' (qBit step will be skipped): %s", ctx.title, e)
            ctx.step_warnings.append(f"TorrentHash: {e}")
        if ctx.torrent_hashes or ctx.torrent_hash:
            await self._persist(ctx)
        elif ctx.is_retry:
            await self._load_persisted(ctx)

    @staticmethod
    async def _resolve_fresh(ctx: DeletionContext) -> None:
        if ctx.is_consolidated:
            from .deletion_helpers import _find_torrent_hashes_consolidated
            ctx.torrent_hashes = await _find_torrent_hashes_consolidated(ctx.item)
        else:
            from .deletion_helpers import _find_torrent_hash
            ctx.torrent_hash = await _find_torrent_hash(ctx.item)

    @staticmethod
    async def _load_persisted(ctx: DeletionContext) -> bool:
        """Use the hash(es) stored by an earlier attempt, if any (arr history
        may be gone by now). Only called when the fresh lookup found nothing."""
        item_id = ctx.item.get("id")
        if not item_id:
            return False
        try:
            from .db.repositories import get_queue_torrent_hash
            stored = await get_queue_torrent_hash(int(item_id))
        except Exception as e:
            logger.warning("TorrentHashStep: persisted hash unreadable for '%s': %s", ctx.title, e)
            return False
        hashes = [h for h in (p.strip() for p in stored.split(",")) if h]
        if not hashes:
            return False
        if ctx.is_consolidated:
            ctx.torrent_hashes = set(hashes)
        else:
            ctx.torrent_hash = hashes[0]
        return True

    @staticmethod
    async def _persist(ctx: DeletionContext) -> None:
        """Store the freshly resolved hash(es) — nothing when none was found."""
        item_id = ctx.item.get("id")
        found = sorted(ctx.torrent_hashes) if ctx.is_consolidated else (
            [ctx.torrent_hash] if ctx.torrent_hash else [])
        if not item_id or not found:
            return
        try:
            from .db.repositories import set_queue_torrent_hash
            await set_queue_torrent_hash(int(item_id), ",".join(found))
        except Exception as e:
            logger.warning("TorrentHashStep: hash not persisted for '%s': %s", ctx.title, e)
            ctx.step_warnings.append(f"TorrentHash: not persisted: {e}")


class DiscordNotifyStep(DeletionStep):
    """Send Discord deletion notification before Emby removal (poster still accessible).

    Idempotent: skips if a 'now' notification was already recorded for this item,
    so a failed-then-retried deletion does not send a duplicate Discord message.
    """

    async def execute(self, ctx: DeletionContext) -> None:
        if ctx.dry_run:
            return
        try:
            item_id = ctx.item.get("id")
            from .db.engine import get_db
            if item_id:
                async with get_db() as _db:
                    _row = await _db.fetch_one(
                        "SELECT 1 FROM notifications WHERE media_id=? AND threshold='now'",
                        (item_id,),
                    )
                if _row:
                    return
            from .discord_client import send_notification
            await send_notification([ctx.item], "now", dry_run=False)
            # Persist the 'now' marker here — not only in update_queue_status,
            # which records it solely for the deleted/deleting statuses. A
            # deletion that fails (status 'error') and is retried would otherwise
            # re-send a duplicate Discord message, since the guard above would
            # still find no marker.
            if item_id:
                async with get_db() as _db:
                    await _db.execute(
                        "INSERT OR IGNORE INTO notifications (media_id, threshold) VALUES (?,?)",
                        (item_id, "now"),
                    )
                    await _db.commit()
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
        if not emby_id:
            return
        if emby_id.startswith("sonarr-"):
            await self._delete_consolidated(ctx)
            return

        from .media_server_factory import delete_server_item, get_server_item_id
        from .db.media_servers import is_plex
        from .db.logs import add_log
        from .logmsg import lm

        server = ctx.server or {}
        ok = await delete_server_item(
            server, ctx.item, server_id=ctx.server_id, missing_ok=ctx.is_retry)
        if not ok:
            raise RuntimeError(f"media server delete failed for '{ctx.title}'")
        if is_plex(server):
            item_id = get_server_item_id(server, ctx.item)
            await add_log("DEBUG", lm("plex.deleted", key=item_id), "deletion")
        else:
            await add_log("DEBUG", lm("emby.hardlink", title=ctx.title), "deletion")

    async def _delete_consolidated(self, ctx: DeletionContext) -> None:
        """Consolidated season/series entries carry no per-item Emby ID — resolve
        the matching Series/Season library item by its on-disk path instead.

        The path comes from Sonarr (authoritative), not from this row's stored
        file_path alone, since the anchor episode's path is only reliable for
        locating the season subfolder, not the series root.
        """
        import os

        from .arr_clients import sonarr_get_series_by_id_any
        from .emby_client import delete_item as emby_delete_item, find_item_by_path
        from .db.logs import add_log
        from .logmsg import lm

        series_id = ctx.item.get("sonarr_series_id")
        if not series_id:
            return
        srv = ctx.item.get("arr_server_url")
        series = await (sonarr_get_series_by_id_any(int(series_id), arr_server_url=srv)
                        if srv else sonarr_get_series_by_id_any(int(series_id)))
        series_path = (series or {}).get("path") or ""
        if not series_path:
            ctx.step_warnings.append(
                f"MediaServer: chemin de la série introuvable côté Sonarr pour '{ctx.title}'")
            return

        season_number = ctx.item.get("season_number")
        if season_number is not None:
            season_path = os.path.dirname(ctx.item.get("file_path") or "")
            target = await find_item_by_path(season_path, include_types="Season", server_id=ctx.server_id) if season_path else None
        else:
            target = await find_item_by_path(series_path, include_types="Series", server_id=ctx.server_id)

        if not target:
            await add_log("WARN", lm("emby.consolidated_not_found", title=ctx.title), "deletion")
            ctx.step_warnings.append(
                f"MediaServer: cible introuvable côté serveur média pour '{ctx.title}'")
            return

        extra = {"missing_ok": True} if ctx.is_retry else {}
        ok = await emby_delete_item(str(target.get("Id")), server_id=ctx.server_id, **extra)
        await add_log(
            "DEBUG" if ok else "WARN",
            lm("emby.consolidated_ok" if ok else "emby.consolidated_err", title=ctx.title),
            "deletion",
        )
        if not ok:
            raise RuntimeError(f"media server delete failed for consolidated '{ctx.title}'")


class ArrStep(DeletionStep):
    """Remove from Radarr or Sonarr (keep files — media server owns the hardlinks)."""

    async def execute(self, ctx: DeletionContext) -> None:
        if ctx.dry_run:
            return
        from .deletion_helpers import _delete_from_arr
        # missing_ok only on a retry: a 404 then means "removed by the
        # interrupted previous attempt"; on a first attempt it stays a failure.
        ok = await (_delete_from_arr(ctx.item, missing_ok=True) if ctx.is_retry
                    else _delete_from_arr(ctx.item))
        if not ok:
            raise RuntimeError(f"arr delete failed for '{ctx.title}'")


class SeerrStep(DeletionStep):
    """Delete the Seerr request linked to this media item."""

    async def execute(self, ctx: DeletionContext) -> None:
        if ctx.dry_run:
            return
        from .deletion_helpers import _delete_from_seerr
        await _delete_from_seerr(ctx.item)


class QbitStep(DeletionStep):
    """Tag or delete the torrent(s) in qBittorrent based on configured action.

    Consolidated entries hand over a set of hashes (ctx.torrent_hashes) — every
    one of them gets tagged/deleted, not just the first.
    """

    async def execute(self, ctx: DeletionContext) -> None:
        if ctx.dry_run:
            return
        from .db.logs import add_log
        from .logmsg import lm
        if ctx.torrent_hashes:
            hashes = sorted(ctx.torrent_hashes)
        elif ctx.torrent_hash:
            hashes = [ctx.torrent_hash]
        else:
            await add_log(
                "WARN",
                lm("deletion.qbit_not_found", prefix=ctx.log_prefix, title=ctx.title),
                "deletion",
            )
            ctx.step_warnings.append("Qbit: torrent hash not found")
            return
        if ctx.qbit_action == "delete_files":
            kept = await self._delete_files(ctx, hashes)
        else:
            kept = 0
            for torrent_hash in hashes:
                if not await self._apply(ctx, torrent_hash):
                    kept += 1
        if kept:
            await self._record_kept(ctx, kept)

    @staticmethod
    async def _record_kept(ctx: DeletionContext, kept: int) -> None:
        """Make an incomplete deletion visible on the queue row and in the log."""
        from .db.repositories import KEPT_NOTE_PREFIX, set_queue_note
        ctx.kept_note = f"{kept} torrent(s) conservé(s) : partagé(s) ou non vérifiable(s)"
        item_id = ctx.item.get("id")
        if not item_id:
            return
        try:
            await set_queue_note(int(item_id), KEPT_NOTE_PREFIX + ctx.kept_note)
        except Exception as e:
            logger.warning("QbitStep: kept-torrents note not stored for '%s': %s", ctx.title, e)
            ctx.step_warnings.append(f"Qbit: note de conservation non enregistrée: {e}")

    @staticmethod
    async def _delete_files(ctx: DeletionContext, hashes: list) -> int:
        """delete_files: remove the torrents together with the linked cross-seed
        copies (identical file list), and erase files only where no surviving
        torrent still points at them. Returns how many torrents were kept."""
        from .db.logs import add_log
        from .db.settings_store import get_bool_setting
        from .deletion_helpers import _handle_qbit, _shared_pack_blocker
        from .qbit_siblings import build_plan, still_exclusive

        kept, deletable, blocked = 0, [], set()
        for torrent_hash in hashes:
            blocker = await _shared_pack_blocker(ctx.item, torrent_hash) if ctx.media_type != "Movie" else None
            if blocker:
                ctx.step_warnings.append(f"Qbit: {blocker}")
                await add_log("WARN", f"{ctx.log_prefix}{ctx.title} : {blocker}", "deletion")
                blocked.add(torrent_hash)
                kept += 1
            else:
                deletable.append(torrent_hash)
        if not deletable:
            return kept

        twins = await get_bool_setting("qbit_delete_linked_crossseeds")
        try:
            plan = await build_plan(set(deletable), include_twins=twins, protected=frozenset(blocked))
        except Exception as e:
            # Ownership cannot be established: keep everything rather than guess.
            logger.warning("QbitStep: removal plan failed for '%s': %s", ctx.title, e)
            ctx.step_warnings.append(f"Qbit: plan de suppression impossible ({e}) — torrents conservés")
            return kept + len(deletable)
        if plan is None:
            # Listing unavailable: survivors and twins cannot be checked, so erasing
            # files blind is not an option. Fail the step: the row goes to 'error'
            # and is retried with backoff (Emby/arr steps replay idempotently).
            raise RuntimeError("liste qBittorrent indisponible : suppression des fichiers reportée")

        planned = {r.hash.lower() for r in plan}
        for torrent_hash in deletable:
            if torrent_hash.lower() not in planned:
                await add_log("INFO", f"{ctx.log_prefix}{ctx.title} : torrent {torrent_hash[:8]} déjà absent de qBittorrent", "deletion")

        for r in plan:
            label = f"{ctx.title} (copie cross-seed liée)" if r.twin else ctx.title
            reason, erase = r.reason, r.delete_files
            if erase and not await still_exclusive(r.path, planned):
                # A torrent appeared on this path since the plan was made.
                erase, reason = False, "chemin devenu utilisé par un autre torrent : fichiers conservés"
            if not erase:
                ctx.step_warnings.append(f"Qbit {r.hash}: {reason}")
                await add_log("WARN", f"{ctx.log_prefix}{label} : {reason}", "deletion")
            ok = await _handle_qbit(
                r.hash, label, "delete_files" if erase else "delete_torrent",
                ctx.qbit_tag, ctx.step_warnings)
            if not ok:
                kept += 1
        return kept

    @staticmethod
    async def _apply(ctx: DeletionContext, torrent_hash: str) -> bool:
        """Tag, pause or delete (without files) one torrent. delete_files goes
        through _delete_files, which plans removals across linked torrents."""
        from .deletion_helpers import _handle_qbit

        return await _handle_qbit(
            torrent_hash, ctx.title, ctx.qbit_action, ctx.qbit_tag, ctx.step_warnings)


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
            logger.warning("StatsStep for '%s': stats not recorded: %s", ctx.title, e)
            ctx.step_warnings.append(f"Stats: {e}")


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
