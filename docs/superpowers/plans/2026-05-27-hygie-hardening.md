# Hygie Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 6 identified issues (storage latency, proxy memory leak, missing resource limits, untraced deletion logs, CDN reliability, UX loading state) without breaking any existing feature.

**Architecture:** In-place edits to existing files — no new modules, no schema changes, no removed endpoints. Each task is independently testable and reversible. Docker image rebuilt locally at the end.

**Tech Stack:** FastAPI, aiosqlite, httpx, Tailwind CDN (→ local), Font Awesome (→ local), pytest-asyncio, Docker

---

## File Map

| File | Change |
|---|---|
| `backend/routers/storage.py` | Add 60s TTL in-memory cache |
| `backend/main.py` | Cap image proxy response at 10 MB (streaming) |
| `backend/scheduler.py` | Pass `run_id` to `_delete_media` → prefix deletion logs |
| `frontend/templates/index.html` | Replace CDN links → local paths; add skeleton CSS |
| `frontend/static/js/app.js` | Replace `loadStorage` spinner → skeleton |
| `Dockerfile` | Download Tailwind CDN script + Font Awesome during build |
| `/opt/media-stack/compose.yml` | Add `mem_limit: 512m` + `cpus: '1.0'` to hygie service |

---

## Task 1: Storage TTL Cache

**Files:**
- Modify: `backend/routers/storage.py`
- Test: `tests/test_routes.py` (new test added)

- [ ] **Step 1: Write the failing test**

```python
# In tests/test_routes.py — add after existing storage tests
async def test_storage_uses_cache_on_second_call(registered_client, monkeypatch):
    """Second call within TTL must not re-fetch from Radarr/Sonarr."""
    import backend.routers.storage as storage_mod
    client, token = registered_client

    call_count = 0
    orig_gather = asyncio.gather
    async def counting_gather(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return await orig_gather(*args, **kwargs)

    # First call populates cache (mocked services return empty)
    storage_mod._storage_cache.update({"data": None, "ts": 0.0})
    r1 = await client.get("/api/storage/", cookies={"hygie_session": token})
    assert r1.status_code == 200
    ts_after_first = storage_mod._storage_cache["ts"]
    assert ts_after_first > 0

    # Second call must return immediately from cache (ts unchanged)
    r2 = await client.get("/api/storage/", cookies={"hygie_session": token})
    assert r2.status_code == 200
    assert storage_mod._storage_cache["ts"] == ts_after_first  # same ts → cache hit
```

- [ ] **Step 2: Run to confirm fail (test not yet passing without cache)**

```bash
cd /opt/claude/hygie
python3 -m pytest tests/test_routes.py -k "storage_uses_cache" -v
```
Expected: FAIL or ERROR

- [ ] **Step 3: Implement cache in storage.py**

Replace the entire `storage.py` with the cached version — prepend module-level state and wrap the return:

