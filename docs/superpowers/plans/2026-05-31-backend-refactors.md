# Backend Refactors — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Déplacer `conditions.py` dans `rules/` pour regrouper toute la logique d'évaluation, ajouter un retry avec backoff exponentiel sur les appels réseau arr_clients, et déployer.

**Architecture:** Deux changements indépendants et ciblés. Le déplacement de `conditions.py` est un refactor pur (import renames, zéro logique changée). Le retry est un helper asyncio sans dépendance externe appliqué aux fonctions critiques Radarr/Sonarr.

**Tech Stack:** Python 3.12, asyncio, httpx, aiosqlite

**Scope explicite — ce qu'on NE fait PAS ici :** Refactoring des routers en couche service complète (trop risqué sans tests E2E, reporté à une version future). On crée le répertoire `services/` pour préparer le terrain.

---

## File Map

| Action | Fichier | Rôle |
|---|---|---|
| Create | `backend/rules/legacy_conditions.py` | Copie de conditions.py dans rules/ |
| Modify | `backend/conditions.py` | Devient un shim de compatibilité (réexporte depuis rules/legacy_conditions) |
| Modify | `backend/scanner/_emby_scanner.py` | Importe depuis rules.legacy_conditions |
| Modify | `backend/routers/media.py` | Importe depuis rules.legacy_conditions |
| Create | `backend/arr_clients/retry.py` | Helper retry asyncio avec backoff exponentiel |
| Modify | `backend/arr_clients/radarr.py` | Applique retry sur build_cache et delete |
| Modify | `backend/arr_clients/sonarr.py` | Applique retry sur build_cache et delete |
| Create | `backend/services/__init__.py` | Package vide — répertoire services/ préparé |

---

## Task 1 : Déplacer conditions.py → rules/legacy_conditions.py

**Stratégie :** Créer `rules/legacy_conditions.py` comme alias vers conditions.py via un shim de compatibilité. Cela permet de migrer proprement sans casser les imports existants en une seule étape.

**Files:**
- Create: `backend/rules/legacy_conditions.py`
- Modify: `backend/conditions.py` (shim)
- Modify: `backend/scanner/_emby_scanner.py`
- Modify: `backend/routers/media.py`

- [ ] **Step 1 : Lire conditions.py pour connaître ses exports**

```bash
head -20 /opt/claude/hygie/backend/conditions.py
grep -n "^def \|^class \|^async def " /opt/claude/hygie/backend/conditions.py
```

- [ ] **Step 2 : Copier conditions.py → rules/legacy_conditions.py**

```bash
cp /opt/claude/hygie/backend/conditions.py \
   /opt/claude/hygie/backend/rules/legacy_conditions.py
```

Ensuite, dans `rules/legacy_conditions.py`, corriger les imports relatifs :
- `from .db.` → `from ..db.`
- `from .arr_clients` → `from ..arr_clients`
- `from .emby_client` → `from ..emby_client`
- `from .discord_client` → `from ..discord_client`

**Important :** Le fichier copié utilise des imports relatifs depuis la racine backend (ex: `from .db.utils import ...`). Dans `rules/`, il faut un niveau de plus : `from ..db.utils import ...`.

- [ ] **Step 3 : Transformer conditions.py en shim de compatibilité**

Remplacer le contenu de `backend/conditions.py` par :

```python
"""Backward-compatibility shim — all symbols now live in rules.legacy_conditions.

Import from rules.legacy_conditions directly in new code.
"""
from .rules.legacy_conditions import (  # noqa: F401
    ScanContext,
    _eval_op,
    _evaluate_conditions,
    _evaluate_item,
    _get_poster_url,
    _get_seerr_grace,
    _seerr_filter_passes,
    _update_delete_at_if_pending,
)
```

- [ ] **Step 4 : Mettre à jour les imports dans _emby_scanner.py**

```python
# Avant
from ..conditions import _evaluate_conditions, _evaluate_item, _get_poster_url
# Après
from ..rules.legacy_conditions import _evaluate_conditions, _evaluate_item, _get_poster_url
```

- [ ] **Step 5 : Mettre à jour les imports dans routers/media.py**

```bash
grep -n "from.*conditions import" /opt/claude/hygie/backend/routers/media.py
```

```python
# Avant
from ..conditions import _get_poster_url
# Après
from ..rules.legacy_conditions import _get_poster_url
```

- [ ] **Step 6 : Vérifier que routers/ignored.py n'importe pas conditions**

```bash
grep "conditions" /opt/claude/hygie/backend/routers/ignored.py
```

