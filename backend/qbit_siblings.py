"""Linked cross-seed cleanup for qBittorrent deletions.

Deleting a torrent with its files leaves every other torrent pointing at the
same files in `missingFiles`. This module decides, from a fresh qBittorrent
listing, which torrents go together and — more important — which paths may
really be erased:

* a *twin* has exactly the same file list (relative path + size, every file)
  as a torrent being removed. Names are never compared: a similar name with a
  different file list is left strictly alone;
* files are erased only when no surviving torrent (one not being removed)
  overlaps the path. Overlap means identical, contained in, or containing.

Known limit: Hygie cannot see the media filesystem, so symlinks and hardlinks
are invisible here. A survivor that reaches the files through a symlink with a
different content_path is not detected (cross-seed in this deployment uses
hardlinks, which keep the data alive on their own).
"""
import logging
import posixpath
from dataclasses import dataclass
from typing import Iterable, Optional

from .qbit_client import qbit_list_torrents, qbit_torrent_files

logger = logging.getLogger(__name__)

# Upper bound on candidate twins inspected (one /torrents/files call each).
MAX_TWINS = 20


@dataclass(frozen=True)
class Removal:
    hash: str
    delete_files: bool
    twin: bool
    reason: str = ""
    path: str = ""


def _norm(path: str) -> str:
    """Canonical form for comparison: forward slashes, no `.`/`..`/`//`, and
    case-folded (a case-insensitive filesystem must not hide an overlap — a
    false overlap only keeps files, which is the safe direction)."""
    p = (path or "").replace("\\", "/")
    if not p.strip("/"):
        return ""
    return posixpath.normpath(p).casefold()


def _is_shallow(path: str) -> bool:
    """A root or a top-level directory is never a legitimate torrent payload."""
    return len([part for part in _norm(path).split("/") if part]) < 2


def paths_overlap(a: str, b: str) -> bool:
    """True when one path equals, contains or is contained in the other."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _signature(files: Optional[Iterable[dict]]) -> Optional[frozenset]:
    if not files:
        return None
    try:
        return frozenset((f["name"], int(f["size"])) for f in files)
    except (KeyError, TypeError, ValueError):
        return None


def plan_removal(
    targets: set, torrents: list, signatures: dict, *, include_twins: bool,
    protected: frozenset = frozenset(),
) -> list:
    """Ordered removals (twins first, targets last) with a per-torrent
    `delete_files` verdict. Pure function: no I/O.

    `protected` hashes (kept on purpose by the caller, e.g. a shared season
    pack) are neither targets nor twins: they stay survivors.
    """
    want = {h.lower() for h in targets}
    keep = {h.lower() for h in protected}
    sigs = {h.lower(): s for h, s in signatures.items() if s}
    present = [t for t in torrents if t["hash"].lower() in want]
    if not present:
        return []

    twins: list = []
    if include_twins:
        target_sigs = {sigs[t["hash"].lower()] for t in present if t["hash"].lower() in sigs}
        twins = [
            t for t in torrents
            if t["hash"].lower() not in want | keep and sigs.get(t["hash"].lower()) in target_sigs
        ]

    removing = {t["hash"].lower() for t in present} | {t["hash"].lower() for t in twins}
    survivors = [t for t in torrents if t["hash"].lower() not in removing]

    def verdict(t: dict, is_twin: bool) -> Removal:
        path = t.get("content_path") or ""
        if not path or _is_shallow(path):
            return Removal(t["hash"], False, is_twin, "chemin inconnu ou trop court : fichiers conservés", path)
        if any(paths_overlap(path, s.get("content_path") or "") for s in survivors):
            return Removal(t["hash"], False, is_twin, "chemin utilisé par un autre torrent : fichiers conservés", path)
        return Removal(t["hash"], True, is_twin, "", path)

    return [verdict(t, True) for t in twins] + [verdict(t, False) for t in present]


async def still_exclusive(path: str, removing: set) -> bool:
    """Fresh check right before erasing files: nobody outside `removing`
    (lower-case hashes) may overlap `path`. Unreadable listing = not exclusive."""
    torrents = await qbit_list_torrents()
    if torrents is None:
        return False
    return not any(
        t["hash"].lower() not in removing and paths_overlap(path, t.get("content_path") or "")
        for t in torrents
    )


async def build_plan(
    hashes: set, *, include_twins: bool, protected: frozenset = frozenset(),
) -> Optional[list]:
    """Fetch what is needed from qBittorrent and plan. None = listing unavailable."""
    torrents = await qbit_list_torrents()
    if torrents is None:
        return None
    want = {h.lower() for h in hashes}
    keep = {h.lower() for h in protected}
    present = [t for t in torrents if t["hash"].lower() in want]

    signatures: dict = {}
    if include_twins and present:
        sizes = {t.get("size") for t in present if t.get("size")}
        pool = sorted(
            (t for t in torrents if t["hash"].lower() not in want | keep and t.get("size") in sizes),
            key=lambda t: t["hash"],
        )
        if len(pool) > MAX_TWINS:
            logger.warning("qbit_siblings: %d candidate copies, only the first %d inspected", len(pool), MAX_TWINS)
        candidates = pool[:MAX_TWINS]
        for t in [*present, *candidates]:
            sig = _signature(await qbit_torrent_files(t["hash"]))
            if sig:
                signatures[t["hash"]] = sig
    return plan_removal(hashes, torrents, signatures, include_twins=include_twins, protected=protected)