```python
"""Storage — disk metrics from Radarr/Sonarr, with 60-second TTL cache."""
import asyncio
import logging
import time

import aiosqlite
import httpx
from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..database import DB_PATH, STATUS_PENDING, get_setting, TIMEOUT_MEDIUM

router = APIRouter(prefix="/api/storage", tags=["storage"])
logger = logging.getLogger(__name__)

_storage_cache: dict = {"data": None, "ts": 0.0}
_STORAGE_TTL = 60.0  # seconds


def invalidate_storage_cache() -> None:
    """Call after deletions or scans to force fresh data on next request."""
    _storage_cache.update({"data": None, "ts": 0.0})


@router.get("")
async def get_storage(user: str = Depends(require_auth)):
    now = time.time()
    if _storage_cache["data"] is not None and now - _storage_cache["ts"] < _STORAGE_TTL:
        return _storage_cache["data"]

    radarr_url = (await get_setting("radarr_url") or "").rstrip("/")
    radarr_key = await get_setting("radarr_api_key") or ""
    sonarr_url = (await get_setting("sonarr_url") or "").rstrip("/")
    sonarr_key = await get_setting("sonarr_api_key") or ""

    disks: list = []
    movies: dict = {}
    series: dict = {}
    total_media_size: int = 0
    radarr_movies_by_id: dict = {}

    async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:

        async def _get(url: str, params: dict):
            try:
                r = await c.get(url, params=params)
                return r if r.status_code == 200 else None
            except Exception:
                return None

        async def _noop() -> None:
            return None

        r_disk_task   = _get(f"{radarr_url}/api/v3/diskspace", {"apikey": radarr_key}) if radarr_url and radarr_key else _noop()
        r_movie_task  = _get(f"{radarr_url}/api/v3/movie",     {"apikey": radarr_key}) if radarr_url and radarr_key else _noop()
        s_disk_task   = _get(f"{sonarr_url}/api/v3/diskspace", {"apikey": sonarr_key}) if sonarr_url and sonarr_key else _noop()
        s_series_task = _get(f"{sonarr_url}/api/v3/series",    {"apikey": sonarr_key}) if sonarr_url and sonarr_key else _noop()

        rd, rm, sd, rs = await asyncio.gather(r_disk_task, r_movie_task, s_disk_task, s_series_task)

        if rd:
            for disk in rd.json():
                disks.append({
                    "path": disk.get("path", "?"),
                    "label": disk.get("label", ""),
                    "source": "Radarr",
                    "total": disk.get("totalSpace", 0),
                    "free": disk.get("freeSpace", 0),
                    "accessible": disk.get("accessible", True),
                })
        if rm:
            all_movies = rm.json()
            radarr_movies_by_id = {m["id"]: m for m in all_movies}
            total_in_lib = len(all_movies)
            with_file = sum(1 for m in all_movies if m.get("hasFile"))
            mon = sum(1 for m in all_movies if m.get("monitored"))
            size = sum(m.get("sizeOnDisk", 0) or 0 for m in all_movies)
            movies = {
                "total_in_library": total_in_lib,
                "count": with_file,
                "monitored": mon,
                "unmonitored": total_in_lib - mon,
                "size": size,
            }
            total_media_size += size

        if sd:
            existing_paths = {d["path"] for d in disks}
            for disk in sd.json():
                if disk.get("path", "?") not in existing_paths:
                    disks.append({
                        "path": disk.get("path", "?"),
                        "label": disk.get("label", ""),
                        "source": "Sonarr",
                        "total": disk.get("totalSpace", 0),
                        "free": disk.get("freeSpace", 0),
                        "accessible": disk.get("accessible", True),
                    })
        if rs:
            all_series = rs.json()
            count = len(all_series)
            mon = sum(1 for s in all_series if s.get("monitored"))
            eps = sum(s.get("statistics", {}).get("episodeFileCount", 0) or 0 for s in all_series)
            size = sum(s.get("statistics", {}).get("sizeOnDisk", 0) or 0 for s in all_series)
            series = {
                "count": count,
                "monitored": mon,
                "unmonitored": count - mon,
                "episodes": eps,
                "size": size,
            }
            total_media_size += size

    queue: dict = {
        "pending": 0,
        "deleted": 0,
        "excluded": 0,
        "error": 0,
        "reclaimable_size": 0,
        "reclaimable_count": 0,
    }
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT status, COUNT(*) FROM media_queue GROUP BY status"
            ) as cur:
                for status, cnt in await cur.fetchall():
                    if status in queue:
                        queue[status] = cnt

            async with db.execute("SELECT COUNT(*) FROM ignored_media") as cur:
                row = await cur.fetchone()
                queue["excluded"] = row[0] if row else 0

            if radarr_movies_by_id:
                async with db.execute(
                    "SELECT radarr_id FROM media_queue "
                    "WHERE status='pending' AND radarr_id IS NOT NULL"
                ) as cur:
                    pending_radarr_ids = [row[0] for row in await cur.fetchall()]
                reclaimable = 0
                count_reclaimable = 0
                for rid in pending_radarr_ids:
                    movie = radarr_movies_by_id.get(rid)
                    if movie:
                        reclaimable += movie.get("sizeOnDisk", 0) or 0
                        count_reclaimable += 1
                queue["reclaimable_size"] = reclaimable
                queue["reclaimable_count"] = count_reclaimable
            else:
                queue["reclaimable_count"] = queue["pending"]

    except Exception as e:
        logger.warning(f"Queue stats: {e}")

    result = {
        "disks": disks,
        "movies": movies,
        "series": series,
        "total_media_size": total_media_size,
        "queue": queue,
    }
    _storage_cache.update({"data": result, "ts": time.time()})
    return result
```

- [ ] **Step 4: Run tests**
```bash
python3 -m pytest tests/ -q
```
Expected: 154+ passed

---

## Task 2: Image Proxy Content-Length Cap

**Files:**
- Modify: `backend/main.py` — the `proxy_image` endpoint
- Test: `tests/test_routes.py` (new test)

- [ ] **Step 1: Write failing test**