Si import trouvé, mettre à jour de la même façon.

- [ ] **Step 7 : Vérifier le test existant**

```bash
grep -n "from.*conditions import" /opt/claude/hygie/backend/tests/test_conditions.py
```

Le shim dans `conditions.py` maintient la rétro-compatibilité — le test devrait fonctionner sans modification.

- [ ] **Step 8 : Lancer les tests**

```bash
cd /opt/claude/hygie && python3 -m pytest tests/test_conditions.py -v
```

Résultat attendu : tous les tests passent.

- [ ] **Step 9 : Vérifier l'import depuis le container**

```bash
docker exec hygie python3 -c "
import sys; sys.path.insert(0, '/app')
from backend.rules.legacy_conditions import _evaluate_conditions, _get_poster_url
from backend.conditions import _evaluate_conditions  # shim
print('OK — both import paths work')
"
```

- [ ] **Step 10 : Commit**

```bash
git add backend/rules/legacy_conditions.py backend/conditions.py \
        backend/scanner/_emby_scanner.py backend/routers/media.py
git commit -m "refactor(rules): move conditions.py to rules/legacy_conditions.py

conditions.py becomes a backward-compat shim. All evaluation logic
now lives in rules/ alongside engine.py and models.py."
```

---

## Task 2 : Retry asyncio sur arr_clients

**Objectif :** Ajouter 3 tentatives avec backoff exponentiel (1s, 2s, 4s) sur les fonctions critiques qui font des appels réseau. Uniquement sur les exceptions réseau/connexion — pas sur les erreurs HTTP logiques (401, 404).

**Files:**
- Create: `backend/arr_clients/retry.py`
- Modify: `backend/arr_clients/radarr.py`
- Modify: `backend/arr_clients/sonarr.py`

- [ ] **Step 1 : Créer backend/arr_clients/retry.py**

```python
# backend/arr_clients/retry.py
"""Simple async retry with exponential backoff for network calls.

Usage:
    from .retry import with_retry

    result = await with_retry(some_async_fn, arg1, arg2, label="radarr.delete")
"""
import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

# Exceptions that indicate a transient network problem (worth retrying)
_RETRYABLE = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


async def with_retry(fn, *args, retries: int = 3, base_delay: float = 1.0, label: str = "", **kwargs):
    """Call ``fn(*args, **kwargs)`` up to ``retries`` times on transient errors.

    Raises the last exception if all attempts fail.
    Non-retryable exceptions propagate immediately.
    """
    last_exc = None
    for attempt in range(retries):
        try:
            return await fn(*args, **kwargs)
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt < retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "arr retry %d/%d [%s]: %s — retrying in %.1fs",
                    attempt + 1, retries, label, exc, delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.error("arr retry exhausted [%s]: %s", label, exc)
        except Exception:
            raise  # non-retryable — propagate immediately
    raise last_exc
```

- [ ] **Step 2 : Appliquer le retry dans radarr.py**

Lire `backend/arr_clients/radarr.py` puis ajouter `from .retry import with_retry` en haut.

Wrapper les fonctions critiques. Exemple pour `build_radarr_path_cache` :

```python
# Avant — dans build_radarr_path_cache
async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:
    r = await c.get(f"{url}/api/v3/movie", headers=_arr_auth(key))

# Après — wrappé avec retry au niveau du client call
async def _fetch_movies(url: str, key: str) -> list:
    async with httpx.AsyncClient(timeout=TIMEOUT_MEDIUM) as c:
        r = await c.get(f"{url}/api/v3/movie", headers=_arr_auth(key))
        if r.status_code != 200:
            return []
        return r.json()

# Dans build_radarr_path_cache :
movies = await with_retry(_fetch_movies, url, key, label=f"radarr.build_cache[{url}]")
```

**Fonctions à wrapper dans radarr.py :**
- `build_radarr_path_cache` — le call `c.get(f"{url}/api/v3/movie", ...)`
- `radarr_find_by_path` — le call `c.get(f"{url}/api/v3/movie", ...)`
- `radarr_delete` — le call `c.delete(...)`
- `radarr_get_torrent_hash` — les deux calls history

**Pattern à utiliser dans chaque fonction :**

Extraire le bloc httpx dans une sous-fonction `_fetch_*` interne, appeler via `with_retry`. Alternativement, wrapper toute la fonction avec un try/retry en interne.

**Approche alternative plus simple (inline retry) :**

