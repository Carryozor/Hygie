"""qBittorrent health — read-only."""
from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..qbit_health import qbit_health

router = APIRouter(prefix="/api/qbit", tags=["qbit"])


@router.get("/health")
async def get_qbit_health(user: str = Depends(require_auth)):
    """Reachability, state counts, missingFiles/error torrents and port status."""
    return await qbit_health()