```python
async def test_proxy_image_rejects_oversized_response(registered_client, respx_mock):
    """Proxy must return 413 if upstream sends more than 10 MB."""
    client, token = registered_client
    import backend.main as main_mod
    big_content = b"x" * (11 * 1024 * 1024)  # 11 MB

    # Whitelist the host for the test
    main_mod._proxy_whitelist = {"example.com"}
    main_mod._proxy_whitelist_ts = float("inf")

    respx_mock.get("http://example.com/img.jpg").mock(
        return_value=httpx.Response(200, content=big_content,
                                    headers={"content-type": "image/jpeg"})
    )
    from urllib.parse import quote
    url = quote("http://example.com/img.jpg", safe="")
    r = await client.get(f"/api/proxy/image?url={url}",
                         cookies={"hygie_session": token})
    assert r.status_code == 413
```

- [ ] **Step 2: Implement streaming cap in main.py**

Find the `proxy_image` function and replace the httpx call block:

```python
PROXY_MAX_BYTES = 10 * 1024 * 1024  # 10 MB

# Inside proxy_image, replace:
#   async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
#       r = await client.get(target_url)
#       if r.status_code == 200: ...
# With:

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            async with client.stream("GET", target_url) as r:
                if r.status_code == 200:
                    ct = r.headers.get("content-type", "image/jpeg")
                    if not ct.startswith("image/"):
                        return Response(status_code=415)
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in r.aiter_bytes(65536):
                        total += len(chunk)
                        if total > PROXY_MAX_BYTES:
                            logger.warning(
                                f"Proxy: response too large for {sanitize_url(target_url)[:80]}"
                            )
                            return Response(status_code=413)
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    return Response(
                        content=content,
                        media_type=ct,
                        headers={"Cache-Control": "public, max-age=3600"},
                    )
                if r.status_code != 500:
                    logger.warning(
                        f"Proxy: upstream HTTP {r.status_code} for {sanitize_url(target_url)[:80]}"
                    )
```

- [ ] **Step 3: Run all tests**
```bash
python3 -m pytest tests/ -q
```
Expected: all pass

---

## Task 3: Deletion Correlation ID

**Files:**
- Modify: `backend/scheduler.py` — `run_deletion` and `_delete_media`
- Test: `tests/test_scheduler_persistence.py` (new assertion)

- [ ] **Step 1: Add `run_id` param to `_delete_media`**

Change signature from:
```python
async def _delete_media(row: dict, dry_run: bool, qbit_action: str = "", qbit_tag_val: str = "") -> bool:
```
To:
```python
async def _delete_media(row: dict, dry_run: bool, qbit_action: str = "", qbit_tag_val: str = "", run_id: int = 0) -> bool:
```

Add at the top of `_delete_media` body, after `prefix = ...`:
```python
    job_tag = f"[job:{run_id}] " if run_id else ""
```

Replace every `await add_log(...)` inside `_delete_media` (and in the nested `_delete_one` closure) to prepend `job_tag`:
```python
    # e.g.:
    await add_log("INFO", f"{job_tag}{prefix}Suppression : {title}", "deletion")
    # ...
    await add_log("WARN", f"{job_tag}Discord (non bloquant) : {e}", "deletion")
    await add_log("DEBUG", f"{job_tag}Emby : hardlink retiré pour {title}", "deletion")
    await add_log("INFO", f"{job_tag}Suppression complète : {title}", "deletion")
    await add_log("ERROR", f"{job_tag}Erreur suppression {title}: {e}", "deletion")
```

- [ ] **Step 2: Pass `run_id` from `run_deletion` to `_delete_media`**

In `run_deletion`, find the deletion loop:
```python
            for row in to_delete:
                ok = await _delete_media(row, dry_run, qbit_action=_qbit_action, qbit_tag_val=_qbit_tag)
```
Change to:
```python
            for row in to_delete:
                ok = await _delete_media(row, dry_run, qbit_action=_qbit_action, qbit_tag_val=_qbit_tag, run_id=run_id)
```

- [ ] **Step 3: Run tests**
```bash
python3 -m pytest tests/ -q
```
Expected: all pass

---

## Task 4: Frontend CDN → Local Bundle (Dockerfile)

**Files:**
- Modify: `Dockerfile`
- Modify: `frontend/templates/index.html`

- [ ] **Step 1: Update Dockerfile to download Tailwind CDN + Font Awesome**