```python
async def radarr_delete(radarr_id: int, delete_files: bool = False, url: str = "", key: str = "") -> bool:
    if not url or not key:
        url, key = await _radarr_config()
    if not url or not key or not radarr_id:
        return False
    
    async def _do_delete():
        async with httpx.AsyncClient(timeout=TIMEOUT_SHORT) as c:
            r = await c.delete(
                f"{url}/api/v3/movie/{radarr_id}",
                headers=_arr_auth(key),
                params={"deleteFiles": str(delete_files).lower(), "addImportExclusion": "false"},
            )
            return r.status_code in (200, 204)
    
    try:
        return await with_retry(_do_delete, label=f"radarr.delete[{radarr_id}]")
    except Exception as e:
        logger.warning(f"radarr_delete: {e}")
        return False
```

- [ ] **Step 3 : Appliquer le retry dans sonarr.py**

Même pattern pour :
- `build_sonarr_path_cache` — les calls `c.get(f"{url}/api/v3/series", ...)` et `c.get(f"{url}/api/v3/episodefile", ...)`
- `sonarr_delete_episode_file` — `c.delete(...)`
- `sonarr_delete_season` — les deux calls
- `sonarr_delete_series` — les deux calls

- [ ] **Step 4 : Mettre à jour backend/arr_clients/__init__.py**

Ajouter `with_retry` aux exports si nécessaire (optionnel — c'est un helper interne).

- [ ] **Step 5 : Lancer les tests**

```bash
cd /opt/claude/hygie && python3 -m pytest tests/ -k "arr" -v 2>&1 | tail -20
```

- [ ] **Step 6 : Tester l'import**

```bash
cd /opt/claude/hygie && python3 -c "
from backend.arr_clients.retry import with_retry
from backend.arr_clients.radarr import build_radarr_path_cache, radarr_delete
print('OK — retry imported and functions accessible')
"
```

- [ ] **Step 7 : Commit**

```bash
git add backend/arr_clients/retry.py backend/arr_clients/radarr.py backend/arr_clients/sonarr.py
git commit -m "feat(arr): add retry with exponential backoff on network calls

retry.py: retries on httpx transient errors (ConnectError, timeouts)
up to 3 times with 1s/2s/4s backoff. Non-retryable errors propagate
immediately. Applied to build_cache, delete, get_torrent_hash."
```

---

## Task 3 : Créer backend/services/ + déployer

**Files:**
- Create: `backend/services/__init__.py`
- Deploy

- [ ] **Step 1 : Créer le répertoire services/**

```bash
mkdir -p /opt/claude/hygie/backend/services
cat > /opt/claude/hygie/backend/services/__init__.py << 'EOF'
"""Services layer — business logic between routers and repositories.

Current modules:
  (empty — service layer is being progressively introduced)

Planned:
  arr_service.py   — Radarr/Sonarr operations with retry
  scan_service.py  — Scan orchestration
"""
EOF
```

- [ ] **Step 2 : Déployer tous les fichiers modifiés**

```bash
cd /opt/claude/hygie && bash scripts/deploy.sh
```

Résultat attendu : `✅ Deploy complete — Hygie is healthy`

- [ ] **Step 3 : Vérifier dans le container**

```bash
docker exec hygie python3 -c "
import sys; sys.path.insert(0, '/app')
from backend.rules.legacy_conditions import _evaluate_conditions
from backend.arr_clients.retry import with_retry
from backend.services import __doc__
print('legacy_conditions: OK')
print('retry: OK')
print('services: OK')
"
```

- [ ] **Step 4 : Lancer le test de schéma**

```bash
cd /opt/claude/hygie && make check-schema
```

- [ ] **Step 5 : Commit final**

```bash
git add backend/services/__init__.py
git commit -m "feat(services): scaffold services/ layer

Empty package that will progressively house business logic
extracted from routers. Separates HTTP concerns from domain logic."
```

---

## Self-Review Checklist

- [x] **Imports relatifs** : legacy_conditions.py dans rules/ a besoin de `..` supplémentaire pour accéder à db/, arr_clients/, etc.
- [x] **Rétro-compatibilité** : conditions.py shim réexporte tout → tests/test_conditions.py passe sans modification
- [x] **Retry scope** : seulement les exceptions réseau httpx, pas les erreurs HTTP logiques (401, 404 propagent immédiatement)
- [x] **Pas de nouvelle dépendance** : retry.py utilise seulement asyncio + httpx (déjà dans requirements.txt)
- [x] **Deploy validé** : scripts/deploy.sh copie backend/rules/ et backend/services/