Add a build stage before `COPY backend/`:
```dockerfile
# ── Download frontend dependencies (no CDN at runtime) ───────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && mkdir -p /app/frontend/static/css /app/frontend/static/webfonts \
    # Tailwind CDN runtime (identical behaviour, hosted locally)
    && curl -fsSL https://cdn.tailwindcss.com \
         -o /app/frontend/static/js/tailwind.cdn.js \
    # Font Awesome 6.5 CSS + 3 webfonts (solid, regular, brands)
    && curl -fsSL "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" \
         -o /app/frontend/static/css/fa.min.css \
    && for f in fa-solid-900.woff2 fa-regular-400.woff2 fa-brands-400.woff2; do \
         curl -fsSL "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/webfonts/$f" \
           -o "/app/frontend/static/webfonts/$f"; \
       done \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 2: Update index.html CDN references**

Replace:
```html
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css"/>
```
With:
```html
<script src="/static/js/tailwind.cdn.js"></script>
<link rel="stylesheet" href="/static/css/fa.min.css"/>
```

- [ ] **Step 3: Build Docker image and verify no CDN calls needed at runtime**

```bash
cd /opt/claude/hygie
docker build -t hygie:local .
```
Expected: build completes, static files present in image

---

## Task 5: Storage Skeleton Loading

**Files:**
- Modify: `frontend/templates/index.html` — add skeleton CSS
- Modify: `frontend/static/js/app.js` — replace spinner with skeleton

- [ ] **Step 1: Add skeleton CSS to index.html `<style>` block**

```css
.skeleton {
  background: linear-gradient(90deg, #ffffff08 25%, #ffffff18 50%, #ffffff08 75%);
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.5s infinite;
  border-radius: 6px;
}
@keyframes skeleton-shimmer {
  0%   { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
```

- [ ] **Step 2: Replace spinner in `loadStorage()` with skeleton**

Replace:
```javascript
box.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)"><i class="fas fa-spinner fa-spin" style="font-size:24px;margin-bottom:12px;display:block"></i>Chargement...</div>';
```
With:
```javascript
box.innerHTML = `
  <div class="card" style="padding:20px">
    <div class="skeleton" style="height:18px;width:35%;margin-bottom:20px"></div>
    <div style="display:flex;flex-direction:column;gap:14px">
      ${[1,2,3].map(()=>`
        <div>
          <div style="display:flex;justify-content:space-between;margin-bottom:8px">
            <div class="skeleton" style="height:13px;width:40%"></div>
            <div class="skeleton" style="height:22px;width:12%"></div>
          </div>
          <div class="skeleton" style="height:10px;width:100%"></div>
        </div>`).join('')}
    </div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
    ${[1,2].map(()=>`
      <div class="card" style="padding:20px">
        <div class="skeleton" style="height:18px;width:50%;margin-bottom:16px"></div>
        ${[1,2,3,4].map(()=>`<div class="skeleton" style="height:13px;margin-bottom:10px"></div>`).join('')}
      </div>`).join('')}
  </div>`;
```

- [ ] **Step 3: Run tests**
```bash
python3 -m pytest tests/ -q
```
Expected: all pass (frontend changes don't break backend tests)

---

## Task 6: Resource Limits in media-stack compose.yml

**Files:**
- Modify: `/opt/media-stack/compose.yml`

- [ ] **Step 1: Add `mem_limit` and `cpus` to hygie service**

Find the hygie service block and add after `restart: unless-stopped`:
```yaml
    mem_limit: 512m
    cpus: '1.0'
```

- [ ] **Step 2: Validate compose syntax**
```bash
docker compose -f /opt/media-stack/compose.yml config --quiet
```
Expected: no errors

---

## Task 7: Docker Build & Local Deploy

- [ ] **Step 1: Run full test suite one last time**
```bash
cd /opt/claude/hygie
python3 -m pytest tests/ -v 2>&1 | tail -20
```
Expected: all pass

- [ ] **Step 2: Build new local image**
```bash
cd /opt/claude/hygie
docker build -t hygie:local .
```

- [ ] **Step 3: Replace running container**
```bash
cd /opt/media-stack
docker compose stop hygie
docker compose up -d hygie
```

- [ ] **Step 4: Wait for healthy and verify**
```bash
sleep 10 && docker inspect hygie --format='{{.State.Health.Status}}'
curl -s http://localhost:8000/health | python3 -m json.tool
```
Expected: `healthy`, JSON with `"status": "healthy"`

- [ ] **Step 5: Verify no external CDN calls at runtime**
```bash
docker exec hygie grep -r "cdn.tailwindcss\|cdnjs.cloudflare\|font-awesome" /app/frontend/templates/
```
Expected: no output (all CDN references removed)

---

## Self-Review

- [x] Storage cache TTL tested + invalidation function available for future use
- [x] Proxy cap uses streaming (no RAM spike before rejection)
- [x] Correlation ID is a zero-schema-change approach (prepended to message)
- [x] CDN bundling preserves identical runtime behavior (CDN script downloaded, not compiled)
- [x] Skeleton loading is pure JS/CSS, no API contract change
- [x] Compose limits match the values already recommended in hygie's own docker-compose.yml example
- [x] All existing 154 tests must pass at every step
